---
title: "Quadratic regex sweep and derive/maintenance cleanups: 18 O(n²) `\\s+`-anchored patterns fixed repo-wide by a consuming `(?<!\\s)\\s+` guard, one-walk note rebuild, prefiltered metric labels, accumulated-count section lines, closeness advisory fix"
status: executed
filed: "2026-11-10"
executed: "2026-11-10"
completed_md: "502"
area: "helpers/graph/extract_relations.py, helpers/core/parse_newsletter.py, helpers/graph/derive_insights.py, helpers/graph/derive_events.py, helpers/core/entity_identity.py, helpers/graph/triage_pending_quotes.py, helpers/maintenance/related_party_sync.py, helpers/maintenance/rebuild_note_search.py, helpers/graph/algorithms.py, tests/test_fuzz_regex.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Quadratic regex sweep + derive/maintenance cleanups

**Date:** 2026-11-10 · **Status:** EXECUTED · **Completed:** completed.md #502 · **Area:** `helpers/graph/extract_relations.py`,
`helpers/core/parse_newsletter.py`, `helpers/graph/derive_insights.py`,
`helpers/graph/derive_events.py`, `helpers/core/entity_identity.py`,
`helpers/graph/triage_pending_quotes.py`, `helpers/maintenance/related_party_sync.py`,
`helpers/maintenance/rebuild_note_search.py`, `helpers/graph/algorithms.py`,
`tests/test_fuzz_regex.py`

**Headline:** S6 is the reason this proposal exists — an AST sweep of all 366 `re.compile`
sites under `helpers/` found **17 O(n²) patterns** of the shape `\s+(?:…)$`, plus `SECTION_RE`
(a different mechanism: `^\s*` crossing newlines under `re.MULTILINE`) — **18 patterns rewritten
in total**. Three are
reachable end-to-end on newsletter text and stall for **2.6–8.5 s** (unbounded on longer
runs). The current vault does not trigger them; the OCR/text ingest path can. S1–S5, S7 are
the low-hanging perf/observability cleanups that came out of the same hunt (≈0.4 s of
measured wall-clock upside — see §3).

## 0. Context — what this is NOT

This started as "residual perf after the 2026-08/09 landings" and the two audit passes changed
its centre of gravity: the wall-clock slices are small and measured, while the regex sweep found
a real latent stall. Both are recorded here because they came from one hunt.

`doc/improvements/archive/tooling/perf_improvs.md` (2026-08-10, P0–P3) is **stale**. All four
items are landed and verified in the current tree:

| Doc claim | Current state |
|---|---|
| P0 NetworkX closeness 8.8 s | scipy lane: `_run_closeness` (`helpers/graph/algorithms.py:1044-1047`) → `_run_scipy_lane`, routing `"closeness_centrality": "SCIPY"` (`helpers/graph/scipy_bridge.py:159-161`), gen-keyed cache (`:234-243`). NOTE: the persistent `v_centrality_closeness` table is deliberately **dropped** for lane-served families (`helpers/graph/query.py:2205-2208`); `v_centrality_closeness_full` (`:2273-2279`) is stamped but never read by `closeness_centrality` — see S5 |
| P1 `derive_events` uncompiled `re.split`, double `splitlines` | `_SENTENCE_SPLIT_RE` compiled (`helpers/graph/derive_events.py:301`), single `splitlines` + `seen` set (`:437-469`), shared `windows` (`:842-886`) |
| P2 `rebuild_note_search` inline whitespace sub + O(N²) rows | `_WS_RE` (`helpers/maintenance/rebuild_note_search.py:167`), `_rows_by_path` dict (`:669`, `:985`) |
| P3 `derive_insights` inline patterns | compiled at `helpers/graph/derive_insights.py:380-421`, `:1375-1417` |

Likewise **no** quadratic `buf += chunk` concats (only numpy `hist += chunk_hist` in
`helpers/graph/stats.py:149`), **no** `(.+)+`/`(.*)*` nested quantifiers (repo-wide `rg` for
`(\.\+)\+|\(\.\*\)\*|\(\.\+\)\*|\(\.\*\)\+` returns only this proposal's own prose; the
nearest live nested-`+` shape is `helpers/analytics/bili_analytics.py:496`), and **no**
per-line `re.compile` in hot loops (AST sweep: the single `re.compile` inside a loop or
comprehension repo-wide is `tests/api_contract.py:158`, not production). The CPython `re`
cache (512 entries) means static inline patterns compile once; hoisting them saves ~1–5%
lookup and is not worth a slice on its own.

