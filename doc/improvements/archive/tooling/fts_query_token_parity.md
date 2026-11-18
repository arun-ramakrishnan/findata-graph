---
title: "Align fts_query_tokens with the DuckDB analyzer to close the macro-fallback recall gap"
status: executed
filed: "2026-11-18"
executed: "2026-11-18"
completed_md: "543"
area: "helpers/maintenance"
---

# Align `fts_query_tokens` with the DuckDB analyzer

**Date:** 2026-11-18 · **Status:** PROPOSED ·
**Area:** `helpers/maintenance/rebuild_doc_search.py`, `tests/test_fts_topk_parity.py`

## 1. Motivation

The SQL top-k leg (`fts_topk`, helpers/core/duckdb_shared.py:520) resolves
query tokens against the FTS `dict` before reading postings. Unresolvable
tokens return `(None, "macro")` and the caller falls back to the
`match_bm25` macro — a whole-table predicate scan. Two consequences, both
measured 2026-11-18:

1. **Every punctuated query takes the slow path.** `fts_query_tokens`
   (rebuild_doc_search.py:921) splits on whitespace only, so `cattle-feed`
   is one token. The analyzer splits on punctuation, so **no dictionary term
   in any store contains `-` or `&`** (doc 0/11,942 · script 0/5,175 · note
   0/31,390). The token can never resolve. `perf_smoke` shows the fallback
   firing live on `script/aquaculture` and `script/to`.
2. **The fallback loses recall for `R&D`.** `doc_search` has 3 rows whose
   `content` literally contains `R&D`; `match_bm25(id, 'R&D')` returns 0.
   Splitting the same query into `['r','d']` resolves on top-k. So the
   documented drift at `tests/test_fts_topk_parity.py:42-56` is not just a
   ranking difference — the lexical leg contributes **nothing** for that
   query, and hybrid mode masks it behind the vector leg.

The drift is already pinned as *known* in
`doc/improvements/archive/database/duckdb_watchlist_hardening.md` §2 G4 and
`doc/improvements/pending.md:1158-1172`. This proposal closes it rather than
re-documenting it. My earlier characterization of `aquaculture` as a
correctness gap was **wrong**: the script corpus has 0 rows containing that
word, so returning 0 was correct. The real recall gap is the ampersand family
(`R&D`); the hyphen family is a latency + ranking gap.

## 2. Evidence (measured 2026-11-18, this box)

| Configuration | Result | Verdict |
|---|---|---|
| `fts_query_tokens("cattle-feed")` | `['cattle-feed']` | one unresolvable token |
| `fts_query_tokens("R&D")` | `['R&D']` | one unresolvable token |
| `fts_topk(tokens=['cattle-feed'])` | `(None, 'macro')` | falls back |
| `fts_topk(tokens=['cattle','feed'])` | top-k, 10 rows, 32.1 ms | resolves |
| `fts_topk(tokens=['r','d'])` | top-k, 10 rows, 14.3 ms | resolves |
| `match_bm25(id,'R&D')` on doc | 0 rows, while `ILIKE '%R&D%'` = 3 | recall gap |
| `match_bm25(id,'cattle-feed')` on doc | 159 rows, 27.7 ms (vs top-k 3.5 ms) | overmatch + 8× slower |
| dict terms containing `-` / `&` | 0 / 0 (doc, script, note) | analyzer splits punctuation |

`test_fts_topk_parity.py:104` already asserts the DuckDB-vs-FTS5 result
contract on an edge-query bank (`hyper-graph`, `cattle-feed`,
`state-of-the-art`, `R&D`, `FY2026`, `don't`) and pins the current drift in
`_KNOWN_DRIFT`. That test is the oracle and the acceptance gate.

## 3. Design

### S1 — analyzer-matched query tokenizer

Change `fts_query_tokens` to split on the same punctuation class DuckDB's
analyzer uses, producing `cattle-feed → ['cattle','feed']`, `R&D → ['r','d']`,
`don't → ['don','t']` (or whatever the analyzer's exact output is — prove it
against `SELECT term FROM <fts>.dict` on the seeded fixture, not by guess).
Keep `fts_match_expr` (the FTS5 quoted-OR form) unchanged: it is the legacy
surface with its own unicode61 semantics and its own pinned results.

Make the splitter a single shared helper used only by the DuckDB top-k
caller. Rejected alternative: changing `fts_topk` to split internally — the
stem-guess ladder already owns token resolution, and doing it in two places
would diverge from the parity test's direct `fts_topk` calls.

### S2 — retire `_KNOWN_DRIFT` and tighten the oracle

Once S1 lands, run `test_duckdb_vs_legacy_fts5_tokenization_parity`. For each
entry currently in `_KNOWN_DRIFT`, either:
- the drift closed → delete the entry, so the test now requires exact
  DuckDB=FTS5 agreement on that query; or
- the drift persists for a documented reason → keep the entry with an
  updated mechanism note.

