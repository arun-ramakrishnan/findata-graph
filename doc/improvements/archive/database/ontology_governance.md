---
title: "Ontology governance front door — master doc, concept lifecycle, change gate"
status: executed
filed: "2026-09-17"
executed: "2026-09-17"
completed_md: "244"
area: "doc/design + helpers (seed_concepts, integrity check, static_checks)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Ontology governance front door — master doc, concept lifecycle, change gate

**Date:** 2026-09-17 · **Status:** EXECUTED 2026-09-17 (completed.md #244) ·
**Area:** doc/design + helpers (seed_concepts, integrity check, static_checks) — ontology governance arc

## 1. Motivation

The ontology convention stack (#234, schema v11) landed the tables but
left three governance gaps, all surfaced by the 2026-09-17 session
(EvoOntology exploration + the operator's master-doc observation):

1. **No single master doc.** The ontology layer is spread over five
   documents with no front door: `db_schema.md` documents storage
   table-by-table (provenance `:287`, concepts `:311`), `graph_design.md`
   §5.5 carries cross-cutting graph conventions, `ontology_glossary.md`
   is citations-only, the archived proposal is frozen history, and the
   `doc/local` assessment memo is the research record. The question
   "what are the controlled vocabularies and binding rules of this
   ontology?" has no single answer document — for operators or agents.
2. **No lifecycle on concepts.** `seed_concepts.py` DELETE-then-INSERTs
   its `seed:%` rows (measured below), so roster changes (absorptions,
   renames) destroy queryable history. There is no candidate→active
   promotion path — and the deferred industry→NIC coding migration
   (816 non-null labels / 117 distinct / 0 coded, #234 S2 baseline)
   needs exactly that path to land operator-verifiable suggestions.
3. **No answer-level change gate.** Ontology-affecting changes are
   gated structurally (tests, integrity, reconciliation dry-runs) but
   never on downstream answers: nothing asks "did the changed ontology
   regress a frozen set of known-good query answers?".

EvoOntology (ruc-datalab, MIT, arXiv 2609.15779; evaluated 2026-09-17
from a shallow clone, /tmp/evoontology — see §2) contributes two
mechanisms worth translating: record-level `lifecycle_state`, and the
gated parent-vs-candidate evaluation (accept only on strictly-more-wins
AND zero critical errors). Both translate to our conventions-not-
runtimes posture; the rest of that system we decline with reasons
(§2 table).

## 2. Evidence (measured 2026-09-17, this box)

Code seams (read this session, file:line):

| Fact | Anchor |
|---|---|
| converger DELETE sites (mappings, concepts, schemes — delete-then-reinsert on `seed:%`) | `helpers/misc/seed_concepts.py:260-262` |
| concepts/concept_mappings DDL — no status column | `seed_concepts.py:100-124` |
| `subtree()` closure helper (recursive CTE) | `seed_concepts.py:284` |
| `check_concepts` — dangling broader / cycles / duplicate pref_labels / empty schemes; WARNING tier | `helpers/misc/database_integrity_check.py:113,1220` |
| edge roster registry (20 types) | `database_integrity_check.py:36-57` |
| canonical event types registry | `helpers/validators/static_checks.py:463` |
| SQLite schema version 11 | `helpers/core/db.py:66-67` |
| maint-full composition (18 PRE_FULL+TIER steps, seed_concepts = 6th PRE_FULL) | #234 execution record |
| promotion-surface precedent (plan-then-apply, batch-blocks-on-failure) | `helpers/misc/backfill_identifiers.py` (`--set-cin`, S3) |

Doc dispersion (doc_query "ontology design taxonomy schema", 2026-09-17):
top 8 hits fragment across 5 files — archive proposal, doc/local
assessment, db_schema.md, completed.md #234, ontology_glossary.md —
none of them a normative master.

EvoOntology adopt/reject verdicts (clone read, anchors under
`/tmp/evoontology/evoontology/`):

| Mechanism | Verdict | Reason |
|---|---|---|
| record `lifecycle_state` (active/deprecated/superseded on every record) | **adopt** (S1) | formalizes absorption history; pure convention |
| gated evaluation: accept = strictly-more-wins AND zero critical errors | **adopt, deterministic variant** (S2) | `evaluation/evaluation.py:63`; our answers are row-sets so comparison is exact — no LLM needed |
| versioned store + active pointer (`ontology/store.py`) | reject | git + stg is our version lineage |
| trajectory store + trigger thresholds (30 trajectories / 7 days, `trigger/trigger.py`) | reject | git history + completed.md run log already mine failures into fixes |
| `confidence` enums on records | reject | D-O3 (no fake confidence) — reaffirmed |
| MCP tool layer (`browse/resolve_semantics`) | reject | doc_query/script_query/ripwire already cover on-demand access |

Measured, do not re-audit: the anchor table above; the dispersion run;
the adopt/reject table. Re-check `/tmp/evoontology` (a /tmp clone may
be purged) only if the design reference needs re-verification — the
repo is young (6 commits) and is cited as design reference only;
nothing is vendored.

## 3. Design — three slices, order fixed

### S0 — master ontology doc + roster-drift advisory (executes first)

`doc/design/ontology.md` (new; design/ is the "what the store is" area,
`reference/` stays the citation home). Four sections:

1. **Posture** — conventions-not-runtimes verdict (SKOS/ORG/PROV-O/n-ary
   as table conventions; no RDF/SHACL/OWL), linking the assessment memo
   (research record) and archived #234 (history).
2. **Vocabulary rosters** — the 20 edge types + symmetry set, the 9
   namespaces→schemes, 4 canonical event types, hyperedge types, event
   role vocabulary (`_EVENT_ROLES`, `derive_hyperedges.py:541`), and
   every CHECK enum (match_type, identifier_type, source_tier). Each
   roster block is delimited by `<!-- roster: name -->` …
   `<!-- /roster -->` markers and anchored to its code registry as
   truth — the doc restates for humans, the code decides.
3. **Binding decisions** — D-O1…D-O6 from the assessment §10.3 +
   the writer/reader obligations table (§10.4).
4. **Where live truth lives** — counts → `db_schema.md` (measured,
   never transcribed); membership → code anchors; citations → glossary;
   extractor shapes → module docstrings. **The doc carries ZERO live
   counts** — that rule is the anti-drift contract.

Roster-drift advisory: a parse-only check in `static_checks.py` (it
already reads proposals/ for the lifecycle check) that extracts marker
blocks from `ontology.md` and diffs them against a loader table seeded
with the two cleanly-exportable registries (`_KNOWN_EDGE_TYPES`,
`CANONICAL_EVENT_TYPES`). DDL-only CHECK enums join the loader table
when shared constants exist (deferred, §7). The check is ERROR-tier and
qa-gated via static_checks: doc↔registry disagreement fails loudly in
both directions, mechanizing the manual S0 census cure from #234.

`graph_design.md` §5.5 gains one "see doc/design/ontology.md" pointer
line; `db_schema.md` and the glossary are untouched.

### S1 — concept lifecycle (status, supersede-not-delete, promotion)

- **Schema**: `concepts` and `concept_mappings` each gain
  `status TEXT NOT NULL DEFAULT 'active' CHECK (status IN
  ('candidate','active','superseded'))`. Self-ensured in
  `seed_concepts.py`'s CREATE + guarded ALTER (the S3 pattern); SQLite
  v11→v12 (`db.py` EXPECTED_*); no DuckDB cache bump (concepts were
  never materialized — #234 S2 precedent). Maint-full stays 18 steps;
  `test_maint` pins unchanged.
- **Converger**: DELETE-then-INSERT becomes a three-way merge — roster
  rows upsert as `active`; seed-owned rows the roster no longer
  produces flip to `superseded` (kept queryable, not deleted);
  reappearing rows resurrect to `active` (idempotence preserved);
  operator/agent rows (non-`seed:%` source_ref) untouched, as today.
  The dry-run report gains superseded/resurrected sections.
- **Promotion surface**: `--promote scheme:code[,…]` plan-then-apply
  mirroring S3's `--set-cin` gates (target exists, no conflicting
  active mapping, any failure blocks the batch). Candidate rows arrive
  via extractor/triage funnels stamped `agent_id` — S1-of-#234
  provenance already names the proposer; lifecycle + provenance is
  EvoOntology's Evidence pattern in our conventions.
- **Readers (land same-slice)**: `subtree()` defaults to active-only
  traversal (a superseded sub_sector must stop grouping companies);
  `check_concepts` gains three WARNING advisories — candidates staler
  than N maint runs, active mappings pointing at superseded concepts,
  prefLabel uniqueness scoped to `active` (duplicates across time are
  the history being kept).
- **First consumer** (out of this arc's scope): the deferred NIC-2008
  seed table lands its auto-suggested codes as candidate
  `concept_mappings`; the operator spot-checks and promotes.

### S2 — deterministic ontology change gate

- **Question set**: `tests/data/ontology_questions.json` (fixture home,
  beside the onager baseline) — ≥20 questions whose answers are
  row-sets over the live store, spanning three shapes: subtree
  membership, event-counterparty sets, identifier resolution
  (`/api/resolve`-shaped). Answers are version-stamped; the operator
  authors the roster.
- **Runner**: `helpers/misc/ontology_eval_gate.py` — takes a parent DB
  copy and a candidate DB copy, answers the frozen set against both,
  exact set-diff. Accept rule translated from EvoOntology
  (`evaluation.py:63`): **zero regressions** on frozen answers (the
  zero-critical-errors veto) **AND** the change's targeted improvement
  materializes (strictly-beats-parent). Report is JSON with a
  `protocol` field — `"set_diff"` now; the LLM-judge variant
  (`"llm_judge"`, anonymized A/B, same accept rule) is a named slot
  deferred behind the LLM-API posture (§7).
- **Rebaseline discipline**: after an accepted change, expected answers
  update via explicit `--rebaseline` (recorded in the report with the
  accepting change's reference) — never automatically, else the gate
  deflates silently.
- **Workflow placement**: not a qa leg, not a maint step (gates don't
  converge). It runs on the disposable-copy between dry-run and
  canonical apply, and the proposal template + proposals/README gain
  one sentence: an eval-gate bullet is mandatory in acceptance criteria
  whenever a change alters query-visible semantics (rosters, crosswalks,
  hierarchies, extractor rules).

**Order**: S0 first (the normative home S1's enums document into);
S1 second (unblocks the NIC migration); S2 last (needs operator
question authoring). **Alternatives declined**: any RDF/SHACL runtime
(assessment verdict); EvoOntology's versioned store/trigger/MCP
machinery (§2 table); adding the gate to `make qa` (mid-arc doctrine:
targeted checks, full gates once at arc end).

## 4. Acceptance criteria & shakedown

1. S0: `ontology.md` exists with all roster blocks; roster-drift check
   green on the live doc; a unit test plants a corrupted copy (one
   roster bullet removed) and asserts the check FAILS naming the
   missing value; md-lint clean; post `make search-fresh`, doc_query
   "ontology" surfaces the new doc as a top hit.
2. S1: `PRAGMA user_version` = 12 post-apply; converger idempotence
   tests (supersede on removal, resurrect on re-add, second `--apply`
   is a zero-flip no-op, operator rows untouched, dry-run report
   sections present); `--promote` gates tested (missing target /
   conflicting active mapping each block the batch); `subtree()` fixture
   — superseding a sub_sector drops it from the closure; the three new
   `check_concepts` advisories report live; `test_maint` pins unchanged
   (18 steps).
3. S2: gate dry-run on fixture DB pairs — planted regression → REJECT
   with the regressed question named; improvement-only → ACCEPT;
   `--rebaseline` bumps the version stamp and records the accepting
   reference; question set exercises all three answer shapes.
4. Arc gates once, at end, with the operator's go: `make qa` /
   `make advisory` / `make search-fresh` green; close order
   maint-full → search-fresh → snapshot.

| Projected outcome | Today | After |
|---|---|---|
| ontology front door | 5 dispersed files, no master | 1 master doc + pointer map |
| concept history on roster change | deleted | superseded, queryable |
| candidate→active promotion | none | validated `--promote` surface |
| ontology-affecting changes | structural gates only | + deterministic zero-regression answer gate |

## 5. Risks

- **Sixth drift surface** — the master doc becomes one more stale copy.
  Mitigation: the zero-counts rule (membership only), the qa-gated
  roster-drift check failing in both directions, and the §4 pointer map.
- **Status columns with no consumers** — schema creep. Mitigation:
  readers land in the same slice (`subtree()` scoping + advisories);
  obligations row below names writer and reader per artifact.
- **Question-set erosion** — the gate rots if answers drift unchecked.
  Mitigation: version stamps + explicit recorded `--rebaseline`.
- **Roster check brittleness** — parse-only, no DB access, fixture-
  tested against corrupted copies; marker syntax is three lines of
  regex.
- **EvoOntology immaturity** (6 commits) — cited as design reference
  only; zero imports, zero vendored code; re-verify the repo before
  extending the borrow list.

| Artifact | Writer (named) | Reader (named) |
|---|---|---|
| `doc/design/ontology.md` | this arc (S0); subsequent ontology arcs update rosters same-change | doc_query corpus; roster-drift static check |
| `status` columns + supersede/upsert converger | `seed_concepts.py` | `subtree()` active-only; `check_concepts` advisories |
| `--promote` surface | `seed_concepts.py --promote` | NIC seed slice (future); unified_search facets (future consumer) |
| `ontology_questions.json` + gate runner | operator authors questions; `ontology_eval_gate.py` runs | proposal acceptance criteria of ontology-affecting arcs |

## 6. Non-goals

- No RDF store, no SHACL/ShEx execution, no OWL import (assessment
  verdict, unchanged).
- No confidence columns anywhere (D-O3 reaffirmed — EvoOntology's
  confidence enums explicitly declined).
- No trajectory store, no evolution state machine, no versioned
  snapshot store, no MCP/tool layer (§2 reject table).
- No `part_of`/`has_company` retirement (still behind the #234 §8-R3
  gate).
- No NIC-2008 seed table and no industry-code filling in this arc —
  S1 only prepares the promotion path they will use.
- No LLM-judge implementation (slot reserved in the report schema).

## 7. Deferred / future-facing

- **Mapping record type** (EvoOntology `Mapping`: term → table/column/
  semantic filter/aggregation semantics/grain) — a glossary note in
  `doc/reference/ontology_glossary.md`; trigger: a metrics Q&A lane
  proposal.
- **LLM-judge protocol 2** — trigger: an LLM-API posture (the terrain
  C4 revival condition); aggregation rule copied from
  `evaluation.py:63` into the existing report schema.
- **DDL CHECK enums in the roster loader table** — behind shared
  constants for match_type/identifier_type/source_tier.
- **NIC-2008 seed table** (standing #234 deferral) — first real
  consumer of S1's candidate/promotion flow.

## Appendix — raw measurement log

| Run | Command / action | Result | Notes |
|---|---|---|---|
| 2026-09-17 | doc_query "ontology design taxonomy schema" --limit 8 | top hits across 5 files, no master | dispersion evidence |
| 2026-09-17 | read `seed_concepts.py` (DDL, converger, subtree) | DELETE sites :260-262; no status column | S1 seam |
| 2026-09-17 | read `db.py` version constants | EXPECTED_USER_VERSION = 11 | S1 bump baseline |
| 2026-09-17 | `grep -n '^#'` db_schema.md / graph_design.md; `ls doc/reference/` | ontology content at db_schema :287/:311, graph_design §5.5; reference/ = glossary only | S0 dispersion map |
| 2026-09-17 | shallow clone github.com/ruc-datalab/EvoOntology → /tmp/evoontology | models/store/trigger/evaluation/session read; adopt/reject table §2 | design reference, MIT |
| 2026-09-17 | `stg series` / `stg show --stat ontology` | top patch `ontology` empty (created 18:49 this box) | filing refreshes into it (scoped pathspec) |

## 8. Execution Results

- **S0 EXECUTED (2026-09-17)** — master doc + roster-drift advisory.
  `doc/design/ontology.md` live: posture, 12 marker-delimited rosters
  (2 gated: edge_types, canonical_event_types), D-O1…O6, obligations
  table, where-live-truth pointer map, zero-counts rule.
  `check_ontology_doc_rosters` in static_checks (qa-gated, ERROR tier)
  with loader table seeded from `_KNOWN_EDGE_TYPES` +
  `CANONICAL_EVENT_TYPES`; the check BIT ON FIRST LIVE RUN and caught a
  real discrepancy: `EDGE_REGISTRY` (query.py) holds only the 12
  DuckDB-materialized edge types, NOT the 20-type vocabulary — the
  registry's own "must match" comment is aspirational. Doc attribution
  corrected (EDGE_REGISTRY = cache subset). graph_design.md §5.5 pointer
  line. Tests: 5 new in test_static_checks (matching doc passes, removed
  value fails naming it, unknown value fails, absent roster fails, live
  doc green); suite 98/98. md-lint clean.
- **S1 EXECUTED (2026-09-17)** — concept lifecycle; SQLite schema v11→v12
  (db.py EXPECTED_*; no DuckDB cache bump — concepts still not
  materialised). Landed: `status` column (candidate|active|superseded,
  CHECK) on concepts + concept_mappings (CREATE for fresh DBs, guarded
  ALTER via the S3 pattern for pre-S1 DBs); lifecycle-aware converger —
  roster rows reinserted active (resurrection falls out), seed-owned
  rows the roster drops flip to superseded (kept queryable, never
  deleted), operator/agent rows untouched, dry-run reports
  superseded/resurrected counts; `--promote scheme:code` /
  `--promote-map src->tgt[:match]` plan-then-apply surfaces (missing
  target, already-active, conflicting active mapping each block the
  whole batch — phantom-plan leak caught by test, fixed);
  `subtree(include_inactive=)` active-only default; `check_concepts`
  gains active-scoped prefLabel uniqueness + three advisories
  (active mappings → superseded concepts, hierarchy through superseded,
  dangling candidates), pre-S1 DBs skip cleanly. Live apply
  (pre-backed-up /tmp/research_db_pre_s1_backup.db): 444 concepts / 103
  mappings all active, ZERO lifecycle flips (roster unchanged — pure
  column landing), ensure_db_meta healed user_version 12 (rebuild_schema
  is NOT in the maint chain — heal is an apply-time one-liner, the #234
  pattern), check_concepts 0 warnings/0 errors live. db_schema.md S2
  section updated. Tests: 16 new in test_seed_concepts (28 total file),
  integrity/validators/maint suites 172/172 unaffected; test_maint pins
  unchanged (18 steps).
- **S2 EXECUTED (2026-09-17)** — deterministic change gate.
  `helpers/misc/ontology_eval_gate.py` (set_diff protocol): frozen
  question set answered against parent + candidate DB copies, accept iff
  zero regressions AND no undeclared changes AND every --expect-improve
  question changes (a declared change that only LOSES rows = regression,
  not improvement); unanswerable questions hard-reject; report JSON
  carries `protocol` (the LLM-judge variant's reserved slot).
  `tests/data/ontology_questions.json` ships DRAFT-gated (three shape
  examples; the gate refuses a draft set — the operator authors the
  roster and flips the flag). `--rebaseline REF` refuses unless the gate
  ACCEPTed, rewrites expected from candidate, bumps version, stamps the
  accepting reference. Template §4 + proposals/README carry the
  mandatory gate-bullet rule for query-visible changes. Tests: 15 in new
  test_ontology_eval_gate (accept-rule matrix, draft refusal, CLI rc
  0/1/2, rebaseline record + refusal).
- **S2b EXECUTED (2026-09-17)** — question set DERIVED and de-drafted.
  `tests/data/ontology_questions.json` v1 live: 35 questions (14 subtree —
  all 10 super_sector roots, top-3 sectors by closure size, one leaf; 13
  counterparties — 12 acquisition/jv picks by distinct-counterparty
  coverage + a guidance negative pinning "guidance carries no
  counterparties"; 8 CIN resolves — 6 positives across vintages incl.
  unlisted-U/PTC/1918 + 2 negatives). Expected lists computed by the
  gate's own `answer_question` against live, then INDEPENDENTLY
  verified: raw recursive-CTE/SELECT spot checks match; live-vs-live
  gate run ACCEPT rc 0; planted drift (2 Banking subsectors superseded
  on a copy) → REJECT naming `subtree-super-sector-financials` with the
  exact lost rows. CINs: 728/728 unambiguous live (S3 landed 0 — the
  triage funnel filled them). Default --questions path repo-rooted;
  draft-pinning test replaced by a shipped-set well-formedness test
  (unique ids, ≥20, all three shapes).
- **S2c EXECUTED (2026-09-17)** — question set REHOMED + v2 doc-driven
  roster. Operator ruled tests/data the wrong home; repo precedent found
  and followed: `helpers/misc/embed_eval_questions.json` — a frozen eval
  set lives BESIDE ITS CONSUMER, reviewed in patches like code. Now
  `helpers/misc/ontology_questions.json` (DEFAULT_QUESTIONS module-dir;
  template/README/master-doc references repointed). Five doc-driven
  shapes added to the runner (entities_of_type, edge_neighbors,
  hyperedge_members, concept_mappings_for, provenance_agents_for — each
  pinning a master-doc §2 roster or db_schema surface; +6 shape tests).
  v2 roster: 79 questions mapped 1:1 to the documented surfaces
  (19 edge types, 9 hyperedge types, 7 entity-type vocabularies, 14
  subtree, 13 counterparties, 8 resolves, 4 crosswalks, 5 provenance
  tables). Verified: all five new shapes raw-SQL spot-checked;
  live-vs-live ACCEPT; planted ontology drift (a source's full
  co-mention neighborhood deleted + a sector hyperedge losing 3
  members) → REJECT naming both with exact lost rows. SCOPE MEASURED:
  bulk data drift in unpinned neighborhoods is NOT the gate's job
  (5 listed_in deletions correctly pass — integrity check + snapshot
  parity own that). Operating procedure + coverage map + rebaseline
  flow: `doc/procedures/ontology-gate.md`; master doc §6 points at it.
- **ARC CLOSED (2026-09-17)** — gates run in full, with the operator's go.
  Close order: maint-full 21/21 steps OK (lifecycle converger green
  in-chain; generation bump normal) → search-fresh APPLY + convergence
  rc=0 → snapshot (final maint step). Gates: `make qa` 9/9 PASS
  (~3,100 tests; one rerun — tmpfs pressure from a leaked-tempdir pileup,
  see below), `make advisory` 10/10 PASS (first-run bill: S608 noqa on
  the check_concepts f-string, C901 refactors (`evaluate` split into
  `_evaluate_one`, `promote` into `_plan_mapping_promotions`), UP017
  datetime.UTC,
  plus PRE-EXISTING drift fixed en route: search-tui.md MD056 two-rows-
  on-one-line typo from #242/#243 era, ty error in test_search_tui
  (`in` on a Rich renderable → str()), two ty Optional-guards in
  test_graph_disk/test_hyper_incidence, frontend `bun install` for the
  missing sugar-high dep). `make perf` SOLO 20/22 —
  graph_eigenvector 2.23–2.34s vs 2.0 budget and graph_link_prediction
  3.35–3.46s vs 2.0: PRE-EXISTING growth drift, first perf run since
  2026-09-12 (pre-chatter-ingest store; then 0.53s/1.62s at ~1.2k
  companies vs 6.2k now); WAIVED by the operator 2026-09-17, budget
  decision deferred to pending.md. Environmental findings recorded:
  /tmp tmpfs exhaustion (81%) from test_fuzz_shortest_path leaking a
  176MB sp.db tempdir per run — cleaned 3.7GB, leak itself deferred.
