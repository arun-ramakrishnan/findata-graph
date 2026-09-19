#!/usr/bin/env python3
"""Tests for the hypergraph incidence layer (hypergraph_incidence_hyx, 2026-09-13).

Three layers:
- Schema/bootstrap: the canonical DDL constants build the tables and the FK
  cascade holds (hyper_edges -> hyper_incidences, entities -> incidences).
- Backfill (derive_hyperedges.py): star + symmetric regrouping, dry-run
  counting, apply idempotence — on a synthetic tmp-file DB.
- Consumer (hyper_communities.py): degeneracy guard (singleton + giant) and
  seeded determinism on a toy incidence (fits are < 0.1 s at this size;
  hypergraphx is a pyproject dep, so no venv juggling).

Live-scale checks (1,165 companies) are NOT here — they live in the proposal
appendix (measured) and would re-couple unit tests to the live DB.

Run:
    pytest tests/test_hyper_incidence.py -v
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pyarrow as pa
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

from helpers.maintenance.migrate_to_graph_edges import (  # noqa: E402
    ENTITIES_DDL,
    GRAPH_EDGES_DDL,
    GRAPH_EDGES_INDEXES,
    HYPER_EDGES_DDL,
    HYPER_EDGES_INDEXES,
    HYPER_INCIDENCES_DDL,
    HYPER_INCIDENCES_INDEXES,
    QUOTES_DDL,
    QUOTES_INDEXES,
    EVENTS_DDL,
    EVENTS_INDEXES,
)
from helpers.graph import derive_hyperedges as dh  # noqa: E402
from helpers.graph import hyper_arrow as ha  # noqa: E402
from helpers.graph import hyper_hif as hh  # noqa: E402
from helpers.graph import hyper_communities as hc  # noqa: E402

# Bare names: the entities CHECK rejects company names ending in the
# Ltd/Pvt/Private suffixes, so the fixture avoids suffixes entirely.
CO = ["Alpha", "Beta", "Gamma", "Delta", "Eps"]


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    """Canonical schema (entities + graph_edges + hyper tables) + seed dyads."""
    c = sqlite3.connect(tmp_path / "hyper.db")
    c.execute("PRAGMA foreign_keys = ON")
    c.execute(ENTITIES_DDL)
    c.execute(GRAPH_EDGES_DDL)
    for idx in GRAPH_EDGES_INDEXES:
        c.execute(idx)
    c.execute(HYPER_EDGES_DDL)
    for idx in HYPER_EDGES_INDEXES:
        c.execute(idx)
    c.execute(HYPER_INCIDENCES_DDL)
    for idx in HYPER_INCIDENCES_INDEXES:
        c.execute(idx)
    c.execute(QUOTES_DDL)
    for idx in QUOTES_INDEXES:
        c.execute(idx)
    c.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
        [(n, "company") for n in CO]
        + [("Automotive", "sector"), ("EV_Transition", "theme"), ("India", "country")],
    )
    c.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, properties, source_ref) "
        "VALUES (?, ?, ?, ?, 'test')",
        [
            ("Alpha", "Automotive", "part_of", "{}"),
            ("Beta", "Automotive", "part_of", "{}"),
            ("Gamma", "Automotive", "part_of", "{}"),
            ("Alpha", "EV_Transition", "exposed_to", "{}"),
            ("Beta", "EV_Transition", "exposed_to", "{}"),
            ("Gamma", "India", "listed_in", "{}"),
            # symmetric source: same_group with a properties.group key
            ("Alpha", "Beta", "same_group", json.dumps({"group": "M Group"})),
            # symmetric source: co_mentioned_in with a properties.edition key
            ("Alpha", "Gamma", "co_mentioned_in", json.dumps({"edition": "Ed 1"})),
            ("Beta", "Gamma", "co_mentioned_in", json.dumps({"edition": "Ed 1"})),
        ],
    )
    # S8 seed: quotes drive the weighted edition regroup. Alpha quoted 3x in
    # Ed 1 (weight 3, member UNION with the co_mentioned set), Delta quoted
    # 2x in a concall-title "edition" (Ed 2), plus one sector-entity quote
    # that must NOT become a member (mis-capture filter).
    c.executemany(
        "INSERT INTO quotes (entity, quote_text, as_of_edition, source_ref) "
        "VALUES (?, ?, ?, 'test')",
        [
            ("Alpha", "q1", "Ed 1"),
            ("Alpha", "q2", "Ed 1"),
            ("Alpha", "q3", "Ed 1"),
            ("Delta", "q4", "Ed 2"),
            ("Delta", "q5", "Ed 2"),
            ("Automotive", "sector noise", "Ed 1"),
        ],
    )
    c.commit()
    return c


# --------------------------------------------------------------------------- #
# Schema                                                                       #
# --------------------------------------------------------------------------- #
class TestSchema:
    def test_hyper_tables_exist(self, conn):
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"hyper_edges", "hyper_incidences"} <= names

    def test_unique_edge_type_label(self, conn):
        conn.execute(
            "INSERT INTO hyper_edges (edge_type, label, source_ref) "
            "VALUES ('sector', 'Automotive', 't')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO hyper_edges (edge_type, label, source_ref) "
                "VALUES ('sector', 'Automotive', 't2')"
            )

    def test_incidence_cascade_on_hyperedge_delete(self, conn):
        cur = conn.execute(
            "INSERT INTO hyper_edges (edge_type, label, source_ref) "
            "VALUES ('sector', 'Automotive', 't')"
        )
        he_id = cur.lastrowid
        conn.execute(
            "INSERT INTO hyper_incidences (edge_id, entity_name) VALUES (?, 'Alpha')",
            (he_id,),
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM hyper_incidences WHERE edge_id = ?", (he_id,)
            ).fetchone()[0]
            == 1
        )
        conn.execute("DELETE FROM hyper_edges WHERE id = ?", (he_id,))
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM hyper_incidences WHERE edge_id = ?", (he_id,)
            ).fetchone()[0]
            == 0
        )

    def test_direction_check_constraint(self, conn):
        cur = conn.execute(
            "INSERT INTO hyper_edges (edge_type, label, source_ref) VALUES ('edition', 'E1', 't')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO hyper_incidences (edge_id, entity_name, direction) "
                "VALUES (?, 'Alpha', 'sideways')",
                (cur.lastrowid,),
            )

    def test_bad_properties_json_rejected(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO hyper_edges (edge_type, label, properties, source_ref) "
                "VALUES ('sector', 'S', '{not json', 't')"
            )


# --------------------------------------------------------------------------- #
# Backfill                                                                     #
# --------------------------------------------------------------------------- #
class TestBackfill:
    def test_star_regroup(self, conn):
        hyper, weights = dh.collect_hyperedges(conn)
        assert hyper["sector"] == {"Automotive": {"Alpha", "Beta", "Gamma"}}
        assert hyper["theme"] == {"EV_Transition": {"Alpha", "Beta"}}
        assert hyper["country"] == {"India": {"Gamma"}}

    def test_symmetric_regroup_both_endpoints(self, conn):
        hyper, weights = dh.collect_hyperedges(conn)
        assert hyper["group"] == {"M Group": {"Alpha", "Beta"}}
        # S8: edition = UNION of co_mentioned members and quoted companies;
        # sector-entity quotes filtered out; weight = quote count.
        assert hyper["edition"] == {
            "Ed 1": {"Alpha", "Beta", "Gamma"},
            "Ed 2": {"Delta"},
        }
        assert weights == {
            ("edition", "Ed 1", "Alpha"): 3.0,
            ("edition", "Ed 2", "Delta"): 2.0,
        }

    def test_dry_run_writes_nothing_then_apply_idempotent(self, conn):
        hyper, weights = dh.collect_hyperedges(conn)
        stats = dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=True)
        # 6 hyperedges would be new (sector+theme+country+group+2 editions).
        assert sum(e for e, _ in stats.values()) == 6
        # memberships: 3 + 2 + 1 + 2 + 3 + 1
        assert sum(i for _, i in stats.values()) == 12
        assert conn.execute("SELECT COUNT(*) FROM hyper_edges").fetchone()[0] == 0

        stats1 = dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False)
        assert sum(e for e, _ in stats1.values()) == 6
        assert conn.execute("SELECT COUNT(*) FROM hyper_incidences").fetchone()[0] == 12
        # S8: quote-count weights land on the incidence rows.
        w = conn.execute(
            "SELECT hi.weight FROM hyper_incidences hi JOIN hyper_edges he "
            "ON he.id = hi.edge_id WHERE he.edge_type = 'edition' "
            "AND he.label = 'Ed 1' AND hi.entity_name = 'Alpha'"
        ).fetchone()[0]
        assert w == 3.0

        stats2 = dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False)
        assert sum(e for e, _ in stats2.values()) == 0
        assert sum(i for _, i in stats2.values()) == 0

    def test_source_ref_records_upstream(self, conn):
        hyper, weights = dh.collect_hyperedges(conn)
        dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False)
        refs = dict(
            conn.execute("SELECT edge_type || ':' || label, source_ref FROM hyper_edges").fetchall()
        )
        assert refs["sector:Automotive"] == "derive:hyperedges:part_of"
        assert refs["edition:Ed 1"] == "derive:hyperedges:co_mentioned_in+quotes"

    def test_load_incidence_labels_prefixed(self, conn, tmp_path):
        hyper, weights = dh.collect_hyperedges(conn)
        dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False)
        conn.commit()
        conn.close()
        tbl = ha.load_incidence_arrow(
            ["sector", "theme"], source="live", db_path=tmp_path / "hyper.db"
        )
        inc = ha.incidence_dict(tbl)
        assert inc["sector:Automotive"] == ["Alpha", "Beta", "Gamma"]
        assert inc["theme:EV_Transition"] == ["Alpha", "Beta"]


class TestHifLane:
    """S18(d): HIF export — canonical zstd parquet + transient JSON skin."""

    def test_parquet_roundtrip_fidelity(self, conn, tmp_path):
        import pyarrow.parquet as pq

        hyper, weights = dh.collect_hyperedges(conn)
        dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False)
        conn.commit()
        db = tmp_path / "hyper.db"
        conn.close()
        out = tmp_path / "hif"
        hh.write_hif_parquet(["sector", "edition"], out, db_path=db)
        stats = hh.validate_hif_roundtrip(["sector", "edition"], out, db_path=db)
        assert stats["edges"] == 3 and stats["incidences"] > 0
        # zstd codec on disk + footer network type
        md = pq.read_metadata(out / "hif_edges.parquet")
        codec = md.row_group(0).column(0).compression
        assert "ZSTD" in codec.upper()
        assert (md.metadata or {}).get(b"network_type") == b"undirected"
        # weights survive the parquet boundary
        incs = hh.read_hif_parquet(out)["incidences"].to_pylist()
        n_w = sum(1 for r in incs if r["weight"] is not None)
        assert n_w == stats["weighted"] > 0

    def test_json_skin_is_transient_hgx_skin(self, conn, tmp_path):
        hyper, weights = dh.collect_hyperedges(conn)
        dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False)
        conn.commit()
        db = tmp_path / "hyper.db"
        conn.close()
        out = tmp_path / "hif"
        hh.write_hif_parquet(["sector"], out, db_path=db)
        jp = hh.write_hif_json(out, tmp_path / "skin.json")
        data = json.loads(jp.read_text())
        tbls = hh.read_hif_parquet(out)
        assert data["type"] == "undirected"
        assert len(data["nodes"]) == tbls["nodes"].num_rows
        assert len(data["edges"]) == tbls["edges"].num_rows
        assert len(data["incidences"]) == tbls["incidences"].num_rows
        read_hif = pytest.importorskip("hypergraphx.readwrite.hif", reason="hgx missing").read_hif
        hg = read_hif(str(jp))
        assert hg.num_nodes() == tbls["nodes"].num_rows


class TestHyperArrowLoader:
    """S18(b/c/e): canonical Arrow loader — live/snapshot parity + consumers."""

    @staticmethod
    def _seed(conn):
        hyper, weights = dh.collect_hyperedges(conn)
        dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False)
        conn.commit()

    def test_live_snapshot_parity(self, conn, tmp_path):
        """Same (edge_type, label, node, weight) multiset from both sources."""
        import pyarrow.parquet as pq

        self._seed(conn)
        db = tmp_path / "hyper.db"
        pq_dir = tmp_path / "parquet"
        pq_dir.mkdir()
        # Export the two at-rest tables the snapshot lane owns (zstd, §10).
        src = sqlite3.connect(db)
        for table in ("hyper_edges", "hyper_incidences"):
            rows = src.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
            cols = [d[0] for d in src.execute(f"SELECT * FROM {table} LIMIT 0").description]  # noqa: S608
            t = pa.table({c: pa.array([r[i] for r in rows]) for i, c in enumerate(cols)})
            pq.write_table(t, pq_dir / f"{table}.parquet", compression="zstd")
        src.close()
        conn.close()

        live = ha.load_incidence_arrow(["sector", "edition"], source="live", db_path=db)
        snap = ha.load_incidence_arrow(["sector", "edition"], source="snapshot", parquet_dir=pq_dir)
        assert live.num_rows == snap.num_rows

        def key(t):
            return sorted(
                zip(
                    t["edge_type"].to_pylist(),
                    t["label"].to_pylist(),
                    t["node"].to_pylist(),
                    t["weight"].to_pylist(),
                )
            )

        assert key(live) == key(snap)
        # weights ride through both paths (S8 edition quote counts)
        assert live["weight"].null_count < live.num_rows

    def test_schema_and_order(self, conn, tmp_path):
        self._seed(conn)
        conn.close()
        tbl = ha.load_incidence_arrow(["sector"], db_path=tmp_path / "hyper.db")
        assert tbl.schema.names == ["edge_type", "label", "node", "weight"]
        assert pa.types.is_float64(tbl.schema.field("weight").type)
        triples = list(zip(tbl["label"].to_pylist(), tbl["node"].to_pylist()))
        assert triples == sorted(triples)

    def test_empty_sources(self):
        tbl = ha.load_incidence_arrow([])
        assert tbl.num_rows == 0 and tbl.schema.names == ["edge_type", "label", "node", "weight"]

    def test_bad_source_raises(self):
        with pytest.raises(ValueError, match="live.*snapshot"):
            ha.load_incidence_arrow(["sector"], source="csv")

    def test_incidence_query_in_flight(self, conn, tmp_path):
        self._seed(conn)
        conn.close()
        tbl = ha.load_incidence_arrow(["sector"], db_path=tmp_path / "hyper.db")
        res = ha.incidence_query(
            tbl, "SELECT label, count(*) AS n FROM incidence GROUP BY 1 ORDER BY 1"
        )
        d = dict(zip(res["label"].to_pylist(), res["n"].to_pylist()))
        assert d["Automotive"] == 3

    def test_hypergraph_boundary(self, conn, tmp_path):
        hgx = pytest.importorskip("hypergraphx")
        self._seed(conn)
        conn.close()
        tbl = ha.load_incidence_arrow(["sector"], db_path=tmp_path / "hyper.db")
        hg, labels = ha.hypergraph_from_arrow(tbl)
        assert isinstance(hg, hgx.Hypergraph)
        assert hg.num_edges() == len(tbl["label"].unique().to_pylist())
        assert any(k.startswith("sector:") for k in labels.values())

    def test_arrow_direct_dict_boundary(self, conn, tmp_path):
        """S18(b) exit: consumers use load_incidence_arrow + incidence_dict."""
        self._seed(conn)
        conn.commit()
        db = tmp_path / "hyper.db"
        conn.close()
        tbl = ha.load_incidence_arrow(["sector", "theme"], source="live", db_path=db)
        via_boundary = ha.incidence_dict(tbl)
        assert via_boundary["sector:Automotive"] == ["Alpha", "Beta", "Gamma"]
        assert via_boundary["theme:EV_Transition"] == ["Alpha", "Beta"]


class TestIndustryLane:
    """S5: the industry frontmatter source."""

    NOTE = """---
