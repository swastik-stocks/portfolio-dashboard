"""
app.py — Portfolio Dashboard (Streamlit)

Run: streamlit run app.py

Dynamic portfolio tracker: add/edit/delete positions (ticker, purchase
price, quantity, account), live prices via free Yahoo Finance data,
consolidated or per-account view, optional DeepSeek red-flag/SWOT
commentary per stock.

All data lives in a local SQLite file (portfolio.db) — nothing is sent
anywhere except: (1) yfinance price lookups (ticker symbols only, no
personal data), (2) DeepSeek analysis calls IF you click "Analyze" on
a stock (sends only the ticker + company name, nothing else).
"""

import streamlit as st
import pandas as pd
from datetime import datetime

from auth import check_password
from db import (init_db, add_holding, update_holding, delete_holding, get_all_lots,
                 get_consolidated, get_accounts, get_signal_cache, save_signal_cache,
                 get_scanner_signals, get_sector_breadth, get_ticker_sector_map,
                 get_holding_stops, get_industry_breadth)
from price_fetcher import get_live_prices
from deepseek_client import get_analysis, is_configured as deepseek_configured
from signal_engine import VERDICT_COLOR
from quick_signal import compute_quick_signal

st.set_page_config(page_title="Portfolio Dashboard", layout="wide")

if not check_password():
    st.stop()

init_db()

# ── Session state ────────────────────────────────────────────────────────
if "editing_id" not in st.session_state:
    st.session_state.editing_id = None


# ── Helpers ───────────────────────────────────────────────────────────────
def fmt_inr(x: float) -> str:
    return f"₹{x:,.2f}"


def pct(x: float) -> str:
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:.2f}%"


VERDICT_EMOJI = {
    "ADD": "🟢 ADD",
    "HOLD": "🟡 HOLD",
    "TRIM": "🟠 TRIM",
    "STRONG TRIM": "🔴 STRONG TRIM",
    "REVIEW": "⚪ REVIEW",
}


def build_view(rows: list, prices: dict, signal_cache: dict = None) -> pd.DataFrame:
    signal_cache = signal_cache or {}
    out = []
    for r in rows:
        ltp = prices.get(r["symbol"])
        invested = r.get("total_invested", r["qty"] * r["avg_price"])
        current_value = ltp * r["qty"] if ltp is not None else None
        pnl = (current_value - invested) if current_value is not None else None
        pnl_pct = (pnl / invested * 100) if pnl is not None and invested else None

        cached = signal_cache.get(r["symbol"])
        signal_display = VERDICT_EMOJI.get(cached["verdict"], "—") if cached else "— (not run)"

        out.append({
            "id": r.get("id"),
            "Signal": signal_display,
            "Symbol": r["symbol"],
            "Name": r.get("company_name", ""),
            "Account(s)": ", ".join(r["accounts"]) if "accounts" in r else r.get("account", ""),
            "Qty": r["qty"],
            "Avg Price": r["avg_price"],
            "LTP": ltp,
            "Invested": invested,
            "Current Value": current_value,
            "P&L": pnl,
            "P&L %": pnl_pct,
        })
    return pd.DataFrame(out)


