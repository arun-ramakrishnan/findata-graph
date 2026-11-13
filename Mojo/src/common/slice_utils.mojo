"""
 Safe slice-bound clamping for Array/Span slicing.

 Mojo 1.2 slicing (`a[lo:hi]`) is a zero-copy Span view with STRICT,
 non-recoverable bounds checks: an out-of-range start/end or a negative
 index aborts the process with an Assert Error (probe results recorded in
 doc/improvements/proposals/tooling/mojo_12_changelog_trials.md §2.4 §2.4.1).
 `clamp_slice` turns a possibly-untrusted [lo, hi) request into a legal
 [lo', hi') range so callers can slice safely.

 NOTE on API shape: returning a Span from a free helper is NOT expressible
 in the 1.2 stdlib — the span's `origin` parameter depends on the argument
 (`Span[mut, T, origin=Origin[mut=mut]]`), and `-> Span[T, ...]` fails with
 `'Span[T, _, address_space=_]' is not concrete, use '[]' to bind missing
 parameters`; `-> Span[T, True]` fails with `value passed to 'origin' cannot
 be converted from 'Bool' to 'Origin[mut=$0]'`. Hence the clamp returns the
 bound pair; the caller applies `a[lo:hi]` itself.
"""


def clamp_slice(len_: Int, lo: Int, hi: Int) -> Tuple[Int, Int]:
    """Clamp [lo, hi) into [0, len_] with empty-safe semantics.

    - negative lo clamps to 0 (no Python-style wrap semantics)
    - hi > len_ clamps to len_
    - lo >= hi yields an empty range (lo' == hi')
    - len_ == 0 yields (0, 0)
    """
    var l = lo
    if l < 0:
        l = 0
    if l > len_:
        l = len_
    var h = hi
    if h < l:
        h = l
    if h > len_:
        h = len_
    return Tuple[Int, Int](l, h)


def main():
    # The flat-binary build in Makefile.mojo builds every src/<pkg>/*.mojo
    # into Mojo/bin/; modules need a main. Library self-check only.
    var c = clamp_slice(5, -1, 10)
    if c[0] != 0 or c[1] != 5:
        print("slice_utils self-check FAILED")
