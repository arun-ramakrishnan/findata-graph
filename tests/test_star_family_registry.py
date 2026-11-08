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
    for family, _dyad, _min_k in _STAR_SOURCES:
        assert family in HYPEREDGE_TYPES, f"star family {family!r} not in HYPEREDGE_TYPES"
    for family, _dyad, _key in _SYMMETRIC_SOURCES:
        assert family in HYPEREDGE_TYPES, f"symmetric family {family!r} not in HYPEREDGE_TYPES"


def test_no_duplicate_dyad_types_across_sources():
    star_dyads = [dyad for _family, dyad, _min_k in _STAR_SOURCES]
    sym_dyads = [dyad for _family, dyad, _key in _SYMMETRIC_SOURCES]
    all_dyads = star_dyads + sym_dyads
    assert len(all_dyads) == len(set(all_dyads)), "a dyad type feeds two families"


def test_star_and_symmetric_families_are_disjoint():
    star = {family for family, _dyad, _min_k in _STAR_SOURCES}
    sym = {family for family, _dyad, _key in _SYMMETRIC_SOURCES}
    assert not star & sym, f"family in both lanes: {star & sym}"


def test_star_min_portfolio_size_is_optional_positive_int():
    """k=None keeps every label; k>=2 drops sub-threshold portfolios."""
    for family, _dyad, min_k in _STAR_SOURCES:
        assert min_k is None or (isinstance(min_k, int) and min_k >= 2), (
            f"star family {family!r} carries an unusable min portfolio size {min_k!r}"
        )


def test_sources_never_shadow_merger_families():
    derived = {family for family, _dyad, _min_k in _STAR_SOURCES} | {
        family for family, _dyad, _key in _SYMMETRIC_SOURCES
    }
    assert not derived & MERGER_HYPEREDGE_TYPES, (
        "merger/demerger are written only by derive_merger_hyperedges; "
        "the dyad regroup must not shadow them"
    )


def test_s4_tree_portfolio_families_registered():
    registered = {(family, dyad) for family, dyad, _min_k in _STAR_SOURCES}
    assert ("subsidiary", "subsidiary_of") in registered
    assert ("supply", "supplier_to") in registered
    assert {"subsidiary", "supply"} <= HYPEREDGE_TYPES


def test_s4_tree_portfolio_families_carry_k3_threshold():
    """The k>=3 portfolios are what keep the tree/supply lanes signal-dense."""
    thresholds = {(family, dyad): min_k for family, dyad, min_k in _STAR_SOURCES}
    assert thresholds[("subsidiary", "subsidiary_of")] == 3
    assert thresholds[("supply", "supplier_to")] == 3
