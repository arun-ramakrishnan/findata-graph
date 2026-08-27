#!/usr/bin/env python3
"""
Triage the `findata/_pending_relations.txt` review queue (pending_relations_triage).

The sidecar is where extract_relations parks pattern matches whose target
entity couldn't be resolved, and where suggest_relations --append dumps its
link-prediction candidates. Left alone it conflates two populations,
accrues append-only duplicates, and fills with deterministic noise
(countries, generic phrases, mangled fragments) — the third full-manual
triage in a month motivated encapsulating the whole workflow here:

  --report (default)  dedupe + split populations + bucket prose rows,
                      emit the eyeball report and an annotated-ready
                      decisions file. NON-destructive.
  --apply-decisions   validate + act on annotated decisions; with --write:
                      alias rows persist to findata/relation_aliases.json
                      (runtime-loaded by extract_relations), accept rows
                      write their edge straight into graph_edges
                      (suggested_relations_accept, S4), discard/skip rows
                      drop out, `suggested` rows move to their own file,
                      unresolved prose rows stay (deduped).
  --clear             truncate the sidecar to 0 (the post-triage endgame).

Decision actions: discard | skip | stub | alias:<Entity> |
accept:<edge_type>[:<Target Entity>] — the accept writes
(source, target[, override], edge_type) into graph_edges with
source_ref='triage:accept' and the row's provenance in properties; used for
link-prediction suggestions (assign the missing typed edge) and for
mangled-mention prose rows whose true target already exists.

Usage:
    python3 helpers/graph/triage_pending_relations.py                 # report
    python3 helpers/graph/triage_pending_relations.py --apply-decisions
    python3 helpers/graph/triage_pending_relations.py --apply-decisions --write
    python3 helpers/graph/triage_pending_relations.py --clear

Exit codes: 0 ok, 1 bad decisions / validation failure.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# Repo root: helpers/graph/triage_pending_relations.py -> parents[2]. Must be
# on sys.path BEFORE the helpers.* imports below so the script works as a
# subprocess the same way it works under pytest. (House bootstrap.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.fuzzy_match import fuzzy_match  # noqa: E402

# Monkeypatchable paths (the VAULT_ROOT lesson — tests retarget all of them).
SIDECAR = _REPO_ROOT / "findata" / "_pending_relations.txt"
SUGGESTIONS = _REPO_ROOT / "findata" / "_pending_suggestions.txt"
ALIAS_FILE = _REPO_ROOT / "findata" / "relation_aliases.json"
REPORT = _REPO_ROOT / "findata" / "_pending_triage_report.md"
DECISIONS = _REPO_ROOT / "findata" / "_pending_triage_decisions.jsonl"
# graph_edges write target for `accept:` decisions. None = connect()'s
# default (memory/research.db); tests point it at a tmp schema file.
EDGE_DB_PATH: Path | None = None

# Vocabulary an accept: decision may assign (the graph's relation edge
# types — roster bookkeeping types part_of/has_company are excluded: a
# suggested company↔company pair is never a roster edge).
_ACCEPT_EDGE_TYPES = frozenset({
    "competes_with", "jv_with", "supplier_to", "customer_of", "acquired",
    "subsidiary_of", "same_group", "co_mentioned_in", "semantic_peer",
    "invested_in", "exposed_to", "cited_in",
})
# Stored canonicalised as ONE row with symmetric=1 (graph_design §4 — the
# same set extract_relations writes with the flag).
_SYMMETRIC_ACCEPT_TYPES = frozenset({
    "jv_with", "same_group", "competes_with", "co_mentioned_in",
})
_ACCEPT_SOURCE_REF = "triage:accept"

# Deterministic noise classifiers (must stay in sync with the extractor's
# write-time gate — the goal is that post-S2 these rows never reach the
# sidecar; the classifiers here still clean whatever slipped in before).
COUNTRIES = {
    "india", "japan", "germany", "china", "usa", "us", "u.s.", "uk", "u.k.",
    "france", "switzerland", "netherlands", "singapore", "uae", "dubai",
    "italy", "sweden", "korea", "south korea", "europe", "america",
    "australia", "russia", "brazil", "thailand", "malaysia", "indonesia",
    "vietnam", "ukraine", "israel", "taiwan", "hong kong", "canada",
    "mexico", "spain", "denmark", "finland", "belgium", "austria", "norway",
    "poland", "turkey", "egypt", "south africa", "nigeria", "kenya",
    "bangladesh", "pakistan", "nepal", "sri lanka", "ecuador", "oman",
}
_GENERIC_PREFIX = re.compile(
    r"^(?:vendor|suppliers?|customers?|clients?|partners?|contractors?|"
    r"players?|operators?|manufacturers?|dealers?|distributors?|retailers?|"
    r"oems?|tier[- ]1|psu|government|army|navy|air force|indian armed forces|"
    r"indian railways|railways?|farmers?|consumers?|banks?|nbfcs?|hfcs?|"
    r"fintechs?|startups?|platforms?|brands?|products?|subsidiaries|group|"
    r"holding|investors?|peers?|competitors?|markets?|industries?|sectors?|"
    r"companies|fortune 500|cdmo)\b",
    re.IGNORECASE,
)
# Lowercase junk fragments that mark a mangled capture window, not a name.
_FRAGMENT_JUNK = re.compile(
    r"\s(?:but|we|earlier|effectively|now|since|by|and now|operational)\b"
)
# Generic descriptor ENDINGS ("Electric Arc Furnace operators") — the
# capture named a category, not a company.
_GENERIC_SUFFIX = re.compile(
    r"\b(?:operators?|contractors?|players?|firms?|clients?|customers?|"
    r"partners?|suppliers?|manufacturers?|brands?)$",
    re.IGNORECASE,
)


def noise_target(target: str) -> bool:
    """True for deterministic non-entity targets (countries, generic
    phrases/descriptors, mangled capture fragments). CANONICAL classifier —
    extract_relations imports this for its write-time gate so such rows
    never reach the sidecar in the first place."""
    t = _norm_target(target)
    tl = t.lower()
    return (
        not t or len(tl) < 4
        or tl in COUNTRIES
        or bool(_GENERIC_PREFIX.match(tl))
        or bool(_GENERIC_SUFFIX.search(tl))
        or bool(_FRAGMENT_JUNK.search(tl))
    )


def _row_id(edge_type: str, source: str, target: str) -> str:
    """Stable short id for a (edge, source, target) — the decisions key."""
    h = hashlib.sha256(
        f"{edge_type}\x1f{source}\x1f{target}".encode()
    ).hexdigest()
    return h[:10]


def _norm_target(t: str) -> str:
    """Normalize a mention for matching: strip boundary punctuation and a
    possessive suffix. (A literal suffix strip — rstrip("'s") would eat any
    trailing 's' and turn 'Railways' into 'Railway'.)"""
    t = t.strip().rstrip(".,;:")
    for suf in ("'s", "\u2019s"):
        if t.lower().endswith(suf):
            return t[: -len(suf)].strip()
    return t


def load_entity_names() -> set[str]:
    """Distinct entity names from the live DB (monkeypatchable in tests)."""
    from helpers.core.db import connect

    conn = connect()
    try:
        return {r[0] for r in conn.execute("SELECT name FROM entities")}
    finally:
        conn.close()


def _bucket(edge_type: str, target: str, names: set[str]) -> tuple[str, str]:
    """(bucket, detail) for one prose row's target. Deterministic, advisory."""
    t = _norm_target(target)
    tl = t.lower()
    if noise_target(t):
        return "discard", "country/generic/fragment"
    # Alias candidate: the fuzzy matcher resolves it to a DIFFERENT
    # existing name (exact-cased match means the extractor should already
    # have resolved it, so only report genuine re-spellings).
    match, method, score = fuzzy_match(t, sorted(names))
    if match and match.lower() != tl and method != "spellfix":
        return "alias_candidate", f"{match} ({method}, {score:.2f})"
    # Stub candidate: >=2 tokens, all name-shaped (capitalized or joiners).
    words = t.split()
    joiners = {"of", "the", "and", "de", "der", "van"}
    if len(words) >= 2 and all(
        w[0].isupper() or w.lower() in joiners for w in words if w
    ):
        return "stub_candidate", "name-shaped"
    return "manual", ""


