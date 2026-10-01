---
title: "Split query.near_duplicate_notes behind a parity fixture that pins tie order, with the prune asserted through the stats seam"
status: executed
filed: "2026-10-01"
executed: "2026-10-01"
completed_md: "328"
area: "helpers/graph/query.py, helpers/graph/fixture_note_embeddings.py, helpers/misc/parity_harness.py, tests/"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Split query.near_duplicate_notes behind a parity fixture that pins tie order, with the prune asserted through the stats seam

**Date:** 2026-10-01 · **Status:** EXECUTED ·
**Area:** `helpers/graph/query.py:3650-3778`, new
`helpers/graph/fixture_note_embeddings.py`,
`helpers/misc/parity_harness.py` (REGISTRY), `tests/`

> **Position: this executes FIRST, ahead of `c901_d1_split_batch1.md`** (which
> holds `csr.try_shortest_path`, `extract_relations._process_pattern_matches`
> and `review_selection.main` as S1–S3). Operator sequencing 2026-10-01. The
> batch-1 proposal deferred this function as "batch 2" for a fold-collision
> reason that no longer holds — see §1.
> *(Historical note, added same day: batch 1 landed first after all —
> committed `63176a216` — so this arc executed second in wall-clock order;
> the sequencing constraint itself had already expired.)*

## 1. Motivation

Operator ruling 2026-09-30 (review findings collation S6, recorded in
`review_findings_collation.md`) schedules four C901 masks
for D1 splits; `../../pending.md` books them as named functions. This proposal
takes the one batch-1 explicitly excluded.

**The exclusion reason has expired.** `c901_d1_split_batch1.md` §1 deferred
this function because it *"sits in `query.py` — the file a parallel session is
actively reworking for P2.2 (the build path). Splitting it now risks a fold
collision."* Verified 2026-10-01: the P2.2 arc's entire diff is **8 files, all
under `doc/`, zero non-doc changes** — `git status -- helpers/ Mojo/ tests/`
is empty. No `query.py` diff exists, so there is nothing to collide with. The
P2.2 measurement was scratch-isolated by construction (a non-`DB_PATH`
`db_path` resolves to a sibling `.duckdb`), so it never needed a source edit.
`near_duplicate_notes` is additionally **byte-identical in `main` and
`graph_algos`** (verified by diff over lines 3650-3800), so the split lands
once with no divergence to reconcile.

**Why it still goes first despite being the riskiest of the four.** Three
separate correctness reworks are stacked in these 129 lines, each with an
explicit exactness claim, and the split has to preserve all of them:

