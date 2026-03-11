# researcher.py
# pip install pytrends newsapi-python apify-client requests pandas rich

import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from apify_client import ApifyClient
from newsapi import NewsApiClient
from pytrends.request import TrendReq
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint

# ─── CONFIG ────────────────────────────────────────────────────────────────
APIFY_TOKEN  = os.environ.get("APIFY_TOKEN")
NEWSAPI_KEY  = os.environ.get("NEWSAPI_KEY")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

apify   = ApifyClient(APIFY_TOKEN)
newsapi = NewsApiClient(api_key=NEWSAPI_KEY)
console = Console()


# ─── 1. GOOGLE TRENDS — Find when search volume FIRST SPIKED ───────────────
def get_google_trends_origin(keyword):
    """
    Returns the week the keyword first had a major search spike.
    Searches back 5 years of Google Trends data.
    """
    console.print(f"\n[cyan]🔍 Checking Google Trends for '{keyword}'...[/cyan]")
    try:
        pytrend = TrendReq(hl='en-US', tz=360)
        pytrend.build_payload(
            [keyword],
            timeframe='today 5-y',   # last 5 years
            geo='US'                 # USA focus
        )
        df = pytrend.interest_over_time()
        if df.empty:
            return None, None

        # Find the first week where interest crossed 20/100
        spike_weeks = df[df[keyword] >= 20]
        if spike_weeks.empty:
            return None, None

        first_spike_date = spike_weeks.index[0].strftime("%B %Y")
        peak_date        = df[keyword].idxmax().strftime("%B %Y")
        peak_score       = df[keyword].max()

        # Related queries — shows WHY it went viral
        related = pytrend.related_queries()
        rising  = related.get(keyword, {}).get("rising", pd.DataFrame())
        top_related = rising["query"].tolist()[:5] if not rising.empty else []

        return {
            "first_spike": first_spike_date,
            "peak_month": peak_date,
            "peak_score": int(peak_score),
            "related_queries": top_related,
            "full_data": df
        }
    except Exception as e:
        console.print(f"[red]Google Trends error: {e}[/red]")
        return None


# ─── 2. NEWS — Find the OLDEST article ever written about this topic ────────
def get_first_news_article(keyword):
    """
    Searches NewsAPI for the oldest article mentioning this keyword.
    Sorts by oldest first to find origin coverage.
    """
    console.print(f"[cyan]📰 Searching earliest news articles for '{keyword}'...[/cyan]")
    try:
        # Search from 2020 to now, sorted oldest first
        result = newsapi.get_everything(
            q=keyword,
            language="en",
            sort_by="publishedAt",
            from_param="2020-01-01",
            page_size=10
        )

        articles = result.get("articles", [])
        if not articles:
            return None

        # Sort by date ascending to get oldest first
        articles.sort(key=lambda x: x["publishedAt"])

        earliest = articles[0]
        return {
            "title":      earliest["title"],
            "source":     earliest["source"]["name"],
            "date":       earliest["publishedAt"][:10],
            "url":        earliest["url"],
            "description": earliest.get("description", "")[:200]
        }
    except Exception as e:
        console.print(f"[red]NewsAPI error: {e}[/red]")
        return None


# ─── 3. TIKTOK — Find earliest viral TikTok videos about this topic ─────────
def get_first_tiktok_videos(keyword):
    """
    Uses Apify TikTok Keyword Search Scraper to find
    earliest TikToks about this topic sorted by date.
    """
    console.print(f"[cyan]🎵 Searching earliest TikTok videos for '{keyword}'...[/cyan]")
    try:
        run = apify.actor("sociavault/tiktok-keyword-search-scraper").call(
            run_input={
                "query":      keyword,
                "sort_by":    "date",        # oldest first
                "region":     "US",
                "max_results": 5
            }
        )
        items = list(apify.dataset(run["defaultDatasetId"]).iterate_items())

        videos = []
        for item in items:
            videos.append({
                "author":    item.get("author", {}).get("nickname", "Unknown"),
                "desc":      item.get("desc", "")[:100],
                "likes":     item.get("stats", {}).get("diggCount", 0),
                "views":     item.get("stats", {}).get("playCount", 0),
                "date":      item.get("createTime", ""),
                "url":       f"https://tiktok.com/@{item.get('author',{}).get('uniqueId','')}/video/{item.get('id','')}"
            })

        # Sort by date ascending
        videos.sort(key=lambda x: x["date"])
        return videos[:3]

    except Exception as e:
        console.print(f"[red]TikTok error: {e}[/red]")
        return []


# ─── 4. CELEBRITY CHECK — Who first posted about this topic? ────────────────
def get_celebrity_trigger(keyword):
    """
    Checks if any celebrity was linked to the keyword going viral.
    Searches news for [keyword + celebrity names].
    """
    console.print(f"[cyan]🌟 Checking celebrity triggers for '{keyword}'...[/cyan]")
    celebs = [
        "Lisa BLACKPINK", "Rihanna", "Dua Lipa", "Kylie Jenner",
        "Kim Kardashian", "Selena Gomez", "Doja Cat", "Charli D'Amelio",
        "MrBeast", "Billie Eilish", "Cardi B", "Ariana Grande"
    ]
    hits = []
    for celeb in celebs:
        try:
            result = newsapi.get_everything(
                q=f"{keyword} {celeb}",
                language="en",
                sort_by="publishedAt",
                page_size=1
            )
            if result["totalResults"] > 0:
                article = result["articles"][0]
                hits.append({
                    "celebrity": celeb,
                    "headline":  article["title"],
                    "date":      article["publishedAt"][:10],
                    "url":       article["url"]
                })
        except:
            continue
    return hits