def build_triage(lines: list[str], names: set[str]) -> dict:
    """Parse + dedupe + split + bucket. Pure function of (lines, names).

    Returns {"suggested": [...], "prose": [...], "unparseable": [...],
    "dupes": n} where each row dict carries id/edge_type/source/
    target_mention/quote/edition/bucket/detail."""
    suggested, prose, unparseable = [], [], []
    seen: set[str] = set()
    dupes = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            unparseable.append(line)
            continue
        key = f"{d.get('edge_type')}\x1f{d.get('source')}\x1f{d.get('target_mention')}"
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        row = {
            "id": _row_id(d.get("edge_type", ""), d.get("source", ""),
                          d.get("target_mention", "")),
            "edge_type": d.get("edge_type", ""),
            "source": d.get("source", ""),
            "target_mention": d.get("target_mention", ""),
            "quote": (d.get("quote") or "")[:160],
            "edition": d.get("edition", ""),
        }
        if row["edge_type"] == "suggested":
            suggested.append(row)
        else:
            bucket, detail = _bucket(
                row["edge_type"], row["target_mention"], names
            )
            if row["source"] and row["source"] not in names:
                bucket, detail = "bad_source", "source not an entity"
            row["bucket"] = bucket
            row["detail"] = detail
            prose.append(row)
    return {"suggested": suggested, "prose": prose,
            "unparseable": unparseable, "dupes": dupes}


