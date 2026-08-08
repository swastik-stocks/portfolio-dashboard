# Next Steps — P1 Symbol Resolver follow-ups (Portfolio Dashboard side)

Tracking doc for what's left after the P1 symbol resolver work (2026-08-08).
See `symbol_check.py`, `db.py`'s `_migrate_holdings_columns`, and `app.py`'s
sidebar entry-time resolution flow for what shipped. Companion doc in
`F:\nse_momentum\NEXT_STEPS.md` covers the nse_momentum-side items
(scan-run confirmation).

## Pending

1. **Delete the `libsql` line from `requirements.txt`'s installed venv**
   if you ever prune dependencies — it's no longer imported anywhere, only
   removed from `requirements.txt`, not uninstalled from `venv/`.

2. **Not this repo's fix, but affects a feature here**: nse_momentum's
   `turso_sync.publish_holding_stops()` is defined but never called
   anywhere — dead code. It's what's supposed to feed this app's
   `get_holding_stops()` / "Portfolio Heat" feature via `position_actions`
   `HOLD_STOP` rows, so that feature has never had real data. Tracked in
   nse_momentum's `NEXT_STEPS.md`; needs a decision there on whether to
   wire it in or remove it.

## Done (2026-08-08)

- **Cosmetic `company_name` fix** — the two backfilled holdings (id 22, 23)
  now show their real NSE names in the shared Turso `holdings` table.

- **`libsql` → `libsql_client` migration** — `db.py` rewritten behind a
  small `_LocalConn`/`_TursoConn` adapter (stdlib `sqlite3` for local-only
  mode, `libsql_client` for Turso — same HTTP-based client nse_momentum's
  `turso_sync.py` already uses successfully). Fixes the indefinite
  `conn.sync()` hang from the old `libsql` embedded-replica client — every
  read/write function verified working and instant against production
  Turso (previously hung 10+ minutes with no error). `TURSO_DATABASE_URL`/
  `TURSO_AUTH_TOKEN` re-enabled in `.env`. Stale `portfolio_replica.db*`
  files (no longer used) deleted.

- `symbol_check.py` — entry-time resolution against `nse_symbol_master`
  (published nightly by nse_momentum into the shared Turso DB).
- `db.py` — `resolution_status` column on `holdings`, `reason_code` column
  on `signal_cache` (both idempotent migrations).
- `app.py` — sidebar entry form now resolves symbols before saving: exact
  matches save silently, fuzzy matches prompt "did you mean X?", no match
  requires an explicit "I've verified this manually" checkbox before
  saving. Real reason codes (`NOT_YET_REFRESHED`/`NO_DATA`/`FETCH_ERROR`)
  replace the old generic "(not run)" signal label.
- `quick_signal.py` — `compute_quick_signal()` is now exception-safe. Found
  and fixed a real bug along the way: it previously had no try/except, so
  one bad symbol during "Refresh Signals" crashed the whole batch and left
  every symbol after it in iteration order stuck at "(not run)" too, not
  just the one that actually failed.