# ─── 5. DISPLAY RESULTS ─────────────────────────────────────────────────────
def display_results(keyword, trends, news, tiktoks, celebs):
    console.print(Panel(
        f"[bold yellow]🔬 ORIGIN RESEARCH REPORT[/bold yellow]\n[white]Topic: {keyword.upper()}[/white]",
        style="bold blue"
    ))

    # Google Trends Section
    if trends:
        console.print("\n[bold green]📈 GOOGLE TRENDS ORIGIN[/bold green]")
        console.print(f"  🗓  First Spike:  [yellow]{trends['first_spike']}[/yellow]")
        console.print(f"  🔥 Peak Month:   [yellow]{trends['peak_month']}[/yellow]")
        console.print(f"  📊 Peak Score:   [yellow]{trends['peak_score']}/100[/yellow]")
        if trends['related_queries']:
            console.print(f"  🔗 Why it spread: [cyan]{', '.join(trends['related_queries'])}[/cyan]")

    # First News Article
    if news:
        console.print("\n[bold green]📰 FIRST NEWS ARTICLE[/bold green]")
        console.print(f"  📅 Date:    [yellow]{news['date']}[/yellow]")
        console.print(f"  📰 Source:  [cyan]{news['source']}[/cyan]")
        console.print(f"  📌 Title:   {news['title']}")
        console.print(f"  🔗 URL:     [blue]{news['url']}[/blue]")
        if news['description']:
            console.print(f"  📝 Summary: [dim]{news['description']}[/dim]")

    # TikTok Videos
    if tiktoks:
        console.print("\n[bold green]🎵 EARLIEST TIKTOK VIDEOS[/bold green]")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Date")
        table.add_column("Creator")
        table.add_column("Views")
        table.add_column("Likes")
        table.add_column("URL")
        for v in tiktoks:
            date_str = datetime.fromtimestamp(int(v["date"])).strftime("%Y-%m-%d") if v["date"] else "?"
            table.add_row(
                date_str,
                f"@{v['author']}",
                f"{v['views']:,}",
                f"{v['likes']:,}",
                v["url"][:40] + "..."
            )
        console.print(table)

    # Celebrity Triggers
    if celebs:
        console.print("\n[bold green]🌟 CELEBRITY TRIGGERS[/bold green]")
        for c in celebs[:3]:
            console.print(f"  ⚡ [yellow]{c['celebrity']}[/yellow] — {c['date']}")
            console.print(f"     {c['headline']}")
            console.print(f"     [blue]{c['url']}[/blue]")

    # Timeline Summary
    console.print("\n[bold green]🗺  ORIGIN TIMELINE[/bold green]")
    events = []
    if news:
        events.append((news['date'], "📰 First news article", news['source']))
    if tiktoks:
        for v in tiktoks[:1]:
            d = datetime.fromtimestamp(int(v["date"])).strftime("%Y-%m-%d") if v["date"] else "?"
            events.append((d, f"🎵 First TikTok", f"@{v['author']} | {v['views']:,} views"))
    if celebs:
        events.append((celebs[0]['date'], f"🌟 Celebrity mention", celebs[0]['celebrity']))
    if trends:
        events.append((trends['first_spike'], "🔍 Google search spike", f"Score: {trends['peak_score']}/100"))

    events.sort(key=lambda x: x[0])
    for date, event, detail in events:
        console.print(f"  [yellow]{date}[/yellow] → {event} [dim]({detail})[/dim]")


# ─── 6. SEND TO TELEGRAM (optional) ─────────────────────────────────────────
def send_to_telegram(keyword, trends, news, tiktoks, celebs):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    msg  = f"🔬 <b>ORIGIN RESEARCH: {keyword.upper()}</b>\n\n"
    if trends:
        msg += f"📈 <b>Google Trends</b>\n"
        msg += f"First Spike: {trends['first_spike']}\n"
        msg += f"Peak: {trends['peak_month']} ({trends['peak_score']}/100)\n\n"
    if news:
        msg += f"📰 <b>First News Article</b>\n"
        msg += f"{news['date']} — {news['source']}\n"
        msg += f"{news['title']}\n{news['url']}\n\n"
    if tiktoks:
        msg += f"🎵 <b>First TikTok</b>\n"
        d = datetime.fromtimestamp(int(tiktoks[0]["date"])).strftime("%Y-%m-%d")
        msg += f"{d} — @{tiktoks[0]['author']} | {tiktoks[0]['views']:,} views\n\n"
    if celebs:
        msg += f"🌟 <b>Celebrity Trigger</b>\n"
        msg += f"{celebs[0]['celebrity']} — {celebs[0]['date']}\n"

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
    )
    console.print("[green]✅ Report sent to Telegram![/green]")


# ─── 7. MAIN ENTRY POINT ────────────────────────────────────────────────────
if __name__ == "__main__":
    console.print("[bold yellow]🔬 USA Topic Origin Researcher[/bold yellow]")
    console.print("[dim]Type any topic to find when & why it went viral[/dim]\n")

    while True:
        keyword = input("🔍 Enter topic (or 'quit'): ").strip()
        if keyword.lower() == "quit":
            break
        if not keyword:
            continue

        trends = get_google_trends_origin(keyword)
        news   = get_first_news_article(keyword)
        tiktoks = get_first_tiktok_videos(keyword)
        celebs  = get_celebrity_trigger(keyword)

        display_results(keyword, trends, news, tiktoks, celebs)
        send_to_telegram(keyword, trends, news, tiktoks, celebs)
        console.print("\n" + "="*60 + "\n")
