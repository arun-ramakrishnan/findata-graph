---
title: Documentation consistency pass — README files, procedures, schema & config guidance
status: executed
filed: '2026-08-27'
executed: '2026-08-27'
completed_md: '167'
area: tooling
---

# Proposal: Documentation consistency pass — README files, procedures, schema & config guidance

**Date**: 2026-08-27
**Status**: EXECUTED 2026-08-27 (user go after review round; all 9 files,
§3 fixes 1–8 + amendments; verification + lifecycle below).
**Depends on**: nothing code-side — proposed changes touch `README.md`,
`frontend/README.md`, four `doc/procedures/*.md`, `doc/schema.md`,
`doc/findata.md`, and two Makefile help/annotation lines only. No script,
schema, or gate behavior change.
**Trigger**: standalone audit session 2026-08-27. Method: every *checkable*
factual claim in README files, operator docs, configuration guidance (Makefile
help/annotations) and usage examples was cross-checked against the current
implementation, the git-tracked databases/sidecars, and recent commits.
Ground rules held throughout: modify only what is directly verifiable from
code/constants/commits/query output; preserve existing structure, terminology
and writing style; never edit historical logs (`completed.md`, archived
proposals).

---

## 1. Context

Commit `b11810b7` ("README refresh") landed 2026-08-26 and one further code
commit followed it: `ef980d14` ("note_search --check drift reporting +
serialized graph-build flock"), which changed two documented surfaces:

1. `rebuild_note_search.py --check` previously printed a count only and
   **always exited 0**; since `ef980d14` it prints a FRESH/STALE drift verdict
   (changed/new/deleted paths) and exits **1** on drift (#164).
2. `query.py connect(read_only=True)`'s cold/stale fallback build path is now
   serialized cross-process by an `flock` on `<cache>.build.lock` (#165).

Several older documentation claims predate even earlier changes (tag
whitelist expansion, DuckDB schema v13, frontend entity-page TS conversion)
and were never reconciled. Findings below are grouped per file; each carries
the doc line and the pinning evidence.

---

## 2. Findings

### 2.1 README.md (repo root)

| # | Claim (line) | Evidence | Verdict |
|---|---|---|---|
| R1 | "**Tests** \| 2,574 across 126 modules" (L15; echoed L82 "126 pytest modules (2,574 tests)") | `.venv/bin/python3 -m pytest --collect-only -q` collects **2,581** at audit time (default `testpaths = tests`; only `-m live` is deselected at runtime). Module count `ls tests/test_*.py \| wc -l` = **126** ✓. Diff grep over `ef980d14` shows exactly **7** new test functions (6× `test_rebuild_note_search.py`, 1× `test_graph_disk.py`) = the delta after the README refresh. **2026-08-27 review note:** the embed-store execution (#166) added ~9 net tests since (6 migration round-trip + 2 vec-store pins + 2 backup-store branches − 1 removed), so expect ≈**2,590** — do NOT "fix" toward 2,581; the fresh re-collect mandated in §4 is authoritative. | STALE |
| R2 | "only `entity_type/`, `sector/`, `market_cap/`, `subsector/` namespaces are mirrored" (L183–184) | `helpers/core/sync_tags.py` `ALLOWED_CATEGORIES` has **9**: entity_type, sector, market_cap, subsector, holding_company, geography, business_model, risk_investment, investment_theme (in-code HISTORY comment: expanded 2026-07-30). The word "only" makes this falsifiable — five mirrored namespaces are omitted. | CONTRADICTED |
| R3 | doc_search corpus "~63 files, ~490 sections" (L136) | Live sidecar (`memory/doc_search.db`, read-only): `doc_search_meta` = **65 rows**, FTS section rows = **513**. Growth since the refresh: the `embed_store_consolidation.md` proposal (+406 lines) plus `doc/local/` scratch (the index intentionally ingests gitignored `doc/local/`). Re-measure at execution time before writing (machine-local variance). | STALE (hedged numbers drifted ~+5%) |

Checked consistent (no action): entities **1,517** with exact breakdown
(company 1,063 · institution 205 · edition 108 · sub_sector 78 · sector 42 ·
super_sector 9 · theme 12); edges **17,022 / 15 types**; events 357 · quotes
2,607 · metrics 1,794 · 14 persisted metric kinds; notes **1,226** tracked vs
note_search "(1,224 docs)" — both correct on different bases (the 2 absentees
are intentionally unindexed `image_map.md` chrome files); all repository-layout
paths exist; quickstart targets + FLASK_PORT 5200 default verified;
`_SCHEMA_VERSION = "13"` matches "schema v13"; algorithm list matches
`algorithms.py` dispatch; all 26 named make targets exist; all API excerpt
routes match `app.py` decorators; frontmatter field list matches
`doc/schema/frontmatter.company.v1.json`.

### 2.2 Makefile (configuration guidance)

| # | Claim | Evidence | Verdict |
|---|---|---|---|
| M1 | `advisory` @echo help (L31) and target annotation (L312) enumerate advisory as "ty-on-tests, live invariants, frontend, graph algos, analytics, suggestions, integration, lint-audit". Header (L29) demands help/annotation stay in sync. | `tests/run_gate_report.py:148-156`: advisory also runs `doc-search-check`, `script-search-check`, `note-search-check` (each `rebuild_*_search.py --check`). Post-`ef980d14` the note-check row yields FAIL when stale, so the omission materially understates the gate. | CONTRADICTED (enumeration omits 3 rows) |
| M2 | `metrics-rebuild` help line says "company financials + industry edges from yfinance"; target annotation says "company financials + notes from yfinance" (L58 vs L127). | Internal divergence between the synced pair (house rule L29); resolve against the recipe/script's actual behavior at execution time. Pre-existing rot, unrelated to `ef980d14`. | INCONSISTENT (internal) |

Checked consistent: `search-fresh` annotation == recipe; flags cited in other
help strings exist (`suggest-relations --append`, `graph-algos --all
--no-apply`, `analytics REPORT=coverage`, `triage-relations
--apply-decisions --write`). GATE_JOBS comment (L10–16) on RO-concurrency is
still true for the warm steady state — the flock only concerns cold/stale
races; extension optional, not a contradiction.

### 2.3 doc/schema.md

| # | Claim (line) | Evidence | Verdict |
|---|---|---|---|
| S1 | "`_build_meta.schema_version` = \"9\"" (L178) | `helpers/graph/query.py:185` `_SCHEMA_VERSION = "13"`; `_is_warm()` rejects any cache stamped otherwise. | CONTRADICTED |
| S2 | Cache inventory: "20 objects", enumerating 12 `e_*` tables + v_node/v_embeddings etc. (L177–186) | Current manifest from `_EXTRA_MATERIALIZED` (query.py:717-734) + EDGE_REGISTRY = **28 objects**: adds e_semantic_peer, e_invested, e_cited_in, e_all_und, e_dir, v_theme, v_edition, v_institution, v_note_embeddings. Also `v_embeddings` is materialised by `query.py` from SQLite `company_embeddings` (query.py:1027-1031), not written by embeddings.py as implied. | CONTRADICTED |
| S3 | "Mirrors only `entity_type/`, `sector/`, `market_cap/`, `subsector/`" (L41–42) | Same evidence as R2. | CONTRADICTED |
| S4 | Integrity-check registry table lists 15 checks (L200–217) | `database_integrity_check.py:74-96` `_CHECKS` includes a **16th**: `Check("note_tags", "check_note_tags", "error", "Note Tags (source trees)")`. All other rows' names/severities verified accurate. The table purports to mirror the registry. | PARTIALLY-STALE |

### 2.4 doc/findata.md

| # | Claim (line) | Evidence | Verdict |
|---|---|---|---|
| F4 | "Only `entity_type/`, `sector/`, `market_cap/`, `subsector/` are mirrored; the rest stay note-only." (L76–78) | Same evidence as R2/S3. Everything else in the vault spec checked out (directory names, 42 sectors, frontmatter required fields per JSON schema, sync rules 1–6). | CONTRADICTED |

### 2.5 doc/procedures/doc-search.md

| # | Claim (line) | Evidence | Verdict |
|---|---|---|---|
| D1 | Garbled duplicated clause: "… Deliberately NOT in `make qa`: It is deliberately NOT in `make qa`: …" (L130); adjacent sentence L132 "perf is the single home for rc + wall-clock budgets" while L123–127 correctly say the freshness gate moved to `make search-fresh`. | Editing residue in a file whose surrounding paragraphs are all correct (refresh commands, `--check` exit doctrine, search-fresh/advisory wiring verified against ground truth). | GARBLED / partially contradicted context |

### 2.6 doc/procedures/embeddings.md

| # | Claim (line) | Evidence | Verdict |
|---|---|---|---|
| E1 | "`rebuild_note_search.py --check` is a dry run: it walks every doc, embeds it, prints 'would index N docs', and writes nothing to research.db" (L41–55). All quoted statements remain true — but the description predates `ef980d14`. | Since `ef980d14`: `--check` also prints the FRESH/STALE drift verdict (`_print_staleness`) and exits **1** when the index is stale (`main()`), matching doc/script rebuilder parity. An operator chaining `--check` into an apply step hits a nonzero exit whenever findata drifted. Model/CLI/sidecar claims in this doc otherwise fully aligned with code constants (`local_embedder.py:49-55`). | PARTIALLY-STALE (missing new verdict + exit code) |

### 2.7 doc/procedures/markdown_parse.md

| # | Claim (line) | Evidence | Verdict |
|---|---|---|---|
| P1 | "To backfill existing rows: `python3 helpers/maintenance/backfill_valid_from.py --apply`." (L66) | No `*valid_from*` file exists anywhere in the tree; sibling reference `backfill_okf_provenance.py` is real. Dead command path. | CONTRADICTED |
| P2 | "adds ~2–5s on the current 950-entity graph" (L29) | Current `entities` count far higher (1,517 total incl. editions/themes/sub-sectors). Rotted statistic inside a timing anecdote. | PARTIALLY-STALE |
| P3 | Internally inconsistent stage numbering: the doc calls manual enhancement "Stage 5" (L11–13, :87), but :535/:585 say `sync_sector_wikilinks` runs "as the first Stage-5 step of --apply" — true only under `parse_newsletter.py` numbering, where stages are 0–6 and stage 5 = validate, first tuple entry `("sync_sector_wikilinks", …)` (:635-641). `architecture.md` uses the code numbering. Nothing tells the reader the two schemes differ. | Internal inconsistency resolvable with one disambiguating parenthetical; verifiable against code. | INTERNALLY INCONSISTENT |
| P4 | "Adding an Entity" snippet inserts `market_cap` into `entities` and writes membership edges to table `relations` via MCP tool call (~L371–399). | `helpers/core/db.py:77-78`: `entities.market_cap` column dropped (Bundle C2, 2026-07-28; derived from tags); `relations` is a backward-compat VIEW over `graph_edges`; the dual-MCP subsystem is removed. Following the snippet literally today fails/is rejected. | PARTIALLY-STALE (historical pseudo-code needs a caveat, not a rewrite) |

Everything else in markdown_parse.md checked out: argparse/--apply/--with-analytics semantics, pdf engine matrix (`--engine auto|local|paddle`), tag grammar, validation-chain order, capture-helper flags, integrity exit-code claim.

### 2.8 frontend/README.md

| # | Claim (line) | Evidence | Verdict |
|---|---|---|---|
| U1 | "`static/entity_detail.js` and `static/script.js` remain hand-written vanilla JS — this is a localized pilot" (L60–61) | Neither file exists (`git ls-files static`, repo-wide find). Loaders reference committed bundles: `templates/entity_detail.html:157` → `entity.bundle.js`, `templates/findata.html:481` → `findata.bundle.js`. Entity page went TS (ui_redesign arc, S6). | CONTRADICTED |
| U2 | Layout tree lists only `src/findata.ts` (L8–16); build said to emit a single `../static/findata.bundle.js` (L21–23) | Actual tree: `src/entity.ts`, `src/core/{dom,router,markdown,api,toast}.ts`, `src/views/{graph,stats,companies,sectors,docs}.ts`; `frontend/package.json:7` builds TWO esbuild entries → both committed bundles. `make frontend`/`make frontend-check` recipes themselves match the doc. Commands correct; tree/bundle inventory stale. | PARTIALLY-STALE |
| U3 | "the deploy (Dockerfile / nixpacks) stays 100% Python" (L28) | No Dockerfile exists anywhere; deploy config is `nixpacks.toml` alone. | PARTIALLY-STALE |

Consistent: IIFE format claim, `window.viewer` re-attach, committed-bundle policy, strict/noEmit tsconfig.

### 2.9 Surfaces audited clean (no change planned)

- **AGENTS.md** — every command/flag/exit-code claim exact against
  `doc_query.py`/`script_query.py` argparse, the `search-fresh` recipe, the
  gate runner, app endpoints/port. Cosmetic simplification of the script_query
  output-format sentence noted, not actionable under the verify-only rule.
- **doc/architecture.md** — all ~25 tooling-map paths, lazy-import claims,
  make targets, duckpgq/NetworkX retirement verified; contains no sentence
  invalidated by either `ef980d14` behavior change. One completeness nit
  (tooling map doesn't list `procedures/doc-search.md`): additive, out of scope.
- **doc/graph_design.txt** — no statement contradicted by the flock change
  (§8 states only DuckDB's own one-RW/N-RO rule, which is unchanged). Adding a
  decision-log entry for the build lock would be *new authoring*, not repair —
  deferred to its own ask.
- **doc/improvements/proposals/README.md + archive/README.md** — lifecycle
  rules true; sampled links 7/7 resolve.
- **pyproject.toml vs README** — `requires-python >=3.14` and
  `uv sync --all-extras` valid.
- Historical logs (`completed.md`, archived proposals) excluded per immutable-
  history rule; used as evidence only.

---

## 3. Planned fixes (all minimal, in place)

> **File-state note (2026-08-27 review):** completed.md #166 (embed-store
> execution) edited three target files AFTER this audit was drafted
> (`procedures/doc-search.md`, `procedures/embeddings.md`,
> `architecture.md`). D1 and E1 were re-verified present verbatim on
> 2026-08-27 — content unchanged, line numbers shifted. Patch against
> current file state.

1. **README.md** — tests `2,574` → fresh collect-only count (**expected ≈2,590 post-#166**, never the audit-time 2,581) in both places; replace the 4-namespace mirror sentence with the actual nine; bump doc_search hedged counts to values re-measured after this execution session's own doc edits settle.
2. **Makefile** — add the three `*-search-check` items to the `advisory` echo help AND target annotation (keeping the L29 sync rule and the alphabetical help-line order untouched); reconcile `metrics-rebuild` echo/annotation wording against the recipe's real payload — **arbiter is `helpers/maintenance/enrich_from_yfinance.py`'s actual behavior, not a vote between the two existing strings** (the target's own echo hint — "competes_with moved to relations-enrich" — suggests the annotation's "+ notes from yfinance" is the rotten half; confirm by reading the script once before writing).
3. **doc/schema.md** — schema_version `"9"` → `"13"`; rewrite the inventory block from the current manifest (28 objects) with the corrected `v_embeddings` provenance; fix the mirror sentence; append the `note_tags` row to the registry table copied verbatim from `_CHECKS`.
4. **doc/findata.md** — mirror-sentence correction (same nine namespaces, rest note-only).
5. **doc/procedures/doc-search.md** — remove the duplicated garbled clause and reconcile the neighboring sentence so the freshness-gate paragraph reads coherently.
6. **doc/procedures/embeddings.md** — one added sentence: `--check` reports the drift verdict and exits 1 when stale (post-`ef980d14` parity with doc/script rebuilders); keep the still-true "writes nothing to research.db". While there, align the same paragraph's lingering "sidecar cache" wording to the file's post-#166 "pooled store cache" terminology (one phrase).
7. **doc/procedures/markdown_parse.md** — drop/replace the dead `backfill_valid_from.py` pointer (successor mechanism named only if verifiably identified); update the rotted entity figure; one parenthetical resolving the Stage-5 double meaning; short caveat above the legacy entity-insert snippet noting `entities.market_cap` was dropped 2026-07-28 (derived from tags) and membership edges belong in `graph_edges`, not the `relations` view.
8. **frontend/README.md** — correct the bundle story (two esbuild entries → `findata.bundle.js` + `entity.bundle.js`, no hand-written static JS), extend the layout tree with `src/entity.ts` + `core/` + `views/`, name `nixpacks.toml` without Dockerfile.

## 4. Verification & wrap-up

- Re-run `.venv/bin/python3 -m pytest --collect-only -q` immediately before writing the test-count figure (post-#166 expectation ≈2,590 — see R1).
- Every replaced number/constant re-read from source at edit time (`ALLOWED_CATEGORIES`, `_SCHEMA_VERSION`, `_EXTRA_MATERIALIZED`, `_CHECKS`, `run_gate_report.py` step list, package.json build script — plus a one-read of `enrich_from_yfinance.py` as M2's arbiter).
- Targeted run of only the Makefile help-sorting pytest guard; **no full gates** (etiquette: qa/perf/advisory once at end on explicit ask).
- End-of-session search freshness: `rebuild_doc_search/rebuild_note_search/rebuild_script_search --incremental` then `--check` green on all three (house rule).
- Deliverable report lists every changed file with its doc-line → evidence mapping, plus the explicit no-change list above.

### Self-lifecycle (added 2026-08-27 review round)

On execution this proposal follows its own house checklist
(`proposals/README.md`): Status → EXECUTED; archive move to
`../archive/tooling/`; completed.md entry (**next free number #166 is taken
by the embed-store execution — use #167**); proposals-README live pointer
reset to *(none)* in the same change.

### Explicitly deferred (unchanged by review)

- architecture.md tooling-map row for `procedures/doc-search.md` — additive
  completeness, violates repair-only ground rule; stays out unless the user
  opts in at review.
- graph_design.txt build-lock decision-log entry — new authoring, own ask.

### Execution results (2026-08-27)

- README.md: tests **2,590 across 127 modules** both places (fresh
  collect-only matched the predicted ≈2,590 exactly); doc_search corpus
  re-measured at write time **66 files / 519 sections**; script_search
  units bumped to 198 (drifted with #165/#166 helpers); nine namespaces.
- Makefile: advisory echo + annotation now enumerate the three
  search-freshness rows (sync preserved, alphabetical order untouched);
  metrics-rebuild help line corrected to "company financials + note
  industry sections" — runtime truth per `enrich_from_yfinance.py:671`
  ("competes_with moved to enrich_relations.py"); the TARGET annotation
  ("+ notes") was already right. Leftover noted, out of scope: the
  script's own docstring header still advertises competes_with edges.
- schema.md: v13 + 28-object inventory (breakdown v_node+7 projections+
  2 embedding tables+17 e_*+_build_meta), live row counts, v_embeddings
  provenance fixed to query.py CTAS, new `v_note_embeddings` row, nine-
  namespace sentence, `note_tags` registry row appended.
- findata.md nine namespaces; doc-search.md garble repaired coherently;
  embeddings.md exit-code sentence + pooled-store terminology;
  markdown_parse.md dead backfill pointer dropped, ~1,500-entity figure,
  orchestrator-numbering parentheticals x2, legacy-snippet caveat;
  frontend/README real tree incl. src/core|views, two-bundle build story,
  nixpacks.toml without Dockerfile, no-hand-written-static-JS reality.
