# telegram_bot.py
# pip install pyTelegramBotAPI pytrends newsapi-python apify-client requests

import os
import telebot
import requests
import pandas as pd
from datetime import datetime
from apify_client import ApifyClient
from newsapi import NewsApiClient
from pytrends.request import TrendReq

# ─── CONFIG ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
APIFY_TOKEN      = os.environ.get("APIFY_TOKEN")
NEWSAPI_KEY      = os.environ.get("NEWSAPI_KEY")

bot     = telebot.TeleBot(TELEGRAM_TOKEN)
apify   = ApifyClient(APIFY_TOKEN)
newsapi = NewsApiClient(api_key=NEWSAPI_KEY)


# ─── RESEARCH FUNCTIONS (same as researcher.py) ─────────────────────────────
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
        rising  = related.get(keyword, {}).get("rising", pd.DataFrame())
        top_related = rising["query"].tolist()[:3] if not rising.empty else []
        return {
            "first_spike": spike_weeks.index[0].strftime("%B %Y"),
            "peak_month":  df[keyword].idxmax().strftime("%B %Y"),
            "peak_score":  int(df[keyword].max()),
            "related":     top_related
        }
    except:
        return None

def get_first_news(keyword):
    try:
        result = newsapi.get_everything(
            q=keyword, language="en",
            sort_by="publishedAt",
            from_param="2020-01-01",
            page_size=5
        )
        articles = sorted(result.get("articles", []), key=lambda x: x["publishedAt"])
        if not articles:
            return None
        a = articles[0]
        return {
            "title":  a["title"],
            "source": a["source"]["name"],
            "date":   a["publishedAt"][:10],
            "url":    a["url"]
        }
    except:
        return None

def get_first_tiktok(keyword):
    try:
        run = apify.actor("sociavault/tiktok-keyword-search-scraper").call(
            run_input={"query": keyword, "sort_by": "date", "region": "US", "max_results": 3}
        )
        items = list(apify.dataset(run["defaultDatasetId"]).iterate_items())
        if not items:
            return None
        items.sort(key=lambda x: x.get("createTime", ""))
        v = items[0]
        return {
            "author": v.get("author", {}).get("nickname", "Unknown"),
            "views":  v.get("stats", {}).get("playCount", 0),
            "date":   datetime.fromtimestamp(int(v.get("createTime", 0))).strftime("%Y-%m-%d"),
            "url":    f"https://tiktok.com/@{v.get('author',{}).get('uniqueId','')}/video/{v.get('id','')}"
        }
    except:
        return None

def get_celebrity_trigger(keyword):
    celebs = [
        "Lisa BLACKPINK", "Rihanna", "Dua Lipa", "Kylie Jenner",
        "Kim Kardashian", "Selena Gomez", "Charli D'Amelio", "MrBeast"
    ]
    for celeb in celebs:
        try:
            result = newsapi.get_everything(
                q=f"{keyword} {celeb}", language="en",
                sort_by="publishedAt", page_size=1
            )
            if result["totalResults"] > 0:
                a = result["articles"][0]
                return {
                    "celebrity": celeb,
                    "date":      a["publishedAt"][:10],
                    "headline":  a["title"],
                    "url":       a["url"]
                }
        except:
            continue
    return None


# ─── BUILD THE TELEGRAM REPORT MESSAGE ──────────────────────────────────────
def build_report(keyword):
    trends = get_google_trends(keyword)
    news   = get_first_news(keyword)
    tiktok = get_first_tiktok(keyword)
    celeb  = get_celebrity_trigger(keyword)

    msg = f"🔬 <b>ORIGIN REPORT: {keyword.upper()}</b>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

        if trends:
        msg += f"📈 <b>Google Trends</b>\n"
        msg += f"First Spike: <b>{trends['first_spike']}</b>\n"
        msg += f"Peak: {trends['peak_month']} ({trends['peak_score']}/100)\n"
        if trends['related']:
            msg += f"Why it spread: {', '.join(trends['related'])}\n"
        msg += "\n"
