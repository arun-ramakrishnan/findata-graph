---
title: "One-store consolidation: research.db + embed_store.db + graph.duckdb → single DuckDB (gated)"
status: deferred
filed: "2026-11-06"
executed: "2026-11-06"
completed_md: "483"
area: "helpers/core/db.py writer stack (113 importers) + helpers/maintenance/{db_maint,snapshot_db}.py backup/snapshot legs + helpers/graph/query.py materialise/quack posture + corpus.db disposition"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# One-store consolidation: research.db + embed_store.db + graph.duckdb → single DuckDB (gated)

**Date:** 2026-11-06 · **Status:** PROPOSED ·
**Filed out of:** graph_hnsw_next.md S4 (SUITE: embed-store consolidation)
and the re-entry protocol in
doc/local/evaluations/duckdb_consolidation_assessment.md (trial closure,
2026-09-10: B2 contention map and B1 port sizing precede any storage-merge
proposal; proposal gate = hybrid-attached accepted ✓ B3, single-writer
livable ← B2, port cost < ops win ← B1).

## 1. What this proposal asks for

Not a migration. S4's own text gates the merge on the assessment's
Re-open triggers, and §6 of graph_hnsw_next scopes S4 as "a separate
consolidation proposal gated on the §4 re-open triggers". This document
runs that gate: trigger verdicts (§2), the B2-lite contention map (§4),
the B1-lite port sizing (§5), the posture fork any merge must resolve
first (§3), and a recommendation (§7). Execution slices exist (§6) but
every one is gated on the operator's posture decision.

## 2. Trigger verdicts (assessment Revisit triggers vs 2026-11-06)

1. **v2.0 + fts parity — NOT fired.** DuckDB 2.0.0 shipped and is the
   production line, but fts parity is not met: no snippet()/highlight(),
   no column-weighted bm25(), no NEAR/phrase syntax (B3 standing gaps).
   The convo lane accepted that loss (pure-stack S3, bank gate passed);
   the doc/script/note lane CLIs, app /api/search, and the three --check
   gates did not. Corpus 19,328 notes — not O(100k+).
2. **Second concurrent writer — NOT fired.** Single-writer posture
   accepted 2026-09-29; quack serves reads only.
3. **duckpgq — NOT fired.** Retired 2026-08-14; no property-graph need.
4. **HNSW/vss production-ready — PARTIALLY fired.** Persistent HNSW now
   runs in production on three lanes (convo idx_convo_search_emb since
   the S1 cutover; note_vectors idx_note_search_vec_emb since pure-stack
   S2; graph.duckdb indexes built 2026-11-06 ~09:00). Caveats remain:
   experimental flag required on every session, wholesale reserialization
   at checkpoint, strict recall .8649 @191k (tie .8947) — not the 100%
   the 1.5.x-era semi-join plan reported. Fired enough for vectors (they
   already moved); does not by itself justify relational consolidation.

**Net:** the consolidation gate is NOT met on the assessment's own terms.

## 3. The posture fork (decide BEFORE any merge)

The assessment's blocker table (2026-09-10, still true): one DuckDB file
admits exactly one RW process at a time, or multiple RO processes — the
modes cannot be mixed while the file is open. Today the two regimes are
separate files, so they coexist: research.db + embed_store.db live under
SQLite WAL (N readers + concurrent writers), graph.duckdb lives under
quack (server holds RW 24/7; writes only in stop-file windows). A merge
collapses the first regime into the second. Two postures:

- **P1 — embedded single-writer, no server on the merged file.** All
  writers funnel through one flock holder (the graph.duckdb doctrine,
  widened); Flask flips to read-only opens (today get_db_connection
  returns connect() in RW mode — handlers write nothing, verified: zero
  INSERT/UPDATE/DELETE in request paths — but an RW-mode DuckDB open IS
  the exclusive writer, so this flip is mandatory in every posture).
  Graph queries lose the server: per-query connect ≈ +50 ms on top of
  query cost (B3 measurement) unless a read-only persistent connection
  is kept per process.
- **P2 — quack-everything.** Server holds the merged file RW 24/7 →
  NO local open of any kind (even RO) is possible while it is up. Every
  consumer becomes a wire client (duckdb_shared._attach_wire and the
  served-detection in open_read_only already exist — the
  served_store_read_funnel proposal is the scaffolding) or waits for
  stop windows. Interactive entity edits (rename_entity, move_sector)
  become server round-trips or batched windows.

## 4. B2-lite — contention map (static, 2026-11-06)

Write surfaces that would funnel into the merged file:

1. Flask app — holds RW-mode research.db connections but writes nothing
   (flip to RO mandatory).
2. Rebuild/maint CLIs — rebuild_note_search writes the note corpus AND
   the FTS5 note_search table INSIDE research.db; db_maint (VACUUM /
   backup twins), snapshot_db.
