import json
import logging
import re
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
        self.gemini_models = [
            GEMINI_MODEL,
            "gemini-flash-lite-latest",
            "gemini-3.1-flash-lite",
            "gemini-3.8-flash"
        ]
        # Remove duplicates while preserving order
        seen = set()
        self.gemini_models = [m for m in self.gemini_models if not (m in seen or seen.add(m))]

        self.claude_client = (
            anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
            if (anthropic and ANTHROPIC_API_KEY)
            else None
        )
        self.http_client = httpx.AsyncClient(timeout=25.0)

    async def _call_llm(self, prompt: str) -> str:
        """Calls Gemini API with model fallback chain (and Anthropic fallback if configured)."""
        # 1. Try Gemini models in order
        if self.gemini_key:
            for model_name in self.gemini_models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self.gemini_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.3,
                        "maxOutputTokens": 1500
                    }
                }
                try:
                    resp = await self.http_client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts:
                                return parts[0].get("text", "").strip()
                    elif resp.status_code == 429:
                        logger.warning(f"Gemini model {model_name} quota exceeded (429), trying next model...")
                        continue
                    else:
                        logger.warning(f"Gemini model {model_name} returned status {resp.status_code}: {resp.text[:150]}")
                except Exception as e:
                    logger.warning(f"Gemini model {model_name} call failed: {e}")

        # 2. Try Anthropic Claude if available
        if self.claude_client:
            try:
                resp = await self.claude_client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=1500,
                    temperature=0.3,
                    messages=[{"role": "user", "content": prompt}]
                )
                return resp.content[0].text.strip()
            except Exception as e:
                logger.warning(f"Claude API call failed: {e}")

        return ""

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        """Safely parses JSON from LLM output, extracting from markdown codeblocks or raw braces."""
        if not text:
            return None
        text = text.strip()
        # Remove markdown codeblock wrapper
        if "```" in text:
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
            if match:
                text = match.group(1).strip()
        # Find first '{' and last '}'
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1])
            except Exception as e:
                logger.debug(f"JSON regex extraction failed: {e}")
        return None

    async def analyze_and_summarize(self, title: str, raw_content: str, default_region: str = "National") -> Dict[str, Any]:
        """Summarizes news into 2-3 lines of simple natural Hinglish and scores importance 1-5."""
        prompt = f"""
You are an expert Indian news editor. Write a crisp, engaging 2-3 line news summary in natural conversational HINGLISH (Hindi written in English alphabet, mixed with English words as spoken in India, e.g. "Govt ne naya decision liya hai jisse public ko benefit milega.").

Rules:
- Language MUST be natural, friendly Hinglish.
- 2-3 lines maximum.
- Accurate and informative.

News:
Title: {title}
Content: {raw_content[:2500]}
Default Region: {default_region}

Return ONLY a valid JSON object matching this schema with no extra text:
{{
  "summary_hinglish": "2-3 short lines in simple conversational Hinglish explaining the core news.",
  "category": "One of: Politics, Crime, Weather, Accident, Jobs, Education, Sports, Health, Agriculture, Court, Market, International, General",
  "state": "{default_region}",
  "importance_score": 3,
  "is_breaking": false
}}
"""
        response_text = await self._call_llm(prompt)
        parsed = self._extract_json(response_text)
        if parsed and "summary_hinglish" in parsed:
            return {
                "summary_hinglish": parsed.get("summary_hinglish", f"📌 {title}"),
                "category": parsed.get("category", "General"),
                "state": parsed.get("state", default_region),
                "importance_score": int(parsed.get("importance_score", 3)),
                "is_breaking": bool(parsed.get("is_breaking", False))
            }

        # Local intelligent Hinglish fallback if LLM is unavailable
        content_snippet = raw_content.split(".")[0].strip() if raw_content else ""
        if len(content_snippet) > 20 and content_snippet.lower() != title.lower():
            fallback_summary = f"📌 **Mukhya Baatein:** {title}.\nℹ️ {content_snippet}."
        else:
            fallback_summary = f"📌 **Update:** {title}.\n👉 Is mamle par nayi developments aayi hain. Pura vivaran padhne ke liye Full News par click karein."

        return {
            "summary_hinglish": fallback_summary,
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

        question_context = f"\nUser ne yeh specific sawal poocha hai: '{user_question}'" if user_question else ""

        prompt = f"""
You are a senior investigative journalist and market analyst explaining a news story to an Indian user in clear, informative HINGLISH.

Article Context:
- Headline: {headline}
- Region: {state}
- Category: {category}
- Summary: {summary}
- Details: {full_text[:3500]}
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
        if reply:
            return reply

        # Fallback structured deep-dive if LLM is temporarily unreachable
        return f"""📌 **Kya Hua (What Happened):**
{headline}

📍 **Kahan aur Kab (Where & When):**
Region: {state} | Category: {category}

🔍 **Background & Vajah (Why & Context):**
{summary}

🔮 **Aage Kya Ho Sakta Hai (What Next):**
Is mamle par official updates aur authorities ka agla step aane wala hai.

📈 **Market & Aam Janta Par Asar:**
Local level aur sambandhit sector par iska direct asar dekhne ko mil sakta hai."""

    async def answer_general_query_with_rag(self, query: str, context_articles: List[Dict[str, Any]]) -> str:
        """Answers user queries in Hinglish using the local news database as ground truth or broad AI knowledge."""
        context_str = ""
        if context_articles:
            for i, art in enumerate(context_articles, 1):
                context_str += f"\n[Document {i}]:\nTitle: {art.get('title')}\nState: {art.get('state')}\nSummary: {art.get('summary_hinglish')}\nDetails: {art.get('full_content', '')[:800]}\n"

        prompt = f"""
You are an intelligent, friendly 24x7 personal news & market intelligence AI assistant.
The user is asking: "{query}"

Retrieved Context (if any):
{context_str if context_str else "No direct database match found. Use your general knowledge to answer accurately."}

Instructions:
- Reply in 100% simple, natural, conversational HINGLISH (Hindi written in Roman English script).
- Be helpful, polite, and accurate.
- If asking for news or updates, provide the most relevant details clearly.
- If asking a greeting or general question, answer warmly and let them know they can ask about latest news, state news, or markets.
"""
        reply = await self._call_llm(prompt)
        if reply:
            return reply

        # Intelligent offline fallback
        if context_articles:
            top_arts = "\n".join([f"• **{a.get('title')}**\n  _{a.get('summary_hinglish')}_" for a in context_articles[:3]])
            return f"Aapke sawal se related yeh taaza updates mili hain:\n\n{top_arts}"

        return "Namaste! Main aapka 24x7 News & Market assistant hoon. Aap mujhse kisi bhi state, district, latest news ya stock market updates ke baare me pooch sakte hain. Jaise: `/latest`, `/market`, ya direct 'Maharashtra news', 'Gold rate'."
