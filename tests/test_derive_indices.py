#!/usr/bin/env python3
"""Tests for helpers/graph/derive_indices.py (index-membership fill S2).

Two layers, mirroring test_derive_countries: pure resolution/convergence
over seeded DBs, then the idempotent entity/edge writers.
"""

from __future__ import annotations

import json
import sqlite3

from helpers.graph import derive_indices as di
from tests.schema import EDGES_12COL, ENTITIES_8COL


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(ENTITIES_8COL + EDGES_12COL)
    return conn


def _seed(conn: sqlite3.Connection, rows: list[tuple[str, str | None, str | None]]) -> None:
    conn.executemany(
        "INSERT INTO entities (name, entity_type, ticker, sector_classification) "
        "VALUES (?, 'company', ?, ?)",
        rows,
    )
    conn.commit()


def _const(symbol: str, isin: str = "", industry: str = "Financial Services") -> dict:
    return {
        "index_name": "Nifty 50",
        "index_slug": "ind_nifty50list.csv",
        "symbol": symbol,
        "isin": isin,
        "company_name": f"{symbol} Ltd.",
        "industry": industry,
        "as_of": "2026-09-18",
    }


# --------------------------------------------------------------------------- #
# Resolution                                                                  #
# --------------------------------------------------------------------------- #
class TestResolve:
    def test_isin_resolution(self):
        entities = [("Reliance Industries", "company", "RELIANCE.NS", None)]
        exchange = [("INE002A01018", "RELIANCE", "NSE")]
        edges, names, worklist, industry = di.resolve(
            [_const("RELIANCE", "INE002A01018", "Oil Gas & Consumable Fuels")],
            entities,
            exchange,
        )
        assert names == ["Nifty 50"] and worklist == []
        assert edges[0][:2] == ("Reliance Industries", "Nifty 50")
        assert edges[0][2]["symbol"] == "RELIANCE"
        assert edges[0][3] == di.SOURCE_REF
        assert industry == {"Oil Gas & Consumable Fuels": ["Reliance Industries"]}

    def test_symbol_resolution_without_isin(self):
        entities = [("Tata Consultancy Services", "company", "TCS.NS", None)]
        edges, _, worklist, _ = di.resolve([_const("TCS", "")], entities, [])
        assert len(edges) == 1 and worklist == []

    def test_unresolved_goes_to_worklist_and_index_still_named(self):
        edges, names, worklist, _ = di.resolve([_const("NOPE", "INE999A01000")], [], [])
        assert edges == [] and names == ["Nifty 50"]
        assert worklist[0]["symbol"] == "NOPE" and worklist[0]["reason"] == "no_entity"

    def test_non_company_entity_never_resolves(self):
        entities = [("Some Index", "index", "NIFTY50", None)]
        edges, _, worklist, _ = di.resolve([_const("NIFTY50")], entities, [])
        assert edges == [] and worklist[0]["reason"] == "no_entity"


