#!/usr/bin/env python3
"""MCA CIN sidecar sync (D12) — git-managed resolution registry.

The company-master resolution is slow (per-name OGD lookups), so results
are kept PERMANENTLY in ``memory/data/mca_cin.parquet`` (reviewable, git-diffed)
instead of being re-derived. Three sources merge into it:

- the harvester output (``/tmp/d12_hits.json`` scratch — not committed),
- ``memory/data/mca_cin_manual.csv`` (hand-resolved rows; wins on conflict),
- live resolution (``resolve --name X`` appends).

CINs are immutable, so there is no refresh cycle: new companies resolve
once at creation (newsletter-parse lane) and the CSV grows additively.

    python3 helpers/maintenance/mca_cin_sync.py build            # merge -> CSV (dry)
    python3 helpers/maintenance/mca_cin_sync.py build --apply    # write CSV
    python3 helpers/maintenance/mca_cin_sync.py apply            # CSV -> entities.cin (dry)
    python3 helpers/maintenance/mca_cin_sync.py apply --apply    # write CINs
    python3 helpers/maintenance/mca_cin_sync.py resolve --name "Foo" [--apply]

``apply`` validates every CIN through ``backfill_identifiers`` (hard
parse/entity checks) and writes facets; ``resolve`` queries the OGD
MCA company-master mirror (``GOV_API_KEY`` env, fuzzy-confirmed via
``fuzzy_match.word_overlap_match``).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # noqa: E402

from helpers.core.cin import parse_cin  # noqa: E402
from helpers.core.db import DEFAULT_DB_PATH, connect  # noqa: E402
from helpers.core.fuzzy_match import word_overlap_match  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "memory" / "data"
PARQUET_PATH = (
    DATA_DIR / "mca_cin.parquet"
)  # canonical (bulk-data-lanes: parquet, DuckDB-queryable)
CSV_PATH = DATA_DIR / "mca_cin.csv"  # legacy seed, migrated on first build
MANUAL_PATH = DATA_DIR / "mca_cin_manual.csv"  # hand-authored input rows
SCRATCH_HITS = Path(tempfile.gettempdir()) / "d12_hits.json"
OGD_BASE = "https://api.data.gov.in/resource/ec58dab7-d891-4abb-936e-d5d274a6ce9b"

_FIELDS = (
    "entity_name",
    "cin",
    "mca_name",
    "status",
    "class",
    "pba",
    "state",
    "via",
    "query",
    "fetched_at",
)


SOURCES_DB = DATA_DIR / "sources.duckdb"  # live store; snapshotted via snapshot_db


def _rows_read(path: Path) -> dict[str, dict]:
    """Load sidecar rows — sources.duckdb table is canonical; CSV is legacy/manual input."""
    if path in (PARQUET_PATH, CSV_PATH) and SOURCES_DB.exists():
        path = SOURCES_DB
    if not path.exists():
        return {}
    if path.suffix == ".duckdb":
        import duckdb

        con = duckdb.connect(str(path), read_only=True)
        try:
            cur = con.execute("SELECT * FROM mca_cin")
            cols = [d[0] for d in cur.description]
            return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}
        finally:
            con.close()
    with path.open() as fh:
        return {r["entity_name"]: r for r in csv.DictReader(fh)}


def _write_duckdb(rows: dict[str, dict]) -> None:
    import duckdb

    con = duckdb.connect(str(SOURCES_DB))
    try:
        con.execute("DROP TABLE IF EXISTS mca_cin")
        con.execute("""CREATE TABLE mca_cin (
            entity_name VARCHAR, cin VARCHAR, mca_name VARCHAR, status VARCHAR,
            class VARCHAR, pba VARCHAR, state VARCHAR, via VARCHAR, query VARCHAR,
            fetched_at VARCHAR)""")
        con.executemany(
            "INSERT INTO mca_cin VALUES (?,?,?,?,?,?,?,?,?,?)",
            [tuple(rows[name].get(k, "") for k in _FIELDS) for name in sorted(rows)],
        )
    finally:
        con.close()


def cmd_build(apply: bool) -> int:
    """Merge harvester scratch + manual overrides -> memory/data/mca_cin.parquet."""
    merged = _rows_read(CSV_PATH)
    manual = _rows_read(MANUAL_PATH)
    merged.update(manual)
    if SCRATCH_HITS.exists():
        hits = json.loads(SCRATCH_HITS.read_text())
        for name, hit in hits.items():
            if name in manual:
                continue
            merged[name] = {
                "entity_name": name,
                "cin": hit.get("cin", ""),
                "mca_name": hit.get("mca_name", ""),
                "status": hit.get("status", ""),
                "class": hit.get("class", ""),
                "pba": hit.get("pba", ""),
                "state": hit.get("state", ""),
                "via": hit.get("via") or "harvest",
                "query": hit.get("query", ""),
                "fetched_at": datetime.now(UTC).strftime("%Y-%m-%d"),
            }
    # sidecar holds only real CINs — foreign F-numbers / LLPINs fail the
    # format gate here so apply's all-or-nothing batch never blocks
    dropped = [nm for nm, r in merged.items() if r.get("cin") and not parse_cin(r["cin"]).ok]
    for nm in dropped:
        merged.pop(nm)
    n = len(merged)
    print(
        f"[mca-cin] {n} row(s) merged (manual={len(manual)}), "
        f"{len(dropped)} dropped by CIN-format gate: {dropped[:5]}"
    )
    if apply:
        _write_duckdb(merged)
        print(f"[mca-cin] wrote {SOURCES_DB.relative_to(REPO_ROOT)}::mca_cin ({n} rows)")
    else:
        print("[mca-cin] dry-run — pass --apply to write")
    return 0


def cmd_apply(apply: bool, db: Path | None) -> int:
    """Push the sidecar CINs into entities via backfill_identifiers."""
    from helpers.misc.backfill_identifiers import apply_set_ops, plan_set_ops

    rows = _rows_read(PARQUET_PATH)
    if not rows:
        print("[mca-cin] sidecar empty — run build first")
        return 1
    conn = connect(db or DEFAULT_DB_PATH)
    specs = [f"{name}={row['cin']}" for name, row in sorted(rows.items())]
    ops, failures = plan_set_ops(conn, specs, None)
    for f in failures:
        print(f"[mca-cin] SKIP {f}")
    if failures:
        print("[mca-cin] batch blocked — fix sidecar rows above (all-or-nothing)")
        conn.close()
        return 1
    print(f"[mca-cin] {len(ops)} validated [{'APPLY' if apply else 'DRY-RUN'}]")
    if apply:
        wrote = apply_set_ops(conn, ops)
        conn.commit()
        print(f"[mca-cin] wrote {wrote}")
    conn.close()
    return 0


def _ogd_lookup(candidate: str, key: str) -> dict | None:
    qs = urllib.parse.urlencode(
        {"api-key": key, "format": "json", "limit": "10", "filters[company_name]": candidate}
    )
    req = urllib.request.Request(f"{OGD_BASE}?{qs}", headers={"User-Agent": "Mozilla/5.0 research"})  # noqa: S310  # https data.gov.in OGD endpoint
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:  # noqa: S310  # URL is the hardcoded https OGD endpoint (OGD_BASE) + urlencoded query; no user-controlled scheme/host
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                import time

                time.sleep(min(45, 5 + 4 * attempt))
                continue
            raise
        except Exception:
            import time

            time.sleep(4)
    return None


def cmd_resolve(name: str, apply: bool) -> int:
    """Live-resolve one company name -> sidecar row (OGD mirror, fuzzy-ok)."""
    key = os.environ.get("GOV_API_KEY", "")
    if not key and Path(REPO_ROOT / "memory" / ".env").exists():
        for line in (REPO_ROOT / "memory" / ".env").read_text().splitlines():
            if line.startswith("GOV_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        print("[mca-cin] GOV_API_KEY not set (memory/.env)")
        return 1
    cands = [name.upper(), name.upper() + " LIMITED", name.upper() + " PRIVATE LIMITED"]
    for cand in cands:
        j = _ogd_lookup(cand, key)
        if not j:
            continue
        recs = j.get("records") or []
        recs = [r for r in recs if r.get("company_status") == "Active"] or recs
        if not recs:
            continue
        pub = [r for r in recs if (r.get("company_class") or "").lower().startswith("pub")]
        r0 = (pub or recs)[0]
        mca_name = r0["company_name"]
        match, score = word_overlap_match(name, [mca_name], threshold=0.4)
        if not match:
            print(f"[mca-cin] {mca_name!r} failed fuzzy confirm ({score:.2f})")
            continue
        row = {
            "entity_name": name,
            "cin": r0["corporate_identification_number"],
            "mca_name": mca_name,
            "status": r0.get("company_status", ""),
            "class": r0.get("company_class", ""),
            "pba": r0.get("principal_business_activity", ""),
            "state": r0.get("registered_state", ""),
            "via": "live",
            "query": cand,
            "fetched_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        }
        print(f"[mca-cin] {name} -> {mca_name} (CIN {row['cin']})")
        if apply:
            rows = _rows_read(SOURCES_DB)
            rows[name] = row
            _write_duckdb(rows)
            print(f"[mca-cin] wrote {SOURCES_DB.relative_to(REPO_ROOT)}::mca_cin")
        return 0
    print(f"[mca-cin] no MCA match for {name!r}")
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("cmd", choices=("build", "apply", "resolve"))
    p.add_argument("--apply", action="store_true", help="write (default dry-run)")
    p.add_argument("--name", help="company name for resolve")
    p.add_argument("--db", type=Path, default=None)
    a = p.parse_args(argv)
    if a.cmd == "build":
        return cmd_build(a.apply)
    if a.cmd == "apply":
        return cmd_apply(a.apply, a.db)
    if not a.name:
        p.error("resolve requires --name")
    return cmd_resolve(a.name, a.apply)


if __name__ == "__main__":
    raise SystemExit(main())
