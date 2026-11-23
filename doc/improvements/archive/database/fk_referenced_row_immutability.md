---
title: "Referenced-row write immutability under DuckDB 2.x — drop constraining FKs, gate-enforce"
status: executed
filed: '2026-11-22'
executed: '2026-11-23'
completed_md: "563"
area: schema/duckdb/research.sql, helpers/core/db_engine.py, helpers/core/stable_write.py, helpers/misc/database_integrity_check.py, helpers/graph/sector_membership.py, helpers/graph/derive_insights.py, helpers/graph/derive_events.py, helpers/graph/derive_event_participants.py, helpers/core/parse_newsletter.py
---

# Proposal: Referenced-row write immutability under DuckDB 2.x

**Date:** 2026-11-22 · **Status:** EXECUTED 2026-11-23 (unparked by the
operator alongside the quotes arc; see §6 for the execution record and
completed.md #563. The six point-fixes in completed.md #560 stand and
remain correct under either resolution.)

> **Record:** filed 2026-11-22 as a design verdict and parked the same day
> (operator: "file a proposal and mark it deferred for now"); executed
> 2026-11-23. The DuckDB 2.x FK limitation is real, documented upstream,
> and had already produced six per-site fixes in one sitting (#560); this
> proposal recorded the design-level answer — drop the constraining FKs on
> the three hot referenced tables, keep the provenance FK, and move
> enforcement to the integrity gate.

## 1. Motivation — the engine constraint contradicts the write model

DuckDB 2.x refuses **any** UPDATE or DELETE of a row in a table referenced
by a foreign key — documented in the upstream constraints docs, and the
non-key-column case (our `hyper_edges SET properties`,
`entities SET file_path`) is a known upstream false-positive report
(update of an indexed non-PK column still triggers the FK check). There is
no session-level escape hatch (no FK-enforcement toggle). The workaround
upstream names is ours: delete children first, or drop the constraint.

The estate's write model needs mutable referenced rows on three central
tables, so the constraint makes them effectively write-immutable:

- **`entities(name)`** — 11 referencing tables (graph_edges source/target,
  hyper_incidences, quotes, company_metrics, events entity + counterparty,
  event_participants, entity_tags, entity_identifiers, graph_analytics,
  graph_analytics_contract, co_mention_editions). Routine flows mutate
  entity rows: D17 stub upgrades (file_path), move_sector, renames, type
  corrections.
- **`hyper_edges(id)`** — referenced by hyper_incidences; derive restamps
  n_members/source_ref every pass.
- **`events(id)`** — referenced by event_participants; derive_events
  replaces the derived set on every run.

completed.md #560 carries the first six per-site fixes (sector_membership
best-effort restamps, parse_newsletter lock release, derive_insights
conditional re-type, `_edge_writer` reserved-keyword quoting,
stable_prefix_replace children param, derive_event_participants DDL
alignment). They are triage: the next writer that touches these tables
hits the wall again.

## 2. Design recommendation

1. **Drop the constraining FKs whose referenced table has legitimate
   mutable writes** — the `entities(name)`-side constraints,
   `hyper_incidences → hyper_edges(id)`, `event_participants → events(id)`.
   Tracked DDL change in schema/duckdb/research.sql (regenerate via
   make schema-dump; a migration ALTERs or rebuilds the tables — the
   #554 VARIANT mechanics are the template: backup first, dependent
   objects handled, schema-check clean).
2. **Keep `provenance_agents(agent_id)` FKs** — the one case the AGENTS
   law explicitly relies on (insert-time IntegrityError for unregistered
   agents), and it never blocks a legitimate write (append-only registry).
3. **Move enforcement to the integrity gate** — database_integrity_check
   already machine-checks orphan companies, missing notes, and file-path
   resolution; formalize explicit referential-orphan queries for the
   dropped constraints as ERROR-severity registry checks so the gate still
   trips (per arc, instead of per statement).
4. **Reopen trigger**: a future DuckDB pin that fixes the non-key-UPDATE
   false positive lets the FKs return (the guards in #560 are all
   best-effort skips that simply never fire either way — no debt either
   direction).

Trade-off, stated: a bad entity reference currently fails at INSERT time;
after the drop it fails at gate time. The writers resolve names via
lookups before writing (the pending-relations queue exists for unknown
mentions), so the practical exposure is small — but the timing of the
guarantee changes, which is why this is the operator's call and parked.

## 3. Rejected alternatives

- **Generic referenced-row write helper** (delete-children → mutate →
  restore as a library): generalizes the manual dances, but every future
  writer must adopt it, and it composes badly with partial-failure states.
- **Schema redesign** to keep mutable attributes off referenced rows
  (n_members into a view, mutable entity attributes into a sidecar): deep
  churn across readers for no runtime gain.

## 4. First slice of any execution: the transaction-semantics audit

