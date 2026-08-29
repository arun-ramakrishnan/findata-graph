from std.python import Python
from std.time import perf_counter_ns

# Regex interop probe: verifies the Python `regex` module through the
# Mojo bridge AND measures call overhead. Fixtures:
#   Mojo/bench/mojo_regex_cases.json — 51 cases as data (the SINGLE
#     source both sides execute: patterns, inputs, modes, goldens)
#   Mojo/bench/mojo_regex_battery.py — file-driven dispatcher + timing
#   Mojo/bench/build_mojo_regex_cases.py — regenerates the JSON goldens
#
# Method (2026-08-29 rework — was 3 direct patterns + a python-internal
# battery run + a 200k-vs-100k bench):
#   1. python side  — battery.python_results(): every case executed
#                     NATIVELY in CPython
#   2. direct side  — the SAME case callables invoked FROM Mojo through
#                     the bridge, one by one
#   3. compare      — direct-side repr == python-side repr == expected
#   4. bench        — 100k case calls cycling all 51 callables, timed
#                     identically on both sides (real interop workload)
# Run from the repo root (the bench harness sets cwd): make mojo-bench
# MOJO_BENCH_ARGS="--leg regex-bridge".  Requires `regex` in the .venv.

def main() raises:
    # Python.evaluate is eval(): statements are rejected — use an
    # expression (eval("import ...") is a SyntaxError).
    Python.evaluate("__import__('sys').path.insert(0, 'Mojo/bench')")
    var battery = Python.import_module("mojo_regex_battery")

    # --- python side (native) ---
    var py = battery.python_results()

    # --- direct side: every case called from Mojo through the bridge ---
    var cases = battery.cases()
    var n = Int(String(battery.ncases().__str__()))
    var npass = 0
    for i in range(n):
        var one_case = cases[i]
        var name = String(one_case[0].__str__())
        var case_fn = one_case[1]
        # __repr__ (not __str__): python_results uses repr(), and for
        # bare strings str("a")="a" != repr("a")="'a'".
        var got_direct = String(case_fn().__repr__())
        var pair = py.get(name)
        var got_python = String(pair[0].__str__())
        var exp_repr = String(pair[1].__str__())
        if got_direct == got_python and got_direct == exp_repr:
            npass += 1
            print("PASS", name, ": direct == python == expected")
        else:
            print("FAIL", name, ": direct=", got_direct,
                  " python=", got_python, " expected=", exp_repr)
    print("---", npass, "/", n, "direct-vs-python checks passed")

    # --- bench: the REAL workload on both sides — `iters` case calls
    # cycling the 21 callables (each is compile+match+result-build; most
    # are findall) ---
    var iters = 100000
    var pure = battery.bench_report(iters)
    print("pure-python ", pure)

    var t0 = perf_counter_ns()
    for i in range(iters):
        var case_fn = cases[i % n][1]
        _ = case_fn()
    var t1 = perf_counter_ns()
    var dt = Float64(t1 - t0) / 1_000_000_000.0
    var total = Float64(iters)
    print("mojo-bridge  ", iters, " case calls: ", dt, "s, ",
          total / dt, " calls/s")
