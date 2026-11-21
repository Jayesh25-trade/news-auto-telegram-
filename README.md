# 24x7 Personal News & Market Intelligence Telegram Bot 🚀

A production-ready, autonomous intelligence bot running 24x7 on any cloud server (VPS/Docker). It continuously monitors all 28 Indian States & 8 UTs (village/district level to national), high-impact global developments, and financial markets (Nifty, Sensex, Gold, Crude, Forex). Every update is delivered in **crisp Hinglish** directly to your private Telegram chat, backed by Claude AI for deep-dive investigative analysis and RAG search.

---

## 🌟 Key Features

1. **24x7 Multi-Source News Monitoring (Every 1-2 Minutes):**
   - Official: PIB (National & PM releases), DD News.
   - National Outlets: The Hindu, Indian Express, Hindustan Times, NDTV.
   - All 36 Indian States & UTs via targeted Google News RSS queries (politics, crime, weather, accidents, agriculture, courts, jobs).
   - Global Geopolitics: Reuters, BBC, Al Jazeera, AP (USA, China, Russia, Pakistan, Middle East, UN, wars).

2. **Deduplication & AI Scoring Engine:**
   - Jaccard token overlap + Levenshtein sequence matching to prevent duplicate stories.
   - Claude 3.5 Sonnet generates 2-3 line crisp **Hinglish** summaries.
   - Categorizes, extracts location, and assigns an importance score (1 to 5).
   - Instant **🔴 BREAKING NEWS** tags for score 5 stories.

3. **Inline Interactive Buttons & RAG Q&A:**
   - `[📖 Full News (AI)]`: Calls Claude to deliver a 5-point investigative breakdown (Kya hua, Kahan & Kab, Background, Next Steps, Market/Public impact).
   - `[🖼️ Image]`: Fetches high-resolution lead image extracted via Trafilatura.
   - `[🔗 Source]`: Direct link to original article.
   - **Ask anything in chat**: Send any question (e.g. *"Iska share market par kya asar hoga?"* or *"Bihar election update?"*) and the bot searches the SQLite FTS5 database to answer with Claude AI.

4. **Live Market & Commodity Intelligence:**
   - Nifty 50, Sensex, Bank Nifty, USD/INR.
   - Commodities: Gold (International & Domestic 24K estimate), Silver, Crude Oil, Natural Gas, Copper.
   - Scheduled Automated Broadcasts (IST):
     - `09:00 AM` — Morning Pre-market Brief
     - `09:15 AM` — Market Opening Alert
     - `03:30 PM` — Market Closing Report
     - `08:00 PM` — Night Commodity & Global Wrap
   - Price Alerts (`/alert ^NSEI ABOVE 25000`) & Custom Watchlists (`/watchlist add RELIANCE.NS`).

5. **Personalization & Ops:**
   - **Private Access Control**: Only your Telegram User ID can talk to or receive alerts.
   - **Quiet Hours**: During night hours (11 PM - 7 AM), non-critical news is queued into digest; only breaking news pings.
   - **Daily DB Backup**: Automated daily snapshot at 2:00 AM IST with Telegram confirmation.
   - **Healthcheck & Crash Alerts**: Pings you if an unhandled error occurs.

---

## 📁 Project Structure

```
news_market_bot/
├── config.py             # Feed URLs, State queries, Tickers, Settings
├── database.py           # Async SQLite with FTS5, Watchlist, Alerts & Prefs
├── deduplicator.py       # Sliding-window similarity & duplicate filter
├── news_scraper.py       # Async RSS fetcher & Trafilatura content/image extractor
├── ai_processor.py       # Claude API prompt engineering for Hinglish & RAG
├── market_service.py     # yfinance real-time rates, bullion & Q-results
├── bot.py                # Main Telegram Bot (PTB v20+) & APScheduler routines
├── Dockerfile            # Container build specification
├── docker-compose.yml    # Docker Compose for 24x7 VPS deployment
├── requirements.txt      # Python dependencies
├── .env.example          # Template for credentials
└── README.md             # Deployment & usage instructions
```

