---
title: "Stem-ladder coverage and the memory/git macro-only lexical legs"
status: executed
filed: "2026-11-18"
executed: "2026-11-18"
completed_md: "544"
area: "helpers/core"
---

# Stem-ladder coverage and the memory/git macro-only lexical legs

**Date:** 2026-11-18 · **Status:** PROPOSED ·
**Area:** `helpers/core/duckdb_shared.py`, `helpers/maintenance/rebuild_memory_search.py`,
`helpers/maintenance/rebuild_git_search.py`, `tests/test_fts_topk_parity.py`

## 1. Motivation — the survey that found these

Follow-on to `fts_query_token_parity` (S1 landed 2026-11-18): the same audit
method — pin the index/analyzer's actual behavior, then compare with what the
query side assumes — applied to the remaining lexical surfaces found three
more silent degradations. None raise an error; all return plausible-looking
results.

## 2. Findings (all measured 2026-11-18, this box)

### F1 — `_stem_guesses` covers ~75% of real vocabulary; the rest silently takes the macro detour

`_stem_guesses` (helpers/core/duckdb_shared.py:780) approximates the
analyzer's stemmer with six suffix classes (ies/ing/ed/es/s/e). The real
stemmer (pinned via the same fixture-PRAGMA oracle as proposal 2) is more
aggressive. Probing 400 sampled words from the live doc corpus vocabulary
(15,516 unique words ≥4 chars):

- **101/400 (25%) of real vocabulary cannot resolve on the top-k leg.**
- Morphological classes that ALWAYS miss: `-ation` (generation→`gener`,
  tokenization→`token`), `-ize` (normalize→`normal`), `-al/-ic`
  (critical→`critic`, historical→`histor`), `-ness/-ment`
  (freshness→`fresh`, alignment→`align`), `y→i` (registry→`registri`,
  recovery→`recoveri`, taxonomy→`taxonomi`), gemination (planned→`plan`,
  running→`run`, mapped→`map`).
- Highest-frequency victims in the doc corpus: `registry` (freq 469!),
  `recovery` (206), `taxonomy` (182), `installed` (168), `compression`
  (126), `assertions` (96), `acquisition` (77).
- User-facing: `search_docs("registry dedup")` = **59.7 ms** macro detour
  vs 13.9 ms for a fully-resolving query (4.3×), and the macro's loose OR
  ranking replaces the top-k AND-first blend. ANY unresolvable token
  poisons the whole query (`compression ratio` → macro because
  `compression` misses, even though `ratio` resolves).
- The legacy FTS5 path stems correctly **at query time** — so
  duckdb-vs-legacy results diverge exactly on these words. The parity
  test never caught it because its edge bank (`hyper-graph`,
  `cattle-feed`, `state-of-the-art`, `R&D`, `FY2026`, `don't`) contains
  **no morphological variants**.
- Correctness is preserved (macro fallback stems properly via
  `match_bm25`); the damage is latency, ranking quality, and
  engine-divergence — the same "silently slower and differently ranked"
  class as the proposal-2 hyphen bug, but hitting 25% of vocabulary
  instead of punctuated queries only.

### F2 — memory_search duckdb leg is macro-only: compound queries collapse to loose OR

`_search_memory_duckdb` (rebuild_memory_search.py:1786) feeds
`fts_match_expr(q)` — the FTS5 quoted-OR syntax — into DuckDB's
`match_bm25`. There is no top-k leg. Measured on `memory/memory_search.duckdb`
(386 rows):

- Quotes are noise to `match_bm25`: `'"search-fresh"'` = `"search-fresh"`
  = `search fresh` = **222/386 rows (57%)** — union(search 187, fresh 150).
  The compound phrase intent is silently lost; ranking is dominated by
  generic single words.