# ── Sidebar: Add / Edit holding ──────────────────────────────────────────
with st.sidebar:
    if st.button("🔓 Log out"):
        st.session_state.authenticated = False
        st.rerun()
    st.divider()
    st.header("Add / Edit Holding")

    editing = st.session_state.editing_id is not None
    lots = get_all_lots()
    edit_row = next((l for l in lots if l["id"] == st.session_state.editing_id), None) if editing else None

    with st.form("holding_form", clear_on_submit=not editing):
        symbol = st.text_input(
            "NSE Ticker (e.g. RELIANCE.NS)",
            value=edit_row["symbol"] if edit_row else "",
        ).strip().upper()
        company_name = st.text_input(
            "Company Name",
            value=edit_row["company_name"] if edit_row else "",
        )
        account = st.text_input(
            "Account tag (e.g. AXIS, YES, DHAN)",
            value=edit_row["account"] if edit_row else "",
        )
        qty = st.number_input(
            "Quantity",
            min_value=0.0, step=1.0,
            value=float(edit_row["qty"]) if edit_row else 0.0,
        )
        avg_price = st.number_input(
            "Purchase Price (per share)",
            min_value=0.0, step=0.01, format="%.2f",
            value=float(edit_row["avg_price"]) if edit_row else 0.0,
        )
        notes = st.text_input("Notes (optional)", value=edit_row["notes"] if edit_row else "")

        col1, col2 = st.columns(2)
        submit_label = "Update Holding" if editing else "Add Holding"
        submitted = col1.form_submit_button(submit_label, use_container_width=True)
        cancelled = col2.form_submit_button("Cancel", use_container_width=True) if editing else False

        if submitted and symbol and qty > 0 and avg_price > 0:
            if not symbol.endswith(".NS") and not symbol.endswith(".BO"):
                st.warning("Ticker should end in .NS (NSE) or .BO (BSE) — added .NS automatically.")
                symbol = symbol + ".NS"
            if editing:
                update_holding(st.session_state.editing_id, symbol, company_name, account, qty, avg_price, notes)
                st.session_state.editing_id = None
            else:
                add_holding(symbol, company_name, account, qty, avg_price, notes)
            st.rerun()

        if cancelled:
            st.session_state.editing_id = None
            st.rerun()

    st.divider()
    st.caption(
        "DeepSeek analysis: " + ("configured ✓" if deepseek_configured() else "not configured — add DEEPSEEK_API_KEY to .env to enable")
    )


# ── Main ──────────────────────────────────────────────────────────────────
st.title("📊 Portfolio Dashboard")

lots = get_all_lots()
if not lots:
    st.info("No holdings yet. Add your first position using the sidebar.")
    st.stop()

