---
title: "Guard derived bare-name columns — FK-shaped integrity for tables that carry names without a FK"
status: executed
filed: "2026-11-17"
executed: "2026-11-17"
completed_md: "529"
area: "helpers/graph/derive_relational_insights.py + helpers/graph/name_dedup.py + helpers/misc/database_integrity_check.py + helpers/maintenance/snapshot_db.py"
---

# Guard derived bare-name columns — FK-shaped integrity for tables that carry names without a FK

**Date:** 2026-11-17 · **Status:** EXECUTED (S1–S4 landed 2026-11-17) ·
**Area:** `helpers/graph/derive_relational_insights.py`, `helpers/graph/name_dedup.py`,
`helpers/misc/database_integrity_check.py`, `helpers/maintenance/snapshot_db.py`

> **Correction recorded after implementation (2026-11-17).** This proposal's
> §2 headline said "5 tables, 9 columns", but its own census table lists six
> rows carrying an unguarded name column and ten columns in total
> (`ppr_results.seed` 1, `hidden_connections` 2, `ultimate_parent` 2,
> `supply_chokepoints` 1, `competitive_overlap` 3, `hyper_edges.label` 1). The
> implementation uses the measured **6 tables / 10 columns**, and the naive
> registry also surfaced a 7th false positive, `concept_schemes.label` (a SKOS
> scheme title, not a company), which is excluded explicitly.

## 1. Motivation

`entities.name` is the join key for the whole store, and 13 child tables honour
that with a real foreign key (`ON DELETE CASCADE` or `SET NULL`). Five derived
tables do not. They store company names as plain `TEXT` columns — no FK, no
`CHECK`, nothing that makes SQLite reject a dangling reference.

That is fine while the only writer is the deriver (which reads live names).
It stops being fine the moment anything *renames* an entity. The 2026-11-16
name-dedup fold (`doc/improvements/archive/database/entity_fold_fk_cascade_repair.md`)
did exactly that: `helpers/graph/name_dedup.py::_refold_entity_refs` walks a
hardcoded `_REFOLD_COLUMNS` list and rewrites each name column by hand. Because
no FK exists, that hand-rewrite is *silent* — nothing tells you the derived
table is now a mixture of pre-fold and post-fold names, and the deriver's
"does the table exist?" freshness check cannot see it either.

