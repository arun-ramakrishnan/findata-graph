#!/usr/bin/env python3
"""
Triage the `findata/Misc/_pending_relations.txt` review queue (pending_relations_triage).

The sidecar is where extract_relations parks pattern matches whose target
entity couldn't be resolved, and where suggest_relations --append dumps its
link-prediction candidates. Left alone it conflates two populations,
accrues append-only duplicates, and fills with deterministic noise
(countries, generic phrases, mangled fragments) — the third full-manual
triage in a month motivated encapsulating the whole workflow here:

  --report (default)  dedupe + split populations + bucket prose rows,
                      emit the eyeball report and an annotated-ready
                      decisions file. NON-destructive.
  --apply-decisions   validate + act on annotated decisions: alias rows
                      persist to findata/Misc/relation_aliases.json
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
SIDECAR = _REPO_ROOT / "findata" / "Misc" / "_pending_relations.txt"
SUGGESTIONS = _REPO_ROOT / "findata" / "Misc" / "_pending_suggestions.txt"
ALIAS_FILE = _REPO_ROOT / "findata" / "Misc" / "relation_aliases.json"
# G3: runtime-loaded discard gate. (edge_type, source, target_mention)
# triples the operator has explicitly rejected — consulted by
# extract_relations at write time so plain discards do NOT re-enter
# the sidecar on the next full-corpus extract (the #169 / #217
# re-entry lesson: 10 discarded noise rows came straight back).
NOISE_FILE = _REPO_ROOT / "findata" / "Misc" / "relation_noise.json"
REPORT = _REPO_ROOT / "findata" / "Misc" / "_pending_triage_report.md"
REVIEW_JOURNAL_DIR = _REPO_ROOT / "outputs" / "relations_review"
DECISIONS = _REPO_ROOT / "findata" / "Misc" / "_pending_triage_decisions.jsonl"
# graph_edges write target for `accept:` decisions. None = connect()'s
# default (memory/research.db); tests point it at a tmp schema file.
EDGE_DB_PATH: Path | None = None

# Vocabulary an accept: decision may assign (the graph's relation edge
# types — roster bookkeeping types part_of/has_company are excluded: a
# suggested company↔company pair is never a roster edge).
_ACCEPT_EDGE_TYPES = frozenset(
    {
        "competes_with",
        "jv_with",
        "supplier_to",
        "customer_of",
        "acquired",
        "subsidiary_of",
        "same_group",
        "co_mentioned_in",
        "semantic_peer",
        "invested_in",
        "exposed_to",
        "cited_in",
    }
)
# Stored canonicalised as ONE row with symmetric=1 (graph_design §4 — the
# same set extract_relations writes with the flag).
_SYMMETRIC_ACCEPT_TYPES = frozenset(
    {
        "jv_with",
        "same_group",
        "competes_with",
        "co_mentioned_in",
        "semantic_peer",
    }
)
_ACCEPT_SOURCE_REF = "triage:accept"

# Deterministic noise classifiers (must stay in sync with the extractor's
# write-time gate — the goal is that post-S2 these rows never reach the
# sidecar; the classifiers here still clean whatever slipped in before).
COUNTRIES = {
    "india",
    "japan",
    "germany",
    "china",
    "usa",
    "us",
    "u.s.",
    "uk",
    "u.k.",
    "france",
    "switzerland",
    "netherlands",
    "singapore",
    "uae",
    "dubai",
    "italy",
    "sweden",
    "korea",
    "south korea",
    "europe",
    "america",
    "australia",
    "russia",
    "brazil",
    "thailand",
    "malaysia",
    "indonesia",
    "vietnam",
    "ukraine",
    "israel",
    "taiwan",
    "hong kong",
    "canada",
    "mexico",
    "spain",
    "denmark",
    "finland",
    "belgium",
    "austria",
    "norway",
    "poland",
    "turkey",
    "egypt",
    "south africa",
    "nigeria",
    "kenya",
    "bangladesh",
    "pakistan",
    "nepal",
    "sri lanka",
    "ecuador",
    "oman",
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
_FRAGMENT_JUNK = re.compile(r"\s(?:but|we|earlier|effectively|now|since|by|and now|operational)\b")
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
        not t
        # Institution short-names are exempt from the fragment-length rule
        # ("RBI" is 3 chars): regulator mentions must reach the sidecar,
        # not die silently (country layer arc I1). Exact-name only — the
        # rest of the fragment class is untouched.
        or (len(tl) < 4 and tl not in _INSTITUTION_SHORT_NAMES)
        or tl in COUNTRIES
        or bool(_GENERIC_PREFIX.match(tl))
        or bool(_GENERIC_SUFFIX.search(tl))
        or bool(_FRAGMENT_JUNK.search(tl))
    )


# Exact-normalized exemption set for noise_target's length rule. Mirrors
# the resolver allowlist in extract_relations.INSTITUTION_MENTIONS (keys,
# lowercased); extend both together.
_INSTITUTION_SHORT_NAMES = frozenset({"rbi", "sebi"})


def _row_id(edge_type: str, source: str, target: str) -> str:
    """Stable short id for a (edge, source, target) — the decisions key."""
    h = hashlib.sha256(f"{edge_type}\x1f{source}\x1f{target}".encode()).hexdigest()
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


def _norm_target_lower(t: str) -> str:
    """Lowercased, boundary-stripped mention — the noise-gate key
    form (mirrors extract_relations._norm_target_lower so the gate
    key matches between write-time consultation and discard
    persistence)."""
    return t.strip().rstrip(".,;:").lower()


def load_entity_names(conn=None) -> set[str]:
    """Distinct entity names from the live DB (monkeypatchable in tests).

    Country entities are EXCLUDED: a country named "india" is a substring
    of half the banking sector ("Bank of India"), so it must never become
    an alias-candidate target (country layer C1 guard — the same
    word-overlap containment family #218 flags).
    """
    own = conn is None
    if own:
        from helpers.core.db import connect

        conn = connect()
    try:
        return {
            r[0] for r in conn.execute("SELECT name FROM entities WHERE entity_type != 'country'")
        }
    finally:
        if own:
            conn.close()


def _bucket(edge_type: str, target: str, names: set[str]) -> tuple[str, str, bool]:
    """(bucket, detail, word_overlap) for one prose row's target.

    Deterministic, advisory. ``word_overlap`` is True only for
    alias_candidates whose fuzzy method is ``word_overlap`` — the known
    false-positive family (20 Microns, Sailing_the_Tide, Circle,
    American_Express: shared tokens say "same sector", not "same
    company"). Such rows render with a ``_confirm?_`` marker in the
    report so the operator never accepts one by accident; genuine
    re-spelling aliases (spellfix / jaccard-neighbour) stay plain.
    """
    t = _norm_target(target)
    tl = t.lower()
    if noise_target(t):
        return "discard", "country/generic/fragment", False
    # Alias candidate: the fuzzy matcher resolves it to a DIFFERENT
    # existing name (exact-cased match means the extractor should already
    # have resolved it, so only report genuine re-spellings).
    match, method, score = fuzzy_match(t, sorted(names))
    if match and match.lower() != tl and method != "spellfix":
        return (
            "alias_candidate",
            f"{match} ({method}, {score:.2f})",
            method == "word_overlap",
        )
    # Stub candidate: >=2 tokens, all name-shaped (capitalized or joiners).
    words = t.split()
    joiners = {"of", "the", "and", "de", "der", "van"}
    if len(words) >= 2 and all(w[0].isupper() or w.lower() in joiners for w in words if w):
        return "stub_candidate", "name-shaped", False
    return "manual", "", False


def build_triage(lines: list[str], names: set[str]) -> dict:
    """Parse + dedupe + split + bucket. Pure function of (lines, names).

    Returns {"suggested": [...], "prose": [...], "unparseable": [...],
    "dupes": n} where each row dict carries id/edge_type/source/
    target_mention/quote/edition/direction/bucket/detail."""
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
            "id": _row_id(d.get("edge_type", ""), d.get("source", ""), d.get("target_mention", "")),
            "edge_type": d.get("edge_type", ""),
            "source": d.get("source", ""),
            "target_mention": d.get("target_mention", ""),
            "quote": (d.get("quote") or "")[:160],
            "edition": d.get("edition", ""),
            # Pre-2026-09-05 rows lack the flag; legacy rows are forward.
            "direction": d.get("direction", "forward"),
        }
        if row["edge_type"] == "suggested":
            suggested.append(row)
        else:
            bucket, detail, word_overlap = _bucket(row["edge_type"], row["target_mention"], names)
            if row["source"] and row["source"] not in names:
                bucket, detail = "bad_source", "source not an entity"
            row["bucket"] = bucket
            row["detail"] = detail
            row["word_overlap"] = word_overlap
            prose.append(row)
    return {"suggested": suggested, "prose": prose, "unparseable": unparseable, "dupes": dupes}


def write_report(triage: dict, names: set[str]) -> None:  # noqa: C901
    """``names`` is the full canonical-name set (VSS hints need it; the
    count in the report header is len(names))."""
    """Emit the eyeball report + the decisions file (non-destructive).

    NOTE: the decisions file is REGENERATED here — annotate only after the
    last --report run, or annotations are lost."""
    lines = [
        "# Pending-relations triage report",
        "",
        f"- entities in DB: {len(names)}",
        f"- suggested rows (link-prediction dump): {len(triage['suggested'])}",
        f"- prose rows (true queue): {len(triage['prose'])}",
        f"- duplicate lines absorbed: {triage['dupes']}",
        f"- unparseable lines (kept verbatim on rewrite): {len(triage['unparseable'])}",
        "",
    ]

    # S7 follow-up: VSS hints for unresolved prose targets (eyeball-only).
    # Semantic neighbors are frequently competitors (the embedding matches
    # the domain, not the identity), so hints are NEVER pre-filled as
    # decisions — D4 holds here exactly as in triage_pending_quotes.
    vss_cache: dict[str, str] = {}
    try:
        from helpers.core.get_tickers import vss_match as _vss_match

        names_sorted = sorted(names)
        for r in triage["prose"]:
            tm = r.get("target_mention", "")
            if tm and tm not in vss_cache:
                match, score = _vss_match(tm, names_sorted)
                if match:
                    vss_cache[tm] = f"{match} ({score:.2f})"
    except Exception as exc:  # best-effort hint — never blocks the report
        print(f"WARNING: VSS hints unavailable ({exc})", file=sys.stderr)
    sug_rows = _read_suggestions_rows()
    if sug_rows:
        top = sorted(sug_rows, key=lambda r: float(r.get("score") or 0), reverse=True)
        lines.append(
            f"- suggestions file (`{Path(SUGGESTIONS).name}`): {len(sug_rows)} rows — top by score:"
        )
        for r in top[:10]:
            lines.append(
                f"  - {r.get('source')} <-> {r.get('target_mention')} "
                f"({r.get('score')}, {r.get('method')})"
            )
        lines.append("")
    from collections import Counter

    counts = Counter(r["bucket"] for r in triage["prose"])
    wo_count = sum(1 for r in triage["prose"] if r.get("word_overlap"))
    lines.append("| bucket | rows |")
    lines.append("|---|---|")
    for b, n in counts.most_common():
        lines.append(f"| {b} | {n} |")
    if wo_count:
        lines.append(f"| _word-overlap alias (confirm before accepting)_ | {wo_count} |")
    lines.append("")
    order = ["discard", "alias_candidate", "stub_candidate", "manual", "bad_source"]
    for b in order:
        rows = [r for r in triage["prose"] if r["bucket"] == b]
        if not rows:
            continue
        lines.append(f"## {b} ({len(rows)})")
        lines.append("")
        for r in rows:
            lines.append(
                f"- `{r['id']}` **{r['edge_type']}** {r['source']} -> "
                + (" _[confirm? word-overlap alias]_ " if r.get("word_overlap") else "")
                + f"**{r['target_mention']}**"
                + (
                    " _[captured reversed: the mention is the edge SOURCE]_"
                    if r.get("direction") == "reverse"
                    else ""
                )
                + (f" _({r['detail']})_" if r["detail"] else "")
            )
            lines.append(f"  > {r['quote']}")
        lines.append("")
    lines.append(
        "## suggested rows on the sidecar (moved out on --write; "
        "triage the suggestions file itself via accept:/discard)"
    )
    lines.append("")
    for r in triage["suggested"][:50]:
        lines.append(f"- {r['source']} <-> {r['target_mention']}")
    if len(triage["suggested"]) > 50:
        lines.append(f"- … and {len(triage['suggested']) - 50} more")
    Path(REPORT).write_text("\n".join(lines) + "\n", encoding="utf-8")

    with Path(DECISIONS).open("w", encoding="utf-8") as f:
        for r in triage["prose"]:
            f.write(
                json.dumps(
                    {
                        "id": r["id"],
                        "edge_type": r["edge_type"],
                        "source": r["source"],
                        "target_mention": r["target_mention"],
                        "direction": r["direction"],
                        "bucket": r["bucket"],
                        "word_overlap": r.get("word_overlap", False),
                        "vss_hint": vss_cache.get(r.get("target_mention", ""), ""),
                        "decision": None,
                        "note": None,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def _read_decisions(path: Path) -> list[dict]:
    rows = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
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
    plan: dict = {
        "aliases": {},
        "applied_keys": set(),
        "stubs": [],
        "accepts": [],
        "discard": 0,
        "skip": 0,
        "rows": rows,  # raw decision dicts — _merge_noise_file reads them
    }
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
            target = decision[len("alias:") :].strip()
            if target not in entity_names:
                print(
                    f"ERROR: alias target {target!r} is not an existing entity (row {d['id']})",
                    file=sys.stderr,
                )
                return None
            plan["aliases"][_norm_target(d["target_mention"]).lower()] = target
        elif decision.startswith("accept:"):
            parsed = _parse_accept(d, decision, entity_names)
            if parsed is None:
                return None
            plan["accepts"].append(parsed)
        else:
            print(f"ERROR: unknown decision {decision!r} (row {d['id']})", file=sys.stderr)
            return None
        plan["applied_keys"].add(key)
    return plan


def _parse_accept(d: dict, decision: str, entity_names: set[str]) -> dict | None:
    """Validate one `accept:<edge_type>[:<Target Entity>]` decision into a
    writable edge spec (source/target/edge_type/properties/symmetric)."""
    parts = decision[len("accept:") :].split(":", 1)
    edge_type = parts[0].strip()
    if edge_type not in _ACCEPT_EDGE_TYPES:
        print(
            f"ERROR: accept edge_type {edge_type!r} is not in the relation "
            f"vocabulary {sorted(_ACCEPT_EDGE_TYPES)} (row {d['id']})",
            file=sys.stderr,
        )
        return None
    # Explicit target override for mangled mentions; otherwise the row's own
    # target_mention must BE the entity (link-prediction rows always are).
    target = parts[1].strip() if len(parts) > 1 else _norm_target(d["target_mention"])
    if target not in entity_names:
        print(
            f"ERROR: accept target {target!r} is not an existing entity "
            f"(row {d['id']}) — stub it first, or name an existing one",
            file=sys.stderr,
        )
        return None
    if d["source"] not in entity_names:
        print(
            f"ERROR: accept source {d['source']!r} is not an existing entity (row {d['id']})",
            file=sys.stderr,
        )
        return None
    properties: dict = {"edition": d.get("edition", ""), "origin": d.get("origin", "manual_triage")}
    if d.get("score") is not None:
        properties["score"] = d["score"]
    if d.get("method"):
        properties["method"] = d["method"]
    # Direction-aware (2026-09-05): reverse captures ("parent company of X",
    # "acquired by X", "sources from X") make the MENTION the edge source;
    # swap so `accept:` writes the edge in the orientation the extractor
    # would have. Symmetric types canonicalise downstream (no-op there).
    source, target = d["source"], target
    if d.get("direction", "forward") == "reverse":
        source, target = target, source
    return {
        "source": source,
        "target": target,
        "edge_type": edge_type,
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
            print("WARNING: relation_aliases.json unreadable — replacing", file=sys.stderr)
    existing.update(aliases)
    Path(ALIAS_FILE).write_text(
        json.dumps(dict(sorted(existing.items())), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if aliases:
        print(f"relation_aliases.json: +{len(aliases)} entries (now {len(existing)})")

    # 2. Accepted edges -> graph_edges (same INSERT discipline as
    # extract_relations: INSERT OR IGNORE + per-row integrity skips;
    # idempotent via the UNIQUE constraint).
    if plan["accepts"]:
        _write_accepted_edges(plan["accepts"])

    # 3. Rewrite the sidecar: applied prose rows drop, suggested rows move
    # to their own file, unresolved prose rows stay (deduped), unparseable
    # lines preserved verbatim.
    triage = build_triage(
        Path(SIDECAR).read_text(encoding="utf-8").splitlines() if Path(SIDECAR).exists() else [],
        set(),
    )
    kept = [
        r
        for r in triage["prose"]
        if (r["edge_type"], r["source"], r["target_mention"]) not in plan["applied_keys"]
    ]
    Path(SIDECAR).write_text(
        "".join(
            json.dumps(
                {
                    "edge_type": r["edge_type"],
                    "source": r["source"],
                    "target_mention": r["target_mention"],
                    "quote": r["quote"],
                    "edition": r["edition"],
                    "direction": r.get("direction", "forward"),
                },
                ensure_ascii=False,
            )
            + "\n"
            for r in kept
        )
        + "\n".join(triage["unparseable"])
        + ("\n" if triage["unparseable"] else ""),
        encoding="utf-8",
    )
    _move_suggestions(triage["suggested"])

    # 4. Drop decided rows (accept + discard) from the suggestions file —
    # both populations exit through the same decisions workflow.
    _drop_decided_suggestions(plan["applied_keys"])

    # 5. G3: persist discard decisions as the runtime noise gate so plain
    # discards do NOT re-enter the sidecar on the next full-corpus
    # extract (the #169 / #217 re-entry lesson: 10 discarded noise rows
    # came straight back). Aliases / stubs / accepts are handled by their
    # own writers above; only `discard` rows are noise-gate material.
    _merge_noise_file(plan)

    print(f"sidecar rewritten: {len(kept)} prose rows remain")
    if plan["stubs"]:
        print(
            "\nSTUB PLAN (create explicitly — the collision-check discipline; then re-run extract):"
        )
        for d in plan["stubs"]:
            print(
                f"  - {d['source']} {d['edge_type']} -> "
                f"{d['target_mention']}" + (f"  [{d.get('note')}]" if d.get("note") else "")
            )


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
    kept = [
        r
        for r in rows
        if (r.get("edge_type", ""), r.get("source", ""), r.get("target_mention", ""))
        not in applied_keys
    ]
    if len(kept) != len(rows):
        Path(SUGGESTIONS).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8"
        )
        print(
            f"suggestions file: {len(rows) - len(kept)} decided rows dropped ({len(kept)} remain)"
        )


def _merge_noise_file(plan: dict) -> None:
    """G3: persist `discard` decisions into the runtime noise gate.

    extract_relations consults this at write time, so plain discards do
    NOT re-enter the sidecar on the next full-corpus extract. Aliases /
    stubs / accepts are not noise-gate material (they resolve or create
    real edges), so only `discard` rows land here. Merged, deduped, sorted
    for stable diffs — same discipline as the alias file.
    """
    discards = {
        (d["edge_type"], d["source"], _norm_target_lower(d["target_mention"]))
        for d in plan["rows"]
        if d.get("decision") == "discard"
    }
    if not discards:
        return
    existing: set[tuple[str, str, str]] = set()
    if Path(NOISE_FILE).exists():
        try:
            for item in json.loads(Path(NOISE_FILE).read_text(encoding="utf-8")):
                if isinstance(item, (list, tuple)) and len(item) == 3:
                    existing.add((str(item[0]), str(item[1]), str(item[2])))
        except OSError, ValueError:
            pass
    existing |= discards
    Path(NOISE_FILE).write_text(
        json.dumps(sorted(existing), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"relation_noise.json: +{len(discards)} discard gate entries (now {len(existing)})")


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
                props = json.dumps(a["properties"], ensure_ascii=False, sort_keys=True)
                try:
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO graph_edges
                            (source, target, edge_type, properties, source_ref,
                             symmetric, valid_from)
                        VALUES (?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (
                            a["source"],
                            a["target"],
                            a["edge_type"],
                            props,
                            _ACCEPT_SOURCE_REF,
                            1 if a["symmetric"] else 0,
                        ),
                    )
                    inserted += cur.rowcount
                except Exception as exc:
                    print(
                        f"warning: skipped accept {a['source']} → "
                        f"{a['target']} ({a['edge_type']}): "
                        f"{type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                    skipped += 1
    finally:
        conn.close()
    print(f"graph_edges: {inserted} accepted edge(s) written ({skipped} skipped)")


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
    fresh = [r for r in suggested if f"{r['source']}\x1f{r['target_mention']}" not in sug_keys]
    if fresh:
        with Path(SUGGESTIONS).open("a", encoding="utf-8") as f:
            for r in fresh:
                f.write(
                    json.dumps(
                        {
                            "edge_type": "suggested",
                            "source": r["source"],
                            "target_mention": r["target_mention"],
                            "quote": r["quote"],
                            "edition": r["edition"],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    print(f"{len(fresh)} suggested rows -> {Path(SUGGESTIONS).name}")


def apply_decisions(decisions_path: Path, write: bool, entity_names: set[str]) -> int:
    """Validate + act on annotated decisions. Returns 0 ok, 1 failure.

    The CLI always passes ``write=True`` (--apply-decisions implies write);
    library callers can pass False for a validate-only pass.
    """
    rows = _read_decisions(decisions_path)
    plan = _validate_decisions(rows, entity_names)
    if plan is None:
        return 1

    print(
        f"decisions: {len(rows)} rows "
        f"(discard={plan['discard']} skip={plan['skip']} "
        f"alias={len(plan['aliases'])} stub={len(plan['stubs'])} "
        f"accept={len(plan['accepts'])})"
    )

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
        print("  (after stub creation) python3 helpers/maintenance/sync_sector_wikilinks.py")
    print("  make graph-rebuild")
    print("  make snapshot   # or the maint-full wrap-up")


# --------------------------------------------------------------------------- #
# review — journaled sitting over the open queue (review-kit proposal S2)     #
# --------------------------------------------------------------------------- #
_REVIEW_FILE_FIELDS = (
    "id",
    "edge_type",
    "source",
    "target_mention",
    "direction",
    "bucket",
    "word_overlap",
    "vss_hint",
)


def _entity_lookup(query: str, names: set[str]) -> tuple[str | None, list[str]]:
    """Resolve an alias target against entity names: exact
    (case-insensitive) hit, else substring (space-stripped too) +
    acronym-initials candidates (``ongc`` -> ``Oil & Natural Gas
    Corporation``)."""
    entities = {n.lower(): n for n in names}
    q = query.lower()
    if q in entities:
        return entities[q], []
    cands = {
        name
        for name in names
        if q in name.lower() or q.replace(" ", "") in name.lower().replace(" ", "")
    }
    # token-prefix (1-char stem): every query token prefixes some name
    # token, optionally minus its last char ('exide industry' ->
    # 'Exide Industries' — y->ies and trailing-s plurals)
    qt = [t for t in q.split() if len(t) >= 4]
    if qt:  # guard: all() over no tokens would vacuously match everything
        for name in names:
            nt = [t for t in name.lower().split() if len(t) >= 3]
            if all(
                any(n.startswith(t) or (len(t) >= 4 and n.startswith(t[:-1])) for n in nt)
                for t in qt
            ):
                cands.add(name)
    if " " not in q:
        for name in names:
            initials = "".join(w[0] for w in name.split() if w.isalpha())
            if q == initials.lower():
                cands.add(name)
    return None, sorted(cands)[:8]


def review(  # noqa: C901 — keypress parsing + kit wiring, split would scatter state
    *,
    limit: int | None = None,
    input_fn=input,
    print_fn=print,
    apply: bool = True,
    journal_dir: Path | None = None,
    redecide: bool = False,
    skipped: bool = False,
) -> dict:
    """Interactive sitting over the open prose queue (review-kit S2).

    One keypress per row — the sitting only PRODUCES decision rows;
    the confirm gate appends them to the decisions file, and apply stays
    the existing ``--apply-decisions`` step (unchanged validator,
    unchanged writes; the file is no longer hand-edited):

      a[/EDGE_TYPE[/TARGET]]  accept (target entity-validated; override
                              the edge type / a mangled mention target)
      d                       reject -> noise gate (discard)
      al NAME                 alias the mention to existing entity NAME
      st                      stub (entity note via the follow-up chain)
      p                       park — never re-asked (journal read-back)
      ?                       re-render evidence
      q / x                   quit-and-review / abort the walk

    Any verb may carry ``| free note`` — it lands in the row's note field.
    Parking keys on the stable row id (``_row_id``); already-decided rows
    (decisions file read-back) are excluded unless ``--redecide``.
    """
    from helpers.core.review_kit import Journal, ReviewSession, assemble_entries, latest_action_by

    names = load_entity_names()
    journal_path = (journal_dir or REVIEW_JOURNAL_DIR) / "journal.jsonl"
    triage = build_triage(
        Path(SIDECAR).read_text(encoding="utf-8").splitlines() if Path(SIDECAR).exists() else [],
        names,
    )
    decided_rows: list[dict] = []
    decided_keys: set[tuple[str, str, str]] = set()
    if Path(DECISIONS).exists():
        for d in _read_decisions(Path(DECISIONS)):
            decided_keys.add((d["edge_type"], d["source"], d["target_mention"]))
            decided_rows.append(d)
    parked_ids = latest_action_by(journal_path, key_field="id")

    def _key(r: dict) -> tuple[str, str, str]:
        return (r["edge_type"], r["source"], r["target_mention"])

    def _entry(r: dict) -> dict:
        e = dict(r)
        e.setdefault("vss_hint", "")
        e["label"] = r["id"]  # kit item key == stable row id
        return e

    open_rows = [r for r in triage["prose"] if _key(r) not in decided_keys]
    wl = {
        "suggested": [_entry(r) for r in open_rows if r["id"] not in parked_ids],
        "promoted": [_entry(r) for r in decided_rows],
        "skipped": [_entry(r) for r in open_rows if r["id"] in parked_ids],
        "no_signal": [],
    }
    _bucket_rank = {
        "manual": 0,
        "alias_candidate": 1,
        "stub_candidate": 2,
        "discard": 3,
        "bad_source": 4,
    }
    entries = assemble_entries(
        wl,
        labels_filter=None,
        redecide=redecide,
        skipped=skipped,
        sort_key=lambda e: (_bucket_rank.get(e.get("bucket", ""), 9), e["label"]),
        limit=limit,
    )

    legend = [
        "  keys: a[/TYPE[/TARGET]]  accept (row's edge type + target by default;",
        "                             override either for mangled mentions — a//kec",
        "                             keeps the type and DB-resolves the target)",
        "        d   reject -> noise gate    al NAME  alias mention to entity NAME",
        "        st  stub (entity via follow-up chain)    p  park — reopen via --skipped",
        "        ?   this legend + evidence    q/x  quit-and-review / abort walk",
        "        any verb may carry '| free note' — lands in the row's note field",
    ]
    if entries:
        print_fn("\n".join(legend))

    def _evidence(e: dict) -> None:
        flags = []
        if e.get("word_overlap"):
            flags.append("word-overlap alias — confirm")
        if e.get("direction") == "reverse":
            flags.append("captured reversed: mention is the edge source")
        if flags:
            print_fn("     [" + "; ".join(flags) + "]")
        if e.get("detail"):
            print_fn(f"     {e['detail']}")
        if e.get("vss_hint"):
            print_fn(f"     vss: {e['vss_hint']}")
        if e.get("quote"):
            print_fn(f"     > {e['quote']}")
        elif e.get("decision"):
            print_fn(f"     (previous: {e['decision']})")

    def render(idx: int, total: int, e: dict) -> None:
        hdr = (
            f"[{idx}/{total}] `{e['id']}` {e['source']} —[{e['edge_type']}]→ {e['target_mention']}"
        )
        if e.get("bucket"):
            hdr += f"  ({e['bucket']})"
        print_fn(hdr)
        _evidence(e)

    def ask(e: dict, note) -> dict:
        rid = e["id"]
        while True:
            ans = input_fn(
                "  accept a[/TYPE[/TARGET]] / d / al NAME / st / p / ? / q / x: "
            ).strip()
            ans, _, free_note = ans.partition("|")
            ans, free_note = ans.strip(), free_note.strip()
            if ans in {"p", "q", "x"}:
                return {"id": rid, "action": {"p": "skip", "q": "quit", "x": "abort"}[ans]}
            if ans == "?":
                print_fn("\n".join(legend))
                _evidence(e)
                continue
            file_decision = None
            if ans == "d":
                file_decision = "discard"
            elif ans == "st":
                file_decision = "stub"
            elif ans.startswith("al ") or ans == "al":
                query = ans[3:].strip()
                if not query:
                    print_fn("  al <part of name> — exact, substring, or acronym (e.g. al ongc)")
                    continue
                target, cands = _entity_lookup(query, names)
                if target is None and len(cands) == 1:
                    target = cands[0]
                    print_fn(f"  matched: {target}")
                if target is None:
                    if not cands:
                        print_fn(f"  no entity matches {query!r} — try another spelling or stub")
                        note({"id": rid, "action": "bad-alias", "target": query})
                        continue
                    print_fn("  candidates:")
                    for i, c in enumerate(cands, 1):
                        print_fn(f"    {i}) {c}")
                    pick = input_fn("  pick 1-8 / or al <name> again: ").strip()
                    if pick.isdigit() and 1 <= int(pick) <= len(cands):
                        target = cands[int(pick) - 1]
                    elif pick.startswith("al "):
                        target, c2 = _entity_lookup(pick[3:].strip(), names)
                        if target is None and len(c2) == 1:
                            target = c2[0]
                    if target is None:
                        print_fn("  ? (pick a number or al <name>)")
                        note({"id": rid, "action": "bad-alias", "target": pick})
                        continue
                file_decision = f"alias:{target}"
            elif ans == "a" or ans.startswith("a/") or ans.startswith("a "):
                parts = ans.split("/")[1:] if ans.startswith("a/") else ans[1:].split()
                edge_type = parts[0].strip() if parts and parts[0].strip() else e["edge_type"]
                target = parts[1].strip() if len(parts) > 1 else None
                if edge_type not in _ACCEPT_EDGE_TYPES or edge_type == "suggested":
                    print_fn(
                        f"  {edge_type!r} is not an accept edge type — try again"
                        f" ({'/'.join(sorted(_ACCEPT_EDGE_TYPES))})"
                    )
                    note({"id": rid, "action": "bad-accept", "edge_type": edge_type})
                    continue
                eff_target = target or _norm_target(e["target_mention"])
                if target is not None and eff_target not in names:
                    # operator-supplied target: resolve via DB lookup
                    # (exact -> substring/acronym/token-prefix; single
                    # candidate auto-matches, numbered pick when ambiguous)
                    resolved, cands = _entity_lookup(target, names)
                    if resolved is None and len(cands) == 1:
                        resolved = cands[0]
                        print_fn(f"  matched: {resolved}")
                    if resolved is None and cands:
                        print_fn("  candidates:")
                        for i, c in enumerate(cands, 1):
                            print_fn(f"    {i}) {c}")
                        pick = input_fn("  pick 1-8 / or a/TYPE/TARGET again: ").strip()
                        if pick.isdigit() and 1 <= int(pick) <= len(cands):
                            resolved = cands[int(pick) - 1]
                    if resolved is None:
                        print_fn(
                            f"  no entity matches {target!r} — full name via a/TYPE/TARGET, or stub"
                        )
                        note({"id": rid, "action": "bad-accept", "target": target})
                        continue
                    eff_target = resolved
                if eff_target not in names:
                    print_fn(
                        f"  target {eff_target!r} is not an existing entity — name one:"
                        " a/TYPE/TARGET, or stub"
                    )
                    note({"id": rid, "action": "bad-accept", "target": eff_target})
                    continue
                if e["source"] not in names:
                    print(f"  source {e['source']!r} is not an entity — row is bad_source")
                    note({"id": rid, "action": "bad-accept", "source": e["source"]})
                    continue
                file_decision = f"accept:{edge_type}" + (
                    f":{eff_target}" if target is not None else ""
                )
            else:
                print_fn("  ? (a[/TYPE[/TARGET]] / d / al NAME / st / p / ? / q / x)")
                continue
            d = {"id": rid, "action": "approve", "file_decision": file_decision, "note": free_note}
            for k in _REVIEW_FILE_FIELDS:
                d[k] = e.get(k, False if k == "word_overlap" else "")
            return d

    def spec_of(d: dict, e: dict) -> str:  # noqa: ARG001 — d carries the row fields
        return f"{d['source']} —[{d['file_decision']}]→ {d['target_mention']}"

    def apply_batch(specs: list[str], decisions: list[dict]) -> tuple[list[str], list[str]]:
        rows = [
            {k: d.get(k, False if k == "word_overlap" else "") for k in _REVIEW_FILE_FIELDS}
            | {"decision": d["file_decision"], "note": d.get("note", "")}
            for d in decisions
            if d.get("action") == "approve"
        ]
        with Path(DECISIONS).open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return (
            [f"{len(rows)} decision row(s) appended -> {Path(DECISIONS).name}"],
            ["next: python3 helpers/graph/triage_pending_relations.py --apply-decisions"],
        )

    return ReviewSession(
        entries=entries,
        journal=Journal(journal_path),
        render=render,
        ask=ask,
        spec_of=spec_of,
        apply_batch=apply_batch,
        batch_header="batch to append to the decisions file:",
        apply_noun="decision row(s)",
        input_fn=input_fn,
        print_fn=print_fn,
        apply_flag=apply,
    ).run()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--report", action="store_true", help="build the triage report + decisions file (default)"
    )
    p.add_argument(
        "--apply-decisions",
        action="store_true",
        help="act on annotated decisions and write the results "
        "(sidecar rewrite, alias file, suggestions file, accepted edges)",
    )
    p.add_argument(
        "--decisions", default=str(DECISIONS), help=f"decisions jsonl path (default: {DECISIONS})"
    )
    p.add_argument(
        "--clear", action="store_true", help="truncate the sidecar to 0 (post-triage endgame)"
    )
    p.add_argument(
        "--review",
        action="store_true",
        help="interactive journaled sitting over the open queue (review-kit S2); "
        "appends annotated rows to the decisions file on confirm",
    )
    p.add_argument("--limit", type=int, default=None, help="review: cap rows per sitting")
    p.add_argument(
        "--redecide", action="store_true", help="review: also re-walk already-decided rows"
    )
    p.add_argument(
        "--skipped", action="store_true", help="review: also re-walk journaled-parked/declined rows"
    )
    p.add_argument(
        "--dry-run", action="store_true", help="review: journal + print the batch, write nothing"
    )
    args = p.parse_args(argv)

    if args.clear:
        Path(SIDECAR).write_text("", encoding="utf-8")
        print(f"cleared {SIDECAR}")
        return 0
    if args.review:
        review(
            limit=args.limit,
            redecide=args.redecide,
            skipped=args.skipped,
            apply=not args.dry_run,
        )
        return 0
    if args.apply_decisions:
        # --apply-decisions writes (the --write co-flag was folded in —
        # shared_routines_cli_guards W1); apply_decisions keeps the write
        # parameter for library callers wanting a validate-only pass.
        return apply_decisions(Path(args.decisions), True, load_entity_names())

    names = load_entity_names()
    triage = build_triage(
        Path(SIDECAR).read_text(encoding="utf-8").splitlines() if Path(SIDECAR).exists() else [],
        names,
    )
    write_report(triage, names)
    print(f"report -> {REPORT}")
    print(f"decisions -> {DECISIONS}")
    print(
        f"{len(triage['suggested'])} suggested | {len(triage['prose'])} "
        f"prose | {triage['dupes']} dupes absorbed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
