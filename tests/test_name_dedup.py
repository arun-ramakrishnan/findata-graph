"""Entity-name dedup tests (entity_name_dedup proposal S1/S2/S4).

Toy-graph fixture: variant clusters, a listed entity, a footnote
variant, an exact-duplicate pair for the collapse accounting. Every
function takes an injected connection — the live census path uses the
house connect; tests never touch memory/research.db.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from helpers.graph import name_dedup as nd
from helpers.core import entity_identity as ei


@pytest.fixture()
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(tmp_path / "g.db")
    conn.execute(
        "CREATE TABLE entities ("
        " name TEXT PRIMARY KEY, entity_type TEXT NOT NULL,"
        " normalized_name TEXT, file_path TEXT)"
    )
    conn.execute(
        "CREATE TABLE graph_edges ("
        " id INTEGER PRIMARY KEY, source TEXT NOT NULL REFERENCES entities(name),"
        " target TEXT NOT NULL REFERENCES entities(name), edge_type TEXT,"
        " weight REAL, properties TEXT, valid_from TEXT, valid_to TEXT,"
        " source_ref TEXT, symmetric INTEGER, created_at TEXT,"
        " agent_id TEXT, source_tier TEXT)"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    entities = [
        ("Cholamandalam FH", "company", "Cholamandalam_FH", None),
        ("E.I.D.Parry (India)", "company", "EIDParry_India", None),
        ("E.I.D.PARRY (INDIA)", "company", "EIDPARRY_INDIA", None),
        ("E.I.D. Parry (India)", "company", "EID_Parry_India", None),
        ("HDFC Bank", "company", "HDFC_Bank", None),
        ("HDFC Bank**", "company", "HDFC_Bank", None),
        ("HDFC Bank*", "company", "HDFC_Bank", None),
        ("Acme", "company", "Acme", None),
        ("Beta", "company", "Beta", None),
        ("HDFC Securities", "company", "HDFC_Securities", None),
        ("NIFTY 50", "index", "NIFTY_50", None),
        ("NIFTY 100", "index", "NIFTY_100", None),
        ("somewhere", "other", "somewhere", None),
    ]
    conn.executemany("INSERT INTO entities VALUES (?, ?, ?, ?)", entities)
    rows = [
        # Chola/Parry shape: relation scattered across 3 Parry variants
        ("Cholamandalam FH", "E.I.D.Parry (India)", "supplier_to", "vigil:rpt:A", 0),
        ("Cholamandalam FH", "E.I.D.PARRY (INDIA)", "same_group", "vigil:rpt:A", 1),
        ("Cholamandalam FH", "E.I.D. Parry (India)", "same_group", "vigil:rpt:B", 1),
        # listed entity + footnote variant of it (footnote must not win)
        ("HDFC Bank", "NIFTY 50", "listed_on_index", "derive:indices", 0),
        ("HDFC Bank**", "NIFTY 50", "listed_on_index", "derive:indices", 0),
        ("HDFC Bank*", "somewhere", "same_group", "vigil:rpt:C", 1),
        # exact-duplicate pair for the S2 collapse accounting
        ("Acme", "Beta", "jv_with", "extract:x", 0),
        ("Acme", "Beta", "jv_with", "extract:x", 0),
        # distinct entities that must NEVER cluster
        ("HDFC Bank", "HDFC Securities", "competes_with", "hand", 1),
    ]
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref, symmetric)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    yield conn
    conn.close()


def test_normalise_folds_case_punct_footnotes():
    assert nd.normalise("E.I.D.PARRY (INDIA)") == nd.normalise("e.i.d. parry india")
    assert nd.normalise("BNP Paribas Bank**") == nd.normalise("BNP Paribas Bank")
    # camelCase splits (the resolver's compound-join rule, now shared):
    # "IndianOil" == "Indian Oil" — glued compounds fold
    assert nd.normalise("IndianOil - Adani Gas") == "indian oil adani gas"
    assert nd.normalise("NetwebTechnologiesIndia") == nd.normalise("Netweb Technologies India")
    assert nd.normalise("McDonald's") == nd.normalise("McDonalds")  # apostrophe
    assert nd.normalise("Systèmes") == nd.normalise("Systemes")  # de-accent
    # deliberately NOT fuzzy: these stay apart
    assert nd.normalise("HDFC Bank") != nd.normalise("HDFC Securities")


def test_canonical_rule_listing_beats_degree_footnote_never_wins(con):
    names, degree, listed = nd._load_graph(con)
    parry = [n for n in names if "Parry" in n or "PARRY" in n]
    canon = nd.canonical_of(sorted(parry), degree, listed)
    # no listed hit in the cluster -> highest degree wins ("E.I.D.Parry
    # (India)" carries 2 rows); footnote forms are ineligible anyway
    assert canon in {"E.I.D.Parry (India)", "E.I.D.PARRY (INDIA)"}
    assert not nd.is_footnote_variant(canon)
    # a listed name beats a higher-degree non-listed sibling
    cluster = ["HDFC Bank*", "HDFC Bank**", "HDFC Bank"]
    assert nd.canonical_of(cluster, degree, listed) == "HDFC Bank"


def test_census_clusters_and_report_only_transpositions(con):
    cen = nd.census(con)
    keys = {c["key"] for c in cen["clusters"]}
    assert "e i d parry india" in keys
    hdfc = next(c for c in cen["clusters"] if c["key"] == "hdfc bank")
    assert hdfc["listed_hit"] is True
    assert hdfc["canonical"] == "HDFC Bank"
    # distinct entities are not a cluster
    assert "hdfc securities" not in keys
    # every reported cluster has a footnote-free canonical
    assert all(not nd.is_footnote_variant(c["canonical"]) for c in cen["clusters"])


def test_plan_write_and_apply(con, tmp_path: Path):
    plan = tmp_path / "plan.txt"
    cen = nd.census(con)
    cen["transpositions"] = []
    nd.write_plan(cen, plan)
    text = plan.read_text(encoding="utf-8")
    assert "-> fold" in text and "DRY-RUN artifact" in text
    # gated: no --yes, no write
    with pytest.raises(SystemExit, match="gated"):
        nd.apply_plan(con, plan, yes=False)
    res = nd.apply_plan(con, plan, yes=True)
    assert res["zero_loss_ok"] is True
    # all Parry variants folded to one canonical name
    left = {
        r[0]
        for r in con.execute(
            "SELECT target FROM graph_edges WHERE target LIKE '%Parry%' OR target LIKE '%PARRY%'"
        ).fetchall()
    }
    assert len(left) == 1
    # the exact-duplicate Acme/Beta pair collapsed to one row
    n = con.execute(
        "SELECT COUNT(*) FROM graph_edges WHERE source='Acme' AND target='Beta'"
    ).fetchone()[0]
    assert n == 1 and res["duplicate_rows_removed"] >= 1
    # registry fold: variant entity rows are gone, canonicals remain
    assert res["entities_folded"] >= 3  # two Parry variants + HDFC footnotes
    assert con.execute("SELECT COUNT(*) FROM entities WHERE name LIKE '%parry%'").fetchone()[0] == 1
    assert (
        con.execute("SELECT COUNT(*) FROM entities WHERE name LIKE 'HDFC Bank%'").fetchone()[0] == 1
    )
    # post-apply census: the folded clusters are gone
    assert all("parry" not in c["key"] for c in nd.census(con)["clusters"])


def test_apply_extends_ledger_and_survives_replan(con, tmp_path: Path):
    """S2 EXTENDS the append-only fold ledger — never replaces it.

    2026-10-16 ruling. ``--plan`` regenerates the plan artifact after a
    merge and empties it to headers, which would otherwise silently drop
    the fold map every writer consults (``entity_identity.fold_registry``
    promises a signed fold routes old spellings to the canonical
    *forever*). The ledger is the durable half."""
    plan = tmp_path / "plan.txt"
    ledger = tmp_path / "fold_ledger.jsonl"
    cen = nd.census(con)
    cen["transpositions"] = []
    nd.write_plan(cen, plan)
    res = nd.apply_plan(con, plan, yes=True, ledger_path=ledger)
    assert res["fold_ledger_appended"] == res["variants_folded"] > 0
    assert Path(res["fold_ledger"]) == ledger
    recorded = {
        json.loads(line)["variant"]
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert any("arry" in v for v in recorded)

    # The post-merge state: regenerating the plan empties it to headers.
    nd.write_plan(nd.census(con), plan)
    assert "-> fold" not in plan.read_text(encoding="utf-8")

    # The plan half alone is now empty; the ledger half still resolves.
    assert ei.fold_registry(plan, tmp_path / "missing.jsonl") == {}
    folds = ei.fold_registry(plan, ledger)
    assert any("parry" in k for k in folds), folds

    # Idempotent: an empty (regenerated) plan appends nothing.
    before = ledger.read_text(encoding="utf-8")
    nd.apply_plan(con, plan, yes=True, ledger_path=ledger)
    assert ledger.read_text(encoding="utf-8") == before


def test_person_clusters_excluded_and_listed_exempt(con):
    """Person clusters (S1 filter) never merge; a LISTED company with
    person-shaped spelling is exempt from the exclusion."""
    conn = con
    persons = [
        "D. Sivanandhan",
        "D Sivanandhan",
        "Mr Amrutlal K Bharadia",
        "Mr Amrutlal K. Bharadia",
    ]
    smiths = ["A. O. Smith India", "A O Smith India"]
    conn.executemany(
        "INSERT INTO entities VALUES (?, 'company', 'x', NULL)",
        [(n,) for n in persons + smiths],
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref, symmetric)"
        " VALUES (?, 'Beta', 'same_group', 'vigil:x', 1)",
        [(n,) for n in persons + smiths],
    )
    conn.commit()
    cen = nd.census(conn)
    keys = {c["key"] for c in cen["clusters"]}
    assert "d sivanandhan" not in keys  # excluded as a person cluster
    assert {pc["key"] for pc in cen["person_clusters"]} >= {"d sivanandhan"}
    # listed exemption: the A. O. Smith cluster stays mergeable
    assert "a o smith india" in keys


def test_apply_skips_person_rows_from_stale_plan(con, tmp_path: Path):
    """A pre-filter plan that still carries person folds is defused: the
    S2 apply re-checks the person shape and skips those rows."""
    plan = tmp_path / "plan.txt"
    plan.write_text(
        "== D. Sivanandhan  (key: d sivanandhan; edge rows 2)\n"
        "   D. Sivanandhan\n"
        "   D Sivanandhan  -> fold\n"
    )
    plan.with_suffix(".jsonl").write_text(
        json.dumps({"canonical": "D. Sivanandhan", "variants": ["D Sivanandhan"]}) + "\n"
    )
    res = nd.apply_plan(con, plan, yes=True)
    assert res["variants_folded"] == 0  # person rows skipped


def test_apply_collides_into_canonical(con, tmp_path: Path):
    """Both spellings already carry an edge to the same third party:
    the colliding variant row collapses into the canonical row instead
    of tripping UNIQUE(source, target, edge_type)."""
    conn = con
    conn.executemany(
        "INSERT INTO entities VALUES (?, 'company', 'x', NULL)",
        [("ThirdCo",), ("Acme Industries Ltd",), ("Acme Industries Limited",)],
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref, symmetric)"
        " VALUES (?, 'ThirdCo', 'supplier_to', ?, 0)",
        [("Acme Industries Ltd", "vigil:1"), ("Acme Industries Limited", "vigil:2")],
    )
    conn.commit()
    plan = tmp_path / "plan.txt"
    plan.write_text(
        "== Acme Industries Ltd  (key: acme industries ltd; edge rows 2)\n"
        "   Acme Industries Ltd\n"
        "   Acme Industries Limited  -> fold\n"
    )
    plan.with_suffix(".jsonl").write_text(
        json.dumps({"canonical": "Acme Industries Ltd", "variants": ["Acme Industries Limited"]})
        + "\n"
    )
    res = nd.apply_plan(conn, plan, yes=True)
    assert res["zero_loss_ok"] is True
    assert res["collapsing_duplicates"] >= 1
    n = conn.execute(
        "SELECT COUNT(*) FROM graph_edges WHERE target='ThirdCo' AND edge_type='supplier_to'"
    ).fetchone()[0]
    assert n == 1  # one relation, canonical spelling
    names = {
        r[0]
        for r in conn.execute("SELECT source FROM graph_edges WHERE target='ThirdCo'").fetchall()
    }
    assert names == {"Acme Industries Ltd"}


def test_check_gate_baseline(con, tmp_path: Path):
    base = tmp_path / "baseline.txt"
    cen_count = nd.census(con)["n_clusters"]
    base.write_text(str(cen_count) + "\n")
    assert nd.check(con, base) == 0  # at baseline
    base.write_text(str(cen_count - 1) + "\n")
    assert nd.check(con, base) == 1  # growth above baseline fails
    base.unlink()
    assert nd.check(con, base) == 0  # no baseline -> informational only


def test_apply_refuses_missing_plan(con, tmp_path: Path):
    with pytest.raises(SystemExit, match="missing"):
        nd.apply_plan(con, tmp_path / "nope.txt", yes=True)
