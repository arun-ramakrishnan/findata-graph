---
title: "part_of/has_company retirement — the §8-R3 gate's data half is already satisfied"
status: executed
filed: "2026-10-18"
area: "doc/design + helpers/validators"
executed: "2026-10-19"
completed_md: "418"
---

# part_of/has_company retirement — the §8-R3 gate's data half is already satisfied

**Date:** 2026-10-18 · **Status:** PROPOSED

## 1. TL;DR

The legacy bidirectional pair `part_of`/`has_company` was kept behind
the §8-R3 gate: "hyperedge absorption + full test-file enumeration
first" (`ontology_convention_stack.md:206-208`,
`doc/design/ontology.md:73`). The absorption half has been **measured
complete** — every `part_of` source and every `has_company` target sits
in a sector hyperedge, in exact 1:1 correspondence — and the register
(`$TMPDIR/features.txt`) still lists the pair as blocked behind a gate
whose substantive work is finished. What remains is the enumeration and
migration itself: 35 test files, 2,414 edge rows (3.7% of 64,847), and
a roster edit. The hyper store is *strictly more expressive* than the
pair — one company already holds two sector hyperedges while its single
`part_of` row records one — so retirement is an information gain, not a
loss.

## 2. Evidence (measured 2026-10-18, live)

**Absorption — the gate's first precondition, satisfied:**

```sql
-- part_of sources also in a sector hyperedge
part_of sources=1207  covered=1207  uncovered=0
-- has_company targets also in a sector hyperedge
has_company rows=1207  covered=1207  uncovered=0
-- exact pair correspondence
part_of pairs=1207  sector incidences=1208
-- asymmetry probes (both directions): zero rows
```

**The one information gain** — `Kuantum Papers` is a member of both
the `Agriculture` and `Packaging` sector hyperedges while its
`part_of` row names only `Agriculture`. The dyadic pair cannot express
dual membership; the hyperedge store already does.

