"""
fundamentals.py — free fundamentals fetch from Screener.in

Screener.in publishes company financials on a public page per ticker,
no login/API key needed. This mirrors the "Verification Membrane" /
"deterministic engine" idea from the NEXUS architecture review: real
computed numbers, not the LLM inventing figures from training memory.

Polite scraping: single request per Analyze click (low volume, personal
use), real browser User-Agent, no concurrent hammering. Screener.in's
own site doesn't block this kind of light personal-use traffic, but
this is not a bulk-scraping tool — don't loop this over hundreds of
tickers on a schedule.
"""

import re
import time
import logging
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

_last_request_time = 0
MIN_INTERVAL_SECONDS = 2  # polite rate limit, matches NEXUS design note


def _rate_limit():
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < MIN_INTERVAL_SECONDS:
        time.sleep(MIN_INTERVAL_SECONDS - elapsed)
    _last_request_time = time.time()


def _parse_ratio_number(text: str):
    """Screener shows numbers like '1,234.56' or '12.3 %' or '-45.2' — strip to float."""
    if not text:
        return None
    cleaned = text.replace(",", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_fundamentals(nse_symbol: str) -> dict | None:
    """
    nse_symbol like 'RELIANCE.NS' — strips the .NS/.BO suffix for the
    Screener.in URL (Screener uses bare NSE symbols).

    Returns a dict of whatever fields it could find. Missing fields are
    simply absent from the dict — caller (red_flags.py, deepseek_client.py)
    must handle partial data gracefully, never assume all keys exist.
    Returns None only on total fetch failure (network error, 404, etc).
    """
    bare_symbol = nse_symbol.replace(".NS", "").replace(".BO", "")
    url = f"https://www.screener.in/company/{bare_symbol}/"

    _rate_limit()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            log.warning(f"Screener.in returned {resp.status_code} for {bare_symbol}")
            return None
    except requests.RequestException as e:
        log.error(f"Screener.in fetch failed for {bare_symbol}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    data = {"symbol": nse_symbol, "source": "screener.in", "source_url": url}

    # Top ratios box: P/E, market cap, ROE, D/E, etc. — Screener renders these
    # as <li> items with a name span and a value span under #top-ratios
    ratios_box = soup.select_one("#top-ratios")
    if ratios_box:
        for li in ratios_box.select("li"):
            name_el = li.select_one(".name")
            value_el = li.select_one(".number") or li.select_one(".value")
            if not name_el or not value_el:
                continue
            name = name_el.get_text(strip=True).lower()
            value = _parse_ratio_number(value_el.get_text(strip=True))
            if value is None:
                continue
            if "market cap" in name:
                data["market_cap_cr"] = value
            elif name == "stock p/e" or "p/e" in name:
                data["pe_ratio"] = value
            elif "roe" in name:
                data["roe_pct"] = value
            elif "debt to equity" in name or name == "debt / eq":
                data["debt_to_equity"] = value
            elif "face value" in name:
                data["face_value"] = value
            elif "book value" in name:
                data["book_value"] = value
            elif "dividend yield" in name:
                data["dividend_yield_pct"] = value

    # Shareholding pattern table — promoter holding % and pledge %, most recent quarter
    shp_section = soup.select_one("#shareholding")
    if shp_section:
        rows = shp_section.select("table tr")
        for row in rows:
            cells = row.select("td, th")
            if not cells:
                continue
            label = cells[0].get_text(strip=True).lower()
            values = [_parse_ratio_number(c.get_text(strip=True)) for c in cells[1:]]
            values = [v for v in values if v is not None]
            if not values:
                continue
            latest = values[-1]  # rightmost column = most recent quarter
            if "promoter" in label and "pledge" not in label:
                data["promoter_holding_pct"] = latest
            elif "pledge" in label:
                data["promoter_pledge_pct"] = latest
            elif label.startswith("fii"):
                data["fii_holding_pct"] = latest
            elif label.startswith("dii"):
                data["dii_holding_pct"] = latest

    # Profit & Loss table — most recent OCF/PAT/Sales growth if visible on the page
    pl_section = soup.select_one("#profit-loss")
    if pl_section:
        rows = pl_section.select("table tr")
        for row in rows:
            cells = row.select("td, th")
            if not cells:
                continue
            label = cells[0].get_text(strip=True).lower()
            values = [_parse_ratio_number(c.get_text(strip=True)) for c in cells[1:]]
            values = [v for v in values if v is not None]
            if len(values) >= 2:
                if label == "sales" or label == "revenue":
                    data["sales_latest_cr"] = values[-1]
                    data["sales_prev_cr"] = values[-2]
                elif "net profit" in label:
                    data["net_profit_latest_cr"] = values[-1]
                    data["net_profit_prev_cr"] = values[-2]

    if len(data) <= 3:  # only symbol/source/url — nothing actually parsed
        log.warning(f"Parsed 0 fundamental fields for {bare_symbol} — page structure may have changed")

    return data


if __name__ == "__main__":
    import json
    result = fetch_fundamentals("RELIANCE.NS")
    print(json.dumps(result, indent=2))
