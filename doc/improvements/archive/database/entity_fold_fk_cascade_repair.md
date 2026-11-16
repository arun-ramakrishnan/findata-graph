---
title: "Entity fold must re-point every child before deleting — FK-cascade data-loss repair in name_dedup.apply_plan"
status: executed
filed: "2026-11-16"
executed: "2026-11-16"
completed_md: "526"
area: "helpers/graph/name_dedup.py + tests/test_name_dedup.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

**Date:** 2026-11-16 · **Status:** EXECUTED (live fold applied 2026-11-16) ·
**Area:** `helpers/graph/name_dedup.py` (`apply_plan`, `_refold_entity_refs`,
`_REFOLD_COLUMNS`, `entity_fk_children`) + `tests/test_name_dedup.py`

## 1. Motivation

`name_dedup.apply_plan()` merges duplicate entities by re-pointing relations
from a variant spelling onto a canonical one, then `DELETE FROM entities WHERE
name = ?`. Before this arc it re-pointed exactly **two** surfaces —
`graph_edges` and `hyper_incidences` — and then deleted the entity row.

`memory/research.db` declares **13** foreign keys onto `entities(name)`, of
which 11 are `ON DELETE CASCADE` and 2 are `ON DELETE SET NULL`. A third
group of four derived-cohort tables stores the entity name as a **bare string
with no foreign key at all**, so those rows are neither cascaded nor
re-pointed — they simply survive pointing at a row that no longer exists.
`DELETE FROM entities` therefore silently destroys or orphans every child row
the fold did not touch.

This was not theoretical. The trigger was a routine housekeeping pass over 14
duplicate `normalized_name` groups (29 rows, all `file_path IS NULL`, all minted
by the RPT/VIGIL ingestion as legal-suffix spelling variants: `B.V` vs `BV`,
`L.L.C` vs `LLC`, `A/s` vs `As`, `No.1` vs `No1`). The audit that preceded the
fold measured what the then-current `apply_plan` would have done to those 29
rows alone:

| Child surface | Rows on the 29 variants | Pre-existing `apply_plan` behaviour | Outcome |
|---|---|---|---|
| `events.entity` | 6 | not re-pointed, CASCADE | **deleted** |
| `event_participants.participant` | 10 | not re-pointed, CASCADE | **deleted** |
| `speaker_identity.person_name` | 7 | not re-pointed, SET NULL | **nulled** |
| `hyper_incidences.entity_name` | 90 | re-pointed (ledger sweep only) | partially lost — see D2 |
| `hidden_connections` (a, b) | 190 | no FK, not re-pointed | **dangling strings** |
| `ultimate_parent.company` | 34 | no FK, not re-pointed | **dangling strings** |
| `ppr_results.entity` | 2 | no FK, not re-pointed | **dangling strings** |
| `entity_tags`, `entity_identifiers`, `company_metrics`, `quotes`, `graph_analytics`, `graph_analytics_contract`, `co_mention_editions` | 246+ | not re-pointed, CASCADE | **deleted** |

16 authored `events` / `event_participants` rows and 7 `speaker_identity`
links would have vanished with no error, no report line, and no gate failure —
`apply_plan` reported `zero_loss_ok: true` throughout, because that flag only
accounts for `graph_edges` row arithmetic.

The stakes are asymmetric: the fold is the sanctioned repair for a *cosmetic*
problem (two spellings of one counterparty), but it runs `DELETE` on the
central identity table of a 27,826-entity store whose other children carry
authored events, quotes, speaker links and analytics. A dedupe tool that can
lose authored rows is worse than the duplicates it fixes.

## 2. Evidence (measured 2026-11-16, this box)

### 2.1 The FK surface, enumerated from the live schema

`PRAGMA foreign_key_list` over all 47 tables of `memory/research.db`:

