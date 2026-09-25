import sqlite3

from helpers.maintenance import sync_coverage_tags as coverage


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entities (
            name TEXT PRIMARY KEY,
            normalized_name TEXT,
            entity_type TEXT,
            file_path TEXT
        );
        CREATE TABLE quotes (as_of_edition TEXT, entity TEXT);
        """
    )
    conn.execute(
        "INSERT INTO entities VALUES (?, ?, ?, ?)",
        ("Acme India", "acme_india", "company", "findata/Companies/Automotive/Acme_India.md"),
    )
    conn.execute(
        "INSERT INTO entities VALUES (?, ?, ?, ?)",
        ("Regulator", "regulator", "institution", "findata/Institutions/Regulator.md"),
    )
    return conn


def test_merge_tags_is_sorted_idempotent_and_preserves_fields():
    text = """---\ntype: newsletter\ntitle: Test\ngenerated:\n  by: process:okf_backfill\n  at: '2026-08-15T00:00:00Z'\ntags:\n- publisher/zerodha\n- series/the_chatter\n---\n# Test\n"""
    new_text, changed = coverage.merge_tags(text, {"company/acme_india", "company/zeta"})
    assert changed
    assert "title: Test" in new_text
    assert "by: process:okf_backfill" in new_text
    assert new_text.index("company/acme_india") < new_text.index("company/zeta")
    assert coverage.merge_tags(new_text, {"company/acme_india", "company/zeta"}) == (
        new_text,
        False,
    )


def test_build_plan_filters_company_quotes_and_missing_editions(tmp_path):
    (tmp_path / "findata" / "The_Chatter").mkdir(parents=True)
    (tmp_path / "findata" / "The_Chatter" / "Edition_One.md").write_text("---\ntags: []\n---\n")
    conn = _conn()
    conn.executemany(
        "INSERT INTO quotes VALUES (?, ?)",
        [
            ("Edition_One", "Acme India"),
            ("Edition_One", "Regulator"),
            ("Missing_Edition", "Acme India"),
        ],
    )
    plan = coverage.build_plan(conn, tmp_path)
    assert plan.pairs == 3
    assert plan.company_pairs == 1
    assert plan.non_company_pairs == 1
    assert plan.missing_editions == 1
    assert plan.tags_by_edition == {"Edition_One": {"company/acme_india"}}


def test_apply_plan_is_idempotent(tmp_path):
    (tmp_path / "findata" / "The_Chatter").mkdir(parents=True)
    note = tmp_path / "findata" / "The_Chatter" / "Edition_One.md"
    note.write_text("---\ntype: newsletter\ntags:\n- series/the_chatter\n---\n")
    conn = _conn()
    conn.execute("INSERT INTO quotes VALUES (?, ?)", ("Edition_One", "Acme India"))
    plan = coverage.build_plan(conn, tmp_path)
    assert coverage.apply_plan(plan, tmp_path, apply=True) == ["findata/The_Chatter/Edition_One.md"]
    assert "company/acme_india" in note.read_text()
    assert coverage.apply_plan(coverage.build_plan(conn, tmp_path), tmp_path, apply=True) == []