| Rework | Claim that must survive the split |
|---|---|
| AVAIL-1 (`completed.md` #249) | memoised per generation; O(n²) self-join 59.31 s → 0.010 ms; 10,000-path ceiling → 503 |
| `near_dup_prune` (2026-09-26) | SQL join → one f64 GEMM: *"Exactness unchanged: same renormalized means, same `sim = 1 − d²/2 = cosine`, same `(dist, path_a, path_b)` ordering."* |
| `csr_lane_remediation` S4 (2026-09-28) | `S = X @ Xᵀ` materialises the **full P×P** matrix (8·P² bytes; 800 MB at the ceiling), not O(block·paths); bounded accumulator prunes at `4 × limit` and is exact |

The mask comment itself names the contract: *"fetch, renormalise, score,
group in one pass"*. **The pass ordering is the specification**, which is
exactly why the fixture (S1) has to land before the refactor (S2).

## 2. Evidence (verified 2026-10-01, this tree)

| Configuration | Result | Verdict |
|---|---|---|
| mccabe / branch-ish nodes | **12** over threshold; 129 lines (3650-3778); 18 branch-ish AST nodes | baseline — the mask |
| existing coverage | `tests/test_note_embeddings.py::TestNearDuplicateNotes` — top-pair identity, `sim ≈ 1.0`, threshold + doc_type filtering, `limit=0` → `[]` | **semantic only** |
| tie ordering (`(path_a, path_b)` break) | **not covered** — the only pair asserted is a single top pair | gap |
| `4 × limit` prune / truncation exactness | **not covered, and not coverable by parity** — see the correction below | gap (different instrument) |
| multi-section path collapse (`np.add.at` mean) | **not covered** — `note_con` injects single-section notes | gap |
| zero-norm guard (`norms[norms == 0] = 1.0`) | **not covered** | gap |
| `make parity` REGISTRY | 6 entries; **none covers this function** — `_resolve_ladder`, `extract_theme_membership`, `extract_citations`, `l1_betweenness_compute`, `bfs_path`, 2 canaries | baseline — the §4.3 gate is unmet |
| P2.2 arc diff scope | 8 files, all `doc/`; 0 non-doc | the fold-collision premise is dead |

**Correction to a claim made in the 2026-10-01 triage:** the memo is **not**
inside this function. `app.py:402` derives
`key = (gen, doc_type, min_sim, limit)` where `gen = _graph_build_etag()`
(`_build_meta.built_at`), and `app.py:421` calls
`near_duplicate_notes(con, min_sim=..., doc_type=..., limit=...)`. The
function's own docstring says it is *"deliberately NOT an API hot path and NOT
generation-cached"* — the generation cache lives in the API layer. This
**raises** rather than lowers the risk: the cache key is derived from the
caller's parameters, so a split that renames, reorders, or changes a default
would silently desync `key` from the inputs and serve stale results. That
cross-file invariant is asserted in S1.

**Cross-arc dependency (recorded, not blocking):** the vectors come from
`v_note_embeddings` (`query.py:3706`), materialised by
`_materialise_note_embeddings` — the same code P2.2's successors (the (ii)
statement-count fold and (iii) table-level dirty tracking, held in
`../graph/graph_pending.md` P2.2) would change. The fixture pins an
input surface a later slice may move; if (ii)/(iii) lands first, this
proposal's fixture builder must be re-checked against the new materialisation.

## 3. Design

Extraction-only, per the c901 S2–S5 house pattern and
`c901_d1_split_batch1.md` §3: helpers are pulled out, bodies delegate, no
behaviour change, each slice removes its function's noqa as it lands.

- **S1 — the parity fixture and the four coverage gaps (lands FIRST, on its
  own).** New `helpers/graph/fixture_note_embeddings.py` — a deterministic
  builder for a temp DuckDB carrying a `v_note_embeddings(file_path,
  doc_type, title, emb)` table (schema per `query.py:1556-1558`), seeded to
  exercise the gaps §2 lists: at least two pairs at **exactly equal**
  similarity so the `(path_a, path_b)` tie-break actually runs, a
  multi-section path (so the `np.add.at` collapse is live), a zero vector
  (so the norm guard is live), and both `doc_type` values. Register
  `near_duplicate_notes` in `parity_harness.REGISTRY` with
  `render(mod)` — the module object arrives by argument, never by name, per
  the harness contract (`parity_harness.py:28-32`) — calling
  `mod.near_duplicate_notes(con, ...)` across a parameter matrix
  (`min_sim` × `limit`, including `limit=0`) and returning a canonical repr.
  **The parity render deliberately excludes the prune**: the function's own
  docstring records that the 4× bound is *"unobservable from the return value
  — the final sort+truncate makes the result invariant to how much the buffer
  retains, so an output-equality test passes whether pruning runs or not"*,
  and that the `stats` seam exists so the bound can be *"asserted rather than
  merely inspected"*. So the prune is witnessed by a **test** through the
  `stats` channel (`prunes`, `peak`, `post_prune_max`), never by parity —
  pinning it in the render would add cost and prove nothing. What parity
  uniquely witnesses is the **tie order** and the **exact output bytes**,
  which is why the fixture's tie case is mandatory.
  Add targeted tests for the tie order, the `stats`-seam prune assertion,
  multi-section collapse, zero-norm, and the `app.py:402`
  key-covers-signature invariant.
  **Deliverable: `make parity near_duplicate_notes` green at `REF=HEAD`
  before S2 begins, and the `stats`-seam prune test red-then-green against
  the pre-split code.** The fixture is what makes S2's proof a proof.
