#!/usr/bin/env python3
"""Quote capture coverage audit — S0 of the quote-capture proposal
(doc/improvements/proposals/quote_capture_coverage.md, 2026-09-07).

Measures the markdown→notes quote funnel per tree/per note and enforces
the capture tripwire. READ-ONLY: no DB writes, no note edits.

Contract (proposal §3, "covered"):
  denominator = quote-shaped openings (walker-normalized line starts
  with `"` and > 40 chars — the walker's own yardstick, shared helpers);
  covered     = opening matches a `quotes`-table row (80-char normalized
  prefix containment), any entity type; resolved = non-catch-all rows.
  Target: >= 99% corpus; per-note floor 95% (tripwire, S0 amendment).

Buckets (pinned assignment order, first match wins):
  G1 orphaned behind marker headings (bracket/bare-marker regions)
  G2 inside unresolved company sections (resolver misses, worklist)
  G3 inside resolved sections but not extracted (shape misses → S3)
  G4 sector pool (named-sector / speaker / prose regions → S1-family+S4)
  G5 masthead / chrome / preamble

Tripwire + watchlist + rule candidates: proposal §S0 amendment (the S0 salvage pass was absorbed into the production walker by S3 — the rules are now the default extraction behavior).
Classification parity with `derive_insights.iter_company_sections` is
asserted per file (the audit's boundary copy must agree exactly; the
2026-09-07 probe's 23-vs-22 divergence on The_Push_and_Pull.md was a
classifier-copy drift artifact — this copy is parity-gated).

Advisory only — never qa-blocking (same doctrine as search-index checks).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from helpers.graph import derive_insights as di  # noqa: E402

DB_PATH = di.DB_PATH
WATCHLIST_DEFAULT = di.PROJECT_ROOT / "findata" / "quote_coverage_watchlist.json"
TREES = di._NEWSLETTER_TREES
CHROME_FILES = di._NEWSLETTER_CHROME_NAMES

# Marker/speaker/prose predicates: IMPORTED from derive_insights (S1) —
# one source of truth; the audit keeps no drift-prone copies. (The S0-era
# local copies were deleted when S1 landed the family in production; the
# g1_marker/g4_speaker/g4_prose region kinds collapsed with them — post-S1
# that content joins the enclosing company section and the funnel shows it.)


# --------------------------------------------------------------------------- #
# Region classification (parity-gated mirror of iter_company_sections)        #
# --------------------------------------------------------------------------- #
@dataclass
class Region:
    """A maximal run of content between structural boundaries."""

    start_line: int  # 1-based line of the boundary heading (0 = preamble)
    end_line: int  # 1-based, exclusive
    kind: str  # company_resolved | company_unresolved | g1_marker | g4_sector
    #           | g4_speaker | g4_prose | g5_masthead
    heading: str = ""
    canonical: str | None = None


def classify_boundaries(content: str) -> tuple[list[Region], list[dict]]:
    """Mirror `iter_company_sections` classification, keeping ALL regions.

    Returns (regions, parity_records) where parity_records describe each
    company heading exactly as iter_company_sections would yield it
    (canonical, heading_line) — the caller asserts equality against the
    production iterator (parity gate).
    """
    matches = list(di._HEADING_RE.finditer(content))
    structural: list[tuple[int, int, str | None, str]] = []  # start, idx, canonical, raw
    parity: list[dict] = []
    for idx, m in enumerate(matches):
        # Byte-faithful to production: NO html.unescape before
        # canonicalization (parity gate). `&amp;` headings canonicalize with
        # the entity intact; the resolver's normalized_name handles them.
        # Unescape is applied ONLY in region-kind attribution below.
        raw = m.group(2).strip()
        lower = raw.lower()
        # S1 family (mirror of production): markers, speaker headings, prose
        # sub-headings are non-boundaries — content joins the enclosing region.
        if di._is_marker_heading(raw) or di._is_speaker_heading(raw) or di._is_prose_heading(raw):
            continue
        if di._ATTR_DASH_RE.match(raw) or di._ATTR_DASH_CAP_RE.match(raw):
            continue
        if di._ROLE_HEADING_RE.match(lower):
            continue
        if lower.startswith(("comment", "discussion", "don't", "share this", "subscribe", "about ", "welcome")):
            continue
        has_cap = any(tok in lower for tok in di._CAP_TOKENS)
        has_pipe = "|" in raw
        if not (has_cap or has_pipe):
            structural.append((m.start(), idx, None, raw))
            continue
        canonical = di._canonicalize(raw)
        if not canonical or len(canonical) < 3:
            continue
        if canonical.lower() in di._JUNK_CANONICALS:  # S2 junk rule (mirror)
            continue
        structural.append((m.start(), idx, canonical, raw))
        parity.append(
            {"canonical": canonical, "heading_line": content.count("\n", 0, m.start()) + 1}
        )
    # Build regions from structural boundaries (non-structural headings stay
    # inside the enclosing region, exactly as production slices sections).
    n_lines = content.count("\n") + 1
    bounds = [(0, None, "__preamble__", None)] + [
        (content.count("\n", 0, s) + 1, c, raw, None) for s, _, c, raw in structural
    ]
    regions: list[Region] = []
    for bi, (ln, canon, raw, _) in enumerate(bounds):
        end = bounds[bi + 1][0] if bi + 1 < len(bounds) else n_lines + 1
        if canon is not None:
            kind = "company_unresolved"  # resolution applied by caller
        elif raw == "__preamble__":
            kind = "g5_masthead"
        else:
            # Post-S1 the marker/speaker/prose headings are non-boundaries
            # (skipped above), so every remaining non-company structural
            # heading is a named sector region (S4 pool).
            kind = "g4_sector"
        regions.append(
            Region(
                start_line=ln,
                end_line=end,
                kind=kind,
                heading=(raw or "") if raw != "__preamble__" else "",
                canonical=canon,
            )
        )
    return regions, parity


def assert_parity(content: str, resolver_map: dict[str, str]) -> tuple[list[Region], list[str]]:
    """Run the parity gate; rewrite region kinds with resolution results."""
    regions, parity = classify_boundaries(content)
    production = [
        {"canonical": s.canonical_name, "heading_line": s.heading_line}
        for s in di.iter_company_sections(content)
    ]
    divergences: list[str] = []
    if parity != production:
        p_set, o_set = {tuple(p.items()) for p in parity}, {tuple(p.items()) for p in production}
        for d in sorted(p_set ^ o_set):
            divergences.append(dict(d))
    # NOTE: resolution is NOT applied here — audit_note's S2 ladder loop owns
    # the company_unresolved -> company_resolved transition.
    return regions, divergences


# --------------------------------------------------------------------------- #
# Openings + coverage (contract yardstick)                                    #
# --------------------------------------------------------------------------- #
def normalize_lines(text: str) -> list[str]:
    """Walker normalization: emphasis unwrap + curly-quote fold (shared)."""
    out = []
    for ln in text.splitlines():
        if ln.strip() != "___":
            m = di._LINE_EMPH_RE.match(ln)
            if m:
                ln = m.group(1)
        for curly, ascii_q in di._CURLY_QUOTES.items():
            ln = ln.replace(curly, ascii_q)
        out.append(ln)
    return out


def opening_lines(text: str) -> list[int]:
    """1-based line numbers of quote-shaped openings (denominator)."""
    return [
        i + 1
        for i, ln in enumerate(normalize_lines(text))
        if ln.strip().startswith('"') and len(ln.strip()) > 40
    ]


def _cn(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().strip('"')).casefold()[:80]


def coverage_keys(rows: list[str]) -> list[str]:
    return [_cn(r) for r in rows]


def opening_covered(opening: str, keys: list[str]) -> bool:
    k = _cn(opening)
    return any(kk.startswith(k[:60]) or k.startswith(kk[:60]) for kk in keys)


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
def _attrish(s: str) -> bool:
    s = s.strip()
    if not s or len(s) > 100:
        return False
    if di._ATTR_DASH_RE.match(s) or di._ATTR_DASH_CAP_RE.match(s):
        return True
    return di._parse_attribution(s) is not None


def _shape_of(line: str) -> str:
    s = line.strip()
    if s.startswith(("- ", "* ", "> ")):
        return "bullet/blockquote"
    if len(s) <= 40:
        return "sub-40-with-attribution"
    if s.count('"') % 2 == 1:
        return "unclosed/splice"
    if s[0] in ",.;:!?—–-":
        return "leading-punct"
    return "other"


# ---- Per-note audit ----                                                   #
@dataclass
class NoteResult:
    tree: str
    stem: str
    path: str
    openings: int = 0
    buckets: dict = field(default_factory=dict)
    db_rows: int = 0
    covered: int = 0
    resolved_covered: int = 0
    walker_rows: int = 0
    flagged: bool = False
    divergence: list = field(default_factory=list)
    unresolved_canonicals: list = field(default_factory=list)
    suggestions: dict = field(default_factory=dict)
    unmatched_examples: list = field(default_factory=list)
    residual_shapes: dict = field(default_factory=dict)


def audit_note(
    path: Path, resolver_map: dict[str, str], db_rows_by_edition: dict[str, list[tuple[str, str]]],
    threshold: float = 0.95,
) -> NoteResult:
    content = path.read_text(encoding="utf-8", errors="replace")
    stem = path.stem
    tree = path.parent.name
    try:
        rel = str(path.relative_to(di.PROJECT_ROOT))
    except ValueError:  # outside the repo (tests) — absolute path is fine
        rel = str(path)
    res = NoteResult(tree=tree, stem=stem, path=rel)
    edition = di._edition_title(stem, content)
    regions, divergences = assert_parity(content, resolver_map)
    res.divergence = divergences
    # S2 ladder resolution (mirror of production _extract_sections).
    canon_entity: dict[str, str] = {}
    canon_sugg: dict[str, list[str]] = {}
    for r in regions:
        if r.kind == "company_unresolved" and r.canonical:
            ent, _tier, sugg = di._resolve_ladder(r.canonical, resolver_map)
            if ent:
                canon_entity[r.canonical] = ent
                r.kind = "company_resolved"
            else:
                canon_sugg[r.canonical] = sugg
    opens = opening_lines(content)
    res.openings = len(opens)
    lines = normalize_lines(content)

    # Walker pass on resolved sections (production shape).
    resolved_sections = [
        di.CompanySection(
            canonical_name=canon_entity[r.canonical],
            heading_line=r.start_line,
            body="\n".join(lines[r.start_line - 1 : r.end_line - 1]),
        )
        for r in regions
        if r.kind == "company_resolved"
    ]
    walker_rows: list[tuple[str, str]] = []
    for s in resolved_sections:
        walker_rows.extend((q.quote_text, q.entity) for q in di.extract_quotes(s, edition, stem))
    for sec, _raw, _kind in di.iter_sector_sections(content):
        walker_rows.extend((q.quote_text, q.entity) for q in di.extract_quotes(sec, edition, stem))
    for sec in di.iter_edition_note_section(content, stem):
        walker_rows.extend((q.quote_text, q.entity) for q in di.extract_quotes(sec, edition, stem))
    res.walker_rows = len(walker_rows)
    catch_all = di._CATCH_ALL_ENTITY
    walker_keys = [(_cn(t), ent != catch_all) for t, ent in walker_rows]

    # DB coverage (contract metric; resolved = non-catch-all rows).
    db_rows = db_rows_by_edition.get(stem) or db_rows_by_edition.get(edition, [])
    res.db_rows = len(db_rows)
    keys_total = [(_cn(t), ent != catch_all) for t, ent in db_rows]

    region_by_line: list[Region] = []
    for r in regions:
        region_by_line.extend([r] * (r.end_line - r.start_line))

    for ln in opens:
        region = region_by_line[ln - 1] if ln - 1 < len(region_by_line) else regions[-1]
        line = lines[ln - 1]
        def _matched(line: str) -> tuple[bool, bool]:
            k = _cn(line)
            for kk, resolved in (*walker_keys, *keys_total):
                if kk.startswith(k[:60]) or k.startswith(kk[:60]):
                    return True, resolved
            return False, False

        if region.kind == "company_resolved":
            covered, resolved = _matched(line)
            if covered:
                res.covered += 1
                res.resolved_covered += int(resolved)
            else:
                res.buckets["G3"] = res.buckets.get("G3", 0) + 1
                sh = _shape_of(line)
                res.residual_shapes[sh] = res.residual_shapes.get(sh, 0) + 1
                if len(res.unmatched_examples) < 5:
                    res.unmatched_examples.append(line.strip()[:100])
        elif region.kind == "company_unresolved":
            res.buckets["G2"] = res.buckets.get("G2", 0) + 1
            if region.canonical and region.canonical not in res.unresolved_canonicals:
                res.unresolved_canonicals.append(region.canonical)
                res.suggestions[region.canonical] = canon_sugg.get(region.canonical, [])
        elif region.kind in ("g4_sector", "g5_masthead"):
            # S4: sector/edition-note regions are production capture now —
            # catch-all matches count toward total, not resolved.
            covered, resolved = _matched(line)
            if covered:
                res.covered += 1
                res.resolved_covered += int(resolved)
            else:
                key = "G4_sector" if region.kind == "g4_sector" else "G5"
                res.buckets[key] = res.buckets.get(key, 0) + 1
        else:  # g1_marker / g4_speaker / g4_prose — dead post-S1, kept safe
            res.buckets["G1"] = res.buckets.get("G1", 0) + 1

    total_cov = (res.covered / res.openings) if res.openings else 1.0
    res.flagged = bool(res.openings) and total_cov < threshold
    return res


# --------------------------------------------------------------------------- #
# Watchlist + rule candidates                                                 #
# --------------------------------------------------------------------------- #
def load_watchlist(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"threshold": 0.95, "entries": {}}


def update_watchlist(wl: dict, results: list[NoteResult], today: str) -> dict:
    for r in results:
        cov = (r.covered / r.openings) if r.openings else 1.0
        key = f"{r.tree}/{r.stem}"
        e = wl["entries"].get(key)
        if r.flagged:
            if e is None:
                e = {"first_seen": today, "runs_seen": 0, "status": "open", "closed_by": None}
                wl["entries"][key] = e
            e.update(
                {
                    "last_seen": today,
                    "coverage": round(cov, 4),
                    "openings": r.openings,
                    "buckets": r.buckets,
                    "runs_seen": e.get("runs_seen", 0) + 1,
                    "status": "open",
                }
            )
        elif e is not None and e.get("status") == "open":
            e["status"] = "closed"
            e["closed_by"] = f"recovered (coverage {cov:.0%})"
            e["last_seen"] = today
            e["coverage"] = round(cov, 4)  # record the closing coverage
            e["openings"] = r.openings
    return wl


def rule_candidates(wl: dict, results: list[NoteResult]) -> list[dict]:
    """Recurring residual shapes: >=3 runs or >=10 openings corpus-wide."""
    agg: dict[str, dict] = {}
    for r in results:
        if not r.flagged:
            continue
        for shape, n in r.residual_shapes.items():
            a = agg.setdefault(shape, {"openings": 0, "notes": 0, "runs": 0})
            a["openings"] += n
            a["notes"] += 1
            e = wl["entries"].get(f"{r.tree}/{r.stem}")
            if e:
                a["runs"] = max(a["runs"], e.get("runs_seen", 0))
    return [
        {"shape": s, **a} for s, a in sorted(agg.items())
        if a["runs"] >= 3 or a["openings"] >= 10
    ]


# --------------------------------------------------------------------------- #
# Report + CLI                                                                #
# --------------------------------------------------------------------------- #
def run_audit(
    target: str = "findata",
    threshold: float = 0.95,
    watchlist_path: Path | None = WATCHLIST_DEFAULT,
    limit: int | None = None,
    verbose: bool = False,
) -> dict:
    # Project store access goes through helpers.core.db (house rule,
    # static_checks-enforced). The audit never writes.
    from helpers.core.db import connect

    conn = connect()
    resolver_map = di._build_resolver_map(conn)
    # Key DB rows by source_ref stem (derive:quotes:<stem>:<line>) — the
    # exact provenance pointer, immune to edition-title drift (the
    # 2026-09-07 trial caveat: as_of_edition carries titles that moved).
    db_rows_by_edition: dict[str, list[tuple[str, str]]] = {}
    for t, ent, ref in conn.execute(
        "SELECT quote_text, entity, source_ref FROM quotes"
    ).fetchall():
        stem_key = ref.split(":")[2] if ref and ref.startswith(di.QUOTES_PREFIX) else None
        key = stem_key or ent or ""
        db_rows_by_edition.setdefault(key, []).append((t, ent or ""))

    root = di.PROJECT_ROOT / target if target != "findata" else di.PROJECT_ROOT / "findata"
    files = [
        p for p in sorted(root.rglob("*.md"))
        if p.name != "image_map.md" and p.parent.name not in CHROME_FILES
        and any(t in p.parts for t in TREES)
    ]
    if limit:
        files = files[:limit]

    results: list[NoteResult] = []
    t0 = time.time()
    for i, p in enumerate(files, 1):
        if verbose or i % 10 == 0 or i == len(files):
            print(f"[quotes] {i}/{len(files)} files ({time.time() - t0:.1f}s)", file=sys.stderr)
        results.append(audit_note(p, resolver_map, db_rows_by_edition, threshold))

    wl = load_watchlist(watchlist_path) if watchlist_path else {"threshold": threshold, "entries": {}}
    wl["threshold"] = threshold
    today = time.strftime("%Y-%m-%d")
    wl = update_watchlist(wl, results, today)
    cands = rule_candidates(wl, results)
    if watchlist_path:
        watchlist_path.write_text(json.dumps(wl, indent=1, sort_keys=True) + "\n")

    # S2 worklist: tracked triage artifact (canonical -> fuzzy suggestions).
    # User flow: alias decision -> findata/quote_aliases.json (auto-picked-up
    # by the ladder); missing entity -> stub via the user-held flow; both
    # auto-close here when the canonical resolves on a later run.
    today2 = today
    wl_path = di.PROJECT_ROOT / "findata" / "quote_entity_worklist.json"
    try:
        entity_worklist = json.loads(wl_path.read_text()) if wl_path.exists() else {"entries": {}}
    except Exception:
        entity_worklist = {"entries": {}}
    seen_now: dict[str, list[str]] = {}
    for r in results:
        for c, sugg in r.suggestions.items():
            seen_now.setdefault(c, [])
            for s in sugg:
                if s not in seen_now[c]:
                    seen_now[c].append(s)
    for c, sugg in seen_now.items():
        e = entity_worklist["entries"].setdefault(
            c, {"first_seen": today2, "status": "open", "notes": []}
        )
        e.update({"last_seen": today2, "suggestions": sugg, "status": "open"})
        if r_notes := [f"{x.tree}/{x.stem}" for x in results if c in x.suggestions]:
            e["notes"] = sorted(set(e.get("notes", [])) | set(r_notes))[:8]
    for c, e in list(entity_worklist["entries"].items()):
        if c not in seen_now and e.get("status") == "open":
            e["status"] = "resolved"
            e["closed_by"] = "canonical resolves on a later run"
    wl_path.write_text(json.dumps(entity_worklist, indent=1, sort_keys=True) + "\n")
    conn.close()

    # Tree funnels.
    funnels = {}
    for tree in TREES:
        tr = [r for r in results if r.tree == tree]
        if not tr:
            continue
        op = sum(r.openings for r in tr)
        funnels[tree] = {
            "files": len(tr),
            "openings": op,
            "covered": sum(r.covered for r in tr),
            "walker_rows": sum(r.walker_rows for r in tr),
            "db_rows": sum(r.db_rows for r in tr),
            "flagged": sum(1 for r in tr if r.flagged),
            **{b: sum(r.buckets.get(b, 0) for r in tr)
               for b in ("G1", "G2", "G3", "G4_sector", "G4_speaker", "G4_prose", "G5")},
        }
    parity_failures = [r for r in results if r.divergence]
    report = {
        "generated": today,
        "threshold": threshold,
        "files": len(results),
        "duration_s": round(time.time() - t0, 1),
        "funnels": funnels,
        "corpus_coverage": (
            sum(r.covered for r in results) / tot_op
            if (tot_op := sum(r.openings for r in results)) else 1.0
        ),
        "flagged_notes": [
            {
                "note": f"{r.tree}/{r.stem}", "coverage": round(r.covered / r.openings, 4) if r.openings else 1.0,
                "openings": r.openings, "buckets": r.buckets,
                "unresolved": r.unresolved_canonicals, "examples": r.unmatched_examples,
            }
            for r in results if r.flagged
        ],
        "watchlist_open": sum(1 for e in wl["entries"].values() if e.get("status") == "open"),
        "watchlist_closed": sum(1 for e in wl["entries"].values() if e.get("status") == "closed"),
        "rule_candidates": cands,
        "parity_divergences": {f"{r.tree}/{r.stem}": r.divergence for r in parity_failures},
    }
    return report


def print_report(rep: dict) -> None:
    print(f"quote coverage audit — {rep['generated']} (threshold {rep['threshold']:.0%})")
    print(f"files: {rep['files']}  corpus coverage: {rep['corpus_coverage']:.1%}")
    for tree, f in rep["funnels"].items():
        cov = f["covered"] / f["openings"] if f["openings"] else 1.0
        print(
            f"  {tree:<20} files={f['files']:>3} openings={f['openings']:>5} "
            f"covered={f['covered']:>5} ({cov:.1%}) walker={f['walker_rows']:>5} "
            f"db={f['db_rows']:>5} flagged={f['flagged']:>3} "
            f"G1={f.get('G1', 0):>4} G2={f.get('G2', 0):>4} G3={f.get('G3', 0):>4} "
            f"G4sec={f.get('G4_sector', 0):>4} G4spk={f.get('G4_speaker', 0):>4} "
            f"G4prose={f.get('G4_prose', 0):>4} G5={f.get('G5', 0):>3}"
        )
    print(f"watchlist: {rep['watchlist_open']} open / {rep['watchlist_closed']} closed")
    if rep["parity_divergences"]:
        print(f"PARITY DIVERGENCES: {list(rep['parity_divergences'])}")
    for fn in rep["flagged_notes"][:15]:
        print(
            f"  FLAG {fn['note']:<44} {fn['coverage']:.0%} of {fn['openings']}  "
            f"buckets={fn['buckets']}"
        )
        if fn["unresolved"]:
            print(f"       unresolved: {fn['unresolved'][:6]}")
    if rep["flagged_notes"]:
        extra = len(rep["flagged_notes"]) - 15
        if extra > 0:
            print(f"  ... {extra} more flagged notes")
    if rep["rule_candidates"]:
        print("rule candidates (recurring shapes):")
        for c in rep["rule_candidates"]:
            print(f"  {c['shape']:<28} openings={c['openings']:>4} notes={c['notes']:>3} runs={c['runs']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Quote capture coverage audit (S0, advisory)")
    ap.add_argument("--target", default="findata", help="tree dir or findata")
    ap.add_argument("--threshold", type=float, default=0.95, help="per-note tripwire floor")
    ap.add_argument("--limit", type=int, default=None, help="audit only first N files")
    ap.add_argument("--json", action="store_true", help="JSON report on stdout")

    ap.add_argument("--no-watchlist", action="store_true", help="do not read/write the watchlist")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    rep = run_audit(
        target=args.target,
        threshold=args.threshold,
        watchlist_path=None if args.no_watchlist else WATCHLIST_DEFAULT,
        limit=args.limit,
        verbose=args.verbose,
    )
    if args.json:
        json.dump(rep, sys.stdout, indent=1)
        print()
    else:
        print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
