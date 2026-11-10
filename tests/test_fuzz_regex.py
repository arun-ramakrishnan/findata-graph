"""Phase 2 fuzz tests — regex robustness (ReDoS detection).

Property-based tests (via Hypothesis) for the regexes that parse
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

  - `BOLD_LINE_RE` (helpers/pdf/pdf_local.py:76) — detects whole-line
    bold headings. Its pre-2026-11-10 form paired a lazy `.+?` with an
    outer `+` and went EXPONENTIAL on the failing path (145 B -> 3 s).
    It has its own two guards at the bottom of this file; see the
    module-level comment there.

The contract under test is NOT "the regex returns the right match" —
it is "the regex terminates quickly and does not crash on any input".

Note that Hypothesis' `deadline` does NOT deliver that contract on its
own: it fires between examples, so a catastrophically backtracking
regex hangs the test instead of failing it (measured, 2026-11-10). The
guards that defend against a ReDoS therefore assert with an explicit
SIGALRM cap where a hang is the failure mode, and with measured
timing ratios where a slow-down is.

Runs alongside regular pytest in `make qa`; Hypothesis defaults to
100 random examples per `@given` test.
"""

from __future__ import annotations

import re
import signal
import statistics
import time

import pytest

from hypothesis import given, settings, strategies as st


from helpers.core.parse_newsletter import SECTION_RE  # noqa: E402
from helpers.graph.derive_co_mentions import _parse_edition_number  # noqa: E402
from helpers.graph.derive_insights import _BPS_RE, _PCT_RE  # noqa: E402
from helpers.pdf.capture_newsletter_images import IMG_BLOCK_RE  # noqa: E402
from helpers.pdf.pdf_local import BOLD_LINE_RE  # noqa: E402


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
# BOLD_LINE_RE (helpers/pdf/pdf_local.py:76) - two guards, two paths
# ---------------------------------------------------------------------------
# The pre-2026-11-10 form `^(?:\*\*.+?\*\*\s*)+$` paired a lazy `.+?` inside a
# repeated group with an outer `+`.
#
# On the SUCCESS path that is only mildly superlinear, and both the old and
# the shipped form scale linearly there - which is why the success-path ladder
# alone provably cannot see the bug (see its comment below).
#
# The blowup lives on the FAILURE path: a long run of well-formed segments
# followed by a terminator that never closes the match, so the engine re-splits
# every segment boundary and the cost DOUBLES per added segment. Measured
# 2026-11-10: 145 bytes -> 3038 ms at 2.00x/segment - exponential in length,
# not quadratic.
#
# The shipped disjoint form
#     `^(?:\*\*[^*\n]*(?:\*(?!\*)[^*\n]*)*\*\*\s*)+$`
# is 33 ns/byte flat across six doublings (3 KB -> 96 KB).
#
# BOTH pre-existing guards were blind to the old form: monkeypatching it back
# in still gave `2 passed, 11 deselected`. The success-path ladder cannot fail
# because its shape quadruples in LENGTH per rung, and the old random strategy
# never assembled the ~25 complete segments a blowup needs. The failing-path
# ladder and the structured strategy below are the two parts that actually
# discriminate. Rationale and full measurements:
# doc/improvements/archive/testing/bold_line_guard_failing_path.md

_BOLD_GUARD_CAP_S = 1.0  # a healthy call is sub-millisecond at every size here


class _BoldGuardTimeout(Exception):
    """A BOLD_LINE_RE call overran its cap.

    Hypothesis' own ``deadline`` cannot help here - it fires between examples,
    so a catastrophically backtracking regex simply hangs the test instead of
    failing it (observed 2026-11-10: the old pattern ran >20 s at 30 segments
    with no deadline violation). An explicit SIGALRM cap turns a would-be hang
    into an immediate, attributable failure.
    """


def _call_with_cap(fn, text: str, cap_s: float = _BOLD_GUARD_CAP_S):
    """Run ``fn(text)`` under a SIGALRM cap; raise _BoldGuardTimeout on overrun."""

    def _alarm(signum, frame):
        raise _BoldGuardTimeout

    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, cap_s)
    try:
        return fn(text)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


