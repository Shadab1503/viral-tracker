# viral_tracker.py
# Requirements: pip install apify-client newsapi-python apscheduler requests pandas

import os, time, json, smtplib
from datetime import datetime, timedelta
from collections import defaultdict
from apify_client import ApifyClient
from newsapi import NewsApiClient
from apscheduler.schedulers.blocking import BlockingScheduler
import pandas as pd

# ─── CONFIG ────────────────────────────────────
import os
APIFY_TOKEN        = os.environ.get("APIFY_TOKEN")
NEWSAPI_KEY        = os.environ.get("NEWSAPI_KEY")
ALERT_EMAIL        = os.environ.get("ALERT_EMAIL")
SMTP_PASS          = os.environ.get("SMTP_PASS")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
VIRALITY_THRESHOLD = 3


# Optional: Virlo API (dev.virlo.ai) for deeper TikTok scoring
VIRLO_API_KEY     = "YOUR_VIRLO_KEY"          # optional

apify = ApifyClient(APIFY_TOKEN)
newsapi = NewsApiClient(api_key=NEWSAPI_KEY)

# ─── STORE: track history to detect SPIKES ─────────────────────────────────
history = defaultdict(list)   # topic → [{"time": ..., "score": ...}]

# ─── 1. TWITTER/X USA TRENDS ───────────────────────────────────────────────
def get_twitter_trends():
    """Pulls top-50 USA trending topics from X via Apify scraper."""
    run = apify.actor("eunit/x-twitter-trends-scraper").call(
        run_input={
            "location": "United States",    # USA filter
            "timeRange": "1h"               # last 1 hour
        }
    )
    items = list(apify.dataset(run["defaultDatasetId"]).iterate_items())
    trends = []
    for item in items:
        trends.append({
            "topic": item.get("name", ""),
            "tweet_volume": item.get("tweetVolume", 0),
            "platform": "twitter"
        })
    return trends


# ─── 2. TIKTOK USA VIRAL TOPICS ────────────────────────────────────────────
def get_tiktok_trends():
    """Pulls viral TikTok hashtags filtered to USA region."""
    run = apify.actor("manju4k/social-media-trend-scraper-6-in-1-ai-analysis").call(
        run_input={
            "platforms": ["TikTok"],
            "region": "united_states",      # USA only
            "timeRange": "1h",
            "comparisonType": "hashtags"
        }
    )
    items = list(apify.dataset(run["defaultDatasetId"]).iterate_items())
    trends = []
    for item in items:
        trends.append({
            "topic": item.get("hashtag", ""),
            "score": item.get("viralityScore", 0),
            "platform": "tiktok"
        })
    return trends


# ─── 3. NEWS SPIKE DETECTION (NewsAPI) ─────────────────────────────────────
def get_news_trending(keyword=None):
    """
    If keyword given → checks if that topic spiked in last 6h.
    If no keyword → returns top US news headlines as topics.
    """
    if keyword:
        # count articles mentioning this keyword in last 6 hours
        from_time = (datetime.utcnow() - timedelta(hours=6)).isoformat()
        result = newsapi.get_everything(
            q=keyword,
            language="en",
            sort_by="publishedAt",
            from_param=from_time,
            page_size=100
        )
        return {"topic": keyword, "article_count": result["totalResults"], "platform": "news"}
    else:
        # get top US headlines and extract topics
        top = newsapi.get_top_headlines(country="us", page_size=20)
        topics = []
        for article in top.get("articles", []):
            topics.append({
                "topic": article["title"],
                "source": article["source"]["name"],
                "platform": "news",
                "published_at": article["publishedAt"]
            })
        return topics


# ─── 4. INSTAGRAM REELS TRENDS ─────────────────────────────────────────────
def get_instagram_reels_trends():
    """Pulls viral Instagram Reels trends for USA via Apify."""
    run = apify.actor("manju4k/social-media-trend-scraper-6-in-1-ai-analysis").call(
        run_input={
            "platforms": ["Instagram"],
            "region": "united_states",
            "timeRange": "4h",
            "comparisonType": "hashtags"
        }
    )
    items = list(apify.dataset(run["defaultDatasetId"]).iterate_items())
    trends = []
    for item in items:
        trends.append({
            "topic": item.get("hashtag", ""),
            "engagement": item.get("engagementRate", 0),
            "platform": "instagram"
        })
    return trends


# ─── 5. CROSS-PLATFORM VIRALITY SCORER ─────────────────────────────────────
def compute_virality(twitter, tiktok, instagram, news_topics):
    """
    Normalizes topic names, finds topics appearing on 2+ platforms.
    Returns a ranked list with a viral_score (0-100).
    """
    platform_hits = defaultdict(set)   # topic → set of platforms

    def normalize(s):
        return s.lower().strip().replace("#", "").replace(" ", "")

    for t in twitter:
        platform_hits[normalize(t["topic"])].add("twitter")
    for t in tiktok:
        platform_hits[normalize(t["topic"])].add("tiktok")
    for t in instagram:
        platform_hits[normalize(t["topic"])].add("instagram")
    for t in news_topics:
        platform_hits[normalize(t["topic"])].add("news")

    results = []
    for topic, platforms in platform_hits.items():
        score = len(platforms) * 25   # 25 pts per platform, max 100
        results.append({
            "topic": topic,
            "platforms": list(platforms),
            "viral_score": score,
            "platform_count": len(platforms),
            "detected_at": datetime.utcnow().isoformat()
        })

    results.sort(key=lambda x: x["viral_score"], reverse=True)
    return results


