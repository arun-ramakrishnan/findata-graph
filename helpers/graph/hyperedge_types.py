#!/usr/bin/env python3
"""The hyperedge-type vocabulary (merger_demerger_hyperedges S1).

``hyper_edges.edge_type`` carries NO DDL CHECK (schema/sqlite/research.sql:
the table is ``UNIQUE(edge_type, label)`` and nothing more) — a new family
is a CODE registry, not a migration, so the schema version does NOT move
when a family is added. The registry is gated twice:

* ``helpers/validators/static_checks.py::roster_registry_sources`` exposes
  ``hyperedge_types``; the ontology-doc gate then requires the
  ``<!-- roster: hyperedge_types -->`` block in ``doc/design/ontology.md``
  §2.5 to equal this registry in BOTH directions (doc ⇄ code).
* ``helpers/misc/database_integrity_check.py::check_hyperedge_types``
  fails the integrity gate when a live ``hyper_edges`` row carries a type
  outside the registry, and when a merger/demerger incidence carries a
  role outside its family vocabulary.

Ten families were live on 2026-10-21 (event 1133, edition 222, industry
119, sub_sector 110, sector 42, org_membership 34, country 21, theme 12,
group 9, jv 6). Two join here — both n-ary, both written only by
``helpers/graph/derive_merger_hyperedges.py``:

``index``
    Index membership (hgx_over_dyads S2, 2026-10-30): the 57 ``listed_on_index``
    targets regrouped star-style — members are the listed companies, label the
    index name (116× compression over the 6,615 dyads; 753 members in ≥2
    indexes). Written by ``derive_hyperedges``; the 8 largest indexes exceed
    the community-fit giant threshold and stay centrality/query-only.

``merger``
    A + B (+ …) → survivor C.  The survivor is a MEMBER, not a second
    dyad: the old shape ("A merged with B") forced the fact into a
    dyadic ``acquired`` edge, which cannot carry the entity that exists
    after the merger.  Roles: ``constituent`` (each merged party),
    ``resultant`` (the survivor).

``demerger``
    parent → child (the company that was spun out).  Roles: ``parent``
    (the company that split), ``resultant`` (the spun-off child).

Roles live on ``hyper_incidences.role`` — they are NOT part of the
``event_roles`` roster in the ontology doc (different lane, different
vocabulary, same discipline as ``org_membership``'s promoter/holder).
"""

from __future__ import annotations

#: Every hyperedge family the store may carry.  Gated against
#: doc/design/ontology.md §2.5 and against the live rows.
HYPEREDGE_TYPES: frozenset[str] = frozenset(
    {
        "country",
        "demerger",
        "edition",
        "event",
        "group",
        "index",
        "industry",
        "jv",
        "merger",
        "org_membership",
        "sector",
        "sub_sector",
        "theme",
    }
)

#: The n-ary families owned by ``derive_merger_hyperedges``.
MERGER_HYPEREDGE_TYPES: frozenset[str] = frozenset({"merger", "demerger"})

#: Role vocabulary per merger family.  ``constituent``/``resultant``/
#: ``parent`` are per-family roles; they are NOT event roles.
MERGER_ROLE_BY_TYPE: dict[str, tuple[str, ...]] = {
    "merger": ("constituent", "resultant"),
    "demerger": ("parent", "resultant"),
}


def roles_for(edge_type: str) -> tuple[str, ...]:
    """Roles the given merger family may write ('' when not a merger family)."""
    return MERGER_ROLE_BY_TYPE.get(edge_type, ())
