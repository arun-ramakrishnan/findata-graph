# Instructions for LLM/agent sessions

## Query the doc/ knowledge index — don't scan the docs

This repo's design corpus (`doc/` — architecture, ~30 archived proposals,
the completed.md run log, procedures, local assessments; ~380 indexed
sections) is **content-addressable**. Before reading doc files wholesale
to answer a design/decision/history question, query the index and jump
straight to the ranked section:

```bash
.venv/bin/python3 helpers/misc/doc_query.py "why did we not adopt langgraph" --limit 5
.venv/bin/python3 helpers/misc/doc_query.py "embed cache" --json      # machine-readable
```

- Output lines are `path:line [section] snippet` — paths are REPO-ROOTED
  (`doc/procedures/embeddings.md`), so the `path:line` is a direct
  `Read(offset=line)` / editor-jump target from the repo root.
- Punctuation and full-sentence questions are safe (tokens are OR-joined).
- HTTP equivalent (if the app is running on :5200):
  `GET /api/docs/search?q=...` → `mode: hybrid|bm25|scan`.

Rules of the road:

- **Index stale or missing?** The CLI warns on stderr and still answers;
  refresh with `.venv/bin/python3 helpers/maintenance/rebuild_doc_search.py`
  (warm ≈ instant — content-hash embed cache). The endpoint degrades to a
  filesystem scan automatically.
- **Don't** `cat` 40 KB proposals to find one decision — query first, then
  read only the linked section. The 166 KB `doc/improvements/completed.md`
  especially: query it, never read it whole.
- The index covers ALL of `doc/` including gitignored `doc/local/` — it is
  machine-local (sidecar DB under gitignored `memory/`), so local-only
  knowledge is queryable but never published.
- Full operator doc: `doc/procedures/doc-search.md`; design + eval:
  `doc/improvements/archive/tooling/doc_search_embeddings.md`.
