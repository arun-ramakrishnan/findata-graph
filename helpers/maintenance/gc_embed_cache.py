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
- Rows of a NON-active model are RETAINED, never deleted (2026-10-08).
  Each reference index declares its active model stamp (its info/meta
  ``embed_model``); the reference recipe is anchored to that stamp, so
  it can only prove dead-ness for the ACTIVE model's rows. A row whose
  model differs from its cohort's stamp is rollback insurance — the
  first multi-model survey flagged all 787 granite script rows "dead"
  the moment the surface flipped to gemma.
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
    # the index's ACTIVE model stamp (one-row, one-col SQL): rows of a
    # DIFFERENT model in this cohort are rollback insurance — retained,
    # never deleted, because the reference recipe is anchored to the
    # stamp and cannot prove other-model rows dead
    stamp_sql: str | None = None
    # per-stamp basis recipes: models that wrap the basis differently
    # (gemma prefixes it) hash DIFFERENT cache keys for the same row —
    # the recipe must follow the stamp or the ACTIVE model's live rows
    # all read "dead" (2026-10-08: the granite _doc_text recipe marked
    # all 550 gemma script rows dead)
    text_by_model: dict[str, Callable[[tuple], str]] | None = None
    # same dispatch for text_db Refs (company: the basis re-reads the
    # note through the connection), overriding text_db per stamp
    text_db_by_model: dict[str, Callable[[Any, tuple], str]] | None = None


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


def _company_text_gemma_db(conn, row: tuple) -> str:
    """The gemma company basis — the recipe the gemma populate actually
    hashed (same title/sector/body as the granite lane, prefixed); must
    match it or live rows read dead."""
    from helpers.graph.embeddings import _company_text_pair

    return _company_text_pair(conn, row[0] or "")[1]


def _script_text_gemma(row: tuple) -> str:
    """The gemma script basis (title/purpose prefixed) — the recipe the
    gemma rebuild actually hashed; must match it or live rows read dead."""
    from helpers.maintenance.rebuild_script_search import _gemma_basis

    return _gemma_basis(row[0] or "", row[1] or "", row[2] or "")


from helpers.core.gemma_embedder import MODEL_LABEL as _GEMMA_MODEL_LABEL  # noqa: E402


DEFAULT_REFS: tuple[Ref, ...] = (
    Ref(
        "doc",
        REPO / "memory/doc_search.db",
        "SELECT title, section_title, content FROM doc_search",
        _doc_text,
        stamp_sql="SELECT value FROM doc_search_info WHERE key = 'embed_model'",
    ),
    Ref(
        "script",
        REPO / "memory/script_search.db",
        "SELECT title, purpose, content FROM script_search",
        _doc_text,
        stamp_sql="SELECT value FROM script_search_info WHERE key = 'embed_model'",
        text_by_model={_GEMMA_MODEL_LABEL: _script_text_gemma},
    ),
    Ref(
        "memory",
        REPO / "memory/memory_search.db",
        "SELECT title, purpose, content FROM memory_search",
        _doc_text,
        stamp_sql="SELECT value FROM memory_search_info WHERE key = 'embed_model'",
        # same (title, purpose, content) shape as scripts — shared gemma
        # recipe
        text_by_model={_GEMMA_MODEL_LABEL: _script_text_gemma},
    ),
    Ref(
        "note",
        REPO / "memory/research.db",
        "SELECT title, sector, section_title, content FROM note_search",
        _note_text,
        stamp_sql="SELECT value FROM db_meta WHERE key = 'note_embed_model'",
    ),
    Ref(
        "convo",
        REPO / "memory/convo_search.duckdb",
        "SELECT snippet FROM convo_search",
        # convo_meta, NOT a *_search_info sibling: rebuild_convo_search writes
        # the stamp there (and the table name differs from every sqlite cohort,
        # so a copied stamp_sql would abort the whole GC on a missing table).
        # Armed for a future convo model swap: foreign-model rows become
        # rollback insurance instead of silently reading dead.
        stamp_sql="SELECT value FROM convo_meta WHERE key = 'embed_model'",
    ),
    Ref(
        "company",
        # the company lane (helpers/graph/embeddings.py): maintained by
        # populate_local --maint; its gemma basis re-reads the note (the
        # granite lane is the default text_db)
        REPO / "memory/research.db",
        "SELECT DISTINCT company_name FROM company_embeddings",
        text_db=_company_text_db,
        stamp_sql="SELECT model FROM company_embeddings LIMIT 1",
        text_db_by_model={_GEMMA_MODEL_LABEL: _company_text_gemma_db},
    ),
)

