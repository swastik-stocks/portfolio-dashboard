"""
red_flags.py — pure-Python deterministic rule engine.

Same principle as the NEXUS architecture review and your own
pattern_agent.py: the LLM narrates, it does NOT decide. Every rule here
is auditable, reproducible, and independent of any AI call. Given the
same fundamentals dict, this always returns the same flags — no model
variance, no hallucination risk on the pass/fail logic itself.

Severity: FAIL (serious), WARN (worth watching), OK (no issue), NA
(insufficient data — never silently defaults to a pass).
"""

from dataclasses import dataclass


@dataclass
class RedFlag:
    rule: str
    severity: str  # "FAIL", "WARN", "OK", "NA"
    detail: str


def evaluate(fundamentals: dict) -> list[RedFlag]:
    flags = []

    # --- Promoter pledge ---
    pledge = fundamentals.get("promoter_pledge_pct")
    if pledge is None:
        flags.append(RedFlag("Promoter Pledge", "NA", "Pledge data not available"))
    elif pledge > 20:
        flags.append(RedFlag("Promoter Pledge", "FAIL", f"{pledge:.1f}% pledged — high risk of forced selling"))
    elif pledge > 0:
        flags.append(RedFlag("Promoter Pledge", "WARN", f"{pledge:.1f}% pledged — monitor"))
    else:
        flags.append(RedFlag("Promoter Pledge", "OK", "No pledge reported"))

    # --- Debt to Equity ---
    de = fundamentals.get("debt_to_equity")
    if de is None:
        flags.append(RedFlag("Debt/Equity", "NA", "D/E data not available"))
    elif de > 2:
        flags.append(RedFlag("Debt/Equity", "FAIL", f"D/E {de:.2f} — high leverage (non-BFSI context assumed)"))
    elif de > 1:
        flags.append(RedFlag("Debt/Equity", "WARN", f"D/E {de:.2f} — moderate leverage"))
    else:
        flags.append(RedFlag("Debt/Equity", "OK", f"D/E {de:.2f} — conservative"))

    # --- Promoter holding trend (single snapshot here — trend needs multi-quarter,
    # noted as a known v1 limitation) ---
    promoter_pct = fundamentals.get("promoter_holding_pct")
    if promoter_pct is None:
        flags.append(RedFlag("Promoter Holding", "NA", "Holding % not available"))
    elif promoter_pct < 25:
        flags.append(RedFlag("Promoter Holding", "WARN", f"{promoter_pct:.1f}% — relatively low promoter stake"))
    else:
        flags.append(RedFlag("Promoter Holding", "OK", f"{promoter_pct:.1f}% promoter stake"))

    # --- Revenue growth (latest vs previous period shown on Screener) ---
    sales_latest = fundamentals.get("sales_latest_cr")
    sales_prev = fundamentals.get("sales_prev_cr")
    if sales_latest is not None and sales_prev is not None and sales_prev != 0:
        growth = (sales_latest - sales_prev) / abs(sales_prev) * 100
        if growth < 0:
            flags.append(RedFlag("Revenue Growth", "WARN", f"{growth:.1f}% — revenue declined period-over-period"))
        else:
            flags.append(RedFlag("Revenue Growth", "OK", f"+{growth:.1f}% period-over-period"))
    else:
        flags.append(RedFlag("Revenue Growth", "NA", "Insufficient period data"))

    # --- Net profit vs revenue direction (crude proxy for margin direction) ---
    np_latest = fundamentals.get("net_profit_latest_cr")
    np_prev = fundamentals.get("net_profit_prev_cr")
    if np_latest is not None and np_prev is not None and np_prev != 0:
        pat_growth = (np_latest - np_prev) / abs(np_prev) * 100
        if pat_growth < 0:
            flags.append(RedFlag("Net Profit Trend", "WARN", f"{pat_growth:.1f}% — profit declined period-over-period"))
        else:
            flags.append(RedFlag("Net Profit Trend", "OK", f"+{pat_growth:.1f}% period-over-period"))
    else:
        flags.append(RedFlag("Net Profit Trend", "NA", "Insufficient period data"))

    # --- Valuation sanity (P/E only — no peer comparison in v1) ---
    pe = fundamentals.get("pe_ratio")
    if pe is None:
        flags.append(RedFlag("P/E Ratio", "NA", "P/E not available (may be loss-making or not applicable, e.g. ETF)"))
    elif pe > 60:
        flags.append(RedFlag("P/E Ratio", "WARN", f"P/E {pe:.1f} — expensive in absolute terms, check sector context"))
    elif pe < 0:
        flags.append(RedFlag("P/E Ratio", "WARN", f"P/E negative — company reporting losses"))
    else:
        flags.append(RedFlag("P/E Ratio", "OK", f"P/E {pe:.1f}"))

    return flags


def summarize(flags: list[RedFlag]) -> str:
    """Plain-text summary block to feed into the LLM prompt as grounded fact."""
    lines = []
    for f in flags:
        lines.append(f"- {f.rule}: [{f.severity}] {f.detail}")
    return "\n".join(lines)


if __name__ == "__main__":
    test_fundamentals = {
        "promoter_pledge_pct": 15.0,
        "debt_to_equity": 2.5,
        "promoter_holding_pct": 55.0,
        "sales_latest_cr": 1000,
        "sales_prev_cr": 900,
        "net_profit_latest_cr": 80,
        "net_profit_prev_cr": 100,
        "pe_ratio": 35.0,
    }
    result = evaluate(test_fundamentals)
    print(summarize(result))
