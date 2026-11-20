---
title: "Harvest the 2026-11-20 estate hunt — census record (verdict: REJECTED) and gated probe promotions"
status: deferred
filed: "2026-11-20"
executed: "2026-11-21"
completed_md: "550"
area: "doc/improvements + helpers/graph + helpers/misc + frontend + app.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Harvest the 2026-11-20 estate hunt — census record (verdict: REJECTED) and gated probe promotions

**Date:** 2026-11-20 · **Status:** ARCHIVED 2026-11-21 (status:
deferred — S1/S5/S2/S4 executed, S3 MCP server stays deferred with its
gates; the census verdict is **REJECTED** (§2.4; the lane is recorded,
not pursued) — execution results in the Execution Results section, full
gate record in the completed.md entry) ·
**Area:** `doc/improvements` + `helpers/graph` + `helpers/misc` +
`frontend` + `app.py`

> **Follows:**
> `doc/improvements/archive/graph/graph_lane_unlocks.md` §5 (the
> unmet-trigger register — the U5 row this census closes) and §6 (non-goals);
> `doc/improvements/archive/graph/hgx_unlock_shelf_probe.md` (2026-10-28
> motifs NO-GO + 2026-11-02 re-probe, lanes 1–6);
> `doc/improvements/archive/graph/algorithms_adoption_sweep.md` (#494 —
> S3 landed the PAIR-overlap census `helpers/graph/hyper_motifs.py`);
> completed.md #549 (`hgx_unlock_lanes_and_emeans` — landed
> `hyper_spectral_cluster.py`, the `--orders reducible` default and the
> wrapper CLIs), #472 (spectral/RW measurements), #461 (price-lane arc —
> the MCP sequencing blocker, now executed).

## 1. Motivation

The 2026-11-20 estate hunt (session sweep via master_query / convo_query /
ripwire over the register, pending.md and the tree) produced a ranked
3-tier findings list. The operator then ordered the one unmeasured Tier-1
item — the U5 SQL-native motif census — run read-only, and afterwards asked
where the findings should live. Nothing in `doc/` held them (verified:
every number below existed only in session chat before this document).
House options were: pending.md bullets (too small for tables), an archive
note (wrong — the lane is not executed), or a proposal. This proposal is
the record.

