"""
signal_engine.py — deterministic ADD / TRIM / HOLD / REVIEW verdict.

Same principle as red_flags.py and your own pattern_agent.py: the
DECISION is computed in pure Python from real inputs, fully auditable.
DeepSeek's job (in deepseek_client.py) is to explain this computed
verdict in plain English -- it does not invent the verdict itself.

Two separate axes, deliberately kept apart rather than blended into one
opaque score:

  1. FUNDAMENTAL AXIS -- "does the business still look sound?"
     Built from red_flags.py output + recent news sentiment. Independent
     of what YOU paid or how much you're up/down.

  2. POSITION AXIS -- "should you rebalance regardless of fundamentals?"
     Built from your unrealized P&L%. A big unrealized gain isn't a
     fundamental problem, but it's a legitimate reason to consider
     trimming for risk management -- kept as a separate, clearly-labeled
     factor rather than disguised as a fundamental red flag.

Data completeness is judged from the red_flags themselves (how many
came back NA), not from the raw fundamentals dict size -- a stock with
only 2-3 parseable Screener.in fields can still yield a fully confident
signal if those 2-3 fields are the ones that matter (e.g. promoter
pledge + D/E alone can already justify STRONG TRIM).
"""

from dataclasses import dataclass, field


@dataclass
class Signal:
    verdict: str  # "ADD", "HOLD", "TRIM", "STRONG TRIM", "REVIEW"
    fundamental_score: float
    fundamental_factors: list = field(default_factory=list)
    position_note: str = ""
    news_sentiment: int = 0
    news_factors: list = field(default_factory=list)
    data_completeness: str = "FULL"  # "FULL", "PARTIAL", "INSUFFICIENT"


def _score_fundamentals(red_flags: list):
    """Returns (score, factors, na_count, total_count)."""
    if not red_flags:
        return 0.0, ["No red flags computed -- fundamentals fetch likely failed"], 0, 0

    score = 0.0
    factors = []
    na_count = 0
    for f in red_flags:
        if f.severity == "FAIL":
            score -= 1.0
            factors.append(f"[FAIL] {f.rule}: {f.detail}")
        elif f.severity == "WARN":
            score -= 0.3
            factors.append(f"[WARN] {f.rule}: {f.detail}")
        elif f.severity == "OK":
            score += 0.2
            factors.append(f"[OK] {f.rule}: {f.detail}")
        else:
            na_count += 1

    return score, factors, na_count, len(red_flags)


def _score_news(news_items: list):
    if not news_items:
        return 0, ["No recent news found"]

    analyst_items = [n for n in news_items if n["is_analyst_mention"]]
    if not analyst_items:
        return 0, ["No analyst/broker-tagged headlines in recent news (general news only)"]

    net = sum(n["sentiment"] for n in analyst_items)
    factors = [f"{n['title']} ({n['source']})" for n in analyst_items]
    return net, factors


def _position_note(position: dict) -> str:
    if not position or position.get("pnl_pct") is None:
        return "No P&L data available"
    pnl_pct = position["pnl_pct"]
    if pnl_pct >= 50:
        return f"Up {pnl_pct:+.1f}% -- large unrealized gain, worth considering partial profit booking regardless of fundamentals"
    elif pnl_pct >= 20:
        return f"Up {pnl_pct:+.1f}% -- healthy gain, no urgency to act on this alone"
    elif pnl_pct <= -25:
        return f"Down {pnl_pct:+.1f}% -- significant unrealized loss, worth weighing against fundamental picture below"
    elif pnl_pct <= -10:
        return f"Down {pnl_pct:+.1f}% -- moderate drawdown"
    else:
        return f"{pnl_pct:+.1f}% -- roughly flat, no position-driven pressure either way"


