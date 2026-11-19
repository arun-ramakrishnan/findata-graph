---
title: "zstd-compress pytest metadata sidecars at gate-artifact retain time"
status: proposed
filed: "2026-11-19"
executed: null
completed_md: null
area: "tests/run_gate_report.py + helpers/misc/gate_query.py + outputs/.artifacts"
---

# zstd-compress pytest metadata sidecars at gate-artifact retain time

**Date:** 2026-11-19 · **Status:** PROPOSED · **Area:** `tests/run_gate_report.py`,
`helpers/misc/gate_query.py`, one-time backfill under `outputs/.artifacts/`

## 1. Context — the 63% that is redundant but not deletable

The 2026-11-19 outputs/ cleanup took the directory from 954.1 MB to 349 MB and
left `outputs/.artifacts` at 176.9 MB, composed as:

| component | MB | share |
|---|---|---|
| junit XML (retained per run) | 34.2 | 19% |
| **metadata sidecar JSON** | **111.7** | **63%** |
| per-step stdout logs | 31.0 | 18% |

The sidecars are `<gate>.metadata.gwN.json` (one per pytest-xdist worker) plus a
59-byte `<gate>.metadata.json` stub. Per gate: qa 94.4 MB, integration 12.9 MB,
advisory 4.1 MB; the newest qa run alone carries 2.6 MB of them.

They are redundant *and* load-bearing at the same time:

- **Redundant:** the facts they carry are already in `outputs/gate_runs.duckdb` —
  for all 40 kept runs of each gate, `test_facts` rows cover every sidecar test
  (spot-check run 4160 / dir 20261112T120948-624359: gw0 994=994, gw1 1254=1254,
  gw2 1588=1588, gw3 2295=2295, markers and `phase_seconds_json` match both sides).
- **Load-bearing:** `gate_query refresh --rebuild-tests`
  (`helpers/misc/gate_query.py:2553` `rebuild_test_facts`) re-derives every
  `tests`/`test_facts` row from the RETAINED junit artifact, and
  `_test_metadata_by_node` (`helpers/misc/gate_query.py:498-523`) reads the
  sidecars to recover markers, phases, `file_line` and worker. Live junit XML
  carries NO pytest properties at all (measured: 6,486 testcases, 0 with
  `./properties/property`), so deleting the sidecars would permanently degrade
  any future rebuild.

