# PROVENANCE — vendored onager extension (models/onager/bin)

Built by `vendor/onager/build.sh` (onager_vendored_source_build).
Update this file on every re-pin — the LOAD verdict is the gate: the
extension must LOAD into the production duckdb and answer one table
function (`tests/test_graph_fallbacks.py::TestVendoredResolution` carries
the canary; the 64-test `tests/test_onager_capabilities.py` battery
un-skips on top of it).

## Current pin

| field | value |
|---|---|
| onager commit | `c45146bc4b259cfcbd32ddace34eb8fc112e061d` (tag v0.5.0, 2026-10-23 — "Update the project to build after the DuckDB 2.0.0's API changes (#32)") |
| duckdb submodule | `d54cbbe3df17e8e5ce68312f483b4155836112aa` — **exactly the v2.0.0 tag** (asserted by build.sh) |
| extension-ci-tools submodule | `1b1d40ec725860761a219de0bba2651065a2f834` |
| graphina submodule | `835d1bc01afc703eb9d77c037410463ec46d2a68` |
| rust toolchain | 1.86.0 (rust-toolchain.toml pin, rustup-fetched) |
| build flags | `make release GEN=ninja` (rust core `cargo build --release --features duckdb_extension` → libonager.a → loadable extension) |
| onager.duckdb_extension sha256 | `db2f1e87b8d3a875248b89745e8222df0df75907b43ddbfca6f67e4889c8895f` |
| LOAD verdict | **PASS 2026-10-24** — loads by path under duckdb 2.0.0 (connect-time `allow_unsigned_extensions`, see below); `onager_ctr_*`/`onager_mtr_*` table functions execute file-mode AND over the quack wire; full 79-test capability battery green |

## Build traps (hit 2026-10-24, keep for re-pins)

- **DUCKDB_VERSION stamp**: the duckdb CMake build must receive
  `DUCKDB_VERSION=v2.0.0` (build.sh exports it). A default-configured
  duckdb build stamps a dev version string into the extension ABI check
  and the produced artifact fails the loader's version gate even with
  unsigned extensions allowed.
- **Unsigned flag is connect-time**: `SET allow_unsigned_extensions`
  post-connect raises "Cannot change allow_unsigned_extensions setting
  while database is running" on duckdb 2.0 — the flag must ride
  `duckdb.connect(config=...)`. Resolution lives in
  `helpers/core/duckdb_shared.py::unsigned_ext_kwargs()` /
  `helpers/graph/onager.py::connect_kwargs()` and is threaded through
  every factory that opens a connection the extension may load into
  (engine, probes, stamp/swap lanes, quack server child, wire fallback).
- **Build tree is disposable**: the clone lives under `$TMPDIR`
  (`/mnt/data/tmp/onager-build/onager`) — only the artifact + this file
  are durable. `build.sh` re-fetches the pins; a lost build tree is a
  re-run of `make onager-build`, not an incident.

## Why vendored (truth corrected 2026-10-24 afternoon)

`INSTALL onager` under duckdb 2.0.0 returns HTTP 404 — **from the main
repository** (`extensions.duckdb.org/v2.0.0/...`; clean-room re-verified
2026-10-24: plain INSTALL fails with a 404 and no community-repo
fallback). The **community repository does serve the artifact**
(`http://community-extensions.duckdb.org/v2.0.0/linux_amd64/
onager.duckdb_extension.gz` → HTTP 200, v0.5.0 dist build — proven
2026-10-24); it reached this box as a trusted install under
`~/.duckdb/extensions/v2.0.0/linux_amd64/` (sha256 `de09c1d9…cebd5c`,
which a bare flagless `LOAD onager` therefore accepts). The install was
done from the standalone **duckdb CLI tool**, which manages `~/.duckdb`;
the venv's duckdb-py keeps its own python libs but its DEFAULT
extension directory resolves through `$HOME` to the same
`~/.duckdb/extensions/` — proven 2026-10-24: from `.venv/bin/python3`,
flagless `LOAD onager` succeeds with `installed_from='community'`
under the real HOME, and fails (IO Error naming
`$HOME/.duckdb/extensions/v2.0.0/.../onager.duckdb_extension`) under an
empty fake HOME. One install therefore serves the CLI and every venv
whose duckdb-py runs with the same `$HOME`. Fresh-box
bootstrap recipe:

```sql
SET custom_extension_repository = 'community-extensions.duckdb.org';
INSTALL onager;
LOAD onager;
```

The vendored build is consequently **belt-and-braces, not the only
channel**: it pins the exact upstream port commit locally, needs no
network, and (via the `ONAGER_VENDORED_EXTENSION` override) gives the
resolver a second path. With a trusted install present, the
distribution-first resolution runs the upstream dist bytes; the vendored
file stays the re-bootstrap path on a wiped machine or an offline one.

The break that started this: our duckdb 1.5.6 → 2.0.0 upgrade
(completed.md #427) demoted all 13 `_METRIC_DISPATCH` families to the
scipy/numpy/igraph/pure-Python fallback chain (`helpers/graph/
graph_fallbacks.py`, landed 735e10e60) — graph_link_prediction went
2.3-2.6 s → 39.8-47.5 s naive → 6.15 s vectorised (e3147ef3c, budgets
re-based LP 8.0 / route 7.5). The decisive production fix was **not**
the artifact but the never-wired dispatch: `graph_fallbacks._dispatch`
had zero callers since 735e10e60, so even a loadable extension changed
nothing until `helpers/graph/algorithms.py::_extension_first` routed the
read paths through it (2026-10-24 — LP measured 2.88 s with the
extension live).

## Constraints that survive the build

- **personalised-pagerank wrap stays forbidden** (doc/pending_improvs.md:
  personalised_pagerank ignores the personalisation vector,
  helpers/graph/onager.py:836-era analysis, graph_design.md §5.5) —
  neither the vendored build nor the dist artifact changes anything in
  the algorithm; only an upstream fix reopens that wrap.
- **Trust is a function of location**: a copy under the duckdb
  extensions dir loads flaglessly; the same bytes loaded BY PATH
  (models/onager/bin) require connect-time
  `allow_unsigned_extensions` (see Build traps). `tests/
  test_graph_fallbacks.py::TestVendoredResolution` isolates the arms
  with an empty fake HOME because the trusted install satisfies the
  distribution arm.

## Re-pin procedure

1. `PINNED_COMMIT=` in `build.sh` → new upstream commit (keep
   `DUCKDB_PIN` = the duckdb tag the production runtime runs — build.sh
   fails loudly if the submodule drifts off it).
2. `make onager-build`.
3. LOAD verdict: `.venv/bin/python3 -m pytest
   tests/test_graph_fallbacks.py::TestVendoredResolution -q` (canaries)
   then `tests/test_onager_capabilities.py tests/test_centrality_projection.py`
   (the batteries that un-skip once LOAD works).
4. Record commit + sha256 + verdict here.
