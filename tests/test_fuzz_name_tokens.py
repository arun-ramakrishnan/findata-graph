#!/usr/bin/env python3
"""Fuzz tests for name tokenization (S4, name_tokens leg).

Two tokenizers:
- `helpers.graph.extract_relations._tokens` — memoized canonicalization
  used by the resolver; property: deterministic frozenset, no empty
  tokens, no whitespace inside tokens, memoized (cache hits grow).
- `helpers.maintenance.enrich_from_yfinance._name_tokens` — corporate
  name tokens; properties: accents fold (NFKD), `&` behaves like
  " and ", plural folding is symmetric, suffixes stripped, tokens are
  lower-case alnum with len > 1, deterministic.

Can-fail proofs: accented vs de-accented variants tokenize to the same
set; a plural and its singular meet after folding.
"""

from __future__ import annotations

import unicodedata

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from helpers.graph.extract_relations import _tokens
from helpers.maintenance.enrich_from_yfinance import _name_tokens

_SETTINGS = settings(max_examples=60, deadline=None)

_ACCENTED = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz " + "áéíóúñüçàè"),
    min_size=1,
    max_size=50,
)
_TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=0, max_size=80)


@_SETTINGS
@given(_TEXT)
def test_tokens_deterministic_and_memoized(name):
    first = _tokens(name)
    second = _tokens(name)
    assert isinstance(first, frozenset)
    assert first == second
    assert all(t for t in first)
    assert all(" " not in t for t in first)


@_SETTINGS
@given(_TEXT)
def test_tokens_cache_hits_grow(name):
    info_before = _tokens.cache_info()
    _tokens(name)
    _tokens(name)
    info_after = _tokens.cache_info()
    assert info_after.hits >= info_before.hits + 1


def _strip_accents(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


@_SETTINGS
@given(_ACCENTED)
def test_name_tokens_accent_fold(name):
    assert _name_tokens(_strip_accents(name)) == _name_tokens(name)


_AMPERSAND_TEXT = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz &"),
    min_size=2,
    max_size=50,
).filter(lambda s: "&" in s)


@_SETTINGS
@given(_AMPERSAND_TEXT)
def test_name_tokens_ampersand_equals_and(name):
    assert _name_tokens(name.replace("&", " and ")) == _name_tokens(name)


@_SETTINGS
@given(st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz "), min_size=1, max_size=60))
def test_name_tokens_lower_alnum_nonempty(name):
    toks = _name_tokens(name)
    assert all(tok == tok.lower() for tok in toks)
    assert all(tok.isalnum() for tok in toks)
    assert all(len(tok) > 1 for tok in toks)


@_SETTINGS
@given(_TEXT)
def test_name_tokens_deterministic(name):
    assert _name_tokens(name) == _name_tokens(name)


def test_can_fail_accent_fold():
    assert _name_tokens("Shangar Décor") == _name_tokens("Shangar Decor")


def test_can_fail_plural_fold():
    assert _name_tokens("Lambodhara Textiles") == _name_tokens("Lambodhara Textile")


def test_name_tokens_none_raises():
    with pytest.raises((TypeError, AttributeError)):
        _name_tokens(None)


def test_tokens_none_raises():
    with pytest.raises((TypeError, AttributeError)):
        _tokens(None)
