---
title: "Temp-dir hygiene — retarget gate/bench/Pdf scratch paths from /tmp to tracked, owned lifecycles"
status: executed
filed: "2026-09-17"
executed: "2026-09-17"
completed_md: "245"
area: "tests/ fuzz fixtures, helpers/bench + helpers/pdf scratch, run_gate_report steps"
---

# Temp-dir hygiene: every gate run under a self-cleaning /tmp budget

**Date:** 2026-09-17 · **Status:** EXECUTED ·
**Area:** tests/ fuzz fixtures, helpers/bench + helpers/pdf scratch, run_gate_report steps

> Filed AFTER a full disk audit (2026-09-17), per the house rule —
> proposal first, implementation next. All numbers measured on this box
> today; raw log in the Appendix.

## 1. Motivation

`/tmp` is tmpfs (RAM-backed). At audit time it held **2.0G / 7.1G (30%)**
and rising hour-over-hour across the day's gate runs. Three classes of
leftovers accumulate:

1. **pytest fuzz fixtures that bypass `tmp_path`**. `conftest.py` retains
   tmp dirs only for *failing* tests (`tmp_path_retention_policy = failed`,
   `count = 1`) — correct health present. But the fuzz suites reach past
   pytest and call `tempfile.mkdtemp()` / `mkstemp()` directly, so their
   artifacts are **never** reaped.
2. **bench/pdf scratch built with `mkdtemp` and no `finally`**, plus
   `delete=False` temp files unlinked only on the happy path.
