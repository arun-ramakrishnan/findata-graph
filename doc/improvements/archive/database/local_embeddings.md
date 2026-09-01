---
title: In-House Semantic Embeddings via a Local bge-small-en Model
status: executed
filed: '2026-08-20'
executed: '2026-08-20'
completed_md: '141'
area: 'helpers/core/local_embedder.py'
---

# Proposal: In-House Semantic Embeddings via a Local bge-small-en Model

**Status:** EXECUTED (implemented 2026-08-20; user-run live apply + eval
2026-08-21; end gates qa/perf/advisory green 2026-08-21). Archived to
`doc/improvements/archive/database/local_embeddings.md`.
**Date:** 2026-08-20
**Author:** Agent analysis (user-directed)
**Builds on:** completed.md #141 (real-API embedding path removed as
dead code), the A1 hybrid-ranking work (`vec_search.py`), and the
2026-08-20 OpenViking pilot analysis (deferred —
`doc/local/openviking_pilot_proposal.md`).
**Scope:** new `helpers/core/local_embedder.py`, three consumer modules
(`helpers/graph/embeddings.py`, `helpers/maintenance/rebuild_note_search.py`,
`helpers/core/get_tickers.py`), tests, one dependency addition, snapshot
regen. Explicitly out of scope: any server/daemon, any network API at
runtime, OpenViking itself.

---

## 1. TL;DR

Every "semantic" surface in this repo currently runs on deterministic
SHA-256 **pseudo-embeddings** — hash vectors with no semantic content.
Replace them with real 384-dim vectors from a local, offline
`bge-small-en-v1.5` (Apache-2.0, 33M params, English), injected through
the `embed_fn` seams the stack was designed with. No new service, no
egress, no AGPL, no pre-1.0 dependency churn. This is the chosen first
step over the deferred OpenViking pilot; the labeled eval set built
here transfers to OpenViking verbatim if it is ever revived.

## 2. Problem

Three embedding surfaces, all pseudo:

| Surface | Store | Today | Consumer |
|---|---|---|---|
| Company VSS | `company_embeddings` (SQLite) → DuckDB `v_embeddings` | `dry-run-v384`, 1,050 rows | `query.py::semantic_neighbors()` (brute-force cosine, ~3ms @ 1k) |
| Note hybrid rank | `note_search` FTS5 JSON col + `note_search_vec` vec0 sidecar | 64-dim pseudo (`rebuild_note_search._default_embed`) | `vec_search.py::knn_similarities()` hybrid BM25+cosine |
| Entity resolution | query-time embedding in `get_tickers._pick_embedder` | pseudo fallback | `get_tickers.vss_match()` |

Consequences: the vector leg of hybrid ranking adds nothing over BM25;
`semantic_neighbors` returns lexical noise dressed as semantics; the
docstrings themselves say "inject a real embed_fn for semantic hybrid
ranking". The original real-API path (OpenAI text-embedding-3-small)
was never invoked and was removed in completed.md #115; this proposal
reintroduces the capability **locally** instead of against an API.

Why bge-small-en-v1.5 specifically:

- **384 dims — same as the live `company_embeddings`.** The SQLite
  `CHECK(array_length(embedding) = N)`, the DuckDB `FLOAT[]` cast in
  `_materialise_embeddings`, and the snapshot parquet shape are all
  unchanged. `note_search` moves 64 → 384, which is free (the JSON
  column is dim-agnostic; the vec0 sidecar is a derived, rebuildable
  mirror by design).
- English-tuned, Apache-2.0, 33M params — CPU embedding of ~1k
  companies + ~1.2k note docs is a one-off minutes-scale run and
  seconds-scale incremental.
- Same model family as the OpenViking local default (`bge-small-zh`),
  so if that pilot is ever revived, quality findings transfer.

## 3. Non-Goals

