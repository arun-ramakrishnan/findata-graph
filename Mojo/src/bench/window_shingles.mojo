"""
 Windowed-shingle similarity lens (Mojo side of the parity bench leg).

 Feature proposal:
 doc/improvements/proposals/tooling/mojo_window_shingle_lens.md

 This module implements the identical algorithm as the Python golden
 fixture (Mojo/bench/mojo_window_shingles.py):
   - tokenize (bridge lower; alnum runs; stopword filter; dedupe)
   - contiguous windows width W over the token array; final window clamped
     via slice_utils.clamp_slice (FATAL bounds asserts otherwise)
   - window Jaccard over unique token sets (exact rational parity via
     cross-multiplication — zero float error)
   - local_affinity = max (and mean) over aligned windows

 The `main` runs the parity leg: every golden pair must match exactly
 (numerator/denominator equality), plus a perf smoke over the 2 largest
 docs. Exit code 1 on any mismatch.
"""

from std.python import Python, PythonObject
from std.time import perf_counter_ns

from bridge import sys_exit, to_i
from slice_utils import clamp_slice


def tokenize_lower(s: String, lower_fn: PythonObject) raises -> List[String]:
    """Mirror of the golden fixture tokenizer (alnum runs; stopword filter;
    dedupe). Stopwords: the/and/of/for/to/in/on/with/at/by/from."""
    var low = String(lower_fn(s).__str__())
    var out = List[String]()
    var cur = String("")
    for i in range(len(low.codepoints())):
        var cs = String(low[codepoint=i])
        var alnum = (cs >= "a" and cs <= "z") or (cs >= "0" and cs <= "9")
        if alnum:
            cur += cs
        else:
            if (
                cur.byte_length() > 0
                and not _is_stopword(cur)
                and not _contains(out, cur)
            ):
                out.append(cur)
            cur = String("")
    if (
        cur.byte_length() > 0
        and not _is_stopword(cur)
        and not _contains(out, cur)
    ):
        out.append(cur)
    return out^


def _is_stopword(t: String) -> Bool:
    var words = List[String]()
    words.append("the")
    words.append("and")
    words.append("of")
    words.append("for")
    words.append("to")
    words.append("in")
    words.append("on")
    words.append("with")
    words.append("at")
    words.append("by")
    words.append("from")
    for i in range(words.__len__()):
        if words[i] == t:
            return True
    return False


def _contains(lst: List[String], t: String) -> Bool:
    for i in range(lst.__len__()):
        if lst[i] == t:
            return True
    return False


def unique_window_tokens(toks: List[String], lo: Int, hi: Int) -> List[String]:
    var seen = List[String]()
    for i in range(lo, hi):
        var t = toks[i]
        if not _contains(seen, t):
            seen.append(t)
    return seen^


def window_jaccard(
    a: List[String], b: List[String], lo_a: Int, hi_a: Int, lo_b: Int, hi_b: Int
) -> Tuple[Int, Int]:
    """Return (num, den) of the window Jaccard (0/1 for empty union)."""
    var sa = unique_window_tokens(a, lo_a, hi_a)
    var sb = unique_window_tokens(b, lo_b, hi_b)
    var shared = 0
    for i in range(sa.__len__()):
        if _contains(sb, sa[i]):
            shared += 1
    var union = sa.__len__() + sb.__len__() - shared
    if union == 0:
        return Tuple[Int, Int](0, 1)
    return Tuple[Int, Int](shared, union)


def window_ranges(n: Int, width: Int) -> List[Tuple[Int, Int]]:
    var out = List[Tuple[Int, Int]]()
    if n <= 0:
        return out^
    var i = 0
    while i < n:
        var c = clamp_slice(n, i, i + width)
        out.append(Tuple[Int, Int](c[0], c[1]))
        i += width
    return out^


