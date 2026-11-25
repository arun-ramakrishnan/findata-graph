#!/usr/bin/env python3
"""Fuzz tests for the SQLite→DuckDB schema converter (S4, swap_convert leg).

Properties over random schema fragments:
- `_translate_view`: only the tracked GLOB form is rewritten to
  regexp_matches; any other view SQL passes through with only the
  CREATE VIEW → CREATE OR REPLACE VIEW prefix change.
- `_translate_index`: multi-column indexes keep column order; NOCASE
  columns become lower(col); unparsed index SQL raises ValueError.
- `_emit_table_ddl`: PK, NOT NULL, DEFAULT and FK clauses are emitted
  from arbitrary column/fk specs.
- `_rowid_alias_pk`: single-column INTEGER PRIMARY KEY is the rowid
  alias; any other PK shape is not.

Can-fail proof: an index whose SQL is syntactically unparsed must
raise — a silent pass-through would hide conversion errors.
"""

from __future__ import annotations


import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from helpers.misc.swap_convert import (
    _emit_table_ddl,
    _rowid_alias_pk,
    _translate_index,
    _translate_view,
)

_SETTINGS = settings(max_examples=60, deadline=None)

_COL_NAMES = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz_"), min_size=1, max_size=16
)
_COL_TYPES = st.sampled_from(["INTEGER", "TEXT", "REAL", "BLOB", "DATE"])
_PK_ORD = st.one_of(st.none(), st.integers(min_value=1, max_value=3))
_NOTNULL = st.booleans()


def _col_spec():
    return st.fixed_dictionaries(
        {
            "name": _COL_NAMES,
            "type": _COL_TYPES,
            "sqlite_type": _COL_TYPES,
            "notnull": _NOTNULL,
            "default": st.one_of(
                st.none(), st.text(alphabet=st.sampled_from("0123456789'-"), min_size=1, max_size=8)
            ),
            "pk": _PK_ORD,
        }
    )


@st.composite
def _table_spec(draw):
    n = draw(st.integers(min_value=1, max_value=6))
    cols = [draw(_col_spec()) for _ in range(n)]
    # two columns cannot share the same pk order
    seen_pk: set[int] = set()
    for c in cols:
        if c["pk"] is not None:
            if c["pk"] in seen_pk:
                c["pk"] = None
            else:
                seen_pk.add(c["pk"])
    has_fk = draw(st.booleans())
    fks = []
    if has_fk and cols:
        src = draw(st.sampled_from(cols))["name"]
        tgt = draw(st.sampled_from(cols))["name"]
        fks.append(
            {
                "parent": draw(_COL_NAMES),
                "from": [src],
                "to": [tgt],
                "on_update": "NO ACTION",
                "on_delete": "NO ACTION",
            }
        )
    return {"columns": cols, "fks": fks}


@_SETTINGS
@given(st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz_"), min_size=1, max_size=20))
def test_translate_view_passthrough_sql(ident):
    sql = f"CREATE VIEW v{ident} AS SELECT 1"
    out = _translate_view(sql)
    assert out.startswith("CREATE OR REPLACE VIEW ")
    assert "regexp_matches" not in out
    assert "GLOB" not in out


@_SETTINGS
@given(st.integers(min_value=1, max_value=4))
def test_translate_view_glob_becomes_regexp(n_digits):
    glob_sql = f"CREATE VIEW v AS SELECT * FROM t WHERE source_ref GLOB '*[0-9]{'[0-9]' * (n_digits - 1)}_WEB.xml'"  # noqa: S608  # test-only int interpolation
    out = _translate_view(glob_sql)
    assert "regexp_matches(source_ref, " in out
    assert "GLOB" not in out
    assert out.endswith("$')")
    # the collapsed regex class must carry exactly n_digits
    marker = "[0-9]"
    start = out.index(marker)
    inner = out[start : out.index("_WEB")]
    assert inner == f"[0-9]{{{n_digits}}}"


@_SETTINGS
@given(
    st.lists(
        st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz_"), min_size=1, max_size=12),
        min_size=1,
        max_size=5,
    ),
    st.lists(st.booleans(), min_size=1, max_size=5),
)
def test_translate_index_columns_and_nocase(cols, nocase_flags):
    sql_cols = []
    want = []
    for name, nocase in zip(cols, nocase_flags):
        sql_cols.append(f"{name} COLLATE NOCASE" if nocase else name)
        want.append(f"lower({name})" if nocase else name)
    sql = f"CREATE INDEX idx ON tbl ({', '.join(sql_cols)})"
    out = _translate_index("tbl", sql)
    assert out == f'CREATE INDEX "idx" ON "tbl" ({", ".join(want)})'


def _test_translate_index_unparsed_raises(sql: str) -> None:
    with pytest.raises(ValueError):
        _translate_index("tbl", sql)


@_SETTINGS
@given(st.sampled_from(["CREATE INDEX idx ON tbl", "CREATE INDEX idx", "CREATE INDEX", "garbage"]))
def test_translate_index_malformed_sql_raises(sql: str) -> None:
    _test_translate_index_unparsed_raises(sql)


@_SETTINGS
@given(_table_spec())
def test_emit_table_ddl_shape(spec):
    ddl = _emit_table_ddl("t", spec)
    assert ddl.startswith('CREATE TABLE "t" (')
    assert ddl.endswith("\n)")
    pk_cols = {c["name"] for c in spec["columns"] if c["pk"]}
    if pk_cols:
        assert "PRIMARY KEY (" in ddl
        for name in pk_cols:
            assert f'"{name}"' in ddl
    for c in spec["columns"]:
        if c["notnull"] or c["name"] in pk_cols:
            assert "NOT NULL" in ddl
    for fk in spec["fks"]:
        assert "FOREIGN KEY (" in ddl
        assert f'REFERENCES "{fk["parent"]}"' in ddl


@_SETTINGS
@given(_table_spec())
def test_rowid_alias_pk_only_single_integer(spec):
    pk_cols = sorted((c for c in spec["columns"] if c["pk"]), key=lambda c: c["pk"])
    got = _rowid_alias_pk(spec)
    if len(pk_cols) == 1 and pk_cols[0]["sqlite_type"].strip().upper() == "INTEGER":
        assert got == pk_cols[0]["name"]
    else:
        assert got is None


def test_can_fail_index_unparsed_raises():
    _test_translate_index_unparsed_raises("INDEX idx ON t (a)")
