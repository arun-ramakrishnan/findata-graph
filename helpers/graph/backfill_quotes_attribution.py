#!/usr/bin/env python3
"""S21 — quotes attribution backfill (proposal hypergraph_incidence_hyx.md).

Re-attributes mis-captured quote rows (``entity`` = 'Quotes', a sector, or
an edition) to their real company/institution. Stages, first match wins:

1. ``ensure_entities`` — create the operator-approved missing entities
   (companies via ``parse_newsletter.create_entity`` — the standard
   stub-note + entity + part_of path; institutions via the
   ``enrich_relations`` INSERT pattern, no note by convention).
2. ``roster`` — speaker_name -> single company across the 7.5k resolved
   quotes (1,268 unambiguous speakers).
3. ``token`` — exactly one company name appears in title+text window,
   matched against the live company vocabulary.
4. ``heading_walk`` — ``quotes.source_ref`` -> edition note -> nearest
   COMPANY section heading (speaker sub-headings skipped; the
   ``[Company | Cap | Sector]`` link-line fallback covers sections whose
   company hides in a link line, e.g. Page Industries).
5. ``speaker_map`` — the curated ``quotes_speaker_map.json`` beside this
   script (operator-evaluated 2026-09-13; evidence + confidence per row).

NON_COMPANY rows (journalists, analysts, regulators, academics, the
``Subtext`` section-opening quotes) stay put by design: the ``Quotes``
super_sector is
the intended catch-all (findata/Super_Sectors/Quotes.md).

Audit: every applied row gets ``properties.reattributed_from`` (original
entity) + ``properties.reattribution_stage``; the UPDATE is idempotent —
rows already pointing at a company-kind entity are never touched.

Usage::

    python3 helpers/graph/backfill_quotes_attribution.py            # dry-run
    python3 helpers/graph/backfill_quotes_attribution.py --apply
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.core.parse_newsletter import create_entity  # noqa: E402

DB_PATH = PROJECT_ROOT / "memory" / "research.db"
MAP_PATH = Path(__file__).with_name("quotes_speaker_map.json")
VAULT = PROJECT_ROOT / "findata"

_SUFFIX = re.compile(r"\b(Limited|Ltd|Inc|Corporation|Corp|Company|Co)[.]?$")
_SPEAKERISH = re.compile(
    r"^(-|—|\[|Management|Team|Source|Reference|TV Interviews|None$)"
    r"|,\s*(MD|CEO|CFO|COO|CTO|Director|Executive|Spokesperson|President|Chairman"
    r"|Chief|EVP|VP|ED|JMD|Governor|Minister|Secretary|Strategist|Fellow|Professor"
    r"|Advisor|Researcher|Postdoctoral|Founder|Head of|Lead|Investor Relations"
    r"|Whole-time|CMD|DMD)"
    r"|\bSubtext\b",
    re.I,
)


def _norm(head: str) -> str:
    head = html.unescape(head or "")
    head = re.split(r"\bon\b|\||:|\(|,|—|-", head)[0].strip()
    prev = None
    while prev != head:
        prev = head
        head = _SUFFIX.sub("", head).strip().rstrip(",").strip()
    head = re.sub(r"^(Dr\.?|Mr\.?|Ms\.?|Shri|Prof\.?)\s+", "", head)
    head = head.replace("'", "").replace("\u2019", "")
    head = re.sub(r"\s*&\s*", " and ", head)
    return re.sub(r"\s+", " ", head).strip()


class EntityIndex:
    """name/normalized-name lookup over the live entities table."""

    def __init__(self, conn):
        self.kinds = {r[0]: r[1] for r in conn.execute("SELECT name, entity_type FROM entities")}
        self.by_norm = {}
        for name, kind in self.kinds.items():
            n = _norm(name)
            if n and len(n) >= 3:
                self.by_norm.setdefault(n.lower(), (kind, name))

    def resolve(self, head: str):
        if not head:
            return None
        nh = _norm(head)
        if not nh or len(nh) < 3:
            return None
        if nh.lower() in self.by_norm:
            return self.by_norm[nh.lower()]
        hit = (nh + " ").lower()
        best = None
        for nn, (kind, name) in self.by_norm.items():
            if hit.startswith(nn + " ") or (nn + " ").startswith(hit):
                if best is None or len(name) < len(best[1]):
                    best = (kind, name)
        return best


def _section_walk(note_lines, line_no, idx):
    heads = [
        (i + 1, m.group(1).strip())
        for i in range(min(line_no - 1, len(note_lines) - 1), -1, -1)
        if (m := re.match(r"^#{1,4} (.+?)\s*$", note_lines[i]))
    ]
    for ln, h in heads:
        if _SPEAKERISH.search(h):
            continue
        fe = idx.resolve(h)
        if fe:
            return fe, h
        for j in range(ln, min(ln + 8, len(note_lines))):
            mm = re.match(r"^\[([^\]|]+)\s*\|", note_lines[j])
            if mm:
                fe2 = idx.resolve(mm.group(1).strip())
                if fe2:
                    return fe2, note_lines[j][:60]
        return None, h
    return None, None


def _load_notes():
    notes = {}
    for p in VAULT.rglob("*.md"):
        if "Super_Sectors" in str(p) or "images" in str(p):
            continue
        notes.setdefault(p.stem, p.read_text(encoding="utf-8", errors="replace").split("\n"))
    return notes


def _prop_merge(old: str | None, new: dict) -> str:
    try:
        base = json.loads(old) if old else {}
        if not isinstance(base, dict):
            base = {}
    except json.JSONDecodeError:
        base = {}
    base.update(new)
    return json.dumps(base, ensure_ascii=False)


def main(argv=None) -> int:  # noqa: C901 — stage dispatcher; each stage is a helper (S21)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="execute (default: dry-run)")
    args = ap.parse_args(argv)

    smap = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    conn = connect(DB_PATH)
    idx = EntityIndex(conn)

    # Stage 1: ensure entities
    created = 0
    for ent in smap["entities"]:
        if ent["name"] in idx.kinds:
            continue
        if ent["kind"] == "company":
            create_entity(conn, ent["name"], ent["sector"], ent["ticker"], apply=args.apply)
        else:
            if args.apply:
                conn.execute(
                    "INSERT OR IGNORE INTO entities (name, entity_type, normalized_name,"
                    " file_path, sector_classification, ticker)"
                    " VALUES (?, 'institution', ?, NULL, NULL, NULL)",
                    (ent["name"], _norm(ent["name"]).replace(" ", "_")),
                )
        created += 1
    if args.apply:
        conn.commit()
        idx = EntityIndex(conn)
    print(
        f"[ensure_entities] {'created' if args.apply else 'would create'} {created} entities"
        f" ({len(smap['entities'])} seeds checked)"
    )

    # Rows in scope: current entity is NOT company-kind (or is 'Quotes').
    rows = conn.execute(
        "SELECT q.id, q.entity, q.speaker_name, q.speaker_title, q.as_of_edition,"
        " q.source_ref, substr(q.quote_text, 1, 400) AS win"
        " FROM quotes q JOIN entities e ON e.name = q.entity"
        " WHERE e.entity_type != 'company' OR q.entity = 'Quotes'"
    ).fetchall()

    roster = {}
    for name, ent in conn.execute(
        "SELECT DISTINCT q.speaker_name, q.entity FROM quotes q"
        " JOIN entities e ON e.name = q.entity"
        " WHERE e.entity_type = 'company' AND q.speaker_name IS NOT NULL"
    ):
        roster.setdefault(name, set()).add(ent)
    roster = {k: next(iter(v)) for k, v in roster.items() if len(v) == 1}

    tokmap = {}
    for (name,) in conn.execute("SELECT name FROM entities WHERE entity_type = 'company'"):
        for w in re.split(r"_| ", name):
            if len(w) > 3 and w.lower() not in (
                "india",
                "limited",
                "ltd",
                "pvt",
                "financial",
                "finance",
            ):
                tokmap.setdefault(w.lower(), set()).add(name)

    notes = _load_notes()
    spk_map = {s["speaker"]: s for s in smap["speakers"]}

    plan = {}
    stats = Counter()
    samples = []
    for r in rows:
        rid, cur, spk, title, ed, ref, win = r
        stage = target = kind = None
        if spk and spk in roster:
            stage, target, kind = "roster", roster[spk], "company"
        else:
            cands = set()
            for w in re.split(r"[^A-Za-z]+", (title or "") + " " + (win or "")):
                if w.lower() in tokmap:
                    cands |= tokmap[w.lower()]
            if len(cands) == 1:
                stage, target, kind = "token", next(iter(cands)), "company"
        if target is None and ref:
            m = re.match(r"derive:quotes:([^:]+):(\d+)$", ref)
            if m and m.group(1) in notes:
                fe, _ = _section_walk(notes[m.group(1)], int(m.group(2)), idx)
                # Sector/sub_sector/edition headings are context, not
                # attribution — only company/institution targets count.
                if fe and fe[0] in ("company", "institution"):
                    stage, target, kind = "heading_walk", fe[1], fe[0]
        if target is None and spk and spk in spk_map:
            entry = spk_map[spk]
            if entry["target"] in idx.kinds and idx.kinds[entry["target"]] in (
                "company",
                "institution",
            ):
                stage, target, kind = "speaker_map", entry["target"], idx.kinds[entry["target"]]
        if target is None:
            stats["unrecovered"] += 1
            continue
        if target == cur:
            stats[f"noop_{stage}"] += 1
            continue
        plan[rid] = (cur, target, kind, stage)
        stats[f"{stage}_{kind}"] += 1
        if len(samples) < 6:
            samples.append((rid, cur, target, stage))

    for rid, cur, target, stage in samples:
        print(f"  sample: #{rid} {cur!r} -> {target!r} [{stage}]")
    print(f"[plan] {len(plan)} rows re-attributed; {dict(stats)}")
    if not args.apply:
        print("dry-run only (pass --apply)")
        return 0

    for rid, (cur, target, kind, stage) in plan.items():
        props = conn.execute("SELECT properties FROM quotes WHERE id = ?", (rid,)).fetchone()
        conn.execute(
            "UPDATE quotes SET entity = ?, properties = ? WHERE id = ?",
            (
                target,
                _prop_merge(
                    props[0] if props else None,
                    {"reattributed_from": cur, "reattribution_stage": stage},
                ),
                rid,
            ),
        )
    conn.commit()
    done = conn.execute(
        "SELECT COUNT(*) FROM quotes q JOIN entities e ON e.name = q.entity"
        " WHERE e.entity_type = 'company'"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    print(
        f"[apply] {len(plan)} rows updated; company-attributed quotes: {done}/{total} = {done / total:.1%}"
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