- `'"memory_remember"'` → 155 rows (40%); `'"cattle-feed"'` → 37 rows
  (the word "cattle" appears in no memory row — it's all loose OR).
- No pollution from the OR literal itself (`match_bm25('or')` = 0 —
  stopword), so results are "plausible garbage", not visibly broken.
- `fts_main_memory_search` has a standard dict (6,272 terms, 0 hyphen
  terms) — `fts_topk` can serve it unchanged; kind/history ride
  `where_extra` like doc_type does for notes.

### F3 — git_search duckdb leg: same macro-only shape, worse proportionally

`_search_commits_duckdb` (rebuild_git_search.py:1742) has the identical
seam. Measured on `memory/git_search.duckdb` (595 commits):

- `'"search-fresh"'` → **493/595 = 83% of all commits**; `"worktree
  merge"` → 249/595 (42%); `"bili namespace"` → 123/595 (21%).
- `git_query`'s rare-literal/identifier router mitigates only
  identifier-shaped queries; prose/compound queries get the loose-OR page.
- Dict is standard (19,190 terms) — same top-k routing applies.

### Checked and NOT broken (for the record)

- `_like_pattern` (convo_query.py:753) escapes `\`, `%`, `_` correctly
  under `ESCAPE '\'`.
- `fts_topk_count` shares the ladder/tokens with `fts_topk` — no
  count-vs-page divergence.
- note `embed_cache` 19,093 rows vs note_search 19,356: keyed by content
  hash, not a 1:1 mirror — not a staleness signal.
- The semantic matrix keys carry `path#anchor` (no row-index alignment):
  a stale matrix loses new notes, never misattributes rows.

## 3. Design

### S1 — bring `_stem_guesses` to measured parity with the stemmer

Extend the ladder with the classes the oracle proved: `y→i` (incl.
`-ly`→`li`), `-ation`/`-ization` strip (to the `-at`-less stem), `-ize`/`-ise`
strip, `-al`/`-ical` strip, `-ness`/`-ment` strip, geminated-consonant undo
(`planned`→`plan`, `running`→`run`). Every new rule must be derived from the
fixture oracle, not English intuition — and the oracle test (fixture-indexed
vocabulary bank → assert ladder hits) becomes part of
`tests/test_fts_topk_parity.py` so an analyzer change flips it red. Keep the
macro fallback untouched; an over-guessing ladder is caught by the existing
"never silently narrows recall" contract (wrong stem guess = macro, not wrong
results).

### S2 — route memory through the top-k leg

`_search_memory_duckdb`: resolve `fts_query_tokens_duckdb(q)` via
`fts_topk(fts_table="fts_main_memory_search", base_table="memory_search", ...,
where_extra=<kind/history>)`; fetch full rows by id for the page (same flow as
doc); fall back to the existing macro block unchanged when mode == "macro".
The sqlite FTS5 branch (:1368) is untouched — legacy surface, own semantics.

### S3 — route git through the top-k leg

Same pattern for `_search_commits_duckdb` (id column `id`, base
`git_search`; since/path filters become `where_extra`).

### S4 — pin the new surfaces

- Parity test edge bank gains morphological variants (`registry`,
  `compression`, `tokenization`, `planned`, `freshness`) — the bank that
  would have caught F1 years ago.
- `perf_smoke._STORES` gains memory + git engines (it currently covers
  doc/note/convo/script only — the two macro-only stores were invisible to
  the fallback watch). Their allowlisted macro pairs are pinned with
  reasons.

## 4. Acceptance criteria

1. Ladder-vs-oracle probe on the same 400-word sample: miss rate ≤5%
   (from 25%); the class bank (`-ation`, `-ize`, `-al/-ic`, `-ness/-ment`,
   `y→i`, gemination) resolves 100%.
2. `search_docs("registry dedup")` median ≤ 20 ms (from 59.7) and mode
   topk on both tokens.
3. memory/git compound queries resolve on topk (e.g. `search-fresh` on
   git returns the search-fresh-touched commits ranked by BM25 over the
   postings, not 83% of the table).
4. Parity test passes with the extended bank; `perf_smoke` covers all six
   engines and reports `OK` against the allowlist; rc stays 0.
5. Targeted tests only; `ruff format --check`, lint, md-lint green.

Query-visible semantics change (ranking on morphological queries), same
audit note as proposal 2: `ontology_eval_gate.py` does not cover FTS; the
parity test is the contract.

## 4b. Outcome (measured 2026-11-18, this box — S1+S2+S3+S4 landed)

All four slices landed same-day; 221 targeted tests green.

| Slice | What landed | Measured outcome |
|---|---|---|
| S1 ladder | `_stem_guesses` (duckdb_shared.py) replaced by an iterative depth-3 superset generator: `_is_vowel_char`/`_porters_measure`/`_undouble_consonant`/`_stem_step` with the measured class tuple (ies→{i,y}, y→{i,∅}, ed/ing+e-restore, m>1 strips incl. ization/ation/ency/ence/ility/ity/ical/ment/ness(m>0)/ize/ate/ator/al/ic/ative/ibility/ability/able/ible/ly/er/ism/ent/ive/ful(m>0)/ous/ivity, ility→stem+l, ion s/t-guarded, final-e drop) | battery 183 oracle words: 0 misses (legacy 106); live 400-word corpus sample (seed 42): **7/400, all analyzer stopwords + 1 proper noun — 0 real-vocab misses** (legacy 112/400 = 28%). Production ladder verified candidate-set-identical to the validated simulator. |
| S2 memory topk | `_search_memory_duckdb`: `fts_topk(fts_main_memory_search, memory_search)` fast path, kind/history as `ss.kind` `where_extra`; 7-col page fetch by id; macro block unchanged | `search fresh` / `memory remember` / `cattle feed` all serve topk (21–44 ms, ranked 10) — was 222/386-row loose OR |
| S3 git topk | `_search_commits_duckdb`: same pattern; since/path requalified to `ss.adate`/`ss.files` (LIKE escape kept); routed_lexical/vector_only logic untouched | `search fresh` / `worktree merge` / `bili namespace` all serve topk (65–138 ms, ranked 10) — was 493/595 = 83% of the table |
| S4 pins | `TestStemLadderParity` in tests/test_fts_topk_parity.py (57-pair `_STEM_BANK`, ladder-candidates test, fixture-PRAGMA end-to-end resolution test, exact-token/parity-edge test); `perf_smoke._STORES` gains memory+git; `EXPECTED_MACRO` allowlist gains `("memory","aquaculture")` | 7/7 parity tests pass; perf_smoke 6 engines: 21/24 topk + 3 dict-honest macro pairs, `OK: macro bank matches the allowlist`, rc 0 |

Acceptance: 1 ✓ (7/400 ≤ 20, real-vocab 0); 2 ✓ (`registry dedup` 13.0 ms
topk); 3 ✓ (memory/git compound queries topk-ranked); 4 ✓ (parity 7/7,
perf_smoke OK rc 0); 5 ✓ (ruff clean ×5 files; md-lint run at close-out).
New oracle facts recorded: `ies→i` direct (studies→studi — the old
ies→y guess never hit); the engine composes rules iteratively
(awareness→aware→awar, typically→typical→typic, activities→activity→activ);
gemination includes ll (controlled→control) but keeps ss (assessed→assess);
`-er` strips at m>1 only (builder/older keep; processor keeps because it
ends `or`); `-ly` strips at m>1 (accurately→accur; quickly keeps `li`);
`-ility` stems end in the -ble l (scalability→scalabl, visibility→visibl,
compatibility→compat via `-ibility`).

## 5. Risks

- **Over-guessing stems admits wrong-token matches.** Mitigation: every
  guess still resolves against the dict (exact term), and the macro
  fallback remains the net; the oracle test pins per-class behavior.
- **top-k routing changes memory/git ranking** for compound queries
  (loose OR → postings BM25). That is the point — the pinned comparison
  table in §2 documents the before state; `git_query`'s identifier/rare-
  literal router is orthogonal and stays.
- **`fts_match_expr` remains the guard** for empty/clean queries in both
  surfaces; only the lexical execution changes.

## 6. Non-goals

- No change to `fts_match_expr` or the sqlite FTS5 surfaces.
- No change to the vector legs or embedder selection.
- No analyzer/index-side changes — query-side only, same as proposal 2.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-18 | fixture oracle: word → `PRAGMA create_fts_index` → dict | `generation→gener`, `critical→critic`, `freshness→fresh`, `registry→registri`, `planned→plan` | stemmer classes pinned |
| 2026-11-18 | `_stem_guesses` vs oracle, class bank | 17/44 miss (see §F1 table) | six-suffix ladder insufficient |
| 2026-11-18 | 400-word sample of doc vocab (seed 42) | 101/400 = 25% miss | F1 sizing |
| 2026-11-18 | `search_docs("registry dedup")` median×7 | 59.7 ms, mode bm25, 7 results | vs 13.9 ms resolving query |
| 2026-11-18 | `match_bm25('"search-fresh"')` on memory | 222/386 = 57% | F2 |
| 2026-11-18 | `match_bm25('"search-fresh"')` on git | 493/595 = 83% | F3 |
| 2026-11-18 | `match_bm25('or')` on memory | 0 rows | no OR-literal pollution |
| 2026-11-18 | `_like_pattern` escaping audit | correct | not broken |
| 2026-11-18 | `fts_topk_count` ladder audit | same ladder as `fts_topk` | not broken |
| 2026-11-18 | oracle batteries 2–3 (guard pins + residual families) | `ness`/`ful` strip at m>0; `er` m>1 (builder/processor keep); `ly` m>1 (quickly→quickli); `ility`→stem+l; `ivity` (connectivity→connect); `ency` m>1 (currency→currenc, fluency→fluenci via y→i) | probes stem_probe{2,3}.py |
| 2026-11-18 | simulator vs legacy ladder, 183-word battery | new 0 miss / legacy 106 miss | `/mnt/data/tmp/opencode/stem_sim.py` |
| 2026-11-18 | production ladder vs simulator, candidate sets | identical on battery + 400-word sample | `stem_verify_prod.py` |
| 2026-11-18 | live A/B post-landing (median×7) | doc `registry dedup` 13.0 ms topk; memory `search fresh` 25.3 ms topk; git `search fresh` 137.9 ms topk (was 83%-of-table macro) | §4b table |
| 2026-11-18 | perf_smoke 6 engines | 21/24 topk; 3 allowlisted macro (script/aquaculture, script/to, memory/aquaculture — dict-honest), OK rc 0 | S4 |
| 2026-11-18 | note embed_cache 19,093 vs note_search 19,356 | content-hash cache semantics | not broken |