More dangerous than the FK limitation itself, discovered the same day:
**`with conn:` on the helpers.core.db_engine wrapper does not roll back on
exception** (measured: a failed entities UPDATE persisted prior DELETEs in
the same block — Wakefit's index edges were lost and restored only because
a fresh graph-mirror snapshot existed). Before any FK drop lands, audit
the wrapper's context-manager semantics (implicit transaction vs
autocommit) and pin them with tests; silent partial writes are a worse
failure class than a refused write.

## 5. Acceptance criteria (when unparked)

1. FK inventory documented per table (kept vs dropped, with the reason).
2. DDL migration applied with backup + schema-check clean (#554 template).
3. Integrity gate carries the dropped constraints as error-severity checks;
   exit 0 on the clean estate.
4. The #560 guards remain (they no-op without the FKs).
5. Transaction-semantics audit landed FIRST, pinned by tests.
6. Eval-gate: not required — no query-visible semantics change (constraint
   metadata + validator coverage only); note that foreign_key_check
   coverage narrows to the kept FKs.

## 6. Execution & findings (2026-11-23)

All five acceptance criteria met, in §4's prescribed order:

- **Slice 1 — transaction semantics (§4, first)**: `_DuckDBConn`
  (helpers/core/db_engine.py:159) auto-committed every statement —
  DuckDB has no implicit open transaction, so the old `__exit__`'s
  rollback-on-exception had nothing to undo and partial writes persisted
  (the Wakefit mechanism, now pinned to exact lines). New
  depth-counted explicit transactions: `__enter__` issues
  `BEGIN TRANSACTION` at depth 0, `__exit__` COMMITs or ROLLBACKs the
  outermost block (conn stays open — prior regression preserved),
  nested `with` joins the outer txn, `commit()` mid-block pins writes
  (sqlite3 parity), statements outside any block stay autocommit.
  Six tests in `tests/test_db_engine.py::TestWithBlockTransactionSemantics`
  (rollback-on-exception preserves deleted rows; fresh-conn durability;
  nested join; mid-block pinning; reuse-after-abort). The migration
  itself then proved the semantics live: the first `DROP CONSTRAINT`
  attempt raised `NotImplementedException` and the whole scratch txn
  rolled back atomically (24 FKs verified intact after).
- **Slice 2 — FK drop**: `ALTER TABLE ... DROP CONSTRAINT` is NOT
  IMPLEMENTED in DuckDB 2.0.1, so the migration rebuilds the 13
  referencing/referenced tables as shadow tables with the FK clauses
  asserted-absent exactly once, inside one txn through house
  `connect()`: 3 views dropped and recreated
  (company_metrics_ruled, relations, v_quote_speaker), 14 secondary
  indexes recreated, 471,157 rows preserved (row + sequence + index
  counts equal pre/post). 24 → 7 FKs. KEPT 7: five
  `agent_id → provenance_agents` (company_metrics, events, graph_edges,
  hyper_edges, quotes — the AGENTS law) + concepts' two intra-table
  FKs. Backup: full `make snapshot` before; `make schema-dump` after,
  diff = exactly the 17 FK removals. Referenced-row UPDATE verified
  legal on the live store.
- **Slice 3 — gate enforcement**: `check_orphan_spine_refs` in
  helpers/misc/database_integrity_check.py — ERROR-severity registry
  row, engine-agnostic NOT EXISTS probe over the five newly-unguarded
  spine columns (co_mention_editions.entity_name,
  entity_identifiers.entity_name, graph_analytics.entity_name,
  graph_analytics_contract.entity_name, speaker_identity.person_name;
  NULLs skipped, missing tables tolerated), rendered in both report
  writers. The other 10 dropped entity-side columns keep their
  dedicated ERROR backstops (check_events orphaned, check_quotes,
  check_company_metrics, check_entity_tags, check_orphan_incidences,
  check_orphan_graph_edges, check_orphan_event_participants,
  check_orphan_ppr_results). Full gate exit 0 on the clean estate;
  four tests in `tests/test_database_integrity_check.py::TestOrphanSpineRefs`
  (suite 101 passed).
- **Pre-existing red fixed en route** (the "reds from earlier arcs"
  standing rule): `test_derived_rows_replaced_on_rederive` failed on
  sqlite fixtures since the children param landed in the writer wave —
  `stable_prefix_replace` now skips child-table deletes whose table is
  absent (`sqlite_master` probe, the derive_hyperedges gate shape;
  DuckDB serves sqlite_master as a compat view, so both engines pass).
- **#560 guards unchanged**: every FK-law guard added in #560 is
  best-effort and simply never fires without the constraints — no debt
  either direction. `foreign_key_check` in the integrity gate now
  covers only the kept 7, which is precisely the narrowed coverage the
  proposal called for.
