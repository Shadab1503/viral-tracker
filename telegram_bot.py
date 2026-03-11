import os
import requests
import wikipedia
import pandas as pd
from datetime import datetime
from googleapiclient.discovery import build
from apify_client import ApifyClient
from newsapi import NewsApiClient
from pytrends.request import TrendReq
import telebot

# ─── CONFIG ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN      = os.environ.get("TELEGRAM_TOKEN")
APIFY_TOKEN         = os.environ.get("APIFY_TOKEN")
NEWSAPI_KEY         = os.environ.get("NEWSAPI_KEY")
YOUTUBE_API_KEY     = os.environ.get("YOUTUBE_API_KEY")
REDDIT_CLIENT_ID    = os.environ.get("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET")

bot     = telebot.TeleBot(TELEGRAM_TOKEN)
apify   = ApifyClient(APIFY_TOKEN)
newsapi = NewsApiClient(api_key=NEWSAPI_KEY)


# ─── 1. GOOGLE TRENDS ──────────────────────────────────────────────────────
def get_google_trends(keyword):
    try:
        pytrend = TrendReq(hl='en-US', tz=360)
        pytrend.build_payload([keyword], timeframe='today 5-y', geo='US')
        df = pytrend.interest_over_time()
        if df.empty:
            return None
        spike_weeks = df[df[keyword] >= 20]
        if spike_weeks.empty:
            return None
        related = pytrend.related_queries()
        rising = related.get(keyword, {}).get("rising", pd.DataFrame())
        top_related = rising["query"].tolist()[:3] if not rising.empty else []
        return {
            "first_spike": spike_weeks.index[0].strftime("%B %Y"),
            "peak_month": df[keyword].idxmax().strftime("%B %Y"),
            "peak_score": int(df[keyword].max()),
            "related": top_related
        }
    except Exception as e:
        return None


# ─── 2. NEWS — Multiple articles sorted oldest first ───────────────────────
def get_news_articles(keyword):
    try:
        # Search with origin-focused terms
        origin_queries = [
            f'"{keyword}" launch OR debut OR "went viral" OR "first time"',
            f'"{keyword}" trending OR viral OR exploded',
            f"{keyword}"
        ]
        all_articles = []
        for query in origin_queries:
            result = newsapi.get_everything(
                q=query,
                language="en",
                sort_by="relevancy",
                page_size=3
            )
            for a in result.get("articles", []):
                url = a["url"]
                if url not in [x["url"] for x in all_articles]:
                    all_articles.append({
                        "title": a["title"],
                        "source": a["source"]["name"],
                        "date": a["publishedAt"][:10],
                        "url": url,
                        "description": (a.get("description") or "")[:120]
                    })
            if len(all_articles) >= 3:
                break

        all_articles.sort(key=lambda x: x["date"])
        return all_articles[:3]
    except Exception as e:
        return []



# ─── 3. YOUTUBE — Earliest videos about this topic ─────────────────────────
def get_youtube_videos(keyword):
    try:
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        # Search for origin/viral moment specifically
        search_queries = [
            f"{keyword} went viral",
            f"{keyword} origin explained",
            f"{keyword} first viral video"
        ]
        all_videos = []
        for query in search_queries:
            request = youtube.search().list(
                q=query,
                part="snippet",
                type="video",
                order="viewCount",        # most viewed = truly viral
                maxResults=3,
                relevanceLanguage="en",
                regionCode="US"
            )
            response = request.execute()
            for item in response.get("items", []):
                snippet = item["snippet"]
                # Only English titles
                title = snippet["title"]
                channel = snippet["channelTitle"]
                date = snippet["publishedAt"][:10]
                url = f"https://youtube.com/watch?v={item['id']['videoId']}"
                # Avoid duplicates
                if url not in [v["url"] for v in all_videos]:
                    all_videos.append({
                        "title": title,
                        "channel": channel,
                        "date": date,
                        "url": url
                    })

        # Sort oldest first to show true origin
        all_videos.sort(key=lambda x: x["date"])
        return all_videos[:3]
    except Exception as e:
        return []


