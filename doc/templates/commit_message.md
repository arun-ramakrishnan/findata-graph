<!-- contract: doc/procedures/commit-messages.md -->
<!-- Seed for commit messages: copy the skeleton below, fill every <slot>,
     delete the variant blocks you did not use, then apply it with
     `stg edit -f <file> <patch>` (stg) or `git commit -t <file>` (git).
     Guarded by tests/test_templates.py (declaration guard). -->

# Commit message seed

## SUBJECT

    [Findata] <area>: <imperative summary — name the mechanism, not the symptom>

- `[Findata]` on everything GitHub-bound. Two bare exceptions (mechanical,
  stack-local data refreshes): `db_sync: ...` and `snapshot: ...`.
- `<area>` = the arc or subsystem in 1-3 words: `quote capture`,
  `derive render arc`, `OKF v0.2 provenance backfill`, `Triage hygiene`,
  `Code duplication consolidation`.
- Upstream PR squashes append `(#N)`.
- 50 chars target, 72 hard cap.

## BODY (in this order; drop blocks that do not apply)

### 1. WHY — root cause, directive, or proposal back-ref (short paragraph)

    Implements doc/improvements/archive/<area>/<proposal>.md (completed.md #N).

### 2. WHAT — bulleted slices, smallest-risk first, concrete numbers each

    - S1 <file>: <175 -> 45 lines / 7 regexes / 2 asserts> — what now single-owns it
    - S2 <surface>: <rows/files/bytes before -> after>

### 3. DATA PROVENANCE — corpus / note-render patches only

    Rendered <YYYY-MM-DD HH:MMZ> by <tool>/<ver> --apply. Content only;
    machinery lives in the paired <name> patch.

### 4. SNAPSHOT MECHANICS — parquet/binary patches only (db_sync, snapshot)

    Diffs are reviewable as text: *.parquet is binary + diff=parquet
    (.gitattributes); helpers/misc/parquet_textconv.py renders row
    count, schema, sample (PARQUET_TEXTCONV_ROWS / _CELL knobs).
    Cite the row deltas the diff will show so the message
    cross-checks the diff: graph_edges 18,269 -> 18,322.
    note_search_content / v_note_embeddings stay untracked
    (snapshot_untrack_large) - their absence from this patch is
    expected.

### 5. TRAILERS

    Gates: make qa 9/9, make advisory 10/10, make perf 22/22,
    make search-fresh APPLY=1, make static_checks all pass

    List only gates actually run, with their counts. `Gates:` is the
    only required trailer for code arcs; data refreshes may omit it.