```text
entity_tags.entity_name              -> entities.name  ON DELETE CASCADE
graph_edges.source                   -> entities.name  ON DELETE CASCADE
graph_edges.target                   -> entities.name  ON DELETE CASCADE
graph_analytics.entity_name          -> entities.name  ON DELETE CASCADE
events.entity                        -> entities.name  ON DELETE CASCADE
quotes.entity                        -> entities.name  ON DELETE CASCADE
company_metrics.entity               -> entities.name  ON DELETE CASCADE
hyper_incidences.entity_name         -> entities.name  ON DELETE CASCADE
entity_identifiers.entity_name       -> entities.name  ON DELETE CASCADE
graph_analytics_contract.entity_name -> entities.name  ON DELETE CASCADE
event_participants.participant       -> entities.name  ON DELETE CASCADE
ppr_results.entity                   -> entities.name  ON DELETE CASCADE
co_mention_editions.entity_name      -> entities.name  ON DELETE CASCADE
events.counterparty_entity           -> entities.name  ON DELETE SET NULL
speaker_identity.person_name         -> entities.name  ON DELETE SET NULL
```

Plus the **no-FK bare-name cohort tables**, which `PRAGMA foreign_key_list`
cannot see and which a cascade audit therefore misses entirely:

```text
hidden_connections.a / .b            (no FK; UNIQUE(a, b))
ultimate_parent.company              (no FK; UNIQUE(company))
ultimate_parent.ultimate_parent      (no FK)
ppr_results.entity                   (has an FK AND appears here via the
                                      cohort lane; UNIQUE(seed, entity))
```

### 2.2 The UNIQUE constraints a re-point can violate

A re-point is an `UPDATE ... SET <col> = canonical`, which must satisfy every
UNIQUE index on the destination. Measured from `PRAGMA index_list`:

| Table | UNIQUE key |
|---|---|
| `entity_tags` | `(entity_name, tag)` |
| `entity_identifiers` | `(entity_name, identifier_type, identifier_value)` **and** `(identifier_type, identifier_value)` |
| `graph_analytics` | `(metric, entity_name)` |
| `graph_analytics_contract` | `(metric, entity_name)` |
| `quotes` | `(entity, quote_text, as_of_edition)` |
| `event_participants` | `(event_id, participant, role)` |
| `co_mention_editions` | `(edition_title, entity_name)` |
| `speaker_identity` | `(alias_name)` |
| `ppr_results` | `(seed, entity)` |
| `hidden_connections` | `(a, b)` |
| `ultimate_parent` | `(company)` |
| `hyper_incidences` | `(edge_id, entity_name)` |

No UNIQUE on `company_metrics` or `events`.

### 2.3 The five defects, in the order the rehearsal exposed them

Each was found by running the real plan against a byte-copy of the live store
(`research_pre_fold14.db`, `src.backup()` of `memory/research.db`, 363,569,152
bytes) — never against the live file.

**D1 — Child FKs never re-pointed.** The first rehearsal reported
`entities_folded: 0`, `entities_retained_referenced: 12`: every entity delete
raised and was swallowed by the `except sqlite3.IntegrityError` branch, which
was written for the *referenced-by-another-estate* case, not for a unique
collision. Zero entities folded, and the run looked like a benign no-op.

**D2 — `hyper_incidences` re-pointed only by the ledger sweep.** The main fold
loop re-pointed `graph_edges` only; memberships were handled by the separate
*ledger sweep* pass, which iterates `_name_fold_ledger.jsonl` and skips any
variant already in the current plan (`if variant and variant not in folded`).
Freshly planned variants therefore never had their memberships re-pointed, and
the entity delete cascaded them away. Measured: `hyper_incidences`
59,816 → 59,786 in the second rehearsal — **30 rows**, before the fix.

**D3 — `UNIQUE` collision rolls back the whole transaction.** SQLite enforces
UNIQUE per statement. The original order was *move then dedupe*: `UPDATE ...
SET entity_name = canonical` raises the moment the canonical already holds the
same tag / metric / identifier, and because the caller runs inside a single
`with con:` block, that raise rolls back the re-points already committed for
**every other table in the loop**. First real failure message:

```text
sqlite3.IntegrityError: UNIQUE constraint failed: hidden_connections.a, hidden_connections.b
```

`.a` had been re-pointed, and the matching `.b` update then collided — the
whole fold reversed silently. Fix: **collide-before-move** — drop the
variant's colliding rows while the two spellings are still distinguishable,
then move the rest.

