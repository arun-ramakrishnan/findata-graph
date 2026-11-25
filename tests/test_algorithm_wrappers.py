"""Unit pins for ``helpers.graph.algorithm_wrappers`` -- the L3 graph API
that ``helpers.graph.query`` re-exports (``pagerank``,
``pagerank_weighted``, ``weakly_connected_components``,
``clustering_coefficient``).

The wrappers moved out of query.py and had no test of their own until the
2026-11-24 coverage sweep (doc/improvements/proposals/
test_coverage_gap_sweep_nov24.md, S3). Two things are pinned: the surface
callers import (signatures, ``__all__``, the re-export block in query.py),
and the contract with the Onager kernels -- projection, weights, the
company filter and the sort orders -- against a capturing stand-in, so no
graph store or extension is needed here.
"""

from __future__ import annotations

import ast
import inspect
import typing
from pathlib import Path

import pytest

from helpers.graph import algorithm_wrappers as aw
from helpers.graph import query

QUERY_PY = Path(query.__file__)

PRIVATE = ("_company_names", "_label_to_edge_type", "_label_to_table")

#: name -> (positional parameters, keyword parameters with defaults)
_SIGNATURES = {
    "pagerank": (
        ("con", "edge_label", "vertex_label", "as_of"),
        {"edge_label": "Economic", "vertex_label": "Entity", "as_of": None},
    ),
    "pagerank_weighted": (
        ("con", "edge_label", "vertex_label", "as_of"),
        {"edge_label": "Economic", "vertex_label": "Entity", "as_of": None},
    ),
    "weakly_connected_components": (
        ("con", "edge_label", "vertex_label"),
        {"edge_label": "BelongsTo", "vertex_label": "Entity"},
    ),
    "clustering_coefficient": (
        ("con", "edge_label", "vertex_label"),
        {"edge_label": "BelongsTo", "vertex_label": "Entity"},
    ),
}
_RETURNS = {
    "pagerank": list[tuple[str, float]],
    "pagerank_weighted": list[tuple[str, float]],
    "weakly_connected_components": list[tuple[str, int]],
    "clustering_coefficient": list[tuple[str, float]],
}
_LABELS = {
    "BelongsTo",
    "HasCompany",
    "CompetesWith",
    "JvWith",
    "SameGroup",
    "SuppliesTo",
    "CustomerOf",
    "AcquiredBy",
    "SubsidiaryOf",
    "CoMentionedIn",
    "SemanticPeer",
    "InvestedIn",
}


class _FakeCon:
    """Answers the one query the wrappers issue themselves."""

    def __init__(self, companies: tuple[str, ...] = ("Alpha", "Beta", "Gamma")) -> None:
        self.companies = companies
        self.sql: list[str] = []

    def execute(self, sql: str):
        self.sql.append(sql)
        return self

    def fetchall(self) -> list[tuple[str]]:
        return [(name,) for name in self.companies]


@pytest.fixture
def kernel(monkeypatch):
    """Capture every ``_extension_first`` dispatch and hand back a fixed result."""
    calls: list[tuple[object, dict]] = []
    result: dict[str, dict] = {}

    def fake_extension_first(fn, con, **kwargs):
        calls.append((fn, kwargs))
        return result.get("scores", {})

    monkeypatch.setattr(aw, "_extension_first", fake_extension_first)
    return calls, result


def test_signatures_are_the_public_contract():
    assert set(aw.__all__) == set(_SIGNATURES)
    for name, (positional, keyword) in _SIGNATURES.items():
        params = inspect.signature(getattr(aw, name)).parameters
        assert tuple(params) == positional, name
        assert set(keyword) <= set(params), name
        for pname, param in params.items():
            assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, (name, pname)
            if pname in keyword:
                assert param.default == keyword[pname], (name, pname)
            else:
                assert param.default is inspect.Parameter.empty, (name, pname)
        assert typing.get_type_hints(getattr(aw, name))["return"] == _RETURNS[name], name


