import asyncio
import logging
from typing import Dict, Any, List, Optional
import yfinance as yf
from config import MARKET_TICKERS

logger = logging.getLogger(__name__)

class MarketService:
    def __init__(self):
        pass

    async def get_ticker_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch real-time snapshot for a single symbol using yfinance asynchronously."""
        try:
            def _fetch():
                ticker = yf.Ticker(symbol)
                # fast_info is rapid and doesn't download whole balance sheet
                info = ticker.fast_info
                last_price = info.last_price
                prev_close = info.previous_close
                change = last_price - prev_close if prev_close else 0.0
                pct_change = (change / prev_close * 100) if prev_close else 0.0
                return {
                    "price": round(last_price, 2) if last_price else 0.0,
                    "prev_close": round(prev_close, 2) if prev_close else 0.0,
                    "change": round(change, 2),
                    "pct_change": round(pct_change, 2),
                    "currency": getattr(info, "currency", "INR")
                }
            return await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.warning(f"Error fetching ticker {symbol}: {e}")
            return None

    async def get_market_overview(self) -> Dict[str, Any]:
        """Fetches major domestic indices, commodities, and currency rates."""
        results = {}
        for key, sym in MARKET_TICKERS.items():
            quote = await self.get_ticker_quote(sym)
            if quote:
                results[key] = quote
        return results

    async def format_market_summary_hinglish(self, stage: str = "LIVE") -> str:
        """
        Builds a crisp, natural Hinglish market summary for:
        - 9:00 AM (Pre-Market)
        - 9:15 AM (Market Open)
        - 3:30 PM (Market Close)
        - 8:00 PM (Night Wrap)
        - /market command
        """
        overview = await self.get_market_overview()
        
        def fmt(sym_key: str, label: str) -> str:
            item = overview.get(sym_key)
            if not item:
                return f"• {label}: N/A"
            chg = item["change"]
            arrow = "🟢 ▲" if chg >= 0 else "🔴 ▼"
            sign = "+" if chg >= 0 else ""
            return f"• **{label}**: {item['price']:,} ({arrow} {sign}{chg} | {sign}{item['pct_change']}%)"

        header = "📊 **MARKET UPDATE (LIVE)**"
        verdict = "Market me movement normal chal rahi hai."

        if stage == "PRE_MARKET":
            header = "🌅 **MORNING PRE-MARKET BRIEF (09:00 AM)**"
            verdict = "Global cues check karein. Gift Nifty aur Asian markets ke hisaab se opening expected hai."
        elif stage == "OPEN":
            header = "🔔 **MARKET OPEN ALERT (09:15 AM)**"
            verdict = "Indian markets open ho chuke hain. Starting trends dekhein:"
        elif stage == "CLOSE":
            header = "🏁 **MARKET CLOSING REPORT (03:30 PM)**"
            verdict = "Aaj ka trading session conclude hua. Key closing levels:"
        elif stage == "NIGHT_WRAP":
            header = "🌙 **DAILY MARKET & COMMODITY WRAP (08:00 PM)**"
            verdict = "Evening commodity session aur day-end highlights:"

        nifty_txt = fmt("NIFTY_50", "Nifty 50")
        sensex_txt = fmt("SENSEX", "Sensex")
        banknifty_txt = fmt("BANK_NIFTY", "Bank Nifty")
        usdinr_txt = fmt("USD_INR", "USD-INR")
        
        gold_txt = fmt("GOLD", "Gold (Comex/USD)")
        silver_txt = fmt("SILVER", "Silver")
        crude_txt = fmt("CRUDE_OIL", "Crude Oil (WTI)")

        text = f"""{header}

🇮🇳 **Indian Indices:**
{nifty_txt}
{sensex_txt}
{banknifty_txt}

🛢️ **Commodities & Forex:**
{gold_txt}
{silver_txt}
{crude_txt}
{usdinr_txt}

💡 **Hinglish Take:**
{verdict}
"""
        return text.strip()

    async def get_gold_silver_rates_hinglish(self) -> str:
        """Detailed bullion rates report."""
        gold = await self.get_ticker_quote(MARKET_TICKERS["GOLD"])
        silver = await self.get_ticker_quote(MARKET_TICKERS["SILVER"])
        usdinr = await self.get_ticker_quote(MARKET_TICKERS["USD_INR"])

        usd_rate = usdinr["price"] if usdinr else 84.50

        # Rough domestic estimate: (Comex Price / 31.1035) * USDINR * 10 grams * import duty approx (~1.08)
        gold_10g_inr = "N/A"
        silver_1kg_inr = "N/A"

        if gold and gold["price"]:
            g_inr = (gold["price"] / 31.1035) * usd_rate * 10 * 1.09
            gold_10g_inr = f"₹{int(g_inr):,}"
        if silver and silver["price"]:
            s_inr = (silver["price"] / 31.1035) * usd_rate * 1000 * 1.09
            silver_1kg_inr = f"₹{int(s_inr):,}"

        msg = f"""🪙 **GOLD & SILVER LATEST RATES**

💛 **Gold:**
• International: ${gold['price'] if gold else 'N/A'}/oz ({gold['pct_change'] if gold else '0'}%)
• Domestic (Approx 24K 10g): {gold_10g_inr}

🤍 **Silver:**
• International: ${silver['price'] if silver else 'N/A'}/oz ({silver['pct_change'] if silver else '0'}%)
• Domestic (Approx 1kg): {silver_1kg_inr}

💵 **USD/INR Rate:** ₹{usd_rate}

📌 *Note: Local jeweller rates me local tax aur making charges alag hote hain.*"""
        return msg

    async def analyze_earnings_results(self, ticker_symbol: str, company_name: str, revenue_cr: float, profit_cr: float, yoy_growth_pct: float) -> str:
        """Generates simple Hinglish verdict on Quarterly Earnings."""
        verdict = "Achhe results hain! Profit aur revenue me solid growth dikhi hai." if yoy_growth_pct > 12 else \
                  "Mixed/Flat results. Growth expectations ke mutabiq average hai." if yoy_growth_pct > 0 else \
                  "Weak numbers. YoY basis par profit down hai, stock par pressure aa sakta hai."
        
        return f"""📑 **Q-RESULTS SUMMARY: {company_name} ({ticker_symbol})**

• **Revenue:** ₹{revenue_cr:,} Cr
• **Net Profit:** ₹{profit_cr:,} Cr
• **YoY Growth:** {yoy_growth_pct}%
• **Verdict (Hinglish):** {verdict}"""
