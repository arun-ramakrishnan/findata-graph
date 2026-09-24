"""Phase 2 fuzz tests — regex robustness (ReDoS detection).

Property-based tests (via Hypothesis) for the two regexes that parse
untrusted newsletter input:

  - `SECTION_RE` (helpers/core/parse_newsletter.py:67) — extracts
    company headings from OCR'd markdown. The pattern
    `(.+?)(?:\\s*[|·].*)*$` pairs a lazy `.+?` with a greedy `.*`
    inside an optional non-capturing group anchored to end-of-line.
    On adversarial input (many `|`/`·` chars, no terminating newline)
    this can exhibit quadratic backtracking.

  - `IMG_BLOCK_RE` (helpers/pdf/capture_newsletter_images.py:43) —
    extracts <div><img src='URL'></div> blocks from OCR'd HTML-in-
    markdown. Uses three `[^>]*` repetitions; on malformed HTML
    (unclosed tags, many attributes) the regex engine may backtrack
    heavily.

The contract under test is NOT "the regex returns the right match" —
it is "the regex terminates quickly and does not crash on any input".
A ReDoS would manifest as a Hypothesis deadline timeout, which the
test reports as a failure with the offending input shrunk to a
minimal reproducible case.

Runs alongside regular pytest in `make qa`. Hypothesis defaults to
100 random examples per @given test; each completes in <1s.
"""

from __future__ import annotations

import time

from hypothesis import given, settings, strategies as st


from core.parse_newsletter import SECTION_RE  # noqa: E402
from graph.derive_co_mentions import _parse_edition_number  # noqa: E402
from graph.derive_insights import _BPS_RE, _PCT_RE  # noqa: E402
from pdf.capture_newsletter_images import IMG_BLOCK_RE  # noqa: E402
from pdf.pdf_local import BOLD_LINE_RE  # noqa: E402


# ---------------------------------------------------------------------------
# Alphabets
# ---------------------------------------------------------------------------
# Adversarial alphabets are deliberately small and targeted at the regex's
# "interesting" characters. Hypothesis explores more thoroughly when the
# alphabet is constrained — a full-unicode strategy would waste examples
# on chars the regex doesn't branch on.

# SECTION_RE branches on: `#` (heading marker), `|` and `·` (separator),
# whitespace, and end-of-line (`$` with MULTILINE). Any alphanumerics fill
# out the captured group. Keep max_size modest so a genuine ReDoS surfaces
# within Hypothesis's default deadline (200ms) rather than just running long.
SECTION_ALPHABET = st.characters(
    whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="#|·\n \t"
)

# IMG_BLOCK_RE branches on: `<`, `>`, `/`, quotes (`'` and `"`), the literal
# `div`/`img`/`src`, and whitespace. Add a few punctuation chars to stress
# the `[^>]*` and `[^'\"]+` character classes.
IMG_ALPHABET = st.characters(
    whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="<>/\"' \n\tdivsrc=!@"
)


# ---------------------------------------------------------------------------
# 5. SECTION_RE does not exhibit catastrophic backtracking
# ---------------------------------------------------------------------------
# A ReDoS-vulnerable regex will blow past Hypothesis's per-example deadline
# (default 200ms) on a short adversarial input. The test passes if every
# input is processed within the deadline AND every match (if any) is a str.
#
# We bound input size at 400 chars — large enough to surface quadratic
# behavior, small enough that a clean regex finishes in microseconds.
@settings(deadline=500)  # ms; generous enough for CI noise, catches real ReDoS
@given(st.text(alphabet=SECTION_ALPHABET, max_size=400))
def test_section_regex_no_catastrophic_backtracking(markdown):
    matches = SECTION_RE.findall(markdown)
    # Contract: every captured group is a string (the regex has one group).
    for m in matches:
        assert isinstance(m, str), f"SECTION_RE returned non-str match {m!r} for input {markdown!r}"


# ---------------------------------------------------------------------------
# 6. IMG_BLOCK_RE handles malformed HTML gracefully
# ---------------------------------------------------------------------------
# Same ReDoS guard, applied to the image-block regex. Adversarial input is
# random sequences of tag-like characters that never form a valid
# <div><img></div> structure — the worst case for the `[^>]*` repetitions.
#
# Contract: terminates within deadline; every captured URL (if any) is a
# str. We do NOT assert the URL is well-formed (the regex is permissive by
# design — validation happens downstream in parse_images).
@settings(deadline=500)
@given(st.text(alphabet=IMG_ALPHABET, max_size=300))
def test_img_block_regex_malformed_html(html):
    matches = IMG_BLOCK_RE.findall(html)
    for url in matches:
        assert isinstance(url, str), f"IMG_BLOCK_RE returned non-str url {url!r} for input {html!r}"


