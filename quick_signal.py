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

import logging

from fundamentals import fetch_fundamentals
from red_flags import evaluate
from news_fetcher import fetch_news
from signal_engine import compute_signal, Signal

log = logging.getLogger(__name__)


def compute_quick_signal(symbol: str, company_name: str, position: dict = None) -> tuple:
    """
    Same inputs/logic as deepseek_client.get_analysis(), minus the
    DeepSeek call.

    Returns (Signal, reason_code) — reason_code is one of:
      OK        — fundamentals fetched normally, real verdict computed
      NO_DATA   — fetch succeeded but returned nothing usable (<=3 fields;
                  same threshold red_flags.evaluate() already gates on)
      FETCH_ERROR — fetch_fundamentals/fetch_news raised

    P1-08: previously any exception here propagated straight out of this
    function, which meant app.py's Refresh Signals loop (no try/except of
    its own) would crash on the FIRST bad symbol and leave every symbol
    after it in iteration order stuck showing "(not run)" — not just the
    one that actually failed. Catching here means one bad symbol degrades
    to a labeled FETCH_ERROR row instead of silently blocking the whole
    portfolio's refresh.
    """
    try:
        fundamentals = fetch_fundamentals(symbol) or {}
    except Exception as e:
        log.warning(f"fetch_fundamentals failed for {symbol}: {e}")
        fundamentals = {}
        fetch_failed = True
    else:
        fetch_failed = False

    flags = evaluate(fundamentals) if len(fundamentals) > 3 else []

    try:
        news = fetch_news(company_name, symbol) or []
    except Exception as e:
        log.warning(f"fetch_news failed for {symbol}: {e}")
        news = []
        fetch_failed = True

    signal = compute_signal(flags, fundamentals, news, position or {})

    if fetch_failed:
        reason_code = "FETCH_ERROR"
    elif len(fundamentals) <= 3:
        reason_code = "NO_DATA"
    else:
        reason_code = "OK"

    return signal, reason_code
