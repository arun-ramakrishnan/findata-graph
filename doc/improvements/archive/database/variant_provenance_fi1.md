---
title: "FI-1 provenance JSON → VARIANT — S0 census NO-GO (no JSON-shaped provenance column exists)"
status: executed
filed: '2026-11-22'
executed: '2026-11-22'
completed_md: "558"
area: helpers/maintenance/rebuild_convo_search.py, helpers/misc/convo_query.py, schema/duckdb/research.sql, memory
---

# Proposal: FI-1 provenance JSON → VARIANT (re-evaluate post-#554)

**Date:** 2026-11-22 · **Status:** EXECUTED (S0 census ran; verdict NO-GO —
no JSON-shaped provenance column exists anywhere in the estate; FI-1
closed in pending.md. Evidence: bench_data/fi1_provenance_census/).
**Area:** provenance-bearing columns across the DuckDB estate
(convo_search.duckdb, the four lane stores, research.duckdb,
graph.duckdb) + the tracked DDL in schema/duckdb/.

**Follows:** doc/improvements/archive/database/duckdb_pure_stack.md:60
(FI-1, deferred "per its own trigger", twice) and
doc/improvements/archive/database/variant_properties.md (completed.md #554,
executed 2026-11-22 — the migration mechanics this proposal reuses).

## 1. Motivation — the premise changed under the deferral

FI-1 was deferred without a re-statement of its trigger in both places
that carry it (duckdb_pure_stack.md:60, graph_hnsw_next.md:84), and
both deferrals date from when N1 (properties → VARIANT) was still ruled
NOT ADOPTED on DuckDB 1.5.4. #554 flipped that premise on 2.0.1:

- json_extract / json_extract_string work on VARIANT (the 1.5.4 broken
  read path was the original blocker);
- VARIANT struct access measured faster than the text path (0.83×) and
  raw scans 0.83×, storage 1.0× — the "TEXT is cheaper and safer"
  footing is gone.

But what "provenance JSON" means on the live estate needs a census,
not an assumption. The one recorded candidate,
convo_search.provenance, is a VARCHAR harness/kind string
(rebuild_convo_search.py:103, migration :173), NOT JSON — filtered
with LIKE prefix scans (convo_query.py:260, :298, :679). If no
provenance-bearing column is actually JSON-shaped with long-tail key
reads, FI-1 should close as a NO-GO census rather than a migration —
that outcome is legitimate (born-archived precedent, skylake style).

## 2. Slices

### S0 — census (the go/no-go gate)

Enumerate provenance-bearing columns across the DuckDB estate and
classify each: relational (provenance_agents), harness/kind VARCHAR,
JSON-TEXT with json_extract read paths, or opaque. For each JSON-TEXT
candidate measure: key cardinality (is the access long-tail?),
read-path call sites (who json_extracts it today), row counts, and
write sites. Record the census table in the execution record.

### S1 — migration (conditional on S0)

Apply the #554 mechanics to the columns that qualify: backup first,
ALTER TYPE VARIANT, dependent objects (views / indexes / FK tables)
dropped and recreated, make schema-dump regeneration, targeted reads
verified against the backup. json_extract read paths need no rewrite
(#554 §2: no struct-dot access exists in production code — verify the
same holds for the census columns).

### S2 — structured provenance (optional, convo lane only, operator-gated)

IF S0 shows a real win, a structured provenance {harness, kind} VARIANT
would replace the LIKE prefix scans with exact struct equality — the
only slice with query-visible semantics. It changes the source-filter
read surface (bili lanes, --harness filtering) and carries the
eval-gate bullet if pursued. Default posture: leave provenance VARCHAR.

## 3. Risks

- Migration churn for no read-side win — the S0 census gate exists
  precisely to prevent this; only columns with real long-tail key
  reads qualify.
- The LIKE-based source filters are load-bearing; S2 must preserve the
  escape/quoting contract or not happen.

## 4. Acceptance

1. Census recorded with a per-column verdict and measurements.
2. Either: qualifying columns migrated (schema-check clean, targeted
   tests green, backup retained, search-fresh APPLY rc=0); or NO-GO
   recorded with the census as evidence and FI-1 closed in pending.md
   with a dated note.

## 5. Out of scope

- provenance_agents (already relational), agent_id FK discipline (the
  helpers.core.db connect() law), bili_* tier columns, and any
  properties-column work (owned by #554).

## 6. S0 execution findings (2026-11-22) — verdict NO-GO

The census ran over all ten DuckDB stores (banked, rerunnable driver:
`bench_data/fi1_provenance_census/census.py` → census.md; read-only):

- **55 provenance-bearing columns enumerated; ZERO JSON-shaped.** Every
  candidate is a plain VARCHAR scalar:
  - convo_search.duckdb: `provenance` = snap-marker strings
    ("dsh-snap:2026-11-21_17-00-02", 37 distinct over 257,751 rows),
    `agent` = harness names (9 distinct), `model` = model ids — all
    read via exact/LIKE filters, never key extraction.
  - research.duckdb + graph.duckdb: `agent_id` (FK → provenance_agents,
    relational), `source_ref`/`source_tier` (scalar vocabularies, 1-1071
    distinct) — provenance here is RELATIONAL, not JSON, and the #554
    migration already covers the estate's actual JSON columns
    (`properties`, now VARIANT).
  - the four lane stores + embed caches carry only `model` stamp
    columns and doc_search's `meta` VARCHAR.
- **No read-path substrate**: zero json_extract calls touch any
  provenance column (the sole nearby hit in derive_hyperedges.py:1226
  selects source_ref as a column beside a properties extraction). The
  "read-heavy long-tail key access" trigger has nothing to read.
- **Verdict: NO-GO.** S1 (migration) and S2 (structured provenance)
  never fire — FI-1's premise ("provenance JSON stored as TEXT") was a
  design-phase assumption with no live counterpart. Closed in
  pending.md with a dated note; reopen only if a provenance column is
  ever introduced as JSON-TEXT.