**D4 — A UNIQUE whose key column *is* the re-pointed column.** For
`ultimate_parent(company)` and `hidden_connections(a, b)`, the unique key and
the moving column overlap. The "drop rows matching the other key columns"
formulation degenerates to an empty `others` list and matches nothing, so the
`UPDATE` still collides. Needed a second branch: when no *other* columns
remain, drop the variant row when the canonical row simply exists.

**D5 — The recorded UNIQUE key was wrong.** The table was first written as
`hyper_incidences (edge_id, entity_name, role)` — `role` is NULL on all
15,113 `subsidiary` incidences and is not part of the constraint. De-duping on
the wrong key would have left real duplicates in place (and, because the
repair counts reported `refs_collapsed`, hidden them inside an inflated
number). Corrected to `(edge_id, entity_name)` against the live
`sqlite_autoindex_hyper_incidences_1`.

**D6 — The ledger recorded intent, not outcome.** `apply_plan` ended with
`_append_ledger(folded, ledger)` where `folded` is the **plan's** variant map.
A variant whose entity survived the fold — a UNIQUE collision caught by
`except sqlite3.IntegrityError`, a person exclusion, a row already gone —
was still written to the append-only ledger. The ledger is the durable half of
the fold contract: `entity_identity.fold_registry` routes a recorded spelling
to its canonical *forever*, so a false row means the write path stops minting
the variant while the variant **still exists in the table**. The two views
permanently disagree.

**D7 — A rehearsal could write the repo-tracked ledger.** The ledgers live at
`findata/Misc/_name_fold_ledger.jsonl` — inside the tracked tree. `apply_plan`
against a byte-copy (which is exactly what the FK repair above requires, and
what any pre-flight rehearsal does) appends to that same path. Because the
append is **idempotent**, this is worse than a stray write: the rehearsal
records 12 folds, `git` absorbs them, and when the real run finally executes it
appends **0** rows — the audit trail already claims the work is done, and
nothing in the output distinguishes "nothing to do" from "already recorded".

This was observed live on 2026-11-16: a rehearsal against
`/mnt/data/tmp/opencode/research_pre_fold14.db` wrote 12 rows to
`findata/Misc/_name_merge_plan_rpt1416_fold_ledger.jsonl`, which was then swept
into patch `graph_dedupe`, while all 12 variant entities still existed in the
untouched live store (verified: `entities` 27,826, `Service Centre Maastricht
B.V` present).

**D8 — `hyper_edges.label` is a bare string, so the fold orphaned three
subsidiary hyperedges.** Found during the LIVE run (the rehearsal against a
byte-copy could not show it, because the byte-copy was discarded before the
derived family was inspected). `hyper_incidences.entity_name` has an
`ON DELETE CASCADE` FK and is in `_REFOLD_COLUMNS`; `hyper_edges.label` has
**no FK at all** — it is the parent's name stored as text. Folding
`Layde Steel S.L` → `Layde Steel SL` re-pointed the memberships but left the
hyperedge labelled with the deleted spelling, so the row survives pointing at
a name that no longer exists in `entities`:

```text
id=35061 label='Layde Steel S.L'          members=3    entity_exists=0
id=35366 label='S A B Profiel B.V'        members=7    entity_exists=0
id=35448 label='Service Centre Maastricht B.V' members=10 entity_exists=0
```

The same class as D1 (a no-FK bare-name surface invisible to a cascade
audit), but one level up: `hyper_edges` is not a child of `entities`, it is a
*container* whose label happens to be an entity name. Repaired by re-deriving
the family rather than editing labels, because `subsidiary` is fully derived
(1464/1464 rows carry `source_ref`, 0 have `agent_id`, `role` is NULL on all
15,097 incidences) and `derive_hyperedges` is delete-then-insert on `--apply`.
Result: S0 prune dropped 1464 derived edges, re-inserted 1461 edges /
15,082 incidences, orphaned labels **3 → 0**, subsidiary label collisions
**11 → 9**.