def local_affinity(
    a: List[String], b: List[String], width: Int
) -> Tuple[Tuple[Int, Int], Tuple[Int, Int]]:
    """Return ((max_num, max_den), (mean_num, mean_den)) over aligned windows.
    """
    var ra = window_ranges(a.__len__(), width)
    var rb = window_ranges(b.__len__(), width)
    var n = ra.__len__()
    if rb.__len__() < n:
        n = rb.__len__()
    if n == 0:
        return Tuple[Tuple[Int, Int], Tuple[Int, Int]](
            Tuple[Int, Int](0, 1), Tuple[Int, Int](0, 1)
        )
    var best_num = 0
    var best_den = 1
    var sum_num = 0
    var sum_den = 1
    for k in range(n):
        var v = window_jaccard(a, b, ra[k][0], ra[k][1], rb[k][0], rb[k][1])
        # v > best  ⇔  v_num * best_den > best_num * v_den
        if v[0] * best_den > best_num * v[1]:
            best_num = v[0]
            best_den = v[1]
        # sum += v  ⇔  (sum_num*den + num*den)/ (sum_den*den)
        sum_num = sum_num * v[1] + v[0] * sum_den
        sum_den = sum_den * v[1]
        # reduce by gcd (small ints; cheap)
        var g = _gcd(sum_num, sum_den)
        if g > 1:
            sum_num //= g
            sum_den //= g
    var mean_num = sum_num
    var mean_den = sum_den * n
    var g2 = _gcd(mean_num, mean_den)
    if g2 > 1:
        mean_num //= g2
        mean_den //= g2
    return Tuple[Tuple[Int, Int], Tuple[Int, Int]](
        Tuple[Int, Int](best_num, best_den), Tuple[Int, Int](mean_num, mean_den)
    )


def _gcd(a: Int, b: Int) -> Int:
    var x = a
    var y = b
    if x < 0:
        x = -x
    if y < 0:
        y = -y
    while y != 0:
        var t = x % y
        x = y
        y = t
    return x


def parity_run(pairs: PythonObject) raises -> Tuple[Int, Int, Int]:
    var total = pairs.__len__()
    var passed = 0
    var failed = 0
    for p in range(total):
        var row = pairs[p]
        var a = String(row[0].__str__())
        var b = String(row[1].__str__())
        var g_max_num = to_i(row[2])
        var g_max_den = to_i(row[3])
        var g_mean_num = to_i(row[4])
        var g_mean_den = to_i(row[5])
        var g_wins = to_i(row[6])
        var toks_a = tokenize_lower(a, _lower_fn())
        var toks_b = tokenize_lower(b, _lower_fn())
        var aff = local_affinity(toks_a, toks_b, 3)
        var wins_a = window_ranges(toks_a.__len__(), 3)
        var wins_b = window_ranges(toks_b.__len__(), 3)
        var wins = wins_a.__len__()
        if wins_b.__len__() < wins:
            wins = wins_b.__len__()
        var max_ok = aff[0][0] * g_max_den == g_max_num * aff[0][1]
        var mean_ok = aff[1][0] * g_mean_den == g_mean_num * aff[1][1]
        var ok = max_ok and mean_ok and wins == g_wins
        if ok:
            passed += 1
        else:
            failed += 1
            print(
                "FAIL pair ",
                p,
                " mojo max=",
                aff[0][0],
                "/",
                aff[0][1],
                " mean=",
                aff[1][0],
                "/",
                aff[1][1],
                " wins=",
                wins,
                " golden max=",
                g_max_num,
                "/",
                g_max_den,
                " mean=",
                g_mean_num,
                "/",
                g_mean_den,
                " wins=",
                g_wins,
            )
    return Tuple[Int, Int, Int](total, passed, failed)


def _lower_fn() raises -> PythonObject:
    var py = Python.import_module("mojo_window_shingles")
    return py.lower


def main() raises:
    Python.evaluate("__import__('sys').path.insert(0, 'Mojo/bench')")
    var py = Python.import_module("mojo_window_shingles")
    var pairs = py.golden_pairs()
    var t0 = perf_counter_ns()
    var res = parity_run(pairs)
    var dt_ms = Float64(perf_counter_ns() - t0) / 1e6
    print(
        "window-shingles parity: ",
        res[1],
        "/",
        res[0],
        " passed (",
        res[2],
        " failed) in ",
        dt_ms,
        " ms",
    )
    if res[2] != 0:
        sys_exit(1)
    # Perf smoke: the two longest docs (indices 0 and 15)
    var big_a = String(pairs[0][0].__str__())
    var big_b = String(pairs[15][1].__str__())
    var ta = tokenize_lower(big_a, _lower_fn())
    var tb = tokenize_lower(big_b, _lower_fn())
    var t1 = perf_counter_ns()
    var aff = local_affinity(ta, tb, 3)
    var big_ms = Float64(perf_counter_ns() - t1) / 1e6
    print(
        "perf smoke 2 docs: ",
        big_ms,
        " ms (affinity ",
        aff[0][0],
        "/",
        aff[0][1],
        ")",
    )
    if big_ms > 200.0:
        print("PERF SMOKE FAILED (over 200ms)")
        sys_exit(1)
