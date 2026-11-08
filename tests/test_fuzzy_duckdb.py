"""Parity tests: DuckDB fuzzy pushdown vs the Python word-overlap backend.

algorithms_adoption_sweep S1 — the SQL backend
(``helpers/core/fuzzy_duckdb.py``) must replicate
``fuzzy_match.word_overlap_match`` EXACTLY before any caller wiring.
Every test compares both backends on the same inputs; a mismatch fails
the test (the py path is the contract).
"""

import random
import sys
from pathlib import Path

HELPERS = Path(__file__).resolve().parents[1] / "helpers" / "core"
sys.path.insert(0, str(HELPERS))
import fuzzy_duckdb as fd  # noqa: E402
import fuzzy_match as fm  # noqa: E402

import duckdb  # noqa: E402
import pytest  # noqa: E402

ENTITIES = [
    "Tata Consultancy Services",
    "Tata Motors Passenger Vehicles",
    "Tata Steel",
    "Tata Power",
    "Adani Ports and SEZ",
    "Adani Power",
    "Adani Enterprises",
    "HDFC Bank",
    "HDFC Life",
    "HDFC Asset Management",
    "UTI Asset Management",
    "Mahindra & Mahindra",
    "P&G Hygiene and Healthcare",
    "FSN E-Commerce",
    "L&T Finance",
    "Power Grid Corporation of India",
    "Tata Tata Motors",  # duplicate token — set cardinality
    "&",  # punctuation-only — zero tokens
    "  ",  # whitespace-only — zero tokens
    "Nestlé India",  # non-ASCII
    "One 97 Communications PayTM",
    "The And Of India Ltd",  # stopwords-only — zero tokens after strip
]

# The documented traps (fuzzy_match docstring) + degenerate queries.
QUERIES = [
    "Adani Port",  # → Adani Ports and SEZ, NOT Adani Power
    "Adani Ports",
    "Tata",  # containment → first Tata entity, score 1.0
    "tata steel",
    "HDFC Asset Management Company",  # generic-word overlap floor
    "UTI Asset Management",  # must NOT match HDFC Asset Management
    "Mahindra",  # → Mahindra & Mahindra
    "P&G",
    "FSN E Commerce",
    "L&T",
    "power grid corp",
    "nestle india",  # non-ASCII on the entity side
    "One97",
    "",  # blank query guard
    "   ",  # whitespace query guard
    "&",  # punctuation-only query
    "zzz no match anywhere",
    "the and of",  # stopwords-only query
    "t t motors",  # single-letter tokens
    "UTI",
]

TRAPS = [
    ("Adani Port", "Adani Ports and SEZ"),
    ("", None),
    ("   ", None),
]


def test_generic_only_overlap_rejected():
    # Overlap is only generic words ("asset", "management") → no match in
    # either backend — this is what keeps HDFC/UTI Asset Management apart.
    ents = ["HDFC Asset Management", "UTI Asset Management"]
    for q in ["Alpha Asset Management", "Prudential Asset Management Company"]:
        _assert_parity(q, ents)
        match, _score = fd.word_overlap_match_sql(q, ents)
        assert match is None, f"generic-only overlap matched: q={q!r} → {match}"
    # same-family queries with a distinctive shared word still resolve,
    # identically in both backends
    _assert_parity("UTI Asset Mgmt Co", ["HDFC Asset Management", "UTI Asset Management"])


def _assert_parity(query: str, entities: list[str], threshold: float = 0.5) -> None:
    py = fm.word_overlap_match(query, entities, threshold)
    sql = fd.word_overlap_match_sql(query, entities, threshold)
    assert py == sql, f"backend divergence q={query!r}: py={py} sql={sql}"


@pytest.mark.parametrize("query", QUERIES)
def test_parity_documented_traps(query):
    _assert_parity(query, ENTITIES)


@pytest.mark.parametrize("query,expected", TRAPS)
def test_trap_outcomes(query, expected):
    match, _score = fd.word_overlap_match_sql(query, ENTITIES)
    assert match == expected


def test_containment_precedence():
    # Containment wins immediately at entity order position 0, even
    # though a later entity would score higher on word overlap.
    ents = ["Tata Consultancy Services", "Tata"]
    py = fm.word_overlap_match("Tata", ents)
    sql = fd.word_overlap_match_sql("Tata", ents)
    assert py == sql == ("Tata Consultancy Services", 1.0)


def test_first_wins_tie_resolution():
    # Equal scores resolve to the EARLIER entity (strict `>` in the scan).
    ents = ["Alpha Motors", "Beta Motors"]
    py = fm.word_overlap_match("alpha motors ltd", ents)
    sql = fd.word_overlap_match_sql("alpha motors ltd", ents)
    assert py == sql
    assert py[0] == "Alpha Motors"


def test_reused_connection_regression():
    # fetchone() left an unfinished result that poisoned the next
    # execute() on a shared connection — regression-guarded here.
    conn = duckdb.connect()
    try:
        r1 = fd.word_overlap_match_sql("Adani Port", ENTITIES, conn=conn)
        r2 = fd.word_overlap_match_sql("tata steel", ENTITIES, conn=conn)
        r3 = fd.word_overlap_match_sql("zzz no match anywhere", ENTITIES, conn=conn)
    finally:
        conn.close()
    assert r1 == fm.word_overlap_match("Adani Port", ENTITIES)
    assert r2 == fm.word_overlap_match("tata steel", ENTITIES)
    assert r3 == fm.word_overlap_match("zzz no match anywhere", ENTITIES)


def test_pipeline_twin_matches_method_tags():
    py = fm.fuzzy_match("Adani Port", ENTITIES)
    sql = fd.fuzzy_match_sql("Adani Port", ENTITIES)
    assert sql[0] == py[0] == "Adani Ports and SEZ"
    assert sql[1] == "word_overlap_sql"
    assert py[1] == "word_overlap"
    assert sql[2] == py[2]
    # abbreviation stage identical in both pipelines
    assert fm.fuzzy_match("TCS", ENTITIES) == fd.fuzzy_match_sql("TCS", ENTITIES)


def test_seeded_random_parity():
    rng = random.Random(42)
    words = [
        "tata",
        "motors",
        "adani",
        "ports",
        "sez",
        "hdfc",
        "bank",
        "life",
        "power",
        "grid",
        "alpha",
        "beta",
        "india",
        "ltd",
        "limited",
        "asset",
        "management",
        "steel",
        "cement",
        "petro",
        "chem",
    ]
    seps = [" ", " & ", "-", ". ", ", "]
    suffixes = ["", " Ltd", " Limited", " Corporation", " (India)"]
    for _ in range(200):
        n_e = rng.randint(1, 4)
        ents = [
            "".join(rng.choice(words) + rng.choice(seps) for _ in range(rng.randint(1, 3))).rstrip(
                " ,&.-"
            )
            + rng.choice(suffixes)
            for _ in range(n_e)
        ]
        q = "".join(rng.choice(words) + rng.choice(seps) for _ in range(rng.randint(0, 3))).rstrip(
            " ,&.-"
        ) + rng.choice(suffixes)
        _assert_parity(q, ents)


def test_empty_entity_list():
    _assert_parity("tata", [])
    assert fd.word_overlap_match_sql("tata", []) == (None, 0.0)