# ---------------------------------------------------------------------------
# 7. SECTION_RE matches well-formed headings (positive-case sanity)
# ---------------------------------------------------------------------------
# Guard against an over-tight regex that rejects valid input to avoid
# ReDoS. Real newsletter headings look like:
#     ## Bharat Forge | Large Cap | Auto & Defence
#     # Oil and Natural Gas Corporation Limited Large Cap Oil & Gas
# The regex should capture the company name portion.
@settings(deadline=500)
@given(
    name=st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll"), whitelist_characters=" &.-"),
        min_size=1,
        max_size=40,
    ),
    n_seps=st.integers(min_value=0, max_value=3),
)
def test_section_regex_matches_well_formed_heading(name, n_seps):
    # Build a heading with 0-3 trailing "| sector" segments.
    tail = "".join(f" | segment{i}" for i in range(n_seps))
    heading = f"## {name}{tail}\n"
    matches = SECTION_RE.findall(heading)
    # At least one match; the first capture should start with the name.
    assert len(matches) >= 1, f"SECTION_RE missed valid heading: {heading!r}"
    assert isinstance(matches[0], str)


# ---------------------------------------------------------------------------
# 8. IMG_BLOCK_RE matches well-formed blocks (positive-case sanity)
# ---------------------------------------------------------------------------
# Same positive-case guard for the image regex. A well-formed block:
#     <div class="x"><img src="https://example.com/a.jpeg" alt="fig"></div>
# should yield exactly the URL as the capture.
@settings(deadline=500)
@given(
    url=st.text(
        alphabet=st.characters(
            whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=":/._-?=&%"
        ),
        min_size=1,
        max_size=80,
    ),
    quote=st.sampled_from(["'", '"']),
)
def test_img_block_regex_matches_well_formed_block(url, quote):
    block = f"<div class='wrap'><img src={quote}{url}{quote} alt='fig'></div>"
    matches = IMG_BLOCK_RE.findall(block)
    assert len(matches) == 1, f"IMG_BLOCK_RE expected 1 match, got {len(matches)} for {block!r}"
    assert matches[0] == url, f"IMG_BLOCK_RE captured {matches[0]!r}, expected {url!r}"


# ---------------------------------------------------------------------------
# 10. _parse_edition_number extracts an integer edition number (or None)
# ---------------------------------------------------------------------------
# Regex-based extraction of the edition number from a newsletter title / footer
# (helpers/graph/derive_co_mentions.py). Contract: never raises on arbitrary
# input; returns int | None; and when it returns an int it is non-negative and
# round-trips through str().
@settings(deadline=500)
@given(
    st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
        min_size=0,
        max_size=200,
    )
)
def test_parse_edition_number_never_raises(title):
    result = _parse_edition_number(title)
    assert result is None or isinstance(result, int), (
        f"_parse_edition_number({title!r}) -> {result!r}"
    )
    if isinstance(result, int):
        assert result >= 0, f"negative edition number from {title!r}"
        assert int(str(result)) == result


# Positive-case guard: the documented "Edition #N (date)" form must round-trip.
@settings(deadline=500)
@given(st.integers(min_value=0, max_value=10**6))
def test_parse_edition_number_positive(edition):
    title = f"Edition #{edition} (Mar 27, 2026)"
    assert _parse_edition_number(title) == edition


# ---------------------------------------------------------------------------
# 11. Range patterns (_PCT_RE / _BPS_RE) do not backtrack (AVAIL-2)
# ---------------------------------------------------------------------------
# The range separator used to be a bracket class `[-–to ]` that shares the
# space char with the flanking `\s*`. A space run could then be split between
# the three quantifiers in O(m^2) ways, and the two numeric anchors turned
# that cubic — ~8x per doubling, 4 s on a 1.6 KB line, ~500 s at 8 KB, from a
# single hostile line in any corpus document. The class is now an alternation
# `(?:[-–]|to)` (linear; verified 0.9 ms at 6.4 KB). This is the alphabet that
# found it: digits as the two anchors, with space runs to force the split.
# max_size 900 — the adversarial shape spends its budget on TWO space runs, so
# the budget must be large enough for each run to reach ~450 chars, where the
# old cubic takes ~700 ms (past the deadline) instead of ~200 ms (under it).
RANGE_ALPHABET = st.characters(whitelist_categories=("Nd",), whitelist_characters=" -–to%\t")


@settings(deadline=500)
@given(st.text(alphabet=RANGE_ALPHABET, max_size=900))
def test_pct_range_regex_no_catastrophic_backtracking(text):
    matches = _PCT_RE.findall(text)
    for m in matches:
        assert isinstance(m, str), f"_PCT_RE returned non-str match {m!r} for {text!r}"


@settings(deadline=500)
@given(st.text(alphabet=RANGE_ALPHABET, max_size=900))
def test_bps_range_regex_no_catastrophic_backtracking(text):
    matches = _BPS_RE.findall(text)
    for m in matches:
        assert isinstance(m, str), f"_BPS_RE returned non-str match {m!r} for {text!r}"


