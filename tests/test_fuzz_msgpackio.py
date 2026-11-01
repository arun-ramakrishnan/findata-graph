#!/usr/bin/env python3
"""Fuzz tests for helpers/core/msgpackio.py (test_coverage_gap_sweep S2).

msgpackio is the internal transport at the Rust embed-cache boundary,
so the properties are the transport contract: value roundtrip fidelity,
prefix truncation NEVER half-decodes, and arbitrary bytes decode to
either a value or a typed DecodeError — never anything else.
"""

from __future__ import annotations

import msgspec
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from helpers.core import msgpackio  # noqa: E402

_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# msgpack value domain: None/bool, int64..uint64, finite floats, text,
# binary, lists, str-keyed maps (encode requires str/int keys).
_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**63), max_value=2**64 - 1),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=64),
    st.binary(max_size=64),
)
_JSONISH = st.recursive(
    _SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=8),
        st.dictionaries(st.one_of(st.text(max_size=16), st.integers(0, 100)), children, max_size=8),
    ),
    max_leaves=25,
)


@_SETTINGS
@given(_JSONISH)
def test_roundtrip_preserves_any_value(value):
    """decode(encode(v)) == v for every representable value — the
    transport contract the Rust side's response-shape validator assumes."""
    assert msgpackio.decode(msgpackio.encode(value)) == value


@_SETTINGS
@given(_JSONISH, st.integers(min_value=0, max_value=4096))
def test_proper_prefix_never_decodes(value, cut):
    """Any proper prefix of a msgspec-encoded document raises DecodeError:
    msgpack frames are self-delimiting and msgspec decodes strictly, so a
    prefix is either incomplete or has trailing garbage. A decoder that
    silently accepts a prefix would let a corrupted cache row alias its
    neighbor."""
    payload = msgpackio.encode(value)
    cut = cut % max(len(payload), 1)  # keep examples in-range, incl. cut=0
    if cut == len(payload):
        return
    with pytest.raises(msgspec.DecodeError):
        msgpackio.decode(payload[:cut])


@_SETTINGS
@given(st.binary(max_size=256))
def test_arbitrary_bytes_decode_typed(raw):
    """Fuzzing the parser: arbitrary bytes yield a value or a DecodeError
    — nothing else escapes the seam.

    FIXED 2026-11-01 (was FINDING): a msgpack str chunk carrying invalid
    UTF-8 used to raise a bare UnicodeDecodeError (a ValueError sibling
    of DecodeError, not a subclass); the seam now unifies it to
    DecodeError — one typed error class for all transport corruption."""
    try:
        msgpackio.decode(raw)
    except msgspec.DecodeError:
        pass


def test_invalid_utf8_in_str_chunk_raises_decode_error():
    """The specific corrupt-cache-row shape: fixstr(3) with bytes that are
    not valid UTF-8. FIXED 2026-11-01: the seam wraps the bare
    UnicodeDecodeError into msgspec.DecodeError (chained via __cause__)."""
    raw = b"\xa3\xff\xfe\xfd"  # fixstr(3), invalid UTF-8 body
    with pytest.raises(msgspec.DecodeError) as excinfo:
        msgpackio.decode(raw)
    assert isinstance(excinfo.value.__cause__, UnicodeDecodeError)