- **S2 — the split.** Three extractions, each far under threshold:
  `_collapsed_note_matrix(rows) -> (X, paths, titles)` (the numerical core:
  first-seen dict ordering, `np.add.at` accumulation, `bincount` mean,
  zero-norm guard, renormalise — the "identical arithmetic" claim lives here
  and becomes independently testable); `_pair_mask(block, lo, P, min_sim)`
  (the self-pair and `a < b` masks); `_scan_top_pairs(S, paths, limit,
  min_sim, stats)` (the block loop, canonical string orientation swap, the
  `(-sim, path_a, path_b, a_i, b_i)` append, the `4 × limit` prune with its
  `stats` bookkeeping, final sort + truncate). The entry function keeps the
  `limit` clamp, the empty and `limit == 0` guards, the two-phase call, and
  the final `(path_a, path_b, title_a, title_b, sim)` projection. Its `noqa:
  C901` comes off in this slice.

## 4. Acceptance criteria & shakedown

1. `make parity near_duplicate_notes` green at `REF=HEAD` **before** S2, and
   still green after S2 — across ≥ 2 pinned `PYTHONHASHSEED` values, which is
   the `c901_complexity_debt` §4.3 gate. `make parity --strict` gates if the
   operator wants it to.
2. `rg -n 'noqa: C901' helpers/graph/query.py` → only the pre-existing
   `query.py:1591` and `query.py:4012` `_cli` masks remain; the
   `near_duplicate_notes` mask is gone. (Neither `_cli` mask is in the ruling
   — `query.py:1591` is booked to the next `c901_complexity_debt` pass by
   collation S7; `query.py:4012` is unruled. Both stay out of scope.)
3. Every extracted helper measures < 10 mccabe and the entry function ≤ 8,
   re-measured by the `lint-audit` census after landing — no knife-edge 10s.
4. pytest green: `tests/test_note_embeddings.py` (holds
   `TestNearDuplicateNotes`), `tests/test_api_graph_unit.py`, and the
   `tests/test_centrality_cache.py` / `test_snapshot_db.py` siblings that
   read the same module.
5. Tie-order and `stats`-seam prune tests from S1 still pass after S2 — the
   two that would catch a semantically-wrong-but-output-equal refactor.
6. `make qa` legs green: `lint`, `md-lint`, `types`, `static_checks`, `pytest`.
   The known-flaky `test_bold_line_regex_scales_subquadratically` is owned by
   `bold_line_ratio_floor.md` and must not be attributed to this arc.
7. Shakedown: three consecutive `make parity near_duplicate_notes` runs, plus
   one live `make graph-rebuild` + a near-duplicates API call, to confirm the
   GEMM path and the `app.py` memo still agree at production scale (1,186
   paths today).

| Projected outcome | Today | After |
|---|---|---|
| masked functions in `query.py` | 3 (1591, 3650, 4012) | 2 (the two unruled/out-of-scope `_cli` masks) |
| open masks vs the 2026-09-30 ruling | 4 | 3 (batch 1 carries the other three) |
| `make parity` fixtures | 6 | 7 |
| near-duplicate paths under test | 4 semantic | 4 semantic + tie order (parity), prune (stats seam), multi-section, zero-norm, key invariant |
| `near_duplicate_notes` size | 129 lines / mccabe 12 | ~20 lines / mccabe ≤ 8 + 3 helpers |

## 5. Risks

- **A split that is output-equal but semantically wrong.** The three exactness
  claims are the hazard: the `1 − d²/2` identity, the `(dist, path_a,
  path_b)` triple-key ordering, and the prune-is-exact argument. Mitigation:
  the S1 fixture's **tie case** (two pairs at identical similarity) is
  specifically constructed so a tie-break regression cannot pass; S2 is
  forbidden from starting until S1 is green.
