"""
price_fetcher.py — free live NSE price fetch via yfinance.

Same principle as nse_momentum's data pipeline: no paid API, no broker
login needed for quotes. yfinance pulls from Yahoo Finance, which mirrors
NSE/BSE prices with the standard '.NS' suffix.

Batches all tickers in ONE call (yf.Tickers / download) rather than one
call per stock — much faster and less likely to get rate-limited.
Caches results for CACHE_TTL_SECONDS so repeated Streamlit reruns
(which happen on every UI interaction) don't refetch on every click.
"""

import time
import logging
import yfinance as yf

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60
_cache = {"timestamp": 0, "prices": {}}


def get_live_prices(tickers: list, force_refresh: bool = False) -> dict:
    """
    Returns {ticker: last_price} for every ticker it could fetch.
    Tickers it couldn't fetch are simply omitted from the result —
    caller should handle missing keys (e.g. show 'N/A' in the UI).
    """
    now = time.time()
    if not force_refresh and (now - _cache["timestamp"]) < CACHE_TTL_SECONDS:
        cached = _cache["prices"]
        if all(t in cached for t in tickers):
            return {t: cached[t] for t in tickers}

    unique_tickers = sorted(set(tickers))
    prices = {}

    try:
        data = yf.download(
            tickers=unique_tickers,
            period="1d",
            interval="1m",
            group_by="ticker",
            progress=False,
            threads=True,
        )
        for t in unique_tickers:
            try:
                if len(unique_tickers) == 1:
                    series = data["Close"].dropna()
                else:
                    series = data[t]["Close"].dropna()
                if len(series) > 0:
                    prices[t] = float(series.iloc[-1])
            except Exception as e:
                log.warning(f"No intraday data for {t}: {e}")
    except Exception as e:
        log.error(f"Batch download failed: {e}")

    # Fallback for anything still missing: try .info / fast_info per-ticker
    missing = [t for t in unique_tickers if t not in prices]
    for t in missing:
        try:
            fast = yf.Ticker(t).fast_info
            price = fast.get("lastPrice") or fast.get("last_price")
            if price:
                prices[t] = float(price)
        except Exception as e:
            log.warning(f"Fallback fetch failed for {t}: {e}")

    _cache["timestamp"] = now
    _cache["prices"].update(prices)

    return {t: prices.get(t) for t in tickers}


if __name__ == "__main__":
    test_tickers = ["RELIANCE.NS", "TCS.NS", "SBIFUNDS.NS"]
    print(get_live_prices(test_tickers))