**D9 — `absent` was being reported as `retained`.** In the live person run,
3 variants that had been folded on 2026-10-16 (and no longer existed) were
counted under `entities_retained_referenced` and listed in
`entities_retained_names` — reading as "the fold is stuck" when the work was
already done. `apply_plan` now distinguishes the two outcomes: a variant with
no entity row at all goes to `entities_already_absent`, while a variant whose
`DELETE` raised goes to `entities_retained_names`. Only genuinely-folded
variants reach the ledger either way.

### 2.4 Post-fix rehearsal, fully reconciled

Plan: 11 company clusters, 12 variants, generated as
`findata/Misc/_name_merge_plan_rpt1416.{txt,jsonl}` (the sanctioned plan-artifact
shape). Run against the byte-copy:

| Metric | Before | After | Delta | Reconciliation |
|---|---|---|---|---|
| `graph_edges` | 87,117 | 87,082 | −35 | exact-duplicate relation rows collapsed onto the canonical (`duplicate_rows_removed`) |
| `entities` | 27,826 | 27,814 | −12 | 12 variants folded; every one was fileless |
| `hyper_incidences` | 59,816 | 59,800 | −16 | **16 verified genuine double-memberships** — each variant shared hyperedge(s) with its canonical; not loss |
| `graph_analytics` | 204,256 | 204,132 | −124 | **all 124 collapsible**: for every variant, the canonical already held the identical `(metric, …)`; 0 orphans, 0 dangling refs after |
| `events` | 1,572 | 1,572 | 0 | 3 re-pointed |
| `event_participants` | 2,705 | 2,705 | 0 | 6 re-pointed |
| `speaker_identity` | 1,419 | 1,419 | 0 | untouched (the 3 person folds are a separate pass) |
| `entity_tags` | 7,417 | 7,417 | 0 | untouched |
| `quotes` | 8,848 | 8,848 | 0 | untouched |

`apply_plan` returned `zero_loss_ok: true` with a now-meaningful child report:

```json
"refs_repointed": {
  "hyper_incidences.entity_name": 14,
  "hidden_connections.a": 15,
  "hidden_connections.b": 9,
  "events.entity": 3,
  "event_participants.participant": 6,
  "events.counterparty_entity": 3
},
"refs_collapsed": 188,
"entities_folded": 12,
"entities_retained_referenced": 0
```

Residual-string audit after the fold (the D1-bare-name check that no cascade
would ever catch): `hidden_connections.a` 97 → **0**, `.b` 93 → **0**,
`ultimate_parent.company` 23 → **0**, `ppr_results.entity` 2 → **0**.

## 3. Design

`apply_plan` gains one declarative table and one helper; the deletion order
becomes "clear collisions → move every child → delete the row".

### S1 — `_REFOLD_COLUMNS` (landed)

A module-level tuple of `(table, column, unique_key_or_None)` naming every
surface that must be re-pointed before a variant entity may be deleted:
the 11 cascade/set-null FK children, plus the four no-FK bare-name tables.
Each entry carries the UNIQUE key the move can violate, or `None`.

The stated invariant, in the docstring: the fold loop already owns
`graph_edges` (relation payload + self-loop/unique consolidation) and
`hyper_incidences`; **every other** FK onto `entities(name)` is cascade or
set-null, so any child missing from this table is silent data loss.

### S2 — `_refold_entity_refs` + `entity_fk_children` (landed)

- `_refold_entity_refs(con, variant, canonical, present)` walks
  `_REFOLD_COLUMNS`, skipping tables absent from the database (so the toy
  fixtures exercise the same code path). Per entry it **deletes the variant's
  colliding rows first** (scoped to this exact canonical/variant pair, so
  pre-existing duplicates elsewhere are never swept up by a fold), then moves
  the survivors. Returns `({table.column: rows}, rows_collapsed)`.
