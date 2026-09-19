#!/usr/bin/env python3
"""Shared ontology vocabulary constants (ontology_governance #244c).

Single source of truth for the enum values the DDL CHECK clauses enforce
and the `Ontology doc rosters` static check gates against
`doc/design/ontology.md` §2 (rosters match_type_values,
identifier_type_values, source_tier_values, concept_status_values).
A value change must land here, in the DDL sites that embed it, and in
the doc roster — in the same change.
"""

# SKOS mapping lanes (concept_mappings.match_type; seed_concepts DDL)
MATCH_TYPE_VALUES = ("broadMatch", "closeMatch", "exactMatch", "narrowMatch")

# entity_identifiers.identifier_type (backfill_identifiers DDL)
IDENTIFIER_TYPE_VALUES = ("alias", "cin", "cik", "isin", "lei", "llpin")

# Row provenance tier (source_tier column, backfill_row_provenance DDL);
# `regulator` reserved — no producer yet.
SOURCE_TIER_VALUES = ("derive", "external", "manual", "migration", "regulator")

# Concept lifecycle status (concepts/concept_mappings.status; seed_concepts
# DDL): candidate -> active -> superseded. Seed-owned rows supersede (never
# delete) when the roster drops them, resurrect on re-add.
CONCEPT_STATUS_VALUES = ("active", "candidate", "superseded")


def sql_in(values: tuple[str, ...]) -> str:
    """Render a tuple as a SQL ``IN (...)`` list of single-quoted literals."""
    return ", ".join(f"'{v}'" for v in values)
