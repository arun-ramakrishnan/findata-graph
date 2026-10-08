# Tests for helpers/maintenance/gc_embed_cache.py
"""The stamp-aware retention rule (2026-10-08): a cohort's reference
recipe is anchored to its ACTIVE model stamp, so it can only prove
dead-ness for that model's rows. Rows of a non-active model are rollback
insurance — they survive even `--apply` GC (the first multi-model survey
flagged all 787 granite script rows "dead" the moment the surface
flipped to gemma)."""

import sqlite3

from helpers.core.embed_cache import CACHE_DDL_BARE, _hash
from helpers.maintenance import gc_embed_cache as gce


def _seed(monkeypatch, tmp_path):
    """Tmp embed store + a gemma-stamped script index with one live text.

    Cache rows: (live basis, gemma) referenced; (other basis, granite)
    rollback - unreferenced but MUST be retained; (dead basis, gemma)
    genuinely dead - the only deletable row."""
    store = tmp_path / "embed_store.db"
    con = sqlite3.connect(store)
    con.execute(CACHE_DDL_BARE)
    con.executemany(
        f"INSERT INTO {gce.CACHE_TABLE} (text_hash, model, embedding, source) VALUES (?,?,?,?)",  # noqa: S608
        [
            (_hash("live\npurpose\nbody"), "gemma", b"\x00" * 4, "script"),
            (_hash("old plain basis"), "granite", b"\x00" * 4, "script"),
            (_hash("dead text"), "gemma", b"\x00" * 4, "script"),
        ],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(gce, "STORE", store)

    index = tmp_path / "script_search.db"
    con = sqlite3.connect(index)
    con.execute("CREATE TABLE script_search (title TEXT, purpose TEXT, content TEXT)")
    con.execute("CREATE TABLE script_search_info (key TEXT, value TEXT)")
    con.execute("INSERT INTO script_search_info VALUES ('embed_model', 'gemma')")
    # _doc_text basis = f"{title}\n{purpose}\n{content}" -> 'live\npurpose\nbody'
    con.execute("INSERT INTO script_search VALUES ('live', 'purpose', 'body')")
    con.commit()
    con.close()

    refs = (
        gce.Ref(
            "script",
            index,
            "SELECT title, purpose, content FROM script_search",
            gce._doc_text,
            stamp_sql="SELECT value FROM script_search_info WHERE key = 'embed_model'",
        ),
    )
    return refs, store


def _rows(store):
    con = sqlite3.connect(store)
    try:
        return sorted(con.execute("SELECT model, text_hash FROM embed_cache").fetchall())
    finally:
        con.close()


def test_survey_retains_other_model(monkeypatch, tmp_path):
    refs, _store = _seed(monkeypatch, tmp_path)
    out = gce.survey(refs)
    # only the ACTIVE model's unreferenced row is dead; the granite
    # rollback row is retained, not dead
    assert out["dead"] == 1
    assert out["retained"] == 1
    assert out["dead_by_source"] == {"script": 1}
    assert out["retained_by_source"] == {"script": 1}


def test_apply_deletes_only_active_model_dead(monkeypatch, tmp_path):
    refs, store = _seed(monkeypatch, tmp_path)
    out = gce.gc(refs, apply=True)
    assert out["deleted"] == 1
    remaining = _rows(store)
    # the referenced gemma row AND the granite rollback row both survive
    assert sorted(m for m, _h in remaining) == ["gemma", "granite"]
    assert any(m == "granite" and h == _hash("old plain basis") for m, h in remaining)
    assert any(m == "gemma" and h == _hash("live\npurpose\nbody") for m, h in remaining)


def test_reference_recipe_follows_stamp(monkeypatch, tmp_path):
    """A prefixed model hashes DIFFERENT cache keys for the same row —
    the reference recipe must switch with the stamp or the ACTIVE
    model's live rows all read dead (2026-10-08: the granite _doc_text
    recipe marked all 550 gemma script rows dead)."""
    store = tmp_path / "embed_store.db"
    con = sqlite3.connect(store)
    con.execute(CACHE_DDL_BARE)
    con.executemany(
        f"INSERT INTO {gce.CACHE_TABLE} (text_hash, model, embedding, source) VALUES (?,?,?,?)",  # noqa: S608
        [
            # the gemma-prefixed basis, cached under the gemma label
            (_hash("title: live | text: purpose\nbody"), "gemma", b"\x00" * 4, "script"),
            # the same row's GRANITE-shaped basis hash — would be 'live'
            # under the granite recipe, but the stamp says gemma
            (_hash("live\npurpose\nbody"), "granite", b"\x00" * 4, "script"),
        ],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(gce, "STORE", store)

    index = tmp_path / "script_search.db"
    con = sqlite3.connect(index)
    con.execute("CREATE TABLE script_search (title TEXT, purpose TEXT, content TEXT)")
    con.execute("CREATE TABLE script_search_info (key TEXT, value TEXT)")
    con.execute("INSERT INTO script_search_info VALUES ('embed_model', 'gemma')")
    con.execute("INSERT INTO script_search VALUES ('live', 'purpose', 'body')")
    con.commit()
    con.close()

    refs = (
        gce.Ref(
            "script",
            index,
            "SELECT title, purpose, content FROM script_search",
            gce._doc_text,
            stamp_sql="SELECT value FROM script_search_info WHERE key = 'embed_model'",
            text_by_model={
                "gemma": lambda row: f"title: {row[0] or 'none'} | text: {row[1]}\n{row[2]}"
            },
        ),
    )
    out = gce.survey(refs)
    # the gemma row is LIVE under its own recipe; the granite-shaped row
    # is retained (other model), NOT dead
    assert out["dead"] == 0
    assert out["retained"] == 1
    assert out["referenced_by_source"] == {"script": 1}


def test_retire_dry_run_then_apply(monkeypatch, tmp_path):
    """--retire-model is the explicit counterpart to stamp-aware
    retention: passive GC can never delete other-model rows; a NAMED
    retirement can, after a dry-run."""
    refs, store = _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(gce, "DEFAULT_REFS", refs)

    out = gce.retire("granite", "script", apply=False)
    assert out["would_delete"] == {"script": 1}
    assert out["total"] == 1
    assert len(_rows(store)) == 3  # dry run touches nothing

    out = gce.retire("granite", "script", apply=True)
    assert out["deleted"] == 1
    # retire is MODEL-targeted: the granite rollback row goes, both gemma
    # rows (live + the genuinely-dead one that passive GC would own) stay
    assert set(_rows(store)) == {
        ("gemma", _hash("dead text")),
        ("gemma", _hash("live\npurpose\nbody")),
    }


def test_retire_refuses_active_stamp(monkeypatch, tmp_path):
    refs, store = _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(gce, "DEFAULT_REFS", refs)
    try:
        gce.retire("gemma", "script", apply=False)
        raise AssertionError("expected the active-stamp refusal")
    except RuntimeError as exc:
        assert "ACTIVE stamp" in str(exc)
    assert len(_rows(store)) == 3


def test_stampless_cohort_keeps_legacy_behavior(monkeypatch, tmp_path):
    """No stamp SQL -> the cohort keeps the old unreferenced-equals-dead
    rule (convo today; any future index without a stamp table)."""
    store = tmp_path / "embed_store.db"
    con = sqlite3.connect(store)
    con.execute(CACHE_DDL_BARE)
    con.executemany(
        f"INSERT INTO {gce.CACHE_TABLE} (text_hash, model, embedding, source) VALUES (?,?,?,?)",  # noqa: S608
        [
            (_hash("live text"), "granite", b"\x00" * 4, "convo"),
            (_hash("dead text"), "granite", b"\x00" * 4, "convo"),
        ],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(gce, "STORE", store)

    index = tmp_path / "convo.duckdb"
    import duckdb

    dcon = duckdb.connect(str(index))
    dcon.execute("CREATE TABLE convo_search AS SELECT 'live text' AS snippet")
    dcon.close()
    refs = (gce.Ref("convo", index, "SELECT snippet FROM convo_search"),)
    out = gce.gc(refs, apply=True)
    # convo snippet text is hashed raw by the default text lambda
    assert out["deleted"] == 1
    assert _rows(store) == [("granite", _hash("live text"))]
