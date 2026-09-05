# Pending improvements

Full annotated triage map with live-verified trigger status:
`doc/local/future_items.txt` (2026-09-05). Open items below keep their
revisit triggers inline; executed work is compressed to records.

- **Re-evaluate HNSW index macros** (deferred N5 item 5). `hnsw_index_scan`,
  `vss_match`, and `pragma_hnsw_index_info` emit empty-signature binder errors
  on the vss build (re-verified on DuckDB 1.5.5 + Onager 49ad15b; extension
  binaries unchanged since 2026-08-14/09 — nothing new upstream to test).
  Brute-force VSS works (~3ms @ 1k) so nothing is broken today; revisit via
  quarterly `make update-extensions` (~Nov 2026) and re-test the macros
  (graph_design.txt §18.5/§5.4).

- **Wrap `onager_ctr_personalized_pagerank`** (deferred N5 item 6). Onager bug:
  personalisation column ignored, restart node hardcoded to `node_id 1`, and it
  requires a weight column of type `BIGINT`; variants A/B produce identical
  output. Documented at `helpers/graph/onager.py:597` and graph_design.txt §5.5.
  Do not wrap until a future Onager build honours the personalisation vector.

- **`listed_on_index` membership edge** (deferred N5 item 7). The
  `index_membership` column was dropped 2026-07-28; the edge was never built.
  Requires a re-ingest pass extracting `index_membership:` from company YAML
  frontmatter before it can be materialised. Live 2026-09-05: only 9/1,079
  company notes carry the key — not worth the pass until coverage grows.
  Deferred by design.

- **Security Phase 4 (deploy-time; app confirmed NOT deployed 2026-08-17)**
  (private security review under doc/local, untracked;
  Phases 1/1b/2/3/5 DONE — completed.md #116/#117; SEC-9 closed: key
  revoked, GitHub never had the blobs — standing caution: never
  `git push --mirror` / push `main.stgit`; keep running `make secret-scan`
  after big pushes). Activates only if the Flask app is ever deployed
  publicly: dev-default `FLASK_HOST=127.0.0.1`; auth/shared-secret in
  front of `POST /api/graph/refresh`; `uv lock`.

- **B2 relation sidecars** — optional tech-avenues leftover
  (`archive/tooling/tech_avenues.txt` §3): per-relation YAML sidecars with
  provenance (edge_type, counterparties, as_of, confidence, source permalink).
  The only unblocked medium item anywhere in the backlog — but the driver is
  weak while `findata/_pending_relations.txt` stays near-empty (see
  `doc/local/future_items.txt` §D for the queue run book).

- **OpenViking context-server pilot DEFERRED** (2026-08-20; proposal with
  full fact-check at `doc/local/openviking_pilot_proposal.md`). The gap
  it targeted — real semantic embeddings — closed in-house (#141, bge-small-en).
  Revive only for the context-server differentiators (L0/L1 hierarchy,
  automatic memory extraction, retrieval traces); the labeled eval set
  transfers verbatim. Known-if-revived: default embedder is Chinese-tuned
  with a one-model registry (swap needs `model_path` + explicit `dimension` +
  full rebuild — upstream issue #1523); `vlm` key is `api_base`.

- **P2.2 incremental DuckDB materialization** (archive/graph/graph_pending.txt)
  — row trigger FIRED 2026-09-05 (17,323 graph_edges > 10k) but the deferred
  reason (rebuild cost) is not binding: full rebuild measures 3.0 s against
  vault_scaling's 5 s DuckDB budget. Scale strategy is owned by
  `archive/graph/vault_scaling.md` (#204); re-evaluate when its T1 fires
  (~1M doubled rows; currently 34K) or measured rebuild > 5 s.

## Executed — records only (kept for audit; details in completed.md / archive)

- **Technology avenues** — EXECUTED & ARCHIVED 2026-08-17→18
  (`archive/tooling/tech_avenues.txt`): A1 sqlite-vec KNN #124, B1 JSON-Schema
  contract #125, C1 context packs #127, C2 link-prediction + A3 parquet
  analytics #129, A4 PRAGMA-optimize #128. Parked: D MCP server (§5 seam
  sketch; re-open on operator request). Blocked: C4 Kùzu (upstream archived).
  Dropped: Obsidian-UI. Anti-recs standing: LanceDB/third vector store,
  Turso, pgvector, YAML anchors.
- **C3 temporal analytics** — DONE 2026-08-25 (#150;
  `archive/tooling/temporal_analytics.md`): `make analytics REPORT=temporal`.
- **OKF read-side live propagation** — DONE 2026-08-25. N1: 318 notes carry
  `[^chatter-*]` footnotes (commit 4da573b). N3: first `okf_verify.py`
  `verified[]` stamps operator-recorded. No further work.
- **Parallel cold embed** — DONE 2026-08-29 (#173;
  `archive/tooling/parallel_cold_embed.md`): cold note_search 16m13s → 6m01s
  (2.70×); company ~11–15 min → 4m46s. Its §7 deferred-at-scale record holds
  the remaining deferred levers with revisit triggers (all unmet as of
  2026-09-05). Incremental-snapshot item closed by #174.
