"""
 Mojo port of helpers/misc/database_integrity_check.py — FULL check
 surface (proposal doc/improvements/proposals/mojo_db_integrity_port.md,
 EXECUTED as completed.md #182). A TOOL, not a bench probe: source lives
 in Mojo/src/common/ so it lands on PATH alongside the other runnable
 programs; `MOJO_INTEGRITY_PARITY=1 Mojo/bin/integrity_check` adds the bench harness
 comparison (fixture Mojo/bench/mojo_db_integrity.py runs the ORIGINAL
 checker live as the parity oracle).

 POLICY (2026-08-29): native Mojo DB drivers are immature — data access
 goes through the PYTHON drivers via the bridge (sqlite3 via the repo's
 canonical helpers.core.db.connect, duckdb read-only); the CHECK LOGIC
 is Mojo. Bridge-first for facilities Mojo lacks (operator decision
 2026-08-30): str.lower / repr / round via builtins lambdas, first-party
 constants (CANONICAL_EVENT_TYPES, EXPECTED_USER_VERSION,
 EXPECTED_SCHEMA_VERSION, SUPER_SECTORS/SUB_CATEGORIES, EDGE_REGISTRY)
 imported through the bridge — single source of truth, nothing copied.

 Sections (mirroring the original's _CHECKS registry + inline header):
   entities + file-path validation   (filesystem + string rules, native)
   relations                          (14 SQL guards, mojo-side SQL)
   entity_tags events quotes company_metrics orphan_companies
   hierarchy (+ taxonomy drift via build_sector_hierarchy)
   normalization duplicate_tickers fuzzy_duplicates validity_window
   graph_summary market_cap_conflicts db_meta
   note_tags                          (native reads + VENDORED mojo-yaml)
   cache_consistency                  (duckdb vs sqlite reconcile)
 Run from the repo root. Exit semantics match the original main():
 validation_rate < 95 or any error-severity check nonzero -> exit 1;
 in parity mode any golden-parity mismatch also exits 1 — the bench
 leg goes red (2026-09-03, matching the graph-algos gating).
"""


from std.python import Python, PythonObject
from std.time import perf_counter_ns

from yaml import parse


# ---------------------------------------------------------------- helpers


def file_exists(path: String) raises -> Bool:
    try:
        var f = open(path, "r")
        f.close()
        return True
    except:
        return False


def contains(lst: List[String], s: String) -> Bool:
    for i in range(len(lst)):
        if lst[i] == s:
            return True
    return False


def sort_strs(lst: List[String]) -> List[String]:
    """Insertion sort (lists here are <= ~1.5k entries)."""
    var out = lst.copy()
    for i in range(1, len(out)):
        var key = out[i]
        var j = i - 1
        while j >= 0 and out[j] > key:
            out[j + 1] = out[j]
            j -= 1
        out[j + 1] = key
    return out^


def uniq_sorted(lst: List[String]) -> List[String]:
    var s = sort_strs(lst)
    var out = List[String]()
    for i in range(len(s)):
        if i == 0 or s[i] != s[i - 1]:
            out.append(s[i])
    return out^


def join_list(lst: List[String], sep: String) -> String:
    var out = String("")
    for i in range(len(lst)):
        if i > 0:
            out += sep
        out += lst[i]
    return out


def subset(a: List[String], b: List[String]) -> Bool:
    for i in range(len(a)):
        if not contains(b, a[i]):
            return False
    return True


def to_i(o: PythonObject) raises -> Int:
    return Int(String(o.__str__()))


def py_str(o: PythonObject) raises -> String:
    return String(o.__str__())


def norm_underscore(s: String) -> String:
    var out = String("")
    for i in range(len(s.codepoints())):
        var c = String(s[codepoint=i])
        if c == " ":
            out += "_"
        else:
            out += c
    return out


def dir_structure_ok(file_path: String, entity_type: String) -> Bool:
    """Faithful port of _check_directory_structure."""
    if entity_type == "sub_sector" or entity_type == "institution":
        return True
    var parts = file_path.split("/")
    var depth = len(parts)
    if file_path.startswith("findata/Companies/"):
        return depth == 4
    if file_path.startswith("findata/Sectors/"):
        return depth >= 3
    if file_path.startswith("findata/Super_Sectors/"):
        return depth == 3
    if entity_type == "edition" and (
        file_path.startswith("findata/The_Chatter/")
        or file_path.startswith("findata/The_PlotLines/")
        or file_path.startswith("findata/Points_And_Figures/")
    ):
        return depth == 3
    return False


def filename_ok(file_path: String) -> Bool:
    """Port of _check_filename_format: [A-Za-z0-9][A-Za-z0-9_]* stem."""
    var segs = file_path.split("/")
    var fname = String(segs[len(segs) - 1])
    if not fname.endswith(".md"):
        return False
    var stem_len = len(fname.codepoints()) - 3
    if stem_len <= 0:
        return False
    for i in range(stem_len):
        var c = fname[codepoint=i]
        var alnum = (
            (c >= "a" and c <= "z")
            or (c >= "A" and c <= "Z")
            or (c >= "0" and c <= "9")
        )
        if i == 0:
            if not alnum:
                return False
        elif not alnum and String(c) != "_":
            return False
    return True


def name_ok(nn: String) -> Bool:
    """Normalized_name format: [A-Za-z0-9][A-Za-z0-9_]* , no '__', no
    trailing '_' (port of the re.compile in check_normalization)."""
    if len(nn.codepoints()) == 0:
        return False
    var prev_us = False
    for i in range(len(nn.codepoints())):
        var c = nn[codepoint=i]
        var alnum = (
            (c >= "a" and c <= "z")
            or (c >= "A" and c <= "Z")
            or (c >= "0" and c <= "9")
        )
        if i == 0:
            if not alnum:
                return False
            prev_us = False
            continue
        if not alnum and String(c) != "_":
            return False
        if String(c) == "_":
            if prev_us:
                return False
            prev_us = True
        else:
            prev_us = False
    return not prev_us


def note_has_tag(fm: String, tag: String) raises -> Bool:
    """Vendored mojo-yaml: does the frontmatter `tags:` list carry tag?"""
    var doc = parse(fm)
    var seq = doc.get("tags").as_sequence()
    for j in range(len(seq)):
        if seq[j].as_string() == tag:
            return True
    return False


# tokenization for the fuzzy-name check (STOPWORDS/GENERIC mirror the
# original's module-level sets; kept as comma-joined constants)
alias STOPWORDS_S = "the,of,and,ltd,limited,private,pvt,india,industries,company,corporation,enterprise,group,holdings"
alias GENERIC_S = "life,insurance,financial,finance,bank,banking,power,gas,oil,energy,capital,markets,global,technologies,solutions,services,trading,indian,shree,bajaj,national,union,hospitality,housing,pharma,overseas,south,north,east,west"


def csv_contains(csv: String, t: String) -> Bool:
    var parts = csv.split(",")
    for i in range(len(parts)):
        if String(parts[i]) == t:
            return True
    return False


def is_stopword(t: String) -> Bool:
    return csv_contains(String(STOPWORDS_S), t)


def is_generic(t: String) -> Bool:
    return csv_contains(String(GENERIC_S), t)


def meaningful_tokens(
    name: String, lower_fn: PythonObject
) raises -> List[String]:
    """str.lower via the bridge (Mojo lacks it), tokenize in Mojo."""
    var low = String(lower_fn(name).__str__())
    var toks = List[String]()
    var cur = String("")
    for i in range(len(low.codepoints())):
        var cs = String(low[codepoint=i])
        var alnum = (cs >= "0" and cs <= "9") or (cs >= "a" and cs <= "z")
        if alnum:
            cur += cs
        else:
            if cur.byte_length() > 0 and not is_stopword(cur):
                toks.append(cur)
            cur = String("")
    if cur.byte_length() > 0 and not is_stopword(cur):
        toks.append(cur)
    return toks^


