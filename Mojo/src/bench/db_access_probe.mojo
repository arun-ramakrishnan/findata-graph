"""
 DB access probe — SQLite (research.db: FTS5 + relational) and DuckDB
 (graph.duckdb) driven from Mojo through the Python drivers (sqlite3 /
 duckdb) via the bridge. Companion fixture: Mojo/bench/mojo_db_access.py.

 Method (same three-way discipline as the regex battery):
   1. python side — bench_native(): every case run natively in CPython
   2. direct side — the SAME case callables invoked FROM Mojo; every
      row is consumed on the Mojo side (repr checksum) — that row
      marshaling cost is what "access the DB from Mojo" actually costs
   3. compare     — checksum parity + per-case time ratio
 Parity is GATED: any checksum mismatch exits 1 — the bench leg goes
 red (2026-09-03, matching the graph-algos gating).
 Run from the repo root (the bench harness sets cwd).
"""


from std.python import Python
from std.time import perf_counter_ns

from bridge import sys_exit


def main() raises:
    Python.evaluate("__import__('sys').path.insert(0, 'Mojo/bench')")
    var db = Python.import_module("mojo_db_access")

    var n = Int(String(db.ncases().__str__()))
    var cases = db.cases()
    var reps = 50

    # --- python side (native) ---
    var py = db.bench_report(reps)
    print(py)
    print()

    # --- direct side: every case called from Mojo, rows consumed here ---
    var npass = 0
    var nfail = 0
    for i in range(n):
        var name = String(cases[i][0].__str__())
        var case_fn = cases[i][1]
        var t0 = perf_counter_ns()
        var nrows = 0
        var checksum = 0
        for r in range(reps):
            var rows = case_fn()
            nrows = rows.__len__()  # dunder -> Mojo Int
            checksum = 0
            for j in range(nrows):
                # consume the row ON THE MOJO SIDE: repr crosses the
                # bridge value-by-value — the real access cost
                checksum += String(rows[j].__repr__()).byte_length()
        var t1 = perf_counter_ns()
        var dt = Float64(t1 - t0) / 1_000_000_000.0
        var py_ck = Int(String(db.checksum_of(name).__str__()))
        var py_dt = Float64(String(db.elapsed_of(name).__str__()))
        if checksum == py_ck:
            npass += 1
            print(
                "PASS ",
                name,
                ": rows=",
                nrows,
                " checksum=",
                checksum,
                " mojo=",
                dt,
                "s python=",
                py_dt,
                "s ratio=",
                dt / py_dt,
            )
        else:
            nfail += 1
            print(
                "FAIL ", name, ": mojo checksum=", checksum, " python=", py_ck
            )
    print("---", npass, "/", n, "db access cases checksum-parity passed")
    if nfail > 0:
        print("DB-ACCESS PARITY FAIL: ", nfail, " case(s) mismatched")
        sys_exit(1)