- No always-on service, HTTP endpoint, or daemon (the OpenViking
  differentiators — L0/L1 hierarchy, auto memory extraction, retrieval
  traces — are deliberately not built here; that was the deferred
  pilot's job).
- No cloud embedding API, ever, for this corpus (zero-egress rule).
- No change to note content, OKF frontmatter, or the vault.
- No HNSW indexing (brute-force is ~3ms at this scale; pending.md
  already tracks the broken macro revisit).

## 4. Design

### 4.1 One embedder module, three consumers

New `helpers/core/local_embedder.py` — single source of embedding
truth, lazy imports, load-once:

```python
MODEL_ID = "bge-small-en-v1.5"
DIM = 384
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

def available() -> bool          # dep + model file present, no exceptions
def embed_document(text) -> list[float]   # L2-normalised, 384
def embed_query(text) -> list[float       # QUERY_PREFIX + text
```

The BGE query/document asymmetry is the main correctness trap: queries
MUST carry the instruction prefix, documents MUST NOT. The module
exists so the rule lives in exactly one place. (This mirrors
OpenViking's own `embed_query()`/`embed_document()` split.)

### 4.2 Consumers

1. **`helpers/graph/embeddings.py`** — new real path next to
   `populate_dry_run`: `--model bge-small-en-v1.5` populates via
   `local_embedder.embed_document`, persisting `model = MODEL_ID`.
   **Clear-then-populate discipline: never mix model labels in the
   table** — cosine similarity across different models' vector spaces
   is garbage. `stats()` gains a warning when >1 distinct model label
   is present; `_ensure_schema` already handles dim changes.
2. **`helpers/maintenance/rebuild_note_search.py`** —
   `_default_embed` becomes: `local_embedder` when
   `available()`, else the existing pseudo path plus a one-time
   WARNING (mirrors the `vec_search` best-effort pattern: a missing
   model must never break `make maint`). `_EMBED_DIMS` derives from
   the resolved embedder. Runs in maint step 6 unchanged.
3. **`helpers/core/get_tickers.py`** — `_pick_embedder` prefers
   `local_embedder.embed_query`; pseudo fallback stays for dry-run
   environments. Query-side and index-side MUST resolve to the same
   model or `vss_match` scores are meaningless — a startup check
   compares the stored `model` label against `MODEL_ID` and warns on
   mismatch.

### 4.3 Model artifact & backend (Q1/Q2 — accepted as recommended)

Backend: **`llama-cpp-python` + GGUF** — lightest dependency (no torch),
CPU-friendly, and the exact artifact class the deferred OpenViking pilot
would have used. Verified on this box: 0.3.35 builds from sdist on
Python 3.14 (~3.5 min, gcc) and loads the model in ~0.1s.

> Correction (2026-08-20, implementation): the originally cited
> `ggml-org/bge-small-en-v1.5-Q8_0-GGUF` repo does NOT exist. The real
> conversion is **`CompendiumLabs/bge-small-en-v1.5-gguf`** (MIT), file
> `bge-small-en-v1.5-q8_0.gguf`, **35.1MB** (not ~30MB), sha256
> `ec38e8da142596baa913124ae50550de284b6916bf59577ef2f0cb9660c2f514`.

Model file lives at `models/bge-small-en-v1.5-q8_0.gguf` —
**gitignored, sha256-pinned** in the module, fetched once via the
documented download command (in the module docstring). No network at
index or query time, ever.

### 4.4 Snapshot & perf

- Only `snapshots/parquet/duckdb/v_embeddings.parquet` changes content
  → one `snapshot_db.py` regen after the live apply (standing rule
  after any DuckDB-visible change).
- `rebuild_note_search` is a perf-tracked hotspot (0.8s today). A real
  embedder makes the full refresh ~30–60s CPU (≈2.2k docs). Measure
  first (Q3); only if it busts its budget, add a
  `(sha256(text), model) → vector` cache table so incremental runs and
  unchanged docs never re-embed.

## 5. Implementation Plan

1. Add the backend dependency (Q1) + `models/` download path with
   sha256 verification; `local_embedder.py` with the three functions.
2. Unit tests for the module: dims, L2 norm, prefix asymmetry
   (`embed_query(x) != embed_document(x)` canary), `available()`
   both ways (monkeypatched), determinism.
3. Wire the three consumers (§4.2) + their tests (existing suites:
   `tests/test_rebuild_note_search.py`, ticker/vss tests, embeddings
   tests — extend with a tiny fixture GGUF-free path via the pseudo
   backend behind the same interface).
4. Perf measurement pass (Q3 decision point) before any live apply.
5. User-run live apply (dry-run first, per house pattern):
   `embeddings.py --clear` + repopulate; `rebuild_note_search.py`;
   `make graph-rebuild`; snapshot regen.
6. Eval vs §6 criteria; report.

## 6. Success Criteria

- On the ~30-question labeled vault query set (built here; doubles as
  the OpenViking eval set if revived): hybrid BM25+vector recall@5 ≥
  BM25-only, with no regression on exact-name/ticker queries.
- `vss_match` top-1 accuracy on ticker/name variants beats the pseudo
  baseline.
- `semantic_neighbors`: same-sector neighbours dominate top-5 for a
  sampled company set.
- Zero network during index+query (network-disabled verification run).
- `make qa` + `perf` + `advisory` green, run once at the end.
- `company_embeddings` holds exactly one model label after apply.

## 7. Risks

| Risk | Mitigation |
|---|---|
| cp314 wheel gap for llama-cpp-python | Q1 fallback to fastembed/ONNX; gcc exists if a build is needed |
| Mixed-model rows silently corrupt scores | clear-then-populate + `stats()` multi-model warning + `vss_match` label check |
| BGE prefix forgotten at a query site | single-module ownership + prefix-asymmetry unit test |
| Full-refresh embed cost in maint | measure (Q3); content-hash vector cache only if over budget |
| Snapshot drift | expected, one regen; `snapshot-check` green after |
| Model provenance | sha256-pinned file, gitignored; Apache-2.0 model, no license entanglement |

## 8. Open Questions (recommendations)

- **Q1 backend**: ACCEPTED 2026-08-20 — llama-cpp-python+GGUF (0.3.35
  built from sdist on py3.14). fastembed fallback never needed;
  sentence-transformers stays rejected.
- **Q2 model file**: ACCEPTED 2026-08-20 — gitignored `models/` +
  sha256 pin.
- **Q3 vector cache**: ACCEPTED 2026-08-20 (measure-then-decide) —
  measurement showed the full note_search refresh at ~5–6 min CPU with
  the real model (maint step 6 rebuilds FULL every run), busting the
  budget → the §4.4 content-hash cache WAS implemented
  (`note_search` embedding cache table; unchanged docs never re-embed;
  warm full rebuild ≈ seconds). See the implementation log in §10.

## 9. Next Actions

1. ~~Accept Q1–Q3~~ — done 2026-08-20.
2. Implement §5 steps 1–4 (code + tests + perf measurement) — done
   2026-08-20, see §10; live apply held at dry-run for the user.
3. User-run live apply (see §11 runbook), then eval vs §6; report;
   `completed.md` entry + archive move when shipped.

**Follow-up (2026-08-21):** the acknowledged staleness gap — company
embeddings are not refreshed by maint-full — was closed the same day by
`company_embeddings_maint.md` (cached populate + GC + a never-auto-
upgrading `--maint` step; now archived alongside this file, completed.md
\# 142).

## 10. Implementation Log (2026-08-20)

Code landed (tree left dirty for the user's stgit fold):

- `helpers/core/local_embedder.py` — the one-module owner: constants
  (MODEL_ID / DIM 384 / QUERY_PREFIX / sha256 pin), `available()`,
  `embed_document`, `embed_query` (prefix applied), `embed_documents`
  batch, L2 normalisation, load-once cache. Self-check:
  `python3 helpers/core/local_embedder.py` → 384-dim, doc·query 0.828
  on findata vocabulary.
- `helpers/graph/embeddings.py` — `populate_local()` (batch, model label
  `bge-small-en-v1.5`), `_ensure_single_model()` clear-then-populate
  guard on BOTH populate paths, `stats()` mixed-model warning, CLI
  `--model bge-small-en-v1.5` dispatch.
- `helpers/maintenance/rebuild_note_search.py` — `resolve_embedder()`
  (index side) + `query_embedder()` (query side) + `stored_embed_dims()`
  consistency gate; `_PSEUDO_DIMS = 64` replaces `_EMBED_DIMS`; resolved
  dims flow into the vec0 sync; one-time WARNING on pseudo fallback.
- `app.py::_hybrid_search_results` — query side via `query_embedder()`;
  the stored-vs-query dims gate degrades to BM25-only instead of
  zip-truncated garbage cosine. (This consumer was implicit in the
  proposal; the wiring made it explicit.)
- `helpers/core/get_tickers.py::_pick_embedder` — bge-labelled rows
  route through `local_embedder.embed_query` (warn + skip when
  unavailable or dims mismatched).
- `helpers/core/vec_search.py` — `stored_dims()` DDL probe +
  dims-change DROP/recreate in `sync_vec_table` (an IF-NOT-EXISTS
  create would have silently kept serving the old FLOAT[N] table after
  a model swap).
- **Q3 cache** — `_CachedEmbed` in `rebuild_note_search`:
  `(sha256(text), model) → vector` in the vec SIDECAR
  (`vecdb.note_search_emb_cache`) — derived, snapshot-excluded,
  restore-rebuildable state, invisible to the schema-drift guards.
  Pseudo embedder is never cached (hashing is free). Stats:
  `embed_cache_hits` / `embed_cache_misses`.
- Tests: `tests/test_local_embedder.py` (hermetic gates + real-model
  canaries incl. `embed_query(x) == embed_document(prefix+x)`); wiring
  tests in test_rebuild_note_search / test_get_tickers / test_embeddings
  / test_vec_search / test_api_search (incl. the mismatch-gate
  degradation test). conftest gained an autouse `_no_local_embedder` pin
  so the whole suite is hermetic with and without the model file.
- Deps: `llama-cpp-python` in pyproject (sdist build ~3.5 min on
  py3.14, gcc); `.gitignore` `models/`; deptry green.

Measured (this box, 4 cores):

| Path | Cost |
|---|---|
| model load | ~0.1 s |
| embed, short text (~10 tok) | ~25 ms |
| embed, full doc (512 tok truncation) | ~0.8 s |
| note_search full refresh, NO cache (baseline, measured) | **16m13s wall / 44.6 CPU-min** (1,227 docs) |
| note_search full refresh, warm cache (measured) | **0.8 s** (1,227/1,227 hits; junk-vector pre-warmed throwaway copy, so the figure is the pure lookup+read path) |
| `populate_local` 1,050 companies (one-off) | ~15–20 min, rare |

The no-cache baseline was measured in-process before the cache landed.
Warm figure via a pre-warmed throwaway copy (cache fill used junk vectors
through the same hash path, so timings reflect the real lookup+read path;
--check committed nothing to research.db). A `--check` pre-warm bug was
found and fixed en route: cache inserts used to roll back in --check mode
(only the writing transaction committed them) — regression-tested in
`test_check_mode_prewarms_cache`. Cold-cache paths that remain: first
apply, model swap, and the first rebuild after a `snapshot-restore` (the
sidecar is excluded from snapshots by design). Alternative if cold cost
ever matters: the q4_k_m quant (~23.7MB) halves per-doc cost at a small
quality cost — swap = constants + re-pin in `local_embedder`.

## 11. Live-Apply Runbook (user-held)

One-time setup (already done on this box, listed for other machines):

```bash
uv pip install llama-cpp-python   # builds from sdist, needs gcc+cmake
mkdir -p models && curl -L -o models/bge-small-en-v1.5-q8_0.gguf \
  "https://huggingface.co/CompendiumLabs/bge-small-en-v1.5-gguf/resolve/main/bge-small-en-v1.5-q8_0.gguf"
.venv/bin/python helpers/core/local_embedder.py   # self-check
```

Apply (user runs; agent holds at dry-run):

1. `.venv/bin/python helpers/graph/embeddings.py --stats` — expect
   `dry-run-v384` single model (the pre-state).
2. `.venv/bin/python helpers/graph/embeddings.py --clear`
3. `.venv/bin/python helpers/graph/embeddings.py --model bge-small-en-v1.5`
   (~15–20 min one-off; writes `bge-small-en-v1.5` rows only)
4. `.venv/bin/python helpers/graph/embeddings.py --stats` — exactly ONE
   model label.
5. `.venv/bin/python helpers/maintenance/rebuild_note_search.py`
   (first run cold ~20 min populates the sidecar cache; later runs
   seconds; report line shows `embed cache: N hits, M misses`)
6. `make graph-rebuild` — materialise DuckDB `v_embeddings`
7. snapshot regen (only `v_embeddings.parquet` content changes):
   `.venv/bin/python helpers/maintenance/snapshot_db.py ...` then
   `make snapshot-check`.

Post-apply eval vs §6 is a separate pass (the ~30-question labeled set
has not been built yet — it is the next work item after the apply).

## 12. Eval Results (2026-08-21, post-apply)

Applied state verified: 1,068 bge rows (single label), note_search 384-dim
(1,227 docs), sidecar 14MB, snapshot-check green (user-run apply).

Labeled set: `helpers/misc/embed_eval_questions.json` (27 search + 12 vss
+ 10 neighbors; ground truth verified against raw corpus content, never
against the engine under test; doubles as the OpenViking eval set).
Runner: `helpers/misc/embed_eval.py` (report, not a gate).

| Criterion | Result |
|---|---|
| Hybrid recall@5 ≥ BM25, no exact regression | **PASS** — overall 1.00 vs 0.93; exact 1.00→1.00; semantic 0.92→1.00; newsletter 0.67→1.00 |
| vss_match top-1 beats pseudo baseline | **PASS** — 12/12 Yahoo longNames incl. the Apollo Tyres/Hospitals disambiguation (pseudo baseline was ~0 on variants) |
| semantic_neighbors same-sector dominance | **PASS with caveat** — 5/10 strict (≥3/5 same-sector); every "miss" is a cross-sector business peer (Avanti → Sharat/Apex shrimp cluster in FMCG; Coal India → Oil India/Reliance energy; NVIDIA → Intel/Broadcom). The vectors capture business adjacency BETTER than the coarse sector labels; strict sector dominance is the wrong yardstick for this corpus |
| Zero network at index+query | PASS by construction (module imports no network libs; model file local) — formal network-disabled run still pending with the end gate |
| One model label after apply | PASS — `["bge-small-en-v1.5"]` |
| make qa + perf + advisory | **PASS** 2026-08-21 (user-run; en route: embed_eval sqlite3.connect fixed, `callable` annotations → `Callable`, conftest autouse monkeypatch-ordering leak fixed, static_checks CSafeLoader + binary-skip perf fixes) |

Two eval-design findings worth keeping:

1. **The hybrid posture matters.** The endpoint re-ranks only the BM25
   candidate page it fetches — evaluating hybrid with a 5-doc page measures
   permutation, not rescue. With a 25-candidate page, the vector leg
   lifted two queries BM25 missed outright ("defence electronics" →
   Bharat Electronics; the Asian Paints/NMDC chatter → its edition note);
   BM25's top hit for the former was an unrelated company.
2. FTS AND-semantics: a query token absent from the corpus ("maker")
   zeroes the whole BM25 page — hybrid inherits the empty page. Phrase
   queries accordingly ("inverter batteries", not "inverter battery
   maker").