Add `R&D` recall to the test: seed a doc containing the literal `R&D` and
assert the lexical leg finds it. This is the assertion that was missing.

### S3 — promote the fallback watch to an advised leg

`perf_smoke.py` currently prints `NOTE: N query/engine pair(s) took the MACRO
fallback` but always exits 0. After S1/S2, add a bounded assertion: on the
six `_EDGE_QUERIES`, no query may take `macro` on the doc/note/script legs.
Keep it advisory (non-blocking) — a future analyzer change should surface
as a warning, not a gate failure.

## 4. Acceptance criteria & shakedown

1. `pytest tests/test_fts_topk_parity.py -q` passes with `_KNOWN_DRIFT`
   reduced or retired per S2; 3 consecutive runs (tokenization is
   deterministic but the template requires repeat-count for parity work).
2. `fts_topk(tokens=fts_query_tokens(q))` returns mode `topk` for all six
   `_EDGE_QUERIES` on doc/note/script — verified on live stores, read-only.
3. `R&D` lexical recall on doc: the 3 rows with literal `R&D` are found by
   the bm25 leg, not only by the vector leg.
4. `perf_smoke.py` reports 0 macro fallbacks over its query bank (was 2).
5. `make lint-audit`, `make types`, `make static-checks`, `ruff format
   --check` stay green.
6. Targeted tests only: `pytest tests/test_fts_topk_parity.py tests/test_doc_query_duckdb.py tests/test_note_query_duckdb.py tests/test_script_query_duckdb.py -q`.

Query-visible semantics change here (which docs a search returns), so the
audit question is whether `ontology_eval_gate.py` covers search: it does
not — its 239 questions read the graph/entity/cohort tables, not FTS. The
result contract is instead pinned by S2's parity test. No eval-gate run is
needed; record that explicitly in the completed entry.

| Projected outcome | Today | After |
|---|---|---|
| punctuated queries on top-k | 0% (all macro) | 100% resolve |
| `R&D` bm25 recall on doc | 0 of 3 rows | **see amendment — not achievable** |
| macro scan cost per query | 12–28 ms | avoided (top-k 1–8 ms) |
| `_KNOWN_DRIFT` entries | 2 | re-documented (both persist, mechanism updated) |

## 4b. Outcome (measured 2026-11-18, this box — S1+S2+S3 landed)

**Amendment to criterion 3 (R&D recall): the goal is NOT achievable at the
query side, and the proposal's premise for it was wrong.** The fixture
probe (same `PRAGMA create_fts_index('<table>','<id>','<col>')` call the
rebuilds use, default options) proved the analyzer drops 1-character
tokens: `R&D` → `r`/`d` → both gone; likewise `a.b`, `x/y`, `3M`. `R&D`
therefore has no lexical representation in ANY store — no query tokenizer
can recover it, and §2's row "`fts_topk(tokens=['r','d'])` resolves" was
an artifact of the live doc dict carrying bare `r`/`d` from other rows
(resolving them would be an `r OR d` scan over unrelated content, i.e. an
overmatch, not recall). Criterion 3 is replaced by: the splitter drops
1-char tokens (`fts_query_tokens_duckdb("R&D") == []`, pinned in
`TestAnalyzerMatchedTokenizer`), the drift entry stays with an updated
mechanism note, and the 3 literal rows remain reachable via the vector
leg / ILIKE. Criterion 2's "all six edge queries resolve on
doc/note/script" is likewise amended: resolution is dict-honest per
store — a query resolves wherever its parts exist (`cattle-feed` topk on
doc+note, macro on script whose corpus has neither term) and takes the
macro safety net otherwise.

