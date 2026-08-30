from std.python import Python, PythonObject
from std.time import perf_counter_ns

# Graph-algos + FTS bridge probe — phase 1 of the graph-algos port
# (proposal doc/improvements/proposals/mojo_graph_algos_port.md, APPROVED
# 2026-08-30). The ORIGINAL python modules are the engine; this probe is
# the Mojo driver:
#   §1 extension inventory  — onager / sqlite_scanner / vss must be LOADED
#        on the canonical graph connection (query.connect(read_only=True))
#   §2 SQL cases — the Mojo side EXECUTES the SQL itself (Onager temp-table
#        materialisation + table functions with named params (seed => 42),
#        graph aux counts, and the repo's full FTS5 surface: note_search /
#        doc_search / script_search shapes + the vec0 KNN mirror is §3).
#        Parity = row count + repr-byte checksum vs the native run
#        (fixture Mojo/bench/mojo_graph_algos.py, db_access pattern).
#   §3 metric cases — the original functions (all 14 `make graph-algos`
#        metrics + whole-graph metrics + vec0 KNN) driven from Mojo; the
#        CANONICAL string is rebuilt Mojo-side (sort/join/group logic here,
#        only %.6f float formatting via the fixture's bridge lambda) and
#        must equal the native canonical byte-for-byte.
#   §4 end-to-end — algorithms CLI --all --no-apply (the make target,
#        verbatim) driven from Mojo: rc==0, 12 (metric= headers +
#        link-predict + voterank, zero FAIL lines.
#
# GATING (operator decision 2026-08-30): unlike the db-access /
# db-integrity legs, ANY parity failure exits 1 — the bench leg goes red.
# SKIPs (e.g. vec mirror absent) never fail and are excluded from the
# denominator. Run from the repo root (the bench harness sets cwd):
#   make mojo-bench MOJO_BENCH_ARGS='--leg graph-algos'


# ---------------------------------------------------------------- helpers
# (same patterns as integrity_check.mojo — each probe stays self-contained)

def contains(lst: List[String], s: String) -> Bool:
    for i in range(len(lst)):
        if lst[i] == s:
            return True
    return False


def _merge_sorted(a: List[String], b: List[String]) -> List[String]:
    var out = List[String]()
    var i = 0
    var j = 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            out.append(a[i])
            i += 1
        else:
            out.append(b[j])
            j += 1
    while i < len(a):
        out.append(a[i])
        i += 1
    while j < len(b):
        out.append(b[j])
        j += 1
    return out^


def merge_sort_strs(lst: List[String]) -> List[String]:
    """O(n log n) sort for the big lists (link-predict ~20k pairs);
    sort_strs' insertion sort is for the <= ~1.5k key lists."""
    if len(lst) <= 1:
        return lst.copy()
    var mid = len(lst) // 2
    var left = List[String]()
    var right = List[String]()
    for i in range(len(lst)):
        if i < mid:
            left.append(lst[i])
        else:
            right.append(lst[i])
    return _merge_sorted(merge_sort_strs(left), merge_sort_strs(right))


def join_list(lst: List[String], sep: String) -> String:
    var out = String("")
    for i in range(len(lst)):
        if i > 0:
            out += sep
        out += lst[i]
    return out


def py_str(o: PythonObject) raises -> String:
    return String(o.__str__())


def to_i(o: PythonObject) raises -> Int:
    return Int(String(o.__str__()))


def head_bytes(s: String, n: Int) -> String:
    # diagnostic truncation — byte slicing TRAPS past the end
    var end = n if n < s.byte_length() else s.byte_length()
    return String(s[byte=0:end])


