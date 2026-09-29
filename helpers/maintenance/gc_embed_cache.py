#!/usr/bin/env python3
"""Evict dead rows from the shared embed cache (bloat control, T3).

The embed cache (``memory/embed_store.db`` → ``embed_cache``, keyed
``(text_hash, model)``) is append-only by nature, so rows from finished
trials, spikes and abandoned lanes sit next to live ones forever: at the
first convo cold run it held 23,648 rows / 76.9 MiB, of which 1,222 were
a ``company`` trial lane that no index references any more.

Eviction is SAFE BY CONSTRUCTION — the cache is a pure function of
(text, model), so deleting a row can only cost a recompute, never
correctness. What makes it useful is that deletion is the only way the
file shrinks (SQLite does not return pages on DELETE; APPLY VACUUMs).

Two guard rails, both fail-safe:

- Only ``embed_cache`` is touched. ``note_search_vec*`` holds the REAL
  note vectors and is never a GC candidate.
- If any reference index is missing or unreadable the run ABORTS. A
  half-visible reference set would otherwise delete live rows; aborting
  costs a re-embed, deleting live rows costs correctness of nothing but
  time — so the tool refuses to guess.
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable
from typing import Any
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

REPO = _REPO
STORE = REPO / "memory/embed_store.db"
CACHE_TABLE = "embed_cache"


@dataclass(frozen=True)
class Ref:
    """One reference index: how to read it AND how it builds embed text.

    ``text`` matters as much as the path. Every legacy index embeds a
    COMPOSED basis, not its stored column: doc/script use
    ``title\\nsection\\nbody[:4000]`` and note uses
    ``title\\nsector\\nsection\\nbody[:cap]`` (their builders' own
    helpers). Hashing the raw column made 23,595 LIVE cache rows look
    dead on the first survey — the coverage gate below is what stops
    that class of bug from ever deleting.
    """

    label: str
    path: Path
    sql: str
    text: Callable[[tuple], str] = lambda row: row[0]  # noqa: ARG005
    # for indexes whose embed text is derived from the DB itself (company
    # notes): called with the open read-only connection
    text_db: Callable[[Any, tuple], str] | None = None


def _doc_text(row: tuple) -> str:
    from helpers.maintenance.rebuild_doc_search import _EMBED_BODY_CAP

    title, section, content = row
    return f"{title}\n{section}\n{(content or '')[:_EMBED_BODY_CAP]}"


def _note_text(row: tuple) -> str:
    from helpers.maintenance.rebuild_note_search import _embedding_text

    return _embedding_text(row[0] or "", row[1] or "", row[2] or "", row[3] or "")


def _company_text_db(conn, row: tuple) -> str:
    """company_embeddings embeds the company's NOTE text, not its name."""
    from helpers.graph.embeddings import _get_company_text

    return _get_company_text(conn, row[0] or "")


DEFAULT_REFS: tuple[Ref, ...] = (
    Ref(
        "doc",
        REPO / "memory/doc_search.db",
        "SELECT title, section_title, content FROM doc_search",
        _doc_text,
    ),
    Ref(
        "script",
        REPO / "memory/script_search.db",
        "SELECT title, purpose, content FROM script_search",
        _doc_text,
    ),
    Ref(
        "note",
        REPO / "memory/research.db",
        "SELECT title, sector, section_title, content FROM note_search",
        _note_text,
    ),
    Ref("convo", REPO / "memory/convo_search.duckdb", "SELECT snippet FROM convo_search"),
    # the company lane (helpers/graph/embeddings.py) — a trial cohort that
    # nothing rebuilds on demand, so its rows are pure bloat
    Ref(
        "company",
        REPO / "memory/research.db",
        "SELECT DISTINCT company_name FROM company_embeddings",
        text_db=_company_text_db,
    ),
)

# A source whose extracted references cover less than this share of its own
# cache rows is treated as UNVERIFIED: the text basis drifted from the
# builder's, so its rows are reported and left alone rather than deleted.
# The ratio only means something at cohort scale — below MIN_GATE_ROWS a
# source with one stale row would score 0% and look broken.
MIN_COVERAGE = 0.25
MIN_GATE_ROWS = 200