# A source whose extracted references cover less than this share of its own
# cache rows is treated as UNVERIFIED: the text basis drifted from the
# builder's, so its rows are reported and left alone rather than deleted.
# The ratio only means something at cohort scale — below MIN_GATE_ROWS a
# source with one stale row would score 0% and look broken.
MIN_COVERAGE = 0.25
MIN_GATE_ROWS = 200


def _open_ref(ref):
    """Open a reference index read-only: duckdb via the lock queue (a GC
    run must not die because a writer held the file), else sqlite."""
    if str(ref.path).endswith(".duckdb"):
        # S2/S3 (duckdb_transient_lock_retry): queue on the store's
        # io.lock and retry transient conflicts.
        from helpers.misc.duckdb_lock import open_read_only

        return open_read_only(ref.path)
    from helpers.core.db import connect

    return connect(ref.path, read_only=True)


def _ref_basis_text(ref, con, row, text_fn, stamps) -> str | None:
    """One row's cache-key basis text. The text_db variant reads the same
    db the row came from (hash INSIDE the connection scope)."""
    import sqlite3

    if ref.text_db is not None:
        if not isinstance(con, sqlite3.Connection):
            raise RuntimeError(f"{ref.label}: text_db needs a sqlite reference index")
        tdb = ref.text_db
        if ref.text_db_by_model and stamps.get(ref.label):
            tdb = ref.text_db_by_model.get(stamps[ref.label], ref.text_db)
        return tdb(con, row)
    return text_fn(row)


def _read_ref_rows(ref, con, stamps, counts, bucket, hash_fn) -> None:
    """Stamp + rows + hashes for one reference into the shared accumulators."""
    if ref.stamp_sql:
        srow = con.execute(ref.stamp_sql).fetchone()
        stamps[ref.label] = srow[0] if srow else None
    # the basis recipe follows the ACTIVE stamp (see Ref.text_by_model):
    # a prefixed model hashes different cache keys for the same rows
    text_fn = ref.text
    if ref.text_by_model and stamps.get(ref.label):
        text_fn = ref.text_by_model.get(stamps[ref.label], ref.text)
    rows = con.execute(ref.sql).fetchall()
    counts[ref.label] = len(rows)
    for row in rows:
        try:
            t = _ref_basis_text(ref, con, row, text_fn, stamps)
        except Exception as exc:  # noqa: BLE001  # a drifted basis must not delete
            raise RuntimeError(
                f"reference text basis failed for {ref.label} ({exc}) — refusing to GC"
            ) from exc
        if t:
            bucket.add(hash_fn(t))


def _read_refs(
    refs,
) -> tuple[dict[str, set[str]], dict[str, int], dict[str, str | None]]:
    """Live (text_hash) sets + active stamps per source; abort on failure."""
    from helpers.core.embed_cache import _hash

    live: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    stamps: dict[str, str | None] = {}
    for ref in refs:
        if not Path(ref.path).exists():
            raise RuntimeError(
                f"reference index missing: {ref.label} ({ref.path}) — refusing to GC; "
                "a partial reference set would delete live cache rows"
            )
        bucket = live.setdefault(ref.label, set())
        stamps[ref.label] = None
        try:
            con = _open_ref(ref)
            try:
                _read_ref_rows(ref, con, stamps, counts, bucket, _hash)
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001  # any driver error must refuse, not yield None
            # Broad on purpose (convo_embed_gc_stamp S3): the narrow
            # (sqlite3.Error, OSError, RuntimeError) tuple let a duckdb
            # CatalogException — e.g. a stamp_sql naming a table that does not
            # exist — escape raw, so the operator saw a driver message instead
            # of the house "refusing to GC" one. Same precedent and same
            # rationale as the text-basis guard in _read_ref_rows: an
            # unreadable reference must never be read as "no stamp".
            raise RuntimeError(
                f"reference index unreadable: {ref.label} ({ref.path}): {exc} — refusing to GC"
            ) from exc
    return live, counts, stamps


def _classify(
    refs,
) -> tuple[dict[str, set[str]], dict[str, int], dict[str, str], dict]:
    """Read refs, then mark any source whose coverage looks WRONG as
    'unverified' so its cache rows are never deleted."""
    live, counts, stamps = _read_refs(refs)
    from helpers.core.db import connect

    con = connect(STORE, read_only=True)
    try:
        rows = con.execute(f"SELECT model, source, text_hash FROM {CACHE_TABLE}").fetchall()  # noqa: S608
    finally:
        con.close()
    by_source: dict[str, int] = {}
    referenced: dict[str, int] = {}
    retained: dict[str, int] = {}
    for model, source, h in rows:
        by_source[source] = by_source.get(source, 0) + 1
        stamp = stamps.get(source)
        if stamp and model != stamp:
            # rollback insurance: the recipe is anchored to the ACTIVE
            # stamp, so "unreferenced" proves nothing for other models
            retained[source] = retained.get(source, 0) + 1
            continue
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
        share = (referenced.get(source, 0) + retained.get(source, 0)) / total
        if share < MIN_COVERAGE:
            unverified[source] = (
                f"only {share:.0%} of its {total} rows are referenced — "
                "text basis likely drifted from the builder; NOT deleting"
            )
    return (
        live,
        counts,
        unverified,
        {
            "rows": rows,
            "by_source": by_source,
            "referenced": referenced,
            "retained": retained,
            "stamps": stamps,
        },
    )


