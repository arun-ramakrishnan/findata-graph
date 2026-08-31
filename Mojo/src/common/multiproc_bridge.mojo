"""
 Python multiprocessing DRIVEN FROM MOJO through the bridge — verified
 2026-08-29 (Mojo 1.0.0, python 3.14, 4 logical cores). Companion to
 doc/local/mojo/mojo_concurrency.md §6: when work marshals through
 Python (sqlite/duckdb/python-yaml), TaskGroup gains nothing (bridge
 calls hold the GIL) — but Python multiprocessing pools DO parallelize,
 and Mojo can drive them.

 Measured (4 x 20k regex battery case calls, CPU-bound):
   sequential  0.815 s | fork Pool(4) 0.337 s (2.42x)
                              forkserver 0.388 s (2.10x)

 Requirements discovered:
   1. Pool target must be a PICKLABLE module-level function (our
      fixtures qualify; lambdas/exec'd code do not).
   2. fork children inherit the parent's sys.path; forkserver (the
      3.14 Linux default) starts fresh interpreters — set PYTHONPATH
      BEFORE creating the pool or children cannot import the target's
      module.
   3. Python.evaluate is eval(): no statement junk in setup strings.

 Run: make mojo-build && Mojo/bin/multiproc_bridge   (repo root cwd)
"""


from std.python import Python
from std.time import perf_counter_ns


def run_pool(ctx_name: String, nprocs: Int, iters: Int) raises -> Float64:
    var mp = Python.import_module("multiprocessing")
    # forkserver children re-import the target module from scratch —
    # PYTHONPATH must carry it (fork children inherit sys.path anyway)
    Python.evaluate(
        "__import__('os').environ.setdefault('PYTHONPATH', 'Mojo/bench')"
    )
    var ctx = mp.get_context(String(ctx_name))
    var fx = Python.import_module("mojo_regex_battery")
    var target = fx.run_cases  # module-level fn: picklable by reference
    var args = Python.evaluate("[" + String(iters) + "] * " + String(nprocs))
    var t0 = perf_counter_ns()
    var pool = ctx.Pool(nprocs)
    var res = pool.map(target, args)
    pool.close()
    pool.join()
    var t1 = perf_counter_ns()
    var total = Float64(0.0)
    for i in range(res.__len__()):
        total = total + Float64(String(res[i].__str__()))
    print(
        ctx_name,
        ": pool.map ",
        nprocs,
        "x",
        iters,
        " wall=",
        Float64(t1 - t0) / 1e9,
        "s (sum of per-proc secs ",
        total,
        ")",
    )
    return Float64(t1 - t0) / 1e9


def main() raises:
    Python.evaluate("__import__('sys').path.insert(0, 'Mojo/bench')")
    var fx = Python.import_module("mojo_regex_battery")

    # sequential baseline: 4 x 20k in-process
    var t0 = perf_counter_ns()
    var seq = 0.0
    for i in range(4):
        seq = seq + Float64(String(fx.run_cases(20000).__str__()))
    var t1 = perf_counter_ns()
    print(
        "sequential: 4 x 20000 wall=",
        Float64(t1 - t0) / 1e9,
        "s (sum ",
        seq,
        "s)",
    )

    _ = run_pool("fork", 4, 20000)
    _ = run_pool("forkserver", 4, 20000)
