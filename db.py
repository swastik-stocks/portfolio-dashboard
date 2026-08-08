"""
db.py — Portfolio Dashboard storage, local OR hosted (Turso).

CORRECTED VERSION 2: back to `libsql_client` (HTTP-based), not the
`libsql` embedded-replica package. History: this file was originally on
`libsql_client`, switched to `libsql` after a `WSServerHandshakeError`
when Turso deprecated the old websocket/hrana protocol during their infra
migration to AWS — but `libsql`'s embedded-replica `conn.sync()` started
hanging indefinitely in practice (confirmed not a network issue — the same
host responds to a plain HTTPS request in under a second). NSE Momentum's
turso_sync.py has used `libsql_client` successfully against this exact
database throughout, meaning the original websocket issue is resolved and
`libsql_client` is the healthier choice again. No embedded replica, no
local mirror file, no conn.sync() — every call is a direct HTTP request,
same tradeoff turso_sync.py already made deliberately (see that file's
docstring): fine for a UI making occasional queries, not trying to be a
high-throughput embedded DB.

Same design principle as before: ONE codebase, driven by env vars.
  TURSO_DATABASE_URL + TURSO_AUTH_TOKEN set -> Turso via libsql_client
  neither set -> plain local file via stdlib sqlite3, no sync

Each row is one LOT (one purchase, in one account) — NOT pre-consolidated.
Consolidation across accounts happens at query time in get_consolidated().

P1-05: get_scanner_signals() reads scanner_signals, published by
NSE Momentum's daily_scan.yml via turso_sync.py (P1-03). Lives in the
SAME Turso database as holdings — no new connection logic needed, just
another query against the existing get_conn().
"""

import os
import sqlite3
from pathlib import Path
from datetime import date

import libsql_client

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

TURSO_URL   = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

LOCAL_DB_PATH = Path(__file__).parent / "portfolio.db"


class _Result:
    """Normalized result shape both backends below return, so every query
    function elsewhere in this file is backend-agnostic."""
    def __init__(self, rows: list, last_insert_rowid=None):
        self.rows = rows                      # list[dict]
        self.last_insert_rowid = last_insert_rowid


class _LocalConn:
    """stdlib sqlite3, used when Turso isn't configured. Autocommits every
    statement — matches the Turso path's per-request semantics below, so
    callers never need to know which backend they're on."""
    def __init__(self, path):
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row

    def execute(self, sql: str, params=()):
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        rows = [dict(r) for r in cur.fetchall()]
        return _Result(rows, cur.lastrowid)

    def close(self):
        self._conn.close()


class _TursoConn:
    """libsql_client (HTTP), used when Turso is configured. libsql_client
    wants the http(s) scheme, not libsql:// — same fix-up turso_sync.get_client()
    already applies on the NSE Momentum side."""
    def __init__(self, url: str, token: str):
        https_url = url.replace("libsql://", "https://")
        self._client = libsql_client.create_client_sync(https_url, auth_token=token)

    def execute(self, sql: str, params=()):
        rs = self._client.execute(sql, list(params))
        rows = [dict(zip(rs.columns, row)) for row in rs.rows]
        return _Result(rows, rs.last_insert_rowid)

    def close(self):
        self._client.close()


