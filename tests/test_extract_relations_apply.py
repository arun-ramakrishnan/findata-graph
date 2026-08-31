#!/usr/bin/env python3
"""Tests for helpers/graph/extract_relations.py — split from the
original test_extract_relations.py for navigability.

DB persistence (apply_edges), sidecar writing, CLI path expansion.
"""

from __future__ import annotations

import json

from helpers.graph.extract_relations import (  # noqa: E402
    Edge,
    Unresolved,
    _expand_paths,
    _SUPPRESSED_EDGES,
    apply_edges,
    write_sidecar,
)


# --------------------------------------------------------------------------- #
# Persistence                                                                 #
# --------------------------------------------------------------------------- #
class TestApplyEdges:
    def test_apply_edges_dry_run_counts_inserts(self, tmp_path, monkeypatch):
        # Build an in-memory DB with the graph_edges schema.
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
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
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, target, edge_type)
            )
        """)
        edges = [
            Edge(
                source="A",
                target="B",
                edge_type="jv_with",
                properties={"edition": "T"},
                source_ref="t",
                symmetric=True,
            ),
            # Different edge type — should also count.
            Edge(
                source="A",
                target="B",
                edge_type="subsidiary_of",
                properties={"edition": "T"},
                source_ref="t",
                symmetric=False,
            ),
        ]
        # Dry-run: both would be inserted (different edge types).
        result = apply_edges(edges, conn=conn, dry_run=True)
        assert result.inserted == 2
        assert result.skipped_fk == 0
        assert result.skipped_suppressed == 0
        # Real apply: 2 rows written.
        result = apply_edges(edges, conn=conn, dry_run=False)
        assert result.inserted == 2
        # Re-apply should be 0 (idempotent via UNIQUE).
        result = apply_edges(edges, conn=conn, dry_run=False)
        assert result.inserted == 0
        conn.close()

    def test_apply_edges_suppressed_edges_skipped(self, tmp_path, monkeypatch):
        """Edges in `_SUPPRESSED_EDGES` are silently skipped at apply time.

        These represent hand-corrected attributions where the prose was in
        company X's note but the actual subject is a separately-stubbed
        entity Y. Without suppression, re-runs would re-add the wrong edge.
        """
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
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
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, target, edge_type)
            )
        """)
        # Pull a real suppression tuple from the live set so the test stays
        # in sync if we add more later.
        src, tgt, et = next(iter(_SUPPRESSED_EDGES))
        edges = [
            # Suppressed edge — should NOT be written.
            Edge(
                source=src,
                target=tgt,
                edge_type=et,
                properties={"edition": "T"},
                source_ref="t",
                symmetric=False,
            ),
            # Normal edge — should be written.
            Edge(
                source="A",
                target="B",
                edge_type="jv_with",
                properties={"edition": "T"},
                source_ref="t",
                symmetric=True,
            ),
        ]
        result = apply_edges(edges, conn=conn, dry_run=False)
        assert result.inserted == 1  # only the jv_with edge
        assert result.skipped_suppressed == 1  # the hand-corrected tuple
        assert result.skipped_fk == 0
        # Verify the suppressed edge is not in the table.
        row = conn.execute(
            "SELECT 1 FROM graph_edges WHERE source=? AND target=? AND edge_type=?",
            (src, tgt, et),
        ).fetchone()
        assert row is None
        conn.close()

    def test_apply_competes_with_edge_persists_and_idempotent(self):
        """competes_with edges persist with symmetric=1 and are idempotent
        under re-apply via UNIQUE(source, target, edge_type)."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
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
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, target, edge_type)
            )
        """)
        # Canonical alphabetical ordering for symmetric edges.
        edges = [
            Edge(
                source="Ashok Leyland",
                target="Tata Motors",
                edge_type="competes_with",
                properties={"edition": "T", "quote": "...", "newsletter": "X"},
                source_ref="derive:relations:The_Chatter",
                symmetric=True,
            ),
        ]
        assert apply_edges(edges, conn=conn, dry_run=True).inserted == 1
        assert apply_edges(edges, conn=conn, dry_run=False).inserted == 1
        # Idempotent re-apply.
        assert apply_edges(edges, conn=conn, dry_run=False).inserted == 0
        row = conn.execute(
            "SELECT source, target, edge_type, symmetric FROM graph_edges"
        ).fetchone()
        assert row["source"] == "Ashok Leyland"
        assert row["target"] == "Tata Motors"
        assert row["edge_type"] == "competes_with"
        assert row["symmetric"] == 1
        conn.close()

    def test_apply_edges_surfaces_integrity_skips_in_result(self):
        """F5: apply_edges returns an ApplyEdgesResult whose skipped_fk counter
        distinguishes "0 inserted, N skipped" (integrity errors ate the batch)
        from "0 inserted, 0 skipped" (nothing to do).

        Before F5 the function returned only ``inserted`` as a bare int, so a
        schema change that rejected every edge would report success. We trigger
        a real FK IntegrityError (the production graph_edges.source references
        entities.name) — an edge whose source entity doesn't exist. Note
        ``INSERT OR IGNORE`` only suppresses UNIQUE/CHECK/NOT NULL conflicts,
        NOT FK violations (those still raise and hit the except path).
        """
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TABLE entities(name TEXT PRIMARY KEY)")
        conn.execute("""
            CREATE TABLE graph_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL REFERENCES entities(name),
                target TEXT NOT NULL REFERENCES entities(name),
                edge_type TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                properties TEXT NOT NULL DEFAULT '{}',
                valid_from DATE,
                valid_to DATE,
                source_ref TEXT NOT NULL,
                symmetric INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, target, edge_type)
            )
        """)
        # Only GoodCo/OtherCo exist as entities; MissingCo does not.
        conn.executemany("INSERT INTO entities(name) VALUES (?)", [("GoodCo",), ("OtherCo",)])
        edges = [
            # Bad: source "MissingCo" is not in entities → FK violation →
            # caught by apply_edges' except path → skipped_fk += 1.
            Edge(
                source="MissingCo",
                target="OtherCo",
                edge_type="jv_with",
                properties={"edition": "T"},
                source_ref="t",
                symmetric=True,
            ),
            # Good: both endpoints exist → inserts normally.
            Edge(
                source="GoodCo",
                target="OtherCo",
                edge_type="jv_with",
                properties={"edition": "T"},
                source_ref="t",
                symmetric=True,
            ),
        ]
        result = apply_edges(edges, conn=conn, dry_run=False)
        # The result is a NamedTuple — the three counters are now visible to
        # callers (previously only `inserted` escaped the function).
        assert result.inserted == 1
        assert result.skipped_fk == 1
        assert result.skipped_suppressed == 0
        # total_seen is the convenience sum.
        assert result.total_seen == 2
        conn.close()


