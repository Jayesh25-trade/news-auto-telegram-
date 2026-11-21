import os
import sys
import shutil
import logging
import threading
import re
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, time
import pytz

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import (
    TELEGRAM_BOT_TOKEN,
    ALLOWED_USER_IDS,
    NEWS_POLL_INTERVAL_MINUTES,
    MARKET_CHECK_INTERVAL_MINUTES,
    MAX_BROADCAST_PER_POLL,
    TIMEZONE,
    DB_PATH,
    BACKUP_DIR
)
from database import Database
from deduplicator import Deduplicator
from news_scraper import NewsScraper
from ai_processor import AIProcessor
from market_service import MarketService

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("NewsMarketBot")

# Initialize Singletons
db = Database(DB_PATH)
dedup = Deduplicator()
scraper = NewsScraper(dedup)
ai = AIProcessor()
market = MarketService()
scheduler = AsyncIOScheduler(timezone=pytz.timezone(TIMEZONE))

# Healthcheck Server for Render/Uptime Keep-Alive
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', '48')
            self.end_headers()
            self.wfile.write(b"24x7 News & Market Intelligence Bot is Running! OK")
        except Exception:
            pass

    def log_message(self, format, *args):
        pass

def run_health_server(port: int = 10000):
    for p in [port, 10000, 8080, 80]:
        try:
            server = HTTPServer(('0.0.0.0', p), HealthHandler)
            logger.info(f"Health-check web server listening on port {p} (Render ready)")
            server.serve_forever()
            break
        except Exception as e:
            logger.debug(f"Port {p} not available: {e}")

