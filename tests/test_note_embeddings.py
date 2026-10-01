#!/usr/bin/env python3
"""sql_capability_unlocks A1/A2 — v_note_embeddings materialisation + wrappers.

End-to-end over a temp SQLite (entities + FTS5 note_search with JSON
embedding columns) → connect(fresh=True) → DuckDB materialisation → the
four wrappers. Plus the warm-path drift checks (_is_warm model/dims
stamps) that force cold on a model swap.
"""

from __future__ import annotations

import ast
import json
import math
import sqlite3
from pathlib import Path

import pytest


from helpers.graph.fixture_note_embeddings import near_dup_con  # noqa: E402
from helpers.maintenance.rebuild_note_search import NOTE_SEARCH_DDL  # noqa: E402
from helpers.graph.query import (  # noqa: E402
    connect,
    edition_companies,
    near_duplicate_notes,
    notes_like_entity,
    notes_like_text,
    similar_notes,
    _is_warm,
)
from tests.schema import ENTITY_TAGS  # noqa: E402

pytestmark = [pytest.mark.integration]

_SCHEMA = (
    """
CREATE TABLE entities (
    name TEXT PRIMARY KEY NOT NULL,
    entity_type TEXT NOT NULL,
    file_path TEXT,
    normalized_name TEXT,
    sector_classification TEXT,
    ticker TEXT
);
"""
    + ENTITY_TAGS
    + """
CREATE TABLE graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    properties TEXT NOT NULL DEFAULT '{}',
    valid_from DATE,
    valid_to DATE,
    source_ref TEXT NOT NULL,
    symmetric INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
);
"""
)

_DIM = 4

# Vector geometry: HDFC ≈ ICICI ≈ chatter-note (near-parallel, +x); Infosys
# is orthogonal (+y → cosine 0, filtered by the sim > 0 guard); the _Old
# note is an EXACT duplicate of HDFC's vector (the near-dup tripwire).
_VEC_HDFC = [1.0, 0.0, 0.0, 0.0]
_VEC_ICICI = [0.9, 0.1, 0.0, 0.0]
_VEC_INFY = [0.0, 1.0, 0.0, 0.0]
_VEC_CHATTER = [0.95, 0.05, 0.0, 0.0]

_NOTES = [
    # (doc_type, file_path, title, vector)
    ("company", "findata/Companies/Banking/Hdfc_Bank.md", "HDFC Bank", _VEC_HDFC),
    ("company", "findata/Companies/Banking/ICICI_Bank.md", "ICICI Bank", _VEC_ICICI),
    ("company", "findata/Companies/Technology/Infosys.md", "Infosys", _VEC_INFY),
    ("company", "findata/Companies/Banking/Hdfc_Bank_Old.md", "HDFC Bank Old", _VEC_HDFC),
    ("chatter", "findata/The_Chatter/Bank_Chatter.md", "The Chatter: Banks", _VEC_CHATTER),
]