# Structured, not random. `st.text` over a bold-ish alphabet was the original
# strategy and it never found the blowup: it essentially always produces text
# that either matches immediately or fails at the first character. The blowup
# needs ~25 COMPLETE `**seg** ` units before a terminator that cannot close the
# match, so the strategy assembles that shape directly and varies the segment
# count instead of hoping to stumble on it.
_BOLD_HYP_SEGMENT = "**bold segment** "


@settings(deadline=None, max_examples=200)
@given(
    segments=st.integers(min_value=1, max_value=40),
    terminator=st.sampled_from(["|", "!", "(", "a", ""]),
)
def test_bold_line_regex_no_catastrophic_backtracking(segments, terminator):
    r"""BOLD_LINE_RE must terminate fast on adversarial bold-heavy input.

    Exercises both paths at every segment count up to 40: the bare empty
    terminator closes the match (success path), the others leave it unclosed
    (failure path, where the pre-2026-11-10 lazy `.+?` form went exponential).
    The per-call SIGALRM cap is the assertion - a reintroduced ambiguity raises
    here within a second rather than hanging the gate.
    """
    text = _BOLD_HYP_SEGMENT * segments + terminator
    try:
        _call_with_cap(BOLD_LINE_RE.findall, text)
        _call_with_cap(BOLD_LINE_RE.match, text)
    except _BoldGuardTimeout:
        raise AssertionError(
            f"BOLD_LINE_RE exceeded {_BOLD_GUARD_CAP_S:.0f}s on "
            f"{segments} segments + {terminator!r} (len {len(text)}) - the "
            f"failure path has regressed to catastrophic backtracking"
        ) from None


# Positive-path ladder, kept only as a guard against a CUBIC regression. The
# shape is quadratic in LENGTH by construction - `n` bold runs of `n`
# characters - so every rung quadruples the input and ~4x time IS linear. This
# ladder therefore CANNOT see the exponential failure-path blowup that the
# pre-2026-11-10 form had; `test_bold_line_regex_failing_path_stays_linear`
# below is the guard for that. Keeping this one costs ~30 ms and still catches
# a genuinely cubic shape (a mutation measured 12.2x on its middle doubling).
def _measure_bold_line_ladder() -> list[float]:
    """Time BOLD_LINE_RE at the calibrated sizes; min-of-5 per size."""
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
    return timings_ms


def test_bold_line_regex_scales_subquadratically():
    # Score only ratios whose base is >= 1 ms (at n=160 the match is sub-ms, so
    # one preempted sample inflates that first ratio past any threshold:
    # 2026-09-22 0.36->4.44 ms = 12.3x; gate 1137 2026-09-30 first ratio 12.39x
    # off a 0.36 ms base). Under xdist gate load a >= 1 ms base can STILL get
    # one preempted min-of-5 sample (advisory gate 3407 2026-11-04: middle ratio
    # 10.9x because n=320 stayed at its serial 1.45 ms while the loaded n=640
    # sample ran 15.8 ms) - so a would-fail ladder is re-measured, up to 3
    # attempts. A scheduler spike is transient and passes on a retry; a real
    # superquadratic regression is deterministic and fails EVERY attempt.
    worst: tuple[list[float], list[float], list[float]] | None = None
    for _attempt in range(3):
        timings_ms = _measure_bold_line_ladder()
        growth = [timings_ms[i + 1] / max(timings_ms[i], 0.01) for i in range(3)]
        scored = [g for g, base in zip(growth, timings_ms) if base >= 1.0]
        if scored and max(scored) < 8.0:
            return
        worst = (timings_ms, growth, scored)
    timings_ms, growth, scored = worst  # type: ignore[assignment]
    assert scored, (
        f"BOLD_LINE_RE unmeasurable at the calibrated sizes (all bases "
        f"< 1 ms): {timings_ms} - recalibrate the size ladder"
    )
    assert max(scored) < 8.0, (
        f"BOLD_LINE_RE growth superquadratic: {timings_ms} ms, ratios "
        f"{growth} (scored on >=1 ms bases: {scored})"
    )


