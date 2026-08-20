# Pending improvements

- **Re-evaluate HNSW index macros** (deferred N5 item 5). `hnsw_index_scan`,
  `vss_match`, and `pragma_hnsw_index_info` emit empty-signature binder errors
  on the vss build (re-verified on DuckDB 1.5.5 + Onager 49ad15b). Brute-force
  VSS works (~3ms @ 1k) so nothing is broken today; revisit via quarterly
  `make update-extensions` and re-test the macros (graph_design.txt §18.5/§5.4).

- **Wrap `onager_ctr_personalized_pagerank`** (deferred N5 item 6). Onager bug:
  personalisation column ignored, restart node hardcoded to `node_id 1`, and it
  requires a weight column of type `BIGINT`; variants A/B produce identical
  output. Documented at `helpers/graph/onager.py:597` and graph_design.txt §5.5.
  Do not wrap until a future Onager build honours the personalisation vector.

- **`listed_on_index` membership edge** (deferred N5 item 7). The
  `index_membership` column was dropped 2026-07-28; the edge was never built.
  Requires a re-ingest pass extracting `index_membership:` from company YAML
  frontmatter before it can be materialised. Deferred by design.

- **Security Phase 4 (deploy-time; app confirmed NOT deployed 2026-08-17)**
  (private security review under doc/local, untracked;
  Phases 1/1b/2/3/5 DONE — completed.md #116/#117; SEC-9 closed: key
  revoked, GitHub never had the blobs — standing caution: never
  `git push --mirror` / push `main.stgit`; keep running `make secret-scan`
  after big pushes). Activates only if the Flask app is ever deployed
  publicly: dev-default `FLASK_HOST=127.0.0.1`; auth/shared-secret in
  front of `POST /api/graph/refresh`; `uv lock`.

- **Technology avenues EXECUTED & ARCHIVED** (archived proposal:
  `doc/improvements/archive/tooling/tech_avenues.txt`, 2026-08-17→18): every
  shortlist item done — A1 sqlite-vec KNN (#124; ~7ms accepted; vec0
  sidecar regression fixed en route #126), B1 JSON-Schema contract
  (#125), C1 context packs (#127), C2 link-prediction suggestions +
  A3 parquet analytics (#129; advisory/live-invariants Makefile overhaul
  same entry), A4 PRAGMA optimize micro-win (#128). Optional leftovers
  if ever wanted: B2 relation sidecars, C3 temporal analytics. Dropped:
  Obsidian-UI (not an active use case). Parked: D MCP server (seam
  sketch preserved in the archived proposal). Blocked: C4 Kùzu (upstream
  archived 2025-10-10). Anti-recs on record: LanceDB/third vector store,
  Turso, pgvector, YAML anchors.

- **C3 temporal analytics** (tech-avenues leftover, re-ranked 2026-08-19 as
  "whenever wanted"). Unlocked by #134/#136: edition entities carry git
  add-dates, `cited_in` edges + `sources[]` form a clean time axis, and
  `quotes.as_of_edition` is now joinable stems. Candidate report:
  `make analytics REPORT=temporal` — chatter volume by quarter, coverage
  trend per series, staleness curve by sector. Read-only parquet reads,
  ~2 h. Not in the okf_readside bundle (archive/; no note-writing, no shared test
  path).

- **OpenViking context-server pilot DEFERRED** (2026-08-20; proposal with
  full fact-check at `doc/local/openviking_pilot_proposal.md`). The gap
  it targeted — real semantic embeddings — is pursued in-house first:
  `doc/improvements/archive/database/local_embeddings.md` (local bge-small-en,
  no new service). Revive only for the context-server differentiators
  (L0/L1 hierarchy, automatic memory extraction, retrieval traces); the
  labeled eval set built for the in-house proposal transfers verbatim.
  Known-if-revived: default embedder is Chinese-tuned with a one-model
  registry (swap needs `model_path` + explicit `dimension` + full
  rebuild — upstream issue #1523); `vlm` key is `api_base`; OKF-RFC
  claim unverified; ZCode already has cross-session memory (agent
  premise corrected from `opencode-go`).

- **OKF read-side live propagation (operator-held, 2026-08-19)**: the N1
  footnote churn and the first N3 `verified[]` stamps are confirmed working
  and deliberately left for manual runs. N1 (#139 update): the ordinary
  `--stale-only --apply` now propagates renderer drift itself (dry-run:
  314 chatter notes would write, 14 gated) — no special full apply needed.
  N3:
  `python3 helpers/misc/okf_verify.py <note>... --apply` per note as
  reviewed. No code pending — these are footprint decisions, not work
  items.
