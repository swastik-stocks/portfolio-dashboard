"""
seed_holdings.py — one-time load of your existing holdings.

Run once: python seed_holdings.py
Safe to re-run — it wipes and reloads the holdings table each time.
Works against whichever backend db.py is configured for (local file or
Turso) since it goes through db.get_conn(), not raw sqlite3.

Ticker symbols were confirmed via web search 2026-07-27 (several of
these are recent IPOs / renamed companies, so exact NSE symbol wasn't
guessable from the company name alone):
  - LG Electronics India     -> LGEINDIA.NS
  - SBI Funds Management     -> SBIFUNDS.NS
  - ICICI Prudential AMC     -> ICICIAMC.NS
  - SML Mahindra (ex-Isuzu)  -> SMLMAH.NS
  - Yatharth Hospital        -> YATHARTH.NS
  - Nippon India Silver ETF  -> SILVERBEES.NS

Account tags are placeholders (ACCOUNT_2 / ACCOUNT_3) since the exact
Yes Bank vs Dhan mapping wasn't confirmed — rename freely in the app,
this only affects the "Group by Account" view, not P&L math.
"""

from db import get_conn, init_db

# (symbol, company_name, account, qty, avg_price)
SEED_DATA = [
    # --- Axis Securities (from portfolio_holding_report xlsx, 27-Jul-2026) ---
    ("ANANTRAJ.NS",  "Anant Raj Ltd",                              "AXIS", 400,  658.50),
    ("BAJFINANCE.NS","Bajaj Finance Ltd",                          "AXIS", 190,  655.16),
    ("BDL.NS",       "Bharat Dynamics Ltd",                        "AXIS", 50,   1313.34),
    ("CGPOWER.NS",   "CG Power & Industrial Solutions Ltd",        "AXIS", 150,  749.84),
    ("DIVISLAB.NS",  "Divi's Laboratories Ltd",                    "AXIS", 20,   6752.15),
    ("HINDZINC.NS",  "Hindustan Zinc Ltd",                         "AXIS", 100,  651.50),
    ("ICICIAMC.NS",  "ICICI Prudential Asset Management Co Ltd",   "AXIS", 50,   2669.78),
    ("ITC.NS",       "ITC Ltd",                                    "AXIS", 200,  327.72),
    ("JIOFIN.NS",    "Jio Financial Services Ltd",                 "AXIS", 200,  240.71),
    ("SILVERBEES.NS","Nippon India Silver ETF",                    "AXIS", 1000, 229.94),
    ("RELIANCE.NS",  "Reliance Industries Ltd",                    "AXIS", 1,    1319.13),
    ("SBIFUNDS.NS",  "SBI Funds Management Ltd",                   "AXIS", 26,   574.00),
    ("SMLMAH.NS",    "SML Mahindra Ltd",                           "AXIS", 20,   4541.53),
    ("YATHARTH.NS",  "Yatharth Hospital & Trauma Care Services Ltd","AXIS", 200,  686.62),

    # --- From Portfolio.csv export (account TBD — rename in app) ---
    ("LGEINDIA.NS",  "LG Electronics India Ltd",                   "ACCOUNT_2", 12, 1140.00),
    ("SBIFUNDS.NS",  "SBI Funds Management Ltd",                   "ACCOUNT_2", 26, 574.00),

    # --- From app screenshot (account TBD — rename in app) ---
    ("POLYCAB.NS",   "Polycab India Ltd",                          "ACCOUNT_3", 4,  9956.78),
    ("RELIANCE.NS",  "Reliance Industries Ltd",                    "ACCOUNT_3", 1,  1296.58),
    ("SBIFUNDS.NS",  "SBI Funds Management Ltd",                   "ACCOUNT_3", 26, 574.00),
]


def seed():
    init_db()
    conn = get_conn()
    conn.execute("DELETE FROM holdings")
    for symbol, name, account, qty, avg_price in SEED_DATA:
        conn.execute("""
            INSERT INTO holdings (symbol, company_name, account, qty, avg_price, added_date, notes)
            VALUES (?, ?, ?, ?, ?, date('now'), 'seeded from initial import')
        """, (symbol, name, account, qty, avg_price))
    conn.commit()
    cursor = conn.execute("SELECT COUNT(*) FROM holdings")
    n = cursor.fetchone()[0]
    from db import _sync_if_remote
    _sync_if_remote(conn)
    conn.close()
    print(f"Seeded {n} lots across {len(set(r[2] for r in SEED_DATA))} accounts, "
          f"{len(set(r[0] for r in SEED_DATA))} unique symbols.")


if __name__ == "__main__":
    seed()
