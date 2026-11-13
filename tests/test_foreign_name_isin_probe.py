"""Tests for helpers/maintenance/foreign_name_isin_probe.py."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.maintenance.foreign_name_isin_probe import (  # noqa: E402
    _normalize,
    load_foreign_masters,
    probe,
)


def _ent_con():
    con = duckdb.connect(":memory:", **memory_cap_config())
    con.execute("CREATE TABLE entities (name VARCHAR, entity_type VARCHAR)")
    con.executemany(
        "INSERT INTO entities VALUES (?, ?)",
        [
            ("Tencent", "company"),
            ("Samsung Electronics", "company"),
            ("Hyundai Motor Company", "company"),
            ("Reliance Industries", "company"),  # Indian
            ("Schneider Electric", "company"),
            ("Person Ltd", "person"),
        ],
    )
    return con


def _src_con():
    con = duckdb.connect(":memory:", **memory_cap_config())
    con.execute(
        """
        CREATE TABLE exchange_listings (
            isin VARCHAR, name VARCHAR, exchange VARCHAR, symbol VARCHAR, segment VARCHAR
        )
        """
    )
    con.executemany(
        "INSERT INTO exchange_listings VALUES (?, ?, ?, ?, ?)",
        [
            ("KYG217651051", "TENCENT", "HKEX", "00700", "main"),
            (None, "Samsung Electronics", "KRX", "005930.KS", "kospi"),
            (None, "Hyundai Motor", "KRX", "005380.KS", "kospi"),
            (None, "Schneider Electric", "EURONEXT", "SU.PA", "main"),
            (None, "Reliance Industries", "NSE", "RELIANCE", "main"),  # India → excluded
            (None, "Some Random Co", "NASDAQ", "SRC", "main"),
        ],
    )
    return con


def test_normalize_strips_legal_suffixes_and_case():
    assert _normalize("Hyundai Motor Company") == "hyundai motor"
    assert _normalize("L'Oréal SA") == "l oreal"
    assert _normalize("TENCENT") == "tencent"


def test_load_foreign_masters_excludes_india():
    src = _src_con()
    masters = load_foreign_masters(src)
    exchanges = {m[2] for m in masters}
    assert exchanges == {"HKEX", "KRX", "EURONEXT", "NASDAQ"}
    src.close()


def test_probe_exact_and_fuzzy():
    ent = _ent_con()
    src = _src_con()
    r = probe(ent, src)
    assert r["masters_total"] == 5  # India rows excluded
    exact_entities = {m["entity"] for m in r["exact"]}
    # Tencent (TENCENT) + Samsung + Schneider + Hyundai all exact-normalized
    assert {
        "Tencent",
        "Samsung Electronics",
        "Schneider Electric",
        "Hyundai Motor Company",
    } <= exact_entities
    assert r["fuzzy_matches_ge066"] == 0  # no fuzzy matches in this synthetic set
    ent.close()
    src.close()


def test_probe_isin_only_filters_masters():
    ent = _ent_con()
    src = _src_con()
    r = probe(ent, src, min_isin=True)
    assert r["masters_total"] == 1  # only HKEX row carries ISIN
    assert r["exact_matches"] == 1
    assert r["exact"][0]["entity"] == "Tencent"
    ent.close()
    src.close()
