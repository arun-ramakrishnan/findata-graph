#!/usr/bin/env python3
"""bulk_data_lanes S6 — fts_duckdb_parity vector leg repair tests.

The bench was created outside the house harness and missed the #223
f32-BLOB migration: its raw ``json.loads`` reader silently skipped 100%
of the live corpus (UnicodeDecodeError is a ValueError subclass) and
``main()`` crashed on the empty matrix. These tests pin the repaired
contract: BLOB corpus decodes to a non-empty f32 matrix; an empty leg
fails LOUD, never reaching numpy.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from helpers.bench.fts_duckdb_parity import build_vector_leg
from helpers.core.vec_codec import pack_f32

_REPO = Path(__file__).resolve().parents[1]


def _corpus(n: int, dims: int = 16) -> list[dict]:

    rng = np.random.default_rng(0)
    emb = rng.standard_normal((n, dims)).astype(np.float32)
    return [
        {
            "rowid": i,
            "doc_type": "company",
            "file_path": f"findata/Note_{i:03d}.md",
            "title": "",
            "sector": "",
            "content": "",
            "section_title": "",
            "anchor": None,
            "embedding": pack_f32(emb[i].tolist()),
        }
        for i in range(n)
    ]


def test_vector_leg_decodes_blob_corpus():
    corpus = _corpus(9)
    ids, m, norms = build_vector_leg(corpus)
    assert len(ids) == 9
    assert m.shape == (9, 16) and m.dtype == np.float32  # f32 parity leg
    assert norms.shape == (9,)


def test_vector_leg_empty_corpus_fails_loud():
    with pytest.raises(RuntimeError, match="0 embedded sections"):
        build_vector_leg([])
    # embedding-bearing rows that fail to decode must also be loud,
    # not a silently empty matrix handed to numpy.
    with pytest.raises(RuntimeError, match="embedding storage changed"):
        build_vector_leg([dict(_corpus(1)[0], embedding=b"\x01\x02")])


def test_vector_leg_tolerates_legacy_text():

    corpus = _corpus(2)
    corpus[0]["embedding"] = pack_f32(np.ones(16, dtype=np.float32).tolist())
    corpus[1]["embedding"] = json.dumps((2 * np.ones(16, dtype=np.float32)).tolist())
    ids, m, _ = build_vector_leg(corpus)
    assert len(ids) == 2 and m.shape == (2, 16)