# --------------------------------------------------------------------------- #
# Entity + edge writers                                                       #
# --------------------------------------------------------------------------- #
class TestWriters:
    def test_create_index_entities_idempotent(self):
        conn = _conn()
        try:
            n1 = di.create_index_entities(conn, ["Nifty 50", "Nifty Bank"], apply=True)
            n2 = di.create_index_entities(conn, ["Nifty 50", "Nifty Bank"], apply=True)
            assert n1 == 2 and n2 == 0
            rows = conn.execute(
                "SELECT name FROM entities WHERE entity_type='index' ORDER BY name"
            ).fetchall()
            assert [r[0] for r in rows] == ["Nifty 50", "Nifty Bank"]
        finally:
            conn.close()

    def test_create_index_entities_normalizes_name(self):
        import re

        conn = _conn()
        try:
            di.create_index_entities(conn, ["Nifty REITs & Realty", "NIFTY 50"], apply=True)
            rows = dict(
                conn.execute(
                    "SELECT name, normalized_name FROM entities WHERE entity_type='index'"
                ).fetchall()
            )
            assert rows["Nifty REITs & Realty"] == "Nifty_REITs_Realty"
            assert rows["NIFTY 50"] == "NIFTY_50"
            assert all(re.match(r"^[A-Za-z0-9][A-Za-z0-9_]*$", v) for v in rows.values())
        finally:
            conn.close()

    def test_create_index_entities_repairs_bad_normalized_name(self):
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO entities (name, entity_type, normalized_name) "
                "VALUES ('Nifty 50', 'index', 'Nifty 50')"
            )
            conn.commit()
            di.create_index_entities(conn, ["Nifty 50"], apply=True)
            got = conn.execute(
                "SELECT normalized_name FROM entities WHERE name='Nifty 50'"
            ).fetchone()[0]
            assert got == "Nifty_50"
        finally:
            conn.close()

    def test_apply_edges_idempotent(self):
        conn = _conn()
        try:
            edges = [("Reliance Industries", "Nifty 50", {}, di.SOURCE_REF)]
            n1 = di.apply_typed_edges(
                edges, edge_type=di.EDGE_TYPE, symmetric=0, conn=conn, dry_run=False
            )
            n2 = di.apply_typed_edges(
                edges, edge_type=di.EDGE_TYPE, symmetric=0, conn=conn, dry_run=False
            )
            assert n1 == 1 and n2 == 0
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# Industry convergence                                                        #
# --------------------------------------------------------------------------- #
class TestConvergeIndustry:
    def test_fills_empty_and_parks_unmapped(self):
        conn = _conn()
        try:
            _seed(conn, [("Reliance Industries", "RELIANCE.NS", None)])
            filled, unmapped = di.converge_industry(
                conn,
                {
                    "Oil Gas & Consumable Fuels": ["Reliance Industries"],
                    "Services": ["Mystery Co"],
                },
                apply=True,
            )
            assert filled == 1
            assert unmapped == {"Services": ["Mystery Co"]}
            got = conn.execute(
                "SELECT sector_classification FROM entities WHERE name='Reliance Industries'"
            ).fetchone()[0]
            assert got == "Energy"
        finally:
            conn.close()

    def test_never_overwrites_authored_sector(self):
        conn = _conn()
        try:
            _seed(conn, [("HDFC Bank", "HDFCBANK.NS", "Banking")])
            filled, _ = di.converge_industry(
                conn, {"Financial Services": ["HDFC Bank"]}, apply=True
            )
            assert filled == 0
            got = conn.execute(
                "SELECT sector_classification FROM entities WHERE name='HDFC Bank'"
            ).fetchone()[0]
            assert got == "Banking"
        finally:
            conn.close()

    def test_dry_run_writes_nothing(self):
        conn = _conn()
        try:
            _seed(conn, [("Reliance Industries", "RELIANCE.NS", None)])
            planned, _ = di.converge_industry(
                conn,
                {"Oil Gas & Consumable Fuels": ["Reliance Industries"]},
                apply=False,
            )
            assert planned == 1
            got = conn.execute(
                "SELECT sector_classification FROM entities WHERE name='Reliance Industries'"
            ).fetchone()[0]
            assert got is None
        finally:
            conn.close()

    def test_every_mapped_label_targets_a_canonical_sector(self):
        from helpers.validators.static_checks import CANONICAL_SECTORS

        assert set(di.INDUSTRY_TO_SECTOR.values()) <= set(CANONICAL_SECTORS)


# --------------------------------------------------------------------------- #
# Worklist                                                                    #
# --------------------------------------------------------------------------- #
def test_write_worklist(tmp_path):
    path = tmp_path / "index_worklist.json"
    n = di.write_worklist(
        [{"index": "Nifty 50", "symbol": "NOPE", "reason": "no_entity"}],
        {"Services": ["Mystery Co"]},
        path,
    )
    assert n == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["count"] == 1
    assert data["constituents"][0]["symbol"] == "NOPE"
    assert data["unmapped_industries"] == {"Services": ["Mystery Co"]}


# --------------------------------------------------------------------------- #
# S3: listed_on_index valid_from backfill
# --------------------------------------------------------------------------- #
class TestBackfillValidity:
    def _edges_conn(self):
        conn = _conn()
        conn.executemany(
            "INSERT INTO graph_edges (source, target, edge_type, properties, source_ref) "
            "VALUES (?, 'Nifty 50', 'listed_on_index', ?, 'derive:indices:nse-constituents')",
            [
                (
                    "Reliance Industries",
                    json.dumps({"as_of": "2026-09-18", "symbol": "RELIANCE"}),
                ),
                ("TCS", json.dumps({"symbol": "TCS"})),  # no as_of -> untouched
            ],
        )
        conn.commit()
        return conn

    def test_backfill_sets_valid_from_from_as_of(self):
        conn = self._edges_conn()
        n = di.backfill_validity(conn=conn)
        assert n == 1
        rows = conn.execute(
            "SELECT source, valid_from FROM graph_edges WHERE edge_type='listed_on_index' ORDER BY source"
        ).fetchall()
        assert rows == [("Reliance Industries", "2026-09-18"), ("TCS", None)]
        # idempotent: converged -> 0 updates
        assert di.backfill_validity(conn=conn) == 0
        conn.close()