def _read_refs(refs) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Live (text_hash) sets per source label; abort on any read failure."""
    from helpers.core.embed_cache import _hash

    live: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    for ref in refs:
        if not Path(ref.path).exists():
            raise RuntimeError(
                f"reference index missing: {ref.label} ({ref.path}) — refusing to GC; "
                "a partial reference set would delete live cache rows"
            )
        bucket = live.setdefault(ref.label, set())
        try:
            if str(ref.path).endswith(".duckdb"):
                # S2/S3 (duckdb_transient_lock_retry): queue on the
                # store's io.lock and retry transient conflicts — a GC
                # run must not die because a writer held the file.
                from helpers.misc.duckdb_lock import open_read_only

                con = open_read_only(ref.path)
            else:
                con = sqlite3.connect(f"file:{ref.path}?mode=ro", uri=True, timeout=10)
            try:
                rows = con.execute(ref.sql).fetchall()
                counts[ref.label] = len(rows)
                # hash INSIDE the connection scope: a text_db basis (company
                # notes) reads the same db it came from
                for row in rows:
                    try:
                        if ref.text_db is not None:
                            if not isinstance(con, sqlite3.Connection):
                                raise RuntimeError(
                                    f"{ref.label}: text_db needs a sqlite reference index"
                                )
                            t = ref.text_db(con, row)
                        else:
                            t = ref.text(row)
                    except Exception as exc:  # noqa: BLE001  # a drifted basis must not delete
                        raise RuntimeError(
                            f"reference text basis failed for {ref.label} ({exc}) — refusing to GC"
                        ) from exc
                    if t:
                        bucket.add(_hash(t))
            finally:
                con.close()
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            raise RuntimeError(
                f"reference index unreadable: {ref.label} ({ref.path}): {exc} — refusing to GC"
            ) from exc
    return live, counts


def _classify(refs) -> tuple[dict[str, set[str]], dict[str, int], dict[str, str], dict]:
    """Read refs, then mark any source whose coverage looks WRONG as
    'unverified' so its cache rows are never deleted."""
    live, counts = _read_refs(refs)
    con = sqlite3.connect(f"file:{STORE}?mode=ro", uri=True, timeout=10)
    try:
        rows = con.execute(f"SELECT model, source, text_hash FROM {CACHE_TABLE}").fetchall()  # noqa: S608
    finally:
        con.close()
    by_source: dict[str, int] = {}
    referenced: dict[str, int] = {}
    for _model, source, h in rows:
        by_source[source] = by_source.get(source, 0) + 1
        if any(h in bucket for bucket in live.values()):
            referenced[source] = referenced.get(source, 0) + 1
    unverified: dict[str, str] = {}
    for source, total in by_source.items():
        if source not in live:
            # no reference index claims this source label, so we cannot know
            # what text basis it embeds — never delete on a guess
            unverified[source] = "no reference index for this source label"
            continue
        if total < MIN_GATE_ROWS:
            continue  # ratio is noise at this scale; absolute risk is a few rows
        share = referenced.get(source, 0) / total
        if share < MIN_COVERAGE:
            unverified[source] = (
                f"only {share:.0%} of its {total} rows are referenced — "
                "text basis likely drifted from the builder; NOT deleting"
            )
    return (
        live,
        counts,
        unverified,
        {"rows": rows, "by_source": by_source, "referenced": referenced},
    )


def survey(refs=DEFAULT_REFS) -> dict:
    """Report the live/dead split without writing (the --check path)."""
    live, counts, unverified, extra = _classify(refs)
    rows = extra["rows"]
    by_source = extra["by_source"]
    referenced = extra["referenced"]
    dead_by_source: dict[str, int] = {}
    dead = 0
    for _model, source, h in rows:
        if source in unverified:
            continue
        if not any(h in bucket for bucket in live.values()):
            dead += 1
            dead_by_source[source] = dead_by_source.get(source, 0) + 1
    return {
        "cache_rows": len(rows),
        "live": len(rows) - dead,
        "dead": dead,
        "by_source": by_source,
        "dead_by_source": dead_by_source,
        "referenced_by_source": referenced,
        "unverified": unverified,
        "ref_rows": counts,
        "ref_hashes": {k: len(v) for k, v in live.items()},
        "store_mib": round(STORE.stat().st_size / 2**20, 1),
    }


def gc(refs=DEFAULT_REFS, apply: bool = False) -> dict:
    """Delete unreferenced cache rows (and VACUUM) when ``apply``."""
    out = survey(refs)
    if not apply:
        return out
    live, _counts, unverified, extra = _classify(refs)
    try:
        con = sqlite3.connect(str(STORE), timeout=30)
    except sqlite3.Error as exc:
        raise RuntimeError(f"cache store not writable ({exc})") from exc
    try:
        rows = con.execute(f"SELECT rowid, source, text_hash FROM {CACHE_TABLE}").fetchall()  # noqa: S608
        dead_ids = [
            rid
            for rid, source, h in rows
            if source not in unverified and not any(h in bucket for bucket in live.values())
        ]
        if dead_ids:
            con.executemany(
                f"DELETE FROM {CACHE_TABLE} WHERE rowid = ?",  # noqa: S608
                [(rid,) for rid in dead_ids],
            )
            con.commit()
        con.execute("VACUUM")
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            f"cache store busy ({exc}) — do not GC while an embed rebuild runs"
        ) from exc
    finally:
        con.close()
    out["deleted"] = len(dead_ids)
    out["store_mib_after"] = round(STORE.stat().st_size / 2**20, 1)
    return out


def summary(out: dict) -> str:
    lines = [
        f"embed_cache: {out['cache_rows']} rows / {out['store_mib']} MiB "
        f"— live {out['live']}, DEAD {out['dead']}"
    ]
    for src, n in sorted(out["by_source"].items(), key=lambda kv: (-kv[1], kv[0])):
        d = out["dead_by_source"].get(src, 0)
        ref = out["referenced_by_source"].get(src, 0)
        lines.append(f"  {str(src):10} {n:>7} rows  referenced {ref:>7}  dead {d:>7}")
    for src, why in sorted(out.get("unverified", {}).items()):
        lines.append(f"  {str(src):10} UNVERIFIED — {why}")
    lines.append("  reference rows: " + ", ".join(f"{k} {v}" for k, v in out["ref_rows"].items()))
    lines.append(
        "  distinct hashes: " + ", ".join(f"{k} {v}" for k, v in out.get("ref_hashes", {}).items())
    )
    if out.get("deleted") is not None:
        lines.append(f"  deleted {out['deleted']} rows; store now {out['store_mib_after']} MiB")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--apply",
        action="store_true",
        help="delete dead rows + VACUUM (default: report only, exit 1 if dead)",
    )
    args = p.parse_args(argv)
    try:
        out = gc(apply=args.apply)
    except RuntimeError as exc:
        print(f"embed-gc: {exc}", file=sys.stderr)
        return 2
    print(summary(out), file=sys.stderr)
    if args.apply:
        return 0
    return 1 if out["dead"] else 0


if __name__ == "__main__":
    sys.exit(main())