def metric_canonical(
    res: PythonObject, kind: String, fx: PythonObject,
    builtins: PythonObject,
) raises -> String:
    """Rebuild the fixture's canonical(kind, result) form Mojo-side.

    Sort/group/join logic lives HERE; only float formatting (%.6f, which
    Mojo lacks) goes through the fixture's fmt_float / canon_scalar
    bridge functions — the integrity port's formatting discipline."""
    if kind == "name_list":
        # VoteRank: the row order IS the ranking — do NOT re-sort.
        var parts = List[String]()
        for i in range(res.__len__()):
            parts.append(py_str(res[i]))
        return join_list(parts, ">")
    if kind == "pair_list":
        # link-predict: ~20k candidate pairs — merge sort (both sides
        # sort the same "a|b|score" strings).
        var parts = List[String]()
        for i in range(res.__len__()):
            var row = res[i]
            parts.append(
                py_str(row[0]) + "|" + py_str(row[1]) + "|"
                + py_str(fx.fmt_float(row[2])))
        return join_list(merge_sort_strs(parts), ",")
    var items = builtins.list(res.items())
    var keys = List[String]()
    for i in range(items.__len__()):
        keys.append(py_str(items[i][0]))
    # sort KEYS (not "key:value" — ':' sorts after digits, so entry-sort
    # would diverge from python's sorted(d.items()) on prefix keys)
    var skeys = merge_sort_strs(keys)
    if kind == "float_dict":
        var parts = List[String]()
        for i in range(len(skeys)):
            parts.append(
                skeys[i] + ":" + py_str(fx.fmt_float(res[skeys[i]])))
        return join_list(parts, ",")
    if kind == "int_dict":
        var parts = List[String]()
        for i in range(len(skeys)):
            parts.append(skeys[i] + ":" + py_str(res[skeys[i]]))
        return join_list(parts, ",")
    if kind == "scalars":
        var parts = List[String]()
        for i in range(len(skeys)):
            parts.append(
                skeys[i] + ":" + py_str(fx.canon_scalar(res[skeys[i]])))
        return join_list(parts, ",")
    if kind == "partition":
        # wcc: component ids are arbitrary labels — canonicalise the
        # PARTITION (count + member sizes, descending).
        var cids = List[String]()
        var counts = List[Int]()
        for i in range(items.__len__()):
            var cid = py_str(items[i][1])
            var hit = -1
            for j in range(len(cids)):
                if cids[j] == cid:
                    hit = j
                    break
            if hit >= 0:
                counts[hit] += 1
            else:
                cids.append(cid)
                counts.append(1)
        for i in range(1, len(counts)):
            var key = counts[i]
            var j = i - 1
            while j >= 0 and counts[j] < key:
                counts[j + 1] = counts[j]
                j -= 1
            counts[j + 1] = key
        var parts = List[String]()
        for i in range(len(counts)):
            parts.append(String(counts[i]))
        return String(len(counts)) + ":" + join_list(parts, ",")
    raise Error("unknown canonical kind " + kind)


