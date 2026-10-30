---
title: "msgpack + msgspec Struct adoption — typed serialization + internal transport"
status: executed
filed: '2026-10-30'
area: helpers/core
completed_md: '455'
executed: '2026-10-30'
---

# msgpack + msgspec Struct adoption — typed serialization + internal transport

## 1. TL;DR

The repo already uses **orjson** (3.13.0) repo-wide via `helpers/core/jsonio.py` for JSON I/O (1.55–2.7× stdlib). Two low-effort wins remain:

| Target | Mechanism | Measured gain | Effort |
|--------|-----------|---------------|--------|
| **Internal serialization** (cache, IPC, embeddings) | **msgspec.msgpack** | **2.6× faster** than orjson JSON, **~30% smaller** payloads | Add `msgspec.encode/decode` shim alongside `jsonio` |
| **Hot typed paths** (dataclass-heavy pipelines) | **msgspec.Struct** | **7.2× faster encode** vs orjson (which uses `default=str`) | Migrate selected `@dataclass(frozen=True)` → `msgspec.Struct` |

Both tools are **already installed** (`msgspec 0.22.0` in `.venv`). No new dependencies.

## 1.1 Execution status (take-over audit, 2026-10-30)

Slices S1 + the S3 pilot shipped in commit fe1d2351f; take-over audit the same
day caught and fixed weak-model defects. Status per slice:

| Slice | State | Evidence |
|-------|-------|----------|
| S1 `msgpackio.py` shim | SHIPPED | 3 parity tests (`tests/test_msgpackio.py`) green |
| S2 Struct migrations | Edge/Unresolved only | 261 relation tests green; remaining candidates audited EXCLUDED (see §3.2 table) |
| S3 embed_cache IPC pilot | SHIPPED | Rust `embed-cache-lookup` writes rmp-serde msgpack (`with_struct_map`); Python decodes msgpack-first, JSON fallback; `text=False` byte read. Verified live end-to-end on a throwaway embed store |
| S3 promotion bench | **PROMOTED** | Production wire (real binary, 256-text request, 128 hits x 384-d f32): size ratio **0.24** (248,362 B msgpack vs 1,023,603 B JSON; criterion <= 0.70), decode time ratio **0.09** (1.58 ms vs 16.87 ms; criterion <= 0.40), full subprocess round-trip 33.4 ms. Both acceptance criteria pass decisively. The Rust side emits f32 floats — the size win over JSON is f32-vs-decimal-repr, so a Python-side f64 synthetic bench understates it (0.86); only wire-true numbers count |
| Rollout step 3 — cosine_knn | N/A | `cosine_knn` is an IN-PROCESS PyO3 abi3 module (`.venv/.../cosine_knn.abi3.so`, called directly at `helpers/maintenance/rebuild_script_search.py:1672`) — zero-copy native boundary, NO serialization edge to convert |
| Rollout step 3 — queues | N/A | The only queued artifact is the `Unresolved` sidecar JSONL (`helpers/graph/extract_relations.py:3095`) — a human-review artifact read by triage tooling; it MUST stay JSON. derive_insights worker IPC is pickle-of-dicts (not a wire format to replace) |
| Rollout step 4 — bulk-serialize survey | DONE, no site | Surveyed remaining bulk-serialize sites: derive_insights worker IPC (pickle, ~651 quotes + 128 metrics/run — below hot bar), trace_contracts (plain dict rows, config only), enrich_relations reports (per-target rows). No site qualifies for msgpack; reopen when a bulk-serialize site exceeds the hot bar (repeated encode/decode in a profiled hot loop) |

Defects fixed in the take-over audit (all introduced by the initial
implementation commit): `embed_cache.py` was missing the `msgpackio` import —
at HEAD the Rust accelerator path raised `NameError` on first use, and the
broad `except` silently disabled the whole accelerator including the JSON
fallback; `msgpackio.py` lacked the repo-required shebang (static-checks red);
the proposal's S2 table named classes that do not exist in the tree;
"msgspec.Struct is a dataclass subclass" was false (asdict must come from
`msgspec.structs`); filed date read 2026-10-31 (future); duplicated `## 8`
section numbering; 8 md-lint findings.