def _make_db(tmp_path, dims=_DIM, with_model_stamp=None, extra_companies=0):
    db_path = tmp_path / "notes.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name, "
        "sector_classification) VALUES (?,?,?,?,?)",
        [
            (
                "HDFC Bank",
                "company",
                "findata/Companies/Banking/Hdfc_Bank.md",
                "HDFC Bank",
                "Banking",
            ),
            (
                "ICICI Bank",
                "company",
                "findata/Companies/Banking/ICICI_Bank.md",
                "ICICI Bank",
                "Banking",
            ),
            (
                "Infosys",
                "company",
                "findata/Companies/Technology/Infosys.md",
                "Infosys",
                "Technology",
            ),
            ("Banking", "sector", "findata/Sectors/Banking.md", "Banking", None),
        ],
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref) VALUES (?,?,?,'seed')",
        [
            ("HDFC Bank", "Banking", "part_of"),
            ("ICICI Bank", "Banking", "part_of"),
            ("HDFC Bank", "ICICI Bank", "competes_with"),
        ],
    )
    conn.execute(NOTE_SEARCH_DDL)
    for dtype, fpath, title, vec in _NOTES:
        stored = vec if dims == _DIM else vec + [0.0] * (dims - _DIM)
        conn.execute(
            "INSERT INTO note_search (doc_type, file_path, title, sector, "
            "content, embedding) VALUES (?,?,?,?,?,?)",
            (dtype, fpath, title, "", f"body of {title}", json.dumps(stored)),
        )
    # Optional near-parallel company notes, all +x-ish so every mutual cosine
    # is positive and the pair count grows as C(n, 2). The default fixture
    # yields only 6 pairs, which is not enough to cross prune_at=4*limit for
    # any limit above 1 — see the wide_note_con fixture and
    # test_bounded_accumulator_is_exact_under_pruning.
    for i in range(extra_companies):
        vec = [1.0, 0.02 * (i + 1), 0.0, 0.0]
        stored = vec if dims == _DIM else vec + [0.0] * (dims - _DIM)
        conn.execute(
            "INSERT INTO note_search (doc_type, file_path, title, sector, "
            "content, embedding) VALUES (?,?,?,?,?,?)",
            (
                "company",
                f"findata/Companies/Synthetic/Synth_{i}.md",
                f"Synth {i}",
                "",
                f"body of Synth {i}",
                json.dumps(stored),
            ),
        )
    if with_model_stamp is not None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS db_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO db_meta(key, value) VALUES ('note_embed_model', ?)",
            (with_model_stamp,),
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def note_con(tmp_path):
    db_path = _make_db(tmp_path)
    con = connect(db_path=db_path, fresh=True)
    yield con, db_path
    con.close()


@pytest.fixture
def wide_note_con(tmp_path):
    """9 company paths -> 36 pairs, enough to cross prune_at=4*limit for every
    limit the pruning test exercises (4/8/12/28)."""
    db_path = _make_db(tmp_path, extra_companies=5)
    con = connect(db_path=db_path, fresh=True)
    yield con, db_path
    con.close()


class TestMaterialisation:
    def test_projects_all_embedded_rows(self, note_con):
        con, _ = note_con
        n, dim = con.execute("SELECT COUNT(*), first(len(emb)) FROM v_note_embeddings").fetchone()
        assert n == len(_NOTES)
        assert dim == _DIM

    def test_dims_stamped_in_build_meta(self, note_con):
        con, _ = note_con
        row = con.execute("SELECT value FROM _build_meta WHERE key='note_embed_dims'").fetchone()
        assert row is not None and row[0] == str(_DIM)

    def test_model_stamp_round_trips(self, tmp_path):
        db_path = _make_db(tmp_path, with_model_stamp="bge-small-en-v1.5")
        duckdb_path = db_path.with_suffix(".duckdb")
        con = connect(db_path=db_path, fresh=True)
        con.close()
        # _build_meta lives in the duckdb file; read it back read-only.
        import duckdb as _ddb

        ro = _ddb.connect(str(duckdb_path), read_only=True)
        try:
            row = ro.execute(
                "SELECT value FROM _build_meta WHERE key='note_embed_model'"
            ).fetchone()
        finally:
            ro.close()
        assert row is not None and row[0] == "bge-small-en-v1.5"
        assert _is_warm(duckdb_path) is True

    def test_model_swap_forces_cold(self, tmp_path):
        """A same-dims model swap (the case the dims probe alone cannot
        see) must flip _is_warm — never serve cross-model cosines."""
        db_path = _make_db(tmp_path, with_model_stamp="bge-small-en-v1.5")
        duckdb_path = db_path.with_suffix(".duckdb")
        con = connect(db_path=db_path, fresh=True)
        con.close()
        assert _is_warm(duckdb_path) is True

        sc = sqlite3.connect(str(db_path))
        sc.execute(
            "INSERT OR REPLACE INTO db_meta(key, value) VALUES ('note_embed_model', 'minilm-l6-v2')"
        )
        sc.commit()
        sc.close()
        assert _is_warm(duckdb_path) is False

    def test_dims_drift_forces_cold(self, tmp_path):
        """Rewriting note_search at a different vector size (no generation
        bump — FTS5 has no triggers) must still flip _is_warm via the
        note_embed_dims stamp."""
        db_path = _make_db(tmp_path)
        duckdb_path = db_path.with_suffix(".duckdb")
        con = connect(db_path=db_path, fresh=True)
        con.close()
        assert _is_warm(duckdb_path) is True

        sc = sqlite3.connect(str(db_path))
        sc.execute("UPDATE note_search SET embedding = ?", (json.dumps(_VEC_HDFC + [0.0] * 4),))
        sc.commit()
        sc.close()
        assert _is_warm(duckdb_path) is False