# Helper: Authorization Check
def is_authorized(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True # Default to open if not specified, but warned in logs
    return user_id in ALLOWED_USER_IDS

def is_in_quiet_hours(prefs: dict) -> bool:
    if not prefs.get("quiet_hours_enabled"):
        return False
    tz = pytz.timezone(TIMEZONE)
    now_time = datetime.now(tz).time()
    try:
        start_h, start_m = map(int, prefs.get("quiet_hours_start", "23:00").split(":"))
        end_h, end_m = map(int, prefs.get("quiet_hours_end", "07:00").split(":"))
        start = time(start_h, start_m)
        end = time(end_h, end_m)
        if start > end: # Spans midnight (e.g. 23:00 to 07:00)
            return now_time >= start or now_time <= end
        return start <= now_time <= end
    except Exception:
        return False

# ========================================================
# News Processing & Distribution Job
# ========================================================
is_first_sync = True

async def poll_news_job(app: Application):
    """Fetches fresh news (past 24h only), dedups, invokes Hinglish analysis, saves to DB & alerts."""
    global is_first_sync
    try:
        raw_articles = await scraper.fetch_all_feeds()
        if not raw_articles:
            return

        new_saved_articles = []

        for raw in raw_articles:
            # Check DB URL Hash
            if await db.article_exists(raw["url_hash"]):
                continue

            # Extract full article text and lead image with trafilatura
            extracted = await scraper.extract_full_content_and_image(raw["source_url"])
            full_content = extracted.get("full_text") or raw["initial_summary"]
            image_url = extracted.get("image_url") or raw.get("image_url")

            # AI Analysis (Hinglish 2-3 lines, category, state, importance 1-5, is_breaking)
            ai_data = await ai.analyze_and_summarize(
                title=raw["title"],
                raw_content=full_content,
                default_region=raw["region_hint"]
            )

            article_record = {
                "url_hash": raw["url_hash"],
                "title": raw["title"],
                "source_url": raw["source_url"],
                "source_name": raw["source_name"],
                "published_at": raw["published_at"],
                "state": ai_data.get("state", raw["region_hint"]),
                "category": ai_data.get("category", "General"),
                "importance_score": ai_data.get("importance_score", 3),
                "is_breaking": ai_data.get("is_breaking", False),
                "summary_hinglish": ai_data.get("summary_hinglish", raw["title"]),
                "full_content": full_content,
                "image_url": image_url,
                "dedup_hash": dedup.normalize_text(raw["title"])
            }

            article_id = await db.insert_article(article_record)
            article_record["id"] = article_id
            dedup.mark_seen(raw["title"], raw["source_url"])
            new_saved_articles.append(article_record)

        if not new_saved_articles:
            return

        # On initial boot, avoid spamming dozens of notifications at once!
        # Only broadcast up to 3 highest priority items (score >= 4)
        if is_first_sync:
            is_first_sync = False
            broadcast_list = [a for a in new_saved_articles if a.get("importance_score", 3) >= 4][:3]
        else:
            # Sort by importance and limit to MAX_BROADCAST_PER_POLL
            new_saved_articles.sort(key=lambda x: (x.get("is_breaking", False), x.get("importance_score", 3)), reverse=True)
            broadcast_list = new_saved_articles[:MAX_BROADCAST_PER_POLL]

        # Dispatch notifications to allowed users
        for article_record in broadcast_list:
            article_id = article_record.get("id")
            for uid in ALLOWED_USER_IDS:
                prefs = await db.get_user_preferences(uid)
                importance = article_record["importance_score"]
                is_breaking = article_record["is_breaking"]

                # Quiet Hours Check: During quiet hours, only breaking passes immediately
                in_quiet = is_in_quiet_hours(prefs)
                if in_quiet and not is_breaking:
                    await db.queue_for_digest(uid, article_id)
                    continue

                # Digest Mode Check: If user enabled digest, queue normal news
                if prefs.get("digest_mode") and not is_breaking:
                    await db.queue_for_digest(uid, article_id)
                    continue

                # Min Importance Filter (default min 3)
                if importance < prefs.get("min_importance", 3) and not is_breaking:
                    continue

                # State Filter
                sub_states = prefs.get("subscribed_states", "ALL")
                if sub_states != "ALL":
                    states_list = [s.strip().lower() for s in sub_states.split(",")]
                    if article_record["state"].lower() not in states_list:
                        continue

                # Format Telegram Message
                tag = "🔴 **BREAKING NEWS**" if is_breaking else f"🟡 **PRIORITY [{importance}/5]**"
                msg_text = (
                    f"{tag}\n"
                    f"📍 **{article_record['state']}** | 🏷️ #{article_record['category']}\n\n"
                    f"📰 **{article_record['title']}**\n\n"
                    f"{article_record['summary_hinglish']}\n\n"
                    f"⏰ {article_record['published_at']} | 🌐 {article_record['source_name']}"
                )

                keyboard = [
                    [
                        InlineKeyboardButton("📖 Full News (AI)", callback_data=f"full_{article_id}"),
                        InlineKeyboardButton("🔗 Source", url=article_record["source_url"])
                    ]
                ]
                if article_record.get("image_url"):
                    keyboard[0].insert(1, InlineKeyboardButton("🖼️ Image", callback_data=f"img_{article_id}"))

                try:
                    await app.bot.send_message(
                        chat_id=uid,
                        text=msg_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        disable_web_page_preview=True
                    )
                    await asyncio.sleep(1.0)
                except Exception as e:
                    logger.error(f"Failed to send telegram news message to {uid}: {e}")

    except Exception as e:
        logger.error(f"Error in poll_news_job: {e}", exc_info=True)

# ========================================================
# Market Alert Checker Job
# ========================================================
async def check_market_alerts_job(app: Application):
    try:
        active_alerts = await db.get_active_market_alerts()
        for alert in active_alerts:
            symbol = alert["symbol"]
            target = alert["target_price"]
            cond = alert["condition"]
            quote = await market.get_ticker_quote(symbol)
            if not quote or not quote.get("price"):
                continue

            current_p = quote["price"]
            triggered = False
            if cond == "ABOVE" and current_p >= target:
                triggered = True
            elif cond == "BELOW" and current_p <= target:
                triggered = True

            if triggered:
                await db.mark_alert_triggered(alert["id"])
                msg = (
                    f"🚨 **MARKET PRICE ALERT TRIGGERED!**\n\n"
                    f"Ticker: **{symbol}**\n"
                    f"Target Condition: **{cond} {target}**\n"
                    f"Current Live Price: **{current_p}**\n"
                    f"Change: {quote['change']} ({quote['pct_change']}%)"
                )
                await app.bot.send_message(
                    chat_id=alert["user_id"],
                    text=msg,
                    parse_mode=ParseMode.MARKDOWN
                )
    except Exception as e:
        logger.error(f"Error checking market alerts: {e}")

# ========================================================
# Scheduled Market Broadcasts
# ========================================================
async def send_scheduled_market_update(app: Application, stage: str):
    msg = await market.format_market_summary_hinglish(stage)
    for uid in ALLOWED_USER_IDS:
        try:
            await app.bot.send_message(chat_id=uid, text=msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Failed to send scheduled market update to {uid}: {e}")

# ========================================================
# Digest Mode Job (Hourly / Half-Hourly)
# ========================================================
async def flush_digest_job(app: Application):
    for uid in ALLOWED_USER_IDS:
        articles = await db.pop_digest_articles(uid)
        if not articles:
            continue

        header = f"🗞️ **NEWS DIGEST ({len(articles)} Stories)**\n\n"
        items_text = []
        for a in articles[:10]: # Top 10 digest items
            items_text.append(f"• **[{a['state']}]** {a['title']}\n  _{a['summary_hinglish']}_\n")

        digest_body = header + "\n".join(items_text)
        try:
            await app.bot.send_message(chat_id=uid, text=digest_body, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Failed to send digest to {uid}: {e}")

# ========================================================
# Automated Daily Database Backup
# ========================================================
async def backup_database_job(app: Application):
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BACKUP_DIR, f"backup_{timestamp}.db")
        shutil.copy2(DB_PATH, backup_file)
        logger.info(f"Database backed up to {backup_file}")
        
        # Notify Admin
        for uid in ALLOWED_USER_IDS:
            await app.bot.send_message(
                chat_id=uid,
                text=f"💾 **Daily Backup Complete**: `{backup_file}`"
            )
    except Exception as e:
        logger.error(f"Backup failed: {e}")

# ========================================================
# Telegram Bot Command Handlers
# ========================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Unauthorized. This is a private intelligence bot.")
        return

    text = """👋 **Namaste! Welcome to your 24x7 News & Market Intelligence Bot.**

Main aapke liye Bharat ke har state/district se lekar international affairs aur live financial markets ko 24 ghante track karta hoon. Har summary simple Hinglish me aayegi.

🛠️ **Available Commands:**
• `/latest` - Top 5 latest news updates
• `/state <name>` - Filter news by state (e.g. `/state Bihar`)
• `/world` - Global & international developments
• `/market` - Live Nifty, Sensex, Gold, Crude & USD/INR
• `/gold` - Gold & Silver rates (domestic & global)
• `/search <topic>` - Search past news articles
• `/watchlist` - View your tracked stocks
• `/alert <symbol> <above|below> <price>` - Set live price alert
• `/digest` - Check pending news digest
• `/settings` - Configure quiet hours & preferences

💬 **AI Chat & Deep-Dive:**
Aap news ke neeche **[📖 Full News]** dabakar complete investigative breakdown dekh sakte hain ya direct koi bhi sawal pooch sakte hain (e.g., *"Iska share market par kya asar hoga?"*)."""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    articles = await db.get_recent_articles(limit=5)
    if not articles:
        await update.message.reply_text("Abhi database me koi news nahi hai. Agle poll ka wait karein.")
        return

    for a in articles:
        tag = "🔴 BREAKING" if a["is_breaking"] else f"🟡 [{a['importance_score']}/5]"
        text = f"{tag} | 📍 **{a['state']}**\n📰 **{a['title']}**\n\n{a['summary_hinglish']}"
        kb = [[InlineKeyboardButton("📖 Full News (AI)", callback_data=f"full_{a['id']}"),
               InlineKeyboardButton("🔗 Source", url=a['source_url'])]]
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)

async def cmd_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Kripya state ka naam likhein. Example: `/state Uttar Pradesh`", parse_mode=ParseMode.MARKDOWN)
        return
    state_query = " ".join(context.args).strip()
    articles = await db.search_articles_fts(state_query, limit=5)
    if not articles:
        await update.message.reply_text(f"'{state_query}' ke liye abhi koi taaza khabar nahi mili.")
        return

    for a in articles:
        text = f"📍 **{a['state']}** | 🏷️ #{a['category']}\n📰 **{a['title']}**\n\n{a['summary_hinglish']}"
        kb = [[InlineKeyboardButton("📖 Full News (AI)", callback_data=f"full_{a['id']}"),
               InlineKeyboardButton("🔗 Source", url=a['source_url'])]]
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)