3. **Other-tool residue in the audit** (`.{hash}-{chunk}.so` pairs,
   ~19 MiB per killed load) — originally mis-attributed to llama.cpp;
   **corrected 2026-09-17** to opencode's TUI native library
   (Zig/OpenTUI, `mmap`'d by opencode). Recorded as audit history;
   **excluded** from the sweep (not repo-owned).

Failure mode (matched to the field report — gates "filling the disk and
crashing themselves"): once tmpfs fills, `mkdtemp`/`NamedTemporaryFile`
raise `OSError` → pytest fixtures die at setup → the very run performing
the gate goes red; tmpfs shares RAM, so the failure can push the host
toward OOM, not just a red `make`.

Per-cycle cost today (measured 2026-09-17):

| Source | Size / run | Gate that triggers it |
|---|---|---|
| `test_fuzz_shortest_path` module fixture | **~176 MiB** | every `make qa` / `make fuzz` pytest |
| `test_fuzz_edge_writer` fixture | ~12 KiB | every `make qa` pytest |
| opencode `.so` chunk orphans *(audit-only — not repo-owned)* | ~19 MiB per killed load | none — host tool, not a gate |
| `test_fuzz_derive_insights_regions` vault | small | every `make qa` pytest |
| pdf lane (`pix2text`) | MiB/page once used | manual / pdf bench |
| fts-parity / deep-probe benches | bench-scale (GBs on hard stop) | manual / make bench |

## 2. Evidence (measured 2026-09-17, this box)

| Configuration | Result | Verdict |
|---|---|---|
| status quo, one `make qa` | `sp.db` 181,624,832 B + `sp.duckdb` 2,633,728 B + `.build.lock`/.shm/.wal in `/tmp/tmpXXXXXX/` | baseline — leaks |
| status quo, full audit | 8 orphaned `.{hash}-{chunk}.so` pairs ≈ 154 MiB dead — opencode's TUI native lib (Zig/OpenTUI), `mmap`'d, *not* this repo | other-tool residue — **out of scope** after the attribution correction below |
| status quo, one-off leftovers | `gate_drift_candidate.db` 181,633,024 B (full research.db copy, no code ref), ~41 MiB research scrapes, `search_tui.log` 121 KiB unbounded | high-water contributors |
| Already clean (no action) | `tmp_path` fixtures honor `retention=failed/count=1`; xdist per-worker duckdb cleanup in `pytest_sessionfinish` (conftest.py:626); `bench_scale_bfs`, `bench_pdf_pipeline`, `pdf_conv_md`, `markdown_lint` all clean via `finally`/ctx | credit |

Two "measured, do not re-audit" takeaways for future sessions:

- The **~176 MiB/cycle leak has one root cause**: the module-scoped
  `con` fixture in `tests/test_fuzz_shortest_path.py:99-119` runs a full
  unpruned `copy_production_db(DB_PATH, tmp)` (research.db is 174 MiB)
  into `tempfile.mkdtemp()` and never removes it. No other pytest path
  does this.
- The `.{hash}-{chunk}.so` / `.hm` temp files were initially
  mis-attributed to llama.cpp/`local_embedder`. **Corrected 2026-09-17:**
  they are opencode's TUI native library — a Zig-built OpenTUI `.so` that
  Bun extracts to `/tmp/.{hash}-{chunk}.so` and `mmap`s. `lsof -p <opencode>`
  shows the live mappings (the library carries `opentui-notifications`,
  `ALACRITTY_SOCKET` and ALSA symbols; clean exits unlink, abrupt ones
  strand the chunk). Not this repo's residue, so `tmp-sweep` deliberately
  does **not** claim it.

## 3. Design

Chosen mechanism: **own every temp lifecycle**, in three rules —
(1) pytest fixtures use `tmp_path`/`tmp_path_factory` so conftest's
retention policy already covers crashes; (2) everything else moves
inside `TemporaryDirectory()` or a `try/finally` unlink; (3) a
gate-front `tmp-sweep` purges the not-code-owned survivors
(stale research scrapes, other-tool scratch) by age + owner, so the next gate
can never inherit a near-full tmpfs.

Slices, each independently landable (S1 first — it is 95% of the leak):

- **S1 — shortest-path fuzz fixture back onto pytest-owned temp**
  `tests/test_fuzz_shortest_path.py:99-119`: keep `scope="module"`, but
  build under `tmp_path_factory.mktemp()` and copy with
  `copy_production_db(DB_PATH, tmp, vacuum=True)` — the vacuum returns
  freed pages so the pruned copy is ~2 MiB instead of the full 174 MiB
  corpus. Unblocks: ~176 MiB/cycle gone, crash-safe by construction.
- **S2 — edge_writer fixture onto `tmp_path_factory`**
  `tests/test_fuzz_edge_writer.py:56-67`: replace the `mkstemp` +
  unlink-then-reconnect trick with a temp-path-backed sqlite file.
- **S3 — `_splice_state` vault cleanup** `tests/test_fuzz_derive_insights_regions.py:247-260`:
  `@given` can't take fixtures, so keep the lazy module-global build but
  register `atexit(shutil.rmtree, ..., ignore_errors=True)` and retain
  the built path for re-use (no per-example rebuild).
- **S4 — pix2text scratch under `TemporaryDirectory`**
  `helpers/pdf/pix2text_markdown.py:60-96`: wrap the page-PNG render
  loop + `Pix2Text().recognize` in a `with tempfile.TemporaryDirectory(prefix="pix2text_")`
  ctx so success *and* exception leave nothing.
- **S5 — bench scratch under `finally`**
  - `helpers/bench/fts_duckdb_parity.py:252,379`: `mkdtemp` →
    `TemporaryDirectory`; delete the *delete when done* print.
  - `helpers/bench/note_deep_probe.py:201,262` and
    `note_deep_probe_candidates.py:196,264,310,416`: wrap the `run()`
    body so `Path(tmp.name).unlink()` is in `finally` (leak only remains
    for a hard `kill -9` of the bench — acceptable, since tmp-sweep
    covers that class).
- **S6 — search_tui event log cap** `helpers/misc/search_tui_app.py:103`:
  `_EVLOG` truncates to (say) 64 KiB on startup instead of growing
  unbounded; `.log` stays in /tmp but bounded.
- **S7 — `make tmp-sweep` + wire into gates**
  New small helper (own prefix list, default `--dry-run`):
  - `helpers/maintenance/tmp_sweep.py` removes, for the current user
    only, files/dirs in `/tmp` matching known prefixes
    (`sp.db*`/`tmp*.db`/`tmp*.duckdb`/`pix2text_*`/`pdf_local_*`/
    `fts_parity_*`/`md_lint_shard_*`/`scale_bfs_*`/`bench_pdf_*` bench
    scatters + marker dirs) with mtime older than a guard (24 h — never
    touches an in-flight fixture).
  - Wiring: first `Step` of the `qa` and `advisory` gates in
      `tests/run_gate_report.py` (`qa`/`advisory` step tuples) — a
      non-blocking 「cleanup」 row that runs before everything so the
      gate can never inherit a near-full tmpfs. Same step stays
      callable standalone (`make tmp-sweep`).

### Alternatives considered

- A one-off `rm -rf` of /tmp today — not a mechanism; the audit
  leftovers get a one-time purge during execution, but the slices
  above are what stop the re-accretion.
- Moving pytest `--basetemp` to a repo-local `.tmp` — viable later, but
  oversized: the real leak isn't pytest-owned temp, it's the
  direct-`tempfile` sites. Recorded for future migration, out of scope
  here.
- Sweeping `/tmp` wholesale in the gate — rejected: tmpfs is machine
  shared; delete only what this repo provably owns (prefix + owner +
  age guard).

## 4. Acceptance criteria & shakedown

1. **/tmp budget**: `du -sh /tmp` recorded; run `make qa` back-to-back
   twice; the second run adds **< 5 MiB** to `/tmp` (today it adds
   ~176 MiB). No new `sp.db` dirs, no new `tmp*.db` after either run.
2. **Suite green**: `make qa` passes (2423+ pytest cases as of the
   2026-09-31 baselines; the fuzz files S1–S3 touch still fully pass
   under `make fuzz` with `--hypothesis-seed=0`).
3. **Crash semantics**: `kill -9` a run mid-`note_deep_probe`; verify a
   back-dated (mtime > 24 h) copy of its temp is removed by
   `make tmp-sweep` while a fresh, owner-matching file is left alone —
   the age guard is what spares in-flight scratch.
4. **Gate wiring**: `qa` + `advisory` report tables show the new
   `tmp-sweep` step with a passing/cleanup row alongside the existing
   rows, and `make tmp-sweep` exits 0 in `--check` (no-op) mode.

*Eval gate note:* this arc touches no query-visible semantics (rosters,
crosswalks, hierarchies, extractor rules, index contents) — the
`ontology_eval_gate.py` bullet is **N/A**; the closest analogue gate
(item 2 above) is the full pytest + gate surface.

| Projected outcome | Today | After |
|---|---|---|
| /tmp growth per `make qa` | ~176 MiB + 12 KiB | < 5 MiB (sweep-resettable) |
| repo-owned temp orphans at gate start | up to ~154 MiB dead (mis-attributed) | 0 for repo artifacts; opencode `.so` chunks explicitly not claimed |
| hard-stop bench residue | GB-scale possible | swept at next gate |

## 5. Risks

- **S1 fixture move changes module setup order** (temp path, vacuum
  adds ~1-2 s to the copy) — mitigated by keeping `scope="module"`
  via `tmp_path_factory` and asserting the same oracle parity; full
  qa shakedown covers scoping regressions.
- **tmp-sweep deletes something live** — triple guard (exact prefix
  list, process-owner match, 24 h mtime) and default `--dry-run`;
  in-flight scratch is mtime-new, so the age guard alone spares it.
- **pix2text internals assume a stable dir path longer than the
  function call** — verified false: PNGs are read inside the same
  call; `TemporaryDirectory` scoping is whole-function.
- **S5 `finally` on `run()` delays cleanup on the happy path by
  nothing measurable** — unlink cost is microsecond-scale.

## 6. Non-goals

- **`memory/*.duckdb` / `.xdist-*` workers**: in-repo by design; the
  graceful-path cleanup already exists (conftest `pytest_sessionfinish`).
  Worker files leaked by SIGKILLed pytest runs are a separate, smaller
  class — deliberately out of scope here.
- **Tool caches** (`/tmp/opencode` ~1.2 G, `/tmp/ripwire-1000` ~109 M,
  `/tmp/evoontology` ~30 M, node-compile-cache): owned by other tools;
  not gate-caused. A one-time advisory purge is a doc footnote, not an
  arc slice.
- **Research-scrape leftovers** (`kite_inst*.csv`, `bse_sme*.json`,
  `jcd_split`, `hkex_rows`, `sov_base`, `gate_drift_candidate.db`):
  one-off operator artifacts; purged during execution, no code owns them.
- **`tmp_path_retention_policy` stays** `failed / count=1` — that is
  working health, not something to strengthen here.

## 7. Implementation log (2026-09-17)

All seven slices are in. The operator committed S1–S5 (fts) as
`fe97dde6`; the remaining files were left dirty for staging per the
house rule.

| Slice | File | Change |
|---|---|---|
| S1 | `tests/test_fuzz_shortest_path.py` | `con` fixture → `tmp_path_factory.mktemp("shortest_path")/sp.db` + `copy_production_db(..., vacuum=True)` (174 MiB → ~2 MiB) |
| S2 | `tests/test_fuzz_edge_writer.py` | `db_path` fixture → `tmp_path_factory.mktemp("edge_writer")/edges.db` |
| S3 | `tests/test_fuzz_derive_insights_regions.py` | `_splice_state` root = `mkdtemp(prefix="fuzz_derive_insights_")` + `atexit.register(shutil.rmtree, ...)` |
| S4 | `helpers/pdf/pix2text_markdown.py` | render + `Pix2Text().recognize` inside `with tempfile.TemporaryDirectory(prefix="pix2text_")` |
| S5a | `helpers/bench/fts_duckdb_parity.py` | `TemporaryDirectory(prefix="fts_parity_")`; per-query dump moved out of the reaped dir to `outputs/fts_parity_results.json` (gitignored) |
| S5b | `helpers/bench/note_deep_probe.py`, `..._candidates.py` | `atexit.register(Path(tmp.name).unlink, missing_ok=True)` at creation — covers the exception path without re-indenting the whole `run()` body for an equivalent `finally` |
| S6 | `helpers/misc/search_tui_app.py` | `_cap_evlog()` truncates `search_tui.log` at startup when it exceeds 64 KiB |
| S7 | `helpers/maintenance/tmp_sweep.py` (new), `Makefile`, `tests/run_gate_report.py` | owner + 24 h-mtime + prefix/marker-guarded sweep; `make tmp-sweep` (dry-run default, `APPLY=1` removes); non-blocking first step of the `qa` and `advisory` gates |

**Shakedown (2026-09-17):**

- Two back-to-back green `make qa` runs → `/tmp` delta **0 MiB** (budget
  < 5); no new `sp.db` dirs, no `tmp*.db`, pytest basetemp empty.
- `make fuzz` with `--hypothesis-seed=0` → **165 passed**, `/tmp` delta 0,
  no `fuzz_derive_insights_*` residue.
- Crash semantics: a back-dated (>24 h) crashed-bench temp is removed by
  `make tmp-sweep` while a fresh, owner-matching file is spared;
  `tmp_sweep --check` exits 1 when dirty, 0 when clean.
- Gate wiring: the `qa`/`advisory` report tables carry the new
  `tmp-sweep` row (`✓ OK`).

Adjacent flake, **not caused by this arc**, fixed while here: the
`test_fuzzy_duplicates_scales_quadratically_not_worse` /
`test_fuzzy_match_scales_linearly_with_entities` time-ratio guards timed
the small and large phases separately, so one scheduler stall in either
phase flaked the whole ratio under the 4-job suite (~6 ms / ~57 ms
samples → 9.7× while passing solo). Both now time the sizes back-to-back
per round and assert the **median round's ratio** (5 rounds,
`_median_round_ratio` in `tests/test_performance.py`); verified 8/8
under xdist + CPU hogs, and a simulated 8× (O(n³)) regression still
fails. The shakedown above deselected the guard via `PYTEST_ADDOPTS`
because it predates the fix — future shakedowns need not. The one-time
audit survivors (`/tmp/tmpdky35x5i` 184 MB, `/tmp/gate_drift_candidate.db`
174 MB, the day's `tmp*.db`) were purged during execution.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-17 20:25 | `make qa` pytest (observed dir) | `sp.db` 181,624,832 B; `sp.duckdb` 2,633,728 B; `.build.lock`, `.shm`, `.wal` | `test_fuzz_shortest_path.py:103` fixture, module scope |
| 2026-09-17 | audit `ls /tmp` | 10 `.{hash}-{chunk}.so` pairs (13,745,312 B + 5,576,816 B each); 8 dead, 2 live `mmap`'d by opencode (`lsof -p 1675938`) | **mis-attributed to llama.cpp at audit time; corrected to opencode/OpenTUI — not repo-owned** |
| 2026-09-17 | audit `ls /tmp/tmp*.db` | 12,288 B each, dated 09:18→15:02 | `test_fuzz_edge_writer.py:58` mkstemp residue |
| 2026-09-17 | audit `du /tmp` | 2.0 G / 7.1 G tmpfs (30%) | includes 1.2 G tool caches (non-goal) |
| 2026-09-17 | `du -h memory/research.db` | 174 M | the unpruned-copy source for S1 |
| 2026-09-17 | `search_tui.log` | 121,171 B, unbounded append | `search_tui_app.py:103` |