class TestSimilarNotes:
    def test_self_excluded_and_ranked(self, note_con):
        con, _ = note_con
        res = similar_notes(con, "findata/Companies/Banking/Hdfc_Bank.md")
        assert res is not None
        # Orthogonal Infosys is filtered by sim > 0 exactly under cosine;
        # the l2->cosine conversion carries ~1 float32 ulp (~6e-8), so
        # drop sub-1e-6 noise before asserting (proposal: tolerances).
        paths = [p for p, _t, s in res if s > 1e-6]
        assert "findata/Companies/Banking/Hdfc_Bank.md" not in paths  # self-exclusion
        # Exact duplicate first, then the near-parallel rows.
        assert paths[0] == "findata/Companies/Banking/Hdfc_Bank_Old.md"
        assert set(paths) == {
            "findata/Companies/Banking/Hdfc_Bank_Old.md",
            "findata/Companies/Banking/ICICI_Bank.md",
            "findata/The_Chatter/Bank_Chatter.md",
        }

    def test_doc_type_filter(self, note_con):
        con, _ = note_con
        res = similar_notes(con, "findata/Companies/Banking/Hdfc_Bank.md", doc_type="company")
        # Drop conversion ulp noise (see test_self_excluded_and_ranked).
        assert [p for p, _t, s in res if s > 1e-6] == [
            "findata/Companies/Banking/Hdfc_Bank_Old.md",
            "findata/Companies/Banking/ICICI_Bank.md",
        ]

    def test_k_limit(self, note_con):
        con, _ = note_con
        res = similar_notes(con, "findata/Companies/Banking/Hdfc_Bank.md", k=1)
        assert len(res) == 1

    def test_unknown_note_returns_none(self, note_con):
        con, _ = note_con
        assert similar_notes(con, "findata/Companies/Nope.md") is None


class TestNotesLikeEntity:
    def test_newsletters_ranked(self, note_con):
        con, _ = note_con
        res = notes_like_entity(con, "HDFC Bank")
        assert res is not None
        assert [p for p, _t, _s in res] == ["findata/The_Chatter/Bank_Chatter.md"]

    def test_unknown_entity_returns_none(self, note_con):
        con, _ = note_con
        assert notes_like_entity(con, "No Such Co") is None


class TestNotesLikeText:
    def test_text_matches_nearest_company(self, note_con):
        con, _ = note_con
        res = notes_like_text(con, "HDFC Bank", embed_fn=lambda _t: _VEC_HDFC)
        assert res is not None
        res = [r for r in res if r[2] > 1e-6]  # drop conversion ulp noise
        # External text has no self-exclusion: both HDFC rows rank first
        # (exact-duplicate vectors tie), Infosys is orthogonal → filtered.
        assert res[0][0] == "findata/Companies/Banking/Hdfc_Bank.md"
        assert all("Infosys" not in p for p, _t, _s in res)

    def test_doc_type_filter(self, note_con):
        con, _ = note_con
        res = notes_like_text(
            con, "bank chatter", doc_type="chatter", embed_fn=lambda _t: _VEC_CHATTER
        )
        assert res is not None
        assert [p for p, _t, _s in res] == ["findata/The_Chatter/Bank_Chatter.md"]

    def test_min_sim_and_k(self, note_con):
        con, _ = note_con
        # Anti-parallel text vector → all cosines ≤ 0 → filtered by the
        # sim > 0 guard (an exact-duplicate fixture vector would survive
        # any min_sim < 1.0, so this is the deterministic empty case).
        # The l2->cosine conversion carries ~1 float32 ulp (~6e-8): allow
        # sub-1e-6 residue, assert nothing meaningful ranks.
        leaked = notes_like_text(con, "x", embed_fn=lambda _t: [-1.0, 0.0, 0.0, 0.0]) or []
        assert all(s <= 1e-6 for _p, _t, s in leaked)
        res = notes_like_text(con, "x", k=1, embed_fn=lambda _t: _VEC_HDFC)
        assert res is not None and len(res) == 1

    def test_dims_mismatch_returns_none(self, note_con):
        con, _ = note_con
        assert notes_like_text(con, "x", embed_fn=lambda _t: [1.0, 0.0]) is None

    def test_no_embedder_returns_none(self, note_con):
        # conftest's autouse _no_local_embedder pin makes the default
        # path take the unavailable branch — the parse --cross-check
        # "warn and skip" contract depends on this None.
        con, _ = note_con
        assert notes_like_text(con, "HDFC Bank") is None