The 2026-11-10 `BOLD_LINE_RE` fix (#501, `doc/improvements/archive/testing/bold_line_guard_failing_path.md`) is the
template for S6: the guard was green with the exponential pattern installed.

**Wall-clock context (gate, last runs):** `derive_insights` 8.14 s / 9.0 s budget (90%
utilization), `derive_events` 3.29 s / 5.0 s, `pdf_pipeline_local` 3.17 s / 7.0 s, latest `qa`
run 4149 failed only `test_ruff_format_footprint_clean` (unrelated to this work).
Expect corpus-growth, not regression: derive_insights was 1.6 s at the 2026-08-10 doc.

**Measured locally (2026-11-10, dry-run):** the `derive_insights` leg is **7.23 s / 7.44 s**
(`python3 helpers/graph/derive_insights.py`) against its 9.0 s budget
(`tests/run_perf_benchmarks.py:405` — budget comment at `:146` says "derive_insights 4.8-7.5 s
9.0 scan/render wall (corpus growth); +2 s operator boost 09-26"). Profiled: `scan` 3.84 s
(of which ~3.7 s is `select.poll` worker-pool wait) + `render_notes` 2.14 s; the largest
own-code items are `yaml.get_single_data` 0.354 s, `_scan` 0.250 s,
`edition_index.resolve_editions` 0.217 s tottime / 0.667 s cum, `_metrics_from_db` 0.069 s.
**The slices below total ≈0.4 s of measured upside and do not move this leg** — see §3 for
where the real levers are.

## 1. Slices

### S1 — note_search rebuild: one walk, one fingerprint pass

`_iter_findata_docs()` (`helpers/maintenance/rebuild_note_search.py:359-370`) does
`sorted(FINDATA.rglob("*.md"))` + `_doc_type_for` per call. It has **four** call sites, but
the full and incremental branches are mutually exclusive (`if not incremental:` `:1017` /
`else:` `:1089`), so one apply-run reaches **three** walk sites — rows collection (`:615`),
staleness verdict (`:987`), and one branch-local re-walk (`:1048` full / `:1104`
incremental). A `--check` run reaches two (`:615`, `:987`) and returns before the write
branch. Each re-walk re-hashes the same corpus: `rows_by_path` + `"\x00".join(r[4] for r
in doc_rows)` + `_file_fingerprint()` — blake2b `digest_size=8` over
`f"{title}\x00{sector}\x00{content}"` (NUL-separated, `:685-698`; the docstring at `:696`
mis-says `title|sector|content`). Two hash passes run per apply-run today (staleness
`:986-994` + the branch's own loop) and one per `--check`; the staleness `current_meta` map
(`:986-994`) is consumed **only** by the staleness verdict — the full branch computes its
own `meta_rows` (`:1053-1056`) and the incremental branch its own diff (`:1104-1121`).

Fix:
- Compute `current_meta: dict[rel_posix, (mtime, hash)]` **once** and share it. The row
  tuple is `(doc_type, rel_posix, title, sector, body, embedding, section_title, anchor,
  ticker, edge_context)` — it carries `rel_posix`, **not** `abs_path` — so the fix must
  either thread `abs_path` through `_collect_rows` (`:596-654`) into the row tuple, or
  build the map inside `_collect_rows` where `abs_path` is still in scope.
- Staleness (`:975-1009`), full meta refresh (`:1047-1061`), incremental diff (`:1104-1126`)
  all consume that map. No further `_iter_findata_docs()` calls after row collection.
- Keep the incremental "carried rows re-stat" fast path (`:1110-1117`) unchanged.

**Est. win (measured 2026-11-10):** 2 eliminated full-vault walks (3→1 apply, 2→1 `--check`)
+ 1 eliminated blake2b pass per apply-run. Live: `_iter_findata_docs()` returns 1,398 docs
in **0.033 s** and the blake2b fingerprint over all 1,398 docs costs **0.137 s** → the ceiling
is **≈0.17 s per apply-run** (one walk + one hash pass), *not* the 0.3–0.8 s first estimated.
Corpus today: 1,401 findata `.md` files, 30.1 MB, 19,356 `note_search` rows — it scales with
corpus. **Status: APPLIED 2026-11-10** — `_collect_rows` now returns `(rows, doc_paths)` and
`current_meta` is built once from it; instrumented proof: `--check` walks 2→1, incremental 3→1,
fingerprints 1,674→1,398 on an incremental run. Wall clock cannot resolve 0.19 s because the
apply path is dominated by embedding/vec sync (~141 s on a copied DB), so the counter probe is
the evidence. **Risk:** low; pure refactor, no membership semantics change. **Tests:** `tests/test_rebuild_note_search.py`
(existing full/incremental/check fixtures must stay green); add a `--check` fixture
asserting `index_stale` verdict identical before/after.

### S2 — `_label_from_window`: one pass over the window

`helpers/graph/derive_insights.py:1469-1477` runs up to 29 sequential `pat.search(w)`
over `_METRIC_LABEL_PATTERNS` (`:1436-1466`, exactly 29 entries at `:1437-1465`) on the same
window. `_classify_metric` (`:1489`) calls it at `:1504` (pre-window) and at `:1510`
(post-window) — the second runs **only when the first returned `None`** (`:1505` returns
early), so it is "up to twice per metric", not twice. The pre-window is ≤ 30 chars and the
post-window ≤ `len(value_raw) + 25`, so the searches are cheap but the constant factor dominates.

**Measured (2026-11-10, live corpus):** `extract_metrics` over 1,768 reconstructed company
sections costs **1.879 s**; `_label_from_window` is called **9,551** times for **5,219**
metrics and accounts for **0.191 s** cumulative. Monkeypatching it to a no-op drops
`extract_metrics` from 1.879 s → 1.751 s, i.e. **0.128 s (6.8%)** is the true ceiling for
this slice. Per-call micro-bench: 0.83 µs on a first-pattern hit, 8.25 µs on a late hit
("invest"), 16.86 µs on a window with no label (all 29 patterns searched).

Fix — pick ONE, in order of preference:
1. **Literal prefilter** (mirrors the `_MGMT_VERB_TOKENS` prefilter, `helpers/graph/derive_events.py:195-214`):
   scan `w` once for the distinctive alphabetic tokens across all 29 patterns (most contain a
   rare literal: `aum`, `capex`, `attrition`, `penetration`, …); only run the regexes whose
   prefilter token hit.
2. **Single alternation** with named groups, `|`-joined in `_METRIC_LABEL_PATTERNS` order
   (leftmost-match alternation preserves list priority). Before joining, make
   `_L_INVESTMENT`'s empty alternation branch explicit: `:1394` is
   `r"\binvest(?:ments?|ing|ed|)\b"` — the trailing `|` makes the suffix optional, so bare
   "invest" matches (verified live); it never matches the empty string. Rewrite as
   `r"\binvest(?:ments?|ing|ed)?\b"` to keep the same semantics without the empty branch.

Preserve the two special cases explicitly: `_L_MARGIN`/`_L_EBITDA` → `ebitda_margin` vs
`margin` (`:1472-1473`) runs **before** the list and must stay first. Neither is a
`_METRIC_LABEL_PATTERNS` entry.

**Est. win (measured):** **≈0.13 s** of a 1.879 s `extract_metrics` lane (6.8% of that lane;
~0.02 s of the 7.2 s `derive_insights` leg wall). The 2026-08-10 "0.70 s / 50k searches"
figure in `perf_improvs.md` (same directory) does **not** reproduce — the live corpus makes
9,551 `_label_from_window` calls, not 50k. **Risk:** classification drift. **Tests:** `tests/test_derive_insights.py:1849-1889`
(`_label_from_window`/`_classify_metric`) are the pin set; add a table-driven parity test
enumerating every pattern's positive example from the census comments (`:1387-1392`,
`:1403-1408`) before and after the change. **Eval gate:** not ontology-class (label is a
nullable convenience, not a crosswalk) but the parity table is mandatory.

**Status: APPLIED 2026-11-10** via option 1 (literal prefilter). `_LABEL_PREFILTER` maps every
one of the 31 label patterns to a NECESSARY literal; `_MISSING_PREFILTER` raises at import time if
a future pattern lacks one, so the prefilter cannot silently narrow a match. Measured on the
9,551 real windows: **125.7 ms → 46.0 ms (2.73×, 79.7 ms saved)**; 82% of windows are unlabelled
and now short-circuit. Option 2 was rejected after measurement: a `|`-joined alternation gave
0.1148 s vs 0.1268 s sequential (leftmost-match priority is also a behaviour change). Parity 0
mismatches on all 9,551 windows + every line of 400 corpus files; ~40 new parametrized tests in
`tests/test_derive_insights.py`. Three test expectations were initially wrong in my table, not in
the code: `_L_CAPACITY` precedes `_L_INVESTMENT` ("invested in capacity"), `_L_GROWTH` precedes
`_L_VOLUME` ("volume growth"), and `\bgw\b` in `_L_CAPACITY` precedes `_L_RPO` ("rpo of 3 gw").

### S3 — `extract_quotes` / `extract_metrics`: share the `splitlines` pass

Both walk `section.body` from scratch per section (the 2026-08-10 archive doc measured
2,834 section-yields in `iter_company_sections`):
- `extract_quotes` (`helpers/graph/derive_insights.py:1296`) → `body.splitlines()` (`:1314`)
  + `_LINE_EMPH_RE` unwrap + curly-quote replace loop (`:1315-1323`);
- `extract_metrics` (`:1611`) → `body.splitlines()` (`:1628`) + `_SENTENCE_SPLIT_RE.split`
  per line (`:1632`).

Caveat the fix must respect: the two extractors trim **different** prefixes before splitting
— `extract_quotes` cuts to after the first marker heading (`:1309-1312`), `extract_metrics`
cuts to after the concall heading (`:1624-1626`) — and only `extract_quotes` applies the
emphasis/curly-quote normalization.

Fix: split `section.body` **once** per section, then let each extractor compute its own
start offset into the shared `lines` list (offset from the heading match) and keep its
existing per-line behavior. If the emphasis/curly-quote normalization is also shared, it
must be proven behaviour-neutral for `extract_metrics` (it currently sees raw text) — the
parity fixtures decide.

**Est. win (measured 2026-11-10):** **≈0.012 s** — the two `body.splitlines()` calls per
section cost 0.024 s in total across 1,768 sections (90,034 lines), so sharing one pass saves
half of that. The first estimate (~0.6–1.0 s, 10–15%) was **wrong by ~50×**: the double-walk
cost is dominated by per-sentence regex work inside `_extract_metrics_from_sentence`
(60,016 calls / 1.349 s cumulative; 332,540 `re.Pattern.search` calls), not by the
`splitlines` call. The lane-level lever is therefore `_extract_metrics_from_sentence`, not
the split — see §2. **Risk:** low–medium (quote path
is curly-quote sensitive; metric path currently sees un-normalized text). **Tests:**
`tests/test_derive_insights.py` extract_quotes/extract_metrics fixtures +
`tests/test_fuzz_derive_insights_regions.py`.

### S4 — `heading_line`: `count("\n")` once per section, not per section-yield

`iter_company_sections` (`:745-766`) and `iter_sector_sections` (`:873-906`) compute
`content.count("\n", 0, start) + 1` per yield (`:764` and `:901`) — O(start) C-scan each →
O(sections × doc-len).

Fix: precompute a newline-offset array (`[i for i, c in enumerate(content) if c == "\n"]` or
`re.finditer("\n", content)`) once per `_structural_boundaries`/`iter_*_sections` call and
`bisect_left` per section.

**Est. win (measured 2026-11-10):** **≈0.1 s** over the full corpus — the 20,510
`content.count("\n", 0, start)` calls cost **0.097 s** total (4.7 µs/call) and
`_structural_boundaries` over all 1,401 files costs 0.310 s overall. The first estimate
(~0.5–1.0 s) was **wrong by ~5×**. **Status: APPLIED 2026-11-10** — `_line_starts()` accumulates
`content.count("\n", prev, pos)` per boundary gap instead of one `count` from 0 per section;
measured 480.3 ms → 393.5–398.1 ms (~0.09 s) over 1,401 files / 20,510 sections. A first attempt
using a newline-offset list + `bisect` was **5× slower** (2,498 ms) — the C `str.count` over the
gap wins. Parity: 0 mismatching files (1,768 company + 18,742 sector sections, comparing
`(canonical_name, heading_line)`). **Risk:** none (pure
index math). **Tests:** `tests/test_derive_insights.py` — any test asserting `heading_line`.

### S5 — closeness: the synthetic-edges footgun does NOT exist; the advisory is misleading

The original draft of this slice claimed synthetic `edges=` calls land in an exact-Onager
path costing ~150 s. **Verified false — do not implement the draft's `allow_exact=True`
fail-closed guard.**

`closeness_centrality` (`helpers/graph/algorithms.py:214-260`) dispatches through
`_onager_central` (`:666-682`): for the lane-served family `onager_closeness`
(`_LANE_SERVED_FAMILIES`, `:657-663`) it calls `fn(con, edges=edges, **kw)` — i.e.
`helpers/graph/graph_fallbacks.py:530-549` (igraph `g.closeness(normalized=True)`,
networkx `wf_improved` scale `(reachable-1)/(n-1)`) — and never reaches
`_extension_first` (`graph_fallbacks.py:262-291`) nor the vendored extension twin
(`helpers/graph/onager.py:561`).

Measured live (2026-11-10, `memory/research.db` via `helpers.graph.query.connect`):

| path | result |
|---|---|
| synthetic `edges=` (n=3000 nodes, 9000 edges) | 3,000 values in **1.06 s** |
| DB `closeness_centrality(con)` | 19,130 values in **9.4 s** |
| extension twin `helpers.graph.onager.onager_closeness(con)` | 19,718 values in **137.6 s** (values match the fallback to 6 dp) |

The "≈150–163 s cold" number (`_SLOW_TABLE_HINTS`, `algorithms.py:732-736`) belongs to the
**extension** path, which this code path does not take. So there is no ~144 s synthetic-call
hazard and no reason to gate synthetic calls.

The residual finding is **observability, not speed**: the persistent `v_centrality_closeness`
table is deliberately dropped for lane-served families (`helpers/graph/query.py:2205-2208`),
so `_cached_central_scores` (`:708-727`) always misses and prints
`centrality cache: v_centrality_closeness absent — fresh Onager closeness ≈150-163 s cold; the scipy lane serves it in ~6 s (algorithms.py closeness)`
on every call — quoting a cost this path does not pay. (A separate
`v_centrality_closeness_full` table IS stamped — 19,718 rows — but it is a different universe
and `closeness_centrality` never reads it.)

Fix (**applied 2026-11-10**): `_table_never_stamped()` gates the read on the same
`scipy_bridge.ROUTING` table that makes the stamp drop the table — so a lane-served table is never
queried (no failed SQL, no advisory) and reverting a ROUTING value resumes both stamp and read.
The advisory itself moved to `_LANE_TABLE_HINTS`, now describing the lane instead of quoting a
cold-Onager cost, and fires **once per table per process** (`_ADVISED_TABLES`) instead of on every
call. **Est. win:** one failed SQL round-trip plus a stderr line per centrality call removed;
measured DuckDB closeness is 9.4–10.5 s (not 150 s), harmonic 10.3 s. **Risk:** low — the read was
a guaranteed miss. **Tests:** `tests/test_centrality_cache.py` (rewrote
`test_slow_table_miss_advises_lane` → `test_lane_served_table_is_never_read` parametrized over
closeness/harmonic/betweenness + `test_non_lane_cold_table_still_advises_once`),
`tests/test_centrality_projection.py`, `tests/test_scipy_bridge.py`,
`tests/test_integration_perf.py`, `tests/test_graph_fallbacks.py`.

### S6 — the `\s+`-anchored quadratic family: fix 17 patterns (+ `SECTION_RE`), then guard them

**Third revision (2026-11-10) — the earlier drafts of this slice were wrong twice.** Draft 1
asserted the uncovered patterns were quadratic without measuring. Draft 2 measured six of them,
found ratio ≈2.0, and concluded "all linear". Both drafts only tested *the patterns that draft 1
happened to list*. An AST sweep of **all 366 `re.compile` sites under `helpers/`**, each timed on
a long-whitespace input at n = 1000 → 16000, found **17 genuinely quadratic patterns (ratio
≈4.0 per doubling = O(n²))**, and none of the six originally listed are among them:

| Site | Pattern | n=4000 |
|---|---|---|
| `helpers/graph/extract_relations.py:386` | `\s+\d+(?:\.\d+)?\s+\w+(?:\s+\w+){0,2}$` | 156.6 ms |
| `helpers/graph/derive_insights.py:389` | `\s+(?:large\|larg\|mid\|small\|micro\|nano\|mega)\s*cap` | 231.6 ms |
| `helpers/graph/extract_relations.py:377` | `\s+(?:the\|a\|an\|its\|their\|our\|in\|on\|at\|to\|for\|and\|or)$` | 400.1 ms |
| `helpers/graph/extract_relations.py:2250` | `\s+[a-z]$` | 118.7 ms |
| `helpers/graph/extract_relations.py:357` | `\s+(Limited\|Ltd\.?\|Private\|Pvt\.?\|Company\|Co\.?)$` | 222.4 ms |
| `helpers/core/parse_newsletter.py:95` | `\s+(Limited\|Ltd\.?\|Private\|Pvt\.?)$` | 177.6 ms |
| `helpers/graph/extract_relations.py:381` | `\s*,\s*\|\s+\band\b\s+\|\s+\bor\b\s+\|\s+&\s+` | 314.3 ms |
| `helpers/core/parse_newsletter.py:97` | `\s+(Limited\|Ltd\.?\|Private\|Pvt\.?)$` (dup of `:95`) | 177.9 ms |
| `helpers/maintenance/related_party_sync.py:292` | `\s*\((?:formerly\|formerly known as\|now known as)[^)]*\)\s*` | 30.7 ms |
| `helpers/graph/triage_pending_quotes.py:51` | `(\s+I$\|\]$\|[,;]\s*$\|_\S*$\|\\)` | 30.4 ms |
| `helpers/graph/derive_insights.py:399` | `\s+(Limited\|Ltd\.?\|Private\|Pvt\.?)$` (dup of `:390`) | 178.4 ms |
| `helpers/graph/derive_insights.py:390` | `\s+(Limited\|Ltd\.?\|Private\|Pvt\.?\|Inc\.?\|Corp\.?)$` | 222.4 ms |
| `helpers/core/parse_newsletter.py:129` | `\s+(?:large\|larg\|mid\|small\|micro\|nano\|mega)\s*cap` | 230.2 ms |
| `helpers/graph/derive_events.py:272` | `\s+(?:from\|with\|and\|…\|that)$\|^(?:the\|a\|an)\s+` | 539.0 ms |
| `helpers/core/entity_identity.py:46` | `\s*\*+\s*$` | 128.8 ms |
| `helpers/graph/extract_relations.py:366` | `\s+(Limited\|Ltd\.?\|Private\|Pvt\.?)$` (dup of `:357`) | 182.9 ms |
| `helpers/graph/extract_relations.py:361` | `\s+(?:Corp(?:oration)?\|Group\|Holdings\|Industries)$` | 174.5 ms |

**Mechanism:** the leading `\s+` is *greedy and unattached*. `re.search` retries the pattern at
every start position; at each of the n whitespace positions the engine consumes the remaining
whitespace run (O(n)) and then fails the required suffix. n attempts × O(n) = O(n²). It is not
backtracking within a single attempt, so **a possessive quantifier does not fix it** (measured
`\s++` at n=8000: 88 ms vs the original 2275 ms — better constant, still ratio ≈4.0 per
doubling).

**Fix (verified, second form — the first one was wrong):** the obvious rewrite to a bare
zero-width lookbehind `(?<=\s)(?:Limited|Ltd\.?|…)$` **is linear but changes behaviour**: it no
longer consumes the separator whitespace, so `.sub()` leaves a trailing space
(`"Graphite India Limited"` → `"Graphite India "` instead of `"Graphite India"`) and `.split()`
yields `["Graphite India ", "Limited"]`. That silently breaks any call site that does not
`.strip()` — the corpus parity harness found **166 mismatching cases** across 19,709 samples
(66 `_LIST_CHUNK_SPLIT_RE`, 57 `_TRAILING_NUM_UNIT_RE`, 42 `FOOTNOTE_RE`, 1 `_PAREN`), including
real damage: `"Chip designers, foundries, memory manufacturers, and semicon"` lost its list
separator and `"# The Chatter: Decoding Capex, Capacity, and Consumer Trends"` split differently.

The form that both is linear **and** preserves the matched span is a `(?<!\s)` guard *before* the
greedy run:

```text
(?<=\s)…        # linear, but span excludes the separator  -> behaviour change
(?<!\s)\s+…      # linear AND span identical to the original -> use this
```

`(?<!\s)` pins each `re.search` attempt to the first character of a whitespace run (a position
preceded by a non-whitespace character), so a run of n spaces yields **one** attempt instead of n;
the `\s+` then still consumes the run as before. Measured n=4000 vs n=8000: ratio 1.89–2.39 on all
18 patterns, `super-linear: []`, versus 3.89–4.34 (O(n²), stalls to 8.5 s) before. **Parity:
0 mismatches** on 19,709 corpus-derived + adversarial samples, each compared through the method its
own call site uses (`sub().strip()`, `sub().strip(" .,;:")`, bare `sub()`, squash-and-strip,
split-and-strip, search-and-slice, the `_clean_span` loop-until-stable, `findall`, boolean gate).

Two patterns needed the `|^` convenience restored rather than a plain `(?<!\s)` prefix, because the
old leading `\s*` could also match at string start: `entity_identity.FOOTNOTE_RE` →
`r"(?<!\s)\s*\*+\s*$"` and `related_party_sync._PAREN` → `r"(?<!\s)\s*\(…\)\s*"`. `FOOTNOTE_RE`
also must not become a bare `r"\*+\s*$"` — that would strip the leading `**` of `"**Acme"`.

`SECTION_RE` is a different mechanism and a different fix: its `^\s*` crossed newlines under
`re.MULTILINE`, so it became `r"^[^\S\n]*#{1,3}…"` (`[^\S\n]` is the same horizontal whitespace but
cannot cross a newline).

**Reachability — confirmed live, all three end-to-end:**

| Path | Trigger | Cost |
|---|---|---|
| `parse_newsletter.extract_companies` → `_CAP_CUT_RE.search(name)` (`:300`) | heading `## Foo` + 8000 spaces + `Bar` (internal run survives `.strip()`) | **2616 ms** |
| `derive_insights._canonicalize` → `_CAP_CUT.search(name)` (`:469`) | same heading shape | **2642 ms** |
| `derive_events._clean_span` → `_SPAN_TRIM_RE.sub` (`:284`) | captured span with an 8000-space run | **8530 ms** |

Corpus exposure **today is nil**: no run of ≥20 whitespace chars anywhere in the 1,401
`findata/*.md` (max newline run 8). The hazard is the OCR/external text path —
`helpers/pdf/pdf_conv_md.py` → newsletter markdown → `parse_newsletter` — where whitespace runs
are an OCR artifact. So this is a **latent-crash fix + guard**, not a wall-clock win.

**Guard design (fixed by the above):** the #501 failing-path ladder stays the instrument, but the
input ladder must start at a size where the quadratic is *visible*. The existing `SECTION_RE`
guard is the cautionary example: `tests/test_fuzz_regex.py` caps at `max_size=400`, where
`SECTION_RE` costs 1.49 ms — 524 ms at 8000 newlines is invisible to it. Ladder sizes ≥ 4000,
median-of-5, SIGALRM cap, assert `t(2n)/t(n) ≤ 2.6`. Also still worth guarding although linear
(regression net only): `_SPEAKER_NCT_RE` (`derive_insights.py:593`), `_PERSON_RE`
(`derive_events.py:193`), `_LINE_EMPH_RE` (`:1103`), the `_L_*` family (31 constants, 29 in
`_METRIC_LABEL_PATTERNS`, `:1375-1417`), the pdf_local constants (`:60-78`; only `BOLD_LINE_RE:76`
is covered today), the 19 named `derive_events` constants (`:116-302`), and
`bili_analytics.py:496`.

Fix `_L_INVESTMENT` (`derive_insights.py:1394`, `ed\|)` — the empty branch lets bare "invest"
match; it never matches empty) while in the file.

Note: the fuzzed `IMG_BLOCK_RE` in `tests/test_fuzz_regex.py:112,:162` is a **different**
constant — it comes from `helpers/pdf/capture_newsletter_images.py:51` (the fuzz file's own
header cites `:43` — stale), not from `pdf_local.IMG_REF_RE`.

**Status: APPLIED 2026-11-10** — 18 patterns across 7 files (`extract_relations.py`,
`derive_insights.py`, `derive_events.py`, `parse_newsletter.py`, `entity_identity.py`,
`related_party_sync.py`, `triage_pending_quotes.py`). Ladder 1.89–2.39; parity 0/19,709; guard
added to `tests/test_fuzz_regex.py` and proven to AND on the old form (4k 512.9 ms → 8k 2034.1 ms,
ratio 3.97) and PASS on the shipped form (0.32 → 0.69 ms, ratio 2.13).

**Est. win:** none directly (a 2.6–8.5 s stall per adversarial document is prevented). **Risk:**
low — mechanical pattern rewrite, with per-call-site parity measured rather than assumed. **Tests:**
`tests/test_fuzz_regex.py` (3 new parametrized guards + a span-drift pin), plus the touched
modules' suites: 738 passed.

### S7 — trivial hoists (do only if already touching the file)

- `helpers/graph/derive_insights.py:478-496` (`_canonical_metric_value`) — 4× inline `re.sub`
  (`:490` dash fold, `:491` whitespace collapse, `:492` hyphen range, `:493` percent);
  `:491`'s `\s+` duplicates `_WS_CANON_RE:398`.
- `helpers/graph/derive_events.py:740-776` (`event_key`) — 3× inline `re.search` (`:743`
  `20\d{2}`, `:744-749` month alternation, `:772` `\d+`); compile as module consts.
- `helpers/graph/derive_events.py:493-494,534-535,594,667-668` — 4 extractors re-`lower()` the
  same windows. The source comment at `:492` cites "89K windows" (2026-09-01); measured live
  (2026-11-10) `_iter_bullets` yields **232,887** windows over `findata/Companies` (1,213 files,
  1.4 s) and **434,633** over all 1,401 `findata/*.md` (3.3 s) — **applied 2026-11-10**:
  `_windows_lowered()` yields `(window, window.lower())` and `extract_from_prose` materialises it
  once for all four extractors (signature changed to `list[tuple[str, str]]`). Measured on
  117,203 windows: 4 passes 171 ms → 1 pass 73 ms; parity 0 mismatches.
- `helpers/graph/triage_preannotate.py:264,685,756` — greedy `\[.*\]`/`\{.*\}` with `re.S` →
  `.*?`. (`:384` is a **different**, already-lazy pattern —
  `re.findall(r"<item><title>(.*?)</title><link>(.*?)</link>", xml, re.S)`.)
- `helpers/graph/triage_preannotate.py:362-365` — 3× inline `re.search` per RSS `<item>`
  (`<title>`/`<link>`/`<description>`); one `findall`/single pass (`_parse_bing_news_rss`;
  network-bound, tiny).
- `helpers/graph/derive_events.py:453-461` (`_iter_bullets`, `:437-469`) — still two `for`
  loops over the same `lines` list (non-blank yield, then sentence-split yield with
  `_ACQUISITION_SENSE_RE`/`_MODE_SENSE_RE` gate per line). **Applied 2026-11-10**: the stripped
  list is built once and zipped back with the raw line (each line stripped once instead of
  twice); the passes themselves are KEPT because yield order is contract — `_dedup` picks the
  longest quote, so whole lines must precede sentence splits. Measured: 3,265 → 3,210 ms over
  the corpus (0.055 s), parity 0 mismatches on 434,633 windows across 1,401 files. The stale
  docstring ("Now iterates once") is replaced with the actual contract.
- `helpers/graph/derive_insights.py:644-645, 864, 3242, 3244, 3246, 3274, 3276, 3561` — 9× inline
  `re.sub` on heading/symbol strings (`:864`'s `\s+` duplicates `_WS_CANON_RE`).
- `helpers/pdf/pdf_local.py:173-174` — inline `re.search(r"^#{1,6}\s+(.+)$", text, re.M)`
  duplicates `HEADING_LINE_RE:68` `r"^(#{1,6})\s+(.+)$"` minus the hash capture group.
  **Applied 2026-11-10**: hoisted to `_H1_SEARCH_RE` (same shape, `re.MULTILINE`, group 1);
  `HEADING_LINE_RE` itself is un-flagged because every other use is `.match()` on one line.
- `helpers/maintenance/rebuild_note_search.py:179-185` (`_clean_body`) — 4 sequential subs on a
  **section** body (sections are split first at `:520-530`); combine into one alternation pass.

**Est. win:** <0.1 s aggregate. **Risk:** negligible. **Tests:** existing suites.

**Status: PARTIALLY APPLIED 2026-11-10.** Done: the 4+9 `derive_insights` inline `re.sub` sites
hoisted to module consts (`_VAL_DASH_RE`, `_VAL_RANGE_RE`, `_VAL_PCT_RE`, `_BARE_LEAD_RE`,
`_BARE_TRAIL_RE`, `_TRAIL_SEP_RE`, `_PIPE_I_RE`, `_PAREN_STRIP_RE`, `_AMP_RE`, `_AND_RE`,
`_HEADING_HASH_RE`; three of them — `:492`, `:883`, and one more — now use the existing
`_WS_CANON_RE` instead of re-spelling `\s+`), `event_key`'s 3 searches compiled
(`_EVENT_KEY_YEAR_RE`, `_EVENT_KEY_MONTH_RE`, `_EVENT_KEY_DIGITS_RE`), the four-extractor
`lower()` pass, the `_iter_bullets` re-strip, and the `pdf_local` heading search. Not done
(deliberately): `triage_preannotate` greedy-vs-lazy (`.:*?` changes which JSON blob is picked
when the reply contains prose brackets — needs its own parity test, and it is LLM-reply-bound,
not hot), the RSS triple-search (network-bound), and `rebuild_note_search._clean_body` (4 subs
over a SECTION body; ~0.05 s). 738 + 345 targeted tests green.

## 2. Deliberately excluded

- **P0 NetworkX closeness** — fixed; `algorithms.py:214-260` (scipy lane via
  `_run_closeness:1044-1047` + routing map `scipy_bridge.py:159-161`). The persistent
  `v_centrality_closeness` table is deliberately **dropped** for lane-served families
  (`query.py:2205-2208`), so the disk-cache read at `:249` always misses — see S5.
- **CPython `re` cache hoisting as a goal** — the cache makes static inline patterns compile once;
  hoisting is hygiene, not perf. Only do it for ReDoS clarity or when already editing.
- **`bili_analytics.py:496`** — nested-`+` flagged, but file is a DuckDB/Parquet analytics lane,
  not a per-sentence prose path; guard in S6, fix only if the guard fails.
- **`parse_newsletter.py:90-93` `SECTION_RE`** (`^\s*#{1,3}\s+(.+?)(?:\s*[|·].*)*$`, `re.MULTILINE`) —
  already fuzz-covered (`tests/test_fuzz_regex.py:93,135`), but its guard **cannot see its own
  quadratic** (cap `max_size=400` → 1.49 ms; 524 ms at 8000 newlines). Covered by S6's
  size-4000+ ladder rule; the pattern itself is not rewritten here.
- **`_extract_metrics_from_sentence` (`helpers/graph/derive_insights.py:1584`)** — this, not the
  `splitlines` pass, is the real `extract_metrics` lever: 60,016 calls / 1.349 s cumulative and
  332,540 `re.Pattern.search` calls over 1,768 sections. S3's original 0.6–1.0 s estimate was
  actually this function's cost mis-attributed to the double walk. **Deliberately excluded here:**
  it is a behavior-bearing sentence parser, not a mechanical win — it needs its own proposal
  (measurement + parity plan) rather than a slice bolted onto a cleanup.
- **`exchange_sync.py:127-130,133-137,284-288,307-313,333-337`** (inline `findall`/`sub` per table
  cell, `<[^>]+>` ×6), **`shareholding_sync.py:239-242,498-501`** (duplicate
  `re.findall(r"([A-Z][A-Z_ ]*?)\s*:\s*([^|]+)", desc)` + inline cell-strip subs at `:456-460`),
  **`seed_nic2008.py:118-119,134-136`** (inline regexes — but these are **one-time boundary scans
  per PDF**, not per-line processing) — network/one-shot scripts; inline `findall`/`sub` there is noise.
- **Gate budget bumps** — do NOT move `derive_insights` 9 s or `derive_events` 5 s budgets as a
  substitute for the slices; `make perf` failures from contention (psi avg10 >15%) are a
  different class (see `gate_query timing`).
- **`make perf` / `make qa`** — operator runs gates; agents validate with targeted tests only.

## 3. Order and gates

**Applied order (2026-11-10):** S6 → S1 → S2 → S4 → S5 → S7 (partial). S6 went first because it
is the only slice with a real severity: a 2.6–8.5 s stall (and an unbounded stall on longer
runs) from adversarial or OCR-garbled text. Then S1 → S2 → S4 → S5 → S7, with **S3 dropped**
(≈0.012 s does not justify touching the curly-quote/emphasis contract).

Original ordering rationale — the measured ceilings reorder the wall-clock value: S1 ≈0.17 s,
S2 ≈0.13 s, S4 ≈0.10 s, S3 ≈0.012 s. **S6 is the only slice with a real
severity**: a 2.6–8.5 s stall (and an unbounded stall on longer runs) from adversarial or
OCR-garbled text, so do it before S2 if the caller cares about robustness rather than wall-clock.
S7 last; fold into adjacent edits.
Each slice: targeted `.venv/bin/python3 -m pytest` on the named test files, `make advisory`
is the ceiling for agent runs; full gate arc (`qa`/`perf`) only on operator go per
`doc/procedures/gates.md`. No behavior-visible change except S2 (label parity table);
S5 is now observability-only.

**Honest total:** the measured ceilings above sum to ≈0.4 s against a `derive_insights` leg
wall of **7.2–7.4 s** (budget 9.0 s) — so this proposal does **not** move the perf leg
meaningfully. The leg's actual profile is `scan` 3.84 s (of which ~3.7 s is `select.poll`
worker-pool wait) + `render_notes` 2.14 s, with `yaml.get_single_data` (0.354 s),
`edition_index.resolve_editions` (0.217 s tottime / 0.667 s cum) and `_scan` (0.250 s) the
largest own-code items. The real levers are elsewhere (scan parallelism, YAML parsing,
edition resolution) and are **not** in this proposal's scope.

## 4. Acceptance criteria

- [x] S1: zero `_iter_findata_docs()` calls after row collection in `rebuild()`; `current_meta`
      computed once; `--check` staleness verdict unchanged (instrumented: walks 2→1, incremental
      3→1); `tests/test_rebuild_note_search.py` green.
- [x] S2: `_label_from_window` token-prefiltered; parity table (every pattern's positive example)
      green; `_L_INVESTMENT` empty branch gone; `_MARGIN/_EBITDA` special cases preserved;
      `tests/test_derive_insights.py` green.
- [~] S3: **not applied** — measured ceiling ≈0.012 s; the curly-quote/emphasis contract is not
      worth touching for that.
- [x] S4: `heading_line` via accumulated `str.count` over boundary gaps; 0 mismatching files
      (1,768 company + 18,742 sector sections).
- [x] S5: the misleading "≈150–163 s cold" advisory no longer fires on lane-served paths
      (`_table_never_stamped` short-circuits before the SQL and the hint); the synthetic-`edges=`
      test files stay green.
- [x] S6: all 18 quadratic patterns rewritten to the **`(?<!\s)\s+`** consuming form with
      per-call-site parity (0/19,709); size-4000+ failing-path ladders added asserting
      `t(2n)/t(n) < 2.6`; the reachability paths re-measured (2616/2642/8530 ms → sub-ms at the
      same input); `_L_INVESTMENT` fixed; `tests/test_fuzz_regex.py` 68 passed. NOTE: the earlier
      `(?<=\s)` lookbehind form in this doc is **superseded** — it drifts 166/19,709 spans.
- [x] S7: partial by design (see its status block); the three skipped items are documented.
- [x] No gate budgets changed; no unrelated diffs.

## 5. Provenance

2026-11-10 session. Audits by two background subagents, claims re-verified against live source
before filing. **A second verification pass (2026-11-10, three read-only explore subagents +
direct re-reads + live measurements) corrected several claims in the original draft**: the
S1 walk count (3 reachable per run, not 4), the S1 fingerprint separator (NUL, not `|`), the
S2 second-call condition (post-window only on a miss), the S5 premise (the synthetic path is
the fast igraph fallback, **not** the 150–163 s Onager extension — S5 was rewritten as an
observability fix), the `_L_*` family span, the `derive_events` pattern count (19 named
constants / 21 compiles), and the `derive_events` window count (232,887 for
`findata/Companies`, 434,618 for all `findata/*.md`).

**A third pass (2026-11-10) replaced every estimated win with a measured one**, because the
estimates had not been checked against the live corpus:

| Slice | First estimate | Measured | Method |
|---|---|---|---|
| S1 | ~0.3–0.8 s | **≈0.17 s** | `_iter_findata_docs()` = 0.033 s; blake2b over 1,398 docs = 0.137 s |
| S2 | "medium", 50k windows | **≈0.13 s** (6.8% of `extract_metrics`) | no-op monkeypatch: 1.879 s → 1.751 s; 9,551 calls measured |
| S3 | ~0.6–1.0 s | **≈0.012 s** | two `splitlines` = 0.024 s over 1,768 sections |
| S4 | ~0.5–1.0 s | **≈0.10 s** | 20,510 `count("\n",0,start)` calls = 0.097 s |
| S6 | "quadratic candidates" | **17 quadratic patterns found repo-wide** (ratio ≈4.0/doubling); the six first listed measure linear | AST sweep of all 366 `re.compile` sites, n = 1000→16000; 3 reachability paths timed end-to-end |

**A fourth pass (2026-11-10) corrected S6 a second time.** Draft 2's "all measured linear"
conclusion was an artifact of testing only the patterns draft 1 listed. The AST sweep of all 366
`re.compile` sites under `helpers/` found 17 O(n²) patterns of the shape `\s+(?:…)$` /
`\s+(?:…)\s*cap`, none of them in the original list; the mechanism is `re.search` retrying the
unattached greedy `\s+` at every start position, so a possessive quantifier does **not** fix it
(measured still ratio ≈4.0) while a `(?<=\s)` lookbehind does (linear, 3,500× faster at n=8000).
Three end-to-end reachability paths were timed (2,616 ms / 2,642 ms / 8,530 ms); the current
corpus is unexposed (no ≥20-char whitespace run in 1,401 files) — the hazard is the OCR/text
ingest path.

S3's estimate was mis-attributed: the 0.6–1.0 s belongs to
`_extract_metrics_from_sentence` (60,016 calls / 1.349 s cum), which is now explicitly
excluded (§2) as needing its own proposal. The `derive_insights` leg wall was measured at
**7.23 s / 7.44 s** against its 9.0 s budget (`tests/run_perf_benchmarks.py:405`), and the
profiled hot spots are `select.poll` worker wait (3.7 s), `yaml.get_single_data` (0.354 s),
`edition_index.resolve_editions` (0.217 s tottime), `_scan` (0.250 s) — none of them in
this proposal's scope. **This proposal is therefore a cleanup with ~0.4 s of measured upside,
not a perf-leg rescue.**
Wall-clock from `gate_query timing` (runs 4152/4149). Predecessors:
`perf_improvs.md` (2026-08-10), `perf_optimization.md` (2026-08-26, #163),
`../graph/derive_insights_perf.md` (2026-09-05, #208),
`../testing/bold_line_guard_failing_path.md` (2026-11-10, #501). House rules:
`doc/improvements/proposals/README.md`; archived 2026-11-10 per its 7-step checklist
(git mv to `../archive/<topic>/`, unique completed.md number, archive README line,
`make search-fresh APPLY=1` then plain, flip frontmatter status).