- **The `app.py:402` key desyncs from a changed signature.** A renamed or
  reordered parameter with an un-updated key serves stale results with no
  error. Mitigation: S1 asserts the key covers every result-affecting
  parameter; S2 keeps the signature frozen, and any signature change must
  touch `app.py` in the same change.
- **The `stats` dict is mutated as a side channel** (`peak`, `prunes`,
  `post_prune_max`) and callers may read it. Extraction must pass the same
  dict through, not copy it. Mitigation (aligned to §5a — the earlier
  "stats= in the parity render" wording was the corrected draft's
  leftover): the existing `stats`-seam test
  (`test_bounded_accumulator_is_exact_under_pruning`) asserts the caller's
  dict is mutated in place and held through the split.
- **Numpy accumulation order is part of the contract.** `np.add.at` over
  first-seen path order is what makes the mean bit-reproducible; reordering
  `rows` or the dict insertion would change low-order bits and could flip a
  near-threshold pair. Mitigation: the fixture's vectors are chosen so sums
  are order-sensitive under float addition; parity compares full precision,
  not rounded sims.
- **Cross-arc input-surface drift** from P2.2's (ii)/(iii) successors moving
  `_materialise_note_embeddings`. Mitigation: recorded in §2; re-check the
  fixture builder if those land first.

## 5a. Correction to an earlier draft of this proposal

An earlier draft claimed the parity fixture would close the "`4 × limit`
prune exactness" gap. **That was wrong**, and the function's docstring says
why: the final sort+truncate makes the output invariant to buffer size, so an
output-equality test passes whether pruning runs or not. The prune is
therefore witnessed by the `stats` seam in a test, not by parity; parity
witnesses tie order and exact output bytes. §2's gap row, §3's S1, and
acceptance step 5 are corrected accordingly. The two items the same scan
surfaced — the unrecorded 2026-09-26 GEMM rework, and the unwritten
interaction between the 0.9 `min_sim` floor / 10,000-path ceiling / 4× prune
with no perf leg covering any of it — are filed as a separate proposal rather
than folded in here, which would make a refactor arc carry a measurement arc.

## 6. Non-goals

- `csr.try_shortest_path`, `extract_relations._process_pattern_matches`,
  `review_selection.main` — batch 1 S1–S3 (`c901_d1_split_batch1.md`);
  this proposal is independent of it.
- The two pre-existing `_cli` masks at `query.py:1591` and `query.py:4012` —
  unruled / booked to a later `c901_complexity_debt` pass.
- The AVAIL-1 memo in `app.py` and the `_NEAR_DUP_MAX_PATHS` ceiling pre-check
  — this arc changes neither; it only pins their contract in a test.
- Any P2.2 successor work: the (ii) statement-count fold, (iii) table-level
  dirty tracking, and (C) calling `rebuild()` from the ingest lanes. All three
  remain held in `../graph/graph_pending.md`.