---

## 🚀 Step-by-Step 24x7 Cloud Server Deployment

You can deploy this on any cheap or free Cloud VPS (Oracle Cloud Always Free Tier, Hetzner €3/mo, DigitalOcean $4/mo, or AWS EC2 t3.micro).

### Step 1: Create Your Telegram Bot & Get IDs
1. Open Telegram, message **@BotFather**, send `/newbot`, and follow prompts to get your `TELEGRAM_BOT_TOKEN`.
2. Message **@userinfobot** on Telegram to get your numeric `Id` (e.g. `123456789`). This is your `ALLOWED_USER_IDS`.
3. Get your Anthropic Claude API Key from [console.anthropic.com](https://console.anthropic.com/).

### Step 2: Connect to your Cloud VPS
Connect to your remote server via SSH:
```bash
ssh ubuntu@your-server-ip
```

### Step 3: Install Docker & Docker Compose
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose git
sudo systemctl enable --now docker
```

### Step 4: Clone / Upload the Code
```bash
mkdir -p /opt/news_market_bot
cd /opt/news_market_bot
# Upload or copy project files into this directory
```

### Step 5: Configure `.env`
Create `.env` from the template:
```bash
cp .env.example .env
nano .env
```
Fill in your details:
```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USER_IDS=your_telegram_user_id
ANTHROPIC_API_KEY=your_claude_api_key
CLAUDE_MODEL=claude-3-5-sonnet-20241022
TIMEZONE=Asia/Kolkata
```
Press `Ctrl + O` then `Enter` to save, and `Ctrl + X` to exit `nano`.

### Step 6: Start 24x7 in Background with Docker Compose
```bash
docker-compose up -d --build
```
Check running logs:
```bash
docker-compose logs -f
```

The bot will immediately send you a Telegram message:
> 🚀 **24x7 News & Market Intelligence Bot Started Successfully!**

Now even if your PC is turned off, the bot will run uninterrupted in the cloud 24 hours a day, 7 days a week.

---

### Alternative: Run directly with Systemd (Without Docker)

If you prefer running directly in Python on Linux:

1. Install Python 3.10+ and pip:
   ```bash
   sudo apt install -y python3-pip python3-venv
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. Create a systemd service file:
   ```bash
   sudo nano /etc/systemd/system/newsbot.service
   ```
   Add:
   ```ini
   [Unit]
   Description=24x7 News and Market Telegram Bot
   After=network.target

   [Service]
   Type=simple
   User=ubuntu
   WorkingDirectory=/opt/news_market_bot
   ExecStart=/opt/news_market_bot/venv/bin/python bot.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```
3. Enable and start:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable newsbot
   sudo systemctl start newsbot
   sudo systemctl status newsbot
   ```

---

## 📱 Telegram Commands Reference

| Command | Description | Example |
|---|---|---|
| `/start` | Bot welcome menu & operational guide | `/start` |
| `/latest` | 5 latest news stories from database | `/latest` |
| `/state <name>` | News filtered by state/UT | `/state Maharashtra` |
| `/world` | Top international news items | `/world` |
| `/market` | Live Nifty, Sensex, Gold, Crude & USD/INR | `/market` |
| `/gold` | Bullion rates (International & Domestic estimate) | `/gold` |
| `/search <topic>` | Full-Text search over past news | `/search inflation` |
| `/alert <sym> <cond> <p>` | Set price alerts | `/alert ^NSEI ABOVE 25000` |
| `/watchlist` | Check tracked stocks and quotes | `/watchlist` |
| `/watchlist add <sym>` | Add stock to watchlist | `/watchlist add TATAMOTORS.NS` |
| `/digest` | Flush queued low-priority stories | `/digest` |
| `/settings` | View quiet hours and preferences | `/settings` |