def fuzzy_match(ci: List[String], cj: List[String]) -> Bool:
    """Port of _fuzzy_names_match."""
    var shared = List[String]()
    for i in range(len(ci)):
        if contains(cj, ci[i]) and not contains(shared, ci[i]):
            shared.append(ci[i])
    if len(ci) == 1 and len(cj) == 1 and len(shared) == 1:
        return True
    # original: shared == tokens_i OR shared == tokens_j, i.e. one
    # side's FULL token set is contained in the other (shared always
    # ⊆ both sides, so the containment that matters is tokens ⊆ shared)
    if (
        len(ci) >= 2
        and len(shared) >= 2
        and (subset(ci, shared) or subset(cj, shared))
    ):
        for i in range(len(shared)):
            if not is_generic(shared[i]):
                return True
    return False


def main() raises:
    Python.evaluate("__import__('sys').path.insert(0, '')")
    Python.evaluate("__import__('sys').path.insert(0, 'Mojo/bench')")
    # NB: CLI args do not reach the bridge interpreter's sys.argv — use
    # an env var (MOJO_INTEGRITY_PARITY=1) to request parity mode
    var parity_mode = (
        py_str(
            Python.evaluate(
                "__import__('os').environ.get('MOJO_INTEGRITY_PARITY', '')"
            )
        )
        == "1"
    )

    # bridge facilities Mojo lacks (operator decision): lower/repr/round
    var lower_fn = Python.evaluate("lambda s: s.lower()")
    var repr_fn = Python.evaluate("lambda v: repr(v)")
    var round_fn = Python.evaluate("lambda x, n: round(x, n)")

    # canonical results: key -> canonical string (parity oracle shape)
    var canon = Dict[String, String]()
    # report lines
    var rep = List[String]()
    var t_all = perf_counter_ns()

    # ---------------------------------------------------------- connection
    var dbmod = Python.import_module("helpers.core.db")
    var conn = dbmod.connect("memory/research.db")
    # ------------------------------------------- 1. entities + file paths
    var t0 = perf_counter_ns()
    var rows = conn.execute(
        "SELECT name, entity_type, file_path, normalized_name "
        "FROM entities ORDER BY entity_type, name"
    ).fetchall()
    var n = rows.__len__()
    var valid = 0
    var invalid = 0
    var missing_paths = 0
    var not_found = 0
    var bad_structure = 0
    var bad_filename = 0
    var bt_total = Dict[String, Int]()
    var bt_valid = Dict[String, Int]()
    var bt_invalid = Dict[String, Int]()
    var bt_missing = Dict[String, Int]()
    var invalid_list = List[String]()
    for i in range(n):
        var row = rows[i]
        var etype = py_str(row[1])
        var fp = row[2]
        bt_total[etype] = bt_total.get(etype, 0) + 1
        if not fp.__bool__():
            if (
                etype == "sub_sector"
                or etype == "theme"
                or etype == "institution"
            ):
                valid += 1
                bt_valid[etype] = bt_valid.get(etype, 0) + 1
                continue
            missing_paths += 1
            invalid += 1
            bt_missing[etype] = bt_missing.get(etype, 0) + 1
            bt_invalid[etype] = bt_invalid.get(etype, 0) + 1
            invalid_list.append(
                py_str(row[0]) + "|" + etype + "|Missing file path"
            )
            continue
        var file_path = py_str(fp)
        var ok = file_exists(file_path)
        if not ok:
            not_found += 1
        if ok:
            ok = file_path.endswith(".md")
        if ok:
            ok = dir_structure_ok(file_path, etype)
            if not ok:
                bad_structure += 1
        if ok and etype != "edition":
            ok = filename_ok(file_path)
            if not ok:
                bad_filename += 1
        if ok:
            valid += 1
            bt_valid[etype] = bt_valid.get(etype, 0) + 1
        else:
            invalid += 1
            bt_invalid[etype] = bt_invalid.get(etype, 0) + 1
            invalid_list.append(py_str(row[0]) + "|" + etype + "|Other")
    var t1 = perf_counter_ns()
    canon["hdr.total_entities"] = String(n)
    canon["hdr.valid_entities"] = String(valid)
    canon["hdr.invalid_entities"] = String(invalid)
    canon["hdr.missing_file_paths"] = String(missing_paths)
    canon["hdr.file_not_found"] = String(not_found)
    canon["hdr.invalid_structure"] = String(bad_structure)
    canon["hdr.invalid_filename"] = String(bad_filename)
    canon["hdr.invalid_list_count"] = String(len(invalid_list))
    var bt_keys = List[String]()
    for k in bt_total.keys():
        bt_keys.append(k)
    var bt_canon = List[String]()
    for k in sort_strs(bt_keys):
        bt_canon.append(
            k
            + ":"
            + String(bt_total[k])
            + ","
            + String(bt_valid.get(k, 0))
            + ","
            + String(bt_invalid.get(k, 0))
            + ","
            + String(bt_missing.get(k, 0))
        )
    canon["hdr.by_type"] = join_list(bt_canon, ",")
    var vr = String(
        round_fn((Float64(valid) / Float64(n)) * 100.0, 2).__str__()
    )
    var cr = String(
        round_fn(
            ((Float64(n) - Float64(missing_paths)) / Float64(n)) * 100.0, 2
        ).__str__()
    )
    canon["hdr.summary.validation_rate"] = vr
    canon["hdr.summary.coverage_rate"] = cr
    canon["hdr.summary.missing_path_rate"] = String(
        round_fn((Float64(missing_paths) / Float64(n)) * 100.0, 2).__str__()
    )
    canon["hdr.summary.file_not_found_rate"] = String(
        round_fn((Float64(not_found) / Float64(n)) * 100.0, 2).__str__()
    )
    canon["hdr.summary.structure_issues_rate"] = String(
        round_fn((Float64(bad_structure) / Float64(n)) * 100.0, 2).__str__()
    )
    canon["hdr.summary.filename_issues_rate"] = String(
        round_fn((Float64(bad_filename) / Float64(n)) * 100.0, 2).__str__()
    )
    print(
        "entities+paths : n=",
        n,
        " valid=",
        valid,
        " invalid=",
        invalid,
        " missing=",
        missing_paths,
        " elapsed=",
        Float64(t1 - t0) / 1e6,
        "ms",
    )
    rep.append(
        "Entities: "
        + String(n)
        + " (valid "
        + String(valid)
        + ", invalid "
        + String(invalid)
        + ") | validation_rate "
        + vr
        + "%"
    )

    # ------------------------------------------------------ 2. relations
    t0 = perf_counter_ns()
    var KNOWN = String(
        "'part_of','has_company','competes_with','jv_with','same_group',"
        "'supplier_to','customer_of','acquired','subsidiary_of',"
        "'co_mentioned_in','belongs_to','exposed_to','cited_in',"
        "'semantic_peer','invested_in'"
    )
    var rel_total = to_i(
        conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    )
    var rel_unknown = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations WHERE relation_type NOT IN ("
            + KNOWN
            + ")"
        ).fetchone()[0]
    )
    var rel_loops = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations WHERE source = target"
        ).fetchone()[0]
    )
    var rel_orphan = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations r WHERE r.source NOT IN "
            "(SELECT name FROM entities) OR r.target NOT IN "
            "(SELECT name FROM entities)"
        ).fetchone()[0]
    )
    var po_src_bad = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON"
            " r.source=e.name WHERE r.relation_type='part_of' AND"
            " e.entity_type!='company'"
        ).fetchone()[0]
    )
    var po_tgt_bad = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON"
            " r.target=e.name WHERE r.relation_type='part_of' AND"
            " e.entity_type!='sector'"
        ).fetchone()[0]
    )
    var hc_src_bad = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON"
            " r.source=e.name WHERE r.relation_type='has_company' AND"
            " e.entity_type!='sector'"
        ).fetchone()[0]
    )
    var hc_tgt_bad = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON"
            " r.target=e.name WHERE r.relation_type='has_company' AND"
            " e.entity_type!='company'"
        ).fetchone()[0]
    )
    var po_no_hc = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations p WHERE p.relation_type='part_of' "
            "AND NOT EXISTS (SELECT 1 FROM relations h WHERE h.source=p.target "
            "AND h.target=p.source AND h.relation_type='has_company')"
        ).fetchone()[0]
    )
    var hc_no_po = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations h WHERE"
            " h.relation_type='has_company' AND NOT EXISTS (SELECT 1 FROM"
            " relations p WHERE p.source=h.target AND p.target=h.source AND"
            " p.relation_type='part_of')"
        ).fetchone()[0]
    )
    var rel_circ = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations r1 WHERE r1.source < r1.target "
            "AND EXISTS (SELECT 1 FROM relations r2 WHERE r2.source=r1.target "
            "AND r2.target=r1.source AND r2.relation_type=r1.relation_type)"
        ).fetchone()[0]
    )
    var bt_src_bad = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON"
            " r.source=e.name WHERE r.relation_type='belongs_to' AND"
            " e.entity_type NOT IN ('sector','sub_sector')"
        ).fetchone()[0]
    )
    var bt_tgt_bad = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON"
            " r.target=e.name WHERE r.relation_type='belongs_to' AND"
            " e.entity_type NOT IN ('super_sector','sector')"
        ).fetchone()[0]
    )
    var et_src_bad = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON"
            " r.source=e.name WHERE r.relation_type='exposed_to' AND"
            " e.entity_type!='company'"
        ).fetchone()[0]
    )
    var et_tgt_bad = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON"
            " r.target=e.name WHERE r.relation_type='exposed_to' AND"
            " e.entity_type!='theme'"
        ).fetchone()[0]
    )
    var rel_tm = (
        po_src_bad
        + po_tgt_bad
        + hc_src_bad
        + hc_tgt_bad
        + bt_src_bad
        + bt_tgt_bad
        + et_src_bad
        + et_tgt_bad
    )
    var rel_err = (
        rel_unknown
        + rel_loops
        + rel_orphan
        + rel_tm
        + po_no_hc
        + hc_no_po
        + rel_circ
    )
    t1 = perf_counter_ns()
    canon["relations.total"] = String(rel_total)
    canon["relations.unknown_type"] = String(rel_unknown)
    canon["relations.self_loops"] = String(rel_loops)
    canon["relations.orphaned"] = String(rel_orphan)
    canon["relations.type_mismatch"] = String(rel_tm)
    canon["relations.part_of_without_has_company"] = String(po_no_hc)
    canon["relations.has_company_without_part_of"] = String(hc_no_po)
    canon["relations.belongs_to_endpoint_bad"] = String(bt_src_bad + bt_tgt_bad)
    canon["relations.exposed_to_endpoint_bad"] = String(et_src_bad + et_tgt_bad)
    canon["relations.circular"] = String(rel_circ)
    canon["relations.errors"] = String(rel_err)
    print(
        "relations      : total=",
        rel_total,
        " errors=",
        rel_err,
        " elapsed=",
        Float64(t1 - t0) / 1e6,
        "ms",
    )
    rep.append(
        "Relations: total=" + String(rel_total) + " errors=" + String(rel_err)
    )

    # ------------------------------------- 3. count-only table checks
    t0 = perf_counter_ns()
    var et_total = to_i(
        conn.execute("SELECT COUNT(*) FROM entity_tags").fetchone()[0]
    )
    var et_orphan = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM entity_tags WHERE entity_name NOT IN "
            "(SELECT name FROM entities)"
        ).fetchone()[0]
    )
    canon["entity_tags.total"] = String(et_total)
    canon["entity_tags.orphaned"] = String(et_orphan)
    canon["entity_tags.errors"] = String(et_orphan)

    # CANONICAL_EVENT_TYPES is a frozenset — sorted list via the bridge
    # (the original builds the same sorted IN-list)
    var ev_types = List[String]()
    var ev_tl = Python.evaluate(
        "sorted(list(__import__('importlib').import_module("
        "'helpers.validators.static_checks').CANONICAL_EVENT_TYPES))"
    )
    for i in range(ev_tl.__len__()):
        ev_types.append(py_str(ev_tl[i]))
    var ev_list = String("'") + join_list(sort_strs(ev_types), "','") + "'"
    var ev_total = to_i(
        conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    )
    var ev_unknown = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type NOT IN ("
            + ev_list
            + ")"
        ).fetchone()[0]
    )
    var ev_orphan = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM events WHERE entity NOT IN "
            "(SELECT name FROM entities)"
        ).fetchone()[0]
    )
    var ev_badp = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM events WHERE json_valid(properties) = 0"
        ).fetchone()[0]
    )
    canon["events.total"] = String(ev_total)
    canon["events.unknown_type"] = String(ev_unknown)
    canon["events.orphaned"] = String(ev_orphan)
    canon["events.bad_properties"] = String(ev_badp)
    canon["events.errors"] = String(ev_unknown + ev_orphan + ev_badp)

    var count_tables = List[String]()
    count_tables.append("quotes")
    count_tables.append("company_metrics")
    for ci in range(len(count_tables)):
        var tbl = count_tables[ci]
        var q_total = to_i(
            conn.execute("SELECT COUNT(*) FROM " + tbl).fetchone()[0]
        )
        var q_orphan = to_i(
            conn.execute(
                "SELECT COUNT(*) FROM "
                + tbl
                + " WHERE entity NOT IN (SELECT name FROM entities)"
            ).fetchone()[0]
        )
        var q_badp = to_i(
            conn.execute(
                "SELECT COUNT(*) FROM "
                + tbl
                + " WHERE json_valid(properties) = 0"
            ).fetchone()[0]
        )
        canon[tbl + ".total"] = String(q_total)
        canon[tbl + ".orphaned"] = String(q_orphan)
        canon[tbl + ".bad_properties"] = String(q_badp)
        canon[tbl + ".errors"] = String(q_orphan + q_badp)

    var oc_row = conn.execute(
        "SELECT (SELECT COUNT(*) FROM entities WHERE entity_type='company'), "
        "(SELECT COUNT(*) FROM entities e WHERE e.entity_type='company' "
        "AND NOT EXISTS (SELECT 1 FROM relations r WHERE "
        "r.relation_type='part_of' AND r.source=e.name))"
    ).fetchone()
    var oc_total = to_i(oc_row[0])
    var oc_orphan = to_i(oc_row[1])
    canon["orphan_companies.total_companies"] = String(oc_total)
    canon["orphan_companies.orphan_companies"] = String(oc_orphan)
    canon["orphan_companies.errors"] = String(oc_orphan)
    t1 = perf_counter_ns()
    print(
        "count-checks   : entity_tags/events/quotes/metrics/orphans elapsed=",
        Float64(t1 - t0) / 1e6,
        "ms",
    )

    # ---------------------------------------------------- 4. hierarchy
    t0 = perf_counter_ns()
    var h_total = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM relations WHERE relation_type='belongs_to'"
        ).fetchone()[0]
    )
    var h_sub = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM entities e WHERE e.entity_type='sub_sector' "
            "AND NOT EXISTS (SELECT 1 FROM relations r WHERE "
            "r.relation_type='belongs_to' AND r.source=e.name)"
        ).fetchone()[0]
    )
    var h_sec = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM entities e WHERE e.entity_type='sector' "
            "AND NOT EXISTS (SELECT 1 FROM relations r JOIN entities t ON "
            "t.name=r.target AND t.entity_type='super_sector' WHERE "
            "r.relation_type='belongs_to' AND r.source=e.name)"
        ).fetchone()[0]
    )
    var h_ss = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM entities e WHERE e.entity_type='super_sector'"
            " AND NOT EXISTS (SELECT 1 FROM relations r WHERE"
            " r.relation_type='belongs_to' AND r.target=e.name)"
        ).fetchone()[0]
    )
    var h_multi = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM (SELECT r.source FROM relations r WHERE "
            "r.relation_type='belongs_to' GROUP BY r.source HAVING "
            "COUNT(DISTINCT r.target) > 1)"
        ).fetchone()[0]
    )
    var h_cycles = to_i(
        conn.execute(
            "WITH RECURSIVE walk(src, cur, depth) AS ("
            "SELECT r.source, r.target, 1 FROM relations r WHERE "
            "r.relation_type='belongs_to' UNION ALL SELECT w.src, r.target, "
            "w.depth+1 FROM walk w JOIN relations r ON r.source=w.cur WHERE "
            "r.relation_type='belongs_to' AND w.depth < 10) "
            "SELECT COUNT(*) FROM walk WHERE walk.src = walk.cur"
        ).fetchone()[0]
    )
    # taxonomy drift: expected set from the curated source-of-truth
    var bsh = Python.import_module("helpers.maintenance.build_sector_hierarchy")
    var expected = List[String]()
    var items = Python.evaluate(
        "list(__import__('importlib').import_module("
        "'helpers.maintenance.build_sector_hierarchy')"
        ".SUPER_SECTORS.items()) + list(__import__('importlib')"
        ".import_module('helpers.maintenance.build_sector_hierarchy')"
        ".SUB_CATEGORIES.items())"
    )
    for i in range(items.__len__()):
        var parent = py_str(items[i][0])
        var members = items[i][1]
        for j in range(members.__len__()):
            expected.append(
                norm_underscore(py_str(members[j]))
                + ">"
                + norm_underscore(parent)
            )
    var live_rows = conn.execute(
        "SELECT source, target FROM relations WHERE relation_type='belongs_to'"
    ).fetchall()
    var live = List[String]()
    for i in range(live_rows.__len__()):
        live.append(
            norm_underscore(py_str(live_rows[i][0]))
            + ">"
            + norm_underscore(py_str(live_rows[i][1]))
        )
    var exp_u = uniq_sorted(expected)
    var live_u = uniq_sorted(live)
    var drift = 0
    for i in range(len(exp_u)):
        if not contains(live_u, exp_u[i]):
            drift += 1
    for i in range(len(live_u)):
        if not contains(exp_u, live_u[i]):
            drift += 1
    var h_err = h_sub + h_sec + h_ss + h_multi + h_cycles + drift
    t1 = perf_counter_ns()
    canon["hierarchy.total_belongs_to"] = String(h_total)
    canon["hierarchy.sub_sector_orphans"] = String(h_sub)
    canon["hierarchy.sector_orphans"] = String(h_sec)
    canon["hierarchy.super_sector_orphans"] = String(h_ss)
    canon["hierarchy.multi_parent"] = String(h_multi)
    canon["hierarchy.cycles"] = String(h_cycles)
    canon["hierarchy.taxonomy_drift"] = String(drift)
    canon["hierarchy.errors"] = String(h_err)
    print(
        "hierarchy      : belongs_to=",
        h_total,
        " drift=",
        drift,
        " errors=",
        h_err,
        " elapsed=",
        Float64(t1 - t0) / 1e6,
        "ms",
    )
    rep.append(
        "Hierarchy: total_belongs_to="
        + String(h_total)
        + " taxonomy_drift="
        + String(drift)
        + " errors="
        + String(h_err)
    )

    # ------------------------------------------------ 5. normalization
    t0 = perf_counter_ns()
    var nm_missing = to_i(
        conn.execute(
            "SELECT COUNT(*) FROM entities WHERE normalized_name IS NULL "
            "OR normalized_name = ''"
        ).fetchone()[0]
    )
    var dup_rows = conn.execute(
        "SELECT normalized_name, COUNT(*) c FROM entities GROUP BY "
        "normalized_name HAVING c > 1"
    ).fetchall()
    var dup_canon = List[String]()
    for i in range(dup_rows.__len__()):
        dup_canon.append(py_str(dup_rows[i][0]) + ":" + py_str(dup_rows[i][1]))
    var norm_rows = conn.execute(
        "SELECT name, normalized_name, file_path, entity_type FROM entities"
    ).fetchall()
    var bad_fmt = List[String]()
    var file_mm = List[String]()
    var nn_set = List[String]()
    for i in range(norm_rows.__len__()):
        var row = norm_rows[i]
        var name = py_str(row[0])
        var nn = String("")
        if row[1].__bool__():
            nn = py_str(row[1])
        var etype = py_str(row[3])
        if nn.byte_length() > 0:
            nn_set.append(nn)
        if nn.byte_length() > 0 and etype != "edition" and not name_ok(nn):
            bad_fmt.append(name + ":" + nn)
        var fp = row[2]
        if nn.byte_length() > 0 and fp.__bool__():
            var fps = py_str(fp)
            var segs = fps.split("/")
            var fname = String(segs[len(segs) - 1])
            var stem = String("")
            if fname.endswith(".md"):
                var fl = len(fname.codepoints()) - 3
                for bi in range(fl):
                    stem += String(fname[codepoint=bi])
            if stem.byte_length() > 0 and nn != stem:
                file_mm.append(name + ":" + nn + ":" + stem)
    # orphaned files: pathlib.rglob via the bridge (Mojo has no glob)
    var orphaned = List[String]()
    var vaults = Python.evaluate(
        "[p for p in ('findata/Companies','findata/Sectors') "
        "if __import__('pathlib').Path(p).exists()]"
    )
    for vi in range(vaults.__len__()):
        var vault = py_str(vaults[vi])
        var md_list = Python.evaluate(
            "list("
            + "__import__('pathlib').Path('"
            + vault
            + "').rglob('*.md'))"
        )
        for mi in range(md_list.__len__()):
            var p = py_str(md_list[mi])
            var segs = p.split("/")
            var fname = String(segs[len(segs) - 1])
            var stem = String("")
            if fname.endswith(".md"):
                var fl = len(fname.codepoints()) - 3
                for bi in range(fl):
                    stem += String(fname[codepoint=bi])
            if not contains(nn_set, stem):
                orphaned.append(p)
    var nm_errors = nm_missing + len(dup_canon) + len(bad_fmt)
    var nm_warn = len(file_mm) + len(orphaned)
    t1 = perf_counter_ns()
    canon["normalization.missing"] = String(nm_missing)
    canon["normalization.duplicates"] = join_list(sort_strs(dup_canon), ",")
    canon["normalization.bad_format"] = join_list(sort_strs(bad_fmt), ",")
    canon["normalization.errors"] = String(nm_errors)
    canon["normalization.file_mismatches"] = join_list(sort_strs(file_mm), ",")
    canon["normalization.orphaned_files"] = join_list(sort_strs(orphaned), ",")
    canon["normalization.warnings"] = String(nm_warn)
    print(
        "normalization  : missing=",
        nm_missing,
        " errors=",
        nm_errors,
        " warnings=",
        nm_warn,
        " elapsed=",
        Float64(t1 - t0) / 1e6,
        "ms",
    )
    rep.append(
        "Normalization: errors="
        + String(nm_errors)
        + " warnings="
        + String(nm_warn)
    )

    # ------------------------------------- 6. duplicate + fuzzy names
    t0 = perf_counter_ns()
    var tick_rows = conn.execute(
        "SELECT ticker, name FROM entities WHERE entity_type = 'company' "
        "AND ticker IS NOT NULL AND ticker <> '' ORDER BY ticker, name"
    ).fetchall()
    var groups = Dict[String, String]()
    for i in range(tick_rows.__len__()):
        var tk = py_str(tick_rows[i][0])
        var nm = py_str(tick_rows[i][1])
        var cur = groups.get(tk, String(""))
        if cur.byte_length() > 0:
            groups[tk] = cur + ";" + nm
        else:
            groups[tk] = nm
    var dup_groups = List[String]()
    var gkeys = List[String]()
    for k in groups.keys():
        gkeys.append(k)
    for i in range(len(gkeys)):
        var tk = gkeys[i]
        var members = groups[tk].split(";")
        if len(members) > 1:
            var names_l = List[String]()
            for j in range(len(members)):
                names_l.append(String(members[j]))
            dup_groups.append(tk + ":" + join_list(names_l, ";"))
    canon["duplicate_tickers.groups"] = join_list(sort_strs(dup_groups), ",")
    canon["duplicate_tickers.errors"] = String(len(dup_groups))

    # fuzzy pairs — tokenization + inverted index + pair rules in Mojo
    var comp_rows = conn.execute(
        "SELECT name, ticker FROM entities WHERE entity_type = 'company'"
    ).fetchall()
    var nc = comp_rows.__len__()
    var names = List[String]()
    var tickers = List[String]()
    var tokens_s = Dict[String, String]()  # i -> comma-joined tokens
    for i in range(nc):
        names.append(py_str(comp_rows[i][0]))
        var tk = String("")
        if comp_rows[i][1].__bool__():
            tk = py_str(comp_rows[i][1])
        tickers.append(tk)
        tokens_s[String(i)] = join_list(
            meaningful_tokens(names[i], lower_fn), ","
        )
    # inverted index: token -> comma-joined candidate row indices
    var inv = Dict[String, String]()
    for i in range(nc):
        var row_toks = tokens_s[String(i)].split(",")
        for ti in range(len(row_toks)):
            var key = String(row_toks[ti])
            var cur = inv.get(key, String(""))
            if cur.byte_length() > 0:
                inv[key] = cur + "," + String(i)
            else:
                inv[key] = String(i)
    # NB: keys are sorted within the pair (pk is built from sort_strs)
    var SUPPRESSED = List[String]()
    SUPPRESSED.append("3M Company|3M India")
    SUPPRESSED.append("Carraro Group|Carraro India")
    SUPPRESSED.append("Colgate Palmolive India|Colgate-Palmolive Company")
    SUPPRESSED.append("Hyundai Motor Company|Hyundai Motor India")
    SUPPRESSED.append("JTEKT|JTEKT India")
    SUPPRESSED.append("PTC India|PTC Industries")
    SUPPRESSED.append("Sanofi|Sanofi India")
    SUPPRESSED.append("Shree Cement|Shree Digvijay Cement")
    var pairs = List[String]()
    for i in range(nc):
        var ci_parts = tokens_s[String(i)].split(",")
        var ci = List[String]()
        for ti in range(len(ci_parts)):
            if String(ci_parts[ti]).byte_length() > 0:
                ci.append(String(ci_parts[ti]))
        if len(ci) == 0:
            continue
        var cands = List[Int]()
        for ti in range(len(ci)):
            var members = inv[ci[ti]].split(",")
            for li in range(len(members)):
                var j = Int(String(members[li]))
                if j <= i:
                    continue
                var dup = False
                for cj_i in range(len(cands)):
                    if cands[cj_i] == j:
                        dup = True
                        break
                if not dup:
                    cands.append(j)
        for ci_j in range(len(cands)):
            var j = cands[ci_j]
            var cj_parts = tokens_s[String(j)].split(",")
            var cj = List[String]()
            for tj in range(len(cj_parts)):
                if String(cj_parts[tj]).byte_length() > 0:
                    cj.append(String(cj_parts[tj]))
            var tk_i = tickers[i]
            var tk_j = tickers[j]
            var dominated = False
            if tk_i.byte_length() > 0 and tk_i == tk_j:
                dominated = True
            if len(cj) == 0:
                dominated = True
            if not dominated and not fuzzy_match(ci, cj):
                dominated = True
            if dominated:
                continue
            var pair = List[String]()
            pair.append(names[i])
            pair.append(names[j])
            var pk = join_list(sort_strs(pair), "|")
            if contains(SUPPRESSED, pk):
                continue
            if not contains(pairs, pk):
                pairs.append(pk)
    t1 = perf_counter_ns()
    canon["fuzzy_duplicates.pairs"] = join_list(sort_strs(pairs), ",")
    canon["fuzzy_duplicates.warnings"] = String(len(pairs))
    canon["fuzzy_duplicates.errors"] = String(0)
    print(
        "names          : dup_groups=",
        len(dup_groups),
        " fuzzy_pairs=",
        len(pairs),
        " elapsed=",
        Float64(t1 - t0) / 1e6,
        "ms",
    )
    rep.append(
        "Duplicate tickers: "
        + String(len(dup_groups))
        + " groups | fuzzy pairs: "
        + String(len(pairs))
    )

    # -------------------------------------------- 7. validity window
    t0 = perf_counter_ns()
    var vw_rows = conn.execute(
        "SELECT edge_type, COUNT(), SUM(CASE WHEN valid_from IS NOT NULL "
        "AND valid_from <> '' THEN 1 ELSE 0 END), SUM(CASE WHEN valid_to "
        "IS NOT NULL AND valid_to <> '' THEN 1 ELSE 0 END) FROM graph_edges "
        "GROUP BY edge_type ORDER BY edge_type"
    ).fetchall()
    var vw_canon = List[String]()
    var vw_warn = 0
    for i in range(vw_rows.__len__()):
        var et = py_str(vw_rows[i][0])
        var tot = to_i(vw_rows[i][1])
        var wf = to_i(vw_rows[i][2])
        var wt = to_i(vw_rows[i][3])
        vw_canon.append(
            et
            + ":"
            + String(tot)
            + ","
            + String(wf)
            + ","
            + String(tot - wf)
            + ","
            + String(wt)
        )
        if et == "acquired" or et == "subsidiary_of":
            vw_warn += tot - wf
    t1 = perf_counter_ns()
    canon["validity_window.by_type"] = join_list(sort_strs(vw_canon), ",")
    canon["validity_window.warnings"] = String(vw_warn)
    print(
        "validity_window: warnings=",
        vw_warn,
        " elapsed=",
        Float64(t1 - t0) / 1e6,
        "ms",
    )

    # -------------------------------------------- 8. graph summary
    t0 = perf_counter_ns()
    var ec_rows = conn.execute(
        "SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type "
        "ORDER BY 2 DESC"
    ).fetchall()
    var ec_canon = List[String]()
    for i in range(ec_rows.__len__()):
        ec_canon.append(py_str(ec_rows[i][0]) + ":" + py_str(ec_rows[i][1]))
    var edge_rows = conn.execute(
        "SELECT edge_type, COUNT(*) AS n FROM graph_edges GROUP BY "
        "edge_type ORDER BY n DESC"
    ).fetchall()
    var ed_canon = List[String]()
    for i in range(edge_rows.__len__()):
        ed_canon.append(py_str(edge_rows[i][0]) + ":" + py_str(edge_rows[i][1]))
    var sz_rows = conn.execute(
        "SELECT sector_classification, COUNT(*) AS n FROM entities WHERE "
        "entity_type='company' AND sector_classification IS NOT NULL "
        "GROUP BY sector_classification ORDER BY n DESC"
    ).fetchall()
    var sizes = List[Int]()
    var sz_pairs = List[String]()
    for i in range(sz_rows.__len__()):
        sizes.append(to_i(sz_rows[i][1]))
        sz_pairs.append(py_str(sz_rows[i][0]) + ":" + py_str(sz_rows[i][1]))
    var sz_count = len(sizes)
    var sz_min = 0
    var sz_med = 0
    var sz_max = 0
    var mean_s = String("0")
    if sz_count > 0:
        sz_min = sizes[0]
        sz_max = sizes[0]
        var total = 0
        for i in range(sz_count):
            if sizes[i] < sz_min:
                sz_min = sizes[i]
            if sizes[i] > sz_max:
                sz_max = sizes[i]
            total += sizes[i]
        var sorted_sizes = sizes.copy()
        for i in range(1, sz_count):
            var key = sorted_sizes[i]
            var j2 = i - 1
            while j2 >= 0 and sorted_sizes[j2] > key:
                sorted_sizes[j2 + 1] = sorted_sizes[j2]
                j2 -= 1
            sorted_sizes[j2 + 1] = key
        sz_med = sorted_sizes[sz_count // 2]
        mean_s = String(
            round_fn(Float64(total) / Float64(sz_count), 1).__str__()
        )
    var largest = List[String]()
    var lim = sz_count if sz_count < 10 else 10
    for i in range(lim):
        largest.append(sz_pairs[i])
    var smallest = List[String]()
    var start = sz_count - 5 if sz_count >= 5 else 0
    for i in range(start, sz_count):
        smallest.append(sz_pairs[i])
    var cap_rows = conn.execute(
        "SELECT substr(t.tag, length('market_cap/')+1) AS cap, COUNT(*) AS n "
        "FROM entities e LEFT JOIN entity_tags t ON t.entity_name = e.name "
        "AND t.tag LIKE 'market_cap/%' WHERE e.entity_type='company' "
        "GROUP BY cap ORDER BY n DESC"
    ).fetchall()
    var cap_canon = List[String]()
    for i in range(cap_rows.__len__()):
        var cap = String("(unset)")
        if cap_rows[i][0].__bool__():
            cap = py_str(cap_rows[i][0])
        cap_canon.append(cap + ":" + py_str(cap_rows[i][1]))
    t1 = perf_counter_ns()
    canon["graph_summary.entity_counts"] = join_list(sort_strs(ec_canon), ",")
    canon["graph_summary.edge_counts"] = join_list(sort_strs(ed_canon), ",")
    canon["graph_summary.sector_size"] = (
        String(sz_count)
        + ":"
        + String(sz_min)
        + ":"
        + String(sz_med)
        + ":"
        + String(sz_max)
        + ":"
        + mean_s
    )
    canon["graph_summary.largest"] = join_list(largest, ",")
    canon["graph_summary.smallest"] = join_list(smallest, ",")
    canon["graph_summary.market_cap"] = join_list(cap_canon, ",")
    print(
        "graph_summary  : types=",
        len(ec_canon),
        " edges=",
        len(ed_canon),
        " elapsed=",
        Float64(t1 - t0) / 1e6,
        "ms",
    )

    # ------------------------------------- 9. market-cap conflicts
    t0 = perf_counter_ns()
    var mc_rows = conn.execute(
        "SELECT entity_name, GROUP_CONCAT(tag, ',') FROM entity_tags "
        "WHERE tag LIKE 'market_cap/%' GROUP BY entity_name HAVING "
        "COUNT(*) > 1 ORDER BY entity_name"
    ).fetchall()
    var mc_canon = List[String]()
    for i in range(mc_rows.__len__()):
        var tag_parts = py_str(mc_rows[i][1]).split(",")
        var tags = List[String]()
        for ti in range(len(tag_parts)):
            tags.append(String(tag_parts[ti]))
        mc_canon.append(py_str(mc_rows[i][0]) + "=" + join_list(tags, "+"))
    t1 = perf_counter_ns()
    canon["market_cap_conflicts.conflicts"] = join_list(
        sort_strs(mc_canon), ","
    )
    canon["market_cap_conflicts.errors"] = String(len(mc_canon))
    print(
        "mcap_conflicts : conflicts=",
        len(mc_canon),
        " elapsed=",
        Float64(t1 - t0) / 1e6,
        "ms",
    )

    # ----------------------------------------------------- 10. db_meta
    t0 = perf_counter_ns()
    var dbm_err = 0
    var dbm_generation = 0
    var dbm_reasons = List[String]()
    var has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='db_meta'"
    ).fetchone()
    if not has_meta.__bool__():
        dbm_err += 1
        dbm_reasons.append(
            "db_meta table missing (run ensure_db_meta migration)"
        )
    else:
        var gen_row = conn.execute(
            "SELECT value FROM db_meta WHERE key='generation'"
        ).fetchone()
        if not gen_row.__bool__():
            dbm_err += 1
            dbm_reasons.append("db_meta.generation missing")
        else:
            try:
                dbm_generation = to_i(gen_row[0])
            except:
                dbm_err += 1
                dbm_reasons.append(
                    "generation not an int: "
                    + String(repr_fn(gen_row[0]).__str__())
                )
        var uv = to_i(conn.execute("PRAGMA user_version").fetchone()[0])
        var exp_uv = to_i(dbmod.EXPECTED_USER_VERSION)
        if uv != exp_uv:
            dbm_err += 1
            dbm_reasons.append(
                "user_version " + String(uv) + " != expected " + String(exp_uv)
            )
        var sv_row = conn.execute(
            "SELECT value FROM db_meta WHERE key='schema_version'"
        ).fetchone()
        var exp_sv = py_str(dbmod.EXPECTED_SCHEMA_VERSION)
        var sv_val = String("")
        if sv_row.__bool__():
            sv_val = py_str(sv_row[0])
        if not sv_row.__bool__() or sv_val != exp_sv:
            dbm_err += 1
            var sv_repr = String("None")
            if sv_row.__bool__():
                sv_repr = String(repr_fn(sv_row[0]).__str__())
            dbm_reasons.append(
                "db_meta.schema_version "
                + sv_repr
                + " != "
                + String(repr_fn(exp_sv).__str__())
            )
    t1 = perf_counter_ns()
    canon["db_meta.errors"] = String(dbm_err)
    canon["db_meta.warnings"] = String(0)
    canon["db_meta.generation"] = String(dbm_generation)
    canon["db_meta.reasons"] = join_list(sort_strs(dbm_reasons), ";")
    print(
        "db_meta        : errors=",
        dbm_err,
        " elapsed=",
        Float64(t1 - t0) / 1e6,
        "ms",
    )

    # --------------------------------------------------- 11. note_tags
    t0 = perf_counter_ns()
    var trows = conn.execute("SELECT note_path, tag FROM note_tags").fetchall()
    var tn = trows.__len__()
    var stale = 0
    for i in range(tn):
        var note_path = py_str(trows[i][0])
        var tag = py_str(trows[i][1])
        var text = String("")
        try:
            var f = open(note_path, "r")
            text = f.read()
            f.close()
        except:
            stale += 1
            continue
        var parts = text.split("---")
        if len(parts) < 3 or not note_has_tag(String(parts[1]), tag):
            stale += 1
    t1 = perf_counter_ns()
    canon["note_tags.total"] = String(tn)
    canon["note_tags.stale"] = String(stale)
    canon["note_tags.errors"] = String(stale)
    print(
        "note_tags      : rows=",
        tn,
        " stale=",
        stale,
        " elapsed=",
        Float64(t1 - t0) / 1e6,
        "ms",
    )

    # ------------------------------------------ 12. cache consistency
    t0 = perf_counter_ns()
    var cc_skipped = 0
    var cc_reason = String("")
    var cc_sv = String("<unset>")
    var cc_expected = String("<unset>")
    var cc_drift = 0
    var cc_mm = List[String]()
    var cc_err = 0
    if not file_exists("memory/graph.duckdb"):
        cc_skipped = 1
        cc_reason = String("cache file absent")
    else:
        var dcon = Python.evaluate(
            "__import__('duckdb').connect('memory/graph.duckdb', "
            "read_only=True)"
        )
        var gq = Python.import_module("helpers.graph.query")
        cc_expected = py_str(gq._SCHEMA_VERSION)
        var svr = dcon.execute(
            "SELECT value FROM _build_meta WHERE key='schema_version'"
        ).fetchone()
        if not svr.__bool__():
            cc_sv = String("None")
        else:
            cc_sv = py_str(svr[0])
        if cc_sv != cc_expected:
            cc_drift = 1
        var duck_n = dcon.execute("SELECT COUNT(*) FROM v_node").fetchone()
        var lite_n = to_i(
            conn.execute(
                "SELECT COUNT(*) FROM entities WHERE entity_type IN "
                "('company','sector','super_sector','sub_sector','theme',"
                "'edition','institution')"
            ).fetchone()[0]
        )
        var dn = String("None")
        if duck_n.__bool__():
            dn = py_str(duck_n[0])
        if dn != String(lite_n):
            cc_mm.append("v_node:" + dn + ":" + String(lite_n))
        var ge_present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='graph_edges' "
            "AND type='table'"
        ).fetchone()
        var er_items = Python.evaluate(
            "list(__import__('importlib').import_module("
            "'helpers.graph.query').EDGE_REGISTRY.items())"
        )
        var e_tables = List[String]()
        for i in range(er_items.__len__()):
            var et = py_str(er_items[i][0])
            e_tables.append(py_str(er_items[i][1]["table"]) + ":" + et)
        e_tables.append("e_belongs_to:belongs_to")
        e_tables.append("e_exposed_to:exposed_to")
        for i in range(len(e_tables)):
            var parts = e_tables[i].split(":")
            var tbl = String(parts[0])
            var et = String(parts[1])
            var dcnt = dcon.execute("SELECT COUNT(*) FROM " + tbl).fetchone()
            var scnt = 0
            if ge_present.__bool__():
                scnt = to_i(
                    conn.execute(
                        "SELECT COUNT(*) FROM graph_edges WHERE edge_type='"
                        + et
                        + "'"
                    ).fetchone()[0]
                )
            var ds = String("None")
            if dcnt.__bool__():
                ds = py_str(dcnt[0])
            if ds != String(scnt):
                cc_mm.append(tbl + ":" + ds + ":" + String(scnt))
        cc_err = cc_drift + len(cc_mm)
    t1 = perf_counter_ns()
    canon["cache_consistency.skipped"] = String(cc_skipped)
    canon["cache_consistency.schema_version"] = String(
        repr_fn(cc_sv).__str__()
    ) if cc_skipped == 0 else String("None")
    canon["cache_consistency.expected_schema_version"] = String(
        repr_fn(cc_expected).__str__()
    ) if cc_skipped == 0 else String("None")
    canon["cache_consistency.schema_version_drift"] = String(
        cc_drift if cc_skipped == 0 else 0
    )
    canon["cache_consistency.row_mismatches"] = join_list(
        sort_strs(cc_mm), ","
    ) if cc_skipped == 0 else String("")
    canon["cache_consistency.errors"] = String(cc_err if cc_skipped == 0 else 0)
    canon["cache_consistency.warnings"] = String(0 if cc_skipped == 0 else 1)
    print(
        "cache_reconcile: mismatches=",
        len(cc_mm),
        " drift=",
        cc_drift,
        " elapsed=",
        Float64(t1 - t0) / 1e6,
        "ms",
    )
    rep.append(
        "Cache consistency: skipped="
        + String(cc_skipped)
        + " schema_drift="
        + String(cc_drift)
        + " row_mismatches="
        + String(len(cc_mm))
    )

    # ------------------------------------------------ report + exit
    var t_end = perf_counter_ns()
    var err_total = 0
    var err_keys = List[String]()
    for ek in String(
        "relations,entity_tags,note_tags,events,quotes,company_metrics,"
        "orphan_companies,hierarchy,market_cap_conflicts,cache_consistency,"
        "normalization,duplicate_tickers,db_meta"
    ).split(","):
        err_keys.append(String(ek))
    for i in range(len(err_keys)):
        var k = err_keys[i] + ".errors"
        var ev = canon.get(k, String("-1"))
        if ev != String("-1"):
            err_total += to_i(ev)
    print(
        "errors (error-severity checks): ",
        err_total,
        " | total wall=",
        Float64(t_end - t_all) / 1e6,
        "ms",
    )

    rep.append("")
    for i in range(len(rep)):
        print(rep[i])

    # write the report file (tool mode: repo root like the original;
    # parity mode: /tmp so the operator's real report is never clobbered).
    # Full sectioned markdown mirroring the original write_report_file.
    var report_path = String("database_integrity_report.txt")
    if parity_mode:
        report_path = String("/tmp/mojo_db_integrity_report.txt")
    var rf = open(report_path, "w")
    rf.write("FinData Knowledge Graph - Database Integrity Report (Mojo port)")
    rf.write("\n")
    rf.write("=" * 60)
    rf.write("\n")
    rf.write("Database: memory/research.db")
    rf.write("\n")
    rf.write(
        "Entities: "
        + String(n)
        + " (valid "
        + String(valid)
        + ", invalid "
        + String(invalid)
        + ") | validation_rate "
        + vr
        + "%"
    )
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## RELATIONS (ERROR-level; gate-failing)")
    rf.write("\n")
    rf.write(
        "total="
        + String(rel_total)
        + " unknown_type="
        + String(rel_unknown)
        + " self_loops="
        + String(rel_loops)
        + " orphaned="
        + String(rel_orphan)
        + " type_mismatch="
        + String(rel_tm)
        + " part_of_without_has_company="
        + String(po_no_hc)
        + " has_company_without_part_of="
        + String(hc_no_po)
        + " circular="
        + String(rel_circ)
        + " -> errors="
        + String(rel_err)
    )
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## ENTITY_TAGS (ERROR-level; gate-failing)")
    rf.write("\n")
    rf.write(
        "total="
        + String(et_total)
        + " orphaned="
        + String(et_orphan)
        + " -> errors="
        + String(et_orphan)
    )
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## NOTE TAGS (ERROR-level; gate-failing)")
    rf.write("\n")
    rf.write(
        "total="
        + String(tn)
        + " stale="
        + String(stale)
        + " -> errors="
        + String(stale)
    )
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## EVENTS (ERROR-level; gate-failing)")
    rf.write("\n")
    rf.write(
        "total="
        + String(ev_total)
        + " unknown_type="
        + String(ev_unknown)
        + " orphaned="
        + String(ev_orphan)
        + " bad_properties="
        + String(ev_badp)
        + " -> errors="
        + String(ev_unknown + ev_orphan + ev_badp)
    )
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    var q_err = to_i(canon.get("quotes.errors", String("0")))
    var cm_err = to_i(canon.get("company_metrics.errors", String("0")))
    rf.write("## QUOTES (ERROR-level; gate-failing)")
    rf.write("\n")
    rf.write(
        "total="
        + canon.get("quotes.total", String("0"))
        + " orphaned="
        + canon.get("quotes.orphaned", String("0"))
        + " bad_properties="
        + canon.get("quotes.bad_properties", String("0"))
        + " -> errors="
        + String(q_err)
    )
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## COMPANY METRICS (ERROR-level; gate-failing)")
    rf.write("\n")
    rf.write(
        "total="
        + canon.get("company_metrics.total", String("0"))
        + " orphaned="
        + canon.get("company_metrics.orphaned", String("0"))
        + " bad_properties="
        + canon.get("company_metrics.bad_properties", String("0"))
        + " -> errors="
        + String(cm_err)
    )
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## ORPHAN COMPANIES (ERROR-level; gate-failing)")
    rf.write("\n")
    rf.write(
        "total_companies="
        + String(oc_total)
        + " orphan_companies="
        + String(oc_orphan)
        + " -> errors="
        + String(oc_orphan)
    )
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## SECTOR HIERARCHY (ERROR-level; gate-failing)")
    rf.write("\n")
    rf.write(
        "total_belongs_to="
        + String(h_total)
        + " sub_sector_orphans="
        + String(h_sub)
        + " sector_orphans="
        + String(h_sec)
        + " super_sector_orphans="
        + String(h_ss)
        + " multi_parent="
        + String(h_multi)
        + " cycles="
        + String(h_cycles)
        + " taxonomy_drift="
        + String(drift)
        + " -> errors="
        + String(h_err)
    )
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## MARKET CAP TAG CONFLICTS (ERROR-level; gate-failing)")
    rf.write("\n")
    if len(mc_canon) > 0:
        rf.write(
            "  "
            + String(len(mc_canon))
            + " entity(ies) with >1 market_cap/* tag:"
        )
        rf.write("\n")
        for i in range(len(mc_canon)):
            rf.write("    - " + mc_canon[i])
            rf.write("\n")
    else:
        rf.write("  none (0 conflicts)")
        rf.write("\n")
    rf.write("  -> errors=" + String(len(mc_canon)))
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## DUCKDB CACHE CONSISTENCY (ERROR-level; gate-failing)")
    rf.write("\n")
    if cc_skipped == 1:
        rf.write("  SKIPPED (" + cc_reason + ") — warnings=1")
        rf.write("\n")
    else:
        rf.write(
            "schema_version="
            + cc_sv
            + " (expected "
            + cc_expected
            + ") drift="
            + String(cc_drift)
            + " row_mismatches="
            + String(len(cc_mm))
            + " -> errors="
            + String(cc_err)
        )
        rf.write("\n")
        for i in range(len(cc_mm)):
            rf.write("    - " + cc_mm[i])
            rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## NORMALIZATION (ERROR + WARNING)")
    rf.write("\n")
    rf.write(
        "missing="
        + String(nm_missing)
        + " duplicates="
        + String(len(dup_canon))
        + " bad_format="
        + String(len(bad_fmt))
        + " -> errors="
        + String(nm_errors)
    )
    rf.write("\n")
    rf.write(
        "file_mismatches="
        + String(len(file_mm))
        + " orphaned_files="
        + String(len(orphaned))
        + " -> warnings="
        + String(nm_warn)
    )
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## SEMANTIC UNIQUENESS (duplicate tickers; ERROR-level)")
    rf.write("\n")
    rf.write(
        "duplicate_ticker_groups="
        + String(len(dup_groups))
        + " -> errors="
        + String(len(dup_groups))
    )
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## FUZZY NAME SIMILARITY (WARNING-level; advisory)")
    rf.write("\n")
    rf.write(
        "fuzzy_duplicate_pairs="
        + String(len(pairs))
        + " (suppressed: "
        + String(len(SUPPRESSED))
        + " triaged pairs)"
    )
    rf.write("\n")
    for i in range(len(pairs)):
        rf.write("    - " + pairs[i])
        rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## EDGE VALIDITY WINDOW COVERAGE (WARNING-level; advisory)")
    rf.write("\n")
    rf.write(
        "warnings="
        + String(vw_warn)
        + " (missing valid_from on acquired/subsidiary_of)"
    )
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## GRAPH SUMMARY (advisory)")
    rf.write("\n")
    rf.write("entities: " + join_list(sort_strs(ec_canon), ", "))
    rf.write("\n")
    rf.write("edges: " + join_list(sort_strs(ed_canon), ", "))
    rf.write("\n")
    rf.write(
        "sector sizes: count="
        + String(sz_count)
        + " min="
        + String(sz_min)
        + " median="
        + String(sz_med)
        + " max="
        + String(sz_max)
        + " mean="
        + mean_s
    )
    rf.write("\n")
    rf.write("largest: " + join_list(largest, ", "))
    rf.write("\n")
    rf.write("smallest: " + join_list(smallest, ", "))
    rf.write("\n")
    rf.write("market_cap: " + join_list(cap_canon, ", "))
    rf.write("\n")
    rf.write("")
    rf.write("\n")
    rf.write("## DB META (ERROR-level)")
    rf.write("\n")
    rf.write(
        "generation=" + String(dbm_generation) + " errors=" + String(dbm_err)
    )
    rf.write("\n")
    for i in range(len(dbm_reasons)):
        rf.write("    - " + dbm_reasons[i])
        rf.write("\n")
    rf.close()
    print("report written to ", report_path)

    var parity_fails = 0
    # ------------------------------------------------------ parity
    if parity_mode:
        var fx = Python.import_module("mojo_db_integrity")
        var walls = fx.python_spawn_wall()
        print(
            "cold spawn: python CLI ",
            Float64(String(walls["python_wall"].__str__())),
            "s (mojo binary wall = enclosing leg time)",
        )
        var base = fx.python_baseline(3)
        print(
            "python original: ",
            Float64(String(base[0].__str__())) / 3.0,
            "s/rep (in-process, warm)",
        )
        var golden = fx.python_all_checks()
        var gitems = Python.import_module("builtins").list(golden.items())
        var mism = List[String]()
        var n_ok2 = 0
        for i in range(gitems.__len__()):
            var k = py_str(gitems[i][0])
            var gv = py_str(gitems[i][1])
            var mv = canon.get(k, String("\x00"))
            if mv != String("\x00"):
                if mv == gv:
                    n_ok2 += 1
                else:
                    mism.append(k + ": mojo=" + mv + " python=" + gv)
            else:
                mism.append(k + ": MISSING in mojo (python=" + gv + ")")
        var mkeys = List[String]()
        for k in canon.keys():
            mkeys.append(k)
        for i in range(len(mkeys)):
            var k = mkeys[i]
            if not golden.__contains__(k):
                mism.append(k + ": EXTRA in mojo")
        print("parity: ", n_ok2, "/", gitems.__len__(), " keys match")
        if len(mism) == 0:
            print("GOLDEN PARITY OK (all checks match the python original)")
        else:
            print("GOLDEN PARITY FAIL: ", len(mism), " mismatches:")
            for i in range(len(mism)):
                print("  ", mism[i])
        parity_fails = len(mism)

    var exit_code = 0
    if err_total > 0:
        exit_code = 1
    if Float64(valid) / Float64(n) * 100.0 < 95.0:
        exit_code = 1
    if parity_fails > 0:
        exit_code = 1
    if exit_code != 0:
        print(
            "EXIT ",
            exit_code,
            " (error-severity regressions, coverage, or parity)",
        )
    sys_exit(exit_code)


def sys_exit(code: Int) raises:
    # sys.exit raises SystemExit, which the bridge surfaces as an
    # unhandled error — os._exit terminates cleanly
    if code != 0:
        Python.evaluate("__import__('os')._exit(" + String(code) + ")")
