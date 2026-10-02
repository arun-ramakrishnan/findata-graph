#!/usr/bin/env python3
"""Tests for helpers/graph/extract_relations.py — B2 relation sidecar
promotion (findata/Misc/_relations/<source>-<edge_type>-<target>.yaml).

Sidecars make human-accepted edges auditable and re-ingestible:
`extract_relations` scans the sidecar directory at extraction start,
loads each YAML, validates the schema, and promotes the edge with
source_ref=promote:sidecar:<filename>. INSERT OR IGNORE semantics make
promotion idempotent against corpus extraction and against edges written
earlier via triage:accept.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.core.frontmatter import yaml_safe_load
from helpers.graph.extract_relations import (  # noqa: E402
    Edge,
    promote_relation_sidecars,
)


# --------------------------------------------------------------------------- #
# Committed artifact validation                                               #
# --------------------------------------------------------------------------- #
class TestSidecarSchema:
    """Each committed sidecar in findata/Misc/_relations must carry the
    B2 schema: edge_type, source, target, counterparties, as_of, confidence,
    provenance (row_id/decision/bucket/word_overlap/note)."""

    SIDECARS = sorted(Path("findata/Misc/_relations").glob("*.yaml"))

    def test_all_sidecars_present_and_yaml_parsable(self):
        # At least the B2 triage batch (7 accepted prose edges).
        assert len(self.SIDECARS) >= 7, f"expected ~7 sidecars, found {len(self.SIDECARS)}"
        for p in self.SIDECARS:
            yaml_safe_load(p.read_text(encoding="utf-8"))  # raises if corrupt

    def test_all_sidecars_have_required_fields(self):
        def _slug(name: str) -> str:
            # Mirrors the B2 sidecar filename generation (spaces, apostrophes
            # -> _ ; parens dropped).
            return name.replace(" ", "_").replace("(", "").replace(")", "").replace("'", "_")

        for p in self.SIDECARS:
            d = yaml_safe_load(p.read_text(encoding="utf-8"))
            assert "edge_type" in d and d["edge_type"]
            assert "source" in d and d["source"]
            assert "target" in d and d["target"]
            assert "as_of" in d
            assert "counterparties" in d and isinstance(d["counterparties"], list)
            assert len(d["counterparties"]) == 2
            assert "confidence" in d and 0.0 <= d["confidence"] <= 1.0
            assert "provenance" in d
            prov = d["provenance"]
            assert "row_id" in prov and "decision" in prov
            assert prov.get("type") == "triage:accept"
            # filenames are source-edge_type_target slugs (self-describing).
            slug = p.stem
            parts = slug.split("-", 2)
            assert len(parts) == 3
            assert parts[0] == _slug(d["source"]), slug
            assert parts[1] == d["edge_type"], slug
            assert parts[2] == _slug(d["target"]), slug

    def test_confidence_formula(self):
        # confidence = bucket base (manual .85, alias_candidate .80,
        # stub_candidate .60) + .05 if word_overlap.
        expected = {
            "L_Oreal": 0.6,
            "Globus_Spirits": 0.6,
            "Nippon_Life_India_Asset_Management": 0.85,
            "Glenmark_Pharma_Nordic_SE": 0.85,
            "Clix_Capital": 0.85,
            "Tata_Consultancy_Services": 0.85,
            "TVS_Supply_Chain_Solutions": 0.85,
        }
        for p in self.SIDECARS:
            d = yaml_safe_load(p.read_text(encoding="utf-8"))
            slug = p.stem.split("-")[0]
            assert d["confidence"] == expected[slug], (p.stem, d["confidence"])


# --------------------------------------------------------------------------- #
# Hook behavior: validation and skip paths                                    #
# --------------------------------------------------------------------------- #
class TestPromoteSidecarsValidation:
    """promote_relation_sidecars skips on schema/integrity failure and
    never aborts the run."""

    def test_missing_as_of_skips(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.yaml"
            p.write_text("edge_type: acquired\nsource: X\ntarget: Y\n")
            monkeypatch.setattr("helpers.graph.extract_relations.RELATIONS_DIR", Path(tmp))
            from helpers.core.db import connect

            conn = connect()
            try:
                promoted, skipped = promote_relation_sidecars(conn, dry_run=True)
            finally:
                conn.close()
            assert promoted == 0
            assert skipped == 1

    def test_corrupt_yaml_skips(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.yaml"
            p.write_text("{not valid yaml {{{")
            monkeypatch.setattr("helpers.graph.extract_relations.RELATIONS_DIR", Path(tmp))
            from helpers.core.db import connect

            conn = connect()
            try:
                promoted, skipped = promote_relation_sidecars(conn, dry_run=True)
            finally:
                conn.close()
            assert promoted == 0
            assert skipped == 1

    def test_unsupported_edge_type_skips(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.yaml"
            p.write_text("edge_type: nonsense\nsource: X\ntarget: Y\nas_of: 2026-10-03\n")
            monkeypatch.setattr("helpers.graph.extract_relations.RELATIONS_DIR", Path(tmp))
            from helpers.core.db import connect

            conn = connect()
            try:
                promoted, skipped = promote_relation_sidecars(conn, dry_run=True)
            finally:
                conn.close()
            assert promoted == 0
            assert skipped == 1

    def test_empty_dir_returns_zero(self, monkeypatch):
        # Retarget to an empty temp dir to verify graceful no-op.
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setattr("helpers.graph.extract_relations.RELATIONS_DIR", Path(tmp))

            from helpers.core.db import connect

            conn = connect()
            try:
                promoted, skipped = promote_relation_sidecars(conn, dry_run=True)
            finally:
                conn.close()
            assert promoted == 0
            assert skipped == 0


# --------------------------------------------------------------------------- #
# Hook behavior: valid promotion and edge emission                            #
# --------------------------------------------------------------------------- #
class TestPromoteSidecarsEmission:
    """A valid sidecar promotes to an Edge with the right shape."""

    def test_valid_jv_sidecar_emits_edge(self, monkeypatch):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Glenmark_Pharma_Nordic_(SE)-jv_with-AbbVie.yaml"
            path.write_text(
                "# B2 sidecar\n"
                "edge_type: jv_with\n"
                "source: Glenmark Pharma Nordic (SE)\n"
                "target: AbbVie\n"
                "counterparties:\n"
                "  - Glenmark Pharma Nordic (SE)\n"
                "  - AbbVie\n"
                "as_of: 2026-10-03\n"
                "confidence: 0.85\n"
                "provenance:\n"
                "  type: triage:accept\n"
                "  row_id: 29b0e7a8c1\n"
                "  decision: accept:jv_with:AbbVie\n"
                "  note: genuine Glenmark-AbbVie JV\n"
            )
            monkeypatch.setattr("helpers.graph.extract_relations.RELATIONS_DIR", Path(tmp))

            captured = []

            def fake_apply_edges(edges, **kw):
                captured.extend(edges)
                return type(
                    "R",
                    (),
                    {
                        "inserted": 0,
                        "skipped_fk": 0,
                        "skipped_suppressed": 0,
                    },
                )()

            from helpers.core.db import connect

            conn = connect()
            try:
                with mock.patch("helpers.graph.extract_relations.apply_edges", fake_apply_edges):
                    promote_relation_sidecars(conn, dry_run=True)
            finally:
                conn.close()

            assert len(captured) == 1
            e = captured[0]
            assert isinstance(e, Edge)
            assert e.source == "Glenmark Pharma Nordic (SE)"
            assert e.target == "AbbVie"
            assert e.edge_type == "jv_with"
            assert e.source_ref == "promote:sidecar:Glenmark_Pharma_Nordic_(SE)-jv_with-AbbVie.yaml"
            assert e.symmetric is True  # jv_with is symmetric
            assert e.valid_from is None
            assert e.properties["doc_type"] == "sidecar"
            assert e.properties["provenance"]["row_id"] == "29b0e7a8c1"

    def test_acquired_is_not_symmetric(self, monkeypatch):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "L_Oreal-acquired-Caring_Beauty.yaml"
            path.write_text(
                "edge_type: acquired\n"
                "source: L'Oreal\ntarget: Caring Beauty\n"
                "counterparties: [L'Oreal, Caring Beauty]\n"
                "as_of: 2026-10-03\nconfidence: 0.6\n"
                "provenance:\n  row_id: 1ecd4a3b3e\n"
            )
            monkeypatch.setattr("helpers.graph.extract_relations.RELATIONS_DIR", Path(tmp))

            captured = []

            def fake_apply_edges(edges, **kw):
                captured.extend(edges)
                return type(
                    "R",
                    (),
                    {
                        "inserted": 0,
                        "skipped_fk": 0,
                        "skipped_suppressed": 0,
                    },
                )()

            from helpers.core.db import connect

            conn = connect()
            try:
                with mock.patch("helpers.graph.extract_relations.apply_edges", fake_apply_edges):
                    promote_relation_sidecars(conn, dry_run=True)
            finally:
                conn.close()

            assert captured[0].symmetric is False
            assert captured[0].edge_type == "acquired"

    def test_existing_edge_is_not_double_promoted(self, monkeypatch):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "X-acquired-Y.yaml"
            path.write_text(
                "edge_type: acquired\nsource: X\ntarget: Y\nas_of: 2026-10-03\n"
                "confidence: 0.6\nprovenance:\n  row_id: r1\n"
            )
            monkeypatch.setattr("helpers.graph.extract_relations.RELATIONS_DIR", Path(tmp))

            def fake_apply_edges(edges, **kw):
                # Edge already in graph_edges (INSERT OR IGNORE would skip)
                return type(
                    "R",
                    (),
                    {
                        "inserted": 0,
                        "skipped_fk": 0,
                        "skipped_suppressed": 0,
                    },
                )()

            from helpers.core.db import connect

            conn = connect()
            try:
                with mock.patch("helpers.graph.extract_relations.apply_edges", fake_apply_edges):
                    promoted, skipped = promote_relation_sidecars(conn, dry_run=True)
            finally:
                conn.close()
            # Already-existing edges are re-confirmed, not double-counted.
            assert promoted == 0
            assert skipped == 0