---

## 2. Motivation

### 2.1 Current state
- `helpers/core/jsonio.py` wraps `orjson` with `OPT_SORT_KEYS | OPT_INDENT_2`, stdlib fallback for other indent values
- 135 `orjson` references across codebase — mass cutover completed (S0/S1)
- Many hot paths serialize **frozen dataclasses** or large dict lists via `default=str` → forces per-object `__str__` dispatch, loses type info, slower

### 2.2 Benchmarks (local, representative payloads)

| Operation | orjson | msgspec.json | msgspec.msgpack | msgspec.Struct (encode) |
|-----------|--------|--------------|-----------------|-------------------------|
| **JSON encode (dict list, 50k rows)** | 1.0× (baseline) | 0.46× (2.2× slower) | — | — |
| **JSON decode (dict list)** | 1.0× | 0.81× (1.23× slower) | — | — |
| **encode (dataclass→JSON via `default=str`)** | 1.0× | — | — | **7.2× faster** |
| **msgpack encode (dict list)** | — | — | **0.38× orjson (2.6× faster)** | 0.37× orjson |
| **msgpack decode** | — | — | ~1.0× orjson JSON | — |
| **Output size (JSON)** | 86,689 B | 86,689 B | — | 86,689 B |
| **Output size (msgpack)** | — | — | **61,234 B (29% smaller)** | 61,234 B |

**Key insight**: `msgspec.Struct` avoids `default=str` entirely — it knows the schema at compile time. For **typed data** (the majority of our pipeline rows), it crushes orjson.

---

## 3. Design

### 3.1 Slice S1 — `helpers/core/msgpackio.py` (new shim)

```python
"""MessagePack I/O using msgspec — drop-in for internal transport.

PyArrow is used for columnar/analytical workloads (parquet snapshots,
hypergraph incidence tables, embedding matrices). msgpack serves a
different niche: **row-oriented, schema-less, fast internal transport**
for caches, IPC, and pipeline intermediates where Parquet's columnar
overhead doesn't pay off.
"""

import msgspec
from typing import Any

# Single encoder/decoder instances (stateless, thread-safe)
_ENCODER = msgspec.msgpack.Encoder()
_DECODER = msgspec.msgpack.Decoder()


def encode(obj: Any) -> bytes:
    return _ENCODER.encode(obj)


def decode(data: bytes, type: type = None) -> Any:
    return _DECODER.decode(data, type=type)


# Convenience for pipelines that already have Struct types
def encode_struct(obj: Any) -> bytes:
    return _ENCODER.encode(obj)


def decode_struct(data: bytes, cls: type) -> Any:
    return _DECODER.decode(data, type=cls)


# Optional: pyarrow Table <-> msgpack (requires to_pylist round-trip)
# Use when you need msgpack transport of Arrow data:
#   data = msgpackio.encode(tbl.to_pylist())  # serialize
#   tbl = pa.Table.from_pylist(msgpackio.decode(data))  # deserialize
# For pure Arrow workflows, prefer pyarrow.ipc (feather) — 10× faster,
# zero-copy, preserves schema exactly.
```

### 3.2 Slice S2 — Migration guide for hot typed paths

Target files with `@dataclass` classes, audited 2026-10-30 against the live
tree (earlier revisions of this table named classes that do not exist):

