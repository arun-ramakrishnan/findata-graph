# contract: mojo format copy-diff gate (tests/test_lint_gates.py; make mojo-format fixes)
"""
 <One-line purpose>.

 <2-6 lines: what it proves/measures, the result if it is a probe
 (with date), and the finding log it belongs to (doc/local/*.md).>

 Run from the repo root (keep the literal command):
   .venv/bin/mojo run Mojo/src/<pkg>/<this>.mojo

 House rules:
  - `mojo format` is byte-canonical, gated by tests/test_lint_gates.py
    (copy-diff, since this toolchain has no --check); `make mojo-format` fixes.
  - This toolchain (Mojo 1.0.0 ed45d567) parses `def` functions; `fn`
    does not parse — match the rest of Mojo/src, don't copy syntax from
    current upstream docs.
  - Module docstrings are the doc-extraction source — first line is the
    purpose, keep prose wrapped ~79 cols like this file.
  - Prefer std.* / max.* only; machine-specific deps stay OUT of the repo
    (see analyzer.mojo's GPU-tier note).
"""


from std.math import sqrt


def scaled(x: Float64, factor: Float64) -> Float64:
    """Return x scaled by factor (one-line contract per def).

    Docstrings extract per-symbol: first line states WHAT, following
    lines state constraints the signature cannot show.
    """
    return x * factor


def main() -> None:
    var total: Float64 = 0.0
    for i in range(10):
        total += scaled(Float64(i), 1.5)
    print("template total:", total)
    print("sqrt(2) =", sqrt(2.0))
