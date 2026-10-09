# Tests for helpers/maintenance/gc_embed_cache.py
"""The stamp-aware retention rule (2026-10-08): a cohort's reference
recipe is anchored to its ACTIVE model stamp, so it can only prove
dead-ness for that model's rows. Rows of a non-active model are rollback
insurance — they survive even `--apply` GC (the first multi-model survey
flagged all 787 granite script rows "dead" the moment the surface
flipped to gemma)."""

import sqlite3

import pytest

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


# --------------------------------------------------------------------------- #
# convo cohort stamp (convo_embed_gc_stamp S3)                               #
# --------------------------------------------------------------------------- #


def _seed_convo(monkeypatch, tmp_path, *, stamp_sql, foreign=True):
    """convo-shaped store + index. Cache: one live granite row (the stamp's
    model), plus one FOREIGN-model row whose fate is the whole point."""
    store = tmp_path / "embed_store.db"
    con = sqlite3.connect(store)
    con.execute(CACHE_DDL_BARE)
    rows = [(_hash("live snippet"), "granite", b"\x00" * 4, "convo")]
    if foreign:
        rows.append((_hash("gemma basis of same text"), "gemma", b"\x00" * 4, "convo"))
    con.executemany(
        f"INSERT INTO {gce.CACHE_TABLE} (text_hash, model, embedding, source) VALUES (?,?,?,?)",  # noqa: S608
        rows,
    )
    con.commit()
    con.close()
    monkeypatch.setattr(gce, "STORE", store)

    index = tmp_path / "convo_search.duckdb"
    import duckdb

    dcon = duckdb.connect(str(index))
    dcon.execute("CREATE TABLE convo_search (snippet VARCHAR)")
    dcon.execute("CREATE TABLE convo_meta (key VARCHAR, value VARCHAR)")
    if stamp_sql:
        dcon.execute("INSERT INTO convo_meta VALUES ('embed_model', 'granite')")
    dcon.execute("INSERT INTO convo_search VALUES ('live snippet')")
    dcon.close()

    refs = (
        gce.Ref(
            "convo",
            index,
            "SELECT snippet FROM convo_search",
            stamp_sql=stamp_sql,
        ),
    )
    return refs, store


def test_convo_stamp_resolves_and_arms_rollback_insurance(monkeypatch, tmp_path):
    """With a stamp, the foreign-model row is RETAINED (insurance), not dead.
    The live row stays live. Acceptance: stamp resolves + arms."""
    refs, store = _seed_convo(
        monkeypatch, tmp_path, stamp_sql="SELECT value FROM convo_meta WHERE key = 'embed_model'"
    )
    live, _counts, stamps = gce._read_refs(refs)
    assert stamps["convo"] == "granite"
    out = gce.survey(refs)
    assert out["retained"] == 1, out
    assert out["dead"] == 0, out
    assert len(_rows(store)) == 2  # nothing deleted in report mode


def test_convo_without_stamp_keeps_legacy_behaviour(monkeypatch, tmp_path):
    """The same population with NO stamp: the foreign row is judged on text
    alone and reads dead. Pins that the change is what arms the insurance."""
    refs, _store = _seed_convo(monkeypatch, tmp_path, stamp_sql=None)
    _live, _counts, stamps = gce._read_refs(refs)
    assert stamps["convo"] is None
    out = gce.survey(refs)
    assert out["retained"] == 0, out
    assert out["dead"] == 1, out


def test_convo_stamp_on_a_missing_table_aborts_the_gc(monkeypatch, tmp_path):
    """convo_meta must be spelled exactly: a wrong table raises inside the
    stamp read and _read_refs re-raises `refusing to GC` rather than
    silently yielding None (which would disarm every cohort's insurance)."""
    refs, _store = _seed_convo(
        monkeypatch, tmp_path, stamp_sql="SELECT value FROM convo_search_info WHERE key = 'embed_model'"
    )
    with pytest.raises(RuntimeError, match="refusing to GC"):
        gce._read_refs(refs)


def test_live_convo_ref_carries_a_stamp_sql():
    """The production Ref itself, not just a seeded one: convo was the only
    cohort in DEFAULT_REFS with no stamp_sql."""
    convo = next(r for r in gce.DEFAULT_REFS if r.label == "convo")
    assert convo.stamp_sql is not None
    assert "convo_meta" in convo.stamp_sql
    unstamped = [r.label for r in gce.DEFAULT_REFS if r.stamp_sql is None]
    assert unstamped == [], f"cohorts left without rollback insurance: {unstamped}"
