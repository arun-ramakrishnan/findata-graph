# Unsigned duckdb extensions — scope note

Date: 2026-11-04 (range-review followup R2)

`allow_unsigned_extensions=True` is required to load the vendored onager
build (`vendor/onager/`). It is a connect-time config in duckdb 2.0 and
cannot be revoked on an open connection, so the surface was narrowed from
"any con built by `helpers/core/duckdb_shared.py`" to the three sites that
genuinely load unsigned extensions:

1. `helpers/graph/onager.py` `_get_onager_connection` — onager's own
   in-memory materialization con (`LOAD sqlite` + `LOAD onager`).
2. `helpers/graph/query.py` file-mode graph build — `duckdb.connect` with
   `onager.connect_kwargs()` directly (never via `connect_shared`).
3. `helpers/maintenance/quack_server_child.py` — the wire server's own con
   must carry the flag or every onager lane arriving over the wire
   degrades to the scipy fallback.

`connect_shared` (the shared router used by all index/query/read lanes)
connects PLAIN since 2026-11-04: no caller loads onager over a shared con.
Bench lanes install the signed distribution onager (`INSTALL onager` from
the community repo — see `vendor/onager/PROVENANCE.md`, truth corrected
2026-10-24) and never needed the flag.

Threat model: an unsigned extension is arbitrary native code; a con with
the flag lets any code path that reaches `LOAD` execute it. Keeping the
flag off the shared router means a future caller cannot pick it up by
accident — it must import `onager.connect_kwargs()` (or
`duckdb_shared.unsigned_ext_kwargs()`) explicitly, which review can see.
Pin: `tests/test_duckdb_shared.py::TestConnectSharedUnsigned`.