def survey(refs=DEFAULT_REFS) -> dict:
    """Report the live/dead split without writing (the --check path)."""
    live, counts, unverified, extra = _classify(refs)
    rows = extra["rows"]
    by_source = extra["by_source"]
    referenced = extra["referenced"]
    stamps = extra["stamps"]
    dead_by_source: dict[str, int] = {}
    retained_by_source: dict[str, int] = {}
    dead = 0
    for model, source, h in rows:
        if source in unverified:
            continue
        stamp = stamps.get(source)
        if stamp and model != stamp:
            retained_by_source[source] = retained_by_source.get(source, 0) + 1
            continue
        if not any(h in bucket for bucket in live.values()):
            dead += 1
            dead_by_source[source] = dead_by_source.get(source, 0) + 1
    return {
        "cache_rows": len(rows),
        "live": len(rows) - dead,
        "dead": dead,
        "retained": sum(retained_by_source.values()),
        "by_source": by_source,
        "dead_by_source": dead_by_source,
        "retained_by_source": retained_by_source,
        "referenced_by_source": referenced,
        "unverified": unverified,
        "stamps": stamps,
        "ref_rows": counts,
        "ref_hashes": {k: len(v) for k, v in live.items()},
        "store_mib": round(STORE.stat().st_size / 2**20, 1),
    }


def gc(refs=DEFAULT_REFS, apply: bool = False) -> dict:
    """Delete unreferenced ACTIVE-model cache rows (and VACUUM) when apply."""
    from datetime import datetime

    from helpers.maintenance.maint_timing import current as _mt_timer

    _mt = _mt_timer()  # None outside a CLI run (tests): all timing below is a no-op
    _survey_started = datetime.now()
    out = survey(refs)
    if _mt:
        _mt.record_phase(
            "survey", _survey_started, datetime.now(), extra=f"rows={out.get('cache_rows', 0)}"
        )
    if not apply:
        return out
    _evict_started = datetime.now()
    live, _counts, unverified, extra = _classify(refs)
    stamps = extra["stamps"]
    from helpers.core.db import connect

    try:
        con = connect(STORE)
    except sqlite3.Error as exc:
        raise RuntimeError(f"cache store not writable ({exc})") from exc
    try:
        rows = con.execute(f"SELECT rowid, model, source, text_hash FROM {CACHE_TABLE}").fetchall()  # noqa: S608
        dead_ids = [
            rid
            for rid, model, source, h in rows
            if source not in unverified
            and not any(h in bucket for bucket in live.values())
            and not (stamps.get(source) and model != stamps[source])
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
    if _mt:
        _mt.record_phase(
            "evict_vacuum", _evict_started, datetime.now(), extra=f"deleted={len(dead_ids)}"
        )
    return out


def summary(out: dict) -> str:
    lines = [
        f"embed_cache: {out['cache_rows']} rows / {out['store_mib']} MiB "
        f"— live {out['live']}, DEAD {out['dead']}"
        + (
            f", retained {out.get('retained', 0)} (other-model rollback)"
            if out.get("retained")
            else ""
        )
    ]
    for src, n in sorted(out["by_source"].items(), key=lambda kv: (-kv[1], kv[0])):
        d = out["dead_by_source"].get(src, 0)
        ref = out["referenced_by_source"].get(src, 0)
        r = out.get("retained_by_source", {}).get(src, 0)
        extra = f"  retained {r:>7}" if r else ""
        lines.append(f"  {str(src):10} {n:>7} rows  referenced {ref:>7}  dead {d:>7}{extra}")
    for src, why in sorted(out.get("unverified", {}).items()):
        lines.append(f"  {str(src):10} UNVERIFIED — {why}")
    lines.append("  reference rows: " + ", ".join(f"{k} {v}" for k, v in out["ref_rows"].items()))
    lines.append(
        "  distinct hashes: " + ", ".join(f"{k} {v}" for k, v in out.get("ref_hashes", {}).items())
    )
    if out.get("deleted") is not None:
        lines.append(f"  deleted {out['deleted']} rows; store now {out['store_mib_after']} MiB")
    return "\n".join(lines)


def retire(model: str, source: str | None = None, apply: bool = False) -> dict:
    """Explicitly retire ONE model's rows (optionally one cohort).

    The operator-invoked counterpart to the stamp-aware retention rule:
    passive GC can never delete other-model rows, so retiring a model's
    cache (e.g. the granite script rows after a gemma cutover) is a
    deliberate, named action. Refuses when ``model`` is the ACTIVE stamp
    of any cohort it would touch — that is live data, not old rows. The
    retired rows are the model-switch-back insurance: a later switch
    re-embeds that cohort from scratch.
    """
    from helpers.core.db import connect

    stamps = _read_refs(DEFAULT_REFS)[2]
    scope = "model = ?" + (" AND source = ?" if source else "")  # noqa: S608
    params = (model, source) if source else (model,)
    con = connect(STORE, read_only=True)
    try:
        victims = con.execute(
            f"SELECT source, COUNT(*) FROM {CACHE_TABLE} WHERE {scope} GROUP BY source",  # noqa: S608
            params,
        ).fetchall()
    finally:
        con.close()
    active_hits = [s for s, _n in victims if stamps.get(s) == model]
    if active_hits:
        raise RuntimeError(
            f"{model!r} is the ACTIVE stamp of {active_hits} — refusing to "
            "retire live rows. Retirement is PER-COHORT (the same model is "
            "often active elsewhere): scope it with --source to the cohort "
            "whose fallback rows you mean, or switch that surface's stamp "
            "first."
        )
    out = {
        "retire_model": model,
        "retire_source": source,
        "would_delete": dict(victims),
        "total": sum(n for _s, n in victims),
    }
    if not apply:
        return out
    con = connect(STORE)
    try:
        cur = con.execute(f"DELETE FROM {CACHE_TABLE} WHERE {scope}", params)  # noqa: S608
        con.commit()
        con.execute("VACUUM")
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            f"cache store busy ({exc}) — do not retire while a rebuild runs"
        ) from exc
    finally:
        con.close()
    out["deleted"] = cur.rowcount or 0
    out["store_mib_after"] = round(STORE.stat().st_size / 2**20, 1)
    return out


