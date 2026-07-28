"""
quick_signal.py — cheap, deterministic ADD/HOLD/TRIM verdict for the
main holdings table, WITHOUT calling DeepSeek.

Full get_analysis() in deepseek_client.py does 3 things: fetch
fundamentals (Screener.in), fetch news (Google News), then ask
DeepSeek to narrate the computed verdict. This module does only the
first two -- fetch + compute_signal() -- skipping the LLM call
entirely. That makes it:
  - Free to run (no DeepSeek API cost) for a full portfolio refresh
  - Still real (same red-flag/news-sentiment computation, same
    signal_engine.compute_signal() logic, same auditability -- nothing
    here is a shortcut or approximation of the real signal)

Still rate-limited by fundamentals.py's polite 2s Screener.in delay,
so refreshing N holdings takes roughly N*2 seconds minimum -- meant
to be triggered manually (a "Refresh Signals" button), not on every
page render.
"""

from fundamentals import fetch_fundamentals
from red_flags import evaluate
from news_fetcher import fetch_news
from signal_engine import compute_signal, Signal


def compute_quick_signal(symbol: str, company_name: str, position: dict = None) -> Signal:
    """
    Same inputs/logic as deepseek_client.get_analysis(), minus the
    DeepSeek call. Returns a Signal object (see signal_engine.py) --
    verdict, fundamental_score, news_sentiment, data_completeness.
    """
    fundamentals = fetch_fundamentals(symbol) or {}
    flags = evaluate(fundamentals) if len(fundamentals) > 3 else []
    news = fetch_news(company_name, symbol) or []
    return compute_signal(flags, fundamentals, news, position or {})