def write_report(triage: dict, names_count: int) -> None:
    """Emit the eyeball report + the decisions file (non-destructive).

    NOTE: the decisions file is REGENERATED here — annotate only after the
    last --report run, or annotations are lost."""
    lines = [
        "# Pending-relations triage report",
        "",
        f"- entities in DB: {names_count}",
        f"- suggested rows (link-prediction dump): {len(triage['suggested'])}",
        f"- prose rows (true queue): {len(triage['prose'])}",
        f"- duplicate lines absorbed: {triage['dupes']}",
        f"- unparseable lines (kept verbatim on rewrite): "
        f"{len(triage['unparseable'])}",
        "",
    ]
    sug_rows = _read_suggestions_rows()
    if sug_rows:
        top = sorted(
            sug_rows, key=lambda r: float(r.get("score") or 0), reverse=True)
        lines.append(f"- suggestions file (`{Path(SUGGESTIONS).name}`): "
                     f"{len(sug_rows)} rows — top by score:")
        for r in top[:10]:
            lines.append(f"  - {r.get('source')} <-> {r.get('target_mention')} "
                         f"({r.get('score')}, {r.get('method')})")
        lines.append("")
    from collections import Counter

    counts = Counter(r["bucket"] for r in triage["prose"])
    lines.append("| bucket | rows |")
    lines.append("|---|---|")
    for b, n in counts.most_common():
        lines.append(f"| {b} | {n} |")
    lines.append("")
    order = ["discard", "alias_candidate", "stub_candidate", "manual",
             "bad_source"]
    for b in order:
        rows = [r for r in triage["prose"] if r["bucket"] == b]
        if not rows:
            continue
        lines.append(f"## {b} ({len(rows)})")
        lines.append("")
        for r in rows:
            lines.append(
                f"- `{r['id']}` **{r['edge_type']}** {r['source']} -> "
                f"**{r['target_mention']}**"
                + (f" _({r['detail']})_" if r["detail"] else "")
            )
            lines.append(f"  > {r['quote']}")
        lines.append("")
    lines.append("## suggested rows on the sidecar (moved out on --write; "
                 "triage the suggestions file itself via accept:/discard)")
    lines.append("")
    for r in triage["suggested"][:50]:
        lines.append(f"- {r['source']} <-> {r['target_mention']}")
    if len(triage["suggested"]) > 50:
        lines.append(f"- … and {len(triage['suggested']) - 50} more")
    Path(REPORT).write_text("\n".join(lines) + "\n", encoding="utf-8")

    with Path(DECISIONS).open("w", encoding="utf-8") as f:
        for r in triage["prose"]:
            f.write(json.dumps({
                "id": r["id"], "edge_type": r["edge_type"],
                "source": r["source"],
                "target_mention": r["target_mention"],
                "bucket": r["bucket"], "decision": None, "note": None,
            }, ensure_ascii=False) + "\n")