def retire_summary(out: dict) -> str:
    lines = [
        f"retire {out['retire_model']!r}"
        + (f" in source {out['retire_source']!r}" if out["retire_source"] else " (all cohorts)")
        + f": {out['total']} rows"
    ]
    for src, n in sorted(out["would_delete"].items()):
        lines.append(f"  {str(src):10} {n:>7} rows")
    if out.get("deleted") is not None:
        lines.append(f"  deleted {out['deleted']} rows; store now {out['store_mib_after']} MiB")
    elif out["total"]:
        lines.append("  dry-run — pass --apply to delete")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--apply",
        action="store_true",
        help="delete dead rows + VACUUM (default: report only, exit 1 if dead)",
    )
    p.add_argument(
        "--retire-model",
        metavar="MODEL",
        help="explicitly delete ONE model's cache rows (rollback insurance) "
        "instead of the passive dead-text GC; refused when MODEL is an "
        "active stamp. Dry-run unless --apply.",
    )
    p.add_argument(
        "--source",
        metavar="COHORT",
        help="scope --retire-model to one cohort (e.g. script)",
    )
    args = p.parse_args(argv)
    from helpers.maintenance.maint_timing import RunTimer, active

    if args.retire_model:
        mode = f"retire:{args.retire_model}" + ("" if args.apply else ":dry-run")
    else:
        mode = "apply" if args.apply else "report"
    timer = RunTimer("gc_embed_cache", mode=mode)
    with active(timer):
        try:
            if args.retire_model:
                out = retire(args.retire_model, args.source, apply=args.apply)
                print(retire_summary(out), file=sys.stderr)
                if args.apply:
                    return timer.finish(0, retire_summary(out).split("\n")[0][:500])
                return timer.finish(
                    1 if out["total"] else 0, retire_summary(out).split("\n")[0][:500]
                )
            out = gc(apply=args.apply)
        except RuntimeError as exc:
            print(f"embed-gc: {exc}", file=sys.stderr)
            return timer.finish(2, f"embed-gc: {exc}"[:500])
    print(summary(out), file=sys.stderr)
    if args.apply:
        return timer.finish(0, summary(out).split("\n")[0][:500])
    return timer.finish(1 if out["dead"] else 0, summary(out).split("\n")[0][:500])


if __name__ == "__main__":
    sys.exit(main())
