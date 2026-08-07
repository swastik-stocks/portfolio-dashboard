"""
symbol_check.py — P1-06

Entry-time symbol resolution against nse_symbol_master, the table NSE
Momentum's publish_symbol_master.py refreshes nightly in the same shared
Turso database this app already reads via db.get_conn(). Deliberately does
NOT import NSE Momentum's symbol_resolver.py — these are separate repos/
deployments with no shared import path (same reasoning as every other
duplicated-by-necessity constant/helper between the two apps), and this
only needs a lightweight local fuzzy match over rows already in the shared
DB, not the CSV-download/cache machinery symbol_resolver.py owns.

Matching vocabulary mirrors NSE Momentum's (EXACT_SYMBOL/FUZZY_NAME/
UNRESOLVED, minus ISIN — holdings entry here has no ISIN field to match on)
so the two apps' resolution semantics stay conceptually aligned even though
the code doesn't share a module.
"""

import difflib

from db import get_conn

FUZZY_CUTOFF = 0.75
AMBIGUITY_MARGIN = 0.08


def resolve_against_master(raw_text: str) -> dict:
    """
    Resolve typed symbol/company-name text against nse_symbol_master.

    Returns {"status", "symbol", "company_name"} where status is one of:
      EXACT_SYMBOL   — raw_text is already a real NSE symbol
      FUZZY_NAME     — best-effort company-name match, not a guaranteed match
      AMBIGUOUS_FUZZY — top match too close to a runner-up to trust automatically
      UNRESOLVED     — no match found
      UNAVAILABLE    — nse_symbol_master unreachable/empty (fail open: caller
                        should let the user save rather than block on our own
                        infra issue)
    """
    text = (raw_text or "").strip().upper()
    if not text:
        return {"status": "UNRESOLVED", "symbol": None, "company_name": None}

    try:
        conn = get_conn()
        cur = conn.execute("SELECT symbol, company_name FROM nse_symbol_master WHERE symbol = ?", (text,))
        row = cur.fetchone()
        if row:
            conn.close()
            return {"status": "EXACT_SYMBOL", "symbol": row[0], "company_name": row[1]}

        cur = conn.execute("SELECT symbol, company_name FROM nse_symbol_master WHERE company_name IS NOT NULL")
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return {"status": "UNAVAILABLE", "symbol": None, "company_name": None}

    if not rows:
        return {"status": "UNAVAILABLE", "symbol": None, "company_name": None}

    # Case-insensitive fuzzy match — the master's names are title case
    # ('Aster DM Quality Care Limited'), typed text is upper (this form's
    # own convention) — see NSE Momentum's symbol_resolver.build_upper_index
    # for why matching on raw case silently fails almost every real case.
    upper_index = {}
    for symbol, name in rows:
        key = name.upper()
        if key not in upper_index:
            upper_index[key] = (name, symbol)

    candidates = difflib.get_close_matches(text, list(upper_index.keys()), n=2, cutoff=FUZZY_CUTOFF)
    if not candidates:
        return {"status": "UNRESOLVED", "symbol": None, "company_name": None}

    top_name, top_symbol = upper_index[candidates[0]]
    if len(candidates) == 2:
        top_ratio = difflib.SequenceMatcher(None, text, candidates[0]).ratio()
        runner_ratio = difflib.SequenceMatcher(None, text, candidates[1]).ratio()
        if (top_ratio - runner_ratio) <= AMBIGUITY_MARGIN:
            return {"status": "AMBIGUOUS_FUZZY", "symbol": top_symbol, "company_name": top_name}

    return {"status": "FUZZY_NAME", "symbol": top_symbol, "company_name": top_name}
