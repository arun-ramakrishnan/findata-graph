"""Fuzz tests — hybrid fuzzy company-name matcher invariants.

Property-based tests (via Hypothesis) for `helpers/core/fuzzy_match.py`.

Invariants pinned:
  1. fuzzy_match() never raises and always returns a (name, method, score)
     tuple for ANY query string, with method in the known set and score in
     [0, 1].
  2. Empty / whitespace-only queries must NOT match anything — a regression
     for the 2026-08-09 bug where fuzzy_match("") false-positived onto the
     first entity via the `'' in entity_lower` substring check.
  3. word_overlap_match() returns (name, score) or (None, 0.0) for any query.
  4. _tokenize() never raises and returns only non-empty lowercase tokens.
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from helpers.core.fuzzy_match import (
    ABBREVIATIONS,
    _tokenize,
    fuzzy_match,
    word_overlap_match,
)

KNOWN_METHODS = {None, "exact", "abbreviation", "word_overlap", "spellfix1"}
ABBREVIATION_TARGETS = set(ABBREVIATIONS.values())

ENTITIES = [
    "Tata Consultancy Services",
    "Tata Motors Passenger Vehicles",
    "HDFC Bank",
    "Infosys",
    "Mahindra & Mahindra",
    "One 97 Communications PayTM",
    "Sun Pharmaceutical Industries",
    "Larsen and Toubro",
]


@settings(max_examples=200, deadline=None)
@given(st.text(min_size=0, max_size=200))
def test_fuzz_fuzzy_match_never_raises_and_is_typed(query: str):
    """Invariant 1: any query → a well-typed 3-tuple, no crash."""
    result = fuzzy_match(query, ENTITIES)
    assert isinstance(result, tuple)
    assert len(result) == 3
    name, method, score = result
    assert method in KNOWN_METHODS
    if method is None:
        assert name is None
        assert score == 0.0
    elif method == "abbreviation":
        # Abbreviations resolve via the global table, which may contain
        # entities not in the caller's list (e.g. GICRE → General
        # Insurance Corporation).
        assert name in ABBREVIATION_TARGETS
        assert score == 1.0
    else:
        # exact / word_overlap / spellfix1 can only return list members.
        assert name in ENTITIES
        assert 0.0 <= score <= 1.0


@settings(max_examples=50, deadline=None)
@given(st.text(min_size=0, max_size=50))
def test_fuzz_fuzzy_match_blank_query_never_matches(query: str):
    """Invariant 2: empty or whitespace-only queries match nothing."""
    if query.strip() != "":
        return  # only blank queries exercise the regression path
    name, method, score = fuzzy_match(query, ENTITIES)
    assert name is None
    assert method is None
    assert score == 0.0


@settings(max_examples=200, deadline=None)
@given(st.text(min_size=0, max_size=200))
def test_fuzz_word_overlap_never_raises(query: str):
    """Invariant 3: word_overlap_match returns (name, score) or (None, 0.0)."""
    name, score = word_overlap_match(query, ENTITIES)
    assert isinstance(name, (str, type(None)))
    assert 0.0 <= score <= 1.0
    if name is None:
        assert score == 0.0
    else:
        assert name in ENTITIES


@settings(max_examples=200, deadline=None)
@given(st.text(min_size=0, max_size=200))
def test_fuzz_tokenize_never_raises(text: str):
    """Invariant 4: _tokenize returns a set of non-empty lowercase tokens."""
    tokens = _tokenize(text)
    assert isinstance(tokens, set)
    for t in tokens:
        assert isinstance(t, str)
        assert t == t.lower()
        assert len(t) >= 1


@settings(max_examples=100, deadline=None)
@given(st.text(min_size=0, max_size=100))
def test_fuzz_fuzzy_match_no_entity_mutation(query: str):
    """Invariant 5: matching must not mutate the entities list."""
    snapshot = list(ENTITIES)
    fuzzy_match(query, ENTITIES)
    word_overlap_match(query, ENTITIES)
    assert ENTITIES == snapshot


# ---------------------------------------------------------------------------

# Tokenization / case invariants (added 2026-08-12)
# ---------------------------------------------------------------------------
from helpers.core.fuzzy_match import _STOPWORDS

_TOKEN_TEXT = st.text(min_size=0, max_size=80)

@settings(max_examples=300, deadline=None)
@given(_TOKEN_TEXT)
def test_fuzz_tokenize_collapse_invariance(s):
    """`&`, `-`, `.` are treated as whitespace separators by _tokenize, so
    substituting them for spaces never changes the token set."""
    collapsed = s.replace("&", " ").replace("-", " ").replace(".", " ")
    assert _tokenize(s) == _tokenize(collapsed)

@settings(max_examples=300, deadline=None)
@given(_TOKEN_TEXT)
def test_fuzz_tokenize_lowercasing_idempotent(s):
    """_tokenize lowercases, so re-lowercasing the input is a no-op on tokens.
    (Full case-*folding* is intentionally NOT asserted: combining marks like
    U+0345 behave differently under .lower()/.upper(), and _tokenize only
    lowercases — that is the contract.)"""
    assert _tokenize(s) == _tokenize(s.lower())

@settings(max_examples=300, deadline=None)
@given(_TOKEN_TEXT)
def test_fuzz_tokenize_no_collapsed_punct_and_nonempty(s):
    toks = _tokenize(s)
    assert all(tok for tok in toks)                       # non-empty tokens
    assert not any(ch in "&-." for tok in toks for ch in tok)   # collapsed punct never survives
    assert all(tok not in _STOPWORDS for tok in toks)     # stopwords removed

@settings(max_examples=300, deadline=None)
@given(_TOKEN_TEXT)
def test_fuzz_tokenize_tokens_drawn_from_cleaned_words(s):
    """Tokens are exactly the whitespace-split lowercased words (after &-. become
    spaces), minus STOPWORDS — no unicode-normalization or other magic."""
    cleaned = s.lower().replace("&", " ").replace("-", " ").replace(".", " ")
    words = set(cleaned.split())
    assert _tokenize(s) == (words - _STOPWORDS)

@settings(max_examples=60, deadline=None)
@given(st.sampled_from(sorted(ABBREVIATIONS)))
def test_fuzz_abbreviation_case_insensitive_and_target(key):
    """The abbreviation branch does `query.upper()` first, so resolution is
    case-insensitive and returns the table target regardless of the caller's
    entity list (here: empty)."""
    target = ABBREVIATIONS[key]
    for q in (key, key.lower(), key.upper(), key.title()):
        name, method, score = fuzzy_match(q, [])
        assert method == "abbreviation"
        assert name == target
        assert score == 1.0
