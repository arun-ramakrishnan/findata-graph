---
title: "Production integration of cosine_knn Rust SIMD backend into query.py::similar_notes and app.py::_hybrid_search_results"
status: deferred
filed: "2026-10-28"
executed: "2026-10-28"
completed_md: "450"
area: graph
---

# Production integration of cosine_knn Rust SIMD backend

**Date:** 2026-10-28 · **Status:** DEFERRED 2026-10-28 (completed.md #450)

## Summary

The `cosine_knn` Rust crate (abi3 module, f32×4 SIMD, rayon parallel ≥5k rows) is built, tested, and wired into `rebuild_script_search.py::_cosine_leg` via the `SCRIPT_KNN` selector (`auto`→`rust`→`numpy`→`pure`). Perf gate passes: **2.72× numpy, 43× pure** at live script-search shape (717×512).

**This proposal covers the two remaining hotspots** that still use slower paths:

| Hotspot | Current Path | Speed | Target Speedup |

|---------|--------------|-------|----------------|
| `query.py::similar_notes` | DuckDB `array_distance` (SQL) | ~baseline | **5–10×** via Rust |

| `app.py::_hybrid_search_results` | `EmbedMatrixStore.top_k()` (numpy matvec) or Python fallback | ~350 q/s | **2–3×** via Rust |

---

## Deferral (2026-10-28)

Deferred after prior-evaluation review and a fresh live-shape bench: both projected speedups conflict with recorded evidence.

**Prior evaluation (already settled):**

- `doc/improvements/archive/graph/rust_cosine_knn_port.md` — this exact integration was S3 of the port arc (lines 43–45); the 2026-10-25 session deliberately narrowed the port target to `_cosine_leg` only: *"similar_notes is DuckDB SQL and app.py's hybrid is sqlite-vec with the Python loop as cold fallback, so neither is a port target"* (lines 72–75). Its amended order of work (lines 128–131): numpy-vectorize the analogs first, re-baseline, then Rust only if a ~5x hot spot remains.
- `doc/improvements/archive/graph/note_knn_distance_ranking.md` — faster note-KNN was tried and settled 2026-09-22: the HNSW/ANN path declined for broken scan recall (lines 81–85); the true brute `array_distance` baseline recorded there (lines 93–95) is the number any `similar_notes` rewrite must beat.

**Fresh bench (2026-10-28, live db, warm medians)** — `v_note_embeddings` = 17,550 x 512:

| leg | measured | vs claim |
|---|---|---|
| `similar_notes(k=10)` — current DuckDB SQL | 1.70 ms | claimed 5–10x headroom; there is none |
| fetch + decode all section vectors to Python (per-call cost the plan adds) | 1717.8 ms | ~1000x regression, not a win |
| `em.top_k(q, N)` numpy full ranking, N=17,550 | 11.34 ms (88 q/s) | proposal assumed ~350 q/s (the 705-row script shape) |
| `cosine_knn.knn` rust, k=N (the proposal's call) | 8.25 ms (121 q/s) | 1.37x numpy, not the claimed 2–3x |
| `cosine_knn.knn` rust, k=10 | 3.64 ms | only wins if the full-ranking contract changes |

Bench method: read-only live connection via `helpers/graph/query.py::connect_read_only`; ref note `findata/Companies/Agriculture/EID_Parry.md`; warm medians (SQL leg 10 reps, matrix legs 20 reps); `em.top_k` over `EmbedMatrixStore().load()` and `cosine_knn.knn` over `np.ascontiguousarray(em.matrix)`, query = a unit-norm matrix row. Note the 2026-09-22 recorded brute baseline (26–27 ms at 16,560×384, `note_knn_distance_ranking.md` lines 93–95) does not match today's 1.70 ms warm median — cold trial vs warm cache, different measurement context; 1.70 ms is what the serving path actually sees.

**Reading:** the `similar_notes` leg is dead as scoped — fetching and decoding every section vector into Python costs ~1.7 s per call, three orders of magnitude over the 1.70 ms in-DuckDB pass it would replace. Rust can only win there behind a materialized note-embedding matrix (the `EmbedMatrixStore` pattern), a far bigger change than scoped. The `_flat_knn_map` leg is genuinely live (N=17,550 exceeds vec0's 14.5k cap, `app.py:955`), but the honest like-for-like win at the real shape is 1.37x (~3 ms/query) — thin for adding a box-local abi3 artifact dependency to the serving path. Recorded evidence also says numpy's BLAS matvec *leads* Rust at >=5k rows on some shapes (`rust_cosine_knn_port.md` line 96: 3,100 vs 367 q/s at 5k x 384), so the ratio is shape-fragile.

**Re-open when:** (a) a numpy argpartition top-k variant is benched at N=17,550 and the surviving gap justifies an abi3 dependency on the serving path; or (b) a materialized note-embedding matrix lands, making the Rust pass the dominant remaining cost in `similar_notes`.

---

## Background

- **S1 (executed 2026-10-25)**: `Rust/cosine_knn` crate + PyO3 abi3 module (`cosine_knn.knn(query, matrix, k)`). Doc: `doc/improvements/archive/graph/rust_cosine_knn_port.md`
- **S2 (executed 2026-10-27)**: Rayon parallel + divan benchmarks. Doc: `doc/improvements/archive/tooling/rust_rayon_parallel_cosine_knn.md`
- **Wired (live)**: `rebuild_script_search.py::_cosine_rank_rows` selector + `tests/test_cosine_knn_backends.py` (9 parity tests)
- **Perf gate**: `Rust/bench/cosine_gate.py` in `make perf` (`cosine_knn_ratios` leg)

---

## Integration Plan

### 1. Shared cosine KNN helper module

Create `helpers/graph/cosine_knn.py` as a thin adapter over the three backends:

```python
# helpers/graph/cosine_knn.py
from __future__ import annotations
import os
import numpy as np
from typing import List, Tuple

# Rust abi3 (optional)
try:
    import cosine_knn

    HAVE_RUST = True
except ImportError:
    HAVE_RUST = False


def cosine_knn_topk(
    query: List[float],
    matrix: np.ndarray,  # [N, dim] float32, contiguous
    k: int,
) -> List[Tuple[int, float]]:
    """
    Returns [(row_index, similarity)] top-k descending.
    Selector: SCRIPT_KNN=auto|rust|numpy|pure (mirrors rebuild_script_search._knn_backend)
    """
    backend = os.environ.get("SCRIPT_KNN", "auto")

    if backend in ("auto", "rust") and HAVE_RUST:
        try:
            # cosine_knn.knn expects (query, matrix, k) -> [(idx, score)]
            return cosine_knn.knn(query, matrix, k)
        except Exception:
            if backend == "rust":
                raise  # explicit rust request -> hard fail
            # auto -> fall through

    # numpy path
    if backend in ("auto", "numpy"):
        q = np.asarray(query, dtype=np.float32)
        q = q / (np.linalg.norm(q) + 1e-8)
        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0] = 1e-8
        sims = matrix @ q / norms
        idx = np.argpartition(-sims, k - 1)[:k]
        idx = idx[np.argsort(-sims[idx])]
        return [(int(i), float(sims[i])) for i in idx]

    # pure python fallback
    q = np.asarray(query, dtype=np.float32)
    q_norm = float(np.linalg.norm(q))
    if q_norm == 0:
        return [(i, 0.0) for i in range(min(k, len(matrix)))]
    scored = []
    for i, row in enumerate(matrix):
        r_norm = float(np.linalg.norm(row))
        if r_norm == 0:
            sim = 0.0
        else:
            sim = float(np.dot(q, row) / (q_norm * r_norm))
        scored.append((i, sim))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]
```

### 2. `query.py::similar_notes` — replace DuckDB `array_distance`

**Current (lines 4221–4238)**: SQL `array_distance(CAST(emb AS FLOAT[dim]), CAST(? AS FLOAT[dim]))` with `GROUP BY file_path + MIN(dist)` for section-level best-match.

**Problem**: DuckDB's `array_distance` is L2 on unit-norm vectors (ordering = cosine). But it runs per-row in SQL, no SIMD, and the `GROUP BY` adds overhead.

**Proposed**: Fetch candidate section vectors once, run Rust cosine KNN in Python, then `GROUP BY file_path` in Python (cheap, N≤~20k sections).

```python
# In similar_notes()
from helpers.graph.cosine_knn import cosine_knn_topk

# 1. Build section matrix: rows are sections, need file_path + title per row
rows = con.execute(
    f"""
    SELECT file_path, title, emb
    FROM v_note_embeddings
    WHERE file_path != ?{type_clause}
    """,
    params,
).fetchall()

if not rows:
    return []

# 2. Decode embeddings to contiguous float32 matrix
section_vecs = []
section_meta = []  # [(file_path, title)]
for fp, title, emb in rows:
    vec = load_vec(emb)
    if vec and len(vec) == dim:
        section_vecs.append(vec)
        section_meta.append((fp, title))

if not section_vecs:
    return []

matrix = np.ascontiguousarray(section_vecs, dtype=np.float32)  # [N, dim]

# 3. Rust cosine KNN over ALL sections (global rank)
top = cosine_knn_topk(ref_vec, matrix, k=len(matrix))  # full ranking

# 4. Collapse to note-level (best section per file_path)
note_best = {}
for idx, sim in top:
    fp, title = section_meta[idx]
    if fp not in note_best or sim > note_best[fp][1]:
        note_best[fp] = (title, sim)

# 5. Sort by sim desc, take top-k
results = [
    (fp, title, sim)
    for fp, (title, sim) in sorted(note_best.items(), key=lambda x: x[1][1], reverse=True)[:k]
]
return results
```

**Key semantic preservation**:
- Same `GROUP BY file_path + MIN(dist)` → best-section-per-note collapse
- Same cosine ordering (L2 on unit-norm = cosine)
- Global section ranking before collapse (matches current DuckDB behavior)

### 3. `app.py::_hybrid_search_results` — replace `EmbedMatrixStore.top_k()`

**Current (lines 704–724, 954–972)**: `_flat_knn_map` loads `EmbedMatrixStore` (numpy `.npy` matrix), calls `top_k()` (numpy matvec `matrix @ query`), falls back to per-row Python cosine.

**Problem**: The numpy path is fast but still has per-call overhead (matrix load, norm, matvec). Rust's fused kernel avoids Python/Numpy dispatch.

**Proposed**: Use `cosine_knn_topk` directly on the loaded matrix.

```python
# In _hybrid_search_results()
from helpers.graph.cosine_knn import cosine_knn_topk

# Replace _flat_knn_map call:
if knn is None:
    # Try matrix path first
    try:
        from helpers.core.embed_matrix import EmbedMatrixStore

        em = EmbedMatrixStore().load()
        id_set = set(em.ids)
        if all(p in id_set for p in page_paths):
            # Use Rust KNN on the full matrix
            top = cosine_knn_topk(q_vec, em.matrix, k=len(em.ids))
            knn = {em.ids[idx]: sim for idx, sim in top}
    except Exception:
        pass

    if knn is None:
        knn = _flat_knn_map(q_vec, [_vec_row_key(r) for r in rows])
```

The `EmbedMatrixStore` already holds `self.matrix` as contiguous `float32` — zero-copy to Rust.

---

## Testing

1. **Parity tests**: Extend `tests/test_cosine_knn_backends.py` pattern:
   - `test_similar_notes_backend_parity` — same note rankings across backends
   - `test_hybrid_search_backend_parity` — same fused rankings across backends

2. **Benchmark**: Add `make bench-graph` legs:
   - `similar_notes` latency (single note, k=10)
   - `_hybrid_search_results` latency (typical query page)
   - Compare `SCRIPT_KNN=rust|numpy|pure`

3. **Perf gate**: Add assertions to `Rust/bench/cosine_gate.py` or new `bench-graph` gate:
   - `similar_notes`: rust ≥ 5× pure at 1K notes
   - `hybrid`: rust ≥ 2× numpy at production page size

---

## Compatibility & Rollback

- **Zero breaking changes**: All paths retain explicit `SCRIPT_KNN` override + silent degrade
- **No new deps**: `cosine_knn` abi3 already optional; `numpy` already required
- **Rollback**: `SCRIPT_KNN=pure` disables both fast paths instantly

---

## Effort Estimate

| Task | Estimate |
|------|----------|
| Create `helpers/graph/cosine_knn.py` adapter | 0.5 day |
| Integrate `similar_notes` + tests | 1 day |
| Integrate `_hybrid_search_results` + tests | 1 day |
| Benchmarks + perf gate | 0.5 day |
| **Total** | **~3 days** |

---

## References

- Trial results: `doc/local/engineering/rust_database.md` §11.2
- Existing wiring: `helpers/maintenance/rebuild_script_search.py::_cosine_rank_rows`
- Parity tests: `tests/test_cosine_knn_backends.py`
- Perf gate: `Rust/bench/cosine_gate.py` (runs in `make perf`)
- Rust crate: `Rust/cosine_knn/` (PyO3 abi3 at `target/release/libcosine_knn.so`)
- Archive proposals: `doc/improvements/archive/graph/rust_cosine_knn_port.md`, `doc/improvements/archive/tooling/rust_rayon_parallel_cosine_knn.md`
- Prior verdicts: `doc/improvements/archive/graph/note_knn_distance_ranking.md` (HNSW declined, l2-ranking swap executed)