# ─── 4. REDDIT — Earliest posts & discussions ──────────────────────────────
def get_reddit_posts(keyword):
    try:
        subreddit = reddit.subreddit("all")
        posts = []
        for post in subreddit.search(keyword, sort="new", limit=5, time_filter="all"):
            posts.append({
                "title": post.title,
                "subreddit": post.subreddit.display_name,
                "score": post.score,
                "comments": post.num_comments,
                "date": datetime.fromtimestamp(post.created_utc).strftime("%Y-%m-%d"),
                "url": f"https://reddit.com{post.permalink}"
            })
        posts.sort(key=lambda x: x["date"])
        return posts[:3]
    except Exception as e:
        return []


# ─── 5. WIKIPEDIA — Topic summary ──────────────────────────────────────────
def get_wikipedia_summary(keyword):
    try:
        wiki_url = f"https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": keyword,
            "srlimit": 1
        }
        search_result = requests.get(wiki_url, params=params).json()
        if not search_result["query"]["search"]:
            return None
        title = search_result["query"]["search"][0]["title"]

        # Get extract
        params2 = {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "extracts",
            "exintro": True,
            "explaintext": True,
            "exsentences": 3
        }
        extract_result = requests.get(wiki_url, params=params2).json()
        page = next(iter(extract_result["query"]["pages"].values()))
        extract = page.get("extract", "")[:300]
        return {"title": title, "summary": extract}
    except Exception as e:
        return None


# ─── 6. CELEBRITY TRIGGER ──────────────────────────────────────────────────
def get_celebrity_trigger(keyword):
    celebs = [
        "Lisa BLACKPINK", "Rihanna", "Dua Lipa", "Kylie Jenner",
        "Kim Kardashian", "Selena Gomez", "Charli D'Amelio", "MrBeast"
    ]
    for celeb in celebs:
        try:
            result = newsapi.get_everything(
                q=f"{keyword} {celeb}",
                language="en",
                sort_by="publishedAt",
                page_size=1
            )
            if result["totalResults"] > 0:
                a = result["articles"][0]
                return {
                    "celebrity": celeb,
                    "date": a["publishedAt"][:10],
                    "headline": a["title"],
                    "url": a["url"]
                }
        except Exception as e:
            continue
    return None