**The cost, measured the same day.** The answerability bank pin
`bank-sql-insight-hidden-count` ("How many hidden-connection pairs are
recorded?") failed the qa leg:

| | value |
|---|---|
| pin (operator-attested) | `41671` |
| sample after a *hand-rewritten* table | `41319` |
| sample after a true re-derivation | `41328` |

`41,319` is the honest count of the *mutated* table. `41,328` is the honest
count of the *derived* table. The hand-rewrite lost 9 pairs relative to a real
rebuild and nobody could tell — the table looked internally consistent because
it was, it was just consistent around a stale derivation. The fix that day was
to re-run `helpers/graph/derive_relational_insights.py --apply` and re-pin to
`41328`; the *class* of bug is untouched.

The same exposure applies to every consumer of these tables:
`helpers/graph/context_pack.py`, `helpers/misc/ontology_eval_gate.py`, the
`/api/graph/*` routes, and `snapshots/parquet` (the published export — a stale
table ships stale answers to readers who never see the live DB).

## 2. Evidence (measured 2026-11-17, this box, `memory/research.db`)

Complete census of tables carrying entity-name columns, from
`PRAGMA foreign_key_list` plus column lists:

| table | name columns | FK to `entities(name)`? | verdict |
|---|---|---|---|
| `graph_analytics` | `entity_name` | ✅ CASCADE | safe |
| `graph_analytics_contract` | `entity_name` | ✅ CASCADE | safe |
| `co_mention_editions` | `entity_name` | ✅ CASCADE | safe |
| `ppr_results` | `entity` | ✅ CASCADE | safe |
| `ppr_results` | `seed` | ❌ — | **unguarded** |
| `hidden_connections` | `a`, `b` | ❌ — | **unguarded** |
| `ultimate_parent` | `company`, `ultimate_parent` | ❌ — | **unguarded** |
| `supply_chokepoints` | `supplier` | ❌ — | **unguarded** |
| `competitive_overlap` | `a`, `b`, `c` | ❌ — | **unguarded** |
| `hyper_edges` | `label` | ❌ — | **unguarded** |

**5 tables, 9 unguarded columns.** (`hyper_edges.label` is the one already
documented as defect **D8** in the FK-cascade proposal — folding a parent name
left three `subsidiary` hyperedges labelled with the deleted spelling; that arc
repaired them by re-deriving the family, but only because the label happened to
be a derived family. For *authored* families `event` / `supply` / `group` a
re-derive would destroy human work, so the same rescue is unavailable.)

Failure modes, in order of how quietly they land:

1. **Silent partial rewrite** (observed 2026-11-16). `_refold_entity_refs`
   rewrites the column on the rows it finds; rows it misses keep the old name.
   No error, no warning, table is now a mixture. The `hidden_connections`
   count drifts by 9 and only a pinned bank question notices.
2. **Stale export.** `snapshots/parquet/sqlite/hidden_connections.parquet`
   precedes the rewrite, so `connect_snapshots()` servers and the
   answerability probe read pre-fold truth while the live DB reads post-fold.
   This is why the 2026-11-17 fix needed *both* a re-derive *and* a
   `snapshot_db.py` re-export — two steps, discoverable only by reading the
   failure.
3. **Dangling membership.** Delete an entity whose name lives only in an
   unguarded column and the referenced company vanishes from the entity table
   while still appearing in derived cohorts and hyperedge labels. Consumers
   that join on the name silently drop the row instead of erroring.

Why the existing gate did not catch the class: `helpers/misc/database_integrity_check.py`
checks *referential* integrity through FKs, so a column with no FK is invisible
to it. Green integrity check, stale table.

### Root cause

Two structural facts, neither of which is a bug on its own:

- **The deriver is not idempotent under rename.** `derive_hidden_connections`
  and friends compute from current names, so a *re-derive* is correct — but
  nothing forces a re-derive after a rename, and nothing invalidates the table.
- **The renamer has a hand-maintained list.** `_REFOLD_COLUMNS` is a literal
  tuple of 19 `(table, column, unique_key)` entries. It is complete today
  (guarded by `tests/test_name_dedup.py::TestRefoldCoverage`, which diffs it
  against live `PRAGMA foreign_key_list`) — but that guard only covers tables
  **with** an FK. The 9 columns above have no FK, so they were added to the
  list by hand and can be forgotten the same way.

So the store has a rename mechanism that is *complete by convention* and a
derivation mechanism that is *fresh by convention*. Both conventions are
honoured by the current code and violated by any future edit.

## 3. Design

### S1 — Inventory the unguarded columns, machine-checkably (first)

Replace the hardcoded knowledge with a computed one. Add
`derived_name_columns(con) -> set[tuple[str, str]]` to
`helpers/graph/name_dedup.py`, enumerating every table/column that:

- has a column whose name is in a fixed registry of *name-shaped* column names
  (`a`, `b`, `c`, `company`, `ultimate_parent`, `supplier`, `label`, `seed` —
  declared once as a constant, since SQLite offers no type-level signal), **and**
- has no FK covering that column to `entities(name)`.

Then assert the result equals the explicit `_DERIVED_NAME_COLUMNS` tuple in the
same module — a two-way guard, so a new unguarded table fails the test rather
than silently escaping. This is the exact shape of `entity_fk_children` +
`TestRefoldCoverage` from the FK arc, extended to the FK-less half.

**Why not add real FKs?** SQLite cannot add a FK to an existing table without a
table rebuild, and these are *derived* tables rewritten wholesale on every
`derive-relational-insights --apply` — a rebuild would need four
create/copy/drop cycles plus a migration story for every deployment. It also
would not fix case 3, because `CASCADE` on a delete is precisely what we do
*not* want for a derived cohort (the cohort should be recomputed, not pruned
row-by-row). FKs are the right tool for authored rows; for derived tables the
right invariant is "this table is fresh or it is marked stale".

### S2 — Freshness marker on derived tables

**Landed as:** one table, `derived_cohort_meta(table_name, mode,
entities_fingerprint, stamped_at)`, declared in `_DDL` of
`helpers/graph/derive_relational_insights.py` — not a per-table pair. A single
row per cohort records `mode` (`derived` | `repointed`), a 16-char sha256 of the
sorted entity-name set it was computed against (`entities_fingerprint()`), and
`stamped_at`. `run_lane` stamps `MODE_DERIVED` inside the same `with conn` block
that rewrites the table, so the marker can never describe rows that failed to
land.

Why one table rather than a column pair per cohort: the four cohorts are
rewritten wholesale by `DELETE` + `INSERT ... VALUES (len(rows[0]))`, so an
extra column would change every row tuple and every row-count assertion in the
lane tests, to carry information that is per-table by nature. A side table also
survives `DELETE FROM <cohort>` untouched, which is what makes it usable as a
staleness marker at all.

Read path today: `helpers/misc/database_integrity_check.py::check_derived_names`
reports every cohort whose mode is `repointed` as an advisory WARNING with the
remedy (`make derive-relational-insights`).

**S2b — the reader guard** (landed 2026-11-17, completing the marker's purpose).
The marker existed but nothing READ it, so a `repointed` cohort was still served
as if derived — the same silence that hid the 41,319-vs-41,328 drift. Two
surfaces now consult it, sharing one decision function:

`derive_relational_insights.cohort_staleness(conn, table, *, fingerprint=None)`
returns `(reason, message)` or `None`, over three reasons:

| reason | condition | what it means |
|---|---|---|
| `unstamped` | meta table EXISTS, no row for this table | something wrote the cohort outside a lane |
| `repointed` | `mode != 'derived'` | fold patched names in place; counts may differ from a re-derivation |
| `fingerprint` | stamped fingerprint ≠ current entity set | a rename landed after the compute |

A store with **no** `derived_cohort_meta` table returns `None` — it predates S2,
nothing is knowable, and warning on every cohort would be noise on every restore.
"Cannot answer" is deliberately not "answered no".

`stale_cohorts(conn, tables)` is the batch form both readers use; it hashes the
entity set **at most once** per call and **zero** times when every failing stamp
is `unstamped`/`repointed` (no comparison needed). Measured: healthy store 1
hash, stale store 1 hash, pre-S2 store 0 hashes.

- `helpers/graph/context_pack.py::_cohort_warnings` prepends a
  **`### Data freshness`** block to the deep-relations section, before the
  numbers, so a reader sees the caveat first. The rollup itself is still
  rendered — the warning annotates, never hides.
- `helpers/misc/answerability_probe.py::cohort_freshness` reads the stamp from
  the LIVE store (the snapshot deliberately excludes `derived_cohort_meta`),
  annotates every `COHORT_SHAPES` record with a `cohort_stale` list, adds a
  `cohorts` block plus `cohort_stale` to the report, and prints a
  `--- derived cohorts not verifiable as fresh ---` section. The probe remains a
  report, not a gate — a stale cohort is not added to `--fail-on`.

### S3 — Make the rename invalidate instead of hand-rewrite

**Landed as: re-point + stamp** (operator-ruled, see §3 S3's closing note).
`helpers/graph/name_dedup.py::_stamp_repointed_cohorts` runs inside the fold's
per-variant transaction and stamps every derived cohort the fold patched, using
`DERIVED_COHORT_TABLES` imported from the deriver so the two modules share one
set. It is a no-op when `derived_cohort_meta` does not exist yet (a store whose
deriver never ran) rather than inventing schema mid-transaction.

### S4 — Integrity-check coverage

`helpers/misc/database_integrity_check.py` gains a section that counts rows in
each `_DERIVED_NAME_COLUMNS` column whose value has no matching
`entities.name`. Advisory first (some legitimately name non-entity things — a
`supply_chokepoints.companies` blob is a JSON list, not a name), then ERROR for
the columns that must be entities. Cheap, and it catches failure mode 3.

### Slices

| slice | content | landed as |
|---|---|---|
| **S1** | `derived_name_columns` + two-way registry guard + live test | `derived_name_columns(con)`, `_DERIVED_NAME_COLUMNS`, `TestDerivedNameCoverage` (3 tests). Also added the 5 missing derived columns to `_REFOLD_COLUMNS` with their real PRIMARY KEYs, and 4 new fold tests. |
| **S2** | freshness marker on the derived cohorts | `derived_cohort_meta` + `entities_fingerprint()` + `stamp_cohort()` + `cohort_meta()`; `run_lane` stamps `derived` in the rewrite transaction |
| **S3** | rename marks instead of claiming derivation | `_stamp_repointed_cohorts` stamps `repointed`; option (b) rejected — see §3 |
| **S4** | integrity-check section for dangling derived names | `check_derived_names` registered as ERROR severity, with `repointed` cohorts surfaced as advisory warnings |

S1 was independently landable and is worth keeping even had S2–S4 never landed —
it is pure observability. S3 depends on S2 to have somewhere to record the mode.

The registry also caught two scoping errors that only measurement could find:
`concept_schemes.label` is name-shaped but is a SKOS scheme title (excluded via
`_NON_ENTITY_NAME_COLUMNS`), and `hyper_edges.label` is polymorphic — 9 families
resolve 100% to an entity name (1,450 subsidiary + 688 supply + 112 sub_sector +
54 index + 42 sector + 34 org_membership + 21 country + 12 theme + 2 demerger),
while 6 carry free text by design (event ids `acquisition:12`, group nicknames
`Adani`, merger prose, edition titles, industry/sub_sector taxonomy). Treating
the column as uniformly entity-shaped would have fired on **2,089 legitimate
rows**. Hence `_HYPEREDGE_ENTITY_LABEL_FAMILIES` and a family-scoped query.

## 4. Acceptance criteria & shakedown

1. ✅ `derived_name_columns(con)` returns exactly the **10** `(table, column)`
   pairs (`checked_columns=10` on the live store), and
   `pytest tests/test_name_dedup.py::TestDerivedNameCoverage` fails if a new
   name column appears without a registry entry.
2. ✅ A rename of a name in `hidden_connections` leaves the table stamped
   `repointed` rather than silently claiming derivation;
   `test_fold_stamps_touched_cohorts_as_repointed` asserts it, including that an
   *untouched* cohort is never stamped.
3. ✅ `check_derived_names` reports 0 dangling rows across the 10 columns on the
   live store, and `make database_integrity_check` exits 0.
4. ✅ `integrity_check`, `schema-check`, `static_checks`, `pytest`,
   `make lint-audit` all green.
5. ✅ **The reader guard fires** (S2b): a `repointed` cohort, a stale
   fingerprint, and an unstamped-but-present row each produce a warning in
   `context_pack` and in the probe's report, while a fully fresh store and a
   pre-S2 store produce none. Asserted by `tests/test_context_pack.py::
   TestCohortFreshness` (4 tests), `tests/test_answerability_probe.py::
   TestCohortFreshness` (5), and `tests/test_derive_relational_insights.py::
   TestCohortStaleness` (9, including that the batch form hashes at most once).
6. ✅ **Eval gate RUN** (2026-11-17, after the reader guard landed).
   `ontology_eval_gate.py --parent <pre-fold backup> --candidate memory/research.db`
   → **ACCEPT — 239 questions, 0 reasons**, all 239 `unchanged`, 0 errored.
   The parent is `db-backup/research_backup.db.zst` decompressed (2026-11-17
   14:41, genuinely pre-fold: `hidden_connections` 41,319 = the stale mixed
   count, `ultimate_parent` 26,145, no `derived_cohort_meta`). 9 of the 239
   questions read the cohorts directly (`ultimate_parent_of` ×6,
   `insight_counterparts` ×3) and every one answered identically — so the arc
   moved no ontology-visible answer. The gate is a **strict reject on any
   undeclared change**, so "0 reasons" is a positive result, not a vacuous one.
   One number needed explaining before that claim held: `ultimate_parent`
   counts fell 26,145 → 25,200. The delta is **865 rows whose `company` was
   never an entity + 85 person rows**, i.e. pre-R3 residue that the lanes had
   accumulated and a full re-derivation correctly drops; every one of the
   25,200 kept rows is a live `entity_type='company'`. Verified three ways:
   set arithmetic on the two stores, the company-roster count
   (25,200 in BOTH stores, unchanged), and rows-not-a-company (85 → 0).
7. ✅ **Repeat-count ≥3 done** (2026-11-17): the gate ran 3 times against the
   same parent/candidate pair and all three reports are **byte-identical**
   (`md5 836a4c9c5bff3de5ca6b1f5a7d3ffbcd` for `eval_rep_1/2/3.json`), and
   `entities_fingerprint` returned `d42e140ffa7d535a` on 3 consecutive calls.
   The determinism claim is therefore measured, not assumed.

| Projected outcome | Before | After |
|---|---|---|
| Unguarded entity-name columns | 10, known only by reading this doc | 10, machine-enumerated and test-guarded |
| Rename leaving a silently-mixed derived table | possible | flagged `repointed` in the store itself |
| Stale derived table served to readers | silent | flagged in the pack AND the probe (§S2b) |
| Dangling derived names detected by gate | no | yes (ERROR-level) |
| Time to diagnose a hidden_connections drift | ~1 session (2026-11-17) | one integrity-check line |

## 5. Risks

- **The fold still re-points derived cohorts, so counts can differ from a true
  re-derivation.** This is precisely what `mode='repointed'` records; the remedy
  is `make derive-relational-insights`, surfaced by the gate. Accepted
  explicitly over option (b) — see §3.
- **Fingerprint cost.** `entities_fingerprint` sorts and hashes ~27.8k names per
  call. It is called once per `stamp_cohort` (once per lane run, once per folded
  variant) and, since S2b, **on reader paths** — `context_pack` and the probe
  now call `stale_cohorts`. Mitigated by batching: one hash per request at most,
  and zero when no stamp needs a comparison. Measured 2026-11-17: healthy store
  1 hash, stale store 1 hash, pre-S2 store 0; the whole probe sweep costs 0.108 s
  end to end.
- **The name-shaped column registry is a convention.** A derived table could
  add a column called `node` referencing an entity and escape. Partial by
  construction; the two-way guard makes the *escape visible* (the registry test
  fails when the enumerated set and the declared tuple diverge) rather than
  invisible.
- **`hyper_edges.label`'s family split is a measurement, not a schema rule.** A
  future family added to `HYPEREDGE_TYPES` is neither entity-label nor
  free-text until classified; `test_hyperedge_label_family_split_is_exhaustive`
  makes an unclassified live family a test failure rather than a coin flip.
- **`derived_cohort_meta` is excluded from the parquet export** (like
  `db_bump_log`), because it changes on every derive while the cohort rows may
  not — exporting it would put a tracked parquet under permanent drift. A
  restored copy re-stamps on its first derive; between restore and that run the
  marker is simply absent, which `cohort_meta()` reports as "unknown", not
  "fresh".

## 6. Non-goals

- Not adding real foreign keys to derived tables (§3 explains why: rebuild cost,
  and CASCADE is the wrong semantics for a recomputable cohort).
- Not fixing `hyper_edges.label` for **authored** families (`event`, `supply`,
  `group`) — those need a curated label-repair path, not a re-derive; that is
  D8's documented residual and stays out of scope here.
- Not addressing `snapshots/parquet` refresh orchestration: `snapshot_db.py`
  merely EXCLUDES `derived_cohort_meta` from the export (§5), which is why the
  probe reads the stamp from the live store. A general "the snapshot itself is
  stale" story is a separate concern.
- Not migrating any existing stale data. The live store was re-derived on
  2026-11-17 and the pin moved to `41328`; this proposal prevents recurrence, it
  does not repair history.

## Appendix — raw measurement log (2026-11-17)

| step | command | result |
|---|---|---|
| FK census | `PRAGMA foreign_key_list(<t>)` on all 47 tables | 13 tables FK to `entities(name)` |
| unguarded census | column-name scan over derived tables | 5 tables / 9 columns |
| drift observation | qa run 4268 `test_shipped_bank_has_zero_drift` | `lost: ['41671']`, `added: ['41319']` |
| true re-derivation | `derive_relational_insights.py --apply` | `hidden_connections 41319 → 41328` |
| CASCADE check on ppr | `PRAGMA foreign_key_list('ppr_results')` | `entity → entities.name ON DELETE CASCADE`; `seed` unguarded |
| hyper_edges FK | `PRAGMA foreign_key_list('hyper_edges')` | only `agent_id → provenance_agents`; `label` unguarded |
| name-shaped scan (implementation) | column scan over all 47 tables | 11 candidate pairs / 7 tables; `concept_schemes.label` excluded as a SKOS title → **10 pairs / 6 tables** |
| hyperedge label resolution | `hyper_edges ⨝ entities` per family | 9 families 100% entity-resolved; 6 free-text families total **2,089** rows (`event` 1,123, `group` 670, `edition` 181, `industry` 109, `sub_sector` 0, `merger` 2, `jv` 4) |
| lane idempotence (post-change) | `derive_relational_insights.py --apply` | digests unchanged: uparent `dbfd08d1e8174924`, hidden `e36294d2e0b7ec90`, chokepoints `fc1d5b02f1540faf`, overlap `ab50560dabb7c4f2` |
| stamped cohorts | `SELECT * FROM derived_cohort_meta` | 4 rows, all `mode=derived`, fingerprint `d42e140ffa7d535a` |
| dangling check (live) | `database_integrity_check.py` | `checked_columns=10 -> errors=0`, exit 0 |
| schema after new table | `schema_dump.py --check` | `schema ok: 22 DBs match their tracked DDL` (after `make schema-dump`) |
| snapshot after new table | `snapshot_db.py --check` | 74 tables OK; `derived_cohort_meta` excluded from export like `db_bump_log` |
