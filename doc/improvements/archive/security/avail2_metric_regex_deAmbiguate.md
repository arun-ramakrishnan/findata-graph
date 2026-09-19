---
title: "AVAIL-2 fix: de-ambiguate the metric range patterns"
status: executed
filed: "2026-09-18"
executed: "2026-09-19"
completed_md: "250"
area: "helpers/graph/derive_insights.py, helpers/graph/derive_events.py"
---

# AVAIL-2 fix: de-ambiguate the metric range patterns and cap line length

**Date:** 2026-09-18 · **Status:** EXECUTED (completed.md #250) · **Follows:**
Addendum 5, finding **AVAIL-2** (CONFIRMED). Found by the S4 verifier,
independently reproduced before filing.

## 1. Motivation

`_PCT_RE` and `_BPS_RE` share one defect in their range expression:

    \b\d[\d,]*(?:\.\d+)?\s*[-–to ]+\s*\d+(?:\.\d+)?\s*%

The literal class `[-–to ]` contains a space, and so does `\s` on both
sides — so a run of spaces splits between the three quantifiers in O(m²)
ways, and the two `\d+` anchors make it cubic. Measured on `"1" + " "*n +
"2" + " "*n + "3"`:

| n | 50 | 100 | 200 | 400 | 800 |
|---|---|---|---|---|---|
| `_PCT_RE` | 1.4 ms | 8.5 | 65 | 510 | 3,988 ms |

~8× per doubling = O(n³). The patterns run over every document line
(`derive_insights.py:1312`) with only a *lower* bound (`len < 15`); the
`derive_events` twin sits behind just an `fy`/`cy` prefilter. One
adversarial 8 KB line in an OCR'd newsletter stalls the metrics pass for
~8 minutes; 32 KB for hours. CLI-triggered (the derive layer is not
imported by `app.py`), but a silently hung extraction job is worse to
diagnose than a slow request.

## 2. Approach

1. **Remove the ambiguity in the pattern** — the space does not belong in
   the literal class. `\s*(?:[-–]|to)\s*` keeps the accepted shapes
   ("10-15%", "10 to 15%") without the overlap; the en-dash range stays.
   Verify the accepted-value corpus is unchanged (the existing metric
   tests pin the captured values).
2. **No length cap** (changed from the draft on evidence). The cap was
   drafted by analogy to `_parse_attribution`'s `len(s) > 100` guard, but a
   corpus measurement rejects it: 21,646 metric-bearing sentences across the
   full corpus run to 3,165 chars (p99 317, p99.9 732), and a 300-char cap
   drops 1.21% of all metric captures — including real `$33.2bn` / `13.2 GW`
   values captured by patterns this fix does not touch, lost only because
   they sit in long sentences. No cap can be both value-preserving (needs
   >3.2 KB) and effective at bounding a cubic (needs <=800 for sub-second),
   so the trade is rejected by the eval gate. The regression guard is the
   fuzz test in item 3 instead — an adversarial input that takes ~10 s on
   the old class and ~1 ms on the fix, which fails in `make qa` the moment
   the ambiguity returns.
3. **Extend `tests/test_fuzz_regex.py`** to both patterns with the
   adversarial input shape (numeric anchors flanked by space runs). The
   first pass cleared these patterns as linear by testing the wrong input
   shape; the fuzz must contain the shape that found the bug, or it guards
   nothing.

## 3. Gates

- md-lint clean; the captured-value tests must show zero regressions (a
  pattern change that drops real guidance values is a correctness bug,
  not a fix).
- Addendum 5's AVAIL-2 status moves to "remediated" with this proposal's
  completion number.
- `make qa` for the new fuzz cases.

## 4. Non-goals

- Rewriting the metric extraction grammar. The ambiguity is local to the
  range expression; the rest of the battery is linear (`_SPEAKER_NCT_RE`
  was verified linear by the same sweep).
- The `derive_events` refactor — only its pattern copy and the shared
  guard change apply.
