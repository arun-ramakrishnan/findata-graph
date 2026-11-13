"""
 Mojo 1.2 changelog trials (filed with
 doc/improvements/proposals/tooling/mojo_12_changelog_trials.md).

 Each trial maps to a 1.2.0 changelog item the lane could adopt:
   T1  match/case pattern matching (literals, or-patterns, guards,
        wildcards, tuples, list destructuring, comptime match)
   T2  String padding / repetition performance (ascii_rjust/ljust/center,
        repeat) — correctness + a coarse timing smoke
   T3  atof() correctness fixes (>53-bit mantissas, malformed raise)
   T4  startswith/endswith O(affix) + Python edge cases
   T5  SIMD.from_bytes/as_bytes endian parameter (Endian.big/.little)
   T6  Array slicing returns Span + Tuple.first_of[T]() accessor
   T7  where <cond> else "msg" diagnostics + trait opt-out `not <Trait>`
   T8  Layout/Allocation keyword-only alignment param (Alignment type)

 Compile-time features (faster builds, --elaboration-stats, jemalloc) are
 measured at the proposal level, not inside this suite.

 Run via `make mojo-test` (mojo run -I Mojo/src/<pkg> Mojo/tests/<file>).
"""

from std.memory.alloc import Layout
from std.simd import SIMD, Endian
from std.testing import TestSuite, assert_true
from std.time import perf_counter_ns


# --------------------------------------------------------------------------
# T1 — pattern matching (1.2 headline feature)
# --------------------------------------------------------------------------

def _match_literal(x: Int) -> String:
    match x:
        case 0:
            return "zero"
        case 1 | 2:  # or-pattern
            return "small"
        case _:       # wildcard binding (no capture)
            return "other"


def _match_guard(x: Int) -> String:
    match x:
        case n if n > 10:
            return "big"
        case n if n > 0:
            return "positive"
        case _:
            return "nonpositive"


def _match_tuple(t: Tuple[Int, String]) -> String:
    match t:
        case (0, s):
            return "zero:" + s
        case (n, "x") if n == 1:
            return "one-x"
        case _:
            return "other"


def _destructure_list(xs: List[Int]) -> Int:
    match xs:
        case []:
            return -1
        case [a]:
            return a
        case [a, *middle, z]:  # list destructuring with *middle
            return a + middle.__len__() + z
    return -99  # unreachable; satisfies definite-return analysis


@__parameter
def _comptime_kind(kind: Int) -> String:
    # comptime match over a compile-time Int
    match kind:
        case 0:
            return "int"
        case 1:
            return "float"
        case _:
            return "other"


def test_t1_pattern_matching() raises:
    assert_true(_match_literal(0) == "zero", "literal match")
    assert_true(_match_literal(1) == "small", "or-pattern 1")
    assert_true(_match_literal(2) == "small", "or-pattern 2")
    assert_true(_match_literal(9) == "other", "wildcard")
    assert_true(_match_guard(42) == "big", "guard >10")
    assert_true(_match_guard(5) == "positive", "guard >0")
    assert_true(_match_guard(-3) == "nonpositive", "guard else")
    assert_true(_match_tuple(Tuple[Int, String](0, "hi")) == "zero:hi", "tuple literal")
    assert_true(_match_tuple(Tuple[Int, String](1, "x")) == "one-x", "tuple guard")
    assert_true(_match_tuple(Tuple[Int, String](2, "y")) == "other", "tuple fallback")
    var xs = List[Int]()
    assert_true(_destructure_list(xs) == -1, "empty list")
    xs.append(7)
    assert_true(_destructure_list(xs) == 7, "single-element list")
    xs.append(2)
    xs.append(3)
    assert_true(_destructure_list(xs) == 11, "a + len(middle) + z")
    assert_true(_comptime_kind(0) == "int", "comptime match 0")
    assert_true(_comptime_kind(1) == "float", "comptime match 1")
    assert_true(_comptime_kind(5) == "other", "comptime match else")