# ─── 6. SPIKE DETECTOR (tracks history) ────────────────────────────────────
def is_new_spike(topic, score, window_hours=6):
    """
    Returns True if this topic just spiked — was below threshold
    in the last window but is now above it.
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=window_hours)
    recent = [h for h in history[topic] if h["time"] > cutoff]

    if not recent:
        # first time seeing this topic
        history[topic].append({"time": now, "score": score})
        return score >= VIRALITY_THRESHOLD * 25

    avg_past = sum(h["score"] for h in recent) / len(recent)
    history[topic].append({"time": now, "score": score})

    # spike = current score is 40%+ higher than recent average
    return score > avg_past * 1.4


# ─── 7. ALERT SYSTEM ───────────────────────────────────────────────────────
import requests

def send_alert(viral_topics):
    if not viral_topics:
        return

    message = "🔥 *USA VIRAL TREND ALERT*\n"
    message += "━━━━━━━━━━━━━━━━━━━━\n\n"

    for t in viral_topics:
        message += f"📌 *#{t['topic'].upper()}*\n"
        message += f"📱 Platforms: {', '.join(t['platforms'])}\n"
        message += f"🚀 Viral Score: {t['viral_score']}/100\n"
        message += f"🕐 Detected: {t['detected_at']}\n\n"

    message += "━━━━━━━━━━━━━━━━━━━━\n"
    message += "Catch it early\\. Post now\\! 🎯"

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "MarkdownV2"
    }

    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print(f"[TELEGRAM SENT] {len(viral_topics)} viral topics sent!")
        else:
            print(f"[TELEGRAM ERROR] {response.text}")
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

    # Still save to log file
    with open("viral_log.json", "a") as f:
        for t in viral_topics:
            f.write(json.dumps(t) + "\n")



# ─── 8. OPTIONAL: CELEBRITY TRIGGER DETECTOR ───────────────────────────────
CELEBRITY_LIST = [
    "Rihanna", "Kylie Jenner", "Charli D'Amelio", "MrBeast",
    "Kim Kardashian", "Dua Lipa", "Selena Gomez", "Doja Cat"
]

def check_celebrity_triggers():
    """
    Checks if any top US celebrity just posted about a new brand/product.
    This mimics the Labubu trigger (Lisa → viral).
    """
    hits = []
    for celeb in CELEBRITY_LIST:
        # Search news for celebrity + "posted" or "wearing" or "using"
        result = newsapi.get_everything(
            q=f"{celeb} viral OR posted OR wearing OR obsessed",
            language="en",
            sort_by="publishedAt",
            from_param=(datetime.utcnow() - timedelta(hours=12)).isoformat(),
            page_size=5
        )
        if result["totalResults"] > 0:
            for article in result["articles"][:2]:
                hits.append({
                    "celebrity": celeb,
                    "headline": article["title"],
                    "url": article["url"],
                    "published": article["publishedAt"]
                })
    return hits


# ─── 9. MAIN SCAN JOB (runs every hour via scheduler) ──────────────────────
def run_scan():
    print(f"\n[{datetime.utcnow().isoformat()}] Starting USA viral scan...")

    # Each platform fetched safely — one failure won't crash everything
    twitter_trends = []
    tiktok_trends = []
    insta_trends = []
    news_topics = []

    try:
        twitter_trends = get_twitter_trends()
        print(f"✅ Twitter: {len(twitter_trends)} trends fetched")
    except Exception as e:
        print(f"❌ Twitter failed: {e}")

    try:
        tiktok_trends = get_tiktok_trends()
        print(f"✅ TikTok: {len(tiktok_trends)} trends fetched")
    except Exception as e:
        print(f"❌ TikTok failed: {e}")

    try:
        insta_trends = get_instagram_reels_trends()
        print(f"✅ Instagram: {len(insta_trends)} trends fetched")
    except Exception as e:
        print(f"❌ Instagram failed: {e}")

    try:
        news_topics = get_news_trending()
        print(f"✅ News: {len(news_topics)} topics fetched")
    except Exception as e:
        print(f"❌ News failed: {e}")

    try:
        celeb_hits = check_celebrity_triggers()
        if celeb_hits:
            print(f"🌟 Celebrity trigger: {len(celeb_hits)} hits!")
    except Exception as e:
        print(f"❌ Celebrity check failed: {e}")

    # Score and alert
    scored = compute_virality(twitter_trends, tiktok_trends, insta_trends, news_topics)
    new_virals = [
        t for t in scored
        if is_new_spike(t["topic"], t["viral_score"])
        and t["platform_count"] >= 2
    ]

    print(f"\n{'='*50}")
    for t in scored[:10]:
        print(f"{t['topic']:<25} {str(t['platforms']):<30} {t['viral_score']}/100")

    if new_virals:
        print(f"\n🔥 {len(new_virals)} NEW VIRAL TOPICS!")
        send_alert(new_virals)
    else:
        print("\n✅ No new spikes yet.")

    pd.DataFrame(scored).to_csv(
        f"scan_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv", index=False
    )



# ─── 10. SCHEDULER SETUP ───────────────────────────────────────────────────
if __name__ == "__main__":
    scheduler = BlockingScheduler()
    scheduler.add_job(run_scan, "interval", hours=1)   # scan every 1 hour
    print("🚀 USA Viral Trend Tracker started. Scanning every hour...")
    run_scan()          # run once immediately on start
    scheduler.start()
