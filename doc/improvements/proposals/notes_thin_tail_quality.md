---
title: "Feed the thin tail — a five-lane quality program for stub-grade company notes"
status: proposed
filed: "2026-10-28"
executed: null
completed_md: null
area: "findata/** (derived note sections) + helpers/maintenance (enrich_from_yfinance) + helpers/graph (derive_insights, relations)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Feed the thin tail — a five-lane quality program for stub-grade company notes

**Date:** 2026-10-28 · **Status:** PROPOSED ·
**Area:** findata/** derived sections · helpers/maintenance/enrich_from_yfinance.py · helpers/graph/derive_insights.py · relations layer

## 1. Motivation

The company-note corpus is bimodal. The median note is substantive
(~7.4 KB), but a long tail of stubs carries almost no information:
one-paragraph business overview, sector/mcap classification, source
attribution, tags — nothing else. For those entities the graph and the
search index inherit the thinness: no metrics to render, no peers, no
concall voice, nothing for `note_search` vector/bm25 legs to rank on.

Trigger: operator observation 2026-10-28 ("notes are a bit thin").
Census the same day (Appendix A) measured the tail and split it by
*which lane can reach it* — the program below is organized by that
split, not by source. Every lane uses writers and probes that already
exist or were already assessed; no new external scraping surfaces.

## 2. Evidence (measured 2026-10-28, this box)

Corpus shape (`findata/Companies/**`, 1,212 notes):

| Measure | Value |
|---|---|
| Notes < 4 KB ("thin stubs") | 277 (23%) |
| Notes < 8 KB | 670 (55%) |
| Notes with `ticker: null` | 161 corpus-wide; 112 of the 277 stubs |
| Stubs with yfinance profile section | 147 (profile ran; companies genuinely tiny) |
| Stubs, tickered, NO yfinance profile | 21 (never enriched) |
| Stubs holding ≥5 `company_metrics` rows | 27 — of which 25 lack the `## Key Figures (auto)` section (2 already had it) |
| Stubs missing Key Figures / Chatter / Management / Key Insights | 246 / 265 / 241 / 238 of 277 |

DB coverage (`memory/research.db`):

| Measure | Value |
|---|---|
| `company_metrics` rows | 76,172 over 995 entities |
| Entities with `competes_with` edges | 734 |
| NULL `metric_label` rows | 3,119 / 66,895 (4.7%, 476 entities) — measured 2026-09-28, #308 |

Spot-check of the render gap (data held, note still thin): SPML Infra
103 metric rows / 9 competes_with edges; Shakti Pumps 99 / 11; vs
Aeron Composite 0 / 0 (nothing held — a fetch problem, not a render
problem).

Source-capability prior (do not re-audit; `doc/improvements/archive/pipeline/relation_enrichment_sources.md` §4, probed 2026-08-22):
yfinance institutional holders = US-listings only (~14 tickers apply);
Wikidata corporate structure sparse below blue chips for Indian
issuers; Screener.in scraping rejected; Google Finance gray zone;
**local embeddings called "the underrated source"** (vectors held for
all 1,314 entities; `semantic_peer` spec'd, never landed).

Interpretation: thinness is *multi-causal* — for ~10% of stubs it is a
pure render gap; for ~60% a fetch gap on reachable tickers; for ~40%
an unreachable-ticker problem that no financial API can fix. No single
lane fixes the tail; hence five lanes.

## 3. Design

Slices are ordered by value-per-effort; each independently landable.
S1 is the trigger slice and starts first. S2–S5 sequence after S1's
verdict (S1's fetch results re-shape the stub census S2 works from).

### S1 — Reachability backfill (render-held data → fetch tickered → resolve untickered)

Three sub-passes, strictly in this order (each shrinks the next's
universe honestly):

- **S1a — render the data we already hold (25 stubs).** EXECUTED
  2026-10-28, with a discovered defect: the Key-Figures render pass
  (`derive_insights.py:render_metrics_notes`) was scoped to metrics
  collected from the CURRENT RUN's edition scan — rows written by other
  writers (`enrich_from_yfinance`) exist only in `company_metrics` and
  were invisible to it, so those notes were never even considered (the
  `--stale-only` gate was NOT the blocker: the 25 notes carry
  `generated.by: process:okf_backfill`, which the gate renders
  through). Fix: new `--kf-manifest FILE` option (entity names or note
  paths; manifest entities render from DB truth, default scan-based
  behavior unchanged) + `_metrics_from_db` loader; 4 targeted tests
  added (`TestKfManifestBackfill`); suite 212 passed; ruff clean.
  Manifest: `bench_data/thin_tail/kf_manifest_entities.txt`.
  Outcome: dry-run promised exactly 25, apply wrote 25, all 25 notes
  now carry `## Key Figures (auto)`, re-run promises 0 (idempotent).
  Zero-fetch, zero bystanders.
- **S1b — fetch for the tickered thin stubs.** EXECUTED 2026-10-28.
  New `--from-manifest FILE` option on `enrich_from_yfinance.py`
  (entity names or note paths; selection-only — skip-logic unchanged;
  3 targeted tests, suite 46 passed). Manifest:
  `bench_data/thin_tail/yf_manifest_tickered.txt` (153 tickered thin
  stubs, regenerated post-S1a). Outcome ledger:
  `bench_data/thin_tail/s1b_yf_outcome_ledger.log`. Measured: 152
  selectable (1 manifest entity discovered untickered at the DB layer),
  139 fetched clean in 22.5 s, **6 fetch failures** (ABS Marine
  `ABS.NS`, Chemcart `544442.BO`, HSIL `HSIL.NS`, Redcliffe
  `REDCLIFFE.NS`, Sequent `SEQUENT.BO`, Symbiotec `544889.BO`) and
  **7 identity-mismatch skips** (GE, GSK, JSW Paints→JSW Dulux,
  Piccadilly→Piccadily spelling, Setco→Shilayan renamed, Shubhshree
  name drift, Vingroup) — both buckets recorded per-company, no silent
  skips. 1,701 metric rows written for 135 companies; 135 notes got
  profile/industry updates. Then a scoped S1b render:
  `derive_insights --apply --stale-only --kf-manifest
  bench_data/thin_tail/kf_manifest_s1b.txt` → **112 Key-Figures
  blocks written**. Corpus-wide finding surfaced while scoping: **477
  entities hold ≥5 metric rows but have no KF render** — the gap is
  NOT thin-tail-specific (rich notes too, e.g. 3M, Aarti Industries).
  Rendering the non-thin remainder is an operator decision (mass
  note-rewrite contract); recorded, not executed. → **EXECUTED same
  evening on operator go** (see S1 close-out below).
- **S1c — ticker resolution for the untickered stubs.** EXECUTED
  2026-10-28 (operator go: "fix via S1c"). New `--untickered
  [--notes-only]` lane on `ticker_hygiene.py`: same guarded pipeline
  (search_ticker + identity_ok), writes `entities.ticker` AND the note
  frontmatter `ticker:` line (null/empty only — populated lines are
  never clobbered). Two guard tightenings were REQUIRED by the
  population: (1) `identity_ok(min_subset_tokens=2)` for the lane —
  one-word note names subset-matched unrelated securities (``Take`` →
  Take-Two, ``GEE`` → Geely) under the default rule; exact token-set
  equality still passes (``PepsiCo`` vs ``PepsiCo, Inc.``); (2)
  `foreign_ok` — validated foreign listings resolve (PepsiCo → PEP,
  Microsoft → MSFT) where the scrip lane's Indian-only rule refused
  pre-guard. Population trap: DB-untickered companies number **16,291**
  (mostly non-note graph entities) — `--notes-only` (file_path
  populated) is the scoped universe: 161. Outcome: **15 resolved and
  written** (11 note frontmatter lines synced), 132 unresolved
  (genuinely unlisted/defunct — the triage queue in
  `outputs/ticker_hygiene_report.md`), 1 guard-refused, 13
  foreign-symbol noise refused. The 15 then rode the yfinance chain
  (`--from-manifest bench_data/thin_tail/yf_manifest_s1c.txt`): 156
  metric rows / 13 notes, KF render picked them up AUTOMATICALLY via
  the fixed default path (no manifest). **Divergence finding (4
  skips):** Dalmia Bharat's note carries `DALMIASUG.NS` (Dalmia
  *Sugar*), Vikram Solar's `VIKRAMTH.BO` (Vikram *Thermo*), Global
  Education's `GLOBAL.NS` vs the searched `VIRTUALG.BO` — DB had no
  ticker at all. DB holds the guarded search result; the note lines
  are untouched pending triage ruling.

### S1 close-out — the permanent fix + corpus-wide bulk render (operator go, 2026-10-28 evening)

The `--kf-manifest` option from S1a was the *scoped* tool; the
underlying defect needed the permanent fix so the gap cannot recur:

1. **`derive_insights` evidence model fixed** (default path, no
   flags): the KF render map is now the UNION of scan-collected metrics
   and every persisted `company_metrics` row (chunked `_metrics_from_db`
   load; scan entries win for their entities so dry-run previews stay
   accurate). 2. **Gate semantics corrected**: the edition-dates gate
   is evidence-blind to DB rows, so `_render_metric_note` now evaluates
   the KF plan FIRST and renders whenever the block would actually
   change — even under `--stale-only` (the idempotency guard keeps
   byte-identical blocks from churning; the `#139` fixed point moves
   from run 2 to run 3, pinned by the amended
   `test_stale_only_heals_then_holds_fixed_point`). 3. **Bulk render
   applied**: dry-run promised 541, apply wrote 541; residual gap = **0**
   of 983 metric-holding entities; idempotent re-run promises 0. KF
   coverage 526 → **1,046 notes**. 4. **`markdown_parse.md` updated**
   (3 sections: stage list, what-it-writes evidence model, stale
   checklist bullet — the 2026-10-03 "9 promised 0 wrote" advisory is
   dead).

**Final S1 census:** thin stubs **277 → 180**; KF notes **526 →
1,046**; note-untickered **161 → 150** (11 resolved + 4 divergence
findings); DB-untickered note-backed 161 → 146; `verify_notes.py` 0
errors; targeted suites **329 passed** (derive 212, enrich 46+,
hygiene 33+, yfinance); 683 vault files changed (operator stages).

### S2 — Concall capture deepening — EXECUTED 2026-10-28 (gates parked by operator until program completion)

The proposal's premise held and sharpened. Census (S2.1, library-level
replay of scan → collapse → skip_guard):

- **Quotes: ZERO gap.** 732 entities produce quotes in the newsletter
  corpus; every one has captured rows (the `make quote-coverage` audit
  reads 98.7% corpus coverage; the flagged residue is G2
  unresolvable-attribution — RBI/SEBI/regulator speakers — a pre-existing
  watchlist, not this arc).
- **Magnitudes: 57% skipped.** 4,727 scanned → 3,878 distinct → only
  1,652 retained; **2,226 skipped by the S2 skip guard** (2,185
  sentence-not-in-note + 41 note-missing). 169 of 520
  magnitude-producing entities had ZERO captured rows.

Root cause (S2.2): **the guard verified against the wrong file.** The
design doc (`derive_insights_apply_gate.md` S2) says "a row whose full
sentence cannot be located **in the edition** is skipped"; the
implementation checked the ENTITY's company NOTE instead — and notes
carry only a quote SELECTION, so magnitude sentences (which are not
attributed quotes) structurally could never pass. The archived proposal
even recorded the symptom ("1988 of 4302 rows stay skipped because a
rendered note carries a paraphrase") without recognizing the drift.

Fix (S2.3): `_skip_guard` now verifies against the **edition file** the
row was scanned from (stem recovered from `source_ref =
derive:metrics:<stem>:<line>`; per-stem text cache). Dry-run promised
2,226 new rows — exactly the census number — apply wrote 2,226 rows +
397 Key-Figures renders; re-run promises 0 (0 skipped, 0 stale).
Corpus: `company_metrics` 76,172 → **78,683 rows / 1,040 entities**.
Side effect: the apply_gate doc's "gate is not idempotent on its first
pass" caveat is dead — run 1 retains everything, so the #139 fixed
point returns to run 2 (test amended back).

Labels (S2.4): the recovered rows exposed the vocabulary gap — 1,634
carried recognizable nouns the 10-pattern label table missed ("RPO of X
GW", "investment of INR X crore", "attrition was around X%"). Ten
census-backed additions (capacity, investment, returns, volume,
attrition, penetration, utilization, dividend, rpo, realisation);
`metric_label` is a stable-replace content column, so the next scan
retro-labeled **267 historical rows** organically. NULL labels **3,119
→ 2,539** — the residue is the genuinely-unlabeled population (#308
contract preserved; its backfill is S4's scope). 6 new tests
(`TestMetricLabelVocabulary`); targeted suites **349 passed**;
`verify_notes.py` 0 errors.

Final S2 census: thin stubs 277 → **185**; KF notes 526 → **1,049**;
The-Chatter notes 714 → 715.

784 notes reference concalls but only 283 have `## Management`
sections; `derive_insights.py` is the capture layer and quotes land
corpus-wide (76k metric rows prove the extractors work). Work:

1. Coverage census: per-company concall-material-in-corpus vs captured
   (reuses the quote-coverage audit pattern, `make quote-coverage`).
2. Extend the capture worklist to the gap set; extractor rules change
   only if the census shows systematic misses (verified against
   `doc/procedures/markdown_parse.md` Stage-4 contract).
3. Guidance rows get `metric_label` at write time (pre-req for S4).

This lane beats any external API for content depth: concall prose is
native, already in-corpus, and multilingual Hinglish handling is a
known strategic weight (search-embeddings doctrine).

### S3 — Peer/ownership edges + note rendering — EXECUTED 2026-10-28

**Discovery: most of the slice was already built.** The
`relation_enrichment_sources` arc (2026-08-25, R3–R6) landed semantic_peer
(E3), coinfer (E4), holders/invested_in (E5) + UI surfacing (E6); the
competes_with industry-KNN topology (E2) is live (3,415 edges,
`yfinance:industry:`); and the BSE shareholding RSS lane the operator
scoped mid-arc landed as #269 (2026-09-22, `shareholding_sync
--bse-rss`) — dual-exchange SHP ingestion feeding the same
shp_filings/shp_holders store, promoter-category tags included, healthy
as of tonight (151 filings, 1,815 invested_in edges after the pending
batch applied). The semantic_peer edges were PURGED 2026-09-16 when
bge-small was superseded — the purge note said re-running re-seeds.

What this arc actually added:

1. **S3.1 — semantic_peer re-seeded under the live gemma space**
   (`embeddinggemma-2-q8_512`, 1,209 rows): fixed `build_semantic_peer_edges`
   to read via `gq.connect_read_only` (wire-aware — the old `gq.connect()`
   file-mode hits the duckdb-2.0 quack split-brain guard); applied **8,892
   pairs** (k=10, threshold 0.0, sym-dedup, `source_ref
   embeddings:embeddinggemma-2-q8_512:<date>`, prefix-scoped idempotent).
   DuckDB cache rebuild DEFERRED — needs `make quack-stop` /
   `graph-rebuild` / `quack-start`, parked on the operator's server
   schedule (the note render reads SQLite, not the cache).
2. **S3.3 — `## Peers (auto)` sentinel block (new writer)** in
   `derive_insights`: competes_with (business relation) → invested_in
   holders (pctHeld; institutions are note-less → plain text, no dead
   wikilinks) → semantic_peer (cosine, explicitly labeled "similarity not
   a business relation"); ranked, capped at 8, both endpoints of symmetric
   rows indexed; same DB-evidence + gate-bypass contract as Key Figures;
   insertion after the KF block (outside auto regions, rescue semantics
   inherited). Applied: **1,209 notes** carry peer/ownership context;
   idempotent re-run 0. 9 new tests (`TestPeersBlock`).
3. **S3.4 — BSE lane**: verified live + applied the pending batch (471
   candidates → invested_in 1,359 → **1,815**); unresolved BSE symbols
   (18) stay on the worklist per #269's non-goals. No new ingestion code
   needed — the arc's ask is satisfied by rendering ownership into notes
   (done via the holder segment above).

Targeted suites **385 passed**; `verify_notes.py` **0 errors, 0
warnings** (the 16 pre-existing advisory warnings cleared). Thin stubs
277 → **144**.

### S4 — Backfill `metric_label` (land deferred #308) — EXECUTED 2026-10-29

The writer-side fix was already substantially in place (S2.4's census
vocabulary + the `guidance` fallback); the residual 2,539 NULL rows
decomposed into two deterministic recoveries and a genuine remainder:

1. **`investing` stem miss** — `_L_INVESTMENT` matched only the noun
   (`\binvestments?\b`); the verb form ("We'll be investing about ₹15-18
   crores") slipped through. Now `\binvest(?:ments?|ing|ed|)\b`.
2. **Forward-tense guidance fallback** — the classifier's docstring always
   promised "guidance-style figures get a recognizable label", but the
   fallback fired only on the literal word "guidance". A census found 512
   NULL rows carrying forward-TENSE markers ("is expected", "projected",
   "will", "poised"...) with no metric noun. The fallback now runs on
   `_FORWARD_LOOK_RE`; noun-in-window still wins, and the `guidance_`
   prefix still requires the literal word.
3. **The remainder (~1,946 rows) is the genuinely-unlabeled population**
   per the #308 contract: present-tense sentences with no metric noun
   ("Roughly 60% of the cost base is tied to imports", "99.3% collection
   efficiency..."). A bigram census of these found only connective
   phrases — no honest label exists; #308's desktop-reader hardening
   (`label: Option`, `—` fallback) is the designed consumer posture.

Applied: **595 rows relabeled** (267 S2.4 + 328 S4), NULL labels **3,119
→ 1,946** (−61%); every rendered figure in the KF blocks now carries a
label or is recorded as genuinely unlabeled. 4 new tests
(`TestGuidanceFallback`); targeted suites **402 passed**.

**Operator rulings applied (S1c divergences, 2026-10-29):** Dalmia
Bharat → note synced to `DALBHARAT.NS`; Vikram Solar → note synced to
`VIKRAMSOLR.NS`; Global Education → **DB reverted to NULL** (the
searched `VIRTUALG.BO` is Virtual Global Education Ltd, HK — a 2-token
subset near-miss; the note's `GLOBAL.NS` stands). Guard follow-up
recorded: a geography-aware suffix preference for foreign-accept matches.

### S5 — Section taxonomy normalization — EXECUTED 2026-10-29

Mechanical, schema-first, heading-line-only. Census (S5.1) split the 40+
H2 variants into three unambiguous families and a recorded remainder:

1. **Chatter double-prefix (660 headings — the big one).** Editions whose
   H1 title began with "The Chatter:" rendered as "## The Chatter — The
   Chatter: X". Writer fix: `_normalize_chatter_edition` strips the
   redundant prefix from the DISPLAY (heading, block comment, Source
   footer) while the raw edition string stays the `as_of_edition` key —
   zero DB row churn — and the block-match comparison normalizes BOTH
   sides so migrated headings still converge (a naive compare would have
   re-inserted duplicated blocks). 4 tests
   (`TestChatterDisplayNormalization`).
2. **Overview family (235 renames):** Overview / Business Overview /
   Comprehensive Overview / Business Profile -> `## Company Overview`
   (the 766-note incumbent). **Key Insights (69 renames):** "Key Insights
   (from newsletter)" -> `## Key Insights` (0 collisions).
3. **Collision safety:** 47 notes carrying a variant ALONGSIDE an existing
   Company Overview were skipped; the first migration pass also created 41
   duplicates in notes holding TWO variants (the set-based guard missed
   occurrence counts) — corrected by restoring the non-canonical
   occurrences to their HEAD names, plus 1 pre-existing dup found. The
   ~90 remaining variant carriers (collision/multi-variant notes) are
   recorded curation follow-ups — body merging is human work, not
   mechanics.

**Drift tripwire (S5.2):** `verify_notes.check_canonical_sections` warns
(advisory) on any known variant — currently 103 warnings, all recorded
carriers, 0 errors. Convergence: post-migration renders reached the
byte-identical fixed point (0 would-write). Suites **406 passed**.
NOTE for arc close: ~1,100 notes changed section headings — the
note_search section_title index needs `search-fresh` at the gates.

## 3b. Deferred remainder — tracked for revisit (2026-10-29)

Three residual populations are deliberately NOT pursued further in this
arc. They are parked here with measured sizes and re-entry triggers so a
future session can pick them up without re-deriving the state. Re-measure
before acting — these numbers are a snapshot.

### D1 — 144 thin stubs (<4 KB), deferred

Down from 277 at arc start. Composition of the remainder: entities where
EVERY lane came up empty — mostly unlisted/private/foreign companies
(NPCIL-class), micro-caps where Yahoo holds nothing (the S1b ledger
records 6 fetch-failures + 7 identity-skips), and notes whose newsletter
source carried no concall/chatter material.

- **Re-entry triggers:** (a) new newsletter editions covering these
  entities (the S2 edition-contract guard now captures everything the
  moment prose exists); (b) the ~90 section-variant curation follow-ups
  and 47 collision notes (§S5.3) — body merging is human work; (c) an
  explicit curation pass to disposition genuinely-contentless stubs
  (merge into sector notes or mark data-triage).

### D2 — 150 untickered note-backed companies, deferred

The `--untickered --notes-only` lane resolved 15/161 in one guarded pass;
132 search-unresolved (mostly genuinely unlisted — the queue lives in
`outputs/ticker_hygiene_report.md`), plus Global Education reverted to
NULL by operator ruling.

- **Re-entry triggers:** (a) operator triage batch over the 132-name
  queue (the `--tier2` BSE scrip-match fallback and `--tier3` kite
  probe already exist for Indian names); (b) a geography-aware suffix
  preference in `identity_ok`'s foreign-accept path (the Global Education
  → VIRTUALG.BO 2-token near-miss) BEFORE widening foreign resolution;
  (c) quarterly re-run as new listings IPO.

### D3 — 1,946 NULL `metric_label` rows, deferred

3,119 → 1,946 via the S2.4/S4 vocabulary + forward-tense fallback. The
remainder is the genuinely-unlabeled population per the #308 contract:
present-tense sentences with no metric noun ("Roughly 60% of the cost
base is tied to imports"); a bigram census found only connective
phrases — no honest keyword label exists.

- **Re-entry triggers:** (a) an LLM-assisted labeling pass — the budget
  decision parked in relation_enrichment_sources §11 (out-of-scope
  follow-ups) is the natural vehicle; a one-off carrier pass over 1,946
  rows with source_quote context is small; (b) vocabulary growth from
  NEW corpus (new editions surface new nouns — re-run the bigram census
  on the then-NULL set); (c) #308's desktop-reader `—` fallback remains
  the safe consumer posture meanwhile.

## 4. Acceptance criteria & shakedown

1. **S1a** — EXECUTED 2026-10-28: after apply, all 25 KF-less
   data-held stubs contain `## Key Figures (auto)`; `git diff
   --name-only findata/` shows exactly the expected file set
   (manifest-diffed, no bystanders); re-run promises 0.
2. **S1b**: outcome ledger covers all 165 manifest entries — every
   line ok / no-data / error, zero silent skips; notes either grew or
   are recorded as Yahoo-empty.
3. **S1c** — EXECUTED 2026-10-28: 15/161 note-backed untickered
   resolved and written (guarded); the 132 unresolved + 4 note/DB
   divergences are recorded in `outputs/ticker_hygiene_report.md` as
   the triage queue — no `null` left unexamined.
4. **Corpus-level**: thin-stub count (<4 KB) drops from 277;
   re-run the Appendix-A census and record before/after in the
   archival entry.
5. **Gates stay green**: `verify_notes.py` clean on touched notes;
   targeted tests only during slices (`make pytest` forbidden per
   gates doctrine); full gates quartet once at arc close on operator
   go.
6. **S3/S5**: eval-gate — `helpers/misc/ontology_eval_gate.py` over
   `helpers/misc/ontology_questions.json` between dry-run and apply
   (query-visible semantics change: rosters/peers), zero regressions.
7. **S4**: post-backfill `SELECT COUNT(*) FROM company_metrics WHERE
   metric_label IS NULL` = 0; `TestPurgeIsPerCohort`-style targeted
   tests untouched.

| Projected outcome | Today (2026-10-28) | After S1 (actual) |
|---|---|---|
| Thin stubs (<4 KB) | 277 | **180** |
| Stubs with ≥1 content lane beyond overview/tags | ~30 | ~180+ |
| Notes with Key Figures | 526 | **1,046** (every metric-holding entity rendered) |
| Recorded no-data facts | none | per-company ledgers (fetch + resolution) |
| Untickered note-backed companies | 161 | 150 (+4 divergence findings → triage) |

## 5. Risks

- **yfinance rate-limits / flaky small-cap data** — scoped manifest +
  outcome ledger + `--workers` default 2; no-data is a recorded
  outcome, not a retry loop.
- **Wrong ticker resolved in S1c poisons downstream lanes** —
  data-triage gate on every ambiguous match; noise-gate mechanics;
  `entity_ticker_status` records provenance.
- **Mass note rewrites** — the 2026-08-19 deliberate-dry-run contract
  stays: apply is always an explicit, manifest-diffed command.
- **`derive_insights` re-render touches sourced rich notes** — use
  `--stale-only` so evidence-unchanged notes are gated out (okf
  freshness doctrine); verify the would-render list against the
  manifest before applying.
- **S5 heading migration collides with writer-owned regions** —
  schema-first ordering + the four-writer convergence integration
  tests (`tests/test_integration_note_writers.py`) must stay green.

## 6. Non-goals

- No new external scraping surfaces (Screener stays rejected; Google
  Finance stays gray-zone pending its own arc).
- No LLM-based relation extraction (v1 non-goal of Relations 2.0,
  unchanged).
- No changes to mechanical backbone edges (co_mentioned_in, cited_in,
  part_of/has_company) — healthy.
- No model changes to the search stack (notes-leg trials are a
  separate, settled line — note_search inherits quality gains only).
- No patch/commit lifecycle ops; operator owns staging and commits.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-28 | `wc -c` distribution over `findata/Companies/*/*.md` | n=1212, median 7,397 B, p10 1,574; 347 <4 KB incl. non-company dirs later split | size proxy |
| 2026-10-28 | `find findata/Companies -name '*.md' -size -4k` | 277 stubs | manifest basis |
| 2026-10-28 | `grep -L 'ticker: [A-Z0-9]'` over stubs | 112 untickered | = all `ticker: null` |
| 2026-10-28 | yfinance-profile presence split over stubs | 147 have profile / 21 tickered without / 109 untickered without | S1 split |
| 2026-10-28 | content-lane census over stubs | missing KeyFigures 246, Chatter 265, Management 241, KeyInsights 238 | multi-lane absence |
| 2026-10-28 | `company_metrics` per-stub probe | 27 stubs ≥5 rows (render gap); 250 <5 rows (fetch gap) | keyed on normalized_name |
| 2026-10-28 | spot checks | SPML Infra 103 rows/9 competes (KF already rendered); Shakti 99/11; Aeron 0/0 | data-held vs nothing-held |
| 2026-10-28 | KF-section presence split of the 27 data-held stubs | 25 without, 2 with | corrected the "27 render-missing" assumption |
| 2026-10-28 | dry-run `--stale-only` (pre-fix) | 0 key-figures notes would write (350 gated) | proved the gate was NOT the blocker |
| 2026-10-28 | dry-run with `--kf-manifest` | 25 key-figures notes would write | exactly the manifest |
| 2026-10-28 | apply with `--kf-manifest` | 25 wrote; git diff 46 files (= 25 KF + 21 predicted chatter) | zero bystanders |
| 2026-10-28 | re-run dry-run | 0 would write | idempotent |
| 2026-10-28 | `pytest tests/test_derive_insights.py` | 212 passed (4 new) | targeted only |
| 2026-10-28 | S1b apply, 153-line manifest | 152 selectable, 139 fetched, 1701 rows, 135 notes; 6 fetch-fail + 7 identity-skip recorded | ledger in bench_data/thin_tail/ |
| 2026-10-28 | S1b scoped KF render | 112 blocks written | `--kf-manifest bench_data/thin_tail/kf_manifest_s1b.txt` |
| 2026-10-28 | census after S1a+S1b | thin stubs 277→186; KF-section notes 526→663; 170 files changed | before/after |
| 2026-10-28 | verify_notes.py full run | 0 errors, 19 advisory warnings | warnings pre-existing lanes |
| 2026-10-28 | targeted suites (derive+enrich) | 291 passed | post-refactor |
| 2026-10-28 | bulk dry-run (default path, post-fix) | 541 key-figures would write, 344 gated | no manifest needed |
| 2026-10-28 | bulk apply | 541 wrote; residual gap 0/983; re-run 0 | fixed point |
| 2026-10-28 | untickered dry-run v1 (unscoped) | 1,884+ candidates staged → KILLED | 16,291 DB-untickered total |
| 2026-10-28 | untickered dry-run v2 (--notes-only, loose guard) | 6 resolved / 22 foreign false matches | short-name subset hazard |
| 2026-10-28 | identity_ok min_subset_tokens=2 + foreign_ok | 15 resolved / 13 foreign refused / 1 guard_refused | Take→TTWO class dead |
| 2026-10-28 | S1c apply | 15 tickers written, 11/15 note lines synced | 4 conflicts → triage |
| 2026-10-28 | S1c yfinance chain | 156 rows / 14 cos / 13 notes; KF auto-rendered | default path, no manifest |
| 2026-10-28 | final census | thin 180, KF 1,046, note-untickered 150 | 683 files changed |
| 2026-10-28 | full targeted suites | 329 passed | derive+enrich+hygiene+yf |
| 2026-10-28 | verify_notes.py | 0 errors, 16 advisory | unchanged lanes |
| 2026-10-28 | S2.1 census (library replay) | quotes gap 0; magnitudes 4727→3878 distinct→1652 retained, 2226 skipped | 2185 sentence-not-in-note + 41 note-missing |
| 2026-10-28 | S2.3 dry-run (edition-contract guard) | 0 skipped; 2226 would persist | = census exactly |
| 2026-10-28 | S2.3 apply | 2226 rows + 397 KF renders; re-run 0 | company_metrics 76,172→78,683 |
| 2026-10-28 | S2.4 vocabulary +10 patterns | 267 retro-labels; NULL 3,119→2,539 | stable-replace content col |
| 2026-10-28 | S2 targeted suites | 349 passed (incl. 6 new label tests) | +quote_capture_s4 |
| 2026-10-28 | semantic_peer dry-run (gemma, wire-mode fix) | 8,892 candidates | 1,209 embeddings, k=10 |
| 2026-10-28 | semantic_peer apply | 8,892 inserted (25 → 8,917 rows) | graph-rebuild deferred (quack) |
| 2026-10-28 | shareholding_sync --rss --bse-rss --apply | invested_in 1,359 → 1,815 | BSE lane healthy (#269) |
| 2026-10-28 | peers render dry-run → apply | 1,209 wrote; re-run 0 | new `## Peers (auto)` block |
| 2026-10-28 | S3 close-out | 385 tests; verify 0 err/0 warn; thin 144 | divergences pending ruling |
| 2026-10-29 | operator rulings | 2 note tickers synced; Global Education DB → NULL | VIRTUALG.BO = HK near-miss |
| 2026-10-29 | quack bounce + graph-rebuild (operator stop/start) | e_semantic_peer 8,917 / e_competes 3,577 / e_invested 1,815 | wire-verified |
| 2026-10-29 | S4 relabels apply | 595 rows; NULL 2,539 → 1,946 | forward-tense fallback + stem fix |
| 2026-10-29 | original arc doc addendum | relation_enrichment_sources §14 | BSE RSS superseded the Screener routing |
| 2026-10-29 | S4 targeted suites | 402 passed | verify 0 err / 0 warn |
| 2026-10-29 | S5.1 census | 3 unambiguous families: 660 chatter doubles, 235 overview variants, 69 insights | 47+41+1 collision/multi-variant carriers recorded |
| 2026-10-29 | S5.3 migration | 964 heading renames / 619 files; then 41 dup-reverts + 660 chatter + 460+19 convergence | heading lines only |
| 2026-10-29 | S5 close-out | 406 tests; verify 0 err / 103 advisory (drift tripwire); fixed point 0 | search-fresh at gates |
| 2026-10-29 | deferred remainder recorded | D1 144 stubs · D2 150 untickered · D3 1,946 NULL labels | §3b, with re-entry triggers |