title: {title}
type: company
industry: {industry}
---

# {title}

Prose line one.
Prose line two.
"""

    @staticmethod
    def _vault(tmp_path: Path) -> Path:
        root = tmp_path / "Companies"
        root.mkdir()
        (root / "Alpha.md").write_text(
            TestIndustryLane.NOTE.format(title="Alpha", industry="Auto Parts")
        )
        (root / "Beta.md").write_text(
            TestIndustryLane.NOTE.format(title="Beta", industry="Auto Parts")
        )
        (root / "Gamma.md").write_text(
            TestIndustryLane.NOTE.format(title="Gamma", industry="Banks - Regional")
        )
        # industry: null -> skipped
        (root / "Null.md").write_text(
            TestIndustryLane.NOTE.format(title="Null Co", industry="null").replace(
                "industry: null", "industry: null"
            )
        )
        (root / "NoField.md").write_text(
            "---\ntitle: NF\ntype: company\n---\n\n# NF\n\nline\nline\n"
        )
        return root

    def test_extract_by_stem(self, tmp_path):
        root = self._vault(tmp_path)
        got = dh.extract_industry_membership(root=root)
        assert got == {"Auto Parts": {"Alpha", "Beta"}, "Banks - Regional": {"Gamma"}}

    def test_extract_path_to_name_join(self, tmp_path):
        root = self._vault(tmp_path)
        # tmp-vault fallback keys are ROOT-relative (prod keys are repo-relative)
        p2n = {"Alpha.md": "Alpha Ltd Co X"}  # only one resolvable note
        got = dh.extract_industry_membership(root=root, path_to_name=p2n)
        assert got == {"Auto Parts": {"Alpha Ltd Co X"}}

    def test_collect_skips_scan_without_file_path_entities(self, conn):
        # conn fixture companies have no file_path -> hermetic: industry = {}
        got, _w = dh.collect_hyperedges(conn, companies_dir=Path("/nonexistent"))
        assert got["industry"] == {}

    def test_collect_includes_industry_via_file_path_join(self, conn, tmp_path):
        root = self._vault(tmp_path)
        conn.executemany(
            "UPDATE entities SET file_path = ? WHERE name = ?",
            [
                ("Alpha.md", "Alpha"),
                ("Beta.md", "Beta"),
                ("Gamma.md", "Gamma"),
            ],
        )
        conn.commit()
        got, _w = dh.collect_hyperedges(conn, companies_dir=root)
        assert got["industry"] == {
            "Auto Parts": {"Alpha", "Beta"},
            "Banks - Regional": {"Gamma"},
        }


# --------------------------------------------------------------------------- #
# Consumer                                                                     #
# --------------------------------------------------------------------------- #
class TestConsumer:
    INCIDENCE = {
        "sector:Auto": ["A", "B", "C", "D"],
        "sector:Tech": ["E", "F", "G", "H"],
        "theme:EV": ["A", "B", "E"],
        "theme:Cloud": ["F", "G"],
    }

    def test_guard_drops_singletons(self):
        # max_frac=1.0 keeps the 3-4 member toy hyperedges under the giant
        # threshold (8 nodes) so ONLY the singleton path is exercised.
        inc = dict(self.INCIDENCE)
        inc["country:Austria"] = ["A"]  # singleton -> 0/0 in the EM
        kept, giants, singletons = hc._exclude_degenerate(inc, allow_giant=False, max_frac=1.0)
        assert singletons == ["country:Austria"]
        assert kept == self.INCIDENCE
        assert giants == []

    def test_guard_drops_giant_unless_allowed(self):
        # max_frac=0.5 -> threshold 5 of 10 nodes: only the 10-member lane
        # is a giant; the 3-4 member toy hyperedges stay.
        inc = dict(self.INCIDENCE)
        inc["country:India"] = list("ABCDEFGHIJ")
        kept, giants, singletons = hc._exclude_degenerate(inc, allow_giant=False, max_frac=0.5)
        assert giants == ["country:India"]
        assert "country:India" not in kept
        assert singletons == []
        kept2, giants2, _s2 = hc._exclude_degenerate(inc, allow_giant=True, max_frac=0.5)
        assert giants2 == [] and "country:India" in kept2

    def test_fit_deterministic_and_shaped(self):
        m1 = hc.fit_communities(self.INCIDENCE, k=2, seed=42, n_iter=200)
        m2 = hc.fit_communities(self.INCIDENCE, k=2, seed=42, n_iter=200)
        assert m1 == m2
        assert set(m1) == {"A", "B", "C", "D", "E", "F", "G", "H"}
        for payload in m1.values():
            assert payload["k"] == 2 and payload["seed"] == 42
            assert payload["block"] in (0, 1)
            # row-normalised memberships sum to ~1
            assert abs(sum(payload["memberships"].values()) - 1.0) < 1e-3

    def test_sweep_k_rows_and_bounds(self):
        rows = hc.sweep_k(self.INCIDENCE, ks=[2, 3], seed=42, n_iter=100)
        assert [r["k"] for r in rows] == [2, 3]
        for r in rows:
            assert isinstance(r["log_likelihood"], float)
            assert 1 <= r["effective_blocks"] <= r["k"]
            # S13: BIC present, penalised (higher than -2*loglik alone)
            assert isinstance(r["bic"], float)
            assert r["bic"] > -2 * r["log_likelihood"]
        rows2 = hc.sweep_k(self.INCIDENCE, ks=[2, 3], seed=42, n_iter=100)
        # deterministic under a fixed seed (fit_s is wall-clock — excluded)
        strip = lambda rs: [{k: v for k, v in r.items() if k != "fit_s"} for r in rs]  # noqa: E731
        assert strip(rows) == strip(rows2)

    def test_fit_breaks_on_singleton(self):
        """The measured failure shape: singleton hyperedges NaN the EM.

        Pinning it as a regression signal: with the guard BYPASSED the fit
        collapses (all nodes in one block). If HGX ever fixes the 0/0, this
        test may start failing — flip it to assert health then.
        """
        inc = dict(self.INCIDENCE)
        inc["country:Austria"] = ["A"]
        m = hc.fit_communities(inc, k=2, seed=42, n_iter=200)
        blocks = {p["block"] for p in m.values()}
        assert blocks == {0}  # collapsed: the degeneracy the guard exists for


# --------------------------------------------------------------------------- #
# Longest chains (stats.longest_chains)                                       #
# --------------------------------------------------------------------------- #
class TestLongestChains:
    """stats.py longest-chains section: pure function of the connection."""

    @staticmethod
    def _chain_db(tmp_path):
        import sqlite3 as s3

        from helpers.maintenance.migrate_to_graph_edges import (
            ENTITIES_DDL,
            GRAPH_EDGES_DDL,
        )

        c = s3.connect(tmp_path / "chains.db")
        c.execute("PRAGMA foreign_keys = ON")
        c.execute(ENTITIES_DDL)
        c.execute(GRAPH_EDGES_DDL)
        ents = [
            ("A", "company"),
            ("B", "company"),
            ("C", "company"),
            ("D", "company"),
            ("Sec", "sector"),
            ("US", "country"),
        ]
        c.executemany("INSERT INTO entities (name, entity_type) VALUES (?, ?)", ents)
        # a 3-hop activity chain + membership stars that would shortcut it
        c.executemany(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref) VALUES (?,?,?,'t')",
            [
                ("A", "B", "competes_with"),
                ("B", "C", "invested_in"),
                ("A", "Sec", "part_of"),
                ("C", "Sec", "part_of"),
                ("D", "US", "listed_in"),
            ],  # D isolated in the activity view
        )
        c.commit()
        return c

    def test_lines_and_views(self, tmp_path):
        from helpers.graph.stats import longest_chains

        c = self._chain_db(tmp_path)
        try:
            lines = longest_chains(c, top_k=2)
        finally:
            c.close()
        text = "\n".join(lines)
        assert "ALL edges" in text and "ACTIVITY edges" in text
        # Longest pair is A<->C: 2 hops via Sec (ALL view) and via B (ACTIVITY).
        assert "d=2  A  <->  C" in text
        # The #1 chain must carry the family per hop.
        assert "competes_with" in text and "invested_in" in text
        assert "#1 chain" in text and "chain composition" in text
        # ACTIVITY view shatters more (D and US become isolates once
        # listed_in is excluded; ALL keeps them as a 2-node component).
        all_line = next(ln for ln in lines if ln.startswith("  ALL edges"))
        act_line = next(ln for ln in lines if ln.startswith("  ACTIVITY edges"))
        m_all = re.search(r"(\d+) components", all_line)
        m_act = re.search(r"(\d+) components", act_line)
        assert m_all is not None and m_act is not None
        n_all, n_act = int(m_all.group(1)), int(m_act.group(1))
        assert n_act > n_all

    def test_degrades_on_empty(self, tmp_path):
        from helpers.graph.stats import longest_chains
        import sqlite3 as s3
        from helpers.maintenance.migrate_to_graph_edges import (
            ENTITIES_DDL,
            GRAPH_EDGES_DDL,
        )

        c = s3.connect(tmp_path / "empty.db")
        c.execute(ENTITIES_DDL)
        c.execute(GRAPH_EDGES_DDL)
        c.execute("INSERT INTO entities (name, entity_type) VALUES ('A', 'company')")
        c.commit()
        try:
            lines = longest_chains(c)
        finally:
            c.close()
        assert all("no edges" in ln for ln in lines)

    def test_capped_sampling_deterministic_and_labeled(self, tmp_path):
        """S3 (hgx_first_scaling): above the cap the diagnostic samples
        stride roots instead of the full n x n matrix — labeled as lower
        bounds, deterministic across runs, structurally complete."""
        import sqlite3 as s3
        from helpers.graph.stats import longest_chains

        from helpers.maintenance.migrate_to_graph_edges import (
            ENTITIES_DDL,
            GRAPH_EDGES_DDL,
        )

        c = s3.connect(tmp_path / "capped.db")
        c.execute(ENTITIES_DDL)
        c.execute(GRAPH_EDGES_DDL)
        # A 12-node path graph; cap at 5 roots forces the sampled path.
        n = 12
        c.executemany(
            "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
            [(f"N{i:02d}",) for i in range(n)],
        )
        c.executemany(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref)"
            " VALUES (?,?, 'competes_with', 't')",
            [(f"N{i:02d}", f"N{i + 1:02d}") for i in range(n - 1)],
        )
        c.commit()
        try:
            first = longest_chains(c, top_k=3, max_exact=5)
            second = longest_chains(c, top_k=3, max_exact=5)
        finally:
            c.close()
        text = "\n".join(first)
        assert "SAMPLED 5/12 roots (cap 5)" in text
        assert "d >= " in text and "lower bounds" in text
        assert first == second  # stride sample: deterministic, no RNG
        # Structure survives sampling: components, pairs, chain lines.
        assert "1 components" in text and "d=" in text

    def test_below_cap_stays_exact_unlabeled(self, tmp_path):
        """Below the cap nothing changes — no SAMPLED annotation appears."""
        import sqlite3 as s3
        from helpers.graph.stats import longest_chains

        from helpers.maintenance.migrate_to_graph_edges import (
            ENTITIES_DDL,
            GRAPH_EDGES_DDL,
        )

        c = s3.connect(tmp_path / "exact.db")
        c.execute(ENTITIES_DDL)
        c.execute(GRAPH_EDGES_DDL)
        c.executemany(
            "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
            [("A",), ("B",), ("C",)],
        )
        c.executemany(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref)"
            " VALUES (?,?, 'competes_with', 't')",
            [("A", "B"), ("B", "C")],
        )
        c.commit()
        try:
            lines = longest_chains(c, top_k=2, max_exact=3000)
        finally:
            c.close()
        text = "\n".join(lines)
        assert "SAMPLED" not in text
        assert "d=2  A  <->  C" in text  # exact answer preserved

    def test_isolated_entities_excluded_from_universe(self, tmp_path):
        """S1 pin (live_inv_longest_chains): isolated entities must not
        enter the longest_chains node universe — they add n^2 matrix
        cells while contributing nothing to chains (the D19 stub blowup).
        Output is byte-identical with vs without isolates in `entities`."""
        import sqlite3 as s3
        from helpers.graph.stats import longest_chains
        from helpers.maintenance.migrate_to_graph_edges import (
            ENTITIES_DDL,
            GRAPH_EDGES_DDL,
        )

        def _db(with_isolates: bool):
            c = s3.connect(tmp_path / ("iso.db" if with_isolates else "plain.db"))
            c.execute(ENTITIES_DDL)
            c.execute(GRAPH_EDGES_DDL)
            c.executemany(
                "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
                [("A",), ("B",), ("C",)] + ([("ISO1",), ("ISO2",)] if with_isolates else []),
            )
            c.executemany(
                "INSERT INTO graph_edges (source, target, edge_type, source_ref) VALUES (?,?,?,'t')",
                [("A", "B", "competes_with"), ("B", "C", "invested_in")],
            )
            c.commit()
            return c

        try:
            with_iso = longest_chains(_db(True))
            plain = longest_chains(_db(False))
        finally:
            pass
        assert with_iso == plain
        text = "\n".join(plain)
        assert "ISO1" not in text and "ISO2" not in text
        assert "d=2" not in text or "A  <->  C" in text


# --------------------------------------------------------------------------- #
# S9: counterparty resolution + event hyperedges                              #
# --------------------------------------------------------------------------- #
class TestCounterpartyResolution:
    @staticmethod
    def _events_conn(conn):
        conn.execute(EVENTS_DDL)
        for idx in EVENTS_INDEXES:
            conn.execute(idx)
        conn.executemany(
            "INSERT INTO events (entity, event_type, event_date, counterparty, source_ref) "
            "VALUES (?, ?, ?, ?, 't')",
            [
                ("Alpha", "acquisition", "2026-01-01", "Beta"),
                ("Gamma", "jv", None, "Ghost CP Co"),  # unresolvable
            ],
        )
        conn.commit()
        return conn

    def test_column_added_by_migrate_shape(self):
        # the ALTER step lives in migrate(); here we just pin the DDL constant
        assert "counterparty_entity" in EVENTS_DDL

    def test_resolve_dry_run_writes_nothing(self, conn):
        c = self._events_conn(conn)
        n_ok, n_bad, names = dh.resolve_counterparties(c, dry_run=True)
        assert (n_ok, n_bad) == (1, 1) and names == ["Ghost CP Co"]
        assert (
            c.execute(
                "SELECT COUNT(*) FROM events WHERE counterparty_entity IS NOT NULL"
            ).fetchone()[0]
            == 0
        )

    def test_resolve_apply_writes_fk_and_worklist(self, conn, tmp_path, monkeypatch):
        c = self._events_conn(conn)
        monkeypatch.setattr(dh, "_REPO_ROOT", tmp_path)
        n_ok, n_bad, _ = dh.resolve_counterparties(c, dry_run=False)
        assert (n_ok, n_bad) == (1, 1)
        row = c.execute("SELECT counterparty_entity FROM events WHERE entity = 'Alpha'").fetchone()
        assert row[0] == "Beta"
        wl = tmp_path / "findata" / "Misc" / "counterparty_worklist.json"
        assert json.loads(wl.read_text())["unresolved"] == ["Ghost CP Co"]

    def test_event_hyperedges_per_row(self, conn):
        c = self._events_conn(conn)
        dh.resolve_counterparties(c, dry_run=False)
        hyper, _w = dh.collect_hyperedges(c, companies_dir=Path("/nonexistent"))
        # only the resolved event becomes a hyperedge: {Alpha, Beta}, size 2
        assert hyper["event"] == {"acquisition:1": {"Alpha", "Beta"}}

    def test_no_events_table_degrades(self, conn):
        n_ok, n_bad, names = dh.resolve_counterparties(conn, dry_run=True)
        assert (n_ok, n_bad, names) == (0, 0, [])


# --------------------------------------------------------------------------- #
# S10: JV venture capture + jv regroup                                        #
# --------------------------------------------------------------------------- #
class TestVentureCapture:
    def test_quoted_name(self):
        from helpers.graph.extract_relations import capture_venture_name

        assert (
            capture_venture_name('announced the JV "JioBlackRock AMC" with partners')
            == "JioBlackRock AMC"
        )

    def test_formed_shape(self):
        from helpers.graph.extract_relations import capture_venture_name

        assert (
            capture_venture_name("The company has formed Acme Batteries Ltd with Tongte")
            == "Acme Batteries Ltd"
        )

    def test_jv_arm_shape(self):
        from helpers.graph.extract_relations import capture_venture_name

        assert (
            capture_venture_name("through its JV arm Siemens Innolight based in")
            == "Siemens Innolight"
        )

    def test_bare_partner_mention_never_matches(self):
        from helpers.graph.extract_relations import capture_venture_name

        # the measured live-corpus shape: partners only, no venture name
        assert (
            capture_venture_name(
                "The company has formed a joint venture with Thai Union Frozen Products PCL"
            )
            is None
        )
        assert capture_venture_name("partnership with L&T for breakwater wall") is None
        assert capture_venture_name("") is None

    def test_jv_hyperedges_regroup_by_venture(self, conn):
        conn.executemany(
            "INSERT INTO graph_edges (source, target, edge_type, properties, source_ref) "
            "VALUES (?, ?, 'jv_with', ?, 'test')",
            [
                ("Alpha", "Beta", json.dumps({"venture": "AB Motors"})),
                ("Beta", "Gamma", json.dumps({"venture": "AB Motors"})),
                ("Alpha", "Gamma", json.dumps({"venture": "AB Motors"})),
            ],
        )
        conn.commit()
        hyper, _w = dh.collect_hyperedges(conn, companies_dir=Path("/nonexistent"))
        # one k=3 hyperedge over the venture's three partners
        assert hyper["jv"] == {"AB Motors": {"Alpha", "Beta", "Gamma"}}


# --------------------------------------------------------------------------- #
# S12: HGX alternate centralities                                              #
# --------------------------------------------------------------------------- #
class TestHyperCentralities:
    INC = {
        "sector:Auto": ["A", "B", "C", "D"],
        "sector:Tech": ["E", "F", "G", "H"],
        "theme:EV": ["A", "B", "E"],
        "industry:Parts": ["A", "B", "F"],
    }

    def test_all_three_lanes_and_meta(self):
        from helpers.graph.hyper_centralities import compute_centralities

        # allow_giant: the toy has 8 nodes, so the 25% guard would eat every
        # 3+ member hyperedge (guard itself is covered in TestConsumer).
        res = compute_centralities(self.INC, s=1, allow_giant=True)
        meta = res.pop("_meta")
        assert meta["hyperedges"] == 4 and meta["dropped_singletons"] == 0
        # ho_pagerank: per-node stationary distribution, sums to ~1
        assert abs(sum(res["ho_pagerank"].values()) - 1.0) < 1e-6
        assert set(res["ho_pagerank"]) == {"A", "B", "C", "D", "E", "F", "G", "H"}
        # s-lanes: per-node means over incident hyperedges
        for m in ("s_betweenness", "s_closeness"):
            assert set(res[m]) == set(res["ho_pagerank"])
            assert all(v >= 0 for v in res[m].values())

    def test_eigen_uniform_runs_and_seeded(self):
        """S15: uniform family -> CEC/ZEC/HEC node-keyed + deterministic."""
        from helpers.graph.hyper_centralities import compute_centralities

        inc = {  # all edges size 2 -> uniform; single connected component
            "pair:AB": ["A", "B"],
            "pair:BC": ["B", "C"],
            "pair:CD": ["C", "D"],
            "pair:DA": ["D", "A"],
        }
        r1 = compute_centralities(inc, s=1, allow_giant=True)
        r2 = compute_centralities(inc, s=1, allow_giant=True)
        m1 = r1.pop("_meta")
        r2.pop("_meta")
        assert m1["eigen_uniform"] is True
        for m in ("eigen_cec", "eigen_zec", "eigen_hec"):
            assert set(r1[m]) == {"A", "B", "C", "D"}, m
            assert all(v >= 0 for v in r1[m].values()), m
            assert r1[m] == r2[m], f"{m} not seeded-deterministic"

    def test_eigen_non_uniform_skips_with_note(self):
        from helpers.graph.hyper_centralities import compute_centralities

        res = compute_centralities(self.INC, s=1, allow_giant=True)
        meta = res.pop("_meta")
        assert meta["eigen_uniform"] is False
        assert "uniform-hypergraph definitions" in meta["eigen_skipped"]
        assert "eigen_cec" not in res and "eigen_zec" not in res and "eigen_hec" not in res

    def test_singleton_guard_applies(self):
        from helpers.graph.hyper_centralities import compute_centralities

        inc = dict(self.INC)
        inc["country:Austria"] = ["A"]  # would 0/0 the walk machinery
        res = compute_centralities(inc, s=1, allow_giant=True)
        assert res["_meta"]["dropped_singletons"] == 1

    def test_deterministic(self):
        from helpers.graph.hyper_centralities import compute_centralities

        r1 = compute_centralities(self.INC, s=1, allow_giant=True)
        r2 = compute_centralities(self.INC, s=1, allow_giant=True)
        assert r1 == r2


# --------------------------------------------------------------------------- #
# S11: industry -> sub_sector mapping                                          #
# --------------------------------------------------------------------------- #
class TestSubSectorMapping:
    HYPER = {
        "industry": {
            "Specialty Chemicals": {"A", "B"},
            "Steel": {"C"},
            # S17 (2026-09-13): Auto Parts now maps to Auto_Ancillary
            "Auto Parts": {"D", "E"},
            "Banks - Regional": {"F"},  # unmapped, carries a suggestion
            "Metal Fabrication": {"G"},  # unmapped (no canonical node)
        }
    }
    VALID = {
        "A",
        "B",
        "Micron Technology",
        "C",
        "D",
        "E",
        "F",
        "G",
        "Specialty_Chemicals",
        "Iron_and_Steel",
        "Auto_Ancillary",
    }

    def test_union_and_unmapped(self):
        groups, unmapped, _unmapped_authored = dh.derive_sub_sectors(self.HYPER, self.VALID)
        assert groups["Specialty_Chemicals"] == {"A", "B"}
        assert groups["Iron_and_Steel"] == {"C"}
        assert groups["Auto_Ancillary"] == {"D", "E"}  # S17 alias
        # unmapped sorted by member count desc; suggestion attached when known
        assert [u[0] for u in unmapped] == ["Banks - Regional", "Metal Fabrication"]
        sugg = {u[0]: u[2] for u in unmapped}
        assert "Banks" in sugg["Banks - Regional"]

    # ------------------------------------------------------------------ #
    # D7: authored subsector precedence                                    #
    # ------------------------------------------------------------------ #
    def test_authored_is_canonical_and_exclusive(self):
        groups, _un, unmapped_authored = dh.derive_sub_sectors(
            self.HYPER,
            self.VALID,
            authored={"apparel retail": {"A"}},  # A alias-derives to Specialty_Chemicals
            sub_sector_entities={"Specialty_Chemicals", "Iron_and_Steel", "Apparel_Retail"},
        )
        assert unmapped_authored == []
        assert groups["Apparel_Retail"] == {"A"}  # normalized hand value
        assert "A" not in groups["Specialty_Chemicals"]  # moved out, not unioned
        assert groups["Specialty_Chemicals"] == {"B"}  # alias sibling untouched

    def test_authored_unknown_value_worklisted_not_fatal(self):
        groups, _un, unmapped_authored = dh.derive_sub_sectors(
            self.HYPER,
            self.VALID,
            authored={"Not A Subsector": {"A"}},
            sub_sector_entities={"Specialty_Chemicals"},
        )
        assert unmapped_authored == [("Not A Subsector", 1)]
        # membership stays alias-derived — visible, not silent, not fatal
        assert groups["Specialty_Chemicals"] == {"A", "B"}

    def test_authored_can_create_solo_membership(self):
        # a company in an UNMAPPED industry can be authored directly
        groups, _un, _ua = dh.derive_sub_sectors(
            self.HYPER,
            self.VALID,
            authored={"Iron_and_Steel": {"F"}},  # F's industry is unmapped Banks - Regional
            sub_sector_entities={"Iron_and_Steel"},
        )
        assert groups["Iron_and_Steel"] == {"C", "F"}

    def test_extract_subsector_membership_reads_field(self, tmp_path):
        d = tmp_path / "Companies" / "X"
        d.mkdir(parents=True)
        (d / "Acme.md").write_text("---\ntype: company\nsubsector: Apparel_Retail\n---\n# Acme\n")
        (d / "Beta.md").write_text("---\ntype: company\nsubsector: null\n---\n# Beta\n")
        (d / "Gamma.md").write_text("---\ntype: company\n---\n# Gamma\n")
        got = dh.extract_subsector_membership(root=tmp_path / "Companies")
        assert got == {"Apparel_Retail": {"Acme"}}

    def test_company_map_shape(self):
        # D11: the version-controlled edge lane — non-empty string lists
        assert dh.COMPANY_SUB_SECTORS
        for sub, members in dh.COMPANY_SUB_SECTORS.items():
            assert isinstance(sub, str) and sub
            assert members and all(isinstance(m, str) and m for m in members)
            assert len(set(members)) == len(members)

    def test_map_rides_authored_precedence(self):
        # a map entry overrides the alias union exactly like a note field
        groups, _un, unmapped_authored = dh.derive_sub_sectors(
            self.HYPER,
            self.VALID,
            authored={k: set(v) for k, v in dh.COMPANY_SUB_SECTORS.items() if k == "Memory"},
            sub_sector_entities={"Memory"},
        )
        assert groups["Memory"] == {"Micron Technology"}

    def test_worklist_carries_authored_section(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dh, "_REPO_ROOT", tmp_path)
        wl = dh._write_subsector_worklist([("Solar", 2, "new node")], [("Typo Land", 1)])
        data = json.loads(wl.read_text())
        assert data["unmapped"][0]["label"] == "Solar"
        assert data["unmapped_authored"] == [{"value": "Typo Land", "members": 1}]

    def test_stale_alias_raises(self):
        hyper = {"industry": {"Solar": {"A"}}}  # Solar -> Solar alias exists
        bad = set(self.VALID)
        bad.discard("Solar")
        with pytest.raises(ValueError, match="stale alias"):
            dh.derive_sub_sectors(hyper, bad)

    def test_upstream_is_industry(self):
        assert dh._upstream_types("sub_sector") == ["industry"]

    def test_apply_writes_and_worklist(self, conn, tmp_path, monkeypatch):
        monkeypatch.setattr(dh, "_REPO_ROOT", tmp_path)
        conn.executemany(
            "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
            [("Specialty_Chemicals", "sub_sector")],  # Alpha/Beta from fixture
        )
        conn.commit()
        derived = dh.derive_sub_sectors(
            {"industry": {"Specialty Chemicals": {"Alpha", "Beta"}}},
            {"Alpha", "Beta", "Specialty_Chemicals"},
        )[0]
        # D7: authored arg optional; no authored -> identical result, empty 3rd
        assert derived == {"Specialty_Chemicals": {"Alpha", "Beta"}}
        hyper = {"sub_sector": derived}
        stats = dh.apply_hyperedges(hyper, conn=conn, dry_run=False)
        assert stats["sub_sector"] == (1, 2)
        # idempotent
        stats2 = dh.apply_hyperedges(hyper, conn=conn, dry_run=False)
        assert stats2["sub_sector"] == (0, 0)
        # worklist writer mirrors counterparty_worklist
        wl = dh._write_subsector_worklist([("Auto Parts", 45, "new sub_sector Auto_Ancillary")])
        assert wl.parent.parent.name == "findata"
        payload = json.loads(wl.read_text())
        assert payload["count"] == 1 and payload["unmapped"][0]["members"] == 45


# --------------------------------------------------------------------------- #
# S14: weighted higher-order walk                                              #
# --------------------------------------------------------------------------- #
class TestWeightedWalk:
    MEM = {"e1": {"A", "B", "C"}, "e2": {"C", "D"}}

    def test_unweighted_reduction_matches_hgx(self):
        import warnings

        from helpers.graph.hyper_centralities import stationary_pi

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore")
            from hypergraphx import Hypergraph
            from hypergraphx.dynamics.randwalk import RW_stationary_state

        hg = Hypergraph(edge_list=[("A", "B", "C"), ("C", "D")])
        ref = RW_stationary_state(hg)
        ours = stationary_pi(self.MEM)
        # node order: sorted in ours; HGX order is insertion — compare by name
        hgx_named = dict(zip(hg.get_nodes(), ref))
        for n in hgx_named:
            assert abs(ours[n] - hgx_named[n]) < 1e-9

    def test_weights_pull_mass(self):
        from helpers.graph.hyper_centralities import stationary_pi

        w = {("e1", "A"): 10.0}  # A is 10x as intense inside e1
        pi = stationary_pi(self.MEM, weights=w)
        base = stationary_pi(self.MEM)
        assert pi["A"] > base["A"]
        assert pi["B"] < base["B"]  # same structure, A's mass comes from B/C
        assert abs(sum(pi.values()) - 1.0) < 1e-9

    def test_auto_weight_dry_run_meta(self):
        from helpers.graph.hyper_centralities import compute_centralities

        res = compute_centralities(
            {k: list(v) for k, v in self.MEM.items()},
            allow_giant=True,
            weights={("e1", "A"): 10.0},
        )
        assert res["_meta"]["weighted_incidences"] == 1


class TestWorklistParking:
    """review-kit S4: parked items are suppressed on worklist export."""

    def test_counterparty_worklist_filters_parked(self, conn, tmp_path, monkeypatch):
        c = TestCounterpartyResolution._events_conn(conn)
        monkeypatch.setattr(dh, "_REPO_ROOT", tmp_path)
        from helpers.core.review_kit import park_items

        park_items(
            tmp_path / "outputs" / "worklist_parking" / "counterparty" / "journal.jsonl",
            ["Ghost CP Co"],
            key_field="name",
        )
        n_ok, n_bad, _ = dh.resolve_counterparties(c, dry_run=False)
        assert (n_ok, n_bad) == (1, 1)
        wl = json.loads((tmp_path / "findata" / "Misc" / "counterparty_worklist.json").read_text())
        assert wl["unresolved"] == [] and wl["count"] == 0 and wl["parked"] == 1

    def test_counterparty_worklist_rewrite_when_nothing_resolves(self, conn, tmp_path, monkeypatch):
        """Apply-mode must rewrite the worklist even with ZERO newly-resolved
        counterparties — otherwise stale entries sit forever and fresh
        parking never converges the file (found live 2026-09-19: the only
        unresolved row had vanished from events, so `resolved` was empty and
        the rewrite was skipped, leaving parked: 0 on disk)."""
        c = TestCounterpartyResolution._events_conn(conn)
        c.execute(
            "UPDATE events SET counterparty = 'Zynth Industries' "
            "WHERE counterparty = 'Beta'"  # nothing can auto-resolve now
        )
        c.commit()
        monkeypatch.setattr(dh, "_REPO_ROOT", tmp_path)
        from helpers.core.review_kit import park_items

        park_items(
            tmp_path / "outputs" / "worklist_parking" / "counterparty" / "journal.jsonl",
            ["Ghost CP Co"],
            key_field="name",
        )
        n_ok, n_bad, _ = dh.resolve_counterparties(c, dry_run=False)
        assert (n_ok, n_bad) == (0, 2)
        wl = json.loads((tmp_path / "findata" / "Misc" / "counterparty_worklist.json").read_text())
        assert wl["unresolved"] == ["Zynth Industries"] and wl["count"] == 1 and wl["parked"] == 1

    def test_subsector_worklist_filters_parked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dh, "_REPO_ROOT", tmp_path)
        from helpers.core.review_kit import park_items

        park_items(
            tmp_path / "outputs" / "worklist_parking" / "subsector" / "journal.jsonl",
            ["Banks - Regional"],
            key_field="label",
        )
        dh._write_subsector_worklist(
            [("Banks - Regional", 38, "new sub_sector Banks"), ("Widgets", 2, "map to Widgets")]
        )
        wl = json.loads((tmp_path / "findata" / "Misc" / "subsector_worklist.json").read_text())
        assert [u["label"] for u in wl["unmapped"]] == ["Widgets"]
        assert wl["count"] == 1 and wl["parked"] == 1
