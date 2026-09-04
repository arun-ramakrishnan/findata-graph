---
title: "helpers/ de-dup — env.REPO_ROOT + db.connect adoption, utc_today_iso, fold _compute_root/_connect_ro/_now_utc"
status: proposed
filed: "2026-09-03"
executed: null
completed_md: null
area: "helpers/core (env.py, db.py), helpers/maintenance (snapshot_db, db_maint, enrich_relations, enrich_from_yfinance, maint), helpers/misc (git_secret_scan), helpers/validators (static_checks), helpers/graph (query, stats, embeddings, algorithms)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# helpers/ de-dup — adopt the shared repo-root, connect, and timestamp helpers

**Date:** 2026-09-03 · **Status:** PROPOSED ·
**Area:** `helpers/` across core/maintenance/misc/validators/graph ·
disposition index for the `env.REPO_ROOT` / `db.connect` / timestamp
adoptions that #196 W8 left partially done

## 1. Motivation

Roll \#196 (W8) exported `env.REPO_ROOT`, adopted it in `db.py`, and **explicitly
pinned** `db.utc_now()` at the enrich call sites as an output-bytes deviation
(since reversed by the separate archived `doc/improvements/archive/tooling/utc_now_unification.md` proposal, completed.md #199). The
2026-09-03 consolidation survey re-checked the whole `helpers/` tree and
found the *other* shared-helper adoptions were never finished: local
`PROJECT_ROOT`/`_compute_root` re-derivations still exist in ~18 files,
raw `sqlite3.connect()` calls still bypass `db.connect()`, and the same
timestamp expression is re-implemented at many sites while
`frontmatter.iso_now_utc()` sits unused.

Unlike the reversals in the archived `doc/improvements/archive/tooling/utc_now_unification.md` (completed.md #199, which pinned display/vault
timestamps as intentional deviations), the rows below are **shape-compatible
adoptions** — they swap a local copy for the identical shared helper with
zero output change, or they drop a raw connection in favor of the canonical
one.

## 2. Census (measured 2026-09-03) and disposition

### 2.1 Repo-root re-derivation → `env.REPO_ROOT`

| Site | Current | Disposition |
|---|---|---|
| `snapshot_db.py:89-91` | `def _compute_root()` = `Path(__file__).resolve().parents[2]` | **ADOPT** `env.REPO_ROOT` |
| `db_maint.py:678-680` | `def _compute_root()` (identical) | **ADOPT** `env.REPO_ROOT` |

*Note:* the other ~16 local `PROJECT_ROOT = Path(__file__).resolve().parents[2]`
definitions (maint, snapshot_db, build_sector_hierarchy, rename_entity,
move_sector, migrate_to_graph_edges, sync_sector_wikilinks,
enrich_from_yfinance, rebuild_schema, git_secret_scan, parse_newsletter,
get_tickers, stats, embeddings, algorithms, query) are **script entry
points** — their sys.path bootstrap runs before `helpers` is importable
(chicken-and-egg). #196 already recorded this as a pinned deviation. This
proposal does NOT sweep those; it only folds the two `_compute_root()`
defs. **Placement constraint (verified 2026-09-03):** snapshot_db and
db_maint are ALSO script entry points (bootstrap at snapshot_db:77-78 /
db_maint:86-87) whose `helpers.*` references are all *lazy
function-level* imports — there are NO module-level helpers imports today.
The `from helpers.core.env import REPO_ROOT` statement must therefore sit
*after* each file's sys.path bootstrap block (module-level imports are
legal anywhere; the bootstrap stays, per the #196 pin). `env.REPO_ROOT`
resolves to the same directory `_compute_root()` computes (`parents[2]`
of a file in the same checkout — cwd-independent).

### 2.2 Raw `sqlite3.connect()` → `db.connect()`

| Site | What | Disposition |
|---|---|---|
| `snapshot_db.py:94-106` `_connect_ro()` | reimplements `db.connect(read_only=True)` (`mode=ro` URI) | **ADOPT** `db.connect(..., read_only=True)` (call sites 118, 175, 683, 775) |
| `snapshot_db.py:119` `create_snapshot()` | raw writable conn = backup DEST tmp file | **ADOPT** `db.connect(tmp_path, wal=False)` — WAL on a fresh empty dest breaks the backup API (see §6) |
| `snapshot_db.py:154` `verify_snapshot()` | raw `sqlite3.connect(str(tmp_path))` for integrity verify | **ADOPT** `db.connect(tmp_path)` |
| `snapshot_db.py:856` `restore` | raw writable conn for the `.restore-tmp` target (`executescript` DDL) | **ADOPT** `db.connect(tmp)` |
| `static_checks.py:889` | raw `sqlite3.connect(str(db))` for `db_meta`/`PRAGMA user_version` read | **ADOPT** `db.connect(db, read_only=True)` |
| `db_maint.py:293,339-340,435,746` | backup src/dest pairs + `isolation_level=None` conns (VACUUM/checkpoint autocommit) | **PIN** — `db.connect` exposes no `isolation_level`; file stays allowlisted |

All ADOPT rows are covered by `db.py`'s module docstring mandate ("every
DB-touching module should go through `connect()` here").

**P0 allowlist interaction (gate consistency, same change):**
`static_checks.py:834` (`check_sqlite_helper_usage`) allowlists the
snapshot_db and db_maint FILES, and its docstring counts "snapshot_db
(temp-file backup/verify — 4 sites)" (accurate: raw connects at 106, 119,
154, 856) and "db_maint (backup + VACUUM isolation_level=None — 2
sites)" (STALE — db_maint now has 5 connect calls: 293, 339, 340, 435,
746; the file-level allowlist hid the drift). After this proposal,
snapshot_db has ZERO raw connects → remove it from `allowlist_prefixes`,
and recount db_maint as 5 sites (backup pairs + isolation) in the
docstring so the gate's documentation stops drifting.

