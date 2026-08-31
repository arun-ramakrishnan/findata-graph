#!/usr/bin/env python3
"""Fuzz tests for the derive_events prose extractors
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §5 B4).

The existing fuzz coverage for this module is the two date helpers only;
_iter_bullets / _extract_guidance / _extract_management / _dedup see the
whole vault's note prose — the properties pin their no-crash, typed,
deterministic, dedup-stable behaviour over arbitrary bodies.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.graph import derive_events as de  # noqa: E402

_SETTINGS = settings(max_examples=75, deadline=None)

_TEXT = st.text(
    st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
    min_size=0,
    max_size=300,
)
_LINES = st.lists(_TEXT, min_size=0, max_size=12).map(lambda parts: "\n".join(parts))


@_SETTINGS
@given(_LINES)
def test_iter_bullets_lines_first_then_novel_sentences(body):
    out = list(de._iter_bullets(body))
    for w in out:
        assert isinstance(w, str) and w == w.strip() and w
    # Lines come first, in order, verbatim (duplicate lines legitimately
    # repeat); the sentence phase only ADDS strings no line already had.
    nonblank = [ln.strip() for ln in body.splitlines() if ln.strip()]
    assert out[: len(nonblank)] == nonblank
    extras = out[len(nonblank) :]
    assert not (set(extras) & set(nonblank))
    assert len(extras) == len(set(extras))


@_SETTINGS
@given(_LINES)
def test_extract_guidance_typed_and_deterministic(body):
    evs1 = de._extract_guidance("Co", body, "findata/x.md")
    evs2 = de._extract_guidance("Co", body, "findata/x.md")
    assert evs1 == evs2
    for ev in evs1:
        assert ev.event_type == "guidance"
        assert ev.entity == "Co"
        assert ev.source_quote  # every guidance event carries its window


@_SETTINGS
@given(_LINES)
def test_extract_management_typed_and_deterministic(body):
    evs1 = de._extract_management("Co", body, "findata/x.md")
    evs2 = de._extract_management("Co", body, "findata/x.md")
    assert evs1 == evs2
    for ev in evs1:
        assert ev.event_type == "management_change"
        assert ev.entity == "Co"
        assert ev.magnitude  # the matched executive title


@_SETTINGS
@given(
    st.lists(
        st.builds(
            de.Event,
            entity=st.just("Co"),
            event_type=st.sampled_from(["guidance", "management_change"]),
            period=st.one_of(st.none(), _TEXT.map(lambda s: s[:20])),
            magnitude=st.one_of(st.none(), _TEXT.map(lambda s: s[:20])),
            source_quote=st.one_of(st.none(), _TEXT),
        ),
        min_size=0,
        max_size=8,
    )
)
def test_dedup_stable_and_keyed(events):
    out = de._dedup(events)
    keys = [(e.event_type, e.period, e.magnitude) for e in out]
    assert len(keys) == len(set(keys))  # factual identity deduped
    # Dedup never invents: output ⊆ input (same objects).
    assert all(e in events for e in out)


@_SETTINGS
@given(_LINES)
def test_extract_guidance_never_loses_dedup(body):
    """The extractor's own output is always already deduped (guidance
    path applies _dedup internally)."""
    evs = de._extract_guidance("Co", body, "findata/x.md")
    assert de._dedup(evs) == evs