def main() raises:
    Python.evaluate("__import__('sys').path.insert(0, '')")
    Python.evaluate("__import__('sys').path.insert(0, 'Mojo/bench')")
    var fx = Python.import_module("mojo_graph_algos")
    var builtins = Python.import_module("builtins")
    var fails = 0
    var skips = 0
    var t_all = perf_counter_ns()

    # ------------------------------------------- 1. extension inventory
    print("== 1. extension inventory (canonical graph connection) ==")
    var con = fx.graph_con()
    var inv_sql = fx.SQL_CASES[0]["sql"]
    var inv = con.execute(inv_sql, []).fetchall()
    var onager_loaded = False
    for i in range(inv.__len__()):
        var row = inv[i]
        print("   ", row.__repr__())
        if py_str(row[0]) == "onager" and row[1].__bool__():
            onager_loaded = True
    if not onager_loaded:
        print("FAIL: onager extension not loaded on graph connection")
        fails += 1
    var tc = builtins.list(fx.table_counts().items())
    for i in range(tc.__len__()):
        print("   fts table ", py_str(tc[i][0]), ": ", py_str(tc[i][1]),
              " rows")

    # ------------------------------------------------- 2. SQL cases
    print("== 2. SQL cases (mojo-side execute, checksum parity) ==")
    var cases = fx.sql_cases()
    var nc = cases.__len__()
    var sql_pass = 0
    for ci in range(nc):
        var c = cases[ci]
        var name = py_str(c["name"])
        var group = py_str(c["group"])
        var conn = fx.conn_for(py_str(c["conn"]))
        var sql = c["sql"]
        var params = c["params"]
        var reps = to_i(c["reps"])
        var do_fetch = c["fetch"].__bool__()
        var nat = fx.native_sql(name)  # runs the native reps first
        var nat_rows = to_i(nat[0])
        var nat_ck = to_i(nat[1])
        var nat_dt = Float64(String(nat[2].__str__()))
        var t0 = perf_counter_ns()
        var nrows = 0
        var cks = 0
        for _ in range(reps):
            if do_fetch:
                var rows = conn.execute(sql, params).fetchall()
                nrows = rows.__len__()
                cks = 0
                for j in range(nrows):
                    cks += String(rows[j].__repr__()).byte_length()
            else:
                conn.execute(sql, params)
                nrows = 0
                cks = 0
        var t1 = perf_counter_ns()
        var dt = Float64(t1 - t0) / 1e9
        if nrows == nat_rows and cks == nat_ck:
            sql_pass += 1
            print("PASS ", group, "/", name, ": rows=", nrows, " checksum=",
                  cks, " mojo=", dt, "s native=", nat_dt, "s")
        else:
            fails += 1
            print("FAIL ", group, "/", name, ": mojo rows=", nrows,
                  " checksum=", cks, " vs native rows=", nat_rows,
                  " checksum=", nat_ck)
    print("sql cases: ", sql_pass, "/", nc, " checksum-parity passed")

    # --------------------------------------------- 3. metric functions
    print("== 3. metric functions (original modules driven from mojo) ==")
    var mcs = fx.metric_cases()
    var nm = mcs.__len__()
    var met_pass = 0
    var met_counted = 0
    for mi in range(nm):
        var name = py_str(mcs[mi][0])
        var kind = py_str(mcs[mi][1])
        var nat = fx.native_metric(name)  # native run + canonical first
        var nat_canon = py_str(nat[0])
        if nat_canon == "__SKIP__":
            skips += 1
            print("SKIP ", name, ": environment cannot serve this case")
            continue
        met_counted += 1
        var nat_dt = Float64(String(nat[1].__str__()))
        var t0 = perf_counter_ns()
        var res = fx.run_metric(name)
        var canon = metric_canonical(res, kind, fx, builtins)
        var t1 = perf_counter_ns()
        var dt = Float64(t1 - t0) / 1e9
        if canon == nat_canon:
            met_pass += 1
            print("PASS ", name, " (", kind, ") mojo=", dt, "s native=",
                  nat_dt, "s canon_bytes=", canon.byte_length())
        else:
            fails += 1
            print("FAIL ", name, " (", kind, "): mojo canon != native canon")
            print("   mojo  [:160]: ", head_bytes(canon, 160))
            print("   native[:160]: ", head_bytes(nat_canon, 160))
    print("metric cases: ", met_pass, "/", met_counted, " canonical-parity",
          " passed (", skips, " skipped)")

    # ------------------------------------------- 4. CLI end-to-end
    print("== 4. cli end-to-end (algorithms --all --no-apply) ==")
    var t0 = perf_counter_ns()
    var r = fx.cli_all()
    var t1 = perf_counter_ns()
    var rc = to_i(r["rc"])
    var out = r["out"]
    var err = r["err"]
    var n_metric_hdrs = to_i(out.count("(metric="))
    var n_link = to_i(out.count("link-predict (method="))
    var n_vote = to_i(out.count("voterank (seed set"))
    var n_fail = to_i(err.count("FAIL:"))
    print("rc=", rc, " metric headers=", n_metric_hdrs, " link-predict=",
          n_link, " voterank=", n_vote, " FAIL lines=", n_fail,
          " elapsed=", Float64(t1 - t0) / 1e9, "s")
    if rc != 0:
        print("FAIL: cli rc != 0")
        fails += 1
    if n_metric_hdrs != 12:
        print("FAIL: expected 12 (metric= headers")
        fails += 1
    if n_link < 1 or n_vote < 1:
        print("FAIL: link-predict/voterank section missing")
        fails += 1
    if n_fail != 0:
        print("FAIL: per-metric FAIL lines in stderr")
        fails += 1

    # ------------------------------------------------------ gate
    var t_end = perf_counter_ns()
    print("---")
    print("graph-algos probe: ", "fails=", fails, " skips=", skips,
          " | total wall=", Float64(t_end - t_all) / 1e9, "s")
    if fails > 0:
        print("GRAPH-ALGOS PARITY FAIL: ", fails, " failure(s)")
        sys_exit(1)
    print("GRAPH-ALGOS PARITY OK (sql + metrics + cli all match)")


def sys_exit(code: Int) raises:
    # sys.exit raises SystemExit, which the bridge surfaces as an
    # unhandled error — os._exit terminates cleanly (integrity pattern)
    if code != 0:
        Python.evaluate(
            "__import__('os')._exit(" + String(code) + ")")