def get_conn():
    if TURSO_URL:
        return _TursoConn(TURSO_URL, TURSO_TOKEN)
    return _LocalConn(LOCAL_DB_PATH)


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol        TEXT NOT NULL,
            company_name  TEXT,
            account       TEXT NOT NULL,
            qty           REAL NOT NULL,
            avg_price     REAL NOT NULL,
            added_date    TEXT,
            notes         TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_cache (
            symbol             TEXT PRIMARY KEY,
            verdict            TEXT,
            fundamental_score  REAL,
            news_sentiment     INTEGER,
            data_completeness  TEXT,
            computed_at        TEXT
        )
    """)
    _migrate_holdings_columns(conn)
    conn.close()


def _migrate_holdings_columns(conn):
    """
    P1-06: holdings didn't originally track HOW a symbol was resolved at
    entry time (see app.py's entry-time resolution flow). Extends the
    existing table rather than a new one — same check-before-ALTER pattern
    NSE Momentum's turso_sync.migrate_position_actions_columns() uses,
    since SQLite has no "ADD COLUMN IF NOT EXISTS". One of RESOLVED_EXACT /
    RESOLVED_FUZZY_ACCEPTED / UNVERIFIED_OVERRIDE / None (rows written
    before this migration).
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(holdings)").rows}
    if "resolution_status" not in existing:
        conn.execute("ALTER TABLE holdings ADD COLUMN resolution_status TEXT")

    # P1-08: signal_cache needs a real reason a symbol has no verdict yet,
    # replacing the single generic "(not run)" label that covered "never
    # refreshed", "fetch failed", and "no fundamentals data" identically.
    existing_sc = {row["name"] for row in conn.execute("PRAGMA table_info(signal_cache)").rows}
    if "reason_code" not in existing_sc:
        conn.execute("ALTER TABLE signal_cache ADD COLUMN reason_code TEXT")


def get_signal_cache() -> dict:
    """{symbol: {verdict, fundamental_score, news_sentiment, data_completeness, computed_at}}"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM signal_cache").rows
    conn.close()
    return {r["symbol"]: r for r in rows}


def save_signal_cache(symbol: str, verdict: str, fundamental_score: float,
                       news_sentiment: int, data_completeness: str, computed_at: str,
                       reason_code: str = "OK"):
    conn = get_conn()
    conn.execute("""
        INSERT INTO signal_cache (symbol, verdict, fundamental_score, news_sentiment, data_completeness, computed_at, reason_code)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            verdict=excluded.verdict,
            fundamental_score=excluded.fundamental_score,
            news_sentiment=excluded.news_sentiment,
            data_completeness=excluded.data_completeness,
            computed_at=excluded.computed_at,
            reason_code=excluded.reason_code
    """, (symbol, verdict, fundamental_score, news_sentiment, data_completeness, computed_at, reason_code))
    conn.close()


def add_holding(symbol: str, company_name: str, account: str, qty: float,
                 avg_price: float, notes: str = "", resolution_status: str = None) -> int:
    conn = get_conn()
    result = conn.execute("""
        INSERT INTO holdings (symbol, company_name, account, qty, avg_price, added_date, notes, resolution_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol.strip().upper(), company_name.strip(), account.strip(),
          qty, avg_price, date.today().isoformat(), notes, resolution_status))
    conn.close()
    return result.last_insert_rowid


def update_holding(holding_id: int, symbol: str, company_name: str, account: str,
                    qty: float, avg_price: float, notes: str = "", resolution_status: str = None):
    conn = get_conn()
    conn.execute("""
        UPDATE holdings
        SET symbol=?, company_name=?, account=?, qty=?, avg_price=?, notes=?, resolution_status=?
        WHERE id=?
    """, (symbol.strip().upper(), company_name.strip(), account.strip(),
          qty, avg_price, notes, resolution_status, holding_id))
    conn.close()


