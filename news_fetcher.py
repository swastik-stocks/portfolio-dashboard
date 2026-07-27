"""
news_fetcher.py — free recent-news fetch via Google News RSS.

No API key, no login. Google News publishes a public RSS search feed:
    https://news.google.com/rss/search?q=...&hl=en-IN&gl=IN&ceid=IN:en

This is NOT a substitute for a real analyst-target-price data feed
(Bloomberg/Refinitiv/Trendlyne Pro) — those are paywalled and there's
no free equivalent. What this DOES give: real, current headlines,
tagged for whether they mention a major broking house or rating/target
language, so DeepSeek has actual recent text to synthesize instead of
inventing "Jefferies has a target of X" out of thin air.

Broker/rating keyword tagging is deterministic (plain keyword match),
not an LLM judgment call — same philosophy as red_flags.py.
"""

import logging
import xml.etree.ElementTree as ET
from urllib.parse import quote
import requests

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

BROKER_NAMES = [
    "jefferies", "motilal oswal", "morgan stanley", "nomura", "clsa",
    "kotak institutional", "kotak securities", "icici securities", "hsbc",
    "citi", "goldman sachs", "macquarie", "jm financial", "antique",
    "emkay", "nuvama", "prabhudas lilladher", "ubs", "jpmorgan", "jp morgan",
    "bofa", "bank of america", "axis capital", "iifl", "systematix", "elara",
]

RATING_KEYWORDS = [
    "target price", "upgrade", "downgrade", "outperform", "underperform",
    "overweight", "underweight", "maintain buy", "maintain sell", "maintain hold",
    "initiates coverage", "price target", "rating",
]

POSITIVE_WORDS = [
    "upgrade", "buy", "outperform", "overweight", "beat", "beats", "record",
    "strong", "robust", "surge", "rally", "expansion", "growth", "raises target",
]
NEGATIVE_WORDS = [
    "downgrade", "sell", "underperform", "underweight", "miss", "misses",
    "concern", "concerns", "probe", "fraud", "decline", "cut target",
    "slump", "plunge", "weak", "warning",
]


def fetch_news(company_name: str, symbol: str, max_items: int = 8) -> list:
    """
    Returns a list of dicts: {title, source, link, is_analyst_mention, sentiment}
    sentiment is a crude deterministic +1/0/-1 keyword score per headline,
    NOT an LLM judgment — kept simple and auditable on purpose.

    Returns [] on any fetch/parse failure — caller must handle gracefully,
    same pattern as fundamentals.py.
    """
    bare_symbol = symbol.replace(".NS", "").replace(".BO", "")
    query = quote(f"{company_name} stock")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            log.warning(f"Google News RSS returned {resp.status_code} for {company_name}")
            return []
    except requests.RequestException as e:
        log.error(f"Google News RSS fetch failed for {company_name}: {e}")
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        log.error(f"Failed to parse Google News RSS XML: {e}")
        return []

    items = []
    for item in root.findall(".//item")[:max_items]:
        title_el = item.find("title")
        link_el = item.find("link")
        pubdate_el = item.find("pubDate")
        if title_el is None or title_el.text is None:
            continue

        full_title = title_el.text
        # Google News titles are usually "Headline - Source Name"
        if " - " in full_title:
            headline, source = full_title.rsplit(" - ", 1)
        else:
            headline, source = full_title, "Unknown"

        lower = full_title.lower()
        is_analyst_mention = any(b in lower for b in BROKER_NAMES) or any(r in lower for r in RATING_KEYWORDS)

        pos_hits = sum(1 for w in POSITIVE_WORDS if w in lower)
        neg_hits = sum(1 for w in NEGATIVE_WORDS if w in lower)
        sentiment = 1 if pos_hits > neg_hits else (-1 if neg_hits > pos_hits else 0)

        items.append({
            "title": headline.strip(),
            "source": source.strip(),
            "link": link_el.text if link_el is not None else None,
            "pub_date": pubdate_el.text if pubdate_el is not None else None,
            "is_analyst_mention": is_analyst_mention,
            "sentiment": sentiment,
        })

    return items


def summarize_news(news_items: list) -> str:
    """Plain-text block for feeding into the LLM prompt as grounded fact."""
    if not news_items:
        return "(no recent news found)"
    lines = []
    for n in news_items:
        tag = " [ANALYST/RATING MENTION]" if n["is_analyst_mention"] else ""
        sent = {"1": "+", "-1": "-", "0": "="}.get(str(n["sentiment"]), "=")
        lines.append(f"- [{sent}]{tag} {n['title']} ({n['source']})")
    return "\n".join(lines)


if __name__ == "__main__":
    result = fetch_news("Reliance Industries Ltd", "RELIANCE.NS")
    print(summarize_news(result))
