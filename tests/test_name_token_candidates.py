"""Tests for helpers/maintenance/name_token_candidates.py (S1)."""

import duckdb

from helpers.maintenance import name_token_candidates as ntc


def make_con(with_entities: bool = False, extra_embedded: list[str] | None = None):
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE company_embeddings (company_name varchar, embedding blob, model varchar, created_at timestamp)"
    )
    companies = [
        "Tata Motors",
        "Tata Steel",
        "Tata Chemicals",
        "Mahindra & Mahindra",
        "Mahindra CIE",
        "Reliance Industries",
        "Adani Enterprises",
        "Adani Power",
        "Godrej Consumer",
        "Godrej Properties",
        "Titan",
        "Trent",
    ]
    if extra_embedded:
        companies += extra_embedded
    for name in companies:
        con.execute(
            "INSERT INTO company_embeddings VALUES (?, NULL, 'test', now())",
            [name],
        )
    if with_entities:
        con.execute("CREATE TABLE entities (name varchar, entity_type varchar)")
        for name in companies:
            con.execute("INSERT INTO entities VALUES (?, 'company')", [name])
        con.execute("INSERT INTO entities VALUES ('Tata Toys', 'company')")
    con.execute("CREATE TABLE graph_edges (source varchar, target varchar, edge_type varchar)")
    edges = [
        ("Tata Motors", "Tata Steel", "same_group"),
        ("Tata Steel", "Tata Motors", "same_group"),  # reversed duplicate
        ("Mahindra & Mahindra", "Mahindra CIE", "subsidiary_of"),
        ("Adani Enterprises", "Adani Power", "jv_with"),
        ("Reliance Industries", "Tata Steel", "supplier_to"),  # not checked
    ]
    for src, tgt, typ in edges:
        con.execute("INSERT INTO graph_edges VALUES (?, ?, ?)", [src, tgt, typ])
    return con


def test_tokenize_filters_grammar_and_short():
    assert ntc.tokenize("The of a and") == []
    assert ntc.tokenize("Tata Group") == ["tata", "group"]  # group not default-stopped
    assert ntc.tokenize("A-1 Motors") == ["motors"]  # len<=2 dropped
    assert ntc.tokenize("Mahindra & Mahindra") == ["mahindra"]  # dedupe


def test_noise_stopwords_only_under_strict():
    assert "bank" in ntc.NOISE_STOPWORDS
    assert "limited" in ntc.NOISE_STOPWORDS
    assert "bank" not in ntc.STOPWORDS
    assert ntc.tokenize("HDFC Bank Limited") == ["hdfc", "bank", "limited"]
    assert ntc.tokenize("HDFC Bank Limited", strict=True) == ["hdfc"]


def test_generate_candidates_deterministic():
    con = make_con()
    rows1, stats1 = ntc.generate_candidates(con)
    rows2, stats2 = ntc.generate_candidates(con)
    assert rows1 == rows2
    assert stats1 == stats2
    assert stats1["total"] == 6
    assert stats1["hits"] == 3
    assert stats1["new"] == 3
    assert stats1["precision"] == 0.5


def test_candidate_rows_content():
    con = make_con()
    rows, _ = ntc.generate_candidates(con)
    by_pair = {tuple(sorted((r["source"], r["target"]), key=str.lower)): r for r in rows}
    assert ("Tata Motors", "Tata Steel") in by_pair
    assert by_pair[("Tata Motors", "Tata Steel")]["hit"] is True
    assert by_pair[("Tata Chemicals", "Tata Motors")]["hit"] is False
    assert by_pair[("Mahindra & Mahindra", "Mahindra CIE")]["hit"] is True
    assert by_pair[("Adani Enterprises", "Adani Power")]["hit"] is True
    assert by_pair[("Godrej Consumer", "Godrej Properties")]["hit"] is False
    # rarest-token annotation: tata pair carries token tata df 3
    expected_annot = "tata"
    assert by_pair[("Tata Motors", "Tata Steel")]["token"] == expected_annot
    assert by_pair[("Tata Motors", "Tata Steel")]["df"] == 3
    # supplier_to is not a checked relation -> Reliance pair absent entirely
    assert all("Reliance Industries" not in (r["source"], r["target"]) for r in rows)


def test_df_cap_enforced():
    con = make_con()
    rows, stats = ntc.generate_candidates(con, max_df=2)
    # tata (df 3) drops; mahindra/adani/godrej (df 2) stay
    assert stats["total"] == 3
    assert all(r["df"] <= 2 for r in rows)


def test_all_companies_widens_universe():
    con = make_con(with_entities=True)
    rows, stats = ntc.generate_candidates(con)
    # default universe: embedded only -> tata df 3
    assert stats["companies"] == 12
    assert stats["total"] == 6
    rows_all, stats_all = ntc.generate_candidates(con, all_companies=True)
    # + 'Tata Toys' in entities -> tata df 4 -> C(4,2)=6 tata pairs
    assert stats_all["companies"] == 13
    assert stats_all["total"] == 6 + 3  # tata +2 pairs, others same
    assert any(
        tuple(sorted((r["source"], r["target"]), key=str.lower)) == ("Tata Motors", "Tata Toys")
        for r in rows_all
    )


def test_empty_corpus():
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE company_embeddings (company_name varchar, embedding blob, model varchar, created_at timestamp)"
    )
    con.execute("CREATE TABLE graph_edges (source varchar, target varchar, edge_type varchar)")
    rows, stats = ntc.generate_candidates(con)
    assert rows == []
    assert stats["total"] == 0
    assert stats["precision"] == 0.0