class TestEditionCompanies:
    def test_resolves_by_stem(self, note_con):
        con, _ = note_con
        res = edition_companies(con, "Bank_Chatter")
        assert res is not None
        paths = [p for p, _t, _s in res]
        # All four companies have sim > 0 vs the chatter note (Infosys is
        # NEARLY orthogonal at ~0.05, not exactly 0). The two identical
        # HDFC vectors tie for first — assert them as a set.
        assert set(paths[:2]) == {
            "findata/Companies/Banking/Hdfc_Bank.md",
            "findata/Companies/Banking/Hdfc_Bank_Old.md",
        }
        assert paths[2:] == [
            "findata/Companies/Banking/ICICI_Bank.md",
            "findata/Companies/Technology/Infosys.md",
        ]
        # Monotone similarity ordering.
        sims = [s for _p, _t, s in res]
        assert sims == sorted(sims, reverse=True)

    def test_resolves_by_title(self, note_con):
        con, _ = note_con
        res = edition_companies(con, "The Chatter: Banks")
        assert res is not None and len(res) == 4

    def test_unresolvable_returns_none(self, note_con):
        con, _ = note_con
        assert edition_companies(con, "No_Such_Edition") is None


class TestNearDuplicateNotes:
    def test_exact_duplicate_top_pair(self, note_con):
        con, _ = note_con
        pairs = near_duplicate_notes(con, min_sim=0.5)
        assert pairs, "expected at least the injected duplicate pair"
        pa, pb, ta, tb, sim = pairs[0]
        assert sim == pytest.approx(1.0)
        assert {pa, pb} == {
            "findata/Companies/Banking/Hdfc_Bank.md",
            "findata/Companies/Banking/Hdfc_Bank_Old.md",
        }

    def test_threshold_and_doc_type(self, note_con):
        con, _ = note_con
        # Above 0.999: only the exact duplicate (HDFC-ICICI sits at ~0.994).
        strict = near_duplicate_notes(con, min_sim=0.999)
        assert len(strict) == 1
        # The chatter note is excluded from a company-only self-join even
        # at a loose threshold.
        loose = near_duplicate_notes(con, min_sim=0.5)
        assert all(
            "The_Chatter" not in pa and "The_Chatter" not in pb for pa, pb, _ta, _tb, _s in loose
        )

    def test_limit_zero_returns_empty(self, note_con):
        """csr_lane_remediation S4: the bounded accumulator short-circuits on
        limit=0; the retired unbounded version returned [] via `out[:0]`.
        Regression for the IndexError an intermediate heap attempt raised."""
        con, _ = note_con
        assert near_duplicate_notes(con, min_sim=0.0, limit=0) == []

    def test_bounded_accumulator_is_exact_under_pruning(self, wide_note_con):
        """csr_lane_remediation S4: the accumulator is bounded, and pruning is
        exact — both asserted, not inspected.

        Two separate properties, two separate assertions:
          * **the bound holds** — ``stats["peak"]`` never exceeds prune_at, and
            ``stats["prunes"] > 0`` proves the prune actually ran;
          * **pruning is sound** — the result equals the unpruned top-`limit``.

        The second alone is worthless as a check on the *bound*: the final
        `out.sort(); del out[limit:]` makes the return value invariant to how
        much the buffer retains. Verified by mutation — with the prune branch
        disabled this test used to stay green, as did an injected
        `del out[limit + 1:]` off-by-one. The `stats` seam is what makes
        prune-absence detectable; without it only the soundness half is
        testable.

        The corpus must reach prune_at or nothing executes: the default fixture
        yields 6 pairs, so only limit=1 (prune_at=4) would prune and limits
        2/3/7 (8/12/28) would be untested no-ops. The precondition below fails
        loudly if the corpus is ever shrunk back.
        """
        con, _ = wide_note_con
        # min_sim=0.0 keeps every pair (the guard is sim >= min_sim), so the
        # buffer is at its widest: 9 company paths -> C(9,2) = 36 pairs.
        reference = near_duplicate_notes(con, min_sim=0.0, limit=10_000)
        assert len(reference) == 36, f"expected 36 pairs, got {len(reference)}"
        sims = [s for *_rest, s in reference]
        assert sims == sorted(sims, reverse=True), "reference must be similarity-ordered"
        for limit in (1, 2, 3, 7):
            prune_at = 4 * limit
            # Strictly greater: the unpruned buffer would be this large, so
            # peak <= prune_at is a real constraint rather than one any buffer
            # of this corpus satisfies trivially. This assertion is the control.
            assert len(reference) > prune_at, (
                f"corpus yields {len(reference)} pairs, not more than "
                f"prune_at={prune_at} — the peak assertion below would be vacuous"
            )
            stats: dict = {}
            pruned = near_duplicate_notes(con, min_sim=0.0, limit=limit, stats=stats)
            assert stats["prunes"] > 0, f"limit={limit}: the 4x prune never fired"
            assert stats["peak"] <= prune_at, (
                f"limit={limit}: accumulator peaked at {stats['peak']}, above prune_at={prune_at}"
            )
            assert stats["post_prune_max"] <= limit, (
                f"limit={limit}: prune left {stats['post_prune_max']} pairs, above limit={limit}"
            )
            assert pruned == reference[:limit], (
                f"limit={limit} diverged from the reference — pruning discarded a top-{limit} pair"
            )
            pruned = near_duplicate_notes(con, min_sim=0.0, limit=limit)
            assert pruned == reference[:limit], (
                f"limit={limit} diverged from the reference — pruning discarded a top-{limit} pair"
            )