- `entity_fk_children(con)` enumerates the live `PRAGMA foreign_key_list`
  surface. It exists purely as a **coverage guard**: the new test
  `TestRefoldCoverage::test_every_entity_fk_has_a_refold_entry` fails the gate
  when a future schema migration adds an FK onto `entities(name)` without a
  matching `_REFOLD_COLUMNS` entry, with the message:

  ```text
  FK children of entities(name) missing a _REFOLD_COLUMNS re-point entry
  (apply_plan would cascade-delete them): [('new_table', 'col')]
  ```

  `graph_edges` and `hyper_incidences` are asserted-covered separately
  because they have dedicated loops by design.

### S3 — The same repair in the ledger sweep (landed)

The ledger sweep (which retires variants re-minted by earlier runs with zero
edges) had the identical bug: it re-pointed `hyper_incidences` inline and then
`DELETE FROM entities`. It now calls `_refold_entity_refs` too, so both
deletion sites share one code path. `hyper_incidences` moving into
`_REFOLD_COLUMNS` let the sweep's bespoke UPDATE/DELETE pair be deleted
outright.

### S4 — Ledger honesty (landed)

- `_db_path(con)` (helpers/graph/name_dedup.py) reads the on-disk file behind a
  connection from `PRAGMA database_list`; None for `:memory:`.
