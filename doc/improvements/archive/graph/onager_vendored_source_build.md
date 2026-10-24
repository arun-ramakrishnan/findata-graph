---
title: "Onager reinstatement — vendored source build, dispatch rewiring, v_centrality re-stamp"
status: executed
filed: "2026-10-24"
area: "helpers/graph"
executed: "2026-10-24"
completed_md: "434"
---

# Onager reinstatement — vendored source build, dispatch rewiring, v_centrality re-stamp

**Date:** 2026-10-24 · **Status:** EXECUTED — archived 2026-10-24 (completed.md #434)

## 1. TL;DR

The duckdb 1.5.6 → 2.0.0 LTS upgrade (completed.md #427) removed the
onager community extension from the box: `INSTALL onager` under 2.0.0
returns HTTP 404 from the main repository, and every graph-algorithm
family silently demoted to the scipy/numpy/igraph/pure-Python fallback
chain (735e10e60). This proposal reinstates the onager lane end-to-end:
a vendored source build of upstream v0.5.0 — whose HEAD commit IS the
2.0.0 port — against the exact v2.0.0 duckdb; a connect-time
`allow_unsigned_extensions` contract (duckdb 2.0 locks the setting once
a database is open); and the decisive discovery that the fallback
commit never wired its own dispatch — `graph_fallbacks._dispatch` had
zero production callers, so production read paths called the pure
fallback implementations even when an extension was loadable. With
`_extension_first` routing the ten `_onager_central` families and the
link-prediction call site, link prediction returned to the onager
shape (**2.884 s** vs 6.15 s fallback; onager era 2.5-2.7 s) and
route_graph_stats to **2.787 s** (was 4.98 s). The same arc re-stamped
the ten `v_centrality_*`/`v_graph_structure` tables dropped at the 2.0
rebuild (operator directive). Budgets stay at their 10-23 re-baseline
(LP 8.0 / route 7.5) pending the operator's quiet-box gate arc.

## 2. Evidence

**The break window (perf gate corpus):** graph_link_prediction
2.3-2.6 s (onager) → 39.8-47.5 s (naive fallback, runs 1787/1801) →
6.15 s (vectorised, e3147ef3c). Cheap centrality legs +40-70%
(pagerank 0.79→1.23 s, eigenvector 0.76→1.39 s) — within budget but
permanent while the extension was gone. v_centrality tables absent
from the live store since the 2.0 rebuild (wire census: 0 of 10).

**Upstream state (verified 2026-10-24):**

- onager v0.5.0 @ `c45146bc4b259cfcbd32ddace34eb8fc112e061d`
  (2026-10-23) — commit message: "Update the project to build after
  the DuckDB 2.0.0's API changes (#32)". The port EXISTS upstream;
  only the bucket artifact was missing.
- `external/duckdb` submodule == **exactly the v2.0.0 tag**
  (`d54cbbe3df17e8e5ce68312f483b4155836112aa`), and upstream's own
  `dist_pipeline.yml` builds `duckdb_version: v2.0.0`.
- Main-repo `INSTALL onager` still 404s (clean-room re-verified); the
  **community repository serves the artifact**
  (`http://community-extensions.duckdb.org/v2.0.0/linux_amd64/...` →
  HTTP 200) — a trusted copy reached this box via the standalone
  duckdb CLI (operator-managed `~/.duckdb`); the venv's duckdb-py
  resolves the same extensions dir through `$HOME` (proven: flagless
  `LOAD onager` succeeds there, fails under an empty fake HOME).

**Build traps (hit and fixed, recorded in `vendor/onager/PROVENANCE.md`):**

1. **DUCKDB_VERSION stamp** — a default-configured duckdb source build
   stamps the commit hash as its version string; the loader refuses the
   artifact ("built specifically for DuckDB version 'd54cbbe3df' …
   (this version of DuckDB is 'v2.0.0')"). Fix: `DUCKDB_VERSION=v2.0.0`
   exported over `make release`.
2. **Connect-time unsigned flag** — post-connect `SET
   allow_unsigned_extensions` dies with "Cannot change … while database
   is running" on duckdb 2.0. Fix: `unsigned_ext_kwargs()`
   (`helpers/core/duckdb_shared.py`) / `connect_kwargs()`
   (`helpers/graph/onager.py`) threaded through every factory that may
   serve onager lanes: engine `_prepare`, both availability probes,
   `query.py` file-mode connect + fingerprint check lane, the quack
   server child, and `connect_shared` (wire fallback untouched — the
   server-side connection carries the flag instead).
3. **Trust is location-based** — a copy under the duckdb extensions dir
   loads flaglessly; the same bytes loaded BY PATH require the flag.
   The degradation tests isolate arms with an empty fake HOME.

**The dispatch gap (the decisive find):** 735e10e60 exported fallback
twins with onager-identical signatures AND wrote `_dispatch` to prefer
the extension — but imported the fb.* functions directly into
`algorithms.py` call sites. Even with an extension loadable, production
computed on igraph/scipy forever. Fix: `algorithms._extension_first(fn, …)`
resolves `onager.<fn.__name__>` when `onager_available()`, degrades per
call on `OnagerUnavailable`/onager-flavoured duckdb errors (latching the
probe off), and propagates anything else. `_LANE_SERVED_FAMILIES`
(closeness/harmonic/betweenness) stay on their scipy/L1B lanes outright
— the same served-universe contract that keeps the stamp from forking
values.

## 3. Slices

- **S1 — vendored build** — `vendor/onager/build.sh` (pinned fetch +
  submodule SHAs asserted == release config, rust core → libonager.a →
  cmake loadable extension, `DUCKDB_VERSION=v2.0.0`) +
  `vendor/onager/PROVENANCE.md` (pin table, sha256, LOAD verdict,
  build traps, re-pin procedure) + Makefile `onager-build` target
  (help-sync verified by TestMakefileHelpCompleteness). Artifact:
  `models/onager/bin/onager.duckdb_extension`
  sha256 `db2f1e87…8895f` (gitignored, GGUF convention).
- **S2 — wire + probes** — `onager._load_onager` resolution order
  (distribution `LOAD onager` → vendored path-load on a flagged con →
  `OnagerUnavailable`), `connect_kwargs()`/`unsigned_ext_kwargs()`
  threaded through nine factories, `graph_fallbacks.onager_available()`
  and conftest `_onager_available()` rerouted through the same resolver
  (the conftest change un-skips the 64-test capability battery
  automatically when an artifact exists).
- **S3 — dispatch rewiring** — `algorithms._extension_first` +
  `_LANE_SERVED_FAMILIES`, wired into `_onager_central` (ten families)
  and the `link_prediction` read path.
- **S4 — v_centrality re-stamp** (operator directive: "bring back the
  dropped tables") — `make quack-stop` → `make stamp-centrality`
  (swap-rebuild + stamp; lane-served metrics skipped per ROUTING) →
  `make quack-start`. All ten tables verified over the wire (degree
  20,046 rows; `_build_meta` louvain_modularity 0.7217).
- **S5 — perf-report failure attribution** (rode the same session,
  filed here for the record) — `tests/run_perf_benchmarks.py`
  `_load_pressure_reason()` + `_md_status()`: failure rows now carry
  `✗ FAIL(budget|rc[, frag])` with a measured-pressure fragment
  (`psi avg10 N%` / `load1 N.NN` / `lane qa`), so a loaded box's red
  wall (run 1787: 15 legs) reads as load, not regression. The literal
  `FAIL` substring is invariant (gate_query/search_tui key on it).

## 4. Validation

- Capability battery: `test_onager_capabilities.py` +
  `test_centrality_projection.py` **79/79** (first run since 10-21).
- Fallback contract: `test_graph_fallbacks.py` **50/50** incl. the new
  `TestVendoredResolution` (junk/missing/flagless degrade under fake
  HOME, LOAD canary, probe visibility).
- Consumers: test_graph + test_centrality_cache + test_maint +
  test_integration_snapshot_cycle **133/133**; perf-attribution suite
  21/21 + search_tui/gate_query 128/128; ruff clean; Makefile help-sync.
- Extension-over-wire: degree scored **20,046 nodes server-side** via
  `connect_read_only`; the quack wire-params limitation does not bite
  (onager wrappers are literal/identifier SQL by construction —
  `_IDENT_RE` guard, uniform file-mode).
- Measured legs (loaded box, loadavg ~4): LP **2.884 s** (`[via
  onager]`), route_graph_stats **2.787 s**. Budget re-tighten
  (LP 8.0→4.0, route 7.5→~5-6) awaits the operator's quiet-box arc.

## 5. Constraints & non-goals

- **personalised-pagerank wrap stays forbidden** (pending.md N5 item 6):
  neither the vendored build nor the dist artifact changes the
  algorithm — only an upstream fix reopens it.
- The vendored build is belt-and-braces, not the only channel: with a
  trusted install present the distribution arm wins (resolution is
  distribution-first). Fresh-box bootstrap: `SET
  custom_extension_repository='community-extensions.duckdb.org'; INSTALL
  onager; LOAD onager;`.
- `models/onager/bin/` is machine-local; the build tree under $TMPDIR
  is disposable — `make onager-build` re-fetches the pins.
- Budgets untouched in this change (quiet-box re-baseline is the
  operator's gate arc).