def compute_signal(red_flags: list, fundamentals: dict, news_items: list, position: dict) -> Signal:
    fund_score, fund_factors, na_count, total_count = _score_fundamentals(red_flags)
    news_sentiment, news_factors = _score_news(news_items)
    pos_note = _position_note(position)

    if total_count == 0:
        data_completeness = "INSUFFICIENT"
    elif na_count / total_count >= 0.7:
        data_completeness = "PARTIAL"
        fund_factors = fund_factors + [f"{na_count}/{total_count} checks had no data -- lower confidence"]
    else:
        data_completeness = "FULL"

    pnl_pct = position.get("pnl_pct") if position else None

    if data_completeness == "INSUFFICIENT":
        verdict = "REVIEW"
    elif fund_score <= -1.5:
        verdict = "STRONG TRIM" if (pnl_pct is not None and pnl_pct < 0) else "TRIM"
    elif fund_score <= -0.5:
        verdict = "TRIM"
    elif fund_score >= 0.8 and news_sentiment >= 0:
        verdict = "HOLD" if (pnl_pct is not None and pnl_pct >= 50) else "ADD"
    elif fund_score >= 0.8 and news_sentiment < 0:
        verdict = "HOLD"
    elif data_completeness == "PARTIAL":
        verdict = "REVIEW"
    else:
        verdict = "HOLD"

    return Signal(
        verdict=verdict,
        fundamental_score=fund_score,
        fundamental_factors=fund_factors,
        position_note=pos_note,
        news_sentiment=news_sentiment,
        news_factors=news_factors,
        data_completeness=data_completeness,
    )


VERDICT_COLOR = {
    "ADD": "#00D4AA",
    "HOLD": "#FFB300",
    "TRIM": "#FF8C00",
    "STRONG TRIM": "#FF5252",
    "REVIEW": "#8FA3B8",
}


if __name__ == "__main__":
    from red_flags import RedFlag

    print("--- Scenario 1: weak fundamentals + losing position ---")
    test_flags = [
        RedFlag("Promoter Pledge", "FAIL", "35% pledged"),
        RedFlag("Debt/Equity", "WARN", "D/E 1.5"),
        RedFlag("Revenue Growth", "OK", "+12%"),
        RedFlag("Promoter Holding", "OK", "60%"),
        RedFlag("Net Profit Trend", "WARN", "-8%"),
        RedFlag("P/E Ratio", "OK", "18.0"),
    ]
    test_news = [{"title": "Jefferies downgrades to Sell", "source": "Test", "is_analyst_mention": True, "sentiment": -1}]
    test_position = {"qty": 100, "avg_price": 500, "ltp": 350, "pnl": -15000, "pnl_pct": -30.0}
    sig = compute_signal(test_flags, {"pe_ratio": 20}, test_news, test_position)
    print("Verdict:", sig.verdict, "| completeness:", sig.data_completeness, "| fund_score:", sig.fundamental_score)

    print("\n--- Scenario 2: strong fundamentals, big winner ---")
    test_flags2 = [
        RedFlag("Promoter Pledge", "OK", "0%"),
        RedFlag("Debt/Equity", "OK", "D/E 0.3"),
        RedFlag("Revenue Growth", "OK", "+22%"),
        RedFlag("Promoter Holding", "OK", "65%"),
        RedFlag("Net Profit Trend", "OK", "+30%"),
        RedFlag("P/E Ratio", "OK", "25.0"),
    ]
    test_position2 = {"qty": 50, "avg_price": 500, "ltp": 850, "pnl": 17500, "pnl_pct": 70.0}
    sig2 = compute_signal(test_flags2, {"pe_ratio": 25}, [], test_position2)
    print("Verdict:", sig2.verdict, "| completeness:", sig2.data_completeness, "| fund_score:", sig2.fundamental_score)

    print("\n--- Scenario 3: insufficient data ---")
    sig3 = compute_signal([], {}, [], {"pnl_pct": 5.0})
    print("Verdict:", sig3.verdict, "| completeness:", sig3.data_completeness)