async def cmd_world(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    articles = await db.search_articles_fts("International", limit=5)
    if not articles:
        await update.message.reply_text("International news fetch ho rahi hai...")
        return
    for a in articles:
        text = f"🌍 **INTERNATIONAL** | 📍 {a['state']}\n📰 **{a['title']}**\n\n{a['summary_hinglish']}"
        kb = [[InlineKeyboardButton("📖 Full News (AI)", callback_data=f"full_{a['id']}"),
               InlineKeyboardButton("🔗 Source", url=a['source_url'])]]
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)

async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    msg = await market.format_market_summary_hinglish("LIVE")
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def cmd_gold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    msg = await market.get_gold_silver_rates_hinglish()
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Search topic specify karein. Example: `/search monsoon`", parse_mode=ParseMode.MARKDOWN)
        return
    query = " ".join(context.args)
    results = await db.search_articles_fts(query, limit=4)
    if not results:
        await update.message.reply_text(f"'{query}' se related koi news nahi mili.")
        return
    for a in results:
        text = f"🔍 **Search Result** | 📍 {a['state']}\n📰 **{a['title']}**\n\n{a['summary_hinglish']}"
        kb = [[InlineKeyboardButton("📖 Full News (AI)", callback_data=f"full_{a['id']}"),
               InlineKeyboardButton("🔗 Source", url=a['source_url'])]]
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)