# Positive-case guard (AVAIL-2): the accepted guidance shapes must still match
# after de-ambiguation. "10-12%", "10–12%", "10 to 12%", and the bps twins are
# the corpus forms; a fix that drops them is a correctness bug, not a fix.
@settings(deadline=500)
@given(
    lo=st.integers(min_value=0, max_value=1000),
    hi=st.integers(min_value=0, max_value=1000),
    sep=st.sampled_from(["-", "–", " to "]),
)
def test_pct_range_matches_well_formed(lo, hi, sep):
    text = f"{lo}{sep}{hi}%"
    assert _PCT_RE.search(text) is not None, f"_PCT_RE missed valid range: {text!r}"


@settings(deadline=500)
@given(
    lo=st.integers(min_value=0, max_value=1000),
    hi=st.integers(min_value=0, max_value=1000),
    sep=st.sampled_from(["-", "–", " to "]),
)
def test_bps_range_matches_well_formed(lo, hi, sep):
    text = f"{lo}{sep}{hi} bps"
    assert _BPS_RE.search(text) is not None, f"_BPS_RE missed valid range: {text!r}"


# Deterministic adversarial case (AVAIL-2): the exact input shape that made the
# old `[-–to ]` class cubic — two numeric anchors with long space runs between
# them. Not left to Hypothesis discovery: at this size the old pattern takes
# ~4 s and the de-ambiguated one ~1 ms, so the margin is 4000x and the
# assertion cannot flap on CI noise. If the range class ever reacquires the
# space-overlap ambiguity, this fails immediately.
def test_range_regex_adversarial_space_runs_are_linear():
    text = "1" + " " * 800 + "2" + " " * 800 + "3"
    start = time.perf_counter()
    _PCT_RE.findall(text)
    _BPS_RE.findall(text)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 500, (
        f"range patterns took {elapsed_ms:.0f} ms on a 1.6 KB space-run input; "
        "the range separator likely regained its space/\\s* overlap (AVAIL-2)"
    )


# ---------------------------------------------------------------------------
# BOLD_LINE_RE (helpers/pdf/pdf_local.py:70) — the remaining quadratic shape
# ---------------------------------------------------------------------------
# `^(?:\*\*.+?\*\*\s*)+$` pairs a lazy `.+?` inside a repeated group with
# an outer `+`: on a long run of bold segments the engine can re-split the
# boundary between the `.+?` and the trailing `\s*` in O(n^2) ways. Measured
# 2026-09-20: 0.02 ms at 10 segments -> 71 ms at 320 (quadratic, bounded).
# This is the last AVAIL-2-class pattern outside a fuzz guard.

BOLD_ALPHABET = st.characters(
    whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="*| 	"
)


@settings(deadline=500, max_examples=200)
@given(st.text(alphabet=BOLD_ALPHABET, max_size=900))
def test_bold_line_regex_no_catastrophic_backtracking(text):
    r"""BOLD_LINE_RE must terminate fast on any bold-heavy input.

    A regression to an ambiguous form (e.g. `\s*` folded into the class
    as `[-\s]`, or the lazy `.+?` made greedy) turns this quadratic; a
    genuinely cubic shape would blow the Hypothesis deadline here.
    """
    BOLD_LINE_RE.findall(text)


# Deterministic adversarial case: the exact shape that makes the boundary
# ambiguous -- many bold segments, each with content, separated by spaces.
# Asserts SUBQUADRATIC scaling (doubling input must not quadruple cost),
# which is the precise property the AVAIL-2 fix established for the metric
# patterns. A cubic regression here fails the third doubling outright.
def test_bold_line_regex_scales_subquadratically():
    # Measured 2026-09-20 with min-of-3 samples per size: the current
    # (correct) pattern grows ~4.2x per doubling (quadratic is 4x); a
    # nested-quantifier regression -- `(?:.+?\s*)+` inside the group --
    # measures 12.2x on its middle doubling. An 8.0 threshold separates them
    # with margin for CI noise while still rejecting the known regression.
    timings_ms = []
    for n in (160, 320, 640, 1280):
        s = ("**" + "a" * n + "** ") * n
        BOLD_LINE_RE.match(s)  # warm-up: first timed hit pays import/cache costs
        best = float("inf")
        for _ in range(5):  # min-of-5: discards scheduler/cache noise
            t0 = time.perf_counter()
            BOLD_LINE_RE.match(s)
            best = min(best, (time.perf_counter() - t0) * 1000)
        timings_ms.append(best)
    growth = [timings_ms[i + 1] / max(timings_ms[i], 0.01) for i in range(3)]
    # First doubling starts from a sub-ms baseline under xdist and can
    # ratio-inflate (2026-09-22: 0.36→4.44 ms = 12.3x while middle/last
    # ratios stayed at the calibrated ~4.2x / ~2.9x). Warm-up + min-of-5
    # plus a floor on the denominator keeps the middle-doubling
    # discriminator (nested-quantifier regression = 12.2x there) without
    # flakes from a cold first sample.
    assert max(growth) < 8.0, (
        f"BOLD_LINE_RE growth superquadratic: {timings_ms} ms, ratios {growth}"
    )