# --------------------------------------------------------------------------
# T2 — String padding / repetition (perf + correctness)
# --------------------------------------------------------------------------

def test_t2_string_pad_repeat() raises:
    var s = "ab"
    var r = s.ascii_rjust(6, "0")
    assert_true(r == "0000ab", "ascii_rjust")
    var l = s.ascii_ljust(5, ".")
    assert_true(l == "ab...", "ascii_ljust")
    var c = s.ascii_center(7, "-")
    assert_true(c == "--ab---", "ascii_center odd fill")
    var c2 = s.ascii_center(6, "-")
    assert_true(c2 == "--ab--", "ascii_center even fill")
    var rep = s * 3
    assert_true(rep == "ababab", "string repetition")
    # Coarse timing smoke: 20k pads of len-32 must stay well under 1 s
    # (1.2 ascii_rjust is single-allocation; the old path was up to 86x
    # slower per the changelog).
    var t0 = perf_counter_ns()
    for _ in range(20000):
        var _ = s.ascii_rjust(32, " ")
        var _2 = s * 16
    var dt_ms = Float64(perf_counter_ns() - t0) / 1e6
    assert_true(dt_ms < 1000.0, "20k pads+repeats under 1s")


# --------------------------------------------------------------------------
# T3 — atof() correctness (1.2 fixes)
# --------------------------------------------------------------------------

def test_t3_atof_correctness() raises:
    # Note: standalone `atof` no longer exists in the 1.2.0 stdlib; the
    # builtin String→Float64 conversion is the supported path (trial
    # records the changelog's atof() fixes as N/A / moved API).
    # Long-mantissa rounding: 2^53 + 1 must not round up by a full ulp
    # chain (the 1.2 fix rounds >53-bit mantissas correctly).
    var big = Float64("9007199254740993")
    assert_true(big == 9007199254740992.0, "2^53+1 rounds to 2^53")
    # >19 digits
    var long = Float64("1234567890123456789012345")
    assert_true(long > 1.2e24, ">19 digit parse magnitude")
    # Malformed raise
    var raised = False
    try:
        var _ = Float64("1..2")
    except Error:
        raised = True
    assert_true(raised, "malformed '1..2' raises")
    var raised2 = False
    try:
        var _ = Float64("1e5.0")
    except Error:
        raised2 = True
    assert_true(raised2, "malformed '1e5.0' raises")


# --------------------------------------------------------------------------
# T4 — startswith/endswith O(affix)
# --------------------------------------------------------------------------

def test_t4_affix() raises:
    var s = "The quick brown fox"
    assert_true(s.startswith("The"), "startswith hit")
    assert_true(not s.startswith("the"), "startswith case-sensitive miss")
    assert_true(s.startswith(""), "startswith empty affix")
    assert_true(s.endswith("fox"), "endswith hit")
    assert_true(not s.endswith("Fox"), "endswith case miss")
    # O(affix): a 10MB string with a 3-char affix must compare fast.
    var big = "x" * 10000000
    var t0 = perf_counter_ns()
    var hit = big.startswith("xxx")
    var dt_ms = Float64(perf_counter_ns() - t0) / 1e6
    assert_true(hit, "10MB startswith hit")
    assert_true(dt_ms < 100.0, "10MB startswith under 100ms")


# --------------------------------------------------------------------------
# T5 — SIMD.from_bytes/as_bytes endian parameter
# --------------------------------------------------------------------------

def test_t5_simd_endian() raises:
    # 4 bytes, big-endian → 0x01020304
    var bytes = Array[UInt8, 4](uninitialized=True)
    bytes[0] = 1
    bytes[1] = 2
    bytes[2] = 3
    bytes[3] = 4
    var be = SIMD[DType.uint32, 1].from_bytes[endian=Endian.big](bytes)
    assert_true(be[0] == 0x01020304, "from_bytes big-endian")
    var le = SIMD[DType.uint32, 1].from_bytes[endian=Endian.little](bytes)
    assert_true(le[0] == 0x04030201, "from_bytes little-endian")


