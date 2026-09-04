"""
 Correctness tests for the shared cosine SIMD kernel (Mojo/src/common/cosine.mojo).

 Run via `make mojo-test` (mojo run -I Mojo/src/<pkg> Mojo/tests/<file>) — Mojo 1.0
 has no `mojo test` CLI; each test file carries its own TestSuite runner.
 The all-ones case is the regression guard for the read_bytes() clobber
 bug (first 8 bytes zeroed → score collapsed to ~0.9977 instead of 1.0).
"""


from std.math import sqrt
from std.memory.alloc import Layout
from std.testing import TestSuite, assert_true

import cosine


def _f32_ptr(values: List[Float32]) -> Pointer[Float32, MutUntrackedOrigin]:
    # Stack values copied into a small heap block; tests leak intentionally
    # (process-lifetime, same as the benchmark's buffers).
    var p = alloc(Layout[Float32](count=len(values))).unsafe_leak()
    for i in range(len(values)):
        p.unsafe_offset(i)[] = values[i]
    return p


def test_self_vector_is_one() raises:
    # Identical vectors → cosine exactly 1 (barring float rounding).
    var v = _f32_ptr([0.5, -1.25, 2.0, 3.75, 0.125, -0.5, 1.0, 8.0])
    var sumsq = Float64(0.0)
    for i in range(8):
        var x = Float64(v.unsafe_offset(i)[])
        sumsq += x * x
    var score = cosine.row_cosine(v, v, sqrt(sumsq), 8)
    assert_true(abs(score - 1.0) < 1e-5, "self-vector cosine should be 1.0")


def test_all_ones_exact() raises:
    # The read_bytes regression guard: any byte clobber breaks exact 1.0.
    var row = _f32_ptr([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    var q = _f32_ptr([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    var score = cosine.row_cosine(row, q, sqrt(8.0), 8)
    assert_true(abs(score - 1.0) < 1e-6, "all-ones cosine must be 1.0")


def test_orthogonal_is_zero() raises:
    var row = _f32_ptr([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    var q = _f32_ptr([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    var score = cosine.row_cosine(row, q, 1.0, 8)
    assert_true(abs(score) < 1e-12, "orthogonal vectors must score 0")


def test_zero_vector_guards() raises:
    # Zero norms must return 0.0, not NaN (mirrors the app.py contract).
    var zero = _f32_ptr([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    var q = _f32_ptr([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    var score = cosine.row_cosine(zero, q, 8.0, 8)
    assert_true(score == 0.0, "zero-norm cosine must be 0.0, not NaN")


def main() raises:
    TestSuite.discover_tests[__functions_in_module()]().run()
