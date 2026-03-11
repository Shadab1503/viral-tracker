import os
import requests
import pandas as pd
from datetime import datetime
from apify_client import ApifyClient
from newsapi import NewsApiClient
from pytrends.request import TrendReq
import telebot

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
APIFY_TOKEN = os.environ.get("APIFY_TOKEN")
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
apify = ApifyClient(APIFY_TOKEN)
newsapi = NewsApiClient(api_key=NEWSAPI_KEY)


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


def get_first_news(keyword):
    try:
        result = newsapi.get_everything(
            q=keyword,
            language="en",
            sort_by="publishedAt",
            from_param="2020-01-01",
            page_size=5
        )
        articles = sorted(result.get("articles", []), key=lambda x: x["publishedAt"])
        if not articles:
            return None
        a = articles[0]
        return {
            "title": a["title"],
            "source": a["source"]["name"],
            "date": a["publishedAt"][:10],
            "url": a["url"]
        }
    except Exception as e:
        return None


def get_celebrity_trigger(keyword):
    celebs = ["Lisa BLACKPINK", "Rihanna", "Dua Lipa", "Kylie Jenner", "Kim Kardashian", "Selena Gomez"]
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


def build_report(keyword):
    trends = get_google_trends(keyword)
    news = get_first_news(keyword)
    celeb = get_celebrity_trigger(keyword)

    msg = f"🔬 <b>ORIGIN REPORT: {keyword.upper()}</b>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

    if trends:
        msg += "📈 <b>Google Trends</b>\n"
        msg += f"First Spike: <b>{trends['first_spike']}</b>\n"
        msg += f"Peak: {trends['peak_month']} ({trends['peak_score']}/100)\n"
        if trends['related']:
            msg += f"Related: {', '.join(trends['related'])}\n"
        msg += "\n"

    if news:
        msg += "📰 <b>First News Article</b>\n"
        msg += f"Date: {news['date']} — {news['source']}\n"
        msg += f"{news['title']}\n"
        msg += f"<a href='{news['url']}'>Read article</a>\n\n"

    if celeb:
        msg += "🌟 <b>Celebrity Trigger</b>\n"
        msg += f"{celeb['celebrity']} — {celeb['date']}\n"
        msg += f"{celeb['headline']}\n"
        msg += f"<a href='{celeb['url']}'>Read more</a>\n\n"

    if not trends and not news and not celeb:
        msg += "❌ No data found. Try a different keyword."

    return msg


@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(
        message,
        "👋 <b>Welcome to Viral Origin Researcher!</b>\n\nType:\n/research labubu dolls\n/research stanley cup\n/research ChatGPT",
        parse_mode="HTML"
    )


@bot.message_handler(commands=['research'])
def research_command(message):
    keyword = message.text.replace("/research", "").strip()
    if not keyword:
        bot.reply_to(message, "⚠️ Please add a topic!\nExample: /research labubu dolls")
        return
    loading = bot.reply_to(message, f"🔍 Researching <b>{keyword}</b>... please wait ⏳", parse_mode="HTML")
    try:
        report = build_report(keyword)
        bot.delete_message(message.chat.id, loading.message_id)
        bot.send_message(message.chat.id, report, parse_mode="HTML", disable_web_page_preview=False)
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")


if __name__ == "__main__":
    print("🤖 Telegram Research Bot started...")
    bot.infinity_polling()