### 2.3 Timestamp re-implementations → shared helper

| Site | Current | Disposition |
|---|---|---|
| `enrich_relations.py:393,704,806,1372,1499` (×5) | `today = datetime.now(UTC).date().isoformat()` | **ADOPT** new `db.utc_today_iso()` |

No conflict with #199's pin: #199 pinned the *format* (don't change the
bytes); this swaps the *expression* for a helper producing the identical
`YYYY-MM-DD` string — zero output change. The `:994` `coinfer/…` inline
site stays pinned (path identity).
| `git_secret_scan.py:62-63` `_now_utc()` | = `frontmatter.iso_now_utc()` byte-for-byte (`strftime("%Y-%m-%dT%H:%M:%SZ")`) | **ADOPT** import `frontmatter.iso_now_utc` |

### 2.4 Explicitly pinned — do NOT touch

| Site | Why pinned |
|---|---|
| `enrich_from_yfinance.py:280` `Refreshed: {today}` | local date, operator-facing display line — documented deviation in archived `doc/improvements/archive/tooling/utc_now_unification.md` (#199) |
| all `isoformat(timespec="seconds")` display/report/vault timestamps | pinned in archived `doc/improvements/archive/tooling/utc_now_unification.md` (#199: display/stale/header/frontmatter) |

These are **not** re-litigated here; the rows below them (§5) stay.

## 3. Design

- Add **`db.utc_today_iso()`** (`datetime.now(UTC).date().isoformat()`,
  date-only UTC), sibling of `db.utc_now()`, documented for
  path-identity / date-label contexts that want the shared-shape, not a
  full datetime. Replace the 5 copies in `enrich_relations.py`.
- `snapshot_db`/`db_maint`: replace the two `_compute_root()` defs with
  `from helpers.core.env import REPO_ROOT` placed *after* each file's
  sys.path bootstrap (both are script entry points with lazy-only
  `helpers.*` imports today — see the §2.1 placement constraint).
- `snapshot_db`: drop `_connect_ro()`; call `db.connect(path, read_only=True)`
  at its 4 sites, `db.connect(tmp_path, wal=False)` at the :119 backup
  dest (WAL breaks the backup API — §6), and plain `db.connect(tmp)` at
  the :856 restore tmp (executescript path, explicitly sets
  `journal_mode=OFF` after). Confirm `db.connect`'s pragma
  application is benign for read-only verifier use (it is — pragmas are
  per-connection and read-only tolerant; the `read_only` docstring records
  that `wal` is ignored under RO).
- `snapshot_db.verify_snapshot` + `static_checks`: swap raw
  `sqlite3.connect` for `db.connect(..., read_only=True)`.
- **static_checks P0 gate** (same change): remove `snapshot_db.py` from
  `check_sqlite_helper_usage`'s `allowlist_prefixes` (its raw connects
  are now zero) and recount db_maint's sites in the docstring (2 → 5 —
  the file-level allowlist had hidden the drift). db_maint itself stays
  allowlisted (isolation_level=None, §2.2 PIN).