# --------------------------------------------------------------------------- #
# Reconstitution sweep: prune departed members (valid_to) + revive returners
# --------------------------------------------------------------------------- #
class TestReconstitutionSweep:
    def _conn_with_edges(self, rows):
        """Seed graph_edges with ``(source, target, as_of[, symbol])`` membership rows.

        The sidecar symbol defaults to the upper-cased entity name; pass it
        explicitly when the two differ (``"Wipro"`` -> ``"WIPRO"``).
        """
        conn = _conn()
        for row in rows:
            source, index, as_of = row[0], row[1], row[2]
            symbol = row[3] if len(row) > 3 else source.upper()
            conn.execute(
                "INSERT INTO graph_edges (source, target, edge_type, properties, source_ref) "
                "VALUES (?, ?, 'listed_on_index', ?, 'derive:indices:nse-constituents')",
                (source, index, json.dumps({"as_of": as_of, "symbol": symbol})),
            )
        conn.commit()
        return conn

    def test_current_pairs_folds_aliases_and_ignores_dateless(self):
        pairs = di.current_pairs(
            [
                _const("RELIANCE", "INE002A01018"),  # as_of 2026-09-18
                {
                    "index_name": "Nifty MidSmallcap400 50-50",
                    "symbol": "RELIANCE",
                    "as_of": None,  # dateless -> still present, never closed
                    "index_slug": "x",
                    "isin": "",
                    "company_name": "Reliance",
                    "industry": "",
                },
                {
                    "index_name": "Nifty 50",
                    "symbol": "",
                    "as_of": "2026-09-18",
                    "index_slug": "x",
                    "isin": "",
                    "company_name": "x",
                    "industry": "",
                },
            ]
        )
        assert ("RELIANCE", "Nifty 50") in pairs
        assert ("RELIANCE", "Nifty MidSmallcap 400") in pairs
        assert all(symbol for symbol, _ in pairs)

    def test_plan_close_finds_departures_only(self):
        constituents = [
            _const("RELIANCE"),  # still a Nifty 50 member
            {**_const("WIPRO"), "index_name": "Nifty Bank"},  # WIPRO left Nifty 50
        ]
        conn = self._conn_with_edges(
            [
                ("Reliance Industries", "Nifty 50", "2026-09-18", "RELIANCE"),
                ("Wipro", "Nifty 50", "2026-09-18", "WIPRO"),  # gone from the sidecar
            ]
        )
        try:
            plan = di.plan_close(constituents, conn=conn)
        finally:
            conn.close()
        assert [(s, t, sym) for s, t, sym, _ in plan] == [("Wipro", "Nifty 50", "WIPRO")]
        assert plan[0][3] == "2026-09-18"  # close_on = newest vintage for the index

    def test_plan_close_ignores_foreign_symbol(self):
        constituents = [_const("RELIANCE")]
        conn = self._conn_with_edges([("Outsider Co", "Nifty 50", "2026-09-18", "OUTSID")])
        try:
            plan = di.plan_close(constituents, conn=conn)
        finally:
            conn.close()
        assert plan == []  # sidecar never described OUTSID -> out of scope

    def test_close_superseded_sets_valid_to_and_is_idempotent(self):
        constituents = [
            _const("RELIANCE"),
            {**_const("WIPRO"), "index_name": "Nifty Bank"},  # left Nifty 50
        ]
        conn = self._conn_with_edges([("Wipro", "Nifty 50", "2026-09-18", "WIPRO")])
        try:
            plan = di.plan_close(constituents, conn=conn)
            assert di.close_superseded(plan, conn=conn, apply=True) == 1
            row = conn.execute(
                "SELECT valid_from, valid_to FROM graph_edges WHERE source='Wipro'"
            ).fetchone()
            assert tuple(row) == (None, "2026-09-18")
            # second pass: already closed at that date -> no further writes
            assert di.close_superseded(plan, conn=conn, apply=True) == 0
            # dry-run reports the pending change without writing
            conn.execute("UPDATE graph_edges SET valid_to = NULL")
            conn.commit()
            assert di.close_superseded(plan, conn=conn, apply=False) == 1
            assert conn.execute("SELECT valid_to FROM graph_edges").fetchone()[0] is None
        finally:
            conn.close()

    def test_close_superseded_skips_dateless_close_on(self):
        conn = self._conn_with_edges([("Wipro", "Nifty 50", None, "WIPRO")])
        try:
            assert (
                di.close_superseded([("Wipro", "Nifty 50", "WIPRO", "")], conn=conn, apply=True)
                == 0
            )
        finally:
            conn.close()

    def test_reopen_current_revives_only_current_vintage(self):
        constituents = [
            {**_const("WIPRO"), "as_of": "2026-11-13"},  # current vintage
        ]
        conn = self._conn_with_edges(
            [
                ("Wipro", "Nifty 50", "2026-09-18", "WIPRO"),
                ("Wipro", "Nifty Bank", "2026-09-18", "WIPRO"),  # closed, not in sidecar now
            ]
        )
        try:
            conn.execute("UPDATE graph_edges SET valid_to='2026-11-13' WHERE source='Wipro'")
            conn.commit()
            assert di.reopen_current(constituents, conn=conn, apply=True) == 1
            assert (
                conn.execute("SELECT valid_to FROM graph_edges WHERE target='Nifty 50'").fetchone()[
                    0
                ]
                is None
            )
            # the Nifty Bank row stays closed: sidecar no longer asserts it
            assert (
                conn.execute(
                    "SELECT valid_to FROM graph_edges WHERE target='Nifty Bank'"
                ).fetchone()[0]
                == "2026-11-13"
            )
            assert di.reopen_current(constituents, conn=conn, apply=True) == 0
        finally:
            conn.close()

    def test_reopen_current_refuses_mass_reopen(self):
        constituents = [
            {**_const("WIPRO"), "as_of": "2026-11-13"},
        ]
        conn = self._conn_with_edges([("Wipro", "Nifty 50", "2026-09-18", "WIPRO")])
        try:
            conn.execute("UPDATE graph_edges SET valid_to='2026-11-13'")
            conn.commit()
            try:
                di.reopen_current(constituents, conn=conn, apply=True, max_reopen=0)
            except RuntimeError as exc:
                assert "re-open" in str(exc)
            else:
                raise AssertionError("expected RuntimeError on mass reopen")
            assert conn.execute("SELECT valid_to FROM graph_edges").fetchone()[0] == "2026-11-13"
        finally:
            conn.close()

    def test_closed_edge_is_visible_through_day_before_reconstitution(self):
        """valid_to is EXCLUSIVE (as_of.py): the departure disappears at the new vintage."""
        import helpers.graph.as_of as as_of_mod

        # The sidecar publishes the new vintage only; the departed member is
        # absent from it, so close_on is the newest vintage that index has.
        constituents = [
            {**_const("RELIANCE"), "as_of": "2026-11-13"},
            {**_const("WIPRO"), "index_name": "Nifty Bank", "as_of": "2026-11-13"},
        ]
        conn = self._conn_with_edges(
            [
                ("Wipro", "Nifty 50", "2026-09-18", "WIPRO"),
                ("Reliance Industries", "Nifty 50", "2026-09-18", "RELIANCE"),
            ]
        )
        try:
            di.backfill_validity(conn=conn)  # stamps valid_from from properties.as_of
            plan = di.plan_close(constituents, conn=conn)
            assert [row[3] for row in plan] == ["2026-11-13"]
            assert di.close_superseded(plan, conn=conn, apply=True) == 1
            before = [r for r in as_of_mod.edges_as_of(conn, "2026-11-12", edge_type=di.EDGE_TYPE)]
            after = [r for r in as_of_mod.edges_as_of(conn, "2026-11-13", edge_type=di.EDGE_TYPE)]
            assert sorted(r["source"] for r in before) == ["Reliance Industries", "Wipro"]
            assert [r["source"] for r in after] == [
                "Reliance Industries"
            ]  # Wipro gone at the new vintage
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# Prune audit: what a prune plan would actually write                          #
# --------------------------------------------------------------------------- #
class TestPruneAudit:
    @staticmethod
    def _bulk_rows(conn, index, count, *, as_of, valid_from, valid_to=None):
        """Seed ``count`` rows via a single INSERT ... SELECT (no 251-step loop)."""
        conn.execute(
            "INSERT INTO graph_edges "
            "(source, target, edge_type, properties, valid_from, valid_to, source_ref) "
            "SELECT 'S' || key, ?, 'listed_on_index', "
            "       json_object('as_of', ?, 'symbol', 'S' || key), ?, ?, "
            "       'derive:indices:nse-constituents' "
            "FROM json_each(?)",
            (
                index,
                as_of,
                valid_from,
                valid_to,
                json.dumps([0] * count),
            ),
        )
        conn.commit()
        return conn

    def _conn_with_edges(self, rows):
        conn = _conn()
        for row in rows:
            source, index, as_of, valid_to = row[0], row[1], row[2], row[3]
            valid_from = row[4] if len(row) > 4 else None
            conn.execute(
                "INSERT INTO graph_edges "
                "(source, target, edge_type, properties, valid_from, valid_to, source_ref) "
                "VALUES (?, ?, 'listed_on_index', ?, ?, ?, 'derive:indices:nse-constituents')",
                (
                    source,
                    index,
                    json.dumps({"as_of": as_of, "symbol": source.upper()}),
                    valid_from,
                    valid_to,
                ),
            )
        conn.commit()
        return conn

    def test_audit_separates_would_write_from_already_closed(self):
        # plan_close derives a candidate's close_on from the NEWEST sidecar vintage
        # for that index, so the index must appear in the sidecar at 2026-11-14.
        # Nifty Microcap 250 is present there; Nifty 100 is not, and falls back to
        # the row's own valid_from.
        constituents = [
            {**_const("WIPRO"), "as_of": "2026-11-14"},
            {**_const("RELIANCE"), "index_name": "Nifty Microcap 250", "as_of": "2026-11-14"},
        ]
        conn = self._conn_with_edges(
            [
                # still open -> a real write at the new vintage
                ("Wipro", "Nifty Microcap 250", "2026-09-18", None),
                # already closed at exactly that date -> no write
                ("Wipro", "Nifty 100", "2026-11-13", "2026-11-13", "2026-11-13"),
            ]
        )
        try:
            plan = di.plan_close(constituents, conn=conn)
            assert sorted(row[3] for row in plan) == ["2026-11-13", "2026-11-14"]
            audit = di.audit_prune_plan(plan, conn=conn)
            assert audit.candidates == 2
            assert audit.would_write == 1
            assert audit.already_closed == 1
            assert audit.dateless == 0
            assert audit.by_date == {"2026-11-13": 1, "2026-11-14": 1}
            assert audit.by_index["Nifty Microcap 250"] == (1, 0, ["Wipro"], "2026-11-14")
            # the writer is the only thing that can confirm the count
            assert di.close_superseded(plan, conn=conn, apply=True) == 1
        finally:
            conn.close()

    def test_dateless_index_is_reported_as_dateless_not_silently_skipped(self):
        """close_superseded `continue`s an empty close_on: plan and write disagree."""
        constituents = [{**_const("WIPRO"), "index_name": "Nifty Bank", "as_of": None}]
        conn = self._conn_with_edges([("Wipro", "Nifty 50", "2026-09-18", None)])
        try:
            plan = di.plan_close(constituents, conn=conn)
            audit = di.audit_prune_plan(plan, conn=conn)
            assert audit.candidates == 1, "an undated index is still a plan candidate"
            assert audit.dateless == 1 and audit.would_write == 0
            assert di.close_superseded(plan, conn=conn, apply=True) == 0
            assert any("no close date" in w for w in audit.warnings)
            assert any("counts will disagree" in w for w in audit.warnings)
        finally:
            conn.close()

    def test_mass_closure_escalates_per_index_warning(self):
        # plan_close only gates a candidate on the SYMBOL existing somewhere in the
        # sidecar (sidecar_symbols), so a whole index can be absent from the sidecar:
        # the 251 rows fall back to their own valid_from for close_on.
        # The candidate set is keyed on the SYMBOL existing somewhere in the sidecar
        # (sidecar_symbols), so the 251 rows only need their symbols asserted by SOME
        # index; a sentinel member of the index under sweep supplies the vintage that
        # stamps close_on.
        count = di.INDEX_CLOSE_WARN + 1
        constituents = [
            {**_const(f"S{i}"), "index_name": "Nifty Bank", "as_of": "2026-11-14"}
            for i in range(count)
        ]
        constituents.append(
            {**_const("SENTINEL"), "index_name": "Nifty Microcap 250", "as_of": "2026-11-14"}
        )
        conn = _conn()
        self._bulk_rows(
            conn,
            "Nifty Microcap 250",
            count,
            as_of="2026-09-18",
            valid_from=None,
        )
        try:
            plan = di.plan_close(constituents, conn=conn)
            assert len(plan) == count
            audit = di.audit_prune_plan(plan, conn=conn)
            assert audit.would_write == count
            assert any("Nifty Microcap 250" in w for w in audit.warnings)
            assert any("real reconstitution" in w for w in audit.warnings)
            # the advisory's first line is that index, so the operator reads it first
            rendered = di.render_prune_advisory(audit)
            assert "Nifty Microcap 250" in rendered[0]
            assert str(count) in rendered[0]
        finally:
            conn.close()

    def test_render_advisory_warns_when_the_writer_ignores_the_plan(self):
        constituents = [{**_const("WIPRO"), "index_name": "Nifty Bank", "as_of": "2026-11-14"}]
        conn = self._conn_with_edges([("Wipro", "Nifty 50", "2026-09-18", None, "2026-11-13")])
        try:
            plan = di.plan_close(constituents, conn=conn)
            assert [row[3] for row in plan] == ["2026-11-13"]
            audit = di.audit_prune_plan(plan, conn=conn)
            assert audit.would_write == 1
            lines = di.render_prune_advisory(audit, revival=2, close_n=0)
            text = "\n".join(lines)
            assert "prune plan: 1 candidates -> 1 would write" in text
            assert "reopen_current is not wired" in text
            assert "wrote 0 rows against a plan of 1" in text
            assert "0 already closed at that date" in text
            # no writer ran: no disagreement is claimed
            assert "refused or re-counted" not in "\n".join(
                di.render_prune_advisory(audit, revival=0)
            )
        finally:
            conn.close()

    def test_format_prune_advisory_is_one_block(self):
        conn = self._conn_with_edges([("Wipro", "Nifty 50", "2026-09-18", None)])
        try:
            audit = di.audit_prune_plan(di.plan_close([], conn=conn), conn=conn)
            text = di.format_prune_advisory(audit)
            assert "plan is empty" in text
            assert text.count("\n") == text.strip().count("\n")
        finally:
            conn.close()