async def cmd_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    # Syntax: /alert <symbol> <above/below> <price>
    if len(context.args) < 3:
        await update.message.reply_text("Syntax: `/alert <SYMBOL> <ABOVE|BELOW> <PRICE>`\nExample: `/alert ^NSEI ABOVE 25000`", parse_mode=ParseMode.MARKDOWN)
        return
    symbol, cond, price_str = context.args[0], context.args[1].upper(), context.args[2]
    try:
        price = float(price_str)
        if cond not in ["ABOVE", "BELOW"]:
            raise ValueError()
        await db.add_market_alert(update.effective_user.id, symbol, price, cond)
        await update.message.reply_text(f"✅ Alert set: **{symbol}** jab **{cond} {price}** hoga to notification aayega.", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await update.message.reply_text("Invalid arguments. Check numbers and condition (ABOVE/BELOW).")

async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if context.args and context.args[0].lower() == "add":
        symbol = context.args[1].upper()
        await db.add_to_watchlist(update.effective_user.id, symbol)
        await update.message.reply_text(f"✅ Added {symbol} to Watchlist.")
        return

    symbols = await db.get_watchlist(update.effective_user.id)
    if not symbols:
        await update.message.reply_text("Aapki watchlist khali hai. Add karne ke liye: `/watchlist add RELIANCE.NS`", parse_mode=ParseMode.MARKDOWN)
        return

    text = "📋 **Aapki Market Watchlist:**\n\n"
    for s in symbols:
        q = await market.get_ticker_quote(s)
        if q:
            arrow = "🟢" if q['change'] >= 0 else "🔴"
            text += f"• **{s}**: ₹{q['price']} ({arrow} {q['pct_change']}%)\n"
        else:
            text += f"• **{s}**: Data unavailable\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    articles = await db.pop_digest_articles(update.effective_user.id)
    if not articles:
        await update.message.reply_text("Koi pending digest news nahi hai.")
        return
    text = f"🗞️ **Manually Requested Digest ({len(articles)} Stories):**\n\n"
    for a in articles:
        text += f"• **[{a['state']}]** {a['title']}\n  _{a['summary_hinglish']}_\n\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    prefs = await db.get_user_preferences(update.effective_user.id)
    text = f"""⚙️ **Aapki Personal Settings:**

• **Min Importance Score:** {prefs['min_importance']}/5
• **Quiet Hours (Raat):** {'Enabled ✅' if prefs['quiet_hours_enabled'] else 'Disabled ❌'} ({prefs['quiet_hours_start']} to {prefs['quiet_hours_end']})
• **Digest Mode:** {'Enabled (Batched) ✅' if prefs['digest_mode'] else 'Instant Alerts ⚡'}
• **Subscribed States:** {prefs['subscribed_states']}

Settings change karne ke liye format:
`/set_importance <1-5>`
`/toggle_digest`
`/toggle_quiet`"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ========================================================
# Inline Button Callbacks (Full News RAG & Image)
# ========================================================
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # Protect against expired query timeout
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Query answer error/timeout: {e}")

    data = query.data
    if data.startswith("full_"):
        article_id = int(data.split("_")[1])
        article = await db.get_article_by_id(article_id)
        if not article:
            await query.edit_message_reply_markup(reply_markup=None)
            return

        status_msg = await query.message.reply_text("🤖 AI deep-dive analysis generate ho raha hai... Kripya thoda wait karein.")
        analysis = await ai.rag_deep_dive(article)
        try:
            await status_msg.edit_text(analysis, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await status_msg.edit_text(analysis)

    elif data.startswith("img_"):
        article_id = int(data.split("_")[1])
        article = await db.get_article_by_id(article_id)
        if article and article.get("image_url"):
            try:
                await query.message.reply_photo(photo=article["image_url"], caption=f"🖼️ {article['title']}")
            except Exception:
                await query.message.reply_text("Image preview load nahi ho paayi.")

# ========================================================
# Freeform AI Chatbot Message Handler (RAG Q&A + Live Search)
# ========================================================
async def chat_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    lower_text = user_text.lower()

    # 1. Greetings Intent
    if lower_text in ["hi", "hello", "hey", "namaste", "pranam", "kya haal hai", "kaise ho", "help"]:
        welcome_reply = (
            "👋 **Namaste! Main aapka 24x7 News & Market AI Assistant hoon.**\n\n"
            "Aaj **21 Nov 2026** ki taaza khabrein aur live updates ke liye aap yeh try kar sakte hain:\n"
            "• `/latest` - Top 5 latest news updates\n"
            "• `/market` - Live Nifty, Sensex, Gold & Commodities\n"
            "• Ya direct kisi topic/sheher ka naam likhein (jaise: *'Shegaon news'*, *'Maharashtra news'*, *'Nifty view'*)\n"
            "• Ya 'Next news' bolkar aur khabrein dekhein."
        )
        await update.message.reply_text(welcome_reply, parse_mode=ParseMode.MARKDOWN)
        return

    # 2. Next / More News Intent
    if any(k in lower_text for k in ["next news", "aur news", "more news", "latest news", "aur batao", "next"]):
        recent = await db.get_recent_articles(limit=4)
        if recent:
            await update.message.reply_text("📰 **Taaza News Updates (Today):**", parse_mode=ParseMode.MARKDOWN)
            for a in recent:
                tag = "🔴 BREAKING" if a["is_breaking"] else f"🟡 [{a['importance_score']}/5]"
                text = f"{tag} | 📍 **{a['state']}**\n📰 **{a['title']}**\n\n{a['summary_hinglish']}"
                kb = [[InlineKeyboardButton("📖 Full News (AI)", callback_data=f"full_{a['id']}"),
                       InlineKeyboardButton("🔗 Source", url=a['source_url'])]]
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)
            return

    status_msg = await update.message.reply_text("🔍 Jankari check kar raha hoon...")

    # 3. Clean search term for topic/location query (e.g., 'Shegaon news ?' -> 'Shegaon')
    clean_topic = re.sub(r'[^\w\s]', '', lower_text)
    clean_topic = re.sub(r'\b(news|khabar|update|updates|samachar|today|aaj|ki|ka|batao|kya|hai)\b', '', clean_topic).strip()

    search_query = clean_topic if clean_topic else user_text
    relevant_articles = await db.search_articles_fts(search_query, limit=4)

    # 4. If no DB articles found and user queried a topic/city (e.g., 'Shegaon'), fetch live news dynamically!
    if not relevant_articles and clean_topic and len(clean_topic) >= 3:
        try:
            live_items = await scraper.fetch_topic_news(clean_topic, limit=3)
            if live_items:
                for item in live_items:
                    ai_data = await ai.analyze_and_summarize(item["title"], item.get("initial_summary", ""), clean_topic.title())
                    item_rec = {
                        "url_hash": item["url_hash"],
                        "title": item["title"],
                        "source_url": item["source_url"],
                        "source_name": item["source_name"],
                        "published_at": item["published_at"],
                        "state": ai_data.get("state", clean_topic.title()),
                        "category": ai_data.get("category", "General"),
                        "importance_score": ai_data.get("importance_score", 3),
                        "is_breaking": ai_data.get("is_breaking", False),
                        "summary_hinglish": ai_data.get("summary_hinglish", item["title"]),
                        "full_content": item.get("initial_summary", ""),
                        "image_url": item.get("image_url"),
                        "dedup_hash": dedup.normalize_text(item["title"])
                    }
                    art_id = await db.insert_article(item_rec)
                    item_rec["id"] = art_id
                    relevant_articles.append(item_rec)
        except Exception as e:
            logger.debug(f"Dynamic topic news search error: {e}")

    # 5. Let AI respond with RAG context or general knowledge in conversational Hinglish
    reply = await ai.answer_general_query_with_rag(user_text, relevant_articles)
    try:
        await status_msg.edit_text(reply, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await status_msg.edit_text(reply)

# ========================================================
# Error Handler & Crash Notifications
# ========================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Uncaught exception: {context.error}", exc_info=context.error)
    for uid in ALLOWED_USER_IDS:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"⚠️ **Alert**: Bot encountered an error: `{str(context.error)[:300]}`"
            )
        except Exception:
            pass

# ========================================================
# Main Application Bootstrapper
# ========================================================
async def post_init(application: Application):
    """Initializes Database, APScheduler tasks, and sends startup notification."""
    await db.init_db()
    logger.info("Database initialized successfully.")

    # 1. News Polling (Every 2 minutes)
    scheduler.add_job(
        poll_news_job,
        trigger=IntervalTrigger(minutes=NEWS_POLL_INTERVAL_MINUTES),
        args=[application],
        id="news_poll",
        replace_existing=True
    )

    # 2. Market Alerts Checking (Every 5 minutes)
    scheduler.add_job(
        check_market_alerts_job,
        trigger=IntervalTrigger(minutes=MARKET_CHECK_INTERVAL_MINUTES),
        args=[application],
        id="market_alerts",
        replace_existing=True
    )

    # 3. Digest Queue Flush (Every 30 minutes)
    scheduler.add_job(
        flush_digest_job,
        trigger=IntervalTrigger(minutes=30),
        args=[application],
        id="digest_flush",
        replace_existing=True
    )

    # 4. Scheduled Market Reports (IST Timings)
    # 09:00 AM Pre-Market
    scheduler.add_job(
        send_scheduled_market_update,
        trigger=CronTrigger(hour=9, minute=0, timezone=pytz.timezone(TIMEZONE)),
        args=[application, "PRE_MARKET"],
        id="pre_market"
    )
    # 09:15 AM Market Open
    scheduler.add_job(
        send_scheduled_market_update,
        trigger=CronTrigger(hour=9, minute=15, timezone=pytz.timezone(TIMEZONE)),
        args=[application, "OPEN"],
        id="market_open"
    )
    # 03:30 PM Market Close
    scheduler.add_job(
        send_scheduled_market_update,
        trigger=CronTrigger(hour=15, minute=30, timezone=pytz.timezone(TIMEZONE)),
        args=[application, "CLOSE"],
        id="market_close"
    )
    # 08:00 PM Night Commodity & Wrap
    scheduler.add_job(
        send_scheduled_market_update,
        trigger=CronTrigger(hour=20, minute=0, timezone=pytz.timezone(TIMEZONE)),
        args=[application, "NIGHT_WRAP"],
        id="night_wrap"
    )

    # 5. Nightly DB Backup (02:00 AM IST)
    scheduler.add_job(
        backup_database_job,
        trigger=CronTrigger(hour=2, minute=0, timezone=pytz.timezone(TIMEZONE)),
        args=[application],
        id="daily_backup"
    )

    scheduler.start()
    logger.info("APScheduler started with all 24x7 monitoring routines.")

    # Boot Alert on Telegram
    for uid in ALLOWED_USER_IDS:
        try:
            await application.bot.send_message(
                chat_id=uid,
                text="🚀 **24x7 News & Market Intelligence Bot Started Successfully!**\nSystem is live and monitoring all Indian states, international feeds, and financial markets."
            )
        except Exception as e:
            logger.warning(f"Failed to send boot alert to {uid}: {e}")

def main():
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN is not set in .env! Exiting.")
        sys.exit(1)

    # Start healthcheck server immediately in background thread for Render/Koyeb
    port = int(os.getenv("PORT", "10000"))
    threading.Thread(target=run_health_server, args=(port,), daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).job_queue(None).post_init(post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("latest", cmd_latest))
    app.add_handler(CommandHandler("state", cmd_state))
    app.add_handler(CommandHandler("world", cmd_world))
    app.add_handler(CommandHandler("market", cmd_market))
    app.add_handler(CommandHandler("gold", cmd_gold))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("alert", cmd_alert))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("settings", cmd_settings))

    # Inline callbacks
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    # Freeform chat Q&A
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message_handler))

    # Global Error Handler
    app.add_error_handler(error_handler)

    logger.info("Bot polling initiated...")
    while True:
        try:
            app.run_polling(drop_pending_updates=True)
            break
        except Exception as e:
            logger.error(f"Polling loop encountered an error: {e}. Retrying in 5s...", exc_info=True)
            import time as py_time
            py_time.sleep(5)

if __name__ == "__main__":
    main()