def _read_decisions(path: Path) -> list[dict]:
    rows = []
    for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError as e:
            # Editors happily hard-wrap a long JSON row into two physical
            # lines; name the line so the fix is a 5-second rejoin.
            raise SystemExit(
                f"{path.name}:{lineno} is not valid JSON ({e}) — the row "
                f"was probably split across lines:\n  {line[:120]}"
            ) from e
        if d.get("decision"):
            rows.append(d)
    return rows


def _validate_decisions(rows: list[dict], entity_names: set[str]) -> dict | None:
    """Parse + validate annotated decisions into an action plan, or None
    (with the error already printed) on any invalid decision."""
    plan: dict = {"aliases": {}, "applied_keys": set(), "stubs": [],
                  "accepts": [], "discard": 0, "skip": 0}
    for d in rows:
        key = (d["edge_type"], d["source"], d["target_mention"])
        decision = str(d["decision"]).strip()
        if decision == "discard":
            plan["discard"] += 1
        elif decision == "skip":
            plan["skip"] += 1
        elif decision == "stub":
            plan["stubs"].append(d)
        elif decision.startswith("alias:"):
            target = decision[len("alias:"):].strip()
            if target not in entity_names:
                print(f"ERROR: alias target {target!r} is not an existing "
                      f"entity (row {d['id']})", file=sys.stderr)
                return None
            plan["aliases"][_norm_target(d["target_mention"]).lower()] = target
        elif decision.startswith("accept:"):
            parsed = _parse_accept(d, decision, entity_names)
            if parsed is None:
                return None
            plan["accepts"].append(parsed)
        else:
            print(f"ERROR: unknown decision {decision!r} (row {d['id']})",
                  file=sys.stderr)
            return None
        plan["applied_keys"].add(key)
    return plan


def _parse_accept(d: dict, decision: str,
                  entity_names: set[str]) -> dict | None:
    """Validate one `accept:<edge_type>[:<Target Entity>]` decision into a
    writable edge spec (source/target/edge_type/properties/symmetric)."""
    parts = decision[len("accept:"):].split(":", 1)
    edge_type = parts[0].strip()
    if edge_type not in _ACCEPT_EDGE_TYPES:
        print(f"ERROR: accept edge_type {edge_type!r} is not in the relation "
              f"vocabulary {sorted(_ACCEPT_EDGE_TYPES)} (row {d['id']})",
              file=sys.stderr)
        return None
    # Explicit target override for mangled mentions; otherwise the row's own
    # target_mention must BE the entity (link-prediction rows always are).
    target = parts[1].strip() if len(parts) > 1 else _norm_target(
        d["target_mention"])
    if target not in entity_names:
        print(f"ERROR: accept target {target!r} is not an existing entity "
              f"(row {d['id']}) — stub it first, or name an existing one",
              file=sys.stderr)
        return None
    if d["source"] not in entity_names:
        print(f"ERROR: accept source {d['source']!r} is not an existing "
              f"entity (row {d['id']})", file=sys.stderr)
        return None
    properties: dict = {"edition": d.get("edition", ""),
                        "origin": d.get("origin", "manual_triage")}
    if d.get("score") is not None:
        properties["score"] = d["score"]
    if d.get("method"):
        properties["method"] = d["method"]
    return {
        "source": d["source"], "target": target, "edge_type": edge_type,
        "symmetric": edge_type in _SYMMETRIC_ACCEPT_TYPES,
        "properties": properties,
    }


