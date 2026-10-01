#!/usr/bin/env python3
"""Deterministic v_note_embeddings fixture for the near-duplicate split.

c901_d1_split_near_duplicate_notes S1. `near_duplicate_notes` reads exactly
one table — `SELECT file_path, title, emb FROM v_note_embeddings WHERE
doc_type = ?` (query.py:3706) — so an in-memory DuckDB carrying just that
table exercises the whole function with no SQLite/materialisation plumbing
(unlike print_stats, whose fixture needs the live-DB injection point).

The seed set is built so each coverage gap has a dedicated witness:

  - TIE LATTICE — a, b, c, d are chosen so that a and c normalize to the
    same unit vector ([0.6, 0.8, 0, 0] — f64(3/5) == f64(6/10), IEEE
    division is correctly rounded), as do b and d. Sims (a,c) and (b,d) are
    bit-identical 1.0, and (a,b), (a,d), (b,c), (c,d) are bit-identical
    0.96 — four pairs tie at one similarity and two at another, so the
    `(-sim, path_a, path_b)` sort key and the canonical string orientation
    are both load-bearing. The insert order (a, b, d, c) deliberately
    reverses index order vs string order for the (c, d) pair, so the
    emitted orientation can only be right if the swap ran.
  - MULTI-SECTION — e_multi appears as two rows with different vectors and
    different titles; the np.add.at + bincount mean and the first-seen
    title rule are both observable in the output.
  - ZERO-NORM — g_zero is the all-zero vector; the norms[norms == 0] guard
    is what keeps its similarity rows at 0.0 instead of NaN (NaN pairs are
    dropped by the `>= min_sim` mask, so dropping the guard silently
    removes every g_zero pair at min_sim=0.0 — parity-visible).
  - BOTH DOC TYPES — a newsletter pair that must never leak into a company
    query or vice versa.
"""

from __future__ import annotations

import duckdb

# (file_path, doc_type, title, emb) — insertion order is the first-seen
# order the collapse depends on; do not reorder without re-deriving the
# expected outputs in tests/test_note_embeddings.py.
NEAR_DUP_SEEDS: list[tuple[str, str, str, list[float]]] = [
    ("notes/a.md", "company", "Title A", [3.0, 4.0, 0.0, 0.0]),
    ("notes/b.md", "company", "Title B", [4.0, 3.0, 0.0, 0.0]),
    # d before c: index order (2, 3) vs string order — exercises the swap
    ("notes/d.md", "company", "Title D", [8.0, 6.0, 0.0, 0.0]),
    ("notes/c.md", "company", "Title C", [6.0, 8.0, 0.0, 0.0]),
    # multi-section path: two sections, two titles — mean [1, 1, 0, 0],
    # first-seen title wins
    ("notes/e_multi.md", "company", "Multi T1", [2.0, 0.0, 0.0, 0.0]),
    ("notes/e_multi.md", "company", "Multi T2", [0.0, 2.0, 0.0, 0.0]),
    # sim(e_multi, f_dir) = 0.5 — only true if the collapse took the MEAN
    ("notes/f_dir.md", "company", "Dir F", [0.0, 1.0, 1.0, 0.0]),
    ("notes/g_zero.md", "company", "Zero G", [0.0, 0.0, 0.0, 0.0]),
    ("notes/n1.md", "newsletter", "N1", [3.0, 4.0, 0.0, 0.0]),
    ("notes/n2.md", "newsletter", "N2", [4.0, 3.0, 0.0, 0.0]),
]


def near_dup_con() -> duckdb.DuckDBPyConnection:
    """A fresh in-memory DuckDB with v_note_embeddings seeded (schema per
    query.py:1556-1558). Caller closes the connection."""
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE v_note_embeddings (
            file_path VARCHAR,
            doc_type VARCHAR,
            title VARCHAR,
            emb FLOAT[]
        )
        """
    )
    con.executemany(
        "INSERT INTO v_note_embeddings VALUES (?, ?, ?, ?)",
        [(fp, dt, title, emb) for fp, dt, title, emb in NEAR_DUP_SEEDS],
    )
    return con