Compression is the way out: zstd on `qa.metadata.gw3.json` (954,957 bytes) →
122,937 bytes at level 10 (12.9%), 138,452 bytes at level 3 (14.5%). Repo policy
(#174) is the library-default level with no explicit switch — the same convention
as `helpers/core/zstd_io.py` and the archived report tails
(`helpers/misc/gate_query.py:60` already imports `compress, decompress` from
`compression.zstd`).

## 2. Alternatives considered (rejected / deferred ledger)

| alternative | verdict | why |
|---|---|---|
| delete sidecars outright (`test_facts` has the facts) | REJECTED | future `--rebuild-tests` loses markers/worker/`file_line`/phases forever — the DB is a projection, the sidecars are the source |
| shrink retention window (keep10 = 51.5 MB across gates) | REJECTED as primary lever | trades raw history for space; measured keep5/10/20/40 per gate: qa 17.5/34.5/65.7/124.6, integrity 3.0/6.0/11.9/23.9, integration 2.2/4.3/8.6/16.9, advisory 1.4/3.0/4.7/7.3 MB — compression gets most of the win at keep=40 |
| compress junit XML too (34.2 MB) | PROMOTED to S5 (2026-11-19, operator request) | measured 34.2 → 5.2 MB (15.2%); three read sites, see S5 |
| compress per-step logs (31.0 MB) | PROMOTED to S6 (2026-11-19, operator request) | measured 31.0 → 5.3 MB (17.2%); `gate_query output` learns the `.log.zst` form, plain stays readable |
| shrink `gate_runs.duckdb` further | N/A | already compacted 294.3 → 114.3 MB this arc |

## 3. Measured baseline (read/write path facts this design must respect)

- Writer: `retain_test_artifacts` (`tests/run_gate_report.py:644`) copies
  `<gate>.metadata.json` + glob `<gate>.metadata.*.json` + `gate-artifacts.json` +
  14 native artifact names + fresh step logs into the run dir; prune policy
  `_ARTIFACT_KEEP_RUNS = 40` (`tests/run_gate_report.py:139`),
  `prune_test_artifacts` at `:701` called at `:852`.
- Reader: `_test_metadata_by_node` builds candidates as
  `[<gate>.metadata.json, *sorted(glob("<gate>.metadata.*.json"))]`, enforces an
  mtime window `lo-120s .. hi+300s`, then `jsonio.loads(path.read_text())`.
  `jsonio.loads` accepts str OR bytes (`helpers/core/jsonio.py`), so a decompressed
  payload needs no new parsing surface.
- `tests/test_run_gate_report.py:132-176`
  `test_retain_test_artifacts_copies_current_run_files` asserts the EXACT retained
  filename set — it must move with the format.
- zstd error type is `compression.zstd.ZstdError`; a corrupt sidecar must be
  skipped like a `JSONDecodeError` today, never crash `refresh`.
- No other readers: grep over `helpers/`, `tests/`, `Makefile*` finds the sidecar
  filenames only in `gate_query.py`, `run_gate_report.py`, their tests, and the
  conftest writer.

## 4. Slices

### S1 — retain-time compression (`tests/run_gate_report.py`)

In `retain_test_artifacts`, replace the `shutil.copy2` of the metadata sidecars
with compression to `<name>.json.zst` (append `.zst`, the `zstd_io.zst_path`
convention — never suffix-replace), library-default level, then
`shutil.copystat(source, dst)` so the compressed file keeps the source mtime and
the reader window check stays honest. Junit, `gate-artifacts.json`, native
artifacts and step logs stay plain.

**Acceptance:** a fresh run dir contains no plain metadata `.json`; each `.zst`
decompresses to byte-identical JSON; retain-time cost ≤ ~100 ms for a 2.6 MB qa
run.

### S2 — read path (`helpers/misc/gate_query.py` `_test_metadata_by_node`)

Extend the candidate list with the `.zst` variants —
`<gate>.metadata.json.zst` plus glob `<gate>.metadata.*.json.zst` (the existing
plain globs do not match `.json.zst` names) — and decompress before
`jsonio.loads`, catching `ZstdError` alongside `OSError`/`JSONDecodeError`.
Plain-`.json` reading stays (pre-backfill dirs, any external producer).

**Acceptance:** a run dir holding only `.zst` sidecars yields the identical
`_test_metadata_by_node` dict as its plain-`.json` twin.

### S3 — one-time backfill of the 120 kept run dirs (40 × qa/integration/advisory)

Compress the existing sidecars in place under `outputs/.artifacts/`, `copystat`,
remove the plain originals.

**Acceptance:** metadata-json 111.7 MB → ≤ 20 MB; `.artifacts` total 176.9 →
≤ 85 MB; `gate_query refresh --check` clean.

### S4 — tests

Update the asserted filename set in `tests/test_run_gate_report.py:132-176`
(metadata entries become `.json.zst`, plus a decompress-equals-original
assertion); add a `gate_query` test reading `.zst` sidecars (mirror
`test_junit_facts_normalize_metadata_and_fingerprints`); keep the existing
plain-`.json` tests green as the fallback proof.

**Acceptance:** targeted suites pass:
`tests/test_run_gate_report.py`, `tests/test_gate_query.py`,
`tests/test_gate_retain.py`, `tests/test_report_append_convention.py`.

### S5 — junit XML compression (added 2026-11-19, operator request)

Retain the junit as `<gate>.junit.xml.zst` (34.2 MB → 5.2 MB measured,
15.2%). Three read sites move:

- `_retained_artifact_junit` (`helpers/misc/gate_query.py:672`) accepts
  either form and returns the path that exists — this is what feeds the
  `runs.junit_path` DB column, so the column starts carrying `.zst` paths.
- `_junit_facts` (`:572`) parses via `ET.fromstring(decompress(...))` for the
  `.zst` form; plain `ET.parse` stays for the live staging file (fresh gate
  runs still land plain in `outputs/.junit/` and are read there at index
  time). `ZstdError` joins the parse-error skip set.
- The backfill (S3 extension) compresses the retained junit of every run dir
  and rewrites `runs.junit_path` to the `.zst` path for exactly the rows
  whose file was compressed.

**Acceptance:** `refresh --rebuild-tests --apply` over compressed junit
reproduces the pre-backfill `test_facts` multiset hashes; plain-junit fixtures
in the tests keep passing (fallback order: plain first, then `.zst`).

### S6 — per-step log compression (added 2026-11-19, operator request)

Retain each step log as `<gate>.<label>.log.zst` (31.0 MB → 5.3 MB measured,
17.2%). `cmd_output` (`helpers/misc/gate_query.py:2349`) learns the form
everywhere it touches a log: the no-`--leg` listing, the leg-path resolution
(plain first, then `.zst` twin), the retained-legs hint, and the read itself
(decompress then decode). `_retained_artifact_junit`'s
`artifact_state="logs"` detection globs both suffixes.

**Acceptance:** `gate_query output --leg <name>` serves a `.log.zst` log
byte-identically to its plain twin; the listing names both forms; plain-log
fixtures keep passing.

## 5. Non-goals

- No compression of the native gate artifacts (`ruff.json`, `coverage.xml`,
  …) or `gate-artifacts.json` — small, and read by `_artifact_record` as
  plain JSON.
- No retention-window change — keep=40 stays everywhere.
- No `gate_runs.duckdb` schema or content change.
- No change to the live staging dir `outputs/.junit` (runner-owned, 3.8 MB).
- No `ticker_hygiene_report.md` handling (machine-consumed by
  `helpers/maintenance/enrich_relations.py:1933`; separate concern).

## 6. Risks / mitigations

| risk | mitigation |
|---|---|
| backfill sets fresh mtimes → reader window rejects the sidecars | `copystat` in both S1 and S3; S4 test asserts the window still passes |
| corrupt `.zst` crashes `refresh` | `ZstdError` caught, sidecar skipped — same posture as `JSONDecodeError` today |
| `.zst` content silently diverges from the original | backfill verification is a hash-based multiset comparison on `test_facts` (`count(*) + sum(hash(<explicit cols>))` per run — ORDER BY ties make row-diff checks lie) before vs after `refresh --rebuild-tests` |
| an untracked reader breaks | grep found none outside `gate_query.py` + the conftest writer; plain-`.json` fallback retained anyway |

## 7. Verification plan

1. Targeted tests (S4 list) + `ruff format --check .` + `ruff check .` +
   `helpers/validators/static_checks.py` rc 0.
2. Backfill parity: snapshot `count(*)`/`sum(hash(...))` of `test_facts` for the
   affected runs, run `gate_query refresh --rebuild-tests`, compare.
3. `gate_query refresh --check` clean; disk before/after measurement.
4. `make search-fresh APPLY=1 ARGS=--allow-stale` then plain `make search-fresh`
   (helper docstrings change).

## 8. pending.md annotations

None — no `doc/improvements/pending.md` item is touched by this change.

## 9. Decision requested

Approve S1–S4 (retain-time compression + reader + backfill + tests), or S1+S2+S4
only and leave the one-time backfill (S3) to the operator.

**Approved and executed 2026-11-19 (all four slices).**

## 10. Execution record (2026-11-19)

- **S1+S2** landed: `retain_test_artifacts` writes `<name>.json.zst`
  (library-default level, `copystat`); `_test_metadata_by_node` reads both
  forms, `ZstdError` caught alongside `OSError`/`JSONDecodeError`.
- **S3 backfill:** 600 sidecars compressed (0 skipped), sidecar bytes
  111.5 MB → 16.1 MB (14.4%); `outputs/.artifacts` total
  **176.9 MB → 81.4 MB** — both acceptance bars met (≤ 20 MB metadata,
  ≤ 85 MB total).
- **Parity:** `refresh --rebuild-tests --apply` re-derived 120 runs /
  275,335 test rows from the compressed sidecars; per-run multiset hash
  (`count(*)` + `sum(hash(<all 15 explicit cols>))`) against the pre-backfill
  snapshot: **369/369 runs identical, 0 missing, 0 new**. (Comparator gotcha
  fixed en route: JSON lists vs Python tuples compare unequal — compare
  components, not containers.)
- **S4 tests:** filename-set assertion moved to `.json.zst` + round-trip +
  mtime-preservation assertions; new `test_junit_facts_read_zstd_sidecars`
  (enrichment identical, corrupt frame skipped, worker precedence documented:
  the junit `property` wins over the sidecar). 92 passed across the four
  targeted suites.
- Housekeeping: ruff format (1 file) + check clean, static_checks rc 0,
  `gate_query refresh --check` clean, `make search-fresh` APPLY + plain
  check green.
- **S5+S6 (2026-11-19, operator request — see §4):** writer
  `_retain_zst` compresses junit, sidecars, and step logs at retain;
  reader accepts both forms everywhere (`_retained_artifact_junit` .zst
  twin feeding `runs.junit_path`, `_junit_facts` `ET.fromstring`
  decompress branch, `cmd_output` listing/resolution/read). One real bug
  caught by the new test: `_test_metadata_by_node` derived sidecar names
  via `removesuffix(".junit.xml")`, which left `qa.junit.xml.zst`
  unstripped so sidecar enrichment silently vanished for compressed
  junit — fixed to strip `.zst` first. Backfill: 120 junit
  (34.2 MB → 5.2 MB, 15.2%) + 1,919 step logs (31.0 MB → 5.3 MB, 17.2%)
  compressed with round-trip verification; `runs.junit_path` rewritten to
  `.zst` for exactly the 120 compressed files; `outputs/.artifacts` total
  **81.4 MB → 26.8 MB** (35 MB apparent). Parity re-proven after the
  rebuild-from-compressed-junit: **369/369 runs identical, 0 missing,
  0 new**; `refresh --check` clean; zero plain junit/log/sidecar files
  remain. New tests `test_retained_zstd_junit_rebuilds_test_facts` +
  `test_output_verb_reads_zstd_step_log`; markers are the junit ∪ sidecar
  union, so the enrichment assertion pins `["integration","live"]`.
  94 passed across the four targeted suites; ruff format (4 files) +
  check clean, static_checks rc 0.