def _apply_write(plan: dict) -> None:
    """Persist the plan: alias file merge, accepted edges, sidecar rewrite,
    suggestions move + decided-row drops."""
    aliases = plan["aliases"]
    # 1. Alias additions (merged, sorted; file wins at load time).
    existing = {}
    if Path(ALIAS_FILE).exists():
        try:
            existing = json.loads(Path(ALIAS_FILE).read_text(encoding="utf-8"))
        except ValueError:
            print("WARNING: relation_aliases.json unreadable — replacing",
                  file=sys.stderr)
    existing.update(aliases)
    Path(ALIAS_FILE).write_text(
        json.dumps(dict(sorted(existing.items())), indent=2,
                   ensure_ascii=False) + "\n", encoding="utf-8")
    if aliases:
        print(f"relation_aliases.json: +{len(aliases)} entries "
              f"(now {len(existing)})")

    # 2. Accepted edges -> graph_edges (same INSERT discipline as
    # extract_relations: INSERT OR IGNORE + per-row integrity skips;
    # idempotent via the UNIQUE constraint).
    if plan["accepts"]:
        _write_accepted_edges(plan["accepts"])

    # 3. Rewrite the sidecar: applied prose rows drop, suggested rows move
    # to their own file, unresolved prose rows stay (deduped), unparseable
    # lines preserved verbatim.
    triage = build_triage(
        Path(SIDECAR).read_text(encoding="utf-8").splitlines()
        if Path(SIDECAR).exists() else [], set())
    kept = [r for r in triage["prose"]
            if (r["edge_type"], r["source"], r["target_mention"])
            not in plan["applied_keys"]]
    Path(SIDECAR).write_text(
        "".join(json.dumps({
            "edge_type": r["edge_type"], "source": r["source"],
            "target_mention": r["target_mention"], "quote": r["quote"],
            "edition": r["edition"],
        }, ensure_ascii=False) + "\n" for r in kept)
        + "\n".join(triage["unparseable"])
        + ("\n" if triage["unparseable"] else ""),
        encoding="utf-8")
    _move_suggestions(triage["suggested"])

    # 4. Drop decided rows (accept + discard) from the suggestions file —
    # both populations exit through the same decisions workflow.
    _drop_decided_suggestions(plan["applied_keys"])

    print(f"sidecar rewritten: {len(kept)} prose rows remain")
    if plan["stubs"]:
        print("\nSTUB PLAN (create explicitly — the collision-check "
              "discipline; then re-run extract):")
        for d in plan["stubs"]:
            print(f"  - {d['source']} {d['edge_type']} -> "
                  f"{d['target_mention']}"
                  + (f"  [{d.get('note')}]" if d.get("note") else ""))


def _read_suggestions_rows() -> list[dict]:
    """Parsed rows of the suggestions file (missing file = [])."""
    path = Path(SUGGESTIONS)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def _drop_decided_suggestions(applied_keys: set) -> None:
    """Rewrite the suggestions file without rows whose
    (edge_type, source, target_mention) key was decided."""
    rows = _read_suggestions_rows()
    kept = [r for r in rows
            if (r.get("edge_type", ""), r.get("source", ""),
                r.get("target_mention", "")) not in applied_keys]
    if len(kept) != len(rows):
        Path(SUGGESTIONS).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept),
            encoding="utf-8")
        print(f"suggestions file: {len(rows) - len(kept)} decided rows "
              f"dropped ({len(kept)} remain)")


