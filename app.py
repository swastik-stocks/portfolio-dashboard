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

import os
import streamlit as st
import pandas as pd
from datetime import datetime

from auth import check_password
from db import init_db, add_holding, update_holding, delete_holding, get_all_lots, get_consolidated, get_accounts
from price_fetcher import get_live_prices
from deepseek_client import get_analysis, is_configured as deepseek_configured
from signal_engine import VERDICT_COLOR

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


def build_view(rows: list, prices: dict) -> pd.DataFrame:
    out = []
    for r in rows:
        ltp = prices.get(r["symbol"])
        invested = r.get("total_invested", r["qty"] * r["avg_price"])
        current_value = ltp * r["qty"] if ltp is not None else None
        pnl = (current_value - invested) if current_value is not None else None
        pnl_pct = (pnl / invested * 100) if pnl is not None and invested else None
        out.append({
            "id": r.get("id"),
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

with st.expander("🔧 DIAGNOSTIC (temporary -- remove once things work)"):
    st.write("**Environment variables (os.getenv) -- presence only, not values:**")
    st.write(f"- TURSO_DATABASE_URL set: {bool(os.getenv('TURSO_DATABASE_URL'))}")
    st.write(f"- TURSO_AUTH_TOKEN set: {bool(os.getenv('TURSO_AUTH_TOKEN'))}")
    st.write(f"- DEEPSEEK_API_KEY set: {bool(os.getenv('DEEPSEEK_API_KEY'))}")
    st.write("**st.secrets (Streamlit's own secrets store):**")
    try:
        st.write(f"- 'auth' section present: {'auth' in st.secrets}")
        if 'auth' in st.secrets:
            st.write(f"- auth.username set: {bool(st.secrets['auth'].get('username'))}")
        st.write(f"- Root-level TURSO_DATABASE_URL in st.secrets: {'TURSO_DATABASE_URL' in st.secrets}")
        st.write(f"- Root-level DEEPSEEK_API_KEY in st.secrets: {'DEEPSEEK_API_KEY' in st.secrets}")
    except Exception as e:
        st.write(f"Error reading st.secrets: {e}")
    st.write("**db.py module-level (what it actually loaded at import time):**")
    import db as _db_module
    st.write(f"- db.TURSO_URL is set: {bool(_db_module.TURSO_URL)}")

lots = get_all_lots()
if not lots:
    st.info("No holdings yet. Add your first position using the sidebar.")
    st.stop()

def render_analysis_result(result):
    """Shared rendering for the DeepSeek analysis: verdict badge first
    (the computed decision, big and colored), then the narrative
    explaining it, then the fully auditable grounded data underneath."""
    sig = result["signal"]
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

col_refresh, col_time = st.columns([1, 4])
force_refresh = col_refresh.button("🔄 Refresh Prices")

all_symbols = sorted(set(l["symbol"] for l in lots))
with st.spinner("Fetching live prices..."):
    prices = get_live_prices(all_symbols, force_refresh=force_refresh)

col_time.caption(f"Prices as of {datetime.now().strftime('%H:%M:%S')} (cached ~60s)")

missing_prices = [s for s in all_symbols if prices.get(s) is None]
if missing_prices:
    st.warning(f"Couldn't fetch live price for: {', '.join(missing_prices)}. "
               f"Check the ticker is correct, or market may be closed with no cached data yet.")

# ── Build the table depending on view mode ──────────────────────────────
if view_mode.startswith("Consolidated"):
    rows = get_consolidated()
    df = build_view(rows, prices)
else:
    df = build_view(lots, prices)

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
                    render_analysis_result(result)
                else:
                    st.error("Analysis unavailable — check DEEPSEEK_API_KEY in .env, or Screener.in fetch failed")
else:
    st.subheader("Per-Stock Analysis")
    consolidated = get_consolidated()
    symbol_choice = st.selectbox("Select a stock", [r["symbol"] for r in consolidated])
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
            render_analysis_result(result)
        else:
            st.error("Analysis unavailable — check DEEPSEEK_API_KEY in .env, or Screener.in fetch failed")

st.divider()
st.caption(
    "Not SEBI-registered investment advice. Prices via Yahoo Finance (free, "
    "may lag real-time by a few minutes). DeepSeek analysis is for research "
    "reference only, not a buy/sell recommendation."
)
