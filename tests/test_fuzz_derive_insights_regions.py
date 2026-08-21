#!/usr/bin/env python3
"""Fuzz tests for the derive_insights sentinel machinery
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §5 B1).

The 2026-08-19 incident (4 nested-sentinel deletion/misplacement bugs,
76-note repair) was exactly this code: _auto_region_spans,
_replace_or_insert_block/_kf, the nested-block rescue, and the OKF
sources splice. Deterministic tests pin the known shapes; these
properties pin the shapes nobody thought of — texts built from arbitrary
mixes of filler and EVERY auto-marker flavor the vault carries
(derive_insights chatter + key figures + foreign yfinance-style
markers), plus the incident's exact interleave (a KF block nested inside
a chatter region).
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core import edition_index as ei  # noqa: E402
from helpers.graph import derive_insights as di  # noqa: E402

# House printable-ish text strategy.
_TEXT = st.text(
    st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
    min_size=0, max_size=80,
)
# Every auto-marker flavor the renderers can meet in a note — plus
# near-miss marker-ish comments to stress _AUTO_MARKER_RE itself.
_MARKERISH = st.sampled_from([
    "<!-- BEGIN auto yfinance (enrich_from_yfinance.py) -->",
    "<!-- END auto profile -->",
    "<!-- BEGIN auto key figures (other tool) -->",
    "<!-- BEGIN auto -->",
    "<!--   END   auto something -->",
])
_ATOM = st.one_of(
    _TEXT,
    st.just(di._BEGIN), st.just(di._END),
    st.just(di._KF_BEGIN), st.just(di._KF_END),
    _MARKERISH,
)
_NOTES = st.lists(_ATOM, min_size=0, max_size=12).map(
    lambda parts: "\n".join(parts))
_EDITION = st.text(min_size=1, max_size=30)

_SETTINGS = settings(
    max_examples=50, deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _quote(text: str, paraphrase: str | None) -> di.Quote:
    return di.Quote(entity="Co", quote_text=text, paraphrase=paraphrase,
                    speaker_name="Anon Speaker", speaker_title="CEO",
                    as_of_edition="Ed")


_QUOTE_INPUT = st.tuples(
    st.text(min_size=1, max_size=400),
    st.one_of(st.none(), st.text(min_size=1, max_size=200)),
)


# --------------------------------------------------------------------------- #
# Marker structure                                                              #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(_NOTES)
def test_auto_region_spans_are_disjoint_sorted_and_well_formed(text):
    spans = di._auto_region_spans(text)
    assert spans == sorted(spans)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 <= s2                       # no overlap
    for s, e in spans:
        region = text[s:e]
        # A span opens with a BEGIN marker and closes with an END marker
        # (kinds from the same regex the stack walk uses), and is a
        # maximal BALANCED unit.
        kinds = [m.group(1) for m in di._AUTO_MARKER_RE.finditer(region)]
        assert kinds and kinds[0] == "BEGIN" and kinds[-1] == "END"
        assert kinds.count("BEGIN") == kinds.count("END")


@_SETTINGS
@given(_NOTES)
def test_outside_auto_regions_never_lands_inside_a_span(text):
    """A position inside a region moves to the region's START (inserting
    there does not split it) — never stays strictly inside."""
    spans = di._auto_region_spans(text)
    for pos in range(0, len(text) + 1, max(1, len(text) // 8 or 1)):
        moved = di._outside_auto_regions(text, pos)
        assert moved <= pos
        assert not any(s < moved < e for s, e in spans)   # strict interior


@_SETTINGS
@given(_NOTES)
def test_balanced_in_bounded_out(text):
    """If a note's markers balance, every renderer must keep them balanced
    (the write-gate invariant, held at the function level)."""
    if not di._markers_balanced(text):
        return  # only the balanced->balanced direction is guaranteed
    block = di.render_chatter_block("Ed", [_quote("q", None)])
    out, _ = di._replace_or_insert_block(text, "Ed", block)
    assert di._markers_balanced(out)
    kf = di.render_key_figures_block([di.Metric(entity="Co", value_raw="1")])
    out2, _ = di._replace_or_insert_kf(text, kf)
    assert di._markers_balanced(out2)


# --------------------------------------------------------------------------- #
# Renderers                                                                     #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(_EDITION, st.lists(_QUOTE_INPUT, min_size=0, max_size=5))
def test_render_chatter_block_stable_and_bounded(edition, quotes):
    qs = [_quote(t, p) for t, p in quotes]
    b1 = di.render_chatter_block(edition, qs)
    assert b1 == di.render_chatter_block(edition, qs)   # deterministic
    assert b1.count(di._BEGIN) == 1 and b1.count(di._END) == 1
    assert di._markers_balanced(b1)
    assert f"## The Chatter — {edition}" in b1


@_SETTINGS
@given(st.lists(st.tuples(_TEXT, _TEXT, _TEXT), min_size=0, max_size=6))
def test_render_key_figures_block_stable(metric_inputs):
    ms = [di.Metric(entity="Co", value_raw=vr, metric_label=lbl, period=per)
          for lbl, vr, per in metric_inputs]
    b1 = di.render_key_figures_block(ms)
    assert b1 == di.render_key_figures_block(ms)
    assert b1.count(di._KF_BEGIN) == 1 and b1.count(di._KF_END) == 1
    assert di._markers_balanced(b1)


# --------------------------------------------------------------------------- #
# Region refresh: idempotence + preservation                                    #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(_NOTES, _EDITION)
def test_replace_or_insert_block_reaches_fixed_point(text, edition):
    """Well-formed input: run 2 is a byte no-op (#139 byte-guard). Malformed
    input (crossed/stray markers): repeated application CONVERGES by run 3
    — healing a corrupted region may legitimately take one extra pass."""
    block = di.render_chatter_block(edition, [_quote("q", None)])
    out1, ch1 = di._replace_or_insert_block(text, edition, block)
    out2, ch2 = di._replace_or_insert_block(out1, edition, block)
    if "<!--" not in text:
        assert out1 == out2
        assert ch2 is False                 # clean insert -> strict no-op
    out3, ch3 = di._replace_or_insert_block(out2, edition, block)
    assert out3 == out2 and ch3 is False    # fixed point by run 3
    assert out1.count(di._BEGIN) >= 1


def edition_heading_count(text: str, edition: str) -> int:
    return text.count(f"## The Chatter — {edition}")


@_SETTINGS
@given(_TEXT, _TEXT, _EDITION)
def test_hand_text_outside_regions_preserved(pre, post, edition):
    """Curated prose before/after an auto region survives the refresh of a
    DIFFERENT edition's block byte-for-byte. The prose must be marker-free
    and heading-free: markers join the region math, and a `## The Chatter`
    heading in the prose is a legitimate insertion anchor that splits it
    (correct behavior, outside this property)."""
    from hypothesis import assume
    import re
    assume("<!--" not in pre and "<!--" not in post)
    assume(not re.search(r"^## ", pre + "\n" + post, re.MULTILINE))
    other = di.render_chatter_block("Other Ed", [_quote("q", None)])
    text = pre + "\n" + other + "\n" + post
    block = di.render_chatter_block(edition, [_quote("q2", None)])
    out, changed = di._replace_or_insert_block(text, edition, block)
    assert changed is True
    assert pre in out and post in out
    assert out.count(di._BEGIN) == 2 and out.count(di._END) == 2


@_SETTINGS
@given(_TEXT, _EDITION)
def test_kf_nested_in_chatter_is_rescued(filler, edition):
    """The 2026-08-19 incident shape: a key-figures block nested INSIDE the
    chatter sentinel region. Refreshing the chatter block must rescue the
    nested KF block (present exactly once afterwards), never delete it."""
    from hypothesis import assume
    assume("<!--" not in filler)
    chatter = di.render_chatter_block(edition, [_quote("q", None)])
    kf = di.render_key_figures_block(
        [di.Metric(entity="Co", value_raw="42%", metric_label="growth")])
    nested = chatter.replace(di._END, kf + di._END)
    text = filler + "\n" + nested
    block2 = di.render_chatter_block(edition, [_quote("q2", None)])
    out, changed = di._replace_or_insert_block(text, edition, block2)
    assert changed is True
    assert out.count(di._KF_BEGIN) == 1     # rescued, not deleted
    assert out.count(di._KF_END) == 1
    assert di._markers_balanced(out)


@_SETTINGS
@given(_NOTES)
def test_replace_or_insert_kf_reaches_fixed_point(text):
    """Same contract as the chatter block: strict no-op on run 2 for clean
    input, fixed point by run 3 for malformed (crossed-marker) input."""
    kf = di.render_key_figures_block(
        [di.Metric(entity="Co", value_raw="1", metric_label="revenue")])
    out1, ch1 = di._replace_or_insert_kf(text, kf)
    out2, ch2 = di._replace_or_insert_kf(out1, kf)
    if "<!--" not in text:
        assert out1 == out2
        assert ch2 is False
        assert out1.count(di._KF_BEGIN) == 1
    out3, ch3 = di._replace_or_insert_kf(out2, kf)
    assert out3 == out2 and ch3 is False


# --------------------------------------------------------------------------- #
# OKF sources splice                                                            #
# --------------------------------------------------------------------------- #
_SPLICE_STATE: tuple[dict, Path] | None = None


def _splice_state() -> tuple[dict, Path]:
    """Module-level (not a fixture): @given tests can't take pytest
    fixtures. One tiny source vault + its edition index, built lazily."""
    global _SPLICE_STATE
    if _SPLICE_STATE is None:
        import tempfile
        vault = Path(tempfile.mkdtemp()) / "findata"
        (vault / "The_Chatter").mkdir(parents=True)
        (vault / "The_Chatter" / "TC_Alpha.md").write_text(
            "# The Chatter: Alpha Edition\n\nbody\n", encoding="utf-8")
        _SPLICE_STATE = (ei.source_note_index(vault), vault)
    return _SPLICE_STATE


def _with_fm(body: str, sources: list | None) -> str:
    lines = ["---", "title: T", "type: company"]
    if sources is not None:
        lines.append("sources:")
        lines += [f"- id: {sid}" for sid in sources]
    lines.append("---")
    return "\n".join(lines) + "\n" + body


_BODIES = st.one_of(
    _TEXT,
    st.builds(lambda t: f"## The Chatter — {t}\n", _TEXT),
    st.builds(lambda t: f"*Source: The Chatter — {t}*\n", _TEXT),
)
# IDs that render as valid YAML scalars and don't round-trip as ints.
_IDS = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,9}", fullmatch=True)


@_SETTINGS
@given(_BODIES, st.one_of(st.none(), st.lists(_IDS, min_size=0, max_size=3)))
def test_splice_sources_idempotent_and_preserving(body, existing_ids):
    """_splice_sources never invents frontmatter, is idempotent, keeps
    foreign keys and existing entries verbatim, and only ever ADDs to
    sources[]."""
    index, vault = _splice_state()
    text = _with_fm(body, existing_ids)

    out1, ch1 = di._splice_sources(text, index, vault)
    out2, ch2 = di._splice_sources(out1, index, vault)
    assert out1 == out2 and ch2 is False

    _, fm_text, _ = di.split_frontmatter(out1)
    fm = yaml.safe_load(fm_text)
    assert fm["title"] == "T" and fm["type"] == "company"
    ids_after = {str(s.get("id")) for s in (fm.get("sources") or [])
                 if isinstance(s, dict)}
    if existing_ids:
        assert set(existing_ids) <= ids_after         # nothing dropped

    out_nofm, ch_nofm = di._splice_sources(body, index, vault)
    assert out_nofm == body and ch_nofm is False     # never invents FM