def _write_accepted_edges(accepts: list[dict]) -> None:
    """INSERT OR IGNORE the accepted edge specs into graph_edges."""
    from helpers.core.db import connect

    conn = connect(EDGE_DB_PATH)
    inserted = skipped = 0
    try:
        # Same atomicity discipline as extract_relations' U2 bundle: one
        # transaction, so a mid-batch failure leaves nothing committed.
        with conn:
            for a in accepts:
                props = json.dumps(a["properties"], ensure_ascii=False,
                                   sort_keys=True)
                try:
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO graph_edges
                            (source, target, edge_type, properties, source_ref,
                             symmetric, valid_from)
                        VALUES (?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (a["source"], a["target"], a["edge_type"], props,
                         _ACCEPT_SOURCE_REF, 1 if a["symmetric"] else 0),
                    )
                    inserted += cur.rowcount
                except Exception as exc:
                    print(f"warning: skipped accept {a['source']} → "
                          f"{a['target']} ({a['edge_type']}): "
                          f"{type(exc).__name__}: {exc}", file=sys.stderr)
                    skipped += 1
    finally:
        conn.close()
    print(f"graph_edges: {inserted} accepted edge(s) written "
          f"({skipped} skipped)")


def _move_suggestions(suggested: list[dict]) -> None:
    """Move link-prediction rows to the suggestions file (pair-deduped
    against its existing content)."""
    sug_keys: set[str] = set()
    if Path(SUGGESTIONS).exists():
        for line in Path(SUGGESTIONS).read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                sug_keys.add(f"{d.get('source')}\x1f{d.get('target_mention')}")
            except ValueError:
                pass
    fresh = [r for r in suggested
             if f"{r['source']}\x1f{r['target_mention']}" not in sug_keys]
    if fresh:
        with Path(SUGGESTIONS).open("a", encoding="utf-8") as f:
            for r in fresh:
                f.write(json.dumps({
                    "edge_type": "suggested", "source": r["source"],
                    "target_mention": r["target_mention"],
                    "quote": r["quote"], "edition": r["edition"],
                }, ensure_ascii=False) + "\n")
    print(f"{len(fresh)} suggested rows -> {Path(SUGGESTIONS).name}")


def apply_decisions(decisions_path: Path, write: bool,
                    entity_names: set[str]) -> int:
    """Validate + act on annotated decisions. Returns 0 ok, 1 failure."""
    rows = _read_decisions(decisions_path)
    plan = _validate_decisions(rows, entity_names)
    if plan is None:
        return 1

    print(f"decisions: {len(rows)} rows "
          f"(discard={plan['discard']} skip={plan['skip']} "
          f"alias={len(plan['aliases'])} stub={len(plan['stubs'])} "
          f"accept={len(plan['accepts'])})")

    if not write:
        print("(dry-run: no files written)")
    else:
        _apply_write(plan)
    _print_followups(bool(plan["stubs"]))
    return 0


def _print_followups(has_stubs: bool) -> None:
    print("\nfollow-up chain:")
    print("  python3 helpers/graph/extract_relations.py findata --apply")
    if has_stubs:
        print("  (after stub creation) python3 "
              "helpers/maintenance/sync_sector_wikilinks.py")
    print("  make graph-rebuild")
    print("  make snapshot   # or the maint-full wrap-up")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--report", action="store_true",
                   help="build the triage report + decisions file (default)")
    p.add_argument("--apply-decisions", action="store_true",
                   help="act on annotated decisions in the decisions file")
    p.add_argument("--decisions", default=str(DECISIONS),
                   help=f"decisions jsonl path (default: {DECISIONS})")
    p.add_argument("--write", action="store_true",
                   help="with --apply-decisions: actually write files "
                        "(sidecar rewrite, alias file, suggestions file)")
    p.add_argument("--clear", action="store_true",
                   help="truncate the sidecar to 0 (post-triage endgame)")
    args = p.parse_args(argv)

    if args.clear:
        Path(SIDECAR).write_text("", encoding="utf-8")
        print(f"cleared {SIDECAR}")
        return 0
    if args.apply_decisions:
        return apply_decisions(Path(args.decisions), args.write,
                               load_entity_names())

    names = load_entity_names()
    triage = build_triage(
        Path(SIDECAR).read_text(encoding="utf-8").splitlines()
        if Path(SIDECAR).exists() else [], names)
    write_report(triage, len(names))
    print(f"report -> {REPORT}")
    print(f"decisions -> {DECISIONS}")
    print(f"{len(triage['suggested'])} suggested | {len(triage['prose'])} "
          f"prose | {triage['dupes']} dupes absorbed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