- No ruff/mccabe configuration change; no behaviour change anywhere.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-01 | `git status --short -- helpers/ Mojo/ tests/` (main) | empty | P2.2 arc is doc-only; the fold-collision premise is dead |
| 2026-10-01 | `diff <(sed -n 3650,3800p main/helpers/graph/query.py) <(same, worktree)` | identical | the split lands once |
| 2026-10-01 | AST scan of `near_duplicate_notes` | 129 lines (3650-3778), 18 branch-ish nodes, mccabe 12 | mask is live |
| 2026-10-01 | `parity_harness.py --list` | 6 fixtures, none for this function | §4.3 gate unmet |
| 2026-10-01 | read `app.py:400-428` | `key = (gen, doc_type, min_sim, limit)`, `gen` = `_build_meta.built_at` | memo is in the API layer, not the function — the triage claim was wrong |
| 2026-10-01 | read `query.py:3706`, `1556-1558` | reads `file_path, title, emb FROM v_note_embeddings WHERE doc_type = ?` | fixture needs only an injected `con`; no live-DB injection point required (unlike `print_stats`, D1.s7) |
| 2026-10-01 | read `tests/test_note_embeddings.py:367-392` | 4 semantic tests; no tie-order, prune, multi-section, or zero-norm case | the four gaps S1 closes |
| 2026-10-01 | read `query.py:3688-3695` (docstring) | the 4x bound is **"unobservable from the return value"** — the final sort+truncate makes the result invariant to buffer size, "so an output-equality test passes whether pruning runs or not"; the `stats` seam exists precisely to assert it | **correction**: parity CANNOT witness the prune; an earlier draft of this proposal claimed it could. The prune belongs to the `stats`-seam tests, not the parity surface. |
| 2026-10-01 | read `query.py:3684-3686` | `min_sim` is API-floored at 0.9, "so the wide case is latent, not live" | the 4x prune never fires on the API path; guard interaction is unowned — filed as a separate proposal |

## Execution Results

### S1 + S2 executed 2026-10-01 (same session; guard S1 landed before the split)

- **Evidence correction found at execution:** the stats-seam prune was
  already covered — `tests/test_note_embeddings.py::
  test_bounded_accumulator_is_exact_under_pruning` (csr_lane_remediation
  S4) asserts `prunes > 0`, `peak <= 4·limit`, `post_prune_max <= limit`,
  and pruned == reference across limits 1/2/3/7, with documented mutation
  teeth. The §2 appendix had read only `:367-392` and missed `:399-453`.
  No new prune test was needed; that test held through the split.
- **S1:** `helpers/graph/fixture_note_embeddings.py` (tie lattice: two
  bit-identical 1.0 pairs, four at 0.96, four more at 0.9899…, with the
  (c, d) pair inserted index-reversed so the orientation swap is
  load-bearing; multi-section path; zero vector; both doc types) +
  `parity_harness` REGISTRY entry + four gap tests
  (`TestNearDuplicateInvariants`): tie order/orientation, zero-norm guard
  (verified RED with the guard line stripped, then green on restore),
  multi-section mean (0.5, first-seen title), and the `app.py:402`
  key-covers-signature invariant (AST pin: key ⊇ {gen, doc_type,
  min_sim, limit}; the call passes exactly those three, no `stats`).
  Parity at `REF=HEAD` pre-split: 88 rows identical, seeds {0, 1, 7}.
- **S2:** `_collapsed_note_matrix` / `_pair_mask` / `_scan_top_pairs`
  extracted, comments carried verbatim; entry keeps the clamp, the empty
  and `limit == 0` guards in the original order, and the projection.
  `noqa: C901` off. Parity pre-split-HEAD vs post-split tree: 88 rows
  byte-identical, seeds {0, 1, 7}, three consecutive runs. C901 census
  clean (no helper near threshold); the two unruled `_cli` masks remain
  (shifted to `:1594`/`:4055` by the insertions).
- **Suites:** test_note_embeddings 30 passed (incl. record-S2 pins,
  below), test_api_graph_unit + test_centrality_cache + test_snapshot_db
  → 156 passed.
- **Shakedown (§4.7):** live `make graph-rebuild` (data-only, uncontended
  per `witr`) + a `near-duplicates` CLI call (sensible rename-candidate
  pairs: Bank_of_India ↔ Indian_Bank, Waaree Energies ↔ Waaree
  Renewable) + a post-rebuild stats sweep reproducing prunes=188,
  peak=400, post_prune_max=100 — the split path agrees with the memo at
  production scale (P=1,186). The Flask API surface itself is covered by
  test_api_graph_unit.
- **§5a alignment:** the stale §5 "stats= in the parity render"
  mitigation bullet is corrected to the §5a position (above).