def delete_holding(holding_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM holdings WHERE id=?", (holding_id,))
    conn.close()


def get_all_lots() -> list:
    conn = get_conn()
    result = conn.execute("SELECT * FROM holdings ORDER BY symbol, account").rows
    conn.close()
    return result


def get_accounts() -> list:
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT account FROM holdings ORDER BY account").rows
    conn.close()
    return [r["account"] for r in rows]


def get_consolidated() -> list:
    """
    Groups all lots by symbol across ALL accounts: sums quantity, computes
    a quantity-weighted average purchase price.
    """
    lots = get_all_lots()
    by_symbol = {}
    for lot in lots:
        sym = lot["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = {
                "symbol": sym,
                "company_name": lot["company_name"],
                "qty": 0.0,
                "total_invested": 0.0,
                "accounts": [],
                # P1-08: carry resolution_status through so the dashboard can
                # flag a symbol that was saved unverified, independent of
                # whatever the signal_cache reason_code says. If lots of the
                # same symbol somehow have different statuses (shouldn't
                # normally happen — same symbol implies same resolution),
                # the first lot's value wins; consolidated view doesn't need
                # per-lot granularity here.
                "resolution_status": lot.get("resolution_status"),
            }
        entry = by_symbol[sym]
        entry["qty"] += lot["qty"]
        entry["total_invested"] += lot["qty"] * lot["avg_price"]
        entry["accounts"].append(lot["account"])

    result = []
    for sym, entry in by_symbol.items():
        weighted_avg = entry["total_invested"] / entry["qty"] if entry["qty"] else 0
        result.append({
            "symbol": sym,
            "company_name": entry["company_name"],
            "qty": entry["qty"],
            "avg_price": weighted_avg,
            "total_invested": entry["total_invested"],
            "accounts": sorted(set(entry["accounts"])),
            "num_accounts": len(set(entry["accounts"])),
            "resolution_status": entry["resolution_status"],
        })
    return sorted(result, key=lambda x: x["symbol"])


def get_scanner_signals(scan_date: str = None) -> list:
    """
    P1-05: read scanner signals published by NSE Momentum's evening scan.
    Defaults to the most recent scan_date present in the table (there may
    be a gap of days if the scan hasn't run — e.g. weekends — so "most
    recent" is deliberately not "today"). Returns [] gracefully if the
    table doesn't exist yet or the read fails for any reason — a bridge
    read failure must never break the dashboard (same principle as
    P1-06 on the NSE Momentum side).
    """
    try:
        conn = get_conn()
        if scan_date:
            rows = conn.execute(
                "SELECT * FROM scanner_signals WHERE scan_date = ? ORDER BY total_score DESC",
                (scan_date,)
            ).rows
        else:
            rows = conn.execute("""
                SELECT * FROM scanner_signals
                WHERE scan_date = (SELECT MAX(scan_date) FROM scanner_signals)
                ORDER BY total_score DESC
            """).rows
        conn.close()
        return rows
    except Exception:
        return []


def get_sector_breadth(breadth_date: str = None) -> list:
    """
    P3-02: read sector breadth published by NSE Momentum's sector_breadth.py
    (P3-01), using the SAME Turso database as everything else here — no new
    connection logic. Defaults to the most recent breadth_date present.
    Returns [] gracefully on any failure, same principle as
    get_scanner_signals() above (a bridge read must never break the
    dashboard) and P1-06 on the NSE Momentum side.
    """
    try:
        conn = get_conn()
        if breadth_date:
            rows = conn.execute(
                "SELECT * FROM sector_breadth WHERE breadth_date = ? ORDER BY pct_above_sma50 DESC",
                (breadth_date,)
            ).rows
        else:
            rows = conn.execute("""
                SELECT * FROM sector_breadth
                WHERE breadth_date = (SELECT MAX(breadth_date) FROM sector_breadth)
                ORDER BY pct_above_sma50 DESC
            """).rows
        conn.close()
        return rows
    except Exception:
        return []


def get_industry_breadth(breadth_date: str = None) -> list:
    """
    P3-04: read industry breadth published by NSE Momentum's industry_breadth.py,
    same Turso database, same pattern as get_sector_breadth() above — just the
    138-industry granularity table instead of the 20-sector one. Defaults to
    the most recent breadth_date. Returns [] gracefully on any failure — a
    missing industry_breadth table (e.g. before industry_breadth.py has run
    for the first time) must never break the dashboard.
    """
    try:
        conn = get_conn()
        if breadth_date:
            rows = conn.execute(
                "SELECT * FROM industry_breadth WHERE breadth_date = ? ORDER BY pct_above_sma50 DESC",
                (breadth_date,)
            ).rows
        else:
            rows = conn.execute("""
                SELECT * FROM industry_breadth
                WHERE breadth_date = (SELECT MAX(breadth_date) FROM industry_breadth)
                ORDER BY pct_above_sma50 DESC
            """).rows
        conn.close()
        return rows
    except Exception:
        return []


def get_ticker_sector_map() -> dict:
    """
    P3-08: read the ticker->sector mapping published by NSE Momentum's
    sector_breadth.py, so holdings can be cross-referenced against sector
    breadth without this repo needing its own copy of nse_universe.py or
    the NSE constituent CSV. {ticker: sector}. Returns {} gracefully on any
    failure -- same principle as every other bridge read in this file.
    """
    try:
        conn = get_conn()
        rows = conn.execute("SELECT ticker, sector FROM ticker_sector_map").rows
        conn.close()
        return {r["ticker"]: r["sector"] for r in rows}
    except Exception:
        return {}


def get_holding_stops() -> dict:
    """
    P4-03: read the most recent technical stop per held ticker, published
    by NSE Momentum's publish_holding_stops() (turso_sync.py). Returns
    {ticker: {stop, method, action_date}} for dashboard Portfolio Heat
    rendering. Returns {} gracefully on any failure.
    """
    try:
        conn = get_conn()
        rows = conn.execute("""
            SELECT ticker, new_stop, reason, action_date
            FROM position_actions
            WHERE action_type = 'HOLD_STOP'
            AND action_date = (
                SELECT MAX(action_date) FROM position_actions
                WHERE action_type = 'HOLD_STOP'
            )
        """).rows
        conn.close()
        result = {}
        for row in rows:
            result[row['ticker']] = {
                'stop': row['new_stop'],
                'method': row.get('reason', ''),
                'action_date': row['action_date'],
            }
        return result
    except Exception:
        return {}


if __name__ == "__main__":
    init_db()
    mode = "Turso (libsql_client)" if TURSO_URL else f"local file ({LOCAL_DB_PATH})"
    print(f"Initialized DB in {mode} mode")