| File | Actual dataclasses | Bulk-serialized? | Action |
|------|-------------------|------------------|--------|
| `helpers/graph/extract_relations.py` | `Edge`, `Unresolved` | `Unresolved` sidecar (`asdict` → JSONL per run) | **MIGRATED** (commit fe1d2351f) |
| `helpers/analytics/trace_contracts.py` | `ColumnSpec` only — rows are plain `dict`s | No (config objects, ~dozens) | EXCLUDED — no serialized row type |
| `helpers/core/cin.py` | `CinParse` | No (parse results consumed in-process by 4 callers) | EXCLUDED — zero serialization win |
| `helpers/graph/derive_insights.py` | `Quote`, `Metric`, `CompanySection` | Worker IPC is pickle of `asdict` dicts; per-row `properties` JSON only; ~651 quotes + 128 metrics/run | EXCLUDED — below the hot bar |
| `helpers/maintenance/enrich_relations.py` | `GfOutcome`, `FhOutcome` | Per-target report rows, no bulk encode | EXCLUDED — below the hot bar |

Migration pattern (zero-runtime-risk):

```python
# BEFORE (extract_relations.py:577 + 603)
@dataclass
class Edge:
    source: str
    target: str
    edge_type: str
    properties: dict
    source_ref: str
    symmetric: bool
    valid_from: str | None


@dataclass
class Unresolved:
    edge_type: str
    source: str
    target_mention: str


# AFTER (drop-in; defaulted fields stay last, as with dataclasses)
import msgspec


class Edge(msgspec.Struct, frozen=False):
    source: str
    target: str
    edge_type: str
    properties: dict = msgspec.field(default_factory=dict)
    source_ref: str = ""
    symmetric: bool = False
    valid_from: str | None = None


class Unresolved(msgspec.Struct, frozen=False):
    edge_type: str
    source: str
    target_mention: str


# Usage — note msgspec.Struct is NOT a dataclass subclass; attribute access,
# construction, copy and pickle all behave the same, but use
# msgspec.structs.asdict (not dataclasses.asdict) for dict conversion.
# jsonio.dumps(edges) still works (uses default=str)
# msgpackio.encode_struct(edges) uses the native Struct codec
```

### 3.3 Slice S3 — Adoption gates
- **Internal cache/IPC**: switch `embed_cache`, `cosine_knn` results, inter-process queues to `msgpackio`
- **JSON APIs stay on `jsonio`** — external contracts unchanged
- **No `default=str` in new code** — use `msgspec.Struct` or `msgspec.convert()`

---

## 4. Acceptance criteria

| Surface | Requirement | Verification |
|---------|-------------|--------------|
| `msgpackio.py` | Encode/decode round-trip parity with orjson for dict/list/primitives | `test_msgpackio.py` golden compare |
| Migration (S2) | Selected dataclasses → Struct, **zero behavioural change** | Existing tests pass; `make perf` on affected benches |
| `embed_cache` (if adopted) | MsgPack serialization ≤ 40% of JSON time, ≤ 70% size | `bench_embed_cache.py` before/after |
| Perf gate | No regression on `make perf` | 22/22 pass |

---

## 5. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Struct field order must match dataclass | Auto-generate from existing dataclass via script; verify `__dataclass_fields__` order |
| msgspec doesn't support `default=str` for unknown types | Only migrate **known-schema** types; keep `jsonio` for heterogeneous dicts |
| MsgPack not human-readable | Internal-only; JSON remains for APIs/logs/debug |
| Version skew (msgspec 0.22 → future) | Pin in `pyproject.toml`; test matrix on upgrade |
| pyarrow Table <-> msgpack requires `to_pylist()` | Use pyarrow IPC (feather) for Arrow-native workflows; msgpack only for schema-less transport |

---

## 6. pyarrow vs msgpack — when to use which

| Scenario | Recommended | Why |
|----------|-------------|-----|
| **Columnar analytics / snapshots / embeddings** | pyarrow Parquet / IPC | Zero-copy, predicate pushdown, compression, schema evolution |
| **Hypergraph incidence / graph algorithms** | pyarrow Table (in-memory) + Parquet (disk) | Already standardized; joins with DuckDB |
| **Embedding cache (embed_cache.py)** | SQLite BLOB (current) + optional msgpack for IPC | Row-oriented KV; 384-d f32 = 1.5KB rows; Parquet overhead > payload |
| **Internal pipeline intermediates (IPC, queues, temp cache)** | **msgpack** | Row-oriented, schema-less, 2.6× faster than JSON, 30% smaller |
| **Rust ↔ Python CLI (embed-cache-lookup)** | JSON (current) or msgpack | msgpack would be 2× faster but JSON is human-readable for debug |
| **External APIs / logs / human-readable** | JSON (orjson via jsonio) | Standard, readable, stable |

