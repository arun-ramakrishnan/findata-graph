# Local Embeddings & Note-Search Procedure

How the repo's two embedding surfaces are populated, refreshed, and
re-applied after a model or corpus change. Companion procedure to
`doc/improvements/archive/database/local_embeddings.md` (design + implementation
log). All commands assume the repo venv (`make` prepends `.venv/bin`; for
direct shell runs use `.venv/bin/python3`).

## The two parts (one embedder, two indexers)

| | `helpers/graph/embeddings.py` | `helpers/maintenance/rebuild_note_search.py` |
|---|---|---|
| Index | `company_embeddings` (research.db) | `note_search` FTS5 + vec0 sidecar mirror |
| Granularity | **One vector per entity** (~1,050 companies) | **One row per document** (~1,227: companies, sectors, super-sectors, every newsletter edition) |
| Text basis | name + sector + note body first 5,000 chars | title + sector + body first 8,000 chars (newsletters have no entity rows; body text lives only in files) |
| Answers | "Which known company is this name?" (`vss_match` in get_tickers); "companies similar to X" (`semantic_neighbors` via DuckDB `v_embeddings`) | "Which documents discuss X?" (`/api/search?hybrid=true`: BM25 + cosine fused with RRF) |
| Refresh | `make maint-full` step 6b (`--maint`, cached); manual apply only for model upgrades (this procedure) | Every `make maint` step 6 (and standalone) |

Both sides embed through **one module** — `helpers/core/local_embedder.py`
(bge-small-en-v1.5, 384-dim, offline). The BGE rule that queries carry a
retrieval prefix and documents do not lives only there; never embed at a
call site. The vec0 mirror and the pooled **embed cache**
(`helpers/core/embed_cache.py`, one `(sha256(text), model, source)` table
serving every indexer — note/company/doc/script cohorts) live in the
**consolidated embed store** `memory/embed_store.db` (attached as schema
`vecdb`; never research.db itself: DuckDB's SQLite scanner cannot
catalog-scan vec0 virtual tables). The store replaced the per-index
`<db>_vec.db` sidecars in 2026-08 (embed_store_consolidation); the legacy
files survive renamed as `*.migrated.bak` until hand-cleanup.

## One-time setup (per machine)

```bash
uv pip install llama-cpp-python        # builds from sdist (~4 min; needs gcc + cmake)
mkdir -p models && curl -L -o models/bge-small-en-v1.5-q8_0.gguf \
  "https://huggingface.co/CompendiumLabs/bge-small-en-v1.5-gguf/resolve/main/bge-small-en-v1.5-q8_0.gguf"
python3 helpers/core/local_embedder.py   # self-check: available, dim=384
```

The model file (35.1MB) is gitignored and sha256-pinned in
`local_embedder.py`; a mismatched file refuses to load. No network is used
at index or query time — the download above is the only fetch.

## `--check` and pre-warming (read this before the apply)

`rebuild_note_search.py --check` is a dry run: it walks every doc, embeds
it, prints "would index N docs", and writes **nothing to research.db**.
It also prints a FRESH/STALE drift verdict (changed/new/deleted docs) and
**exits 1 when stale**, matching the doc/script rebuilders since #164 —
chain it into apply steps with that exit code in mind. The embed results
ARE stored in the pooled store cache (derived state — that is its job). Because embeddings are cached by content hash, "pre-warming"
lets you pay the cold cost in a zero-risk dry run first:

```bash
python3 helpers/maintenance/rebuild_note_search.py --check   # ~16 min once, DB untouched
python3 helpers/maintenance/rebuild_note_search.py           # ~0.8 s — every doc is a cache hit
```

Skipping the pre-warm is fine — the applying run then pays the ~16 min
itself. Same total cost, less convenient.

## Apply procedure (model upgrade / re-apply)

> **Clear before populate — mandatory.** The purity guard
> (`_ensure_single_model`) hard-stops any attempt to write bge rows over
> rows carrying a different model label: cosine across different models'
> vector spaces is garbage. Run `--clear` first, always.

Run the pre-warm (above) in a second terminal while step 3 grinds — they
are independent.