3. derive-* family — shared stable_write primitive
   (DELETE-prefix-then-INSERT, content-matched).
4. Entity ops — rename_entity, move_sector, extract_relations,
   derive_merger_hyperedges, name_dedup, speaker_census, backfill_*.
5. Ingest/sync — sync_tags, parse_newsletter, corpus.py (separate store).
6. Embed-cache writes — embed_cache init/backfill from rebuilds and
   benches; mostly rebuild-time already (post pure-stack S2, no
   request-time backfill was found in app.py).
7. Graph materialise — rebuild window via stop-file + quack restart.

Load-bearing concurrency that SQLite WAL provides today: Flask sessions
coexist with rebuild writers; cache warming coexists with everything. A
merged embedded file serializes all of it through one lock. Verdict:
**livable under P1** (writes are CLI-batched; Flask is RO in fact) at
the cost of the graph-lane server; **P2 is a full wire conversion** —
scaffolding exists but 113 importers do not use it today.

## 5. B1-lite — port sizing (static, 2026-11-06)

- **113 prod modules** import helpers.core.db (2026-09-10 review said
  44 — the blast radius GREW 2.6× while consolidation waited).
- **19 raw sqlite3.connect prod sites** outside db.py (house rule keeps
  writes on helpers.core.db; these are readers/tooling).
- **FK CASCADE is still a roadblock — measured today on 2.0.0:**
  CREATE TABLE b(aid INTEGER REFERENCES a(id) ON DELETE CASCADE) →
  Parser Error: FOREIGN KEY constraints cannot use CASCADE, SET NULL or
  SET DEFAULT. 33 ON DELETE CASCADE sites in schema/DDL strings; db.py
  sets PRAGMA foreign_keys = ON so cascades are active in production.
  Porting = explicit ordered multi-table deletes inside one transaction
  for every cascade site.
- **Notes lane FTS5 lives inside research.db** → a merge must either
  accept the lexical regression (convo lane did; the other lanes and
  app/search gates did not) or adopt Alternate B from the review
  addendum: FTS5 stays SQLite PERMANENTLY as a by-design sidecar.
- **Maintenance redesign:** integrity gate is PRAGMA-based
  (integrity_check / foreign_key_check) with no DuckDB 1:1; the 43-table
  parquet snapshot set gains DuckDB tables; the 4-worker backup pool
  serializes on one file; VACUUM/ANALYZE semantics differ.
- **corpus.db (61 MB, content-hash cache, rebuildable 8.6 s) — stays a
  cache, never merges.**

## 6. Execution slices (ALL gated on the §3 posture decision)

- **S0 — spikes (cheap, decision-enabling):** FK-CASCADE probe ✓ done
  (rejected, §5); re-measure per-query embedded connect cost on 2.0.0
  against graph-lane query mix; inventory the wire-client conversion if
  P2. Output: the posture decision recorded here.
- **S1 — relational merge (Alternate A, P1 only):** research.db
  relational core (entities, edges, notes text, derived tables) into
  graph.duckdb; note_search FTS5 stays a SQLite sidecar by design
  (Alternate B); writers under one flock funnel; Flask RO.
- **S2 — blob cache + flat matrix decision:** embed_cache STAYS a SQLite
  store (2026-08 consolidation just pooled it; its WAL multi-process
  warming is its value; 455 MB freelist-0 live data per spike E) — the
  S4 one-liner's "blob cache + flat matrix decision" resolves to KEEP;
  embed_matrix.f32 stays a derived-on-demand artifact.
- **S3 — maintenance legs:** DuckDB-leg integrity gate, snapshot
  coverage for the merged file, serialized backup pool redesign.

## 7. Recommendation

**Do not merge now.** Since 2026-09-10 the merge's ops win shrank while
its cost grew: the convo lane is already self-contained DuckDB (2.8 GB,
own swap/backup story), vectors already mirror where queries run
(note_vectors.duckdb, graph.duckdb), and the remaining two-engine sync
is the research.db → graph.duckdb materialise — one rebuild command,
3.7 s benchmarked. What a merge buys today is one-file backup and no
ATTACH dance; what it costs is a 113-module port, 33 cascade sites
re-solved by hand, a maintenance redesign, and the §3 posture change.
Keep the split. Revisit on trigger 2 (a real second-writer requirement)
or a concrete backup-pain event; run S0's spikes only if the operator
wants the option priced.

## Acceptance (this proposal)

- Trigger verdicts, contention map, and port sizing recorded with the
  measurements above ✓ (filed 2026-11-06).
- The posture decision (§3) and the S1–S3 gates are recorded in the same
  change as any future execution go — this document is the gate, not a
  work order.