# --------------------------------------------------------------------------- #
# Sidecar                                                                     #
# --------------------------------------------------------------------------- #
class TestSidecar:
    def test_write_sidecar_appends_jsonl(self, tmp_path, monkeypatch):
        sidecar = tmp_path / "pending.jsonl"
        monkeypatch.setattr("helpers.graph.extract_relations.SIDECAR_PATH", sidecar)
        u1 = Unresolved(
            edge_type="acquired",
            source="A",
            target_mention="X",
            quote="...",
            edition="E1",
        )
        u2 = Unresolved(
            edge_type="jv_with",
            source="B",
            target_mention="Y",
            quote="...",
            edition="E2",
        )
        n = write_sidecar([u1, u2], path=sidecar)
        assert n == 2
        lines = sidecar.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        d = json.loads(lines[0])
        assert d["edge_type"] == "acquired"
        assert d["source"] == "A"

    def test_write_sidecar_empty_is_noop(self, tmp_path):
        sidecar = tmp_path / "pending.jsonl"
        n = write_sidecar([], path=sidecar)
        assert n == 0
        assert not sidecar.exists()


# --------------------------------------------------------------------------- #
# Path expansion (recursive directory scan)                                   #
# --------------------------------------------------------------------------- #
class TestExpandPaths:
    """Tests for `_expand_paths` — the recursive directory scanner used by the CLI."""

    def test_single_file_arg(self, tmp_path):
        f = tmp_path / "Foo.md"
        f.write_text("# Foo", encoding="utf-8")
        out = _expand_paths([str(f)], project_root=tmp_path)
        assert out == [f]

    def test_directory_arg_recursively_scans_md(self, tmp_path):
        nl_dir = tmp_path / "The_Chatter"
        nl_dir.mkdir()
        (nl_dir / "A.md").write_text("# A", encoding="utf-8")
        (nl_dir / "B.md").write_text("# B", encoding="utf-8")
        (nl_dir / "readme.txt").write_text("ignore me", encoding="utf-8")
        # Subdir should also be scanned.
        sub = nl_dir / "extras"
        sub.mkdir()
        (sub / "C.md").write_text("# C", encoding="utf-8")
        out = _expand_paths([str(nl_dir)], project_root=tmp_path)
        names = sorted(p.name for p in out)
        assert names == ["A.md", "B.md", "C.md"]

    def test_image_map_md_is_skipped(self, tmp_path):
        nl_dir = tmp_path / "The_Chatter"
        nl_dir.mkdir()
        (nl_dir / "Foo.md").write_text("# Foo", encoding="utf-8")
        (nl_dir / "image_map.md").write_text("# not prose", encoding="utf-8")
        out = _expand_paths([str(nl_dir)], project_root=tmp_path)
        names = [p.name for p in out]
        assert names == ["Foo.md"]

    def test_images_subdir_is_skipped(self, tmp_path):
        nl_dir = tmp_path / "The_Chatter"
        nl_dir.mkdir()
        (nl_dir / "Foo.md").write_text("# Foo", encoding="utf-8")
        # images/ subdir with stray .md should be ignored.
        img_dir = nl_dir / "images"
        img_dir.mkdir()
        (img_dir / "chart1.jpeg").write_text("binary", encoding="utf-8")
        (img_dir / "README.md").write_text("# ignore", encoding="utf-8")
        out = _expand_paths([str(nl_dir)], project_root=tmp_path)
        names = [p.name for p in out]
        assert names == ["Foo.md"]

    def test_synced_stores_included_in_recursive_scan(self, tmp_path):
        # Scanning a parent of `findata/` now descends into Companies/ and
        # Sectors/ too — company notes are first-class inputs and yield
        # high-precision edges. (Previously they were skipped because the
        # section splitter misclassified their H1 titles.)
        findata = tmp_path / "findata"
        findata.mkdir()
        for nl in ("The_Chatter", "Companies", "Sectors"):
            nd = findata / nl
            nd.mkdir()
            (nd / "Foo.md").write_text("# Foo", encoding="utf-8")
        out = _expand_paths([str(findata)], project_root=tmp_path)
        names = sorted(p.name for p in out)
        # All three .md files included.
        assert names == ["Foo.md", "Foo.md", "Foo.md"]

    def test_relative_path_resolves_against_project_root(self, tmp_path):
        (tmp_path / "Foo.md").write_text("# Foo", encoding="utf-8")
        out = _expand_paths(["Foo.md"], project_root=tmp_path)
        assert out == [tmp_path / "Foo.md"]

    def test_nonexistent_path_emits_warning_skips(self, tmp_path, capsys):
        out = _expand_paths([str(tmp_path / "does_not_exist.md")], project_root=tmp_path)
        assert out == []
        captured = capsys.readouterr()
        assert "path not found" in captured.err

    def test_duplicate_paths_deduped(self, tmp_path):
        f = tmp_path / "Foo.md"
        f.write_text("# Foo", encoding="utf-8")
        # Same file passed twice.
        out = _expand_paths([str(f), str(f)], project_root=tmp_path)
        assert out == [f]

    def test_directory_and_file_mix(self, tmp_path):
        nl_dir = tmp_path / "The_Chatter"
        nl_dir.mkdir()
        (nl_dir / "A.md").write_text("# A", encoding="utf-8")
        other = tmp_path / "Other.md"
        other.write_text("# Other", encoding="utf-8")
        out = _expand_paths([str(nl_dir), str(other)], project_root=tmp_path)
        names = sorted(p.name for p in out)
        assert names == ["A.md", "Other.md"]

    def test_results_sorted_for_deterministic_output(self, tmp_path):
        nl_dir = tmp_path / "The_Chatter"
        nl_dir.mkdir()
        # Create files in non-alphabetical order.
        for name in ["C.md", "A.md", "B.md"]:
            (nl_dir / name).write_text(f"# {name}", encoding="utf-8")
        out = _expand_paths([str(nl_dir)], project_root=tmp_path)
        names = [p.name for p in out]
        assert names == ["A.md", "B.md", "C.md"]