```bash
# 1. Look-only sanity — expect the PRE-STATE: total=1050, models=["dry-run-v384"], sample_dim=384
python3 helpers/graph/embeddings.py --stats

# 2. Wipe the old-model rows (required by the purity guard)
python3 helpers/graph/embeddings.py --clear

# 3. Repopulate with the local model — the one-off: ~15–20 min on a 4-core box.
#    Since 2026-08-21 this run goes through the shared sidecar cache and SEEDS
#    it (company texts cached by content hash), which is what makes later
#    maint-full refreshes warm.
python3 helpers/graph/embeddings.py --model bge-small-en-v1.5

# 4. CHECKPOINT — must show exactly ONE model: ["bge-small-en-v1.5"], total=1050.
#    Anything else: STOP before graph-rebuild and investigate.
python3 helpers/graph/embeddings.py --stats

# 5. Refresh note_search embeddings (~0.8 s if pre-warmed; ~16 min cold)
python3 helpers/maintenance/rebuild_note_search.py

# 6. Materialise into DuckDB
make graph-rebuild

# 7. Snapshot regen (only v_embeddings.parquet content changes) + verify
python3 helpers/maintenance/snapshot_db.py
make snapshot-check
```

Notes:

- **Do not run `make maint` mid-apply** — with the model present, its step 6
  would start its own cold note_search rebuild concurrently. Let this
  sequence own the upgrade.
- After step 4, `get_tickers.vss_match` switches to `embed_query`
  automatically (it reads the table's model label) — no extra step.
- Notes themselves are never touched; nothing leaves the machine.
- What changes: `company_embeddings` content + label, `note_search`
  embeddings + sidecar cache, DuckDB `v_embeddings`.

## Cost reference (4-core box, measured 2026-08-20)

| Path | Cost |
|---|---|
| Model load | ~0.1 s |
| Embed one full doc (512-token truncation) | ~0.8 s |
| note_search full refresh, cold (no cache) | 16m13s / 1,227 docs |
| note_search full refresh, warm cache | 0.8 s |
| Company populate, cold (all 1,050) | ~15–20 min |
| Company populate / `--maint`, warm cache | seconds (reads + hashes; ≈0 embeds on a no-change cycle) |

Cold-cache situations: first apply, a model swap, and the first rebuild
after `make snapshot-restore` (the embed store is excluded from snapshots by
design — the gzip/raw backup twins in `db-backup/` are the recovery
points). A q4_k_m quant halves per-doc cost at a small quality cost if the
cold path ever matters (constants + sha re-pin in `local_embedder.py`).

## What happens when a new letter is processed

Processing (parse_newsletter → enrich → derive_* via maint-full) rewrites a
batch of notes: the letter file itself, company notes receiving
chatter/metric blocks, sector rosters. Afterwards:

- **note_search: mechanically full, economically incremental.** Maint's
  step 6 re-reads and reinserts all ~1,227 docs every run (self-correcting
  by design), but each doc's text is hashed against the pooled store cache —
  unchanged text is a cache hit and never re-embeds. Only the new letter
  and the handful of edited notes re-embed (~0.8 s each) and get cached. A
  typical letter cycle costs seconds-to-a-minute. The vec0 mirror follows
  in lock-step. (An explicit `--incremental` flag exists but maint never
  uses it — the cache made it unnecessary.)
- **company_embeddings: cached refresh in maint-full (step 6b).**
  `embeddings.py --maint` runs right after rebuild-note-search: same
  cache economics (unchanged companies are hits, only notes changed by the
  letter cycle re-embed), plus GC of companies deleted since the last run.
  It is best-effort by contract: with the embedder unavailable or the table
  not bge-populated it prints one WARNING, writes nothing, and exits 0 —
  maint never auto-upgrades company embeddings; a model upgrade is this
  procedure's apply, run by hand.
- **One-time seeding (2026-08-21).** The original apply predated the
  company-side cache; those texts now live in the pooled store stamped
  `legacy-research` (both populations, indistinguishable post-hoc — the
  migration could not split them). New/changed companies written by future
  `--maint` runs get stamped `company`. Run
  the populate in step 3 once more (same ~15–20 min, writes identical
  vectors — embeddings are deterministic — and seeds the cache), or accept
  ONE cold first maint-full; every run after that is warm.
- **DuckDB/snapshot:** `v_embeddings` re-materialises on the next graph
  connect; `v_embeddings.parquet` drifts after any run that changed
  vectors — fold the snapshot regen into the normal db-sync flow.