- `_ledger_write_allowed(con, ledger_path) -> tuple[bool, str]` refuses to
  extend a **repo-tracked** ledger unless the database behind `con` *is*
  `memory/research.db`. A ledger outside the repo (a rehearsal's own) is always
  writable. Rehearsals therefore cannot poison the durable fold map.
- `_append_ledger` now takes `con` and short-circuits with a
  `log.warning("fold ledger NOT extended: %s", why)` when refused.
- `apply_plan` accumulates `folded_now` (only variants whose `DELETE` actually
  returned a rowcount) and `retained_variants`, and passes `folded_now` to the
  ledger. A variant kept alive by a unique collision is now reported in
  `entities_retained_names` instead of being recorded as folded.
- The poisoned artifact from D7 was cleared: all 12 rows in
  `findata/Misc/_name_merge_plan_rpt1416_fold_ledger.jsonl` claimed folds whose
  variants still exist live, so the file was emptied and the real run will
  populate it. Verified no other ledger (`_name_fold_ledger.jsonl`,
  `_person_fold_ledger.jsonl`, `_name_merge_plan_s2_supply_k1_fold_ledger.jsonl`)
  carries an uncommitted diff.

### S5 — Regression tests (landed)

- `test_apply_repoints_every_fk_child_before_deleting` builds the four shapes
  that actually bite (`events` cascade + `counterparty_entity` set-null,
  `event_participants` cascade, `speaker_identity` set-null, `entity_tags`
  unique-collision), attaches authored rows to the **variant**, folds it, and
  asserts (a) row counts unchanged, (b) every row now reads the canonical,
  (c) the colliding tag collapsed rather than raised, (d)
  `refs_repointed["events.entity"] == 1`, (e) the variant row is gone.
- `TestRefoldCoverage::test_every_entity_fk_has_a_refold_entry` is the
  forward-looking gate described in S2.
- `test_ledger_records_only_variants_that_actually_folded` pins D6: a variant
  held alive by a `NO ACTION` child the fold does not know about yields
  `entities_folded == 0`, `entities_retained_names == ["Acme Limited"]`,
  `fold_ledger_appended == 0`, and an untouched ledger file.
- `test_repo_ledger_refused_for_non_live_db` pins D7: a byte-copy connection is
  refused for `nd.LEDGER_PATH` with a message containing `repo-tracked`, a
  ledger outside the repo is allowed, and an in-memory connection is refused
  with `in-memory`.

### Alternatives considered and rejected

| Alternative | Why it lost |
|---|---|
| Keep the two-table re-point and add a "check no other children exist" precondition | The children exist on the very rows the arc needs to fold; the precondition would simply block the work forever. |
| Do the move-then-dedupe the ledger sweep used (re-point, then `DELETE ... GROUP BY` on duplicates) | The `UPDATE` raises *before* the dedupe can run (D3), and the raise rolls back the whole `with con:` block — every other table's re-point vanished with it. |
| Disable `PRAGMA foreign_keys` for the fold and let the cascade be "managed" | Would convert 16 visible deletions into silent orphans; the opposite of the goal. |
| Enumerate children by hand at each call site | Already the pre-existing failure mode: two call sites, two different hand-maintained lists, both wrong (D1, D2). One table + one coverage test is the fix for the *class*. |
| Make the normaliser (`entity_identity.name_key`) collapse letter runs so these 14 groups merge automatically | Measured: collisions jump 2 → 66 clusters / 133 names, i.e. one chore becomes a 129-name curation arc. Filed separately as its own decision, not silently absorbed here. |

## 4. Acceptance criteria & shakedown

1. `python3 -m pytest tests/test_name_dedup.py -q` → 24 passed (20 pre-existing
   + the D1 regression + the coverage guard + the two ledger guards).
2. `python3 -m pytest tests/test_person_dedup.py -q` → 3 passed (the person arc
   shares `apply_plan`).
3. `ruff check helpers/graph/name_dedup.py tests/test_name_dedup.py` → clean;
   `ruff format --check` → unchanged.
4. A rehearsal against a byte-copy of the live store must satisfy, per fold:
   `entities` delta == number of variants; every `hyper_incidences` decrement
   attributable to a verified variant/canonical co-membership; every
   `graph_analytics` decrement attributable to the canonical already holding
   the same metric; zero residual variant strings in the four no-FK tables.
   (All four held at 2026-11-16; numbers in §2.4.)
5. The coverage guard must FAIL when a new FK onto `entities(name)` is added —
   verified by construction: it flagged `graph_edges`/`hyper_incidences` as
   uncovered before those were explicitly exempted.
6. LIVE acceptance (2026-11-16): after `--apply --yes` the deltas must equal
   the rehearsal's; `PRAGMA integrity_check` ok; `PRAGMA foreign_key_check`
   0 rows; `name_dedup.py --check` rc=0. All met.

| Projected outcome | Today | After |
|---|---|---|
| Authored child rows destroyed by a routine fold | 16 `events`/`event_participants` + 7 `speaker_identity` per 29-row batch | 0 |
| Dangling bare-name strings after a fold | 190 `hidden_connections` + 34 `ultimate_parent` + 2 `ppr_results` | 0 |
| Silent whole-transaction rollback on a unique collision | yes (D3/D4) | no |
| Future FK child silently omitted from the fold | undetected | gate failure |
| Ledger rows asserting a fold that did not happen | every retained variant (D6) | 0 |
| A rehearsal able to write the tracked ledger | yes (D7) | refused |
| Subsidiary hyperedges naming a deleted entity after a fold | 3 (D8) | 0 (re-derive) |
| Already-folded variants reported as stuck | 3 (D9) | 0 |

## 5. Risks

- **The collide-before-move delete is destructive by design.** It removes the
  variant's row when the canonical already holds the same UNIQUE key. This is
  correct (the fact survives through the canonical row) but it is a *delete*,
  so the scoping matters: the predicate is restricted to
  `entity = variant` AND a canonical row matching on the *other* key columns.
  A bug here would delete canonical facts, not just variant ones. Mitigation:
  the regression test asserts the surviving row count and the canonical's
  contents, and every rehearsal run is against a byte-copy with a pre-fold
  `.zst` recovery point (`db-backup/research_backup.db.zst`, 102,562,431
  bytes).
- **`_REFOLD_COLUMNS` can drift from the schema.** Mitigated by the
  `TestRefoldCoverage` gate (S2), which reads the live `PRAGMA` rather than a
  copy of the schema.
- **The four no-FK tables are invisible to cascade audits.** Any *other* tool
  that deletes entities (`database_integrity_check` repairs, manual SQL) has
  the same exposure and no equivalent guard. Recorded as a non-goal below —
  this proposal fixes the fold path only.
- **Live application — DONE 2026-11-16.** The live fold ran against
  `memory/research.db` behind a verified pre-mutation recovery point
  (`/mnt/data/tmp/opencode/research_pre_live_fold.db.zst`, 102,385,882 B,
  `PRAGMA integrity_check` ok, counts matched live before the run). Result
  matched the rehearsal exactly: `entities_folded: 12`,
  `fold_ledger_appended: 12`, `entities 27,826 → 27,814`,
  `graph_edges 87,117 → 87,082`, `hyper_incidences 59,816 → 59,800`,
  `graph_analytics 204,256 → 204,132`, `zero_loss_ok: true`.
  Post-apply: `PRAGMA integrity_check` ok, `PRAGMA foreign_key_check`
  0 rows, `dup-census 0 <= baseline 0`, fileless duplicate groups
  14 → 3 (the three remaining are person pairs, see below).
- **The person arc was already done.** `person_dedup.py --apply --yes`
  folded **0** entities and reported the 3 HUF variants as
  `entities_already_absent` — all three were folded on 2026-10-16 and are
  present in `findata/Misc/_person_fold_ledger.jsonl`. The live
  `person-census` gate still reports `4 > baseline 0`, but those 4 clusters
  are a *different* set (HUF-vs-person and `N.A. Estate` spelling pairs) and
  are pre-existing, not a regression from this arc.
- **Residual D8 (found by the live run, not the rehearsal).** The fold
  re-points `hyper_incidences.entity_name` but **not** `hyper_edges.label`,
  which is a bare string with no FK. Three `subsidiary` hyperedges
  (`Layde Steel S.L` id 35061, `S A B Profiel B.V` id 35366,
  `Service Centre Maastricht B.V` id 35448) were left naming deleted
  entities. Repaired by re-deriving the fully-derived family:
  `derive_hyperedges.py --families subsidiary --apply` (S0 prune dropped
  1464 derived edges, re-inserted 1461 edges / 15,082 incidences) —
  orphaned labels 3 → 0. Subsidiary label collisions fell 11 → 9; the
  remaining 9 are the genuinely-distinct-entity pairs that no fold can
  merge (see `name_key_letter_run_collapse.md`).

## 6. Non-goals

- Does **not** change `entity_identity.name_key` or any normaliser. The 4
  residual spelling pairs that fail *both* scope rules (`SAB Profiel BV` vs
  `S A B Profiel BV`, `Designit A S` vs `Designit AS`, `Tata Steel Belgium
  Services N V` vs `NV`, `UNO MINDA EUROPE GMBH` vs `UNOMINDA EUROPE GMBH`)
  cannot be entity-folded under the ledger invariant and are hyperedge-label
  collisions only — a separate decision.
- Does **not** repair the 9 residual `subsidiary` hyperedge label collisions.
  Re-deriving the family (which this arc DID run, closing D8) does not merge
  them: they are 9 pairs of genuinely distinct entities that share a
  dot/space-stripped label, and only a normaliser change can bring them
  together — see `name_key_letter_run_collapse.md`.
- Does **not** add a general `hyper_edges.label` re-point to the fold. D8 was
  repaired by re-deriving a fully-derived family; a family with AUTHORED
  hyperedges (event, supply, group) still has no label guard, and that gap is
  recorded here rather than fixed.
- Does **not** reconcile the 4 pre-existing person-census clusters
  (`N.A. Estate` / `N A Estate`, and three HUF-vs-person pairs). They predate
  this arc and the person plan artifact does not contain them.
- Does **not** add an equivalent child-re-point guard to
  `database_integrity_check` repair paths or any other entity-deleting tool.
- Does **not** touch the retired `memory/embed_store.db`, the 0-byte
  `memory/graph.db`, or the doc census drift — separate housekeeping items.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-16 | `PRAGMA foreign_key_list` over all 47 tables | 13 entity FKs (11 CASCADE, 2 SET NULL) + 4 no-FK bare-name tables | §2.1 |
| 2026-11-16 | `PRAGMA index_list` on the 12 child tables | 12 UNIQUE keys, listed §2.2 | `hyper_incidences` = `(edge_id, entity_name)` |
| 2026-11-16 | rehearsal #1 (pre-fix) | `entities_folded: 0`, `entities_retained: 12` | every delete raised + was swallowed |
| 2026-11-16 | rehearsal #2 | `IntegrityError: UNIQUE constraint failed: hidden_connections.a, hidden_connections.b` | D3: whole-transaction rollback |
| 2026-11-16 | `DELETE` probe on `Service Centre Maastricht B.V` | same IntegrityError | isolated D4 (key column == moving column) |
| 2026-11-16 | rehearsal #3 | `entities_folded: 12`, `hyper_inc 59,786` (−30) | D2 surfaced: memberships lost |
| 2026-11-16 | rehearsal #4 (final) | `entities_folded: 12`, `hyper_inc 59,800` (−16), `analytics 204,132` (−124) | all deltas reconciled, §2.4 |
| 2026-11-16 | variant/canonical co-membership query | 16 shared hyperedges, exactly the −16 | genuine collapses |
| 2026-11-16 | `graph_analytics` orphan check | 124 variant rows, 124 collapsible, **0 orphan** | genuine collapses |
| 2026-11-16 | `pytest tests/test_name_dedup.py -q` | 24 passed | incl. D1 regression + coverage guard + 2 ledger guards |
| 2026-11-16 | `pytest tests/test_person_dedup.py -q` | 3 passed | shared `apply_plan` |
| 2026-11-16 | rehearsal against byte-copy after the D7 guard | `fold_ledger_appended: 0`, tracked ledger unchanged at 12 rows, `log.warning` emitted | guard confirmed on the real rehearsal path |
| 2026-11-16 | live check of the 12 claimed variants | all 12 still present in `memory/research.db` (`entities` 27,826) | proves the 12 ledger rows were false → file emptied |
| 2026-11-16 | `git diff --stat` on the other three fold ledgers | no diff | only the rpt1416 ledger was poisoned |
| 2026-11-16 | pre-mutation recovery point before the live run | `/mnt/data/tmp/opencode/research_pre_live_fold.db.zst` 102,385,882 B; copy `integrity_check` ok; all 7 table counts matched live | live fold went ahead on a verified copy |
| 2026-11-16 | LIVE `name_dedup.py --apply --yes --plan-path findata/Misc/_name_merge_plan_rpt1416.txt` | `entities_folded: 12`, `fold_ledger_appended: 12`, `zero_loss_ok: true`, ledger target `findata/Misc/_name_fold_ledger.jsonl` | matched the rehearsal exactly |
| 2026-11-16 | LIVE post-apply counts | entities 27,814; edges 87,082; hyper_inc 59,800; analytics 204,132; events 1,572; participants 2,705; speakers 1,419; tags 7,417; quotes 8,848; `integrity_check` ok; `foreign_key_check` 0 | every delta as predicted |
| 2026-11-16 | LIVE `person_dedup.py --apply --yes` | `entities_folded: 0`, `entities_already_absent` = the 3 HUF pairs | already folded 2026-10-16 (D9) |
| 2026-11-16 | subsidiary hyperedge orphan scan after the fold | 3 orphaned labels (ids 35061, 35366, 35448) | D8 surfaced only on the live store |
| 2026-11-16 | `derive_hyperedges.py --families subsidiary --apply` | S0 prune dropped 1464; inserted 1461 edges / 15,082 incidences; orphans 3 → 0; collisions 11 → 9 | D8 closed |
| 2026-11-16 | family census after re-derive | subsidiary 1461, event 1123, supply 687, group 671, edition 298, industry 120, sub_sector 112, index 54, sector 42, org_membership 34, country 21, theme 12, jv 6, merger 3, demerger 2; total 4646 edges / 59,785 incidences; integrity ok; 0 FK violations | no other family disturbed |
| 2026-11-16 | `query.py rebuild` | needed `make quack-stop` first (split-brain guard refused file-mode writes while the quack server held `memory/graph.duckdb`); rebuild then reported data-only success | cache refreshed |
| 2026-11-16 | `name_dedup.py --check` | `ok: dup-census 0 <= baseline 0` | gate green |
| 2026-11-16 | `person_dedup.py --check` | `FAIL: person-census 4 > baseline 0` | pre-existing clusters, not a regression (§6) |
| 2026-11-16 | `database_integrity_check.py` | relations/entity_tags/quotes/events/metrics/orphan-companies/hierarchy/DuckDB-cache all `errors=0`; validation rate 100% | integrity green |
