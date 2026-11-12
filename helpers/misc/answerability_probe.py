#!/usr/bin/env python3
"""Answerability probe — run the FROZEN ontology question set against the
git-tracked Parquet snapshot and report coverage per business intent.

The ontology eval gate answers "did the ontology change?" — a set-diff
between two store copies. It does not answer "can this business question be
answered?", because both sides of its diff can be empty, and it never says
what a CAPABILITY covers today. This probe closes that gap **without** an
NL->SQL runtime, a Postgres catalog, a NIM endpoint, or an MCP server (see
doc/local/evaluations/auto_ontology_assessment.md §8.2/§8.3 Path C).

Three properties make it a coverage instrument rather than another gate:

* **Frozen input, committed artifact.** It reads the same
  ``ontology_questions.json`` the gate reads and the same ``snapshots/parquet/``
  the repo commits, so a result is reproducible and cannot mutate the store.
  Live-vs-snapshot parity is *checked* (``--parity``, and
  ``tests/test_answerability_probe.py``) rather than assumed.
* **Shape-parity with the gate.** Every shape is re-expressed against the
  parquet views with the gate's own semantics, over the ``<side>__<name>``
  views mounted by ``helpers.maintenance.analytics.query.connect_snapshots``.
  A difference between the two is a finding, not a dialect difference.
* **Intent grouping.** Every shape carries a business intent, so the report
  reads as coverage per capability (identity, sector membership, supply
  chain, ...) instead of a flat pass/fail list.

Each question is classified, never merely "passed" — the frozen set ships
operator-attested ``expected`` pins, and a pin that no longer holds is the
interesting signal:

answered   rows returned and the pin (when present) matches — capability works
negative   no rows and the pin IS the empty list — an intentional control
drifted    the pin no longer matches the store — a stale pin or a real break
empty      no rows and no pin to explain it — a genuine coverage hole
errored    the shape raised — schema drift between store and export

The ``sql`` shape is the question-bank extension point (Path A): a bank
entry carries its own read-only ``SELECT`` in ``params.sql`` plus an
operator-attested pin, binding NL -> SQL -> pinned answer without a new
shape per intent. A bank file is just another question set — point
``--questions`` at it. Bank entries may also carry ``question`` (the NL
text) and their own ``intent``; both flow into the record. Rows come
back in SQL order — the bank SQL carries its own ORDER BY — stringified
one per row, cells joined with ``|`` for wider projections.
Usage:
    python3 helpers/misc/answerability_probe.py              # text report
    python3 helpers/misc/answerability_probe.py --json       # JSON only
    python3 helpers/misc/answerability_probe.py --intent nary
    python3 helpers/misc/answerability_probe.py --parity     # live vs parquet
    python3 helpers/misc/answerability_probe.py --fail-on drifted
    python3 helpers/misc/answerability_probe.py --shape sql --params '<json>'
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # noqa: E402

import duckdb  # noqa: E402

from helpers.core import jsonio  # noqa: E402
from helpers.maintenance.analytics.query import connect_snapshots  # noqa: E402

DEFAULT_QUESTIONS = Path(__file__).resolve().parent / "ontology_questions.json"
# The question bank (proposal archive/database/company_metrics_grain_and_bank §3.3):
# NL -> SQL -> pinned answer entries, git-tracked like the frozen set. Loaded
# ALONGSIDE the frozen set whenever it exists; --no-bank skips it.
DEFAULT_BANK = Path(__file__).resolve().parent / "answerability_questions.json"

STATUS_ANSWERED = "answered"
STATUS_NEGATIVE = "negative"
STATUS_EMPTY = "empty"
STATUS_DRIFTED = "drifted"
STATUS_ERRORED = "errored"

STATUSES = (
    STATUS_ANSWERED,
    STATUS_NEGATIVE,
    STATUS_EMPTY,
    STATUS_DRIFTED,
    STATUS_ERRORED,
)

# Statuses that make the probe exit non-zero for --fail-on a single status.
# "any" means every status except the two healthy ones; "none" (the default)
# keeps the probe purely informational — it is a report, not a gate.
_FAILURE_SETS: dict[str, frozenset[str]] = {
    STATUS_DRIFTED: frozenset({STATUS_DRIFTED}),
    STATUS_EMPTY: frozenset({STATUS_EMPTY}),
    STATUS_ERRORED: frozenset({STATUS_ERRORED}),
    "any": frozenset({STATUS_DRIFTED, STATUS_EMPTY, STATUS_ERRORED}),
    "none": frozenset(),
}

# Shape -> (business intent, one-line capability description). Ordered by the
# ontology surfaces in doc/design/ontology.md §2 so a reader can walk the
# report against the normative doc.
SHAPE_INTENTS: dict[str, tuple[str, str]] = {
    "subtree": ("taxonomy", "concept-scheme hierarchy navigation (§2.7)"),
    "concept_mappings_for": ("crosswalk", "SKOS crosswalk traversal, one home (D-O1)"),
    "resolve_identifier": ("identity", "name resolution from a raw identifier (D-O4)"),
    "entities_of_type": ("identity", "entity roster membership (§2.6)"),
    "edge_neighbors": ("relations", "dyadic relation traversal (§2.1)"),
    "hyperedge_members": ("nary", "hyperedge incidence membership (§2.5)"),
    "hyperedge_labels": ("nary", "whole n-ary family label census (§2.5)"),
    "event_counterparties": ("events", "event participant rollup (§2.3/§2.4)"),
    "ultimate_parent_of": ("control", "corporate control chain (R3 insight lane)"),
    "insight_counterparts": ("insight", "derived insight counterparts (R3)"),
    "provenance_agents_for": ("provenance", "which agent wrote a fact table (§4)"),
    "sql": ("adhoc", "freeform bank SQL — the Path A question-bank entry point"),
    "metric_value": ("metrics", "certified-grain metric lookup (grain proposal §3.1/§3.2)"),
}

# Sample/drift detail caps: the report is a signal, not a data dump.
SAMPLE_ROWS = 5
DRIFT_EXAMPLES = 5


class ProbeError(RuntimeError):
    """Usage/schema error — the probe could not run (exit 2)."""


def _columns(con: duckdb.DuckDBPyConnection, view: str) -> set[str]:
    try:
        rows = con.execute(f"DESCRIBE {view}").fetchall()  # noqa: S608
    except duckdb.CatalogException as exc:
        raise ProbeError(f"no such view {view!r} (table name from params)") from exc
    return {row[0] for row in rows}


def _require(con: duckdb.DuckDBPyConnection, view: str, needed: set[str]) -> None:
    missing = needed - _columns(con, view)
    if missing:
        raise ProbeError(f"{view} is missing column(s) {sorted(missing)}")


def _answer_subtree(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    root = params.get("root")
    if not root:
        raise ProbeError("subtree question needs params.root")
    # Mirrors seed_concepts traversal: active-only closure, root included.
    rows = con.execute(
        """
        WITH RECURSIVE closure(id) AS (
            SELECT concept_id FROM sqlite__concepts
            WHERE concept_id = ? AND status = 'active'
            UNION
            SELECT c.concept_id FROM sqlite__concepts c
            JOIN closure cl ON c.broader_id = cl.id
            WHERE c.status = 'active'
        )
        SELECT id FROM closure ORDER BY id
        """,
        (root,),
    ).fetchall()
    return [row[0] for row in rows]


def _answer_counterparties(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    entity = params.get("entity")
    if not entity:
        raise ProbeError("event_counterparties question needs params.entity")
    sql = (
        "SELECT DISTINCT counterparty_entity FROM sqlite__events "
        "WHERE entity = ? AND counterparty_entity IS NOT NULL"
    )
    args: list = [entity]
    event_type = params.get("event_type")
    if event_type:
        sql += " AND event_type = ?"
        args.append(event_type)
    return sorted(row[0] for row in con.execute(sql, args).fetchall())


def _answer_resolve(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    value = params.get("value")
    if not value:
        raise ProbeError("resolve_identifier question needs params.value")
    names = {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite__entities WHERE cin = ?", (value,)
        ).fetchall()
    }
    names.update(
        row[0]
        for row in con.execute(
            "SELECT entity_name FROM sqlite__entity_identifiers "
            "WHERE lower(identifier_value) = lower(?)",
            (value,),
        ).fetchall()
    )
    return sorted(names)


def _answer_entities_of_type(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    entity_type = params.get("entity_type")
    if not entity_type:
        raise ProbeError("entities_of_type question needs params.entity_type")
    return sorted(
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite__entities WHERE entity_type = ?", (entity_type,)
        ).fetchall()
    )


def _answer_edge_neighbors(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    source, edge_type = params.get("source"), params.get("edge_type")
    if not source or not edge_type:
        raise ProbeError("edge_neighbors question needs params.source and params.edge_type")
    rows = con.execute(
        "SELECT target FROM sqlite__graph_edges WHERE source = ? AND edge_type = ?",
        (source, edge_type),
    ).fetchall()
    return sorted({row[0] for row in rows})


def _answer_hyperedge_members(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    edge_type, label = params.get("edge_type"), params.get("label")
    if not edge_type or not label:
        raise ProbeError("hyperedge_members question needs params.edge_type and params.label")
    rows = con.execute(
        "SELECT DISTINCT hi.entity_name FROM sqlite__hyper_incidences hi "
        "JOIN sqlite__hyper_edges he ON he.id = hi.edge_id "
        "WHERE he.edge_type = ? AND he.label = ?",
        (edge_type, label),
    ).fetchall()
    return sorted({row[0] for row in rows})


def _answer_hyperedge_labels(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    edge_type = params.get("edge_type")
    if not edge_type:
        raise ProbeError("hyperedge_labels question needs params.edge_type")
    return sorted(
        row[0]
        for row in con.execute(
            "SELECT label FROM sqlite__hyper_edges WHERE edge_type = ?", (edge_type,)
        ).fetchall()
    )


def _answer_concept_mappings_for(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    scheme, concept = params.get("source_scheme"), params.get("source_concept")
    if not scheme or not concept:
        raise ProbeError("concept_mappings_for needs params.source_scheme and source_concept")
    sql = (
        "SELECT target_scheme, target_concept, match_type FROM sqlite__concept_mappings "
        "WHERE source_scheme = ? AND source_concept = ? AND status = 'active'"
    )
    args: list = [scheme, concept]
    match_type = params.get("match_type")
    if match_type:
        sql += " AND match_type = ?"
        args.append(match_type)
    rows = con.execute(sql, args).fetchall()
    return sorted(f"{r[0]}:{r[1]}:{r[2]}" for r in rows)


def _answer_provenance_agents(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    table = params.get("table")
    if not table:
        raise ProbeError("provenance_agents_for question needs params.table")
    view = f"sqlite__{table}"
    _require(con, view, {"agent_id"})
    rows = con.execute(
        f"SELECT agent_id FROM {view} WHERE agent_id IS NOT NULL"  # noqa: S608
    ).fetchall()
    return sorted({row[0] for row in rows})


def _answer_ultimate_parent(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    company = params.get("company")
    if not company:
        raise ProbeError("ultimate_parent_of needs params.company")
    rows = con.execute(
        "SELECT ultimate_parent FROM sqlite__ultimate_parent WHERE company = ?",
        (company,),
    ).fetchall()
    return sorted({row[0] for row in rows})


# competitive_overlap counterpart pairs (both directions of a/b/c) as
# LITERAL SQL — no identifier interpolation, so the S608 audit stays clean.
_OVERLAP_PAIRS_SQL = (
    "SELECT b FROM sqlite__competitive_overlap WHERE a = ?",
    "SELECT c FROM sqlite__competitive_overlap WHERE a = ?",
    "SELECT a FROM sqlite__competitive_overlap WHERE b = ?",
    "SELECT c FROM sqlite__competitive_overlap WHERE b = ?",
    "SELECT a FROM sqlite__competitive_overlap WHERE c = ?",
    "SELECT b FROM sqlite__competitive_overlap WHERE c = ?",
)


def _answer_insight_counterparts(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    kind, subject = params.get("kind"), params.get("subject")
    if kind not in ("hidden_connections", "supply_chokepoints", "competitive_overlap"):
        raise ProbeError(
            "insight_counterparts needs params.kind "
            "(hidden_connections|supply_chokepoints|competitive_overlap)"
        )
    if not subject:
        raise ProbeError("insight_counterparts needs params.subject")
    if kind == "hidden_connections":
        rows = con.execute(
            "SELECT b FROM sqlite__hidden_connections WHERE a = ? "
            "UNION SELECT a FROM sqlite__hidden_connections WHERE b = ?",
            (subject, subject),
        ).fetchall()
        return sorted({row[0] for row in rows})
    if kind == "competitive_overlap":
        sql = " UNION ".join(_OVERLAP_PAIRS_SQL)
        rows = con.execute(sql, (subject,) * 6).fetchall()
        return sorted({row[0] for row in rows})
    out: set[str] = set()
    rows = con.execute(
        "SELECT companies FROM sqlite__supply_chokepoints WHERE supplier = ?", (subject,)
    ).fetchall()
    for (companies,) in rows:
        try:
            out.update(jsonio.loads(companies or "[]"))
        except ValueError:
            pass
    return sorted(out)


def _answer_sql(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    """Answer one bank-supplied query (the Path A question-bank entry point).

    The SQL is the contract: a single read-only SELECT/WITH against the
    mounted ``<side>__<name>`` views. Rows come back in SQL order — the
    bank SQL carries its own ORDER BY — stringified one per row, cells
    joined with ``|`` for wider projections, so pins compare set-wise
    exactly like every other shape.
    """
    sql = params.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise ProbeError("sql question needs params.sql (one read-only SELECT)")
    if sql.strip().split(None, 1)[0].upper() not in {"SELECT", "WITH"}:
        raise ProbeError("params.sql must be a read-only query (SELECT or WITH)")
    rows = con.execute(sql).fetchall()
    return [" | ".join("" if cell is None else str(cell) for cell in row) for row in rows]


def _answer_metric_value(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    """Certified-grain metric lookup, mirroring the ``company_metrics_ruled``
    view (grain proposal §3.1, ruling A1) inline over the parquet snapshot:
    one row per (entity, metric_label, period, consolidated), the latest
    filing within scope, ties broken by the newest id.

    The snapshot has no ``consolidated`` column, so scope is read from the
    properties JSON — exactly what the live view's backfill promoted — and
    the filing timestamp is the 14-digit day-first stamp in the NSE
    ``source_ref`` suffix, reordered to YYYYMMDDHHMMSS so plain string
    comparison is chronological (the view does the same with GLOB/substr).
    """
    entity = params.get("entity")
    label = params.get("metric_label")
    period = params.get("period")
    scope = params.get("scope")
    if not entity or not label or not period:
        raise ProbeError("metric_value needs params.entity, params.metric_label, params.period")
    if scope not in ("consolidated", "standalone"):
        raise ProbeError("metric_value needs params.scope (consolidated|standalone)")
    rows = con.execute(
        """
        WITH keyed AS (
            SELECT id, value_num,
                   CASE WHEN json_extract_string(properties, '$.consolidated') = 'true' THEN 1
                        WHEN json_extract_string(properties, '$.consolidated') = 'false' THEN 0
                        ELSE NULL END AS consolidated,
                   CASE WHEN regexp_matches(source_ref, '_([0-9]{14})_WEB[.]xml$')
                        THEN substr(regexp_extract(source_ref, '_([0-9]{14})_WEB[.]xml$', 1), 5, 4)
                          || substr(regexp_extract(source_ref, '_([0-9]{14})_WEB[.]xml$', 1), 3, 2)
                          || substr(regexp_extract(source_ref, '_([0-9]{14})_WEB[.]xml$', 1), 1, 2)
                          || substr(regexp_extract(source_ref, '_([0-9]{14})_WEB[.]xml$', 1), 9, 6)
                        ELSE NULL END AS filing_ts
            FROM sqlite__company_metrics
            WHERE entity = ? AND metric_label = ? AND period = ?
        ),
        ranked AS (
            SELECT value_num, consolidated, ROW_NUMBER() OVER (
                PARTITION BY consolidated ORDER BY filing_ts DESC NULLS LAST, id DESC
            ) AS rn
            FROM keyed
        )
        SELECT value_num FROM ranked WHERE rn = 1 AND consolidated = ?
        """,
        (entity, label, period, 1 if scope == "consolidated" else 0),
    ).fetchall()
    return sorted({str(r[0]) for r in rows if r[0] is not None})


_SHAPES = {
    "subtree": _answer_subtree,
    "event_counterparties": _answer_counterparties,
    "resolve_identifier": _answer_resolve,
    "entities_of_type": _answer_entities_of_type,
    "edge_neighbors": _answer_edge_neighbors,
    "hyperedge_members": _answer_hyperedge_members,
    "hyperedge_labels": _answer_hyperedge_labels,
    "concept_mappings_for": _answer_concept_mappings_for,
    "provenance_agents_for": _answer_provenance_agents,
    "ultimate_parent_of": _answer_ultimate_parent,
    "insight_counterparts": _answer_insight_counterparts,
    "sql": _answer_sql,
    "metric_value": _answer_metric_value,
}


def answer_question(con: duckdb.DuckDBPyConnection, question: dict) -> list[str]:
    """Answer one frozen question against the mounted snapshot views."""
    shape = question.get("shape")
    fn = _SHAPES.get(shape)
    if fn is None:
        raise ProbeError(f"unknown shape {shape!r} (known: {sorted(_SHAPES)})")
    return fn(con, question.get("params") or {})


def classify(answer: list[str], expected: list[str] | None) -> str:
    """Map one (answer, pin) pair to its coverage status.

    The pin is the operator-attested contract: ``None`` means the question
    carries no pin (so a non-empty answer is the only evidence it works),
    ``[]`` means the empty answer IS the contract (a negative control).
    """
    if expected is None:
        return STATUS_ANSWERED if answer else STATUS_EMPTY
    if set(map(str, answer)) == set(map(str, expected)):
        return STATUS_ANSWERED if answer else STATUS_NEGATIVE
    return STATUS_DRIFTED


def probe_question(con: duckdb.DuckDBPyConnection, question: dict) -> dict:
    """Probe one frozen question; returns its classified record."""
    shape = question.get("shape")
    default_intent, default_capability = SHAPE_INTENTS.get(shape, ("unknown", "unknown shape"))
    record: dict = {
        "id": question.get("id", "<no-id>"),
        "shape": shape,
        "question": question.get("question"),
        "intent": question.get("intent") or default_intent,
        "capability": question.get("capability") or default_capability,
        "status": STATUS_ERRORED,
        "rows": 0,
        "sample": [],
        "pinned": "expected" in question,
        "expected_n": None,
        "lost": [],
        "added": [],
        "lost_n": 0,
        "added_n": 0,
        "error": None,
    }
    try:
        answer = answer_question(con, question)
    except (ProbeError, duckdb.Error) as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    expected = question["expected"] if "expected" in question else None
    record["status"] = classify(answer, expected)
    record["rows"] = len(answer)
    record["sample"] = answer[:SAMPLE_ROWS]
    if expected is not None:
        exp, got = set(map(str, expected)), set(map(str, answer))
        record["expected_n"] = len(exp)
        lost, added = sorted(exp - got), sorted(got - exp)
        record["lost_n"], record["added_n"] = len(lost), len(added)
        record["lost"] = lost[:DRIFT_EXAMPLES]
        record["added"] = added[:DRIFT_EXAMPLES]
    return record


def _load_questions(questions_path: Path | str) -> dict:
    path = Path(questions_path)
    if not path.is_file():
        raise ProbeError(f"question set not found: {path}")
    try:
        doc = jsonio.loads(path.read_bytes())
    except jsonio.JSONDecodeError as exc:
        raise ProbeError(f"cannot parse question set {path}: {exc}") from exc
    if not doc.get("questions"):
        raise ProbeError(f"question set {path} has no questions")
    return doc


def _summarise(doc: dict, records: list[dict], source: str, bank: dict | None = None) -> dict:
    by_intent: dict[str, Counter] = defaultdict(Counter)
    for rec in records:
        by_intent[rec["intent"]][rec["status"]] += 1
    return {
        "version": doc.get("version"),
        "source": source,
        "bank": bank,
        "questions": len(records),
        "totals": dict(Counter(rec["status"] for rec in records)),
        "by_intent": {k: dict(v) for k, v in sorted(by_intent.items())},
        "records": records,
    }


def probe_all(
    questions_path: Path | str = DEFAULT_QUESTIONS,
    *,
    repo_root: Path | None = None,
    bank_path: Path | str | None = DEFAULT_BANK,
) -> dict:
    """Probe the whole frozen set — plus the question bank, when present —
    against the parquet snapshot.

    The bank (``helpers/misc/answerability_questions.json``, proposal §3.3)
    is a second question set the probe runs ALONGSIDE the frozen one: NL
    text -> SQL -> pinned answer entries. It loads when it exists and is
    skipped silently when it does not (pre-bank checkouts, adhoc fixtures);
    pass ``bank_path=None`` to force it off.
    """
    doc = _load_questions(questions_path)
    bank_doc, bank_source = None, None
    if bank_path is not None:
        bank_source = Path(bank_path)
        if bank_source.is_file():
            bank_doc = _load_questions(bank_source)
    con = connect_snapshots(repo_root)
    try:
        records = []
        for q in doc["questions"]:
            rec = probe_question(con, q)
            rec["set"] = "frozen"
            records.append(rec)
        if bank_doc:
            for q in bank_doc["questions"]:
                rec = probe_question(con, q)
                rec["set"] = "bank"
                records.append(rec)
    finally:
        con.close()
    bank = None
    if bank_doc:
        bank = {
            "source": str(bank_source),
            "version": bank_doc.get("version"),
            "questions": len(bank_doc["questions"]),
        }
    return _summarise(doc, records, str(questions_path), bank=bank)


def check_parity(
    questions_path: Path | str = DEFAULT_QUESTIONS,
    *,
    repo_root: Path | None = None,
    bank_path: Path | str | None = DEFAULT_BANK,
) -> list[dict]:
    """Answer every question on both stores; return the disagreements.

    The probe is only a snapshot instrument if the snapshot agrees with the
    live store. Live answers come from the eval gate itself, so this compares
    the two implementations as well as the two datasets — for the frozen set
    AND the bank, whose metric_value entries prove the live certified view
    and the probe's inline grain rule agree.
    """
    from helpers.core.db import connect as connect_live
    from helpers.misc.ontology_eval_gate import answer_question as answer_live

    doc = _load_questions(questions_path)
    questions = list(doc["questions"])
    if bank_path is not None:
        bank_path = Path(bank_path)
        if bank_path.is_file():
            questions.extend(_load_questions(bank_path)["questions"])
    live = connect_live()
    snapshot = connect_snapshots(repo_root)
    mismatches: list[dict] = []
    try:
        for q in doc["questions"]:
            try:
                live_answer = sorted(set(map(str, answer_live(live, q))))
            except Exception as exc:  # noqa: BLE001 - gate raises many types
                mismatches.append(
                    {"id": q["id"], "side": "live", "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            try:
                snap_answer = sorted(set(map(str, answer_question(snapshot, q))))
            except (ProbeError, duckdb.Error) as exc:
                mismatches.append(
                    {"id": q["id"], "side": "snapshot", "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            if live_answer != snap_answer:
                lost = sorted(set(live_answer) - set(snap_answer))
                added = sorted(set(snap_answer) - set(live_answer))
                mismatches.append(
                    {
                        "id": q["id"],
                        "shape": q["shape"],
                        "live_n": len(live_answer),
                        "snapshot_n": len(snap_answer),
                        "missing_from_snapshot": lost[:DRIFT_EXAMPLES],
                        "extra_in_snapshot": added[:DRIFT_EXAMPLES],
                    }
                )
    finally:
        live.close()
        snapshot.close()
    return mismatches


def _print_drift(records: list[dict]) -> None:
    for rec in records[:40]:
        bits = [f"{rec['expected_n']} pinned, {rec['rows']} now"]
        if rec["lost_n"]:
            bits.append(f"-{rec['lost_n']} lost e.g. {rec['lost'][:3]}")
        if rec["added_n"]:
            bits.append(f"+{rec['added_n']} added e.g. {rec['added'][:3]}")
        print(f"  {rec['id']}  [{rec['shape']}]  " + " · ".join(bits))
    if len(records) > 40:
        print(f"  ... {len(records) - 40} more")


def _print_report(report: dict) -> None:
    totals = report["totals"]
    print(
        f"answerability probe — question set v{report['version']}, {report['questions']} questions"
    )
    print(f"source: {report['source']}")
    if report.get("bank"):
        b = report["bank"]
        print(f"bank: {b['source']} (v{b['version']}, {b['questions']} questions)")
    print("  " + " · ".join(f"{s} {totals.get(s, 0)}" for s in STATUSES if totals.get(s, 0)))
    print()
    cols = ("answered", "negative", "drifted", "empty", "errored")
    header = f"{'intent':<14}" + "".join(f"{c:>10}" for c in cols)
    print(header)
    for intent, counts in report["by_intent"].items():
        row = f"{intent:<14}" + "".join(f"{counts.get(c, 0):>10}" for c in cols)
        print(row)
    for status in (STATUS_ERRORED, STATUS_DRIFTED, STATUS_EMPTY):
        rows = [r for r in report["records"] if r["status"] == status]
        if not rows:
            continue
        print()
        print(f"--- {status} ({len(rows)}) ---")
        if status == STATUS_DRIFTED:
            _print_drift(rows)
        else:
            for rec in rows[:40]:
                detail = rec["error"] or "no rows"
                print(f"  {rec['id']}  [{rec['shape']}]  {detail}")
            if len(rows) > 40:
                print(f"  ... {len(rows) - 40} more")


def _restrict(report: dict, intent: str) -> dict:
    kept = [r for r in report["records"] if r["intent"] == intent]
    if not kept:
        raise ProbeError(f"no questions with intent {intent!r}")
    return _summarise({"version": report["version"]}, kept, report["source"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Answerability probe over the Parquet snapshot")
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    ap.add_argument("--shape", help="probe one shape with --params")
    ap.add_argument("--params", default="{}", help="JSON params for --shape")
    ap.add_argument("--intent", help="restrict to one business intent")
    ap.add_argument(
        "--bank",
        default=str(DEFAULT_BANK),
        help="question bank to run alongside the frozen set (default: %(default)s)",
    )
    ap.add_argument(
        "--no-bank",
        action="store_true",
        help="skip the question bank even when the default bank file exists",
    )
    ap.add_argument(
        "--parity",
        action="store_true",
        help="compare live-store answers with snapshot answers and exit 1 on drift",
    )
    ap.add_argument(
        "--fail-on",
        choices=tuple(_FAILURE_SETS),
        default="none",
        help="exit 1 when any question lands in this status (default: none — the probe is a report, not a gate)",
    )
    args = ap.parse_args(argv)

    try:
        if args.parity:
            mismatches = check_parity(args.questions, bank_path=None if args.no_bank else args.bank)
            payload = {"parity_mismatches": len(mismatches), "details": mismatches}
            if args.json:
                print(jsonio.dumps(payload, indent=2))
            else:
                print(f"live vs snapshot parity: {len(mismatches)} mismatch(es)")
                for item in mismatches[:40]:
                    print(f"  {item['id']}  " + jsonio.dumps(item))
            return 1 if mismatches else 0

        if args.shape:
            con = connect_snapshots()
            try:
                question = {
                    "id": f"adhoc-{args.shape}",
                    "shape": args.shape,
                    "params": jsonio.loads(args.params.encode()),
                }
                record = probe_question(con, question)
            finally:
                con.close()
            report = _summarise({"version": None}, [record], "adhoc")
        else:
            report = probe_all(args.questions, bank_path=None if args.no_bank else args.bank)
            if args.intent:
                report = _restrict(report, args.intent)
    except ProbeError as exc:
        print(f"answerability_probe: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(jsonio.dumps(report, indent=2))
    else:
        _print_report(report)
    failing = _FAILURE_SETS[args.fail_on]
    return 1 if any(report["totals"].get(s) for s in failing) else 0


if __name__ == "__main__":
    sys.exit(main())