- `git_secret_scan`: replace `_now_utc()` with the shared
  `frontmatter.iso_now_utc()` import — done LAZILY inside the function
  (the script runs standalone via `make secret-scan` with no sys.path
  bootstrap, and frontmatter pulls yaml; import time stays stdlib-only).
- `tests/test_snapshot_db.py` + `tests/test_db_maint.py` imported the
  deleted `_compute_root` defs — rewired to `helpers.core.env.REPO_ROOT`
  (same worktree-agnostic assertions, renamed tests).

## 4. Non-goals

- **Not** sweeping the 16 script-entry-point `PROJECT_ROOT` definitions
  (chicken-and-egg, pinned in #196). Recorded for a future packaging
  change that makes `helpers` importable pre-bootstrap.
- **Not** adopting `utc_now()` at the display/report/vault timestamps —
  that is governed by archived `doc/improvements/archive/tooling/utc_now_unification.md` (#199).
- **Not** converting the `datetime.now()` local-time producers in
  `maint.py:338`, `get_tickers.py:695`, `verify_notes.py:893` to UTC —
  those are display/metadata lines whose local-vs-UTC semantic is a policy
  decision, separate from duplication (flagged, deferred).
- **No** shared argparse/`strftime` registry — consistent with #196 §4.

## 5. Gates

- `ruff` on all touched files; targeted pytest for
  `enrich_relations`, `snapshot_db`, `db_maint`, `static_checks` /
  `database_integrity_check`-adjacent modules, and `git_secret_scan`.
- `rg 'def _compute_root'` → 0; `rg 'def _connect_ro'` → 0;
  `rg 'sqlite3\.connect' helpers/maintenance/snapshot_db.py` → 0;
  `rg "datetime.now\(UTC\).date\(\).isoformat"` in
  `helpers/` only at intentional sites (dedupe count: 5 → 0 in
  `enrich_relations.py`).
- `make qa` once at arc end.

## 6. Risks

- **Behavioral**: `db.connect(read_only=True)` adds pragmas (FK on,
  busy_timeout, cache/mmap) that the raw `mode=ro` open lacked — WAL is
  explicitly skipped under RO (`db.py:145 if wal and not read_only`).
  FK/busy_timeout are legal per-connection on RO; cache/mmap pragmas are
  tolerant of read-only (they're per-connection advisory, try-wrapped).
  The one REAL breakage found at execution is on the **dest** side, not
  the source: `PRAGMA journal_mode=WAL` on the fresh empty backup-dest
  file makes `src.backup(dest)` fail with "attempt to write a readonly
  database" (bisected pragma-by-pragma 2026-09-03; raw+raw works, WAL-dest
  fails). Mitigation in place: the backup dest uses
  `db.connect(tmp_path, wal=False)`. All other `db.connect` pragmas are
  backup-safe. Low residual risk.
- **`utc_today_iso` output**: identical to the string the 5 sites already
  produce (`YYYY-MM-DD`) — zero byte change.
- **`_compute_root` removal**: both files reference the function only
  post-import; replacing with a module-level `REPO_ROOT` import is
  safe and fails loudly at import if wrong.

## 7. Deferred (record, don't do)

- Script-entry-point `PROJECT_ROOT` sweep — needs a packaging change
  (#196 note).
- Local-vs-UTC display timestamps (`maint.py:338`, `get_tickers.py:695`,
  `verify_notes.py:893`) — policy, not duplication.
