import json
import logging
import asyncio
from typing import Dict, Any, Optional, List
import httpx

try:
    import anthropic
except ImportError:
    anthropic = None

from config import GEMINI_API_KEY, GEMINI_MODEL, ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)

class AIProcessor:
    def __init__(self):
        self.gemini_key = GEMINI_API_KEY
        self.gemini_model = GEMINI_MODEL
        self.claude_client = (
            anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
            if (anthropic and ANTHROPIC_API_KEY)
            else None
        )
        self.http_client = httpx.AsyncClient(timeout=30.0)

    async def _call_llm(self, prompt: str) -> str:
        """Calls Gemini API (or Anthropic as fallback) asynchronously."""
        # 1. Try Gemini first if key available
        if self.gemini_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.25, "maxOutputTokens": 1500}
            }
            try:
                resp = await self.http_client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        return candidates[0]["content"]["parts"][0]["text"].strip()
                else:
                    logger.warning(f"Gemini API returned status {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.warning(f"Gemini API call failed: {e}")

        # 2. Try Anthropic Claude if available
        if self.claude_client:
            try:
                resp = await self.claude_client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=1500,
                    temperature=0.25,
                    messages=[{"role": "user", "content": prompt}]
                )
                return resp.content[0].text.strip()
            except Exception as e:
                logger.warning(f"Claude API call failed: {e}")

        return ""

    async def analyze_and_summarize(self, title: str, raw_content: str, default_region: str = "National") -> Dict[str, Any]:
        """Summarizes news into 2-3 lines of simple natural Hinglish and scores importance 1-5."""
        prompt = f"""
You are an expert news editor who writes crisp, engaging 2-3 line news summaries in natural HINGLISH (Hindi written in Roman English script, mixed with English terms as spoken in India, e.g. "Govt ne naya rule announce kiya hai jisse students ko benefit milega.").

News Item:
Title: {title}
Article Extract: {raw_content[:2500]}
Default Region/Hint: {default_region}

Return ONLY a valid JSON object matching this schema with no markdown code fences:
{{
  "summary_hinglish": "Exact 2-3 short lines in simple conversational Hinglish explaining the core news.",
  "category": "One of: Politics, Crime, Weather, Accident, Jobs, Education, Sports, Health, Agriculture, Court, Market, International, General",
  "state": "Name of Indian State/UT (e.g. Uttar Pradesh, Maharashtra, Bihar, Delhi) OR 'National' OR the International Country (e.g. USA, Russia, Israel)",
  "importance_score": 1 to 5 (5=Massive breaking/critical, 4=Very important, 3=Normal news, 2=Minor local, 1=Trivia),
  "is_breaking": true if importance_score is 5 or extreme urgent alert else false
}}
"""
        response_text = await self._call_llm(prompt)
        if response_text:
            try:
                clean = response_text
                if clean.startswith("```"):
                    clean = clean.strip("`").replace("json\n", "", 1).strip()
                return json.loads(clean)
            except Exception as e:
                logger.debug(f"JSON parse fallback: {e}")

        return {
            "summary_hinglish": f"{title}. Details check karne ke liye Full News button dabayein.",
            "category": "General",
            "state": default_region,
            "importance_score": 3,
            "is_breaking": False
        }

    async def rag_deep_dive(self, article: Dict[str, Any], user_question: Optional[str] = None) -> str:
        """Deep-dive investigative breakdown in structured Hinglish."""
        headline = article.get("title", "")
        summary = article.get("summary_hinglish", "")
        full_text = article.get("full_content", "") or summary
        category = article.get("category", "")
        state = article.get("state", "")

        question_context = f"\nUser ne yeh specific sawal poocha hai: '{user_question}'" if user_question else "\nUser ne 'Full News / Deep-Dive' report maangi hai."

        prompt = f"""
You are a senior investigative journalist and market analyst explaining a news story to an Indian user in clear, informative HINGLISH.

Article Context:
- Headline: {headline}
- Region: {state}
- Category: {category}
- Summary: {summary}
- Full Content: {full_text[:4000]}
{question_context}

Format your output in clean Telegram Markdown with these structured sections:
📌 **Kya Hua (What Happened):**
(2-3 detailed sentences in Hinglish)

📍 **Kahan aur Kab (Where & When):**
(Location and timeline details)

🔍 **Background & Vajah (Why & Context):**
(Piche ka background aur karan)

🔮 **Aage Kya Ho Sakta Hai (What Next):**
(Future implications / next steps)

📈 **Market & Aam Janta Par Asar (Impact Analysis):**
(Iska share market, business, economy ya aam public par kya asar padega)

Keep tone professional, crisp, and 100% natural conversational Hinglish.
"""
        reply = await self._call_llm(prompt)
        return reply if reply else "Deep-dive report generate nahi ho paayi. Kripya thodi der baad try karein."

    async def answer_general_query_with_rag(self, query: str, context_articles: List[Dict[str, Any]]) -> str:
        """Answers user queries in Hinglish using the local news database as ground truth."""
        context_str = ""
        for i, art in enumerate(context_articles, 1):
            context_str += f"\n[Document {i}]:\nTitle: {art.get('title')}\nState: {art.get('state')}\nSummary: {art.get('summary_hinglish')}\nDetails: {art.get('full_content', '')[:1000]}\n"

        prompt = f"""
You are an intelligent 24x7 personal news & market intelligence AI.
Answer the user's question using the retrieved news reports below.
Reply in simple, natural conversational HINGLISH with facts and clarity.

Retrieved News Context:
{context_str}

User Question: {query}
"""
        reply = await self._call_llm(prompt)
        return reply if reply else "Aapke sawal ka jawab abhi database me nahi mila. Dusra sawal poochhein."