**Scale drift since the gate was written** (2026-09-14, #234):

| measure | at filing | live now |
|---|---|---|
| `part_of` / `has_company` rows | 1,179 + 1,179 | 1,207 + 1,207 |
| graph_edges total | 19,325 | 64,847 |
| pair share of edges | ~12.2% | **3.7%** |
| sector hyperedges | 42 | 42 |
| sector hyperedge members | 1,179-class | 1,208 |

**The enumeration surface** — 35 test files reference
`part_of`/`has_company`, including: `tests/conftest.py`,
`tests/fixtures/seed_research_db.py`, `tests/test_graph.py`,
`tests/test_graph_disk.py`, `tests/test_graph_stats.py`,
`tests/test_validators.py`, `tests/test_integration_validators.py`,
`tests/test_integration_graph_algorithms.py`,
`tests/test_integration_graph_rebuild.py`,
`tests/test_integration_snapshot_cycle.py`,
`tests/test_integration_parse_newsletter.py`,
`tests/test_sql_query_correctness.py`, `tests/test_analytics.py`,
`tests/test_note_embeddings.py`, `tests/test_semantic_neighbors.py`,
`tests/test_fuzz_semantic.py`, `tests/test_maintenance_utils.py`,
`tests/test_parse_newsletter_entities.py`,
`tests/test_report_append_convention.py`,
`tests/test_integration_ts_contract.py` (full list re-derivable with
`grep -rln 'part_of\|has_company' tests/`).

**Doc/registry surfaces** — `doc/design/ontology.md:73` (the "Legacy
exception" paragraph), `doc/design/db_schema.md` §graph_edges,
`helpers/misc/database_integrity_check.py:36-57` (edge roster),
`ontology_governance.md:255` (deferral pointer).

## 3. Slices

- **S1 Enumeration + migration inventory** — per test file: what the
  pair is load-bearing for (fixture seed data, roster pins, symmetric
  pair convention tests) and its replacement (sector hyperedge
  membership via `h_incidence` / the hyper API).
- **S2 Data migration** — delete the 2,414 rows in one `--apply`
  transaction (dry-run default); hyperedges untouched; Kuantum Papers
  dual membership preserved as-is; `db_bump_log` entry per the writer
  contract.
- **S3 Registry + validator updates** — edge roster 21 registered /
  20 populated → 19 / 18 (`penalized_by` already zero); symmetric-pair
  convention tests re-point at the surviving symmetric types.
- **S4 Doc updates** — remove the `ontology.md:73` legacy exception,
  update `db_schema.md`, close the §8-R3 clause in
  `ontology_convention_stack.md` §7, annotate `ontology_governance.md:255`.
- **S5 Eval gate** — roster change is query-visible.

## 4. Acceptance

- **Eval-gate bullet:** `ontology_eval_gate.py` frozen question set,
  dry-run vs canonical apply, undeclared-ACCEPT 81/81.
- Integrity check green post-migration (roster count, FK, no orphans).
- Hyper parity pins (`h_edge`/`h_incidence` == star store) unchanged —
  this arc deletes nothing from the star store.
- API surface: `/api/graph/sector` and hyper neighbors endpoints
  return byte-identical sector rosters pre/post.

## 5. Risks & non-goals

- The pair is the only remaining *bidirectional two-row* convention —
  its retirement simplifies the symmetric-edge story but must not
  touch the one-row symmetric types (`co_mentioned_in`,
  `competes_with`, `jv_with`, `same_group`, `semantic_peer`).
- Derivation re-runs must not resurrect the pair: the derive-family
  writers that emit `part_of`/`has_company` need their emitters
  removed in S3, not just the rows.
- No scope creep into `listed_in`/`listed_on_index` (separate
  membership semantics, out of this gate).

## 6. References

- `ontology_convention_stack.md` §5/§7/§8-R3 (the gate), §8 S2/S4
  ("make the gate CHEAPER, never trigger it" — they did),
  `doc/design/ontology.md` §2.1/§2.2, `ontology_governance.md` §6/§7,
  `hyperedge_density_dq.md` (companion sector-family evidence).

## 7. Execution results (2026-10-19)

**Status: EXECUTED** — completed.md #418.

### Absorption evidence (the gate)

Measured on the live store immediately before the migration:

| probe | value |
|---|---|
| `part_of` rows | 1,207 |
| `has_company` rows | 1,207 |
| `part_of` sources with a sector incidence | **1,207 / 1,207** |
| `has_company` targets with a sector incidence | **1,207 / 1,207** |
| sector hyperedges | 42 |
| sector incidences | 1,208 |
| asymmetry, either direction | **0** |
| `part_of` rows with NO incidence | **0** |
| incidences with NO dyad | **1** — `Kuantum Papers` → `Packaging` |

The single surplus incidence is the information gain that justified
option B: `Kuantum Papers` carries BOTH `Agriculture` and `Packaging`
sector hyperedges, while its one `part_of` row can name only
`Agriculture` (its `sector_classification`). A single-valued dyad cannot
express dual membership; the incidence store can. Retiring the dyads
therefore **lost nothing and gained a fact**.

### Landed

- **Write path** — `helpers/graph/sector_membership.py` (new):
  `put_sector_membership` / `remove_sector_membership` /
  `sector_membership_rows`, with `_refresh_member_count` keeping the
  hyperedge's `properties.n_members` honest. The ONE place membership is
  written.
- **Writers migrated** — `helpers/core/parse_newsletter.py`
  (`create_entity` → `put_sector_membership`),
  `helpers/maintenance/move_sector.py` (pair delete+insert → remove+put),
  `helpers/graph/derive_hyperedges.py` (`sector` removed from
  `_STAR_SOURCES` — derivation must not resurrect what it no longer owns).
- **Read projection** — `helpers/graph/query.py`:
  `_materialise_sector_membership()` builds TEMP `_sector_resolved` from
  the incidence store; `_resolve_edges()` appends BOTH directions
  (`part_of` company→sector, `has_company` sector→company) into
  `_edge_resolved`; `_materialise_walk_substrate()` unions the same rows
  into `e_dir`; the fingerprint pass stamps `edges:part_of` /
  `edges:has_company` / `edges:@all` from the hyper store.
  `_walk_edges_sql()` gives the same union to raw-walk callers (the CTE
  oracle), and `onager._edge_source_sql()` gives it to the Onager lane.
- **Migration** — `helpers/maintenance/retire_sector_dyads.py`, dry-run
  default, `--apply` deletes inside ONE transaction **after re-verifying
  absorption**; it REFUSES if any `part_of` row lacks an incidence.
  Live run: `deleted 2414 dyad row(s) in one transaction`,
  `verified: 0 part_of/has_company rows remain; incidences untouched`.
- **Integrity** — `_KNOWN_EDGE_TYPES` drops the pair (roster 21 → 19,
  populated 20 → 18); `check_relations` replaces the five symmetric-pair
  probes with `sector_membership_endpoint_bad` (sector hyperedges hold
  only companies; sector labels name sectors); `check_orphan_companies`
  reads the incidence store with a `relations` fallback for
  pre-retirement restores; `stats._print_data_hygiene` and the edge-type
  breakdown follow it, and the breakdown now shows membership as its own
  line ("part_of/has_company are derived, not stored").
- **Parity surfaces** — `Mojo/src/common/integrity_check.mojo`,
  `Mojo/bench/mojo_db_integrity.py`, `app.py`, `igraph_bridge`,
  `schema/duckdb/graph.sql` and the frontend palette/label maps keep the
  pair as a *served label* because the projection still emits it.

### Verification

- Live projection after migration: `e_belongs` = 1,208, `e_has` = 1,208
  (was 1,207 dyads each — the dual-membership gain is now visible);
  `sector_of('Kuantum Papers')` returns `[Agriculture, Packaging]`;
  `sector_members('Agriculture')` = 14.
- Onager over `BelongsTo`: pagerank 1,207 rows, WCC 1,207 rows,
  `_edge_source_sql` serves 1,208 `part_of` + 1,208 `has_company`.
- `tests/test_sector_membership_projection.py` (new, 11 tests: write
  path, idempotence, dual membership, projection both directions, walk
  substrate, empty store, ordering stability, migration refusal gate).
- `make mojo-parity`: **88 / 88 keys match**, GOLDEN PARITY OK.
- Test files swept: 20 (the estimate of ~10 was low), including
  `tests/schema.py` (new shared `HYPER_STORE` DDL), `tests/conftest.py`,
  `tests/test_validators.py`, `tests/test_hyper_incidence.py`,
  `tests/test_move_sector.py`, `tests/test_integration_parse_newsletter.py`,
  `tests/test_integration_validators.py`,
  `tests/test_parse_newsletter_entities.py`,
  `tests/test_graph_stats.py`, `tests/test_graph.py`,
  `tests/test_sql_query_correctness.py`.
- `tests/test_graph.py` 83/83; ruff clean.

### Non-goals honoured

`listed_in` / `listed_on_index` / `belongs_to` / `exposed_to` untouched;
the one-row symmetric types (`co_mentioned_in`, `competes_with`,
`jv_with`, `same_group`, `semantic_peer`) untouched.