| Criterion | Result |
|---|---|
| S1 splitter | `fts_query_tokens_duckdb(q)` in `helpers/core/duckdb_shared.py` (mechanical rules only: separator split, letter/digit split, digit-only and 1-char dropped; no stopword list — the dict ladder owns resolution). Wired at all five top-k call sites: `rebuild_doc_search.py:1797`, `rebuild_script_search.py:2500`, `note_query.py:200`+`:211`, `convo_query.py:245`, `perf_smoke.py:123`. The orphaned whitespace `fts_query_tokens` deleted (no callers left; `fts_match_expr` tokenizes inline). |
| Parity oracle | `tests/test_fts_topk_parity.py` 4 passed UNCHANGED result sets (cattle-feed's rows are identical macro→topk; only the path and ranking changed). `_KNOWN_DRIFT` entries kept with updated mechanism notes. New `TestAnalyzerMatchedTokenizer` (2 tests) pins the path change and the drop rules. |
| Live perf (full search fn, median of 7) | doc `cattle-feed` 49.5 ms/7 rows (macro overmatch) → **12.5 ms/10 ranked**; note `cattle-feed` 147.2 → **54.3 ms**; note `state-of-the-art` 162.5 → **71.9 ms**; doc `state-of-the-art` 43.9 → 42.9 ms (macro both ways — `of`/`the` absent from the doc dict); script unchanged within noise (terms absent). |
| S3 advisory | `perf_smoke.main()` macro bank is now an allowlist with per-pair reasons (`script/aquaculture`, `script/to` — dict-absent terms). Unexpected entrant → `WARNING (macro regression)`; allowlisted pair resolving → `NOTE retire the entry`; else `OK: macro bank matches the allowlist`. Still exits 0 (advisory contract in `tests/run_gate_report.py:431`). Live: 14/16 topk, 2/2 allowlisted macro, `OK`. |
| Tests | parity+shared+doc+convo+fts+script 88 passed; note_query+note_query_duckdb 29 passed. |
| Lint/format | ruff format+check clean on all 7 touched files. |

`ontology_eval_gate.py` does not cover FTS search (its 239 questions read
graph/entity/cohort tables) — unchanged from §4; the parity test remains
the contract.

## Appendix (continued) — S1/S2/S3 measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-18 | fixture: `abc R&D a.b x/y FY2026 3M co_mention US-it Q4FY25 ab cd` | dict = `['ab','abc','cd','fy','mention']` | pins: 1-char dropped, letter/digit split, digit-only dropped |
| 2026-11-18 | live doc dict probes | `r`,`d`,`us`,`co` present (standalone corpus tokens); `of`,`the`,`2026` absent | stopword list NOT embedded in splitter |
| 2026-11-18 | `fts_query_tokens_duckdb` bank | `cattle-feed→['cattle','feed']`, `R&D→[]`, `FY2026→['fy']`, `don't→['don']`, `3M→[]`, `market share→['market','share']` | |
| 2026-11-18 | live modes doc/note/script | cattle-feed topk/topk/macro; R&D macro×3; state-of-the-art macro/topk/macro; FY2026 topk×3 | dict-honest per store |
| 2026-11-18 | full-search A/B (median×7) | doc 49.5→12.5 ms (7→10 rows); note 147.2→54.3, 162.5→71.9 ms; doc state-of-the-art 43.9→42.9; script ~flat | §4b table |
| 2026-11-18 | `perf_smoke.py` | 16 pairs: 14 topk, 2 allowlisted macro; `OK: macro bank matches the allowlist`; rc=0 | S3 landed |

## 5. Risks

- **The exact analyzer token stream is an internal DuckDB detail** (G3:
  native FTS is macro-only, shadow layout internal, duckdb unpinned). A
  hand-written splitter could drift on upgrade. Mitigation: derive the
  splitter from the seeded fixture's `dict` terms during development, and
  make the parity test the contract — an upgrade that changes the analyzer
  flips S2 red and forces re-derivation rather than silent bad rankings.
- **Splitting changes ranking for multi-token queries** (OR semantics over
  more terms can admit more rows). Mitigation: the parity test compares
  file-level hit sets against FTS5 on the same bank, so a ranking change
  that breaks the contract is caught before merge.
- **Single-letter tokens (`r`, `d`) can overmatch.** Mitigation: S1's
  splitter must apply the same minimum-length / stopword rule the analyzer
  applies; verify against `dict` on the fixture before finalizing.

## 6. Non-goals

- **No change to `fts_match_expr` or the legacy FTS5 surface.** It keeps its
  quoted-OR semantics and its own results.
- **No change to the `note_search_carve_out` / HNSW paths.**
- **No re-opening the macro fallback itself** — it stays as the safety net
  for genuinely unresolvable tokens and schema drift.
- **No change to `--check` modes** — that is the other proposal
  (`search_check_corpus_walk_s3.md`).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-18 | `fts_query_tokens` on 6 edge queries | `cattle-feed → ['cattle-feed']`, `R&D → ['R&D']` | whitespace-only split |
| 2026-11-18 | dict hyphen/amp census (doc/script/note) | 0/11942, 0/5175, 0/31390 | analyzer splits punctuation |
| 2026-11-18 | `fts_topk(tokens=['cattle-feed'])` | `(None,'macro')` | |
| 2026-11-18 | `fts_topk(tokens=['cattle','feed'])` | top-k, 10 rows, 32.1 ms | |
| 2026-11-18 | `fts_topk(tokens=['r','d'])` | top-k, 10 rows, 14.3 ms | |
| 2026-11-18 | `ILIKE '%R&D%'` on doc_search.content | 3 rows | ground truth |
| 2026-11-18 | `match_bm25(id,'R&D')` on doc | 0 rows, 12.8 ms | recall gap |
| 2026-11-18 | `match_bm25(id,'cattle-feed')` on doc | 159 rows, 27.7 ms | overmatch vs 3+165 |
| 2026-11-18 | `fts_topk(tokens=['market'])` on doc | 8.2 ms (top-k) | speed baseline |
| 2026-11-18 | `perf_smoke.py` | 2 macro pairs: script/aquaculture, script/to | live fallback |
| 2026-11-16 | `tests/test_fts_topk_parity.py` `_KNOWN_DRIFT` | cattle-feed, R&D | recorded drift |
