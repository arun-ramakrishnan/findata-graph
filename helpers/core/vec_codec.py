#!/usr/bin/env python3
"""Binary f32 codec for stored embeddings (embedding_blob_migration S1).

Every embedding surface (embed_cache, note_search, company_embeddings,
doc_search, script_search) stored vectors as JSON TEXT (~8.3 KB per
384-d vector). This module is the single choke point for the f32 BLOB
codec (~1.5 KB per vector) and the tolerant reader that accepts either
form during migration.

Precision note (measured 2026-09-10): the stored JSON carries f64
precision; packing rounds to nearest f32 — max |dcos| 8.4e-9, 0/59
top-5 neighbor flips, and every retrieval surface (sqlite-vec chunks,
DuckDB FLOAT[]) already quantizes to f32.

Rejected riders, measured on this box (Skylake: AVX2+F16C, no
AVX512-FP16): f16 — 3.6x CPU penalty in numpy compute for ~12 MB;
int8 — 18/59 top-5 flips naive. Both parked behind revisit triggers in
doc/improvements/proposals/embedding_blob_migration.md §6.
"""

from __future__ import annotations

import array
import json

_DIMS_PER_F32 = 1  # one f32 = 4 bytes = one dimension


def pack_f32(vec: list[float] | tuple[float, ...]) -> bytes:
    """Pack an embedding into little-endian f32 bytes (1,536 B for 384-d)."""
    return array.array("f", vec).tobytes()


def unpack_f32(blob: bytes) -> list[float]:
    """Unpack little-endian f32 bytes into a list of Python floats."""
    a = array.array("f")
    a.frombytes(bytes(blob))
    return a.tolist()


def dims_from_blob(blob: bytes) -> int:
    """Vector dimensionality implied by a packed blob (bytes // 4)."""
    return len(blob) // 4


def load_vec(value: object) -> list[float] | None:
    """Tolerant reader: BLOB -> unpack_f32, TEXT -> json.loads, None/'' -> None.

    Lets data and code land in either order during the migration, and
    keeps hermetic fixtures that seed TEXT rows working."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return unpack_f32(bytes(value))
    if isinstance(value, str):
        if not value:
            return None
        try:
            vec = json.loads(value)
        except TypeError, ValueError:
            return None
        return vec if isinstance(vec, list) else None
    return None
