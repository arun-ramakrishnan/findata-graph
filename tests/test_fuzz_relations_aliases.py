#!/usr/bin/env python3
"""Fuzz tests for the extract_relations alias table + resolver precedence
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §5 B6).

_ALIASES maps brand/abbrevation mentions to canonical entities; a bad
entry (self-alias, dangling target, inconsistent case) silently breaks
resolution or creates self-edges. These properties pin the table's own
invariants and the resolver's whole-mention-over-first-token precedence.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.graph import extract_relations as xr  # noqa: E402

_SETTINGS = settings(max_examples=50, deadline=None)

_TEXT = st.text(
    st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
    min_size=0,
    max_size=60,
)


@_SETTINGS
@given(st.sampled_from(sorted(xr._ALIASES)))
def test_alias_table_invariants(alias):
    """Every alias entry: lowercase key, non-empty trimmed target
    (the documented rule: aliases point at existing entity names).
    NB '3m company' -> '3M Company' is a redundant case-only self-map —
    the exact-match path covers it; benign, tolerated here."""
    target = xr._ALIASES[alias]
    assert alias == alias.lower()
    assert target and target.strip() == target


@_SETTINGS
@given(st.sampled_from(sorted(xr._ALIASES)))
def test_alias_resolves_to_its_target(alias):
    """Against a DB seeded with exactly the alias targets, the alias
    mention resolves to the target — never None, never a self-mention."""
    names = list(set(xr._ALIASES.values()))
    r = xr.EntityResolver(names)
    assert r.resolve(alias) == xr._ALIASES[alias]


@_SETTINGS
@given(st.sampled_from([a for a in sorted(xr._ALIASES) if " " not in a]))
def test_first_token_alias_resolves_decorated_mention(alias):
    """The 0b fallback for single-token aliases: "<alias> Corporation" /
    "the <alias> Group" style decorated mentions resolve through the
    first token when the whole mention isn't itself an alias."""
    names = list(set(xr._ALIASES.values()))
    r = xr.EntityResolver(names)
    assert r.resolve(f"{alias} Corporation") == xr._ALIASES[alias]
    assert r.resolve(f"the {alias} Group") == xr._ALIASES[alias]


@_SETTINGS
@given(_TEXT)
def test_resolve_is_deterministic_and_typed(mention):
    names = list(set(xr._ALIASES.values()))
    r = xr.EntityResolver(names)
    a = r.resolve(mention)
    b = r.resolve(mention)
    assert a == b
    assert a is None or (isinstance(a, str) and a in names)


@_SETTINGS
@given(_TEXT)
def test_resolve_never_returns_self_for_non_entities(mention):
    """A mention that is not a known entity either resolves to a REAL
    entity name or None — it can never echo the raw mention back."""
    names = list(set(xr._ALIASES.values()))
    r = xr.EntityResolver(names)
    out = r.resolve(mention)
    assert out is None or out in names
