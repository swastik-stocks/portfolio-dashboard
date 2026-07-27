"""
deepseek_client.py — per-stock commentary, grounded in real data + a
computed decision signal.

v3 change: previously DeepSeek was asked to give a SWOT-style note but
explicitly told NOT to recommend BUY/SELL/TRIM/ADD -- that hedging is
part of why the output felt bland. Now the ACTUAL verdict (ADD/HOLD/
TRIM/STRONG TRIM/REVIEW) is computed deterministically in
signal_engine.py from real fundamentals + red flags + news sentiment +
your position. DeepSeek's job is to explain WHY, in plain English,
referencing the specific computed factors -- it does not invent or
override the verdict itself. This mirrors the "LLM never emits a
number" principle used throughout this whole system (pattern_agent.py,
red_flags.py) while still giving you the decisive read you're after.
"""

import os
import logging
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from fundamentals import fetch_fundamentals
from red_flags import evaluate
from news_fetcher import fetch_news
from signal_engine import compute_signal

log = logging.getLogger(__name__)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"

SYSTEM_PROMPT = """You are an equity research assistant for an Indian retail investor
who ALREADY HOLDS the position being discussed.

A VERDICT has already been computed deterministically (ADD / HOLD / TRIM /
STRONG TRIM / REVIEW) from real fundamentals, red flags, recent analyst-
tagged news, and the investor's own P&L. Your job is ONLY to explain this
verdict clearly and specifically -- you do NOT choose or override the
verdict, and you do NOT invent a different one.

RULES:
1. NEVER invent a number. Every ratio, percentage, or figure you cite must
   come from the FUNDAMENTALS / RED FLAGS / NEWS / POSITION sections given
   to you. If something isn't there, say "data not available."
2. Open with the verdict and the single strongest reason for it in one
   sentence. Then give 2-3 supporting bullets citing the specific computed
   factors (red flags, news sentiment, position P&L) that drove it.
3. If data_completeness is PARTIAL or INSUFFICIENT, say so plainly and
   explain the verdict is lower-confidence as a result -- don't paper over
   thin data with generic confidence.
4. This is decision SUPPORT, not a guarantee -- one sentence at the end
   noting the investor should weigh this alongside their own judgment,
   not a long disclaimer paragraph.
5. Keep it tight -- under 150 words total. Concrete beats comprehensive."""


def is_configured() -> bool:
    return bool(DEEPSEEK_API_KEY)


def _build_user_prompt(symbol: str, company_name: str, position):
    fundamentals = fetch_fundamentals(symbol) or {}
    flags = evaluate(fundamentals) if len(fundamentals) > 3 else []
    news = fetch_news(company_name, symbol) or []
    signal = compute_signal(flags, fundamentals, news, position or {})

    fund_lines = [f"  {k}: {v}" for k, v in fundamentals.items() if k not in ("symbol", "source", "source_url")]
    fund_block = "\n".join(fund_lines) if fund_lines else "  (no fundamentals could be parsed)"

    flag_block = "\n".join(f"  [{f.severity}] {f.rule}: {f.detail}" for f in flags) if flags else "  (not computed)"

    news_block = "\n".join(f"  [{n['sentiment']:+d}]{' [ANALYST]' if n['is_analyst_mention'] else ''} {n['title']} ({n['source']})" for n in news) if news else "  (no recent news found)"

    position_block = "  (no position data provided)"
    if position:
        qty, avg_price, ltp = position.get("qty"), position.get("avg_price"), position.get("ltp")
        pnl, pnl_pct = position.get("pnl"), position.get("pnl_pct")
        position_block = (
            f"  Quantity: {qty} | Purchase price: Rs.{avg_price:.2f}"
            + (f" | Current: Rs.{ltp:.2f}" if ltp is not None else " | Current: not available")
            + (f" | P&L: Rs.{pnl:.2f} ({pnl_pct:+.2f}%)" if pnl is not None else "")
        )

    prompt = f"""Stock: {company_name} ({symbol})

COMPUTED VERDICT: {signal.verdict}  (data completeness: {signal.data_completeness})
Fundamental score: {signal.fundamental_score:.2f}
News sentiment (analyst-tagged only): {signal.news_sentiment:+d}
Position note: {signal.position_note}

FUNDAMENTALS (source: Screener.in):
{fund_block}

RED FLAGS (deterministic, already evaluated):
{flag_block}

RECENT NEWS (Google News, [+/-] = keyword sentiment, [ANALYST] = mentions a broker or rating language):
{news_block}

YOUR POSITION:
{position_block}

Explain this verdict per the system rules."""

    debug_data = {"fundamentals": fundamentals, "flags": flags, "news": news, "signal": signal}
    return prompt, debug_data


def get_analysis(symbol: str, company_name: str, position: dict = None):
    """
    Returns {"text": str, "signal": Signal, "fundamentals": dict,
    "flags": list, "news": list} or None if not configured / call failed.
    """
    if not DEEPSEEK_API_KEY:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        log.error("openai package not installed -- run: pip install openai")
        return None

    user_prompt, debug_data = _build_user_prompt(symbol, company_name, position)

    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=400,
            temperature=0.2,
        )
        text = response.choices[0].message.content
    except Exception as e:
        log.error(f"DeepSeek analysis failed for {symbol}: {e}")
        return None

    return {
        "text": text,
        "signal": debug_data["signal"],
        "fundamentals": debug_data["fundamentals"],
        "flags": debug_data["flags"],
        "news": debug_data["news"],
    }


if __name__ == "__main__":
    if is_configured():
        result = get_analysis(
            "RELIANCE.NS", "Reliance Industries Ltd",
            position={"qty": 2, "avg_price": 1307.86, "ltp": 1450.0, "pnl": 284.28, "pnl_pct": 10.86},
        )
        if result:
            print("VERDICT:", result["signal"].verdict)
            print(result["text"])
        else:
            print("No result")
    else:
        print("DEEPSEEK_API_KEY not set in .env -- skipping test call.")
