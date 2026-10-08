---
title: "Adopt gemma vectors + edge-enriched lexical leg for the notes hybrid — flip the notes surface to the trial's winning shape"
status: executed
filed: "2026-10-08"
executed: "2026-10-09"
completed_md: "369"
area: "helpers/maintenance/rebuild_note_search + helpers/misc/note_query + notes eval bank"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Adopt gemma vectors + edge-enriched lexical leg for the notes hybrid

**Date:** 2026-10-08 · **Status:** EXECUTED 2026-10-09 (acceptance
green; see §6 execution record) ·
**Area:** helpers/maintenance/rebuild_note_search + helpers/misc/note_query +
notes eval bank

**Follows:** `notes_query_side_levers_trial.md` (the gating trial —
definitive full-pool verdict recorded same day in the assessment §6.4),
`company_embeddings_gemma_trial.md` (#368), `memory_search_gemma_migration.md`
(#367), `script_search_gemma_adoption.md` (#363 — the per-surface adoption
pattern), and `granite_sidecar_selector.md` (revived same day — the notes
flip makes notes the fourth gemma surface, strengthening the sidecar
symmetry argument).

## 1. Motivation

The definitive full-corpus run (75-q extended bank, 17,263 sections):
`hy_gemma_enriched` **41/75** vs the shipped granite hybrid **30/75**
(+11), replicating the quick-pool verdict exactly; the quick-pool
vector tie broke gemma's way on the full corpus (+6); `ml_hard`
un-froze (4/5). Narrow and facet-expanded readings agree. The notes
surface is the last granite/384 surface; the winning shape is
**enriched lexical leg (ticker + edge_context, rare-first @ 900) +
gemma query/section vectors (pure-prose basis) + production RRF k=60**.
The 4-hour gemma embed already exists as
`bench_data/embgemma2/notes_arm_gemma_full.npz` — adoption banks it
into the production cache instead of re-embedding.

## 2. Slices

- **S1 — cache seed (banks the 4-hour run)**: one-shot migration
  script recomputing the gemma doc-side basis per live `note_search`
  row — byte-identical to the trial arm's construction
  (`title: {t} | text: {sector} — {section}\n{content[:8000]}`) — and
  inserting `(text_hash, gemma label) -> vector` into `embed_cache`
  from the npz (joined on `(file_path, anchor)`). Success test: the
  post-flip rebuild scores **0 sidecar embeds** (all cache hits).
- **S2 — index side** (`rebuild_note_search.py`): FTS DDL gains
  **indexed** `ticker` + `edge_context` columns (production port of the
  trial's `context_for` builder: accepted `graph_edges`, rare-first,
  cap 900, per-rel 8); `_embedding_text` flips to the gemma doc-side
  prefix basis (byte-parity with S1); embedder → `gemma_embedder`
  sidecar; `stored_embed_dims` 384 → 512; `db_meta.note_embed_model` =
  `embeddinggemma-2-q8_512`. **`guard_gemma_stamp` semantics**: a
  gemma-stamped notes index refuses rebuilds with the sidecar down
  (D3) — notes gain the hard sidecar dependency granite never had.
  Schema change rides the existing `_migrate_schema` drop-rebuild.
- **S3 — query side** (`note_query.py`): query embedder → gemma
  (`task: search result | query:` prefix, 512-d); stamp-checked shared
  query-vector path unchanged; bm25 equal weights unchanged (boosts are
  a measured null); RRF k=60 unchanged. TUI notes lane + master_query
  inherit through this client.
- **S4 — envelope**: matrix rebuild (`embed_matrix.f32` 512-d);
  `embed-gc` evicts granite note vectors (stamp-following); snapshot
  constants for the wider `note_search_content` shadow; bank regression
  gate (75-q bank via the production client, floor = recorded numbers);
  hermetic tests unchanged (pseudo fallback), stamp-contract + sidecar
  down-refusal tests added.

## 3. Explicitly unchanged

- companies/memory/script surfaces (each keeps its adopted shape);
- the bm25 column weights (boost levers stay dead);
- derive pipeline (consumes accepted edges as eval substrate only);
- convo stays granite (no recall bank).

## 4. Acceptance criteria

1. Seed proves out: rebuild after flip reports 0 embed misses
   (17,263/17,263 cache hits from the npz-seeded rows).
2. `db_meta.note_embed_model` = gemma label; live note_search carries
   512-d vectors + the two enriched columns; `llamacpp-health`-style
   stamp checks green; embed-gc removes the superseded granite rows.
3. Bank gate: production `note_query` hybrid over the 75-q bank scores
   **≥ 39/75** (margin below the recorded 41 for ingest drift), with
   per-tier shape matching the trial (relational sweep intact).
4. Snapshot `snapshot-check` + restore round-trip green on the new
   shadow shape; targeted suites (note_query, rebuild_note_search,
   embed_cache, vec_search) green; ruff/format clean.

## 5. Non-goals

- No changes to company/memory/script/convo surfaces; no derive changes;
- no re-trial of dead levers (boosts, vector-basis enrichment — edges
  stay lexical-only, the §6.4 doctrine);
- granite stays the in-process failsafe for OTHER surfaces; the notes
  surface is gemma-sidecar-primary with refusal semantics.

## 6. Execution record (2026-10-09)

- **S1 seed**: `helpers/maintenance/seed_note_gemma_cache.py` banked
  **17,263/17,263** npz vectors into `vecdb.embed_cache` under the gemma
  label — zero npz↔corpus drift (the exactness guard passed before
  writing), 1.55 s, 253 MB peak. First draft OOM'd the box (per-access
  npz decompression → swap storm); the streaming rewrite is the
  documented fix.
- **S2 index side**: DDL + `_migrate_schema` guard carry the indexed
  `ticker`/`edge_context` columns; `context_for` ported rare-first @
  900/8 (`_edge_ctx_maps`, hermetic-dbs degrade to empty); gemma basis
  `_embedding_text` (byte-parity with the seed); gemma-first resolver
  with `NOTES_EMBEDDER=auto|gemma|granite` + `guard_gemma_stamp` write
  refusal; batch path sidecar-aware (serial gemma mapper, no granite
  spawn pool). Live migration verified: 17,263 rows @ 512-d, stamp
  `embeddinggemma-2-q8_512`, 9,282 ticker / 8,077 edge_context rows.
- **S3 query side**: `query_embedder(model_label)` follows the index
  STAMP, not the env; `note_query.semantic_hits` passes the stored
  stamp; app.py `_resolve_query_vec` same. Sidecar-down on a gemma
  index → RuntimeError → the leg degrades to BM25-only with reason.
- **S4 envelope**: bank gate `bench_data/embgemma2/notes_prod_gate.py`
  = **41/75** (floor 39; recorded trial number reproduced exactly —
  per-tier distribution shifted with the trial's documented 444-row
  vector-pool depression: compare 1→7, ml tiers gave it back; the
  relational sweep is intact). `gc_embed_cache` report: notes dead 0
  (all live bases cached; granite note rows retained per the 2026-10-08
  rollback-insurance doctrine). `snapshot-check` green. 177 targeted
  tests + fuzz green; ruff/format clean.
- **Test-contract updates**: pseudo/legacy-path tests pin
  `gemma_embedder.available()->False`; `TestVecMirror` cosine test made
  hermetic (it historically rebuilt the LIVE db and silently migrated
  it mid-suite once the sidecar went live); stamp-contract +
  sidecar-down-refusal + gemma-512 tests added.
