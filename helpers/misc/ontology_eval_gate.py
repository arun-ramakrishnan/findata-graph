#!/usr/bin/env python3
"""Ontology change gate — deterministic set-diff over a frozen question set.

ontology_governance S2: answers a frozen question set against a PARENT and
a CANDIDATE copy of the store, exact set-compare, and applies the
EvoOntology-derived accept rule (translated for row-set answers):

- **zero regressions** — a frozen question whose answer LOST rows (or
  errored) is a hard reject (the zero-critical-errors veto);
- **no undeclared changes** — every changed answer must be declared via
  ``--expect-improve`` (strict mode: undeclared change = regression —
  forces the change author to say what should move);
- **targeted improvement materializes** — every ``--expect-improve``
  question must actually change (strictly-beats-parent).

Verdict: ACCEPT iff all three hold. Report JSON carries ``protocol`` —
``"set_diff"`` now; the LLM-judge variant (anonymized A/B, same accept
rule) is a reserved slot deferred behind an LLM-API posture.

Re-baselining is EXPLICIT and recorded: after an accepted change, run
``--rebaseline REF`` to rewrite expected answers from the candidate DB
(bumping the question-set version and stamping the accepting reference) —
never automatic, else the gate deflates silently. A question set with
``"draft": true`` is refused: the operator authors the roster and flips
the flag when the answers are verified.

Not a maint step (gates don't converge), not a qa leg — a mid-arc
targeted check run on the disposable copy between dry-run and canonical
apply, mandatory in acceptance criteria whenever a change alters
query-visible semantics (rosters, crosswalks, hierarchies, extractor
rules).

Question shapes (answer = sorted list of strings):

- ``subtree`` — params ``{"root": "scheme:code"}``: active-only closure
  via ``seed_concepts.subtree``;
- ``event_counterparties`` — params ``{"entity": name, "event_type"?}``:
  distinct resolved counterparties from ``events``;
- ``resolve_identifier`` — params ``{"value": ident}``: entities.cin
  first, then the entity_identifiers registry (NOCASE);
- ``entities_of_type`` — params ``{"entity_type": t}``: the entity
  vocabulary rosters (doc/design/ontology.md §2.6);
- ``edge_neighbors`` — params ``{"source": n, "edge_type": e}``: out-edge
  targets — one question per populated edge type covers the §2.1 roster;
- ``hyperedge_members`` — params ``{"edge_type": t, "label": l}``:
  incidence-store member sets — one per hyperedge type covers §2.5;
- ``concept_mappings_for`` — params ``{"source_scheme": s,
  "source_concept": c, "match_type"?}``: ACTIVE crosswalk targets from
  the one crosswalk home (D-O1);
- ``provenance_agents_for`` — params ``{"table": t}``: distinct agent_ids
  that wrote a fact table (S1-of-234 provenance surface).

Usage:
    python3 helpers/misc/ontology_eval_gate.py --parent A.db --candidate B.db
    python3 helpers/misc/ontology_eval_gate.py --parent A.db --candidate B.db \
        --questions helpers/misc/ontology_questions.json --expect-improve q1,q2
    python3 helpers/misc/ontology_eval_gate.py --parent A.db --candidate B.db \
        --questions Q.json --rebaseline "completed.md #NNN (the accepting change)"
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # noqa: E402

from helpers.core.db import connect  # noqa: E402
from helpers.misc.seed_concepts import subtree  # noqa: E402

DEFAULT_QUESTIONS = Path(__file__).resolve().parent / "ontology_questions.json"

VERDICT_UNCHANGED = "unchanged"
VERDICT_IMPROVED = "improved"
VERDICT_REGRESSED = "regressed"
VERDICT_UNDECLARED = "undeclared_change"
VERDICT_ERRORED = "errored"


class GateError(RuntimeError):
    """Usage/IO error — the gate could not run (exit 2)."""


def _answer_subtree(conn: sqlite3.Connection, params: dict) -> list[str]:
    root = params.get("root")
    if not root:
        raise GateError("subtree question needs params.root")
    return subtree(conn, root)


def _answer_counterparties(conn: sqlite3.Connection, params: dict) -> list[str]:
    entity = params.get("entity")
    if not entity:
        raise GateError("event_counterparties question needs params.entity")
    event_type = params.get("event_type")
    sql = (
        "SELECT DISTINCT counterparty_entity FROM events WHERE entity=? "
        "AND counterparty_entity IS NOT NULL"
    )
    args: list = [entity]
    if event_type:
        sql += " AND event_type=?"
        args.append(event_type)
    return sorted(row[0] for row in conn.execute(sql, args))


def _answer_resolve(conn: sqlite3.Connection, params: dict) -> list[str]:
    value = params.get("value")
    if not value:
        raise GateError("resolve_identifier question needs params.value")
    names = [row[0] for row in conn.execute("SELECT name FROM entities WHERE cin=?", (value,))]
    has_registry = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='entity_identifiers' AND type='table'"
    ).fetchone()
    if has_registry:
        names += [
            row[0]
            for row in conn.execute(
                "SELECT entity_name FROM entity_identifiers "
                "WHERE identifier_value=? COLLATE NOCASE",
                (value,),
            )
        ]
    return sorted(set(names))


def _answer_entities_of_type(conn: sqlite3.Connection, params: dict) -> list[str]:
    entity_type = params.get("entity_type")
    if not entity_type:
        raise GateError("entities_of_type question needs params.entity_type")
    return sorted(
        row[0]
        for row in conn.execute("SELECT name FROM entities WHERE entity_type=?", (entity_type,))
    )


def _answer_edge_neighbors(conn: sqlite3.Connection, params: dict) -> list[str]:
    source, edge_type = params.get("source"), params.get("edge_type")
    if not source or not edge_type:
        raise GateError("edge_neighbors question needs params.source and params.edge_type")
    return sorted(
        {
            row[0]
            for row in conn.execute(
                "SELECT target FROM graph_edges WHERE source=? AND edge_type=?",
                (source, edge_type),
            )
        }
    )


def _answer_hyperedge_members(conn: sqlite3.Connection, params: dict) -> list[str]:
    edge_type, label = params.get("edge_type"), params.get("label")
    if not edge_type or not label:
        raise GateError("hyperedge_members question needs params.edge_type and params.label")
    return sorted(
        {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT hi.entity_name FROM hyper_incidences hi "
                "JOIN hyper_edges he ON he.id = hi.edge_id "
                "WHERE he.edge_type=? AND he.label=?",
                (edge_type, label),
            )
        }
    )


def _answer_concept_mappings_for(conn: sqlite3.Connection, params: dict) -> list[str]:
    scheme, concept = params.get("source_scheme"), params.get("source_concept")
    if not scheme or not concept:
        raise GateError("concept_mappings_for needs params.source_scheme and source_concept")
    match_type = params.get("match_type")
    sql = (
        "SELECT target_scheme, target_concept, match_type FROM concept_mappings "
        "WHERE source_scheme=? AND source_concept=? AND status='active'"
    )
    args: list = [scheme, concept]
    if match_type:
        sql += " AND match_type=?"
        args.append(match_type)
    return sorted(f"{r[0]}:{r[1]}:{r[2]}" for r in conn.execute(sql, args))


def _answer_provenance_agents(conn: sqlite3.Connection, params: dict) -> list[str]:
    table = params.get("table")
    if not table:
        raise GateError("provenance_agents_for question needs params.table")
    if not re.fullmatch(r"[a-z_]+", table):
        raise GateError(f"provenance_agents_for: bad table name {table!r}")
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (table,)
    ).fetchone()
    if has is None or "agent_id" not in {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})")  # noqa: S608 — fullmatch-guarded identifier
    }:
        raise GateError(f"provenance_agents_for: table {table!r} has no agent_id column")
    return sorted(
        {
            row[0]
            for row in conn.execute(
                f"SELECT agent_id FROM {table} WHERE agent_id IS NOT NULL"  # noqa: S608 — fullmatch-guarded identifier
            )
        }
    )


_SHAPES = {
    "subtree": _answer_subtree,
    "event_counterparties": _answer_counterparties,
    "resolve_identifier": _answer_resolve,
    "entities_of_type": _answer_entities_of_type,
    "edge_neighbors": _answer_edge_neighbors,
    "hyperedge_members": _answer_hyperedge_members,
    "concept_mappings_for": _answer_concept_mappings_for,
    "provenance_agents_for": _answer_provenance_agents,
}


def answer_question(conn: sqlite3.Connection, question: dict) -> list[str]:
    """Answer one question against ``conn``; raises GateError on shape/param trouble."""
    shape = question.get("shape")
    fn = _SHAPES.get(shape)
    if fn is None:
        raise GateError(f"unknown shape {shape!r} (known: {sorted(_SHAPES)})")
    return fn(conn, question.get("params") or {})


def load_questions(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise GateError(f"cannot load question set {path}: {e}") from e
    if data.get("draft"):
        raise GateError(
            f"{path}: question set is a DRAFT — the operator authors the "
            "roster, verifies the answers, then flips draft to false"
        )
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        raise GateError(f"{path}: no questions[] entries")
    for q in questions:
        if not q.get("id") or q.get("shape") not in _SHAPES:
            raise GateError(f"{path}: question missing id or known shape: {q}")
    return data


def _evaluate_one(
    parent: sqlite3.Connection, candidate: sqlite3.Connection, q: dict, declared: bool
) -> dict:
    """Answer one question on both sides and classify its verdict.

    Returns the report entry; a ``reason`` key is present iff the verdict
    blocks acceptance or a declared improvement failed to materialize.
    """
    qid = q["id"]
    entry: dict = {"id": qid, "shape": q["shape"], "declared": declared}
    try:
        p = answer_question(parent, q)
    except (GateError, sqlite3.Error) as e:
        p = None
        entry["parent_error"] = str(e)
    try:
        c = answer_question(candidate, q)
    except (GateError, sqlite3.Error) as e:
        c = None
        entry["candidate_error"] = str(e)
    if p is None or c is None:
        entry["verdict"] = VERDICT_ERRORED
        entry["reason"] = (
            f"{qid}: could not answer ({entry.get('parent_error') or entry.get('candidate_error')})"
        )
        return entry
    entry["parent_n"], entry["candidate_n"] = len(p), len(c)
    entry["added"] = sorted(set(c) - set(p))
    entry["lost"] = sorted(set(p) - set(c))
    if p == c and declared:
        entry["verdict"] = VERDICT_UNCHANGED
        entry["reason"] = f"{qid}: declared improvement did not materialize"
    elif p == c:
        entry["verdict"] = VERDICT_UNCHANGED
    elif declared and entry["lost"]:
        entry["verdict"] = VERDICT_REGRESSED
        entry["reason"] = f"{qid}: declared change LOST rows ({entry['lost']})"
    elif declared:
        entry["verdict"] = VERDICT_IMPROVED
    else:
        entry["verdict"] = VERDICT_UNDECLARED
        entry["reason"] = (
            f"{qid}: undeclared answer change ({entry['lost']} lost, {entry['added']} added)"
        )
    return entry


def evaluate(
    parent: sqlite3.Connection,
    candidate: sqlite3.Connection,
    questions: list[dict],
    expect_improve: set[str],
) -> tuple[bool, list[dict], list[str]]:
    """Run the accept rule; returns (accept, per-question results, reasons)."""
    known_ids = {q["id"] for q in questions}
    unknown = sorted(expect_improve - known_ids)
    if unknown:
        raise GateError(f"--expect-improve names unknown question ids: {unknown}")
    results = [_evaluate_one(parent, candidate, q, q["id"] in expect_improve) for q in questions]
    reasons = [e.pop("reason") for e in results if "reason" in e]
    # _evaluate_one sets a reason exactly when the verdict blocks acceptance
    # (regressed/undeclared/errored) or a declared improvement didn't move.
    return not reasons, results, reasons


def rebaseline(candidate: sqlite3.Connection, questions_path: Path, data: dict, ref: str) -> dict:
    """Rewrite expected answers from the candidate DB; bump version; stamp ref."""
    for q in data["questions"]:
        q["expected"] = answer_question(candidate, q)
    data["version"] = int(data.get("version", 1)) + 1
    data["rebaselined"] = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ref": ref,
    }
    data.pop("draft", None)
    questions_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--parent", type=Path, help="parent (pre-change) DB copy")
    parser.add_argument(
        "--candidate", type=Path, required=True, help="candidate (post-change) DB copy"
    )
    parser.add_argument(
        "--questions", type=Path, default=DEFAULT_QUESTIONS, help="frozen question set JSON"
    )
    parser.add_argument(
        "--expect-improve", default="", help="comma list of question ids the change targets"
    )
    parser.add_argument(
        "--rebaseline",
        default=None,
        metavar="REF",
        help="after ACCEPT: rewrite expected from candidate, recording REF",
    )
    parser.add_argument("--report", type=Path, default=None, help="write the JSON report here")
    args = parser.parse_args(argv)

    try:
        data = load_questions(args.questions)
        if args.rebaseline and not args.parent:
            raise GateError("--rebaseline needs --parent too (it refuses unless the gate ACCEPTed)")
        if not args.rebaseline and not args.parent:
            raise GateError("--parent is required unless --rebaseline")
        expect = {q for q in args.expect_improve.split(",") if q}
        candidate = connect(args.candidate)
        try:
            if args.rebaseline:
                parent = connect(args.parent)
                try:
                    accept, results, reasons = evaluate(
                        parent, candidate, data["questions"], expect
                    )
                finally:
                    parent.close()
                if not accept:
                    print("[gate] REFUSED to rebaseline: gate did not ACCEPT", file=sys.stderr)
                    return 1
                rebaseline(candidate, args.questions, data, args.rebaseline)
                print(
                    f"[gate] rebaselined {args.questions} (v{data['version']}, ref: {args.rebaseline})"
                )
                return 0
            parent = connect(args.parent)
            try:
                accept, results, reasons = evaluate(parent, candidate, data["questions"], expect)
            finally:
                parent.close()
        finally:
            candidate.close()
    except GateError as e:
        print(f"[gate] ERROR {e}", file=sys.stderr)
        return 2

    report = {
        "protocol": "set_diff",
        "question_set": str(args.questions),
        "question_set_version": data.get("version", 1),
        "expect_improve": sorted(expect),
        "accept": accept,
        "reasons": reasons,
        "questions": results,
    }
    if args.report:
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    verdict = "ACCEPT" if accept else "REJECT"
    print(f"[gate] {verdict} — {len(results)} questions, {len(reasons)} reason(s)")
    for r in reasons:
        print(f"[gate]   {r}")
    if args.report:
        print(f"[gate] report: {args.report}")
    return 0 if accept else 1


if __name__ == "__main__":
    sys.exit(main())
