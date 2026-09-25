#!/usr/bin/env python3
"""Fetch and cache Wikidata QID crosswalk sidecar rows."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from helpers.core.db import DEFAULT_DB_PATH, connect

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "memory" / "data"
PARQUET_PATH = DATA_DIR / "wikidata_qids.parquet"
MANUAL_PATH = DATA_DIR / "wikidata_qids_manual.csv"
CACHE_PATH = DATA_DIR / "wikidata_sparql_cache.json"
API_URL = "https://query.wikidata.org/sparql"
REQUEST_DELAY_SECONDS = 1.0
USER_AGENT = "Findata-Agent"
SOURCE_VERSION = "qid-2026-09-25"

FIELDS = (
    "entity_name",
    "qid",
    "label",
    "description",
    "match_type",
    "confidence",
    "corroboration",
    "query",
    "fetched_at",
)


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _normalise(value: str) -> str:
    return " ".join(value.casefold().split()).strip()


def _read_csv(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="") as fh:
        return {_normalise(row["entity_name"]): row for row in csv.DictReader(fh)}


def _read_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except OSError, json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _write_cache(path: Path, cache: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _sparql_json(query: str, *, cache: dict[str, dict[str, Any]], cache_path: Path) -> dict:
    key = query
    if key in cache:
        return cache[key]
    url = f"{API_URL}?{urllib.parse.urlencode({'query': query, 'format': 'json'})}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/sparql-results+json",
        },
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read())
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            if attempt == 4:
                return {}
            time.sleep(min(30, 2**attempt))
        except TimeoutError, urllib.error.URLError:
            return {}
    cache[key] = payload
    _write_cache(cache_path, cache)
    time.sleep(REQUEST_DELAY_SECONDS)
    return payload


def candidate_entities(conn: sqlite3.Connection) -> list[dict[str, str | None]]:
    rows = conn.execute(
        "SELECT name, ticker, cin FROM entities WHERE entity_type='company'"
    ).fetchall()
    by_name = {
        row[0]: {
            "entity_name": row[0],
            "ticker": row[1],
            "cin": row[2],
            "isin": None,
            "lei": None,
        }
        for row in rows
    }
    try:
        identifier_rows = conn.execute(
            "SELECT entity_name, identifier_type, identifier_value FROM entity_identifiers "
            "WHERE identifier_type IN ('isin', 'lei')"
        ).fetchall()
    except sqlite3.OperationalError:
        identifier_rows = []
    for name, identifier_type, value in identifier_rows:
        if name in by_name:
            by_name[name][identifier_type] = value
    for row in conn.execute("SELECT DISTINCT entity FROM quotes"):
        name = row[0]
        by_name.setdefault(
            name,
            {"entity_name": name, "ticker": None, "cin": None, "isin": None, "lei": None},
        )
    for row in conn.execute("SELECT DISTINCT company_name FROM company_embeddings"):
        name = row[0]
        by_name.setdefault(
            name,
            {"entity_name": name, "ticker": None, "cin": None, "isin": None, "lei": None},
        )
    return sorted(by_name.values(), key=lambda row: str(row["entity_name"]).casefold())


def _claims_values(entity: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for claims in entity.get("claims", {}).values():
        for claim in claims:
            snak = claim.get("mainsnak", {})
            value = snak.get("datavalue", {}).get("value")
            if isinstance(value, str):
                values.add(value.casefold())
            elif isinstance(value, dict) and isinstance(value.get("id"), str):
                values.add(value["id"].casefold())
    return values


def _resolve_candidate(
    candidate: dict[str, str | None],
    search: Callable[[dict[str, str | None]], list[dict[str, Any]]],
    fetch_entity: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any] | None:
    name = str(candidate["entity_name"])
    results = search(candidate)
    if not results:
        return None
    top = results[0]
    qid = str(top.get("id") or "")
    if not qid.startswith("Q"):
        return None
    unique = len({str(item.get("id") or "") for item in results}) == 1
    direct_kind = str(top.get("match_kind") or "")
    values: set[str] = set()
    if fetch_entity is not None and not direct_kind:
        values = _claims_values(fetch_entity(qid))
    expected = {
        str(candidate.get(key) or "").casefold() for key in ("ticker", "cin", "isin", "lei")
    }
    expected.discard("")
    corroborated = bool(direct_kind) or bool(expected & values)
    if corroborated and unique:
        match_type = "exactMatch"
        confidence = "high"
    else:
        match_type = "closeMatch"
        confidence = "low"
    return {
        "entity_name": name,
        "qid": qid,
        "label": str(top.get("label") or ""),
        "description": str(top.get("description") or ""),
        "match_type": match_type,
        "confidence": confidence,
        "corroboration": direct_kind or "identifier_claim" if corroborated else "none",
        "query": ";".join(
            f"{key}={candidate[key]}"
            for key in ("ticker", "isin", "lei", "cin")
            if candidate.get(key)
        )
        or name,
        "fetched_at": _today(),
    }


def build_rows(
    conn: sqlite3.Connection,
    *,
    search: Callable[[dict[str, str | None]], list[dict[str, Any]]],
    fetch_entity: Callable[[str], dict[str, Any]] | None = None,
    limit: int | None = None,
    identified_only: bool = False,
    progress: Callable[[int, int, dict[str, str | None], dict[str, str] | None], None]
    | None = None,
) -> list[dict[str, str]]:
    candidates = candidate_entities(conn)
    if identified_only:
        candidates = [
            row for row in candidates if any(row.get(key) for key in ("ticker", "isin", "lei"))
        ]
    if limit is not None:
        candidates = candidates[:limit]
    rows: dict[str, dict[str, str]] = {}
    for index, candidate in enumerate(candidates, start=1):
        if progress is not None:
            progress(index, len(candidates), candidate, None)
        row = _resolve_candidate(candidate, search, fetch_entity)
        if progress is not None:
            progress(index, len(candidates), candidate, row)
        if row is not None:
            rows[_normalise(str(candidate["entity_name"]))] = row
    return list(rows.values())


def _write_parquet(path: Path, rows: list[dict[str, str]]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({field: [row.get(field, "") for row in rows] for field in FIELDS})
    pq.write_table(table, path, compression="zstd")


def _read_parquet(path: Path) -> list[dict[str, str]]:
    import pyarrow.parquet as pq

    if not path.exists():
        return []
    return pq.read_table(path).to_pylist()


def converge_rows(
    conn: sqlite3.Connection, rows: list[dict[str, str]], *, apply: bool
) -> dict[str, int]:
    exact = [row for row in rows if row.get("match_type") == "exactMatch"]
    close = [row for row in rows if row.get("match_type") == "closeMatch"]
    planned = [(row, "active") for row in exact] + [(row, "candidate") for row in close]
    if not apply:
        return {"exact": len(exact), "close": len(close), "changed": len(planned)}
    with conn:
        conn.execute("DELETE FROM concept_mappings WHERE source_ref='wikidata:qid-sync'")
        conn.executemany(
            "INSERT INTO concept_mappings "
            "(source_scheme, source_concept, target_scheme, target_concept, match_type, "
            "source_ref, version, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "findata:entity",
                    row["entity_name"],
                    "wikidata:qid",
                    row["qid"],
                    row["match_type"],
                    "wikidata:qid-sync",
                    SOURCE_VERSION,
                    status,
                )
                for row, status in planned
            ],
        )
    return {"exact": len(exact), "close": len(close), "changed": len(planned)}


def cmd_converge(apply: bool, db: Path | None) -> int:
    rows = _read_parquet(PARQUET_PATH)
    if not rows:
        print("[wikidata] sidecar empty — run sync --apply first")
        return 1
    conn = connect(db or DEFAULT_DB_PATH)
    try:
        from helpers.misc.seed_concepts import ensure_schema

        ensure_schema(conn)
        counts = converge_rows(conn, rows, apply=apply)
    finally:
        conn.close()
    print(
        f"[wikidata] {counts['exact']} exact active, {counts['close']} close candidate, "
        f"{counts['changed']} planned [{'APPLY' if apply else 'DRY-RUN'}]"
    )
    return 0


def _sparql_literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _binding_value(binding: dict[str, Any], key: str) -> str:
    return str(binding.get(key, {}).get("value", ""))


def _result_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for binding in payload.get("results", {}).get("bindings", []):
        item = _binding_value(binding, "item")
        if not item.startswith("http://www.wikidata.org/entity/Q"):
            continue
        rows.append(
            {
                "id": item.rsplit("/", 1)[-1],
                "label": _binding_value(binding, "label"),
                "description": _binding_value(binding, "description"),
                "match_kind": _binding_value(binding, "match_kind"),
            }
        )
    return rows


def _identifier_query(candidate: dict[str, str | None], name: str) -> str | None:
    clauses: list[str] = []
    for field, prop in (("ticker", "P249"), ("isin", "P946"), ("lei", "P1278")):
        value = str(candidate.get(field) or "").strip()
        if not value:
            continue
        literal = _sparql_literal(value)
        clauses.append(
            f"{{ VALUES ?value {{ {literal} }} ?item wdt:{prop} ?value . "
            f'BIND("{field}" AS ?match_kind) }}'
        )
        if field == "ticker":
            clauses.append(
                f"{{ VALUES ?value {{ {literal} }} ?item p:P249 ?statement . "
                f'?statement ps:P249 ?value . BIND("ticker" AS ?match_kind) }}'
            )
    if not clauses:
        return None
    return (
        "PREFIX wd: <http://www.wikidata.org/entity/> "
        "PREFIX wdt: <http://www.wikidata.org/prop/direct/> "
        "PREFIX p: <http://www.wikidata.org/prop/> "
        "PREFIX ps: <http://www.wikidata.org/prop/statement/> "
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
        "PREFIX schema: <http://schema.org/> "
        "SELECT DISTINCT ?item ?label ?description ?match_kind WHERE { "
        + " UNION ".join(clauses)
        + " ?item rdfs:label ?label . FILTER(LANG(?label) = 'en') "
        " OPTIONAL { ?item schema:description ?description . FILTER(LANG(?description) = 'en') } "
        "} LIMIT 20"
    )


def _label_query(name: str) -> str:
    literal = _sparql_literal(name)
    return (
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
        "PREFIX skos: <http://www.w3.org/2004/02/skos/core#> "
        "PREFIX schema: <http://schema.org/> "
        "SELECT DISTINCT ?item ?label ?description WHERE { { "
        f"?item rdfs:label ?label . FILTER(LCASE(STR(?label)) = LCASE({literal})) "
        "} UNION { "
        f"?item skos:altLabel ?label . FILTER(LCASE(STR(?label)) = LCASE({literal})) "
        "} OPTIONAL { ?item schema:description ?description . FILTER(LANG(?description) = 'en') } "
        "} LIMIT 20"
    )


def _search(
    candidate: dict[str, str | None], cache: dict[str, dict[str, Any]], cache_path: Path
) -> list[dict[str, Any]]:
    name = str(candidate["entity_name"])
    query = _identifier_query(candidate, name)
    if query is not None:
        rows = _result_rows(_sparql_json(query, cache=cache, cache_path=cache_path))
        if rows:
            return rows
    return _result_rows(_sparql_json(_label_query(name), cache=cache, cache_path=cache_path))


def _fetch_entity(qid: str, cache: dict[str, dict[str, Any]], cache_path: Path) -> dict[str, Any]:
    query = (
        "PREFIX wd: <http://www.wikidata.org/entity/> "
        "PREFIX wdt: <http://www.wikidata.org/prop/direct/> "
        "SELECT ?property ?value WHERE { wd:"
        + qid
        + " ?property ?value . FILTER(isLiteral(?value)) } LIMIT 500"
    )
    payload = _sparql_json(query, cache=cache, cache_path=cache_path)
    claims: dict[str, list[dict[str, Any]]] = {}
    for binding in payload.get("results", {}).get("bindings", []):
        prop = _binding_value(binding, "property").rsplit("/", 1)[-1]
        value = _binding_value(binding, "value")
        claims.setdefault(prop, []).append({"mainsnak": {"datavalue": {"value": value}}})
    return {"claims": claims}


def _progress(
    index: int,
    total: int,
    candidate: dict[str, str | None],
    row: dict[str, str] | None,
) -> None:
    width = 20
    filled = round(width * index / total) if total else width
    bar = "#" * filled + "-" * (width - filled)
    name = candidate["entity_name"]
    if row is None:
        print(f"[wikidata] [{bar}] {index}/{total} {name}", flush=True)
    else:
        print(
            f"[wikidata] [{bar}] {index}/{total} {name} -> {row['match_type']} {row['qid']}",
            flush=True,
        )


def cmd_sync(apply: bool, db: Path | None, limit: int | None, identified_only: bool) -> int:
    conn = connect(db or DEFAULT_DB_PATH)
    try:
        cache = _read_cache(CACHE_PATH)

        def search(candidate: dict[str, str | None]) -> list[dict[str, Any]]:
            return _search(candidate, cache, CACHE_PATH)

        def fetch_entity(qid: str) -> dict[str, Any]:
            return _fetch_entity(qid, cache, CACHE_PATH)

        rows = build_rows(
            conn,
            search=search,
            fetch_entity=fetch_entity,
            limit=limit,
            identified_only=identified_only,
            progress=_progress,
        )
    finally:
        conn.close()
    manual = _read_csv(MANUAL_PATH)
    resolved = {_normalise(str(row["entity_name"])): row for row in rows}
    resolved.update(manual)
    exact = sum(row.get("match_type") == "exactMatch" for row in resolved.values())
    close = sum(row.get("match_type") == "closeMatch" for row in resolved.values())
    print(f"[wikidata] {len(resolved)} row(s), exact={exact}, close={close}")
    if apply:
        _write_parquet(PARQUET_PATH, list(resolved.values()))
        print(f"[wikidata] wrote {PARQUET_PATH.relative_to(REPO_ROOT)}")
    else:
        print("[wikidata] dry-run — pass --apply to write sidecar")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sync", "converge"), nargs="?", default="sync")
    parser.add_argument("--apply", action="store_true", help="write sidecar or mappings")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--identified-only", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "converge":
        return cmd_converge(args.apply, args.db)
    return cmd_sync(args.apply, args.db, args.limit, args.identified_only)


if __name__ == "__main__":
    raise SystemExit(main())