# ---------------------------------------------------------------------------
# Failing-path ladder - the guard the success-path ladder cannot be
# ---------------------------------------------------------------------------
# `("**" + "a"*1000 + "** ") * segments + "|"` is the shape the old lazy `.+?`
# form was exponential on: the long pad keeps each segment cheap for a linear
# engine, while the non-closing `|` forces the overall match to FAIL, which is
# what makes the engine re-split every segment boundary. Rungs at 8 and 32
# segments are 8,041 and 32,161 bytes - a 4x length step, so a linear engine
# must score ~4.0.
#
# Measured 2026-11-10 (median of 5 rounds; 10 trials min 3.82 / max 4.15):
#
#     shipped pattern   0.362 ms -> 1.439 ms   ratio 4.11
#     old lazy form     3.318 ms -> TIMEOUT     cannot score at all
_BOLD_FAIL_PAD = 1000
_BOLD_FAIL_RUNGS = (8, 32)  # 8,041 -> 32,161 bytes: a 4x length step
_BOLD_FAIL_CAP_S = 2.0  # per rung; the old form needs >2 s on a 32 KB rung
_BOLD_FAIL_ROUNDS = 5  # median round, per the test_performance.py idiom
_BOLD_FAIL_MAX_RATIO = 8.0  # linear is ~4.0; 2x headroom, cannot flap


def _bold_failing_input(segments: int) -> str:
    """Adversarial line that FAILS to match: N complete segments then ``|``."""
    return ("**" + "a" * _BOLD_FAIL_PAD + "** ") * segments + "|"


def _time_bold_rung(segments: int) -> float:
    """Seconds for one ``BOLD_LINE_RE.match`` at this rung; raises on timeout."""
    start = time.perf_counter()
    _call_with_cap(BOLD_LINE_RE.match, _bold_failing_input(segments), _BOLD_FAIL_CAP_S)
    return time.perf_counter() - start


def _measure_bold_failing_ratio() -> float:
    """Median round ratio between the two rungs; both timed in the same round.

    Timing the rungs back-to-back inside a round (rather than timing each rung
    independently and dividing) is what makes the ratio robust to a loaded box:
    a stalled round inflates both legs, so the median round still reports the
    underlying scaling. Same idiom as ``tests/test_performance.py``.
    """
    small, large = _BOLD_FAIL_RUNGS
    _time_bold_rung(small)  # warm-up: first hit pays regex-cache costs
    ratios = []
    for _ in range(_BOLD_FAIL_ROUNDS):
        t_small = _time_bold_rung(small)
        t_large = _time_bold_rung(large)
        ratios.append(t_large / max(t_small, 1e-9))
    return statistics.median(ratios)


def test_bold_line_regex_failing_path_stays_linear():
    """The guard that actually discriminates: the FAILURE path must stay linear.

    The success-path ladder above passes for the pre-2026-11-10 lazy form as
    well as the shipped one, because both are linear when the line matches.
    This test forces the match to fail, where the lazy form doubled per added
    segment: it times out on the 32 KB rung instead of scoring, so a regression
    to `.+?` fails here loudly rather than shipping a ReDoS.
    """
    try:
        ratio = _measure_bold_failing_ratio()
    except _BoldGuardTimeout:
        raise AssertionError(
            f"BOLD_LINE_RE did not finish a {_BOLD_FAIL_RUNGS[1]}-segment rung "
            f"within {_BOLD_FAIL_CAP_S:.0f}s - the failure path has regressed "
            f"to catastrophic backtracking (a lazy `.+?` paired with the outer "
            f"`+` is the usual cause)"
        ) from None
    assert ratio < _BOLD_FAIL_MAX_RATIO, (
        f"BOLD_LINE_RE failing-path growth {ratio:.2f}x on a 4x length step "
        f"(budget {_BOLD_FAIL_MAX_RATIO}, linear is ~4.0) - the failure path "
        f"regressed to a superlinear shape; a lazy `.+?` paired with the "
        f"outer `+` is the usual cause"
    )


def test_bold_line_regex_failing_path_has_a_timeout_cap():
    """A pathological rung fails fast instead of hanging the gate.

    With the pre-2026-11-10 lazy form installed the large rung must raise
    rather than run for minutes. This is what makes a red gate report the
    regression in seconds instead of timing out the whole pytest leg.
    """
    monkeypatched = re.compile(r"^(?:\*\*.+?\*\*\s*)+$")
    saved = globals()["BOLD_LINE_RE"]
    globals()["BOLD_LINE_RE"] = monkeypatched
    try:
        with pytest.raises(_BoldGuardTimeout):
            _time_bold_rung(_BOLD_FAIL_RUNGS[1])
    finally:
        globals()["BOLD_LINE_RE"] = saved