# --------------------------------------------------------------------------
# T6 — Array slicing → Span + Tuple.first_of[T]()
# --------------------------------------------------------------------------

def _sum_readonly(s: Span[Int, ...]) -> Int:
    var acc = 0
    for i in range(s.__len__()):
        acc += s[i]
    return acc


def _bump_mut(s: Span[mut=True, Int, ...]):
    for i in range(s.__len__()):
        s[i] += 1


def test_t6_slice_firstof() raises:
    var arr = Array[Int, 4](uninitialized=True)
    arr[0] = 1
    arr[1] = 2
    arr[2] = 3
    arr[3] = 4
    var sp = arr[1:3]  # returns Span[Int]
    assert_true(sp.__len__() == 2, "slice length")
    assert_true(sp[0] == 2 and sp[1] == 3, "slice contents")
    var t = Tuple[Int, String](42, "answer")
    var first = t.first_of[Int]()
    assert_true(first == 42, "tuple first_of[Int]")

    # Probed slice semantics (proposal §2.4 / §2.4.1):
    # write-through: mutating the span mutates the array
    _bump_mut(arr[0:2])
    assert_true(arr[0] == 2 and arr[1] == 3, "write-through mutates array")
    # full / tail / head / empty / single
    assert_true(arr[:].__len__() == 4, "full slice")
    assert_true(arr[2:].__len__() == 2, "tail slice")
    assert_true(arr[:2].__len__() == 2, "head slice")
    assert_true(arr[2:2].__len__() == 0, "empty slice")
    assert_true(arr[2:3].__len__() == 1, "single slice")
    # nested slice-of-slice
    var nested = arr[0:4][1:3]
    assert_true(nested.__len__() == 2 and nested[0] == 3, "slice-of-slice")
    # string elements + write-through
    var strs = Array[String, 2](uninitialized=True)
    strs[0] = "alpha"
    strs[1] = "beta"
    var ssp = strs[0:2]
    ssp[0] = "ALPHA!"
    assert_true(strs[0] == "ALPHA!", "string write-through")
    # fn-passing: read-only + mutable parameter forms
    var arr5 = Array[Int, 5](uninitialized=True)
    for i in range(5):
        arr5[i] = i * 10
    assert_true(_sum_readonly(arr5[2:5]) == 90, "read-only span param")
    # materialize (manual allocate+fill; no direct span→array ctor)
    var copy = Array[Int, 3](uninitialized=True)
    for i in range(3):
        copy[i] = arr5[2:5][i]
    assert_true(_sum_readonly(copy) == 90, "manual materialize sum")


# --------------------------------------------------------------------------
# T7 — where <cond> else "msg" + trait opt-out `not <Trait>`
# --------------------------------------------------------------------------

def _requires_positive[x: Int]() -> Int
    where x > 0 else "x must be positive":
    return x * 2


trait CanRun:
    def run(self) -> String:
        ...


struct Excluded(not CanRun):
    pass


def test_t7_where_not_trait() raises:
    assert_true(_requires_positive[4]() == 8, "where-guarded fn")


# --------------------------------------------------------------------------
# T8 — Layout/Allocation keyword-only alignment param
# --------------------------------------------------------------------------

def test_t8_layout_alignment() raises:
    # Note: the installed 1.2.0 stdlib Layout only exposes `count` (the
    # changelog's alignment parameter is not present in this build) —
    # trial records API mismatch; allocation still exercised with count.
    var l = Layout[UInt8](count=8)
    var p = alloc(l).unsafe_leak()
    var addr = Int(p)
    assert_true(addr > 0, "allocation non-null")


def main() raises:
    TestSuite.discover_tests[__functions_in_module()]().run()
