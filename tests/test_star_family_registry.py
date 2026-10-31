"""hgx_over_dyads S4: the star/symmetric source registry stays consistent.

The hyperedge family vocabulary is code-side (``hyper_edges.edge_type``
has no DDL CHECK) and gated by the ontology-doc roster in both directions
(static_checks). What is NOT gated anywhere else is the wiring between
``derive_hyperedges``' source registries and the type registry — a family
added to ``_STAR_SOURCES`` but forgotten in ``HYPEREDGE_TYPES`` would
derive rows ``check_hyperedge_types`` then fails on at gate time, and a
typo'd dyad type would silently derive an empty family. Pin all four
invariants here.
"""

from helpers.graph.derive_hyperedges import _STAR_SOURCES, _SYMMETRIC_SOURCES
from helpers.graph.hyperedge_types import HYPEREDGE_TYPES, MERGER_HYPEREDGE_TYPES


def test_every_source_family_is_registered():
    for family, _ in _STAR_SOURCES:
        assert family in HYPEREDGE_TYPES, f"star family {family!r} not in HYPEREDGE_TYPES"
    for family, _, _ in _SYMMETRIC_SOURCES:
        assert family in HYPEREDGE_TYPES, f"symmetric family {family!r} not in HYPEREDGE_TYPES"


def test_no_duplicate_dyad_types_across_sources():
    star_dyads = [dyad for _, dyad in _STAR_SOURCES]
    sym_dyads = [dyad for _, dyad, _ in _SYMMETRIC_SOURCES]
    all_dyads = star_dyads + sym_dyads
    assert len(all_dyads) == len(set(all_dyads)), "a dyad type feeds two families"


def test_star_and_symmetric_families_are_disjoint():
    star = {family for family, _ in _STAR_SOURCES}
    sym = {family for family, _, _ in _SYMMETRIC_SOURCES}
    assert not star & sym, f"family in both lanes: {star & sym}"


def test_sources_never_shadow_merger_families():
    derived = {family for family, _ in _STAR_SOURCES} | {
        family for family, _, _ in _SYMMETRIC_SOURCES
    }
    assert not derived & MERGER_HYPEREDGE_TYPES, (
        "merger/demerger are written only by derive_merger_hyperedges; "
        "the dyad regroup must not shadow them"
    )


def test_s4_tree_portfolio_families_registered():
    assert ("subsidiary", "subsidiary_of") in _STAR_SOURCES
    assert ("supply", "supplier_to") in _STAR_SOURCES
    assert {"subsidiary", "supply"} <= HYPEREDGE_TYPES