class TestNearDuplicateInvariants:
    """The coverage gaps c901_d1_split_near_duplicate_notes S1 closes, over
    the deterministic fixture (helpers/graph/fixture_note_embeddings.py).

    The 4x prune is NOT re-tested here — test_bounded_accumulator_is_exact_
    under_pruning already holds it down through the stats seam with
    documented mutation teeth. These tests own what no existing test saw:
    tie order, the mean collapse, the zero-norm guard, and the app.py memo
    key's coverage of the function's result-affecting parameters.
    """

    def test_tie_lattice_order_and_orientation(self):
        con = near_dup_con()
        try:
            got = near_duplicate_notes(con, min_sim=0.9, doc_type="company", limit=100)
        finally:
            con.close()
        # The full expected order: the (-sim, path_a, path_b) key fully
        # determines it — two bit-identical 1.0 pairs, four at 0.9899…, four
        # at 0.96. (c, d) is inserted index-reversed (d before c), so its
        # string orientation can only be right if the canonical swap ran.
        expected = [
            ("notes/a.md", "notes/c.md"),
            ("notes/b.md", "notes/d.md"),
            ("notes/a.md", "notes/e_multi.md"),
            ("notes/b.md", "notes/e_multi.md"),
            ("notes/c.md", "notes/e_multi.md"),
            ("notes/d.md", "notes/e_multi.md"),
            ("notes/a.md", "notes/b.md"),
            ("notes/a.md", "notes/d.md"),
            ("notes/b.md", "notes/c.md"),
            ("notes/c.md", "notes/d.md"),
        ]
        assert [(pa, pb) for pa, pb, *_r in got] == expected
        sims = [s for *_r, s in got]
        assert sims[0] == sims[1] == 1.0
        assert sims[2] == sims[3] == sims[4] == sims[5]
        assert sims[6] == sims[7] == sims[8] == sims[9] == 0.96

    def test_zero_norm_guard_stays_finite(self):
        """The norms[norms == 0] = 1.0 guard: without it the zero row divides
        0/0 into NaN, every g_zero pair fails the `>= min_sim` mask, and the
        six sim-0.0 pairs below silently vanish (verified: this test is RED
        with the guard line removed)."""
        con = near_dup_con()
        try:
            got = near_duplicate_notes(con, min_sim=0.0, doc_type="company", limit=10_000)
        finally:
            con.close()
        sims = [s for *_r, s in got]
        assert not any(math.isnan(s) for s in sims), "zero-norm guard lost: NaN in sims"
        zero_pairs = {frozenset((pa, pb)) for pa, pb, *_r, s in got if s == 0.0}
        others = [f"notes/{n}.md" for n in ("a", "b", "c", "d", "e_multi", "f_dir")]
        assert zero_pairs == {frozenset(("notes/g_zero.md", o)) for o in others}

    def test_multi_section_collapse_is_the_mean(self):
        """e_multi carries sections [2,0,0,0] and [0,2,0,0]: the collapse must
        average them to [1,1,0,0] (sim to f_dir = 0.5), not take the first
        section (sim 0.0) or the last (sim 0.0). First-seen title wins."""
        con = near_dup_con()
        try:
            got = near_duplicate_notes(con, min_sim=0.4, doc_type="company", limit=100)
        finally:
            con.close()
        pair = next(p for p in got if {p[0], p[1]} == {"notes/e_multi.md", "notes/f_dir.md"})
        _pa, _pb, title_a, _tb, sim = pair
        assert sim == pytest.approx(0.5)
        assert title_a == "Multi T1"

    def test_app_memo_key_covers_signature(self):
        """app.py's AVAIL-1 memo keys on (gen, doc_type, min_sim, limit) —
        every result-affecting parameter of the call it caches. A split (or
        any signature change) that renames/reorders a parameter without
        updating the key serves stale results with no error, so pin the
        key-covers-call invariant mechanically (AST, not vibes)."""
        app_src = (Path(__file__).resolve().parents[1] / "app.py").read_text()
        fn = next(
            node
            for node in ast.walk(ast.parse(app_src))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_cached_near_duplicates"
        )
        key_names: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "key" for t in node.targets
            ):
                key_names |= {x.id for x in ast.walk(node.value) if isinstance(x, ast.Name)}
        assert {"gen", "doc_type", "min_sim", "limit"} <= key_names, (
            "the memo key no longer covers every result-affecting parameter"
        )
        calls = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Name) and n.func.id == "near_duplicate_notes")
                or (isinstance(n.func, ast.Attribute) and n.func.attr == "near_duplicate_notes")
            )
        ]
        assert len(calls) == 1, "expected exactly one near_duplicate_notes call site"
        kwargs = {kw.arg for kw in calls[0].keywords}
        assert {"min_sim", "doc_type", "limit"} <= kwargs
        assert "stats" not in kwargs, "the cached API path must not pass the stats seam"


