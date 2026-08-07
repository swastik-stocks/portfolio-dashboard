# Next Steps — P1 Symbol Resolver follow-ups (Portfolio Dashboard side)

Tracking doc for what's left after the P1 symbol resolver work (2026-08-08).
See `symbol_check.py`, `db.py`'s `_migrate_holdings_columns`, and `app.py`'s
sidebar entry-time resolution flow for what shipped. Companion doc in
`F:\nse_momentum\NEXT_STEPS.md` covers the nse_momentum-side items
(scan-run confirmation).

## Pending

1. **Migrate `db.py` from `libsql` to `libsql_client`** — `libsql`'s
   embedded-replica `conn.sync()` hangs indefinitely in this environment
   (confirmed twice, 10+ minutes, no error). Not a network issue — `curl`
   reaches the same Turso host in under a second. This file's own docstring
   explains it was switched TO `libsql` FROM `libsql_client` after a past
   `WSServerHandshakeError` when Turso deprecated the old websocket/hrana
   protocol — but nse_momentum's `turso_sync.py` uses `libsql_client`
   successfully against this exact same database throughout the session
   that diagnosed this, suggesting that old issue is resolved and
   `libsql_client` is now the healthier choice. Real rewrite needed
   (different cursor/commit semantics than `libsql`'s), not a one-liner —
   touches every function in this file.

2. **Re-enable Turso in `.env`** — `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN`
   are currently commented out as an immediate unblock so the app loads
   past login. Until item 1 is done, this runs on a stale local
   `portfolio.db` (dated 27 Jul — missing MANAPPURAM/ASTERDM/SAILIFE, has
   since-removed holdings like DIVISLAB/SMLMAH) and the new entry-time
   resolution UI can't be meaningfully tested (the local fallback DB has no
   `nse_symbol_master` table, so `symbol_check.resolve_against_master()`
   fails open and skips validation silently).

3. **Optional — cosmetic**: `company_name` for the two backfilled holdings
   in Turso's `holdings` table (id 22, 23) still reads "ASTER DM QUALITY
   CARE" / "SAI LIFE SCIENCES" instead of their real NSE names ("Aster DM
   Quality Care Limited" / "Sai Life Sciences Limited"). Display-only field,
   never affected anything functionally — low priority.

## Done (2026-08-08)

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
