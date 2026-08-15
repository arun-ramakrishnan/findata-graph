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