class TestNearDuplicateNumerics:
    """near_duplicate_gemm_rework_record S2 — the GEMM rework's exactness
    claims lived only in a query.py comment (the 2026-09-26 near_dup_prune
    block); these tests make them assertable. Written against the extracted
    _collapsed_note_matrix per the record's ordering note (the split landed
    first — the better target). The tie-break pin (c) is owned by
    TestNearDuplicateInvariants::test_tie_lattice_order_and_orientation and
    deliberately not duplicated here."""

    def test_collapse_is_deterministic_and_order_is_load_bearing(self):
        """Same rows -> bit-identical matrix (the record's accumulation-order
        dependency). The m1 path makes the order OBSERVABLE, not just
        repeatable: 1e16 + 1 == 1e16 exactly (ulp(1e16) = 2), so in the
        documented order the big pair cancels and one trailing 1 survives
        (comp-0 sums to 1.0), while in the big-pair-first order both 1s
        survive (comp-0 sums to 2.0). The renormalized DIRECTION of the row
        differs — 0.4472… vs 0.7071… on comp-0 — so any perturbation of the
        accumulation order flips this row and the test goes RED."""
        from helpers.graph.query import _collapsed_note_matrix

        big = 1e16
        # rows in the fetchall shape: (file_path, title, emb); documented
        # section order for m1 is the order below
        rows = [
            ("notes/m1.md", "M1", [big, 2.0, 0.0]),
            ("notes/m1.md", "M1", [1.0, 0.0, 0.0]),
            ("notes/m1.md", "M1", [-big, 0.0, 0.0]),
            ("notes/m1.md", "M1", [1.0, 0.0, 0.0]),
            ("notes/m2.md", "M2", [0.0, 1.0, 0.0]),
        ]
        X1, paths1, titles1 = _collapsed_note_matrix(rows)
        X2, paths2, titles2 = _collapsed_note_matrix(rows)
        assert X1.tobytes() == X2.tobytes(), "collapse is not deterministic"
        assert paths1 == paths2 == ["notes/m1.md", "notes/m2.md"]
        assert titles1 == titles2 == ["M1", "M2"]
        # documented order: comp-0 = ((big + 1) - big) + 1 = 1.0 (the 1 is
        # absorbed by big), comp-1 = 2.0 -> mean [0.25, 0.5, 0], normalized:
        u = [0.25 / math.sqrt(0.3125), 0.5 / math.sqrt(0.3125), 0.0]
        assert X1[0, 0] == u[0] and X1[0, 1] == u[1] and X1[0, 2] == u[2]
        # the big-pair-first order ((-big + big) + 1 + 1 = 2.0) would give
        # the normalized direction [0.7071…, 0.7071…, 0] — assert it did
        # NOT happen
        assert X1[0, 0] != 1 / math.sqrt(2)
        # single-section path: mean is the vector, renormalized to unit length
        assert X1[1, 1] == 1.0

    def test_cross_path_interleaving_is_free_within_path_order_is_not(self):
        """The pinned dependency, stated precisely: rows may interleave
        ACROSS paths as long as each path's first row stays first and its
        internal section order is preserved — the collapse is bit-identical.
        (Within-path reordering is NOT free; the 2^53 test above owns it.)"""
        from helpers.graph.query import _collapsed_note_matrix

        sec = {
            ("a", 1): [3.0, 4.0, 0.0, 0.0],
            ("a", 2): [0.0, 2.0, 0.0, 0.0],
            ("b", 1): [4.0, 3.0, 0.0, 0.0],
            ("c", 1): [6.0, 8.0, 0.0, 0.0],
        }
        contiguous = [
            ("notes/a.md", "A", sec[("a", 1)]),
            ("notes/a.md", "A", sec[("a", 2)]),
            ("notes/b.md", "B", sec[("b", 1)]),
            ("notes/c.md", "C", sec[("c", 1)]),
        ]
        interleaved = [
            ("notes/a.md", "A", sec[("a", 1)]),
            ("notes/b.md", "B", sec[("b", 1)]),
            ("notes/a.md", "A", sec[("a", 2)]),
            ("notes/c.md", "C", sec[("c", 1)]),
        ]
        X_c, paths_c, titles_c = _collapsed_note_matrix(contiguous)
        X_i, paths_i, titles_i = _collapsed_note_matrix(interleaved)
        assert X_c.tobytes() == X_i.tobytes()
        assert paths_c == paths_i == ["notes/a.md", "notes/b.md", "notes/c.md"]
        assert titles_c == titles_i == ["A", "B", "C"]

    def test_returned_sim_is_the_cosine_of_the_path_means(self):
        """The 'sim = 1 − d²/2 = cosine' identity, in its observable form:
        for the controlled (e_multi, f_dir) pair the renormalized means are
        u = [1/√2, 1/√2, 0, 0] and v = [0, 1/√2, 1/√2, 0], so the returned
        sim must equal both u·v = 0.5 and 1 − |u−v|²/2 — a broken collapse
        (first-section-only) would return 0.0 instead."""
        con = near_dup_con()
        try:
            got = near_duplicate_notes(con, min_sim=0.4, doc_type="company", limit=100)
        finally:
            con.close()
        pair = next(p for p in got if {p[0], p[1]} == {"notes/e_multi.md", "notes/f_dir.md"})
        sim = pair[4]
        u = [1 / math.sqrt(2), 1 / math.sqrt(2), 0.0, 0.0]
        v = [0.0, 1 / math.sqrt(2), 1 / math.sqrt(2), 0.0]
        dot = sum(a * b for a, b in zip(u, v))
        dist_sq = sum((a - b) ** 2 for a, b in zip(u, v))
        assert sim == pytest.approx(dot)
        assert sim == pytest.approx(1 - dist_sq / 2)
