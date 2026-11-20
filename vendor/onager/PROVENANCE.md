# PROVENANCE — vendored onager extension (RETIRED 2026-11-20)

**SUPERSEDED BY UPSTREAM.** The locally-built vendored extension is retired
as of 2026-11-20. DuckDB 2.0.1 now has a signed onager distribution in the
community repository, so onager loads flaglessly from the trusted install —
the locally compiled, unsigned artifact is neither needed nor loadable on
2.0.1 (it was stamped for 2.0.0). This file is kept as the historical record
of the pin and the build recipe; `make onager-build` and the
`ONAGER_VENDORED_EXTENSION` resolver arm are removed.

## Superseding distribution (live)

| field | value |
|---|---|
| install | `INSTALL onager FROM community;` (duckdb 2.0.1) |
| repository | `http://community-extensions.duckdb.org/v2.0.1/linux_amd64/onager.duckdb_extension.gz` (HTTP 200, 5,126,734 B gz / 15,348,110 B ext, Last-Modified 2026-11-20) |
| sha256 (ext) | `b530ee350599fbc1d43e35a65c02dc2c41673a2fc1c7bb41659751d1a1b1a135` |
| onager version | `onager_version() = 0.5.0` — upstream source did NOT advance; same v0.5.0 code recompiled for 2.0.1 |
| upstream source | github.com/CogitatorTech/onager, HEAD/tag v0.5.0 `c45146bc4b259cfcbd32ddace34eb8fc112e061d` (2026-10-23) |
| LOAD verdict | **PASS 2026-11-20** — flagless `LOAD onager`, 64 `onager_*` functions, louvain smoke `[(1,0),(2,0),(3,0)]`, installed_from='community' |
| test verdict | **129 passed** in `tests/test_graph_fallbacks.py tests/test_onager_capabilities.py tests/test_centrality_projection.py` after install (64 capability + 8 centrality tests un-skipped) |

The main repository (`extensions.duckdb.org/v2.0.1/...`) still returns 404
for onager — the extension is community-repo-only. Fresh-box bootstrap:

```sql
INSTALL onager FROM community;
LOAD onager;
```

## Historical pin (retired)

Built by `vendor/onager/build.sh` (onager_vendored_source_build). The recipe
below is preserved for provenance only — do not rebuild.

| field | value |
|---|---|
| onager commit | `c45146bc4b259cfcbd32ddace34eb8fc112e061d` (tag v0.5.0, 2026-10-23 — "Update the project to build after the DuckDB 2.0.0's API changes (#32)") |
| duckdb submodule | `d54cbbe3df17e8e5ce68312f483b4155836112aa` — **exactly the v2.0.0 tag** (asserted by build.sh) |
| extension-ci-tools submodule | `1b1d40ec725860761a219de0bba2651065a2f834` |
| graphina submodule | `835d1bc01afc703eb9d77c037410463ec46d2a68` |
| rust toolchain | 1.86.0 (rust-toolchain.toml pin, rustup-fetched) |
| build flags | `make release GEN=ninja` (rust core `cargo build --release --features duckdb_extension` → libonager.a → loadable extension) |
| onager.duckdb_extension sha256 | `db2f1e87b8d3a875248b89745e8222df0df75907b43ddbfca6f67e4889c8895f` |
| LOAD verdict | **PASS 2026-10-24** under duckdb 2.0.0 (connect-time `allow_unsigned_extensions`); full 79-test capability battery green |
| retired reason | fails on duckdb 2.0.1: `Failed to load ... The file was built specifically for DuckDB version 'v2.0.0' ... (this version of DuckDB is 'v2.0.1')` |

### Build traps (hit 2026-10-24, historical)

- **DUCKDB_VERSION stamp**: the duckdb CMake build must receive
  `DUCKDB_VERSION=v2.0.0`. A default-configured duckdb build stamps a dev
  version string into the extension ABI check and the loader rejects it.
- **Unsigned flag was connect-time**: `SET allow_unsigned_extensions`
  post-connect raises on duckdb 2.0 — the flag had to ride
  `duckdb.connect(config=...)`. This whole path is retired with the build.
- **Build tree was disposable**: the clone lived under `$TMPDIR`
  (`/mnt/data/tmp/onager-build/onager`); only the artifact + this file were
  durable.

### Why it was vendored (truth corrected 2026-10-24 afternoon)

`INSTALL onager` under duckdb 2.0.0 returned HTTP 404 from the main
repository. The community repository did serve the artifact, reaching this
box as a trusted install under `~/.duckdb/extensions/v2.0.0/linux_amd64/`
(sha256 `de09c1d9…cebd5c`). The vendored build was consequently
**belt-and-braces, not the only channel** — a network-free re-bootstrap
path. That need disappeared on 2.0.1 (signed dist available, one install
serves CLI + every venv under the same `$HOME`).

## Constraints that survive the build

- **personalised-pagerank wrap stays forbidden** (doc/pending_improvs.md:
  personalised_pagerank ignores the personalisation vector,
  helpers/graph/onager.py:836-era analysis, graph_design.md §5.5) — the
  dist artifact (same v0.5.0 code) does not change the algorithm; only an
  upstream fix reopens that wrap. Confirmed in b12564 probe: upstream still
  has unpatched `create_tensor_qkv` qwen2vl, personalisation bug intact.
- **Trust is a function of location**: the community install under the
  duckdb extensions dir loads flaglessly; the old vendored bytes loaded BY
  PATH required connect-time `allow_unsigned_extensions`. With the vendored
  path retired, `connect_kwargs()` only carries the repo memory cap.
