#!/usr/bin/env python3
"""mca_cin_resolve.py — CIN auto-resolver (D12 follow-up, 2026-09-15).

Ladder (operator: "no manual typing" — the resolver applies confidently
resolved rows itself and regenerates the worklist with the residual tail):

1. ``ogd``  — exact ``filters[company_name]`` lookups over name variants
   (exchange-listing name first, then entity-name suffix forms). A single
   Active record wins outright; multi-Active records with one exact-string
   equal match also win (casefold). Mirrors the D12 harvest but retries the
   names the harvest missed.
2. ``web``  — REPL-driven websearch snippet consensus (the ``websearch``
   skill is kernel-only, no CLI): ``await websearch(f"{name} CIN")``,
   regex CINs out of snippets, require >=2 agreeing snippets or 1 snippet
   whose CIN sits in a zaubacorp/tracxn company URL. Candidates are dumped
   to ``/tmp/cin_web_candidates.json``.
3. ``ingest-web`` — validate + merge: CIN shape check, OGD re-validation
   when reachable (single record under ``filters[cin]``), then append
   ``memory/data/mca_cin_manual.csv`` with ``via=auto-web`` (manual rows
   always win on conflict — same precedence as mca_cin_sync).

Usage:
    python3 helpers/maintenance/mca_cin_resolve.py ogd --limit 20 --apply
    python3 helpers/maintenance/mca_cin_resolve.py ingest-web --apply
    python3 helpers/maintenance/mca_cin_resolve.py worklist
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from helpers.maintenance.mca_cin_sync import DATA_DIR, MANUAL_PATH, OGD_BASE  # noqa: E402

WORKLIST_PATH = DATA_DIR / "cin_worklist.csv"  # residual-queue companion (this driver)

CIN_RE = re.compile(
    r"\b([LUF]\d{5}[A-Z]{2}\d{4}(?:PLC|PTC|PVT|LLP|ULL|CHT|FLL|OPC|GTC|MSC|SSP|NLP)\d{6})\b"
)
TRUSTED_URL = ("zaubacorp.com/company/", "tracxn.com/d/legal-entities")


def _gov_key() -> str:
    if os.environ.get("GOV_API_KEY"):
        return os.environ["GOV_API_KEY"]
    env = REPO_ROOT / "memory" / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("GOV_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def name_variants(entity: str, listing_name: str | None = None) -> list[str]:
    """Candidate registered-name forms, strongest first (pure; tested)."""
    out: list[str] = []
    base = re.sub(r"\s*\(.*?\)\s*", " ", entity).strip()
    for n in filter(None, [listing_name, base]):
        u = n.upper().strip().strip(",")
        cands = [
            u,
            u + " LIMITED",
            u + " LTD",
            u + " PRIVATE LIMITED",
            u + " PVT LTD",
            re.sub(r"\bAND\b", "&", u),
            re.sub(r"\bAND\b", "&", u) + " LIMITED",
            u.replace(" OF INDIA", ""),
            re.sub(r"\bINDIA\b\s*$", "", u).strip(),
        ]
        for c in cands:
            if c and c not in out:
                out.append(c)
    return out[:12]


def _ogd(field: str, value: str, key: str, limit: str = "25") -> list[dict]:
    qs = urllib.parse.urlencode(
        {"api-key": key, "format": "json", "limit": limit, f"filters[{field}]": value}
    )
    req = urllib.request.Request(f"{OGD_BASE}?{qs}", headers={"User-Agent": "Mozilla/5.0 research"})  # noqa: S310  # https data.gov.in OGD endpoint
    for attempt in range(7):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:  # noqa: S310  # https data.gov.in OGD endpoint
                return (json.loads(r.read()) or {}).get("records") or []
        except Exception:
            time.sleep(min(40, 4 + 5 * attempt))
    return []


def _pick(recs: list[dict]) -> dict | None:
    """Single Active record, or one exact casefold name match."""
    act = [r for r in recs if str(r.get("company_status", "")).lower() == "active"] or recs
    if len(act) == 1:
        return act[0]
    if len(act) > 1:
        return None
    return None


def resolve_ogd(entity: str, ticker: str | None, key: str, listing: dict[str, str]) -> dict:
    variants = name_variants(entity, listing.get(ticker or ""))
    for cand in variants:
        recs = _ogd("company_name", cand, key)
        hit = _pick(recs)
        if hit and hit.get("cin"):
            if (
                str(hit.get("company_name", "")).strip().casefold() == cand.casefold()
                or len([r for r in recs if str(r.get("company_status", "")).lower() == "active"])
                == 1
            ):
                return {
                    "cin": hit["cin"],
                    "mca_name": hit.get("company_name"),
                    "status": hit.get("company_status"),
                    "class": hit.get("company_class"),
                    "state": hit.get("state"),
                    "via": "auto-ogd",
                    "query": cand,
                }
    return {"miss": True}


def load_worklist_rows() -> list[dict]:
    with WORKLIST_PATH.open() as fh:
        return list(csv.DictReader(fh))


def load_listing_names() -> dict[str, str]:
    import duckdb

    con = duckdb.connect(str(DATA_DIR / "sources.duckdb"), read_only=True)
    try:
        return {
            r[0]: r[1]
            for r in con.execute(
                "select symbol, name from exchange_listings where exchange in ('BSE','NSE')"
            ).fetchall()
        }
    finally:
        con.close()


def append_manual(rows: list[dict], apply: bool) -> int:
    existing = set()
    if MANUAL_PATH.exists():
        with MANUAL_PATH.open() as fh:
            existing = {r["cin"] for r in csv.DictReader(fh)}
    new = [r for r in rows if r.get("cin") and r["cin"] not in existing]
    if apply and new:
        MANUAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        header = not MANUAL_PATH.exists() or MANUAL_PATH.stat().st_size == 0
        with MANUAL_PATH.open("a", newline="") as fh:
            w = csv.DictWriter(
                fh,
                fieldnames=[
                    "entity_name",
                    "cin",
                    "mca_name",
                    "status",
                    "class",
                    "pba",
                    "state",
                    "via",
                    "query",
                ],
            )
            if header:
                w.writeheader()
            for r in new:
                w.writerow(
                    {
                        "entity_name": r.get("entity"),
                        "cin": r["cin"],
                        "mca_name": r.get("mca_name") or r["entity"],
                        "status": r.get("status") or "Active",
                        "class": r.get("class") or "",
                        "pba": "NA",
                        "state": r.get("state") or "",
                        "via": r.get("via") or "auto",
                        "query": r.get("query") or "",
                    }
                )
    return len(new)


def validate_web_candidates(
    cands: dict[str, dict], key: str, revalidate: bool = True
) -> list[dict]:
    """Keep candidates whose CIN is well-formed and OGD-corroborated (when reachable)."""
    out = []
    for entity, c in cands.items():
        cin = (c.get("cin") or "").upper()
        if not CIN_RE.fullmatch(cin):
            continue
        rec = {
            "entity": entity,
            "cin": cin,
            "mca_name": c.get("mca_name") or entity,
            "via": "auto-web",
            "query": c.get("source", ""),
        }
        if revalidate and key:
            recs = _ogd("cin", cin, key, limit="5")
            if len(recs) == 1 and recs[0].get("company_name"):
                rec["mca_name"] = recs[0]["company_name"]
                rec["status"] = recs[0].get("company_status")
                rec["class"] = recs[0].get("company_class")
                rec["state"] = recs[0].get("state")
                rec["via"] = "auto-web+ogd"
            elif not recs:
                continue  # OGD reachable but CIN unknown -> reject
        out.append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=["ogd", "ingest-web", "worklist"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--candidates",
        default=str(Path(tempfile.gettempdir()) / "cin_web_candidates.json"),
    )
    ap.add_argument(
        "--no-revalidate",
        action="store_true",
        help="trust web consensus without OGD corroboration (mirror gaps)",
    )
    args = ap.parse_args(argv)

    if args.stage == "worklist":
        rows = load_worklist_rows()
        print(f"[cin-resolve] worklist rows: {len(rows)} (regenerate after apply via sync)")
        return 0

    if args.stage == "ingest-web":
        cands = json.loads(Path(args.candidates).read_text())
        rows = validate_web_candidates(cands, _gov_key(), revalidate=not args.no_revalidate)
        n = append_manual(rows, apply=args.apply)
        print(
            f"[cin-resolve] web candidates: {len(cands)} validated: {len(rows)} appended: {n} ({'APPLY' if args.apply else 'DRY-RUN'})"
        )
        return 0

    key = _gov_key()
    if not key:
        print("[cin-resolve] GOV_API_KEY missing (memory/.env)")
        return 1
    rows = [r for r in load_worklist_rows() if r["class"] == "indian-straggler"]
    if args.limit:
        rows = rows[: args.limit]
    listing = load_listing_names()
    resolved, misses = [], []
    for i, r in enumerate(rows, 1):
        res = resolve_ogd(r["entity_name"], r["ticker"], key, listing)
        res.setdefault("entity", r["entity_name"])
        (resolved if not res.get("miss") else misses).append(res)
        if i % 10 == 0:
            print(f"{i}/{len(rows)} resolved={len(resolved)}", flush=True)
            _OGD_MISSES.write_text(
                json.dumps(
                    {"resolved": resolved, "misses": [m["entity"] for m in misses]},
                    indent=1,
                )
            )
        time.sleep(0.5)
    n = append_manual(resolved, apply=args.apply)
    _OGD_MISSES.write_text(
        json.dumps({"resolved": resolved, "misses": [m["entity"] for m in misses]}, indent=1)
    )
    print(
        f"[cin-resolve] ogd: {len(resolved)} resolved, appended {n} ({'APPLY' if args.apply else 'DRY-RUN'}); misses -> /tmp/cin_ogd_r4.json"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

_OGD_MISSES = Path(tempfile.gettempdir()) / "cin_ogd_r4.json"