**Why "REJECTED" lives in prose, not frontmatter.** `rejected` is not a
representable status anywhere in the house: `doc/okf/frontmatter.proposal.v1.json`
enum is `["proposed","executed","deferred"]` and
`helpers/validators/static_checks.py` `check_proposal_lifecycle`
enforces it (live top-level proposals must be `status: proposed` with
null `executed`/`completed_md`; archived must be `executed` or
`deferred`). Rejected verdicts are house-recorded in prose (pending.md's
merge-hypothesis REJECTED; the LanceDB/UQA/LatticeDB single-store
candidates REJECTED) — this document does the same, in the bold line and
§2.4. At close-out the file archives as `status: deferred` (the
record-plus-reopen-trigger shape, precedent
`archive/graph/hgx_unlock_shelf_probe.md`) with a unique completed.md
number (> ## 549, today's max).

## 2. Evidence (measured 2026-11-20, this box)

### 2.1 The U5 SQL-native motif census — scope and global result

Store at census time: `hyper_edges` 4,636 / `hyper_incidences` 59,657;
max(`hyper_edges.created_at`) 2026-11-19 08:20:47; duplicate
(`edge_id`, `entity_name`) pairs 0 (so set semantics = bag semantics).
Brute force is infeasible — all-edge itertools enumeration is 496,912,865
triple instances (one index edge has k=999 → C(999,3) ≈ 1.66e8); the
census therefore caps edge size at k ≤ 40 and runs as one GROUP BY
(Appendix A2, 5.95 s):

| scope | edges | incidences |
|---|---|---|
| k ≤ 40 census scope | 3,046 / 4,636 (65.7%) | 29,945 / 59,657 (50.2%) |
| excluded mega-edges | 164 | 21,965 (max sizes: index 999, country 850, group 276, subsidiary 185, supply 212) |

| quantity (k=3..40, set semantics, cross-family deduped) | value |
|---|---|
| distinct 3-node motifs | 1,258,775 |
| total with multiplicity | 1,563,524 |
| max multiplicity | 51 |
| multiplicity = 1 | 1,045,367 (83.0%) |
| multiplicity = 2 | 147,428 (11.7%) |
| multiplicity ≥ 3 | 65,980 (5.2%) |
| cross-edge multiplicity ≥ 2 | 213,408 (17.0%) |

### 2.2 Per-family census

Family rows count each family separately — a triple present in two
families appears in both rows (Σ families 1,378,553 distinct vs the
1,258,775 cross-family-deduped global). Edges / distinct triples /
mult≥2 / share / max mult:

| family | edges | distinct | mult≥2 | share | max |
|---|---|---|---|---|---|
| subsidiary | 1401 | 398,587 | 33,980 | 8.5% | 40 |
| group | 456 | 450,519 | 18,429 | 4.1% | 4 |
| supply | 644 | 245,138 | 5,493 | 2.2% | 50 |
| edition | 265 | 69,706 | 53,734 | **77.1%** | 8 |
| industry | 85 | 56,321 | 0 | 0.0% | 1 |
| sub_sector | 77 | 47,083 | 0 | 0.0% | 1 |
| index | 31 | 33,043 | 2,192 | 6.6% | 4 |
| sector | 32 | 50,450 | 0 | 0.0% | 1 |
| org_membership | 29 | 11,110 | 56 | 0.5% | 2 |
| theme | 9 | 16,533 | 21 | 0.1% | 2 |
| event | 11 | 14 | 5 | 35.7% | 3 |
| country | 5 | 48 | 0 | 0.0% | 1 |
| merger | 1 | 1 | 0 | 0.0% | 1 |

4-node variant (5 families, k ≤ 40): distinct 7,268,131; mult≥2 744,159
(10.2%); max 39 — feasible, same shape.

### 2.3 Mechanisms — why the zeros are zeros and edition is 77.1%

- **Partition families are definitional zeros.** industry (120 edges /
  1,013 incidences), sub_sector (112 / 1,011) and sector (42 / 1,226) are
  partitions — each company sits in exactly one edge per family, so no
  two edges share a member and no 3-set can repeat. Max
  member-edge-overlap per family: sub_sector 1, sector 2, industry 2
  (mathematically zero) vs subsidiary 92, supply 99, edition 25, group 4,
  index 4, theme 3, event 3. The same logic explains event (1,123 edges
  but only 11 at k≥3), country, jv and demerger.
- **Edition's 77.1% is a temporal-panel artifact, not social structure.**
  An edition is a filer set — one listed parent's disclosed portfolio
  re-derived per period, so consecutive-period snapshots reproduce
  identical triples. Signature: a spike at multiplicity exactly 3
  (34,856 triples); top motif ('Axis Bank', 'HDFC Bank', 'ICICI Bank')
  at mult 8 spanning 42 distinct companies.
- **Cross-family repeats are corporate-structure co-membership.** Of the
  213,408 repeated triples, 110,220 (51.6%) are cross-family; 9,235
  appear in ≥ 3 families. Pairs are almost entirely group+subsidiary
  (31,709) and group+supply (31,677); subsidiary+supply 16,707;
  edition+index only 127. This agrees with the landed pair-overlap census
  (`helpers/graph/hyper_motifs.py`, #494: supply ENRICHED +54.6/+59.6):
  structure concentrates in supply/subsidiary/group co-membership.

### 2.4 Corrected U5 record — verdict REJECTED

Store facts that reframe the old record: weighted incidences 1,696
(2.8%) — only edition (1,409/3,466) and org_membership (287/287),
every other family 0; `direction` non-null 0; `role` non-null 298
(holder 82, promoter 205 — org_membership, 24 edges each; merger
constituent 6 / resultant 1; demerger parent 2 / resultant 2). Entity
purity: org_membership 287/287 person; subsidiary 14,984 company + 2
institution; supply 8,567 company + 6 person + 2 institution; event
2,253 company + 7 person; group/edition/index/industry/sub_sector/
sector/theme/country all company.

Role-triple census (the only ordered-structure signal in the store):
9 distinct ordered shapes / 11,167 total — promoter×3 9,708, holder×3
853, promoter/promoter/holder 199, promoter/holder/promoter 173,
holder/promoter/promoter 150, promoter/holder/holder 32,
holder/holder/promoter 29, holder/promoter/holder 22,
resultant/constituent/constituent 1.

Visibility correction: the 2026-10-28 NO-GO figure "order-3 sees 24.0%
of incidences" was measured on the then-store (2,420/10,083). Today,
store-size-independent, edges with k ≤ 3 hold 4,253 of 59,657 incidences
= 7.1% — i.e. full-visibility instruments see strictly more than the old
one did, and the verdict below is measured AT 50.2–100% visibility, not
at the old defect.

**Verdict: REJECTED as a production motif lane.** The #451 NO-GO stands —
but NOT because of the visibility bug: at 50.2–100% visibility the motif
signal is (a) definitional zeros in partition families and (b)
period-panel duplication in edition. Non-trivial motif structure is thin
where it matters and artifactual where it is thick. The consumer trigger
is the only thing that changes the verdict. **Refined reopen trigger**
(replaces the register's "a hypergraph-structure consumer appears"):
**a consumer needs motif/overlap QA on a deduplicated panel** (edition
deduped by (period, filer) via `properties->>'period'` — the
within-period within-family census is the proposed variant, not run).

### 2.5 The estate-hunt inventory (tiers, measured 2026-11-20)

| # | finding | measured state | disposition |
|---|---|---|---|
| T1.1 | U5 SQL motif census | this §2 — verdict REJECTED | recorded (S1) |
| T1.2 | community-lane gate absent | `helpers/graph/hyper_communities.py:178` `def sweep_k(incidence, *, ks: list[int], seed: int, n_iter: int, allow_giant: bool = False)` — `allow_giant` in the signature (:184) is never read in the body; real filtering is `_exclude_degenerate(raw, allow_giant)` :83/:97/:107, called from `_cli` :293/:331 | S2d: wire or remove |
| T1.3 | `hyper_spectral_cluster.py` shipped untested | #549 landed it (e-means default, `--orders reducible\|3,4,5\|all`, `--apply`, `__main__` :168); NO Makefile target, NO tests (`tests/test_hyper_spectral.py:17` imports the OTHER module `hyper_spectral`); verdict from #549: ARI ≈ 0 at every representative order (max 0.0418 at order 101) — the named entity-clustering ask remains open | S2a: pin the no-signal verdict as a QA test |
| T1.4 | HGX wrapper trio unwired | `hyper_core_periphery.py` (99), `hyper_hysc.py` (130), `hyper_hypergraphmt.py` (122) — zero references outside their own files and doc/ (no make target, no bench, no route, no test) | S2b: written per-file disposition |
| T1.5 | hyper_predict bank unwired | `helpers/graph/hyper_predict.py` (305; lanes jaccard / s_walk s∈{2,3} / stationary_pi via HypergraphX `RW_stationary_state`; star-native; `--apply` writes `hyper_link_prediction`) has exactly one importer — `helpers/bench/hyper_predict_bank.py:35`; the bank (`temporal_split` :116, `retrospective_holdout` :149, report-first, floors in header) has no make target, no tests | S2c: one command + test; realize the prereg |
| T2.1 | MCP server unblocked | parked 2026-10-31 behind the price-lane arc → that arc EXECUTED (#461); zero MCP surface repo-wide (grep `model context protocol\|\bmcp\b` → only `.venv/lib/python3.14/site-packages/mcp/`); the SDK is installed; `app.py` has 39 `@app.route` + 8 search CLIs + the TUI — all single-harness | S3: read-only MCP server |
| T2.2 | helpers test coverage | stem-level textual-mention sweep (`grep -rqw <stem> tests/`): **32/286** modules unmentioned (Appendix A4; the hunt report's same-day figure was 31 — the tree moved mid-arc; the command is the canonical form) | S4: test or disposition each |
| T2.3 | frontend/desktop test gate | `frontend/package.json` scripts = build + typecheck ONLY, yet the Makefile `frontend-check` target (:838-843 as measured) runs `bun test tests` — blind, no test script exists; only `frontend/tests/escaping.test.ts` + `guards.test.ts`; `desktop/src-vue/package.json` has `test:contract` (`node --test test/contract.test.js`) and there is no `desktop/package.json` | S5 |

### 2.6 Measured dead ends — do not re-audit

| candidate | measured verdict (date) |
|---|---|
| HGX `compute_motifs` as the census instrument | NO-GO — `get_edges(size=order, up_to=True)` visibility defect; superseded by the full-visibility SQL census above (2026-10-28 → 2026-11-20) |
| Rust cosine kernels | 0.47–0.49× vs numpy, refuted with rayon (2026-11) — BFS only fired (76.6→11.3 s; #497) |
| numba Phase C | needs ≥ 500k-row matrix; closest-ever is 179k |
| msgspec/msgpack adoption | rejected in an earlier arc — recorded in completed.md |
| vss HNSW macros | recall ~57% @16.5k, +75% size, "opclass not supported" |
| MAX-engine GEMM rung | consumer is single-query brute force |
| LanceDB ANN sidecar | 190 ms cosine @179,264 rows — SHELVED with triggers |
| duckdb_20_sqlite re-entry | sized 17–24 days, deferred; now superseded by the LIVE swap proposal |
| onager personalized PageRank | personalisation vector ignored at `helpers/graph/onager.py:1099` — not universally degenerate (Infosys hop≥2 0.038 vs HDFC Bank 0.508) |

## 3. Design (gated menu — each slice independently landable, none started)

Order matters only within S2. S1 is this document. Everything else waits
for the operator's go per slice (house rule: full gates ONCE per arc, on
the user's go; fixes validated by TARGETED tests only).

- **S1 — land the record (no code).** This file + its README bullet
  (filed untracked per house rule). Close-out: `git mv` to
  `../archive/graph/` as `status: deferred` + unique completed.md
  number (> 549) + pending.md sweep + archive/README line, all in one
  change.
- **S2 — hypergraph probe promotions.**
  - **S2a** `hyper_spectral_cluster`: add `tests/test_hyper_spectral_cluster.py`
    pinning the ARI ≈ 0 verdict (max 0.0418 @ order 101) on a seeded
    fixture — so a future store change that CREATES spectral community
    structure fails loudly instead of decaying; add the make target.
  - **S2b** wrapper trio: write a per-file disposition in each docstring —
    wire (make target + test) / retire / fold into graph_analytics. No
    silent dead code.
  - **S2c** `hyper_predict` + bank: one-command bank run (make target) +
    a targeted test; then realize
    `doc/local/evaluations/linkpred_precision_prereg.md` (filed
    2026-10-31 BEFORE measurement): realized events = `graph_edges`
    `edge_type='acquired'` (56 rows as of 2026-11-20) with
    `valid_from` STRICTLY AFTER the snapshot's `computed_at`
    (2026-09-24..2026-10-30, ~10.6k rows in
    `bench_data/alpha_research/linkpred_snapshot.parquet`);
    precision@K, K∈{10,25,50} vs base rate. Report-first, no store writes.
  - **S2d** community gate: wire or remove `allow_giant` from
    `sweep_k`; then re-run U6 index-community fit as the confirmatory
    (EM collapse at k=12/16, modularity 0.0000 recorded 2026-11-02).
- **S3 — read-only MCP server.** Expose master_query + the seven index
  CLIs + gate_query as MCP tools (stdio), zero-write; `research.db`
  writes stay exclusively behind `helpers.core.db.connect()`. The
  estate stays single-writer/single-harness by design — S3 is a read
  mirror, not a deployment.
- **S4 — coverage backfill.** Every Appendix A4 module gets a targeted
  test OR a written disposition (wire / retire / transitive-coverage
  note). Textual mention ≠ coverage — verify transitively-exercised
  modules before writing redundant tests.
- **S5 — javascript test gate.** Give `frontend/package.json` a real
  `test` script (or repoint `frontend-check`), and wire the desktop
  contract test into a make target. `bun test tests` on a runner-less
  package.json is a blind target today.

## 4. Acceptance criteria & shakedown

1. **S1** — `python3 -c "from helpers.validators.static_checks import
   check_proposal_lifecycle; assert check_proposal_lifecycle() == ([], [])"`
   stays green with this file present, and
   `pytest tests/test_static_checks.py -k proposal` passes (targeted).
   Findability: after the next `make search-fresh APPLY=1` (main tree),
   `doc_query.py "motif census REJECTED"` hits this file.
2. **S2a/S2c** — new targeted tests pass standalone; ≥ 2 consecutive
   bank runs byte-identical (report-first; no single-run timing claims).
3. **S2b** — `rg hyper_core_periphery|hyper_hysc|hyper_hypergraphmt`
   beyond the files themselves and doc/ shows either a wiring (target/
   test) or the disposition line — never silence.
4. **S2d** — `sweep_k`'s signature matches its body (parameter read or
   gone); one CLI run exercises it.
5. **S3** — server starts over stdio; tool list ⊇ the 8 CLIs; every tool
   read-only; no new `research.db` write paths.
6. **S5** — `make frontend-check` passes with a real runner present;
   one make target reaches the desktop contract test.
7. **Eval gate** — NOT triggered by S1–S5 as specified: no slice changes
   query-visible semantics (rosters, crosswalks, hierarchies, extractor
   rules). Any slice that grows to do so gains the mandatory
   `ontology_eval_gate.py` bullet before landing.

| Projected outcome | Today | After |
|---|---|---|
| census record | session chat only | this doc, doc_query-findable |
| `allow_giant` | dead parameter | wired or removed |
| wrapper trio | 0 non-doc references | dispositioned |
| hyper_predict bank | no target, no test | one command + test |
| linkpred prereg | registered, unrealized | realized vs 56 acquired edges |
| MCP surface | 0 | read-only server over the 8 CLIs |
| stem-unmentioned helpers modules | 32/286 | 0 (tested or dispositioned) |
| frontend test runner | absent, check target blind | real script, green check |

## 5. Risks

- **Store drift vs recorded numbers** — the store grew 1,708 → 4,636
  edges between 2026-10-28 and 2026-11-20 (event k≥3 went 0 → 11 in
  days). The census tables are a snapshot; Appendix A2 is one
  copy-pasteable query — re-run, don't trust.
- **Promotion scope creep** — S2 is a menu; each slice needs its own go
  and its own targeted validation, never a bundled mega-arc.
- **MCP read-only promise** — any write-capable tool would surface the
  FK/agent-provenance rules (`helpers.core.db.connect()`, registered
  `provenance_agents`) to an outer harness; writes stay out of scope
  and the app remains undeck-registered (Security Phase 4 stays
  trigger-gated on deployment).
- **Textual-mention coverage counts lie** — a module can be exercised
  transitively; S4 must verify before backfilling redundant tests.

## Execution Results (2026-11-20, S5 + S2 + S4)

S3 (MCP server) was deferred by the operator before execution — it
stays a gated menu item; nothing was built.

- **S5 (landed, riding `harvest_census`)**: `frontend/package.json`
  gained a real `test` script (`bun test tests`); `frontend-check` now
  runs `bun run test` (single source of truth — the gate fails loudly
  if the script goes missing); new root target `desktop-contract`
  delegates to `desktop/Makefile contract` (npm-based per the desktop
  doctrine). Validated: `make frontend-check` green (23 bun tests +
  tsc + prettier + guard regeneration), `make desktop-contract` green
  (14 node:test cases).
- **S2a**: `tests/test_hyper_spectral_cluster.py` — 3 hermetic pins on
  an 80-company grid fixture (taxonomy-aligned order recovered,
  ARI>0.5; taxonomy-crossing order recovers its own structure exactly
  — measured 1.0 — while ARI vs sector stays <0.1; mixed orders <0.1
  at CLI defaults). CLI gained a `--max-ari` QA guard; new target
  `hyper-spectral-check` (guard 0.15). The live run reproduces the
  verdict EXACTLY: max ARI 0.0418 @ order 101, representative orders
  [8, 25, 26, 47, 86, 101], eta=0.0582. Side-find: scikit-learn had
  been pruned from `.venv` (installed by #549 but left undeclared — a
  later `uv sync` removed it, breaking the spectral/HySC/HypergraphMT
  lanes); re-declared in the pyproject `lens` extra and reinstalled
  (1.9.1, the #549 version).
- **S2b**: Standing dispositions written into all three wrapper
  docstrings — RECORDED PROBE, trigger-gated; `hyper_hysc` carries the
  duplicate-seat note (fold-under-hyper_communities guidance before
  any wiring). §4.3 acceptance: rg beyond own files + doc/ = empty;
  the docstrings are the record.
- **S2c**: `tests/test_hyper_predict.py` (9 hermetic tests over the
  scoring lanes and the bank's pure core); new target
  `hyper-predict-bank`; two consecutive runs byte-identical (banked at
  `bench_data/hyper_predict_bank/`). Bank findings: edition is the
  only family with nonzero recall (jaccard @10 0.0024, @50 0.0076,
  @100 0.0098; partition families 0.0 by construction — held-out pairs
  share no train hyperedge); EVERY temporal leg skipped — no
  prediction-grade family carries `valid_from` yet (edge dating
  unpopulated; the leg lights up only when dating lands). Prereg
  REALIZED: 0 scoreable events — 56 acquired edges total, 32 dated,
  max(valid_from)=2026-08-24 PREDATES every snapshot generation (min
  computed_at 2026-09-24): 25 circular + 7 acquirers with no candidate
  row; outcome-log row appended to `linkpred_precision_prereg.md`;
  the ≥10-deal floor stands at 0/10.
- **S2d**: `allow_giant` REMOVED from `sweep_k` (degeneracy filtering
  is `_exclude_degenerate`'s job, applied upstream since S18; the
  parameter was never read). `test_hyper_incidence` sweep_k test
  green; one CLI run exercised the new signature. U6 confirmatory
  re-run (`bench_data/u6_index_community/`): anchors REPRODUCE —
  all-index k=16 NMI 0.4519 (recorded 0.4542), broad-cap k=8 0.1067
  (recorded 0.1100) — but the EM-collapse-to-zero does NOT (k=12
  0.1090, k=16 0.1306; 9/13 effective blocks): today's values sit AT
  the recorded random-partition null (0.1324). Conclusion CONFIRMED —
  broad-cap index blocks carry no sector signal, U6 stays gated —
  while the mechanism drifted on the grown store (54 index edges, 20
  broad-cap by today's keyword rule vs 28 then).

### S4 — stem-unmentioned coverage dispositions (29 on 2026-11-20)

Canonical sweep re-run after this arc's landings: 29 unmentioned —
`hyper_predict`, `hyper_predict_bank`, `hyper_spectral_cluster`
cleared by the new test files (29 + 3 = 32, the hunt's count).
Dispositions (transitive rows cite verified import lines, not
comment-mentions):

| module | disposition |
|---|---|
| forkmap | transitive: `algorithms.py:81` + `scipy_bridge.py:83` import it (both tested) |
| route_skeleton | transitive: `static_checks.py:2013` (tested) |
| derive_cli | transitive: `derive_themes.py:45` + six derive_* siblings (all tested) |
| algorithm_wrappers | transitive: `query.py:5171` (tested) |
| mca_cin_sync | transitive: `mca_cin_resolve.py:45` (tested) |
| ocr_pin | transitive: `review_selection.py:42` (tested); also make-wired |
| maint_timing | transitive: `rebuild_common.py:658` + 13 maintenance importers (tested) |
| git_query | operator CLI (git-history front door); subprocess-driven at `search_tui.py:632` (tested) |
| maint_query | operator CLI — maintenance-timing query leg, gate_query's sibling |
| backfill_quotes_attribution | operator CLI — S21 one-shot quotes re-attribution backfill |
| seed_note_gemma_cache | operator CLI — gemma full-pool embed seeding (notes_gemma_adoption S1) |
| worklist_park | operator CLI — review-kit export side; `derive_countries` consumes its journal (file contract, not an import) |
| anchor_stability | recorded eval instrument — S2 anchor census (re-ingest identity stability) |
| parquet_textconv | git textconv driver (`.gitattributes` diff=parquet) — exercised by git diff/show, never imported |
| llamacpp_health | operator CLI, `make llamacpp-health` |
| quack_health | operator CLI, `make quack-health` (shell-wired via quack_servers.sh) |
| quack_server_child | runtime child of the quack proxy (spawned by quack_servers.sh, not imported) |
| bili_watch_event | operator lane, `make bili-watch-event` |
| bili_thresh_replay | operator lane, `make bili-thresh-replay` |
| fuzzy_backend_bench | recorded eval instrument (bank doctrine) |
| git_eval_bank | recorded eval instrument (bank doctrine) |
| script_eval_bank | recorded eval instrument (bank doctrine) |
| nic_scorer_bench | recorded eval instrument (bank doctrine) |
| hyper_data_bench | recorded eval instrument (bank doctrine) |
| hyper_community_probe | recorded eval instrument — Wall-4 probe, banked 2026-11-02 (`bench_data/hyper_community_probe/`) |
| hyper_core_periphery | S2b Standing disposition (recorded probe, trigger-gated) |
| hyper_hysc | S2b Standing disposition (recorded probe, duplicate-seat note) |
| hyper_hypergraphmt | S2b Standing disposition (recorded probe, trigger-gated) |

The discipline cut both ways: `maint_query` and `seed_note_gemma_cache`
carry only comment-mentions in `master_query` / `rebuild_note_search`
and are dispositioned as operator CLIs, NOT claimed as covered;
`algorithm_wrappers`' only mention in `query.py` looked
comment-shaped but IS a real import at :5171.

## 6. Non-goals

- **No production motif lane.** The #451 NO-GO stands; the census verdict
  REJECTED above is the record, not a door left open beyond the refined
  trigger.
- Register §6 carries over verbatim: no reopening #456–#459 scope; no
  changes to `hyper_motifs.py` (the landed pair census stays as-is); no
  default-scope changes to `hyper_communities` beyond S2d's parameter
  fix; no role-vocabulary canonicalisation beyond #457 case-fold.
- **No perf-lane work** — the operator's earlier perf picks (permanent
  quick-win bench, regex anti-quadratic static check,
  `_extract_metrics_from_sentence` deep-dive) are parked and are not
  slices here.
- **No reopen of trigger-gated register rows** — LanceDB sidecar, DuckDB-fts
  lexical engine, vss HNSW, MAX GEMM, numba Phase C, T3 bipartite
  matching, onager PPR personalisation, P2.2 incremental materialization,
  Mojo disposition.
- **No app deployment / Security Phase 4.**

## Appendix — raw measurement log

**A1 — store at census (2026-11-20 19:41 IST, read-only):**
`hyper_edges` 4,636 / `hyper_incidences` 59,657; k=1 edges 87, k=2
1,228; family edges: subsidiary 1450, event 1123, supply 688, group 671,
edition 298, industry 120, sub_sector 112, index 54, sector 42,
org_membership 34, country 21, theme 12, jv 6, merger 3, demerger 2;
k≥3 per family: subsidiary 1450/1450, supply 688/688, group 594/671,
edition 265/298, industry 86/120, sub_sector 80/112, index 54/54,
sector 42/42, org_membership 29/34, theme 12/12, event 11/1123,
country 7/21, merger 1/3, demerger 0/2, jv 0/6. Schema note:
`hyper_incidences` has NO `edge_type` column — join
`hyper_edges e ON e.id = i.edge_id`; `journal_mode=wal`; raw
`sqlite3.connect` reports `foreign_keys=0` (the house
`connect()` is what enables FK).

**A2 — the census query (5.95 s, 1,258,775 rows):**

    SELECT a.entity_name, b.entity_name, c.entity_name, count(*) AS n
    FROM hyper_incidences a
    JOIN hyper_incidences b ON a.edge_id = b.edge_id AND a.entity_name < b.entity_name
    JOIN hyper_incidences c ON c.edge_id = a.edge_id AND b.entity_name < c.entity_name
    WHERE (SELECT count(*) FROM hyper_incidences x WHERE x.edge_id = a.edge_id) <= 40
    GROUP BY 1, 2, 3 ORDER BY n DESC;

**A3 — run log:**

| Run | Command shape | Result | Notes |
|---|---|---|---|
| 2026-11-20 | brute-force itertools triples, all edges | killed after hang | 496,912,865 instances; C(999,3) ≈ 1.66e8 for one index edge — infeasible |
| 2026-11-20 | A2 at k ≤ 40 | 1,258,775 distinct / 5.95 s | canonical census |
| 2026-11-20 | A2 at k ≤ 60 | 2,822,295 distinct; mult≥2 517,764 (18.3%); max 52; 8.7 s | stability cross-check vs 17.0% at k ≤ 40 |
| 2026-11-20 | 4-node variant, 5 families, k ≤ 40 | distinct 7,268,131; mult≥2 744,159 (10.2%); max 39 | feasible |
| 2026-11-20 | role-triple census | 9 shapes / 11,167 total (§2.4) | roles live only on org_membership/merger/demerger |
| 2026-11-20 | stem-level coverage sweep | 32/286 (A4) | command in A4 |
| 2026-11-20 | MCP grep | 0 repo code | only `.venv` site-packages |

**A4 — stem-unmentioned helpers modules (32/286, `grep -rqw <stem>
tests/` over `find helpers -name '*.py'`, 2026-11-20, lines per
module):** hyper_predict 305, hyper_community_probe 305,
backfill_quotes_attribution 299, mca_cin_sync 278, nic_scorer_bench 248,
maint_timing 235, route_skeleton 232, hyper_data_bench 208, maint_query
205, hyper_predict_bank 202, algorithm_wrappers 200, quack_server_child
189, ocr_key_map 177, hyper_spectral_cluster 169, git_eval_bank 159,
fuzzy_backend_bench 139, git_query 136, seed_note_gemma_cache 131,
llamacpp_health 130, hyper_hysc 130, script_eval_bank 126,
bili_thresh_replay 123, hyper_hypergraphmt 122, bili_watch_event 119,
hyper_core_periphery 99, ocr_pin 96, parquet_textconv 89,
anchor_stability 87, worklist_park 77, derive_cli 76, quack_health 73,
forkmap 70.
