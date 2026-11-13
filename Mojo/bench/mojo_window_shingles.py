"""Windowed-shingle similarity — Python golden oracle for the Mojo bench leg.

Feature proposal: doc/improvements/proposals/tooling/mojo_window_shingle_lens.md

The Mojo side (Mojo/src/bench/window_shingles.mojo) implements the identical
algorithm. This fixture provides:

  * the SAMPLE corpus (deterministic, includes adversarial cases: empty
    docs, width>len, shared-tail windows, duplicate tokens inside a window);
  * `lower()` — the bridge lower_fn (Mojo lacks str.lower);
  * `golden_pairs()` — precomputed goldens per pair: (a, b, max_num,
    max_den, mean_num, mean_den, window_count). Rationals are kept as
    integer numerator/denominator pairs so parity checks are EXACT
    (cross-multiplication, zero float error).

The lens:
  - tokenize: lowercase, alnum runs, stopword filter, dedupe per doc
    (mirrors integrity_check.meaningful_tokens semantics);
  - windows: contiguous non-overlapping windows of width W over the token
    list; the final window may be shorter (clamped);
  - window Jaccard: |A∩B| / |A∪B| over unique tokens in each window
    (empty union → 0/1);
  - local_affinity = max over aligned window pairs; mean variant = mean
    over aligned pairs (aligned = same window index; if window counts
    differ, pairs are the first min(countA, countB) windows).
"""

from __future__ import annotations

from fractions import Fraction

WIDTH = 3

SAMPLE_DOCS = [
    "The quick brown fox jumps over the lazy dog",
    "quick brown fox runs fast under the sun",
    "The lazy dog sleeps all day long",
    "lazy dog and quick brown fox are friends",
    "alpha beta gamma delta epsilon zeta eta theta",
    "alpha beta gamma zeta eta theta",
    "one two three four five six seven eight",
    "one two three nine ten eleven twelve",
    "a b c d e f g",
    "a b c d e f g h i j",
    "x y z x y z x y z",
    "x y z",
    "",
    "singleword",
    "The quick brown fox",
    "the QUICK Brown FOX jumped",
    "fox jumps over the lazy dog",
    "lazy dog sleeps",
    "Quick brown fox jumps",
    "brown fox jumps over",
]

STOPWORDS = {"the", "and", "of", "for", "to", "in", "on", "with", "at", "by", "from"}


def lower(s: str) -> str:
    return s.lower()


def tokenize_lower(s: str) -> list[str]:
    """Mojo-mirror tokenizer (alnum runs; stopword filter; dedupe)."""
    low = lower(s)
    cur = ""
    out: list[str] = []
    for ch in low:
        if ch.isalnum():
            cur += ch
        else:
            if cur and cur not in STOPWORDS and cur not in out:
                out.append(cur)
            cur = ""
    if cur and cur not in STOPWORDS and cur not in out:
        out.append(cur)
    return out


def window_ranges(n: int, width: int) -> list[tuple[int, int]]:
    if n <= 0:
        return []
    out: list[tuple[int, int]] = []
    i = 0
    while i < n:
        hi = min(i + width, n)
        out.append((i, hi))
        i += width
    return out


def _unique_window_tokens(toks: list[str], lo: int, hi: int) -> list[str]:
    seen: list[str] = []
    for k in range(lo, hi):
        t = toks[k]
        if t not in seen:
            seen.append(t)
    return seen


def window_jaccard(
    a: list[str], b: list[str], lo_a: int, hi_a: int, lo_b: int, hi_b: int
) -> Fraction:
    sa = _unique_window_tokens(a, lo_a, hi_a)
    sb = _unique_window_tokens(b, lo_b, hi_b)
    shared = 0
    for t in sa:
        if t in sb:
            shared += 1
    union = len(sa) + len(sb) - shared
    if union == 0:
        return Fraction(0, 1)
    return Fraction(shared, union)


def _aligned_windows(a: list[str], b: list[str], width: int) -> list[tuple[int, int, int, int]]:
    ra = window_ranges(len(a), width)
    rb = window_ranges(len(b), width)
    n = min(len(ra), len(rb))
    return [(ra[i][0], ra[i][1], rb[i][0], rb[i][1]) for i in range(n)]


def local_affinity(a: list[str], b: list[str], width: int) -> tuple[Fraction, Fraction]:
    """Return (max, mean) window Jaccard over aligned windows."""
    wins = _aligned_windows(a, b, width)
    if not wins:
        return Fraction(0, 1), Fraction(0, 1)
    vals = [window_jaccard(a, b, la, ha, lb, hb) for la, ha, lb, hb in wins]
    mx = max(vals)
    mean = sum(vals, Fraction(0, 1)) / len(vals)
    return mx, mean


def golden_pairs() -> list[tuple[str, str, int, int, int, int, int]]:
    """(a, b, max_num, max_den, mean_num, mean_den, window_count)."""
    out: list[tuple[str, str, int, int, int, int, int]] = []
    docs = SAMPLE_DOCS
    for i in range(len(docs)):
        for j in range(i, len(docs)):
            a = tokenize_lower(docs[i])
            b = tokenize_lower(docs[j])
            mx, mean = local_affinity(a, b, WIDTH)
            n_wins = min(len(window_ranges(len(a), WIDTH)), len(window_ranges(len(b), WIDTH)))
            out.append(
                (
                    docs[i],
                    docs[j],
                    mx.numerator,
                    mx.denominator,
                    mean.numerator,
                    mean.denominator,
                    n_wins,
                )
            )
    return out


if __name__ == "__main__":
    pairs = golden_pairs()
    print(f"golden pairs: {len(pairs)}")
    for p in pairs[:5]:
        print(p[:2], p[2:])
