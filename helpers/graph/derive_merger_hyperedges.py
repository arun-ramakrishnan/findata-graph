#!/usr/bin/env python3
"""Derive the ``merger`` / ``demerger`` hyperedge families
(merger_demerger_hyperedges S2).

A merger is an n-ary fact: A + B (+ …) → survivor C.  The dyadic shape
the corpus used to carry ("A merged with B", extracted as a reverse
``acquired`` edge) cannot hold the survivor — the entity that exists
AFTER the merger — as a participant, and a demerger ("X demerged from Y
into Z") is not a dyad at all.  This lane writes both as incidence-store
rows:

* one ``hyper_edges`` row per fact (``UNIQUE(edge_type, label)``);
* one ``hyper_incidences`` row per member, carrying the family role
  (``constituent`` / ``resultant`` for ``merger``; ``parent`` /
  ``resultant`` for ``demerger``).

Facts come from the triage decisions journal
(``findata/Misc/_pending_triage_decisions.jsonl``) — the review lane
(S3) is the only writer of those rows, so a fact exists only after a
human accepted it.  The row contract S2 reads:

``merger``
    ``constituents``: list of company names (≥2, distinct);
    ``resultant``: the survivor name (optional — a merger into a new
    entity may name none; the label then falls back to a deterministic
    synthetic key).
``demerger``
    ``parent``: the company that split; ``child``: the spun-off company.

Acceptance is read from the journal's decision verb, which follows the
house verb grammar ``accept:<edge_type>[:<target>]`` — so
``accept:merger`` / ``accept:merger:<resultant>`` /
``accept:demerger`` / ``accept:demerger:<child>`` all count, and a bare
``accept`` counts too.  A verb naming a DIFFERENT edge type than the
row is a contradiction, and the row is skipped.

Member names must already exist in ``entities`` — this lane does not mint
companies; an unresolved name is reported, not written (S3's review lane
owns entity creation).

Convergence: this lane OWNS the two families outright, so ``--apply``
replaces them wholesale — facts that the journal no longer holds are
dropped (shrinkage converges).  The dyadic graph is untouched: no
``graph_edges`` row is written, created, or removed here.

Provenance: source_ref ``derive:merger_hyperedges`` maps to the
``derive_merger_hyperedges`` agent (registered in
``helpers/misc/backfill_row_provenance.py`` AGENT_SEED; the lane also
self-registers the row so a fresh DB passes the FK).  Dry-run is the
default; ``--apply`` writes inside one transaction.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import DEFAULT_DB_PATH, connect  # noqa: E402
from helpers.graph.hyperedge_types import MERGER_HYPEREDGE_TYPES  # noqa: E402

MERGER_TYPE = "merger"
DEMERGER_TYPE = "demerger"
SOURCE_REF = "derive:merger_hyperedges"
AGENT_ID = "derive_merger_hyperedges"
AGENT_NAME = "derive_merger_hyperedges (merger_demerger_hyperedges S2)"
AGENT_SCRIPT = "helpers/graph/derive_merger_hyperedges.py"
AGENT_VERSION = "baseline"
AGENT_AS_OF = "2026-10-21"
SOURCE_TIER = "derive"

DECISIONS_PATH = _REPO_ROOT / "findata" / "Misc" / "_pending_triage_decisions.jsonl"


# --------------------------------------------------------------------------- #
# journal reading                                                              #
# --------------------------------------------------------------------------- #
def read_decisions(path: str | Path = DECISIONS_PATH) -> list[dict]:
    """Journal rows, newest last. Unparseable lines are skipped."""
    p = Path(path)
    if not p.is_file():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _accept_verb(row: dict) -> tuple[bool, str | None]:
    """(accepted, verb_target) — the verb carries the accepted target."""
    verb = str(row.get("decision") or "").strip()
    if not verb:
        return False, None
    parts = [p for p in verb.split(":") if p != ""]
    if not parts or parts[0].lower() != "accept":
        return False, None
    if len(parts) >= 2:
        named = parts[1].lower()
        if named not in MERGER_HYPEREDGE_TYPES:
            # accept:jv_with:DWS on a merger row is a contradiction.
            return False, None
        if named != str(row.get("edge_type") or "").strip().lower():
            return False, None
        return True, parts[2] if len(parts) >= 3 else None
    return True, None


def _clean_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    for item in value:
        name = str(item).strip()
        if name and name not in out:
            out.append(name)
    return out


def _members_from_row(row: dict, verb_target: str | None) -> tuple[list[tuple[str, str]], str]:
    """(members, label) for one row — members are (entity_name, role) pairs.

    Mirrors the triage-lane accept contract (triage_pending_relations.
    _parse_nary_accept): for ``direction == "resultant"`` rows the section
    company IS the survivor (resultant) and the verb's Name is the OTHER
    constituent — not the resultant. Getting this backwards made Mindtree
    both a constituent and the resultant of its own merger.
    """
    edge_type = str(row.get("edge_type") or "").strip().lower()
    if edge_type == MERGER_TYPE:
        constituents = _clean_list(row.get("constituents"))
        if len(constituents) < 2:
            constituents = _clean_list([row.get("constituent_a"), row.get("constituent_b")])
        if str(row.get("direction") or "").strip().lower() == "resultant":
            resultant = (
                str(row.get("resultant") or "").strip() or str(row.get("source") or "").strip()
            )
            verb_name = (verb_target or "").strip()
            if verb_name and verb_name not in constituents:
                constituents = constituents + [verb_name]
        else:
            resultant = str(row.get("resultant") or "").strip() or (verb_target or "").strip()
        if len(constituents) < 2:
            return [], ""
        members: list[tuple[str, str]] = []
        seen: set[str] = set()
        for name, role in [(n, "constituent") for n in constituents] + (
            [(resultant, "resultant")] if resultant else []
        ):
            if name and name not in seen:  # one row per entity — PK (edge_id, entity_name)
                members.append((name, role))
                seen.add(name)
        label = resultant or f"merger: {' + '.join(sorted(constituents))}"
        return members, label
    if edge_type == DEMERGER_TYPE:
        parent = str(row.get("parent") or "").strip()
        child = str(row.get("child") or "").strip() or (verb_target or "").strip()
        if not parent or not child:
            return [], ""
        return [(parent, "parent"), (child, "resultant")], child
    return [], ""


def collect(conn, decisions_path: str | Path = DECISIONS_PATH) -> dict:
    """Journal → merger/demerger facts, plus the skip reasons.

    Returns ``{"facts": [...], "skipped": [...], "unresolved": [...],
    "conflicts": [...]}``.  ``facts`` entries carry ``edge_type``,
    ``label``, ``members`` (``(name, role)`` pairs) and ``properties``.
    """
    known = {name for (name,) in conn.execute("SELECT name FROM entities")}
    facts: list[dict] = []
    skipped: list[dict] = []
    unresolved: list[dict] = []

    for row in read_decisions(decisions_path):
        edge_type = str(row.get("edge_type") or "").strip().lower()
        if edge_type not in MERGER_HYPEREDGE_TYPES:
            continue
        row_id = str(row.get("id") or "")
        accepted, verb_target = _accept_verb(row)
        if not accepted:
            skipped.append({"id": row_id, "edge_type": edge_type, "reason": "not accepted"})
            continue
        members, label = _members_from_row(row, verb_target)
        if not members:
            skipped.append({"id": row_id, "edge_type": edge_type, "reason": "incomplete members"})
            continue
        missing = [name for name, _ in members if name not in known]
        if missing:
            unresolved.append({"id": row_id, "edge_type": edge_type, "names": missing})
            continue
        props: dict = {"decision_id": row_id}
        quote = str(row.get("source_quote") or row.get("quote") or "").strip()
        if quote:
            props["source_quote"] = quote
        note = str(row.get("note") or "").strip()
        if note:
            props["note"] = note
        if edge_type == MERGER_TYPE:
            props["constituents"] = [n for n, role in members if role == "constituent"]
            resultant = [n for n, role in members if role == "resultant"]
            if resultant:
                props["resultant"] = resultant[0]
        else:
            parent = [n for n, role in members if role == "parent"]
            child = [n for n, role in members if role == "resultant"]
            if parent:
                props["parent"] = parent[0]
            if child:
                props["child"] = child[0]
        props["n_members"] = len(members)
        props["members"] = [{"name": n, "role": r} for n, r in members]
        facts.append(
            {
                "edge_type": edge_type,
                "label": label,
                "members": members,
                "properties": props,
            }
        )

    facts, conflicts = _resolve_conflicts(facts)
    return {"facts": facts, "skipped": skipped, "unresolved": unresolved, "conflicts": conflicts}


def _resolve_conflicts(facts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Two facts with the same (type, label) but different members conflict.

    Both are dropped — first-writer-wins would silently pick one reading of
    an ambiguous fact, and the reviewer (S3) is the right place to settle
    it.  Identical member sets collapse to the first row.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for fact in facts:
        groups.setdefault((fact["edge_type"], fact["label"]), []).append(fact)
    kept: list[dict] = []
    conflicts: list[dict] = []
    for (edge_type, label), group in groups.items():
        member_sets = {tuple(sorted((n, r) for n, r in f["members"])) for f in group}
        if len(member_sets) > 1:
            conflicts.append(
                {
                    "edge_type": edge_type,
                    "label": label,
                    "decision_ids": [f["properties"].get("decision_id") for f in group],
                }
            )
            continue
        kept.append(group[0])
    kept.sort(key=lambda f: (f["edge_type"], f["label"]))
    return kept, conflicts


# --------------------------------------------------------------------------- #
# write                                                                        #
# --------------------------------------------------------------------------- #
def _ensure_agent(conn) -> None:
    """Register the writing agent (FK on hyper_edges.agent_id)."""
    conn.execute(
        "INSERT OR IGNORE INTO provenance_agents "
        "(agent_id, name, version, repo_ref, script, command, as_of) "
        "VALUES (?, ?, ?, NULL, ?, NULL, ?)",
        (AGENT_ID, AGENT_NAME, AGENT_VERSION, AGENT_SCRIPT, AGENT_AS_OF),
    )


def apply_derivation(conn, facts: list[dict], *, dry_run: bool = True) -> dict:
    """Replace both families with ``facts``; return the change stats."""
    existing: dict[tuple[str, str], int] = {}
    for eid, edge_type, label in conn.execute(
        "SELECT id, edge_type, label FROM hyper_edges WHERE edge_type IN (?, ?)",
        (MERGER_TYPE, DEMERGER_TYPE),
    ):
        existing[(edge_type, label)] = int(eid)
    wanted = {(f["edge_type"], f["label"]) for f in facts}
    new_edges = sum(1 for key in wanted if key not in existing)
    dropped = sorted(key for key in existing if key not in wanted)
    new_inc = 0
    for fact in facts:
        key = (fact["edge_type"], fact["label"])
        edge_id = existing.get(key)
        if edge_id is None:
            new_inc += len(fact["members"])
            continue
        have = {
            name
            for (name,) in conn.execute(
                "SELECT entity_name FROM hyper_incidences WHERE edge_id = ?", (edge_id,)
            )
        }
        new_inc += sum(1 for name, _ in fact["members"] if name not in have)
    stats = {
        "facts": len(facts),
        "members": sum(len(f["members"]) for f in facts),
        "new_edges": new_edges,
        "dropped_edges": [f"{t}:{label}" for t, label in dropped],
        "new_incidences": new_inc,
    }
    if dry_run:
        return stats
    if not facts and not dropped:
        # No-op runs must be true no-ops: registering the agent row on an
        # empty apply would dirty the DB (and trip the ontology gate's
        # provenance questions) without producing anything.
        return stats
    _ensure_agent(conn)
    with conn:
        for edge_type, label in dropped:
            conn.execute(
                "DELETE FROM hyper_edges WHERE edge_type = ? AND label = ?", (edge_type, label)
            )  # incidences follow by ON DELETE CASCADE
        for fact in facts:
            edge_type, label = fact["edge_type"], fact["label"]
            props_json = json.dumps(fact["properties"], sort_keys=True)
            edge_id = existing.get((edge_type, label))
            if edge_id is None:
                cur = conn.execute(
                    "INSERT INTO hyper_edges (edge_type, label, properties, source_ref, "
                    "agent_id, source_tier) VALUES (?, ?, ?, ?, ?, ?)",
                    (edge_type, label, props_json, SOURCE_REF, AGENT_ID, SOURCE_TIER),
                )
                edge_id = int(cur.lastrowid)
            else:
                conn.execute(
                    "UPDATE hyper_edges SET properties = ?, source_ref = ?, agent_id = ?, "
                    "source_tier = ? WHERE id = ?",
                    (props_json, SOURCE_REF, AGENT_ID, SOURCE_TIER, edge_id),
                )
                conn.execute("DELETE FROM hyper_incidences WHERE edge_id = ?", (edge_id,))
            conn.executemany(
                "INSERT INTO hyper_incidences (edge_id, entity_name, role) VALUES (?, ?, ?)",
                [(edge_id, name, role) for name, role in fact["members"]],
            )
    return stats


def derive(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    apply: bool = False,
    decisions_path: str | Path = DECISIONS_PATH,
) -> dict:
    """Census + apply in one call; the printed report is the sync artifact."""
    conn = connect(db_path)
    try:
        collected = collect(conn, decisions_path)
        stats = apply_derivation(conn, collected["facts"], dry_run=not apply)
        per_family: dict[str, int] = {}
        for fact in collected["facts"]:
            per_family[fact["edge_type"]] = per_family.get(fact["edge_type"], 0) + 1
        print(
            f"merger/demerger: facts={stats['facts']} members={stats['members']} "
            f"families={dict(sorted(per_family.items()))}"
        )
        print(
            f"  {'[dry-run]' if not apply else '[apply]'} new_edges={stats['new_edges']} "
            f"dropped_edges={len(stats['dropped_edges'])} "
            f"new_incidences={stats['new_incidences']}"
        )
        if collected["skipped"]:
            print(f"  skipped={len(collected['skipped'])} (not accepted / incomplete)")
        if collected["unresolved"]:
            print(
                f"  unresolved={len(collected['unresolved'])} "
                "(member names missing from entities — not written)"
            )
            for item in collected["unresolved"][:10]:
                print(f"    - {item['edge_type']}: {', '.join(item['names'])}")
        if collected["conflicts"]:
            print(f"  conflicts={len(collected['conflicts'])} (same label, different members)")
            for item in collected["conflicts"][:10]:
                print(f"    - {item['edge_type']}:{item['label']}")
        return {**stats, **collected}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="research.db path")
    parser.add_argument(
        "--decisions",
        default=str(DECISIONS_PATH),
        help="triage decisions journal (JSONL)",
    )
    parser.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    args = parser.parse_args(argv)
    derive(args.db, apply=args.apply, decisions_path=args.decisions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