def test_query_re_exports_exactly_the_wrapper_module_surface():
    for name in (*aw.__all__, *PRIVATE):
        assert getattr(query, name) is getattr(aw, name), name

    tree = ast.parse(QUERY_PY.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "helpers.graph.algorithm_wrappers":
            imported.update(alias.name for alias in node.names)
    assert imported == set(aw.__all__) | set(PRIVATE)

    # extraction drift: query.py must not grow a same-named definition of its own
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    assert not defined & imported, defined & imported


def test_edge_labels_resolve_through_the_registry():
    assert set(query.EDGE_REGISTRY_BY_LABEL) == _LABELS
    for label in _LABELS:
        assert aw._label_to_edge_type(label) == query.EDGE_REGISTRY_BY_LABEL[label]["edge_type"]
        table = aw._label_to_table(label)
        assert isinstance(table, str) and table
    # the two historical labels are not registered; they are mapped by hand
    assert aw._label_to_edge_type("BelongsToHierarchy") == "belongs_to"
    assert aw._label_to_edge_type("ExposedTo") == "exposed_to"
    assert aw._label_to_table("BelongsToHierarchy") is None
    assert aw._label_to_table("NoSuchLabel") is None
    with pytest.raises(ValueError, match="unknown edge label: NoSuchLabel"):
        aw._label_to_edge_type("NoSuchLabel")


def test_company_names_is_the_one_query_the_wrappers_issue():
    con = _FakeCon(("Alpha", "Alpha", "Beta"))
    assert aw._company_names(con) == {"Alpha", "Beta"}
    assert con.sql == ["SELECT name FROM v_company"]


def test_pagerank_runs_the_economic_projection_unweighted(kernel):
    calls, result = kernel
    result["scores"] = {"Alpha": 0.2, "Beta": 0.5, "Gamma": 0.5, "Sector:IT": 0.9}

    rows = aw.pagerank(_FakeCon())

    assert rows == [("Beta", 0.5), ("Gamma", 0.5), ("Alpha", 0.2)]  # score desc, name breaks ties
    ((fn, kwargs),) = calls
    assert fn is aw.onager_pagerank
    assert kwargs["edge_types"] is aw.ECONOMIC_EDGE_TYPES  # the shared list, not a copy
    assert kwargs["weighted"] is False
    assert kwargs["as_of"] is None
    assert isinstance(rows[0][1], float)


def test_pagerank_named_label_as_of_and_weighted_variant(kernel):
    calls, result = kernel
    result["scores"] = {"Alpha": 1, "Beta": 2}
    con = _FakeCon()
    expected = [("Beta", 2.0), ("Alpha", 1.0)]

    assert aw.pagerank(con, as_of="2024-01-01") == expected  # economic projection, as_of carried
    assert aw.pagerank(con, edge_label="SuppliesTo", as_of="2024-01-01") == expected
    assert aw.pagerank_weighted(con, as_of="2024-01-01") == expected
    assert aw.pagerank_weighted(con, edge_label="SuppliesTo") == expected

    economic, named, weighted_economic, weighted_named = [kwargs for _, kwargs in calls]
    assert economic["edge_types"] is aw.ECONOMIC_EDGE_TYPES
    assert economic["weighted"] is False
    assert economic["as_of"] == "2024-01-01"
    assert named["edge_types"] == ["supplier_to"]  # SuppliesTo resolves through the registry
    assert named["weighted"] is False
    assert named["as_of"] == "2024-01-01"
    assert weighted_economic["edge_types"] is aw.ECONOMIC_EDGE_TYPES
    assert weighted_economic["weighted"] is True
    assert weighted_economic["as_of"] == "2024-01-01"
    assert weighted_named["edge_types"] == ["supplier_to"]
    assert weighted_named["weighted"] is True
    assert weighted_named["as_of"] is None


def test_membership_wrappers_use_only_the_edge_projection(kernel):
    calls, result = kernel
    con = _FakeCon(("Alpha", "Beta"))

    # ints in, component order beats name order, a non-company dropped
    result["scores"] = {"Beta": 1, "Alpha": 2, "Sector:IT": 0}
    components = aw.weakly_connected_components(con, edge_label="SubsidiaryOf")
    assert components == [("Beta", 1), ("Alpha", 2)]  # (component_id, name), sorted by id
    assert isinstance(components[0][1], int)

    result["scores"] = {"Beta": 0.1, "Alpha": 0.3}
    assert aw.clustering_coefficient(con, edge_label="SubsidiaryOf") == [
        ("Alpha", 0.3),
        ("Beta", 0.1),
    ]

    (component_kwargs, clustering_kwargs) = (kwargs for _, kwargs in calls)
    (component_fn, clustering_fn) = (fn for fn, _ in calls)
    assert component_fn is aw.onager_components
    assert clustering_fn is aw.onager_clustering
    for kwargs in (component_kwargs, clustering_kwargs):
        assert set(kwargs) == {"edge_types"}  # no weights, no as_of
        assert kwargs["edge_types"] == ["subsidiary_of"]