**embed_cache** specifics:
- Current: Python `embed_cache.py` stores f32 BLOB in SQLite, uses JSON for Rust CLI stdio
- Rust `embed-cache-lookup` reads/writes JSON over stdio
- **msgpack opportunity**: replace JSON stdio with msgpack for 2× throughput; or use pyarrow IPC for zero-copy (but adds Arrow dependency to Rust side)

---

## 7. Non-goals

- Replace `orjson` for external JSON APIs
- Rewrite all dataclasses (only hot bulk-serialization paths)
- Add new dependencies (msgspec already present)

---

## 8. Standard framework — msgpack/msgspec + parquet(zstd) + pyarrow

The repo framework: parquet(zstd) for disk (pyarrow) + pyarrow in-flight. msgspec.msgpack complements — not replaces — serving internal transport (row-typed binary, faster/smaller than JSON).

### 3-step adoption (integrates with framework)
- Step 1 (Validation): msgspec.Struct for typed decode; wire stays JSON. Same validation, faster, same wire.
- Step 2 (Transport): `Content-Type: application/msgpack` for hot endpoints (embed_cache IPC, cosine_knn, queues). Handler logic unchanged; only encode/decode changes. JSON stays for debug.
- Step 3 (Schema): msgspec.Struct = typed schema standard. Same Struct works for JSON/msgpack/YAML/TOML. Migrate frozen dataclass paths (Relation, TraceRow, CINRecord, Insight); keep jsonio for heterogeneous dicts.

### Integration

```text
External/APIs → JSON wire (orjson/jsonio) — unchanged; validation via msgspec.Struct (Step 1)
Internal/IPC    → msgpack (Step 2) — msgpackio encode/decode; target: embed_cache (PROMOTED, §1.1; cosine_knn is in-process PyO3 — no wire to convert)
Pipeline typed → msgspec.Struct (Step 3) — schema for JSON/msgpack/YAML; migrated hot paths
Disk            → parquet(zstd) + pyarrow — unchanged (msgpack not columnar)
In-flight      → pyarrow Arrow/Arrow IPC — unchanged (msgpack complements small payloads)
```

---

## 9. Rollout sequence
1. **S1** — `msgpackio.py` + tests; Step 1 pilot (`msgspec.Struct` decode on one endpoint) — DONE
2. **S2** — Pilot `msgpack` internal endpoint (`embed_cache` Rust↔Python IPC); migrate 1-2 frozen dataclasses — DONE (Edge/Unresolved; other candidates audited EXCLUDED, §3.2)
3. **S3** — Promote `embed_cache` msgpack if bench passes; extend Step 2 to other hot endpoints; Step 3 schema standard adopted for new typed data — DONE (bench passed 0.24 size / 0.09 time, §1.1; cosine_knn + queues N/A with evidence; msgspec.Struct adopted as the schema standard for NEW typed data, §8)
4. **S4** — Extend to other bulk-serialize sites per `make perf` profiling — DONE (surveyed; no qualifying site; reopen trigger in §1.1)

---

## Appendix — Quick reference

```python
# For NEW typed data: use msgspec.Struct
class MyRow(msgspec.Struct, frozen=True):
    id: int
    name: str
    score: float


# Fast encode (no default=str dispatch)
data = msgpackio.encode_struct(rows)  # 7× faster than jsonio.dumps

# Fast decode with type hint (validates + constructs)
rows = msgpackio.decode_struct(data, list[MyRow])

# For heterogeneous dicts: keep jsonio
jsonio.dumps(mixed_dict_list)
```