class TestPlanRevival:
    def _conn_with_edges(self, rows):
        conn = _conn()
        for source, index, as_of, valid_to in rows:
            conn.execute(
                "INSERT INTO graph_edges (source, target, edge_type, properties, valid_to, source_ref) "
                "VALUES (?, ?, 'listed_on_index', ?, ?, 'derive:indices:nse-constituents')",
                (
                    source,
                    index,
                    json.dumps({"as_of": as_of, "symbol": source.upper()}),
                    valid_to,
                ),
            )
        conn.commit()
        return conn

    def test_revival_finds_re_admitted_symbol_but_reopen_is_unwired(self):
        # _const pins index_name='Nifty 50', so WIPRO is re-admitted THERE while
        # it left Nifty Bank: revival keys on the (symbol, index) pair, not the symbol.
        constituents = [
            {**_const("WIPRO"), "as_of": "2026-11-14"},
            {
                **_const("RELIANCE", "INE002A01018"),
                "index_name": "Nifty Bank",
                "as_of": "2026-11-14",
            },
        ]
        conn = self._conn_with_edges(
            [
                ("Wipro", "Nifty 50", "2026-09-18", "2026-11-13"),  # left, then re-admitted
                ("Wipro", "Nifty Bank", "2026-09-18", "2026-11-13"),  # left, not re-admitted
            ]
        )
        try:
            revival = di.plan_revival(constituents, conn=conn)
            assert [row[:2] for row in revival] == [("Wipro", "Nifty 50")]
            assert revival[0][3] == "2026-11-14"
            assert di.plan_revival(constituents, conn=conn, symbols={"WIPRO"}) == revival
            # nothing in the CLI calls reopen_current, so the plan stays advisory
            assert di.reopen_current(constituents, conn=conn, apply=True) == 1
            assert di.plan_revival(constituents, conn=conn) == []
        finally:
            conn.close()