# ─── BUILD FULL REPORT ──────────────────────────────────────────────────────
def build_report(keyword):
    trends  = get_google_trends(keyword)
    articles = get_news_articles(keyword)
    videos  = get_youtube_videos(keyword)
    reddit_posts = []
    wiki    = get_wikipedia_summary(keyword)
    celeb   = get_celebrity_trigger(keyword)

    msg = f"🔬 <b>ORIGIN REPORT: {keyword.upper()}</b>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

    # Wikipedia context
    if wiki:
        msg += f"📖 <b>What is it?</b>\n"
        msg += f"{wiki['summary']}\n\n"

    # Google Trends
    if trends:
        msg += "📈 <b>Google Trends (USA)</b>\n"
        msg += f"First Spike: <b>{trends['first_spike']}</b>\n"
        msg += f"Peak: {trends['peak_month']} ({trends['peak_score']}/100)\n"
        if trends['related']:
            msg += f"Related searches: {', '.join(trends['related'])}\n"
        msg += "\n"

    # News Articles
    if articles:
        msg += f"📰 <b>Early News Articles ({len(articles)} found)</b>\n"
        for a in articles:
            msg += f"• {a['date']} — <b>{a['source']}</b>\n"
            msg += f"  {a['title']}\n"
            if a['description']:
                msg += f"  <i>{a['description']}</i>\n"
            msg += f"  <a href='{a['url']}'>Read more</a>\n"
        msg += "\n"

    # YouTube
    if videos:
        msg += f"🎬 <b>Early YouTube Videos</b>\n"
        for v in videos:
            msg += f"• {v['date']} — {v['channel']}\n"
            msg += f"  {v['title']}\n"
            msg += f"  <a href='{v['url']}'>Watch</a>\n"
        msg += "\n"

    # Reddit
    if reddit_posts:
        msg += f"👽 <b>Early Reddit Discussions</b>\n"
        for p in reddit_posts:
            msg += f"• {p['date']} — r/{p['subreddit']}\n"
            msg += f"  {p['title']}\n"
            msg += f"  ⬆️ {p['score']} upvotes | 💬 {p['comments']} comments\n"
            msg += f"  <a href='{p['url']}'>View post</a>\n"
        msg += "\n"

    # Celebrity trigger
    if celeb:
        msg += "🌟 <b>Celebrity Trigger</b>\n"
        msg += f"{celeb['celebrity']} — {celeb['date']}\n"
        msg += f"{celeb['headline']}\n"
        msg += f"<a href='{celeb['url']}'>Read more</a>\n\n"

    # Timeline
    events = []
    if articles:
        events.append((articles[0]['date'], "📰 First news article", articles[0]['source']))
    if videos:
        events.append((videos[0]['date'], "🎬 First YouTube video", videos[0]['channel']))
    if reddit_posts:
        events.append((reddit_posts[0]['date'], "👽 First Reddit post", f"r/{reddit_posts[0]['subreddit']}"))
    if celeb:
        events.append((celeb['date'], "🌟 Celebrity trigger", celeb['celebrity']))
    if trends:
        events.append((trends['first_spike'], "🔍 Google search spike", f"{trends['peak_score']}/100"))

    events.sort(key=lambda x: x[0])
    if events:
        msg += "🗺 <b>Origin Timeline</b>\n"
        for date, event, detail in events:
            msg += f"  {date} → {event} ({detail})\n"

    if not any([trends, articles, videos, reddit_posts, wiki]):
        msg += "❌ No data found. Try a different keyword."

    return msg


# ─── TELEGRAM COMMANDS ──────────────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(
        message,
        "👋 <b>Welcome to Viral Origin Researcher!</b>\n\n"
        "Type:\n"
        "/research labubu dolls\n"
        "/research stanley cup\n"
        "/research ChatGPT\n\n"
        "I will check News, YouTube, Reddit, Wikipedia and Google Trends for you! 🔍",
        parse_mode="HTML"
    )


@bot.message_handler(commands=['research'])
def research_command(message):
    keyword = message.text.replace("/research", "").strip()
    if not keyword:
        bot.reply_to(message, "⚠️ Please add a topic!\nExample: /research labubu dolls")
        return
    loading = bot.reply_to(
        message,
        f"🔍 Researching <b>{keyword}</b>...\nChecking News, YouTube, Reddit, Wikipedia & Google Trends ⏳",
        parse_mode="HTML"
    )
    try:
        report = build_report(keyword)
        bot.delete_message(message.chat.id, loading.message_id)
        # Split into chunks if too long (Telegram limit = 4096 chars)
        if len(report) <= 4096:
            bot.send_message(message.chat.id, report, parse_mode="HTML", disable_web_page_preview=True)
        else:
            chunks = [report[i:i+4000] for i in range(0, len(report), 4000)]
            for chunk in chunks:
                bot.send_message(message.chat.id, chunk, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")


@bot.message_handler(commands=['help'])
def help_command(message):
    bot.reply_to(
        message,
        "📖 <b>Commands</b>\n\n"
        "/research [topic] — Full origin report from 5 sources\n"
        "/start — Welcome message\n"
        "/help — This message\n\n"
        "<b>Sources checked:</b>\n"
        "📰 News articles\n"
        "🎬 YouTube videos\n"
        "👽 Reddit discussions\n"
        "📖 Wikipedia summary\n"
        "📈 Google Trends\n"
        "🌟 Celebrity triggers",
        parse_mode="HTML"
    )


if __name__ == "__main__":
    print("🤖 Telegram Research Bot started...")
    bot.infinity_polling()
