import os
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()

# Telegram Credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_IDS: List[int] = [
    int(uid.strip())
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip().isdigit()
]

# AI Configuration (Gemini default, Anthropic Claude optional)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")

# Operational Parameters
NEWS_POLL_INTERVAL_MINUTES = int(os.getenv("NEWS_POLL_INTERVAL_MINUTES", "2"))
MARKET_CHECK_INTERVAL_MINUTES = int(os.getenv("MARKET_CHECK_INTERVAL_MINUTES", "5"))
DIGEST_INTERVAL_MINUTES = int(os.getenv("DIGEST_INTERVAL_MINUTES", "30"))
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
DB_PATH = os.getenv("DB_PATH", "data/intelligence_bot.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "data/backups")

# All 28 Indian States & 8 UTs for coverage
INDIAN_STATES_AND_UTS = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry"
]

# News Feed Sources
RSS_FEEDS: List[Dict[str, str]] = [
    # Press Information Bureau (PIB)
    {"name": "PIB National", "url": "https://pib.gov.in/RssMain.aspx?ModId=6&LangId=1", "region": "National", "type": "official"},
    {"name": "PIB Prime Minister", "url": "https://pib.gov.in/RssMain.aspx?ModId=2&LangId=1", "region": "National", "type": "official"},
    
    # National Indian News Outlets
    {"name": "NDTV Top Stories", "url": "https://feeds.feedburner.com/ndtvnews-top-stories", "region": "National", "type": "national"},
    {"name": "The Hindu National", "url": "https://www.thehindu.com/news/national/feeder/default.rss", "region": "National", "type": "national"},
    {"name": "Indian Express India", "url": "https://indianexpress.com/section/india/feed/", "region": "National", "type": "national"},
    {"name": "Hindustan Times India", "url": "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml", "region": "National", "type": "national"},
    {"name": "DD News", "url": "https://ddnews.gov.in/rss/national", "region": "National", "type": "official"},

    # International News (USA, China, Pakistan, Russia, Middle East, Europe, UN, Wars)
    {"name": "BBC World", "url": "http://feeds.bbci.co.uk/news/world/rss.xml", "region": "International", "type": "international"},
    {"name": "Reuters World", "url": "https://www.reutersagency.com/feed/?taxonomy=best-sectors&post_type=best", "region": "International", "type": "international"},
    {"name": "Al Jazeera English", "url": "https://www.aljazeera.com/xml/rss/all.xml", "region": "International", "type": "international"},
    {"name": "Associated Press World", "url": "https://feedx.net/rss/apnews.xml", "region": "International", "type": "international"},

    # Business & Financial News
    {"name": "Economic Times Markets", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "region": "Financial", "type": "market"},
    {"name": "Livemint Markets", "url": "https://www.livemint.com/rss/markets", "region": "Financial", "type": "market"},
    {"name": "Business Standard", "url": "https://www.business-standard.com/rss/markets-106.rss", "region": "Financial", "type": "market"},
]

def get_google_news_rss(query: str) -> str:
    from urllib.parse import quote_plus
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"

# Add State Queries
for state in INDIAN_STATES_AND_UTS:
    RSS_FEEDS.append({
        "name": f"GNews-{state}",
        "url": get_google_news_rss(f"{state} news politics OR crime OR weather OR accident OR court"),
        "region": state,
        "type": "state"
    })

# Market Tickers Configuration
MARKET_TICKERS = {
    "NIFTY_50": "^NSEI",
    "SENSEX": "^BSESN",
    "BANK_NIFTY": "^NSEBANK",
    "USD_INR": "INR=X",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "CRUDE_OIL": "CL=F",
    "NATURAL_GAS": "NG=F",
    "COPPER": "HG=F",
}