def render_analysis_result(result, symbol: str = None):
    """Shared rendering for the DeepSeek analysis: verdict badge first
    (the computed decision, big and colored), then the narrative
    explaining it, then the fully auditable grounded data underneath.
    Also updates signal_cache for this symbol with the fresh verdict
    just computed, so the main table's badge reflects it immediately
    without needing a full "Refresh Signals" pass."""
    sig = result["signal"]
    if symbol:
        save_signal_cache(symbol, sig.verdict, sig.fundamental_score,
                          sig.news_sentiment, sig.data_completeness,
                          datetime.now().isoformat())
    color = VERDICT_COLOR.get(sig.verdict, "#8FA3B8")
    completeness_note = "" if sig.data_completeness == "FULL" else f" &nbsp; \u26a0\ufe0f {sig.data_completeness} DATA"
    st.markdown(
        f"<div style='background:{color}22;border:2px solid {color};border-radius:8px;"
        f"padding:12px 18px;margin-bottom:12px'>"
        f"<span style='font-size:24px;font-weight:800;color:{color}'>{sig.verdict}</span>"
        f"<span style='font-size:12px;color:#888'>{completeness_note}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.markdown(result["text"])
    with st.expander("Show grounded data behind this verdict"):
        st.write(f"**Fundamental score:** {sig.fundamental_score:.2f}")
        for factor in sig.fundamental_factors:
            st.write(f"- {factor}")
        st.write(f"**News sentiment (analyst-tagged):** {sig.news_sentiment:+d}")
        for nf in sig.news_factors:
            st.write(f"- {nf}")
        st.write(f"**Position:** {sig.position_note}")
        st.write("**Raw fundamentals (Screener.in):**")
        st.json(result["fundamentals"])


view_mode = st.radio("View", ["Consolidated (across all accounts)", "By Account"], horizontal=True)

col_refresh, col_signals, col_time = st.columns([1, 1, 3])
force_refresh = col_refresh.button("🔄 Refresh Prices")
refresh_signals_clicked = col_signals.button("🚦 Refresh Signals")

all_symbols = sorted(set(l["symbol"] for l in lots))
with st.spinner("Fetching live prices..."):
    prices = get_live_prices(all_symbols, force_refresh=force_refresh)

col_time.caption(f"Prices as of {datetime.now().strftime('%H:%M:%S')} (cached ~60s)")

missing_prices = [s for s in all_symbols if prices.get(s) is None]
if missing_prices:
    st.warning(f"Couldn't fetch live price for: {', '.join(missing_prices)}. "
               f"Check the ticker is correct, or market may be closed with no cached data yet.")

# ── Refresh Signals: computes ADD/HOLD/TRIM for every holding at once ────
# Deterministic only (no DeepSeek call) -- see quick_signal.py. Rate-limited
# by fundamentals.py's polite Screener.in delay, so this takes roughly
# 2 seconds per unique symbol -- a manual action, not run on every render.
if refresh_signals_clicked:
    consolidated_for_signals = get_consolidated()
    progress = st.progress(0.0, text="Refreshing signals...")
    for i, r in enumerate(consolidated_for_signals):
        symbol = r["symbol"]
        ltp = prices.get(symbol)
        invested = r["total_invested"]
        pnl = (ltp * r["qty"] - invested) if ltp is not None else None
        pnl_pct = (pnl / invested * 100) if pnl is not None and invested else None
        position = {"qty": r["qty"], "avg_price": r["avg_price"], "ltp": ltp,
                    "pnl": pnl, "pnl_pct": pnl_pct}
        progress.progress((i) / len(consolidated_for_signals),
                          text=f"Refreshing signals... {symbol} ({i+1}/{len(consolidated_for_signals)})")
        sig = compute_quick_signal(symbol, r["company_name"], position=position)
        save_signal_cache(symbol, sig.verdict, sig.fundamental_score,
                          sig.news_sentiment, sig.data_completeness,
                          datetime.now().isoformat())
    progress.progress(1.0, text="Signals refreshed.")
    st.rerun()

signal_cache = get_signal_cache()
if signal_cache:
    oldest = min(signal_cache.values(), key=lambda r: r["computed_at"])
    st.caption(f"Signals last refreshed: {oldest['computed_at'][:16].replace('T', ' ')} "
               f"(click 🚦 Refresh Signals above to update)")
else:
    st.info("No signals computed yet — click 🚦 Refresh Signals above to see ADD/HOLD/TRIM "
            "verdicts for all your holdings at once.")

# ── Build the table depending on view mode ──────────────────────────────
if view_mode.startswith("Consolidated"):
    rows = get_consolidated()
    df = build_view(rows, prices, signal_cache)
    st.caption("To edit or delete a specific holding, switch to \"By Account\" view above.")
else:
    df = build_view(lots, prices, signal_cache)

# ── Summary metrics ──────────────────────────────────────────────────────
total_invested = df["Invested"].sum()
total_current = df["Current Value"].sum(skipna=True)
total_pnl = total_current - total_invested if pd.notna(total_current) else None
total_pnl_pct = (total_pnl / total_invested * 100) if total_pnl is not None and total_invested else None

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Invested", fmt_inr(total_invested))
m2.metric("Current Value", fmt_inr(total_current) if pd.notna(total_current) else "—")
m3.metric("Total P&L", fmt_inr(total_pnl) if total_pnl is not None else "—",
          delta=pct(total_pnl_pct) if total_pnl_pct is not None else None)
m4.metric("Positions", len(df))

st.divider()

# ── P4-08: Dhan token status warning ─────────────────────────────────────
# data_fetcher._check_dhan_auth() runs at scan start and publishes the
# result to Turso's scanner_signals table's metadata field. We surface it
# here so a stale token (24h SEBI-mandated expiry) is visible on the
# dashboard before the next evening scan, not discovered after 500 tickers
# have already fallen back to the slower Yahoo chain.
# Reads from the most recent scanner signal's dhan_status field if present,
# otherwise reads directly from the last scan log via a lightweight check.
try:
    _recent_signals = get_scanner_signals()
    _dhan_ok = None
    _dhan_msg = ""
    if _recent_signals:
        _dhan_ok = _recent_signals[0].get("dhan_available")
        _dhan_msg = _recent_signals[0].get("dhan_message", "")
    if _dhan_ok is False:
        st.warning(
            f"⚠️ **Dhan token invalid or expired** — last evening scan fell back to Yahoo Finance. "
            f"Refresh your token at [web.dhan.co](https://web.dhan.co), then paste the new "
            f"`DHAN_ACCESS_TOKEN` into `F:\\nse_momentum\\.env`. "
            f"{'Details: ' + _dhan_msg if _dhan_msg else ''}"
        )
    elif _dhan_ok is True:
        pass  # token valid — no banner needed, don't create noise
    # _dhan_ok is None means the field isn't in scanner_signals yet (pre-P4-08
    # schema) — silent, don't show a false alarm
except Exception:
    pass  # Dhan status check must never break the dashboard

# ── Holdings table ────────────────────────────────────────────────────────
display_df = df.copy()
for col in ["Avg Price", "LTP", "Invested", "Current Value", "P&L"]:
    display_df[col] = display_df[col].apply(lambda x: fmt_inr(x) if pd.notna(x) else "—")
display_df["P&L %"] = df["P&L %"].apply(lambda x: pct(x) if pd.notna(x) else "—")

st.dataframe(
    display_df.drop(columns=["id"]) if "id" in display_df.columns else display_df,
    use_container_width=True,
    hide_index=True,
)

# ── Per-holding actions (edit/delete/analyze) — only meaningful in raw lot view ──
if not view_mode.startswith("Consolidated"):
    st.subheader("Manage Lots")
    for lot in lots:
        with st.expander(f"{lot['symbol']} — {lot['account']} — {lot['qty']} @ ₹{lot['avg_price']:.2f}"):
            c1, c2, c3 = st.columns(3)
            if c1.button("Edit", key=f"edit_{lot['id']}"):
                st.session_state.editing_id = lot["id"]
                st.rerun()
            if c2.button("Delete", key=f"delete_{lot['id']}"):
                delete_holding(lot["id"])
                st.rerun()
            if c3.button("Analyze (DeepSeek)", key=f"analyze_{lot['id']}", disabled=not deepseek_configured()):
                ltp = prices.get(lot["symbol"])
                invested = lot["qty"] * lot["avg_price"]
                pnl = (ltp * lot["qty"] - invested) if ltp is not None else None
                pnl_pct = (pnl / invested * 100) if pnl is not None and invested else None
                position = {"qty": lot["qty"], "avg_price": lot["avg_price"], "ltp": ltp,
                            "pnl": pnl, "pnl_pct": pnl_pct}
                with st.spinner("Fetching fundamentals + news + analyzing..."):
                    result = get_analysis(lot["symbol"], lot["company_name"], position=position)
                if result:
                    render_analysis_result(result, symbol=lot["symbol"])
                else:
                    st.error("Analysis unavailable — check DEEPSEEK_API_KEY in .env, or Screener.in fetch failed")
else:
    st.subheader("Per-Stock Analysis")
    consolidated = get_consolidated()
    # Sort so TRIM/STRONG TRIM/REVIEW stocks surface first -- these are the
    # ones actually worth spending a DeepSeek call to dig into.
    verdict_priority = {"STRONG TRIM": 0, "TRIM": 1, "REVIEW": 2, "HOLD": 3, "ADD": 4}
    def _sort_key(r):
        cached = signal_cache.get(r["symbol"])
        v = cached["verdict"] if cached else "REVIEW"
        return verdict_priority.get(v, 5)
    consolidated_sorted = sorted(consolidated, key=_sort_key)

    def _label(r):
        cached = signal_cache.get(r["symbol"])
        badge = VERDICT_EMOJI.get(cached["verdict"], "") if cached else "(no signal yet)"
        return f"{badge}  {r['symbol']}"

    symbol_labels = [_label(r) for r in consolidated_sorted]
    chosen_label = st.selectbox("Select a stock (worst signals shown first)", symbol_labels)
    symbol_choice = consolidated_sorted[symbol_labels.index(chosen_label)]["symbol"]
    if st.button("Analyze (DeepSeek)", disabled=not deepseek_configured()):
        chosen = next(r for r in consolidated if r["symbol"] == symbol_choice)
        ltp = prices.get(chosen["symbol"])
        invested = chosen["total_invested"]
        pnl = (ltp * chosen["qty"] - invested) if ltp is not None else None
        pnl_pct = (pnl / invested * 100) if pnl is not None and invested else None
        position = {"qty": chosen["qty"], "avg_price": chosen["avg_price"], "ltp": ltp,
                    "pnl": pnl, "pnl_pct": pnl_pct}
        with st.spinner("Fetching fundamentals + news + analyzing..."):
            result = get_analysis(chosen["symbol"], chosen["company_name"], position=position)
        if result:
            render_analysis_result(result, symbol=chosen["symbol"])
        else:
            st.error("Analysis unavailable — check DEEPSEEK_API_KEY in .env, or Screener.in fetch failed")

# ── Scanner Signals (P1-05) — NSE Momentum's evening-scan picks, shown ───
# alongside holdings so one screen answers both "what do I own" and
# "what's worth looking at today." Read-only, best-effort: get_scanner_signals()
# already returns [] on any failure (missing table, connection issue, etc.)
# so this section simply doesn't render rather than breaking the dashboard.
st.divider()
st.subheader("🎯 Scanner Signals (NSE Momentum)")

signals = get_scanner_signals()
if not signals:
    st.info("No scanner signals published yet — the evening scan runs Mon–Fri after market close "
            "(NSE Momentum's daily_scan.yml). Check back after the next trading day.")
else:
    scan_date = signals[0].get("scan_date", "")
    st.caption(f"From the {scan_date} evening scan — {len(signals)} signal(s), sorted by score. "
               f"Not SEBI-registered investment advice.")

    held_symbols = {lot["symbol"] for lot in lots}

    signal_rows = []
    for s in signals:
        signal_rows.append({
            "Owned": "✅" if s.get("ticker") in held_symbols else "",
            "Ticker": s.get("ticker", ""),
            "Pattern": s.get("pattern") or "",
            "Score": s.get("total_score"),
            "Tier": s.get("tier"),
            "Confidence": f"{s['confidence_pct']:.0f}%" if s.get("confidence_pct") is not None else "—",
            "Entry": s.get("entry_price"),
            "Pivot": s.get("pivot"),
            "Stop": s.get("stop_loss"),
            "Target 1": s.get("target1"),
            "Target 2": s.get("target2"),
            "R:R": f"{s['rrr']:.1f}x" if s.get("rrr") is not None else "—",
            "RS %ile": f"{s['rs_percentile']:.0f}th" if s.get("rs_percentile") is not None else "—",
            "Regime": s.get("regime") or "",
        })
    signal_df = pd.DataFrame(signal_rows)
    for money_col in ["Entry", "Pivot", "Stop", "Target 1", "Target 2"]:
        signal_df[money_col] = signal_df[money_col].apply(lambda x: fmt_inr(x) if pd.notna(x) else "—")

    st.dataframe(signal_df, use_container_width=True, hide_index=True)

    owned_count = sum(1 for s in signals if s.get("ticker") in held_symbols)
    if owned_count:
        st.caption(f"✅ {owned_count} of these signals are for stocks you already own.")

# ── Sector Rotation (P3-02) ──────────────────────────────────────────────
st.divider()
st.subheader("🏭 Sector Rotation")

breadth_rows = get_sector_breadth()
if not breadth_rows:
    st.info("No sector breadth data published yet — run sector_breadth.py on the NSE Momentum side.")
else:
    breadth_date_shown = breadth_rows[0].get("breadth_date", "")
    st.caption(f"As of {breadth_date_shown} — % of stocks in each sector above SMA20/50/100, "
               f"RSI(14)>50, and with a positive 55-day return. Count-based, not market-cap-weighted.")
    breadth_df = pd.DataFrame([{
        "Sector":    r.get("sector", ""),
        "Stocks":    r.get("stock_count"),
        "SMA20 %":   r.get("pct_above_sma20"),
        "SMA50 %":   r.get("pct_above_sma50"),
        "SMA100 %":  r.get("pct_above_sma100"),
        "RSI50 %":   r.get("pct_above_rsi50"),
        "RS55 %":    r.get("pct_above_rs55"),
    } for r in breadth_rows])
    pct_cols = ["SMA20 %", "SMA50 %", "SMA100 %", "RSI50 %", "RS55 %"]
    styled_breadth = (
        breadth_df.style
        .background_gradient(cmap="RdYlGn", subset=pct_cols, vmin=0, vmax=100)
        .format({col: "{:.1f}%" for col in pct_cols})
    )
    st.dataframe(styled_breadth, use_container_width=True, hide_index=True)

# ── Sector Concentration (P3-08) ─────────────────────────────────────────
CONCENTRATION_THRESHOLD_PCT = 25.0
WEAK_SECTOR_SMA50_PCT = 50.0

st.divider()
st.subheader("⚖️ Sector Concentration")

ticker_sector = get_ticker_sector_map()
sector_breadth_rows = get_sector_breadth()
consolidated_holdings = get_consolidated()

if not ticker_sector or not consolidated_holdings:
    st.info("Sector concentration check needs both your holdings and the ticker->sector map "
            "from NSE Momentum's sector_breadth.py — one of these isn't available yet.")
else:
    sector_breadth_lookup = {r["sector"]: r for r in sector_breadth_rows}
    sector_value = {}
    total_value = 0.0
    unmapped_tickers = []
    for h in consolidated_holdings:
        sector = ticker_sector.get(h["symbol"])
        value = h.get("total_invested", h["qty"] * h["avg_price"])
        total_value += value
        if sector:
            sector_value[sector] = sector_value.get(sector, 0.0) + value
        else:
            unmapped_tickers.append(h["symbol"])

    if total_value > 0:
        concentration_rows = []
        for sector, value in sorted(sector_value.items(), key=lambda x: -x[1]):
            pct_of_portfolio = value / total_value * 100
            breadth = sector_breadth_lookup.get(sector)
            sma50 = breadth.get("pct_above_sma50") if breadth else None
            is_concentrated = pct_of_portfolio >= CONCENTRATION_THRESHOLD_PCT
            is_weak = sma50 is not None and sma50 < WEAK_SECTOR_SMA50_PCT
            flag = ""
            if is_concentrated and is_weak:
                flag = "🔴 Concentrated + weak sector"
            elif is_concentrated:
                flag = "🟡 Concentrated"
            concentration_rows.append({
                "Sector": sector, "Invested": fmt_inr(value),
                "% of Portfolio": f"{pct_of_portfolio:.1f}%",
                "Sector SMA50 Breadth": f"{sma50:.1f}%" if sma50 is not None else "—",
                "Flag": flag,
            })
        st.dataframe(pd.DataFrame(concentration_rows), use_container_width=True, hide_index=True)
        flagged = [r for r in concentration_rows if r["Flag"]]
        if flagged:
            st.warning(f"⚠️ {len(flagged)} sector(s) flagged — concentrated (≥{CONCENTRATION_THRESHOLD_PCT:.0f}%) "
                       f"and/or in a weak sector (<{WEAK_SECTOR_SMA50_PCT:.0f}% above SMA50).")
        else:
            st.caption(f"No sector currently exceeds {CONCENTRATION_THRESHOLD_PCT:.0f}% of your portfolio.")
        if unmapped_tickers:
            st.caption(f"Note: {len(unmapped_tickers)} held ticker(s) have no sector mapping yet "
                       f"({', '.join(unmapped_tickers)}) — excluded from the percentages above.")

# ── Portfolio Heat (P4-03) ────────────────────────────────────────────────
# Total open risk = Σ(distance-to-stop × position_value) per held stock.
# Stops are published nightly by NSE Momentum's scanner via
# turso_sync.publish_holding_stops(), using the same compute_holding_stop()
# methodology (EMA21/10D_LOW/ATR, per-tier capped) as P2-05's EXIT alerts.
# Falls back gracefully to [] if the data isn't available yet (P1-06).
st.divider()
st.subheader("🌡️ Portfolio Heat")

holding_stops = get_holding_stops()
if not holding_stops:
    st.info("No portfolio heat data yet — this populates after the next evening scan "
            "(NSE Momentum's daily_scan.yml, Mon–Fri after market close).")
else:
    heat_date = next(iter(holding_stops.values())).get("action_date", "")
    st.caption(f"As of {heat_date} evening scan. Heat = distance-to-stop × position value. "
               f"Higher = more capital at risk if stop is hit. Not a sell signal — a sizing check.")

    heat_rows = []
    total_heat = 0.0
    total_portfolio_value = 0.0

    for h in consolidated_holdings:
        ticker = h["symbol"]
        stop_data = holding_stops.get(ticker)
        ltp = prices.get(ticker)
        position_value = ltp * h["qty"] if ltp else h["qty"] * h["avg_price"]
        total_portfolio_value += position_value

        if stop_data and ltp:
            stop = stop_data["stop"]
            distance_pct = (ltp - stop) / ltp * 100 if ltp > 0 else 0.0
            heat_inr = max(0.0, (ltp - stop) * h["qty"])
            total_heat += heat_inr
            heat_rows.append({
                "Ticker":         ticker,
                "LTP":            fmt_inr(ltp),
                "Stop":           fmt_inr(stop),
                "Stop Method":    stop_data.get("method", ""),
                "Distance %":     f"{distance_pct:.1f}%",
                "Position Value": fmt_inr(position_value),
                "Heat (₹ at risk)": fmt_inr(heat_inr),
            })
        else:
            heat_rows.append({
                "Ticker":         ticker,
                "LTP":            fmt_inr(ltp) if ltp else "—",
                "Stop":           "—",
                "Stop Method":    "—",
                "Distance %":     "—",
                "Position Value": fmt_inr(position_value),
                "Heat (₹ at risk)": "—",
            })

    heat_rows.sort(key=lambda x: float(x["Heat (₹ at risk)"].replace("₹","").replace(",",""))
                   if x["Heat (₹ at risk)"] != "—" else 0, reverse=True)
    st.dataframe(pd.DataFrame(heat_rows), use_container_width=True, hide_index=True)

    heat_pct = (total_heat / total_portfolio_value * 100) if total_portfolio_value > 0 else 0.0
    col_h1, col_h2 = st.columns(2)
    col_h1.metric("Total Portfolio Heat", fmt_inr(total_heat),
                  help="Total ₹ at risk if every held position hits its stop simultaneously")
    col_h2.metric("Heat as % of Portfolio", f"{heat_pct:.1f}%",
                  help="Standard risk-management rule of thumb: keep total heat below 5-10% of capital")
    if heat_pct > 10:
        st.warning(f"⚠️ Total portfolio heat is {heat_pct:.1f}% of estimated portfolio value — "
                   f"above the typical 10% guideline. Consider reducing position sizes or tightening stops.")

# ── Industry Breadth (P3-04) ─────────────────────────────────────────────
# 138-industry granularity, same breadth flags as Sector Rotation (P3-02)
# but at Screener.in's 4-level taxonomy (broad_sector/sector/broad_industry/
# industry). Useful for drilling below a sector-level signal — e.g. "Capital
# Goods breadth is weak" -> which sub-industries are leading vs lagging?
# Published by NSE Momentum's industry_breadth.py (P3-04).
st.divider()
st.subheader("🏗️ Industry Breadth (138-industry)")

industry_rows = get_industry_breadth()
if not industry_rows:
    st.info("No industry breadth data published yet — run industry_breadth.py on the NSE Momentum side.")
else:
    ind_date_shown = industry_rows[0].get("breadth_date", "")
    st.caption(
        f"As of {ind_date_shown} — same breadth flags as Sector Rotation but at 138-industry "
        f"granularity (Screener.in taxonomy). Sorted by SMA50 breadth descending. "
        f"Low-count industries (≤2 stocks) shown but less statistically meaningful."
    )

    # Search/filter box — 138 rows is too many to scroll without it
    ind_search = st.text_input(
        "Filter by industry name", placeholder="e.g. Pharma, Bank, Auto...",
        key="industry_search"
    ).strip().lower()

    industry_df = pd.DataFrame([{
        "Industry":   r.get("industry", ""),
        "Stocks":     r.get("stock_count"),
        "SMA20 %":    r.get("pct_above_sma20"),
        "SMA50 %":    r.get("pct_above_sma50"),
        "SMA100 %":   r.get("pct_above_sma100"),
        "RSI50 %":    r.get("pct_above_rsi50"),
        "RS55 %":     r.get("pct_above_rs55"),
    } for r in industry_rows])

    if ind_search:
        industry_df = industry_df[
            industry_df["Industry"].str.lower().str.contains(ind_search, na=False)
        ]
        if industry_df.empty:
            st.caption(f"No industries matching '{ind_search}'.")

    if not industry_df.empty:
        ind_pct_cols = ["SMA20 %", "SMA50 %", "SMA100 %", "RSI50 %", "RS55 %"]
        styled_ind = (
            industry_df.style
            .background_gradient(cmap="RdYlGn", subset=ind_pct_cols, vmin=0, vmax=100)
            .format({col: "{:.1f}%" for col in ind_pct_cols})
        )
        st.dataframe(styled_ind, use_container_width=True, hide_index=True)
        st.caption(f"Showing {len(industry_df)} of {len(industry_rows)} industries.")

st.divider()
st.caption(
    "Not SEBI-registered investment advice. Prices via Yahoo Finance (free, "
    "may lag real-time by a few minutes). DeepSeek analysis is for research "
    "reference only, not a buy/sell recommendation."
)
