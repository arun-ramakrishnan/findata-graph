#!/usr/bin/env python3
"""ticker_hygiene.py — resolve scrip-code tickers to real Yahoo symbols (S1).

doc/improvements/proposals/d14_stub_ticker_and_industry_convergence.md §S1.

The D14 universe seeder (exchange_sync.py) mints BSE stubs whose ``ticker``
is the numeric scrip code (``531120.BO``) — BSE's own identifier. Yahoo
Finance uses alphabetic symbols on both Indian exchanges (``PATELENG.NS``),
so every yfinance consumer silently starves on these rows: the 21-target
diagnosis measured ``yf.Ticker('531120.BO').info`` at 13 empty keys vs 162
for a real symbol (metrics lane, industry labels, GF fallback all dead).

This lane resolves them with the EXISTING search machinery —
``helpers.core.get_tickers.search_ticker`` (name -> symbol via yf.Search +
word-overlap validation) guarded by ``enrich_from_yfinance.identity_ok``
(token-subset identity) — and writes ``entities.ticker``. Nothing is
written without the guard; foreign numeric tickers (``006400.KS``,
``1810.HK``) are REAL Yahoo symbols and stay out of scope.

Report shape follows enrich_relations (``## ticker_issues`` with
``| Name | Ticker |`` rows) so a downstream pass can consume it exactly
the way the GF fallback consumes the yfinance report
(``enrich_relations.load_gf_targets``).

Usage:
    python3 helpers/maintenance/ticker_hygiene.py                  # dry-run
    python3 helpers/maintenance/ticker_hygiene.py --apply          # write
    python3 helpers/maintenance/ticker_hygiene.py --fetch-only     # stage only (chain phase A)
    python3 helpers/maintenance/ticker_hygiene.py --apply-only --apply  # apply staged (phase B)

Standalone tool. Not part of maint / maint-full; joins the refresh chain
as its own lane (refresh_chain_parallel S4 wiring).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import yfinance as yf  # noqa: F401  # imported for the logger guard below

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# --- project bootstrap -------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core import jsonio  # noqa: E402  (orjson parse cutover; dumps stay stdlib per db_scaling_tuning §6.3 criteria)
from helpers.core.db import connect  # noqa: E402
from helpers.core.get_tickers import get_basic_info, search_ticker  # noqa: E402
from helpers.maintenance.enrich_from_yfinance import identity_ok  # noqa: E402
from helpers.maintenance.exchange_search import bse_search_cached  # noqa: E402

# --- constants ---------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("ticker-hygiene")

DB_PATH = PROJECT_ROOT / "memory" / "research.db"
REPORT_PATH = PROJECT_ROOT / "outputs" / "ticker_hygiene_report.md"
STAGE_PATH = (
    Path(os.environ.get("TMPDIR", "/mnt/data/tmp/opencode"))
    / "findata-graph"
    / "ticker_hygiene_stage.jsonl"
)
BSE_CACHE_DIR = (
    Path(os.environ.get("TMPDIR", "/mnt/data/tmp/opencode")) / "findata-graph" / "bse_search_cache"
)

# Indian Yahoo symbols are alphabetic; a numeric local part on .NS/.BO is
# always the exchange's own scrip code, never a Yahoo symbol.
_INDIAN_SUFFIXES = (".NS", ".BO")


def is_scrip_ticker(ticker: str | None) -> bool:
    """True for Indian-exchange tickers whose local part is all digits.

    ``531120.BO`` / ``544364.BO`` -> True (BSE scrip codes, un-fetchable).
    ``PATELENG.NS`` / ``5PAISA.NS`` -> False (real symbols — digits allowed,
    just not a purely numeric local part). ``006400.KS`` / ``1810.HK`` ->
    False (foreign exchanges where numeric local parts ARE the Yahoo symbol).
    """
    if not ticker:
        return False
    local, sep, suffix = ticker.partition(".")
    return sep != "" and local.isdigit() and f".{suffix}" in _INDIAN_SUFFIXES


def select_candidates(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(name, ticker) for every company carrying a scrip-code ticker."""
    rows = conn.execute(
        "SELECT name, ticker FROM entities "
        "WHERE entity_type = 'company' AND ticker IS NOT NULL AND ticker != ''"
    ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows if is_scrip_ticker(str(r[1]))]


def select_untickered(
    conn: sqlite3.Connection, *, notes_only: bool = False
) -> list[tuple[str, str]]:
    """(name, "") for companies with NO ticker (thin_tail S1c).

    Same guarded pipeline as the scrip lane (search_ticker + identity_ok);
    these rows were invisible to every yfinance consumer and to the
    financial census. Old ticker is "" — apply pins on the empty/null
    state so a concurrently-resolved row is never clobbered.

    ``notes_only`` restricts to note-backed entities (``file_path``
    populated): the graph holds ~16k entity rows that never got a note;
    resolving those is a separate hygiene pass, not this lane's scope.
    """
    sql = (
        "SELECT name FROM entities "
        "WHERE entity_type = 'company' AND (ticker IS NULL OR ticker = '')"
    )
    if notes_only:
        sql += " AND file_path IS NOT NULL AND file_path != ''"
    rows = conn.execute(sql).fetchall()
    return [(str(r[0]), "") for r in rows]


def resolve_one(
    name: str,
    old_ticker: str,
    *,
    min_subset_tokens: int = 1,
    foreign_ok: bool = False,
) -> dict:
    """Resolve one scrip-code candidate to a record (network: one search).

    Statuses:
      resolved        guarded symbol found — safe to write
      unresolved      search returned nothing (delisted, mangled name, …)
      foreign_symbol  best symbol is not .NS/.BO — refused, never written
                      (unless ``foreign_ok``: then the SAME identity guard
                      decides — validated foreign listings resolve, e.g.
                      PepsiCo -> PEP; noise like Accel -> Acccelevation
                      still refuses, thin_tail S1c)
      guard_refused   identity_ok says the info describes another company

    ``min_subset_tokens=2`` (untickered lane) tightens the identity guard
    against one-word note names subset-matching unrelated multi-word
    securities (``Take`` -> Take-Two, ``GEE`` -> Geely).

    ``new_ticker`` is kept on refused rows for the report, but apply only
    ever touches status == resolved.
    """
    symbol, info = search_ticker(name)
    yf_name = (info or {}).get("longName") or (info or {}).get("shortName")
    rec: dict = {
        "name": name,
        "old_ticker": old_ticker,
        "new_ticker": None,
        "yf_name": yf_name,
        "status": "unresolved",
    }
    if not symbol or not info:
        return rec
    if not symbol.endswith(_INDIAN_SUFFIXES):
        rec["new_ticker"] = symbol
        if foreign_ok and identity_ok(name, info, min_subset_tokens=2):
            rec["status"] = "resolved"
            rec["via"] = "foreign"
        else:
            rec["status"] = "foreign_symbol"
        return rec
    if not identity_ok(name, info, min_subset_tokens=min_subset_tokens):
        rec["new_ticker"] = symbol
        rec["status"] = "guard_refused"
        return rec
    rec["new_ticker"] = symbol
    rec["status"] = "resolved"
    return rec


def resolve_tier2(name: str, old_ticker: str, cache_dir: Path | None = None) -> dict:
    """BSE PeerSmartSearch fallback for tier-1 failures — exchange_search is
    the GF ticker-fallback's tier 2, same precedence.

    The BSE response is matched by SCRIP CODE, not name: the row whose
    ``scrip`` equals ours is BSE's own registry entry for this listing and
    carries the NSE-style symbol + BSE's current listed name. Candidate
    Yahoo symbols are ``SYMBOL.NS`` then ``SYMBOL.BO`` — each verified live
    via get_basic_info + identity_ok before it can reach the store. A live
    listing that fails the guard is a RENAME (BSE's name is current, ours
    is stale) — reported as tier2_refused for the rename pass, never written.
    """
    rec: dict = {
        "name": name,
        "old_ticker": old_ticker,
        "new_ticker": None,
        "yf_name": None,
        "status": "tier2_no_bse_row",
        "via": "tier2",
    }
    try:
        matches, _ = bse_search_cached(name, cache_dir or BSE_CACHE_DIR)
    except Exception as exc:  # noqa: BLE001  # one dead lookup must not kill the sweep
        rec["status"] = "tier2_error"
        rec["yf_name"] = str(exc)[:80]
        return rec
    scrip = old_ticker.split(".", 1)[0]
    row = next((m for m in matches if m.scrip == scrip and m.symbol), None)
    if row is None:
        return rec
    rec["yf_name"] = row.name  # BSE's current listed name — rename evidence
    for suffix in _INDIAN_SUFFIXES:
        cand = row.symbol + suffix
        info = get_basic_info(cand)
        if not info:
            continue
        if identity_ok(name, info):
            rec["new_ticker"] = cand
            rec["yf_name"] = info.get("longName") or info.get("shortName") or row.name
            rec["status"] = "resolved"
            return rec
        rec["yf_name"] = info.get("longName") or info.get("shortName")
        rec["status"] = "tier2_refused"
    return rec


def resolve_tier3(name: str, old_ticker: str, kite_map: dict[str, str]) -> dict:
    """Direct-symbol probe from the local BSE master's kite_tradingsymbol.

    yf.Search failed for these — search indexes name TEXT, which ETF/InvIT/
    renamed listings are poor at; a direct symbol lookup needs no index.
    Tries ``KITE.BO`` then ``KITE.NS`` (the local master's Zerodha symbol,
    keyed by our scrip code), each verified live via get_basic_info +
    identity_ok. A live listing that fails the guard is rename EVIDENCE —
    reported as tier3_rename for the rename pass, never written.
    """
    rec: dict = {
        "name": name,
        "old_ticker": old_ticker,
        "new_ticker": None,
        "yf_name": None,
        "status": "tier3_no_kite",
        "via": "tier3",
    }
    kite = kite_map.get(old_ticker.split(".", 1)[0])
    if not kite:
        return rec
    for suffix in _INDIAN_SUFFIXES:
        cand = kite + suffix
        info = get_basic_info(cand)
        if not info:
            continue
        if identity_ok(name, info):
            rec["new_ticker"] = cand
            rec["yf_name"] = info.get("longName") or info.get("shortName")
            rec["status"] = "resolved"
            return rec
        rec["yf_name"] = info.get("longName") or info.get("shortName")
        rec["status"] = "tier3_rename"  # live listing under a different name
        return rec
    return {**rec, "status": "tier3_dead"}  # kite known, no live Yahoo listing


def _load_kite_map() -> dict[str, str]:
    """scrip -> kite_tradingsymbol from the local BSE master (sources.duckdb).

    Read-only DuckDB side channel for tier-3; the lane's store writes stay
    SQLite-only via helpers.core.db.connect.
    """
    import duckdb

    dcon = duckdb.connect(str(PROJECT_ROOT / "memory" / "data" / "sources.duckdb"), read_only=True)
    try:
        rows = dcon.execute(
            "SELECT symbol, kite_tradingsymbol FROM exchange_listings WHERE exchange = 'BSE'"
        ).fetchall()
    finally:
        dcon.close()
    return {str(s): str(k) for s, k in rows if s and k}


def _load_stage(stage_path: Path) -> dict[str, dict]:
    """name -> staged record; missing or corrupt file -> {} (fresh start)."""
    if not stage_path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in stage_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = jsonio.loads(line)
        except jsonio.JSONDecodeError:
            log.warning("skipping corrupt stage line in %s", stage_path)
            continue
        out[str(rec["name"])] = rec
    return out


def _append_stage(stage_path: Path, records: list[dict]) -> int:
    """Append records to the JSONL stage (extend-only; resume-safe)."""
    if not records:
        return 0
    stage_path.parent.mkdir(parents=True, exist_ok=True)
    with open(stage_path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)


def apply_resolved(
    conn: sqlite3.Connection, records: list[dict], *, now: str | None = None
) -> tuple[int, int]:
    """Guarded UPDATE of entities.ticker for resolved records.

    The WHERE pins BOTH name and the old scrip ticker — a row whose ticker
    moved since staging (concurrent writer, operator fix) is skipped, never
    clobbered. Returns (applied, skipped_moved).
    """
    stamp = now or datetime.now(UTC).isoformat(timespec="seconds")
    applied = skipped = 0
    for rec in records:
        if rec.get("status") != "resolved" or not rec.get("new_ticker"):
            continue
        if rec["old_ticker"]:
            cur = conn.execute(
                "UPDATE entities SET ticker = ?, last_updated = ? WHERE name = ? AND ticker = ?",
                (rec["new_ticker"], stamp, rec["name"], rec["old_ticker"]),
            )
        else:
            # untickered lane: pin on the NULL/empty state (thin_tail S1c)
            cur = conn.execute(
                "UPDATE entities SET ticker = ?, last_updated = ? "
                "WHERE name = ? AND (ticker IS NULL OR ticker = '')",
                (rec["new_ticker"], stamp, rec["name"]),
            )
        if cur.rowcount:
            applied += 1
        else:
            skipped += 1
    conn.commit()
    return applied, skipped


_TICKER_EMPTY_RE = re.compile(r"(?m)^ticker:(?:\s*(?:null|''|\"\"))?\s*$")


def _set_note_ticker(text: str, symbol: str) -> str | None:
    """Point the frontmatter ``ticker:`` line at ``symbol``.

    Frontmatter block only; matches a null/empty ``ticker:`` line. Returns
    the new text, or None when there is no safe site (no frontmatter, or a
    populated ``ticker:`` line — never clobber a resolved value).
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    new_fm, n = _TICKER_EMPTY_RE.subn(f"ticker: {symbol}", text[:end], count=1)
    if n == 0:
        return None
    return new_fm + text[end:]


def apply_note_tickers(conn: sqlite3.Connection, records: list[dict]) -> tuple[int, int]:
    """Sync the note frontmatter ``ticker:`` line for resolved rows (S1c).

    ``entities.ticker`` is the reachability truth (what every yfinance
    consumer reads); the note frontmatter line is the census/display
    surface. Updated only when it holds null/empty — a populated line is
    left untouched and counted as skipped. Returns (updated, skipped).
    """
    updated = skipped = 0
    for rec in records:
        if rec.get("status") != "resolved" or not rec.get("new_ticker"):
            continue
        row = conn.execute(
            "SELECT file_path FROM entities WHERE name = ?", (rec["name"],)
        ).fetchone()
        fp = row["file_path"] if row else None
        if not fp:
            skipped += 1
            continue
        p = PROJECT_ROOT / fp
        if not p.exists():
            log.warning("note %r missing for %s — frontmatter not updated", fp, rec["name"])
            skipped += 1
            continue
        text = p.read_text(encoding="utf-8")
        new_text = _set_note_ticker(text, str(rec["new_ticker"]))
        if new_text is None:
            skipped += 1
            continue
        p.write_text(new_text, encoding="utf-8")
        updated += 1
    return updated, skipped


def append_report(
    path: Path,
    records: list[dict],
    *,
    mode: str,
    applied: int | None = None,
    skipped: int | None = None,
) -> None:
    """Append a run block in the enrich_relations report shape.

    ``## ticker_issues`` rows keep the exact two-cell ``| Name | Ticker |``
    shape ``load_gf_targets`` parses; resolution detail lives in its own
    section so the issues table stays machine-consumable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = [r for r in records if r["status"] == "resolved"]
    issues = [r for r in records if r["status"] != "resolved"]
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"# ticker_hygiene — {mode} report\n\n")
        f.write(
            f"**Generated:** {now}  ·  **Mode:** {mode}  ·  "
            f"**Candidates:** {len(records)}  ·  "
            f"**Resolved:** {len(resolved)}  ·  **Issues:** {len(issues)}\n\n"
        )
        if applied is not None:
            f.write(
                f"**Apply result:** applied={applied} skipped_moved={skipped} "
                f"({'would-write' if mode == 'dry-run' else 'written'} resolved rows)\n\n"
            )
        f.write("## resolved\n\n")
        f.write("| Name | old | new |\n|---|---|---|\n")
        for r in resolved:
            f.write(f"| {r['name']} | {r['old_ticker']} | {r['new_ticker']} |\n")
        if not resolved:
            f.write("| (none) | | |\n")
        f.write(
            "\n## ticker_issues  # unresolved / guard-refused / foreign — yfinance starves on these\n\n"
        )
        f.write("| Name | Ticker |\n|---|---|\n")
        for r in issues:
            f.write(f"| {r['name']} | {r['old_ticker']} |\n")
        if not issues:
            f.write("| (none) |  |\n")
        renames = [r for r in records if r.get("status") in ("tier3_rename", "tier2_refused")]
        f.write(
            "\n## renames  # live listing under a DIFFERENT name — entity-rename-pass evidence\n\n"
        )
        f.write("| Name | scrip | current exchange name |\n|---|---|---|\n")
        for r in renames:
            f.write(f"| {r['name']} | {r['old_ticker']} | {r.get('yf_name') or ''} |\n")
        if not renames:
            f.write("| (none) | | |\n")
        f.write("\n## detail\n\n")
        f.write("| Name | status | yf name | new |\n|---|---|---|---|\n")
        for r in sorted(records, key=lambda x: (x["status"], x["name"])):
            f.write(
                f"| {r['name']} | {r['status']} | {r.get('yf_name') or ''} | {r.get('new_ticker') or ''} |\n"
            )
        f.write("\n")


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    parser = argparse.ArgumentParser(
        description="Resolve scrip-code tickers (531120.BO) to real Yahoo symbols."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write resolved symbols to entities.ticker (default: dry-run report)",
    )
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="resolve + stage to scratch JSONL (no store writes); chain phase A",
    )
    parser.add_argument(
        "--apply-only",
        action="store_true",
        help="apply staged results (no fetch); chain phase B — needs --apply to write",
    )
    parser.add_argument(
        "--company", type=str, default=None, help="restrict to a name/ticker substring"
    )
    parser.add_argument(
        "--untickered",
        action="store_true",
        help="resolve companies with NO ticker instead of scrip-code rows "
        "(thin_tail S1c); guarded search_ticker + identity_ok, applies "
        "entities.ticker AND the note frontmatter `ticker:` line on --apply",
    )
    parser.add_argument(
        "--notes-only",
        action="store_true",
        help="with --untickered: restrict to note-backed entities "
        "(file_path populated). The graph holds ~16k non-note company rows; "
        "resolving those is out of this lane's scope. STRONGLY recommended.",
    )
    parser.add_argument("--limit", type=int, default=None, help="cap candidates (smoke runs)")
    parser.add_argument(
        "--sleep", type=float, default=0.25, help="seconds between network resolutions"
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="re-fetch staged names whose last attempt did not resolve",
    )
    parser.add_argument(
        "--tier2",
        action="store_true",
        help="BSE scrip-match fallback (exchange_search) for tier-1 failures",
    )
    parser.add_argument(
        "--tier3",
        action="store_true",
        help="direct-symbol probe from local kite_tradingsymbol for tier-1 failures",
    )
    parser.add_argument("--db", type=str, default=str(DB_PATH))
    parser.add_argument("--stage", type=str, default=str(STAGE_PATH))
    parser.add_argument("--report", type=str, default=str(REPORT_PATH))
    args = parser.parse_args(argv)

    if args.notes_only and not args.untickered:
        print("[ticker-hygiene] --notes-only only applies to --untickered")
        return 2
    if args.fetch_only and args.apply_only:
        print("[ticker-hygiene] --fetch-only and --apply-only are mutually exclusive")
        return 2
    if args.fetch_only and args.apply:
        print("[ticker-hygiene] --fetch-only never writes; drop --apply")
        return 2
    if args.tier2 and args.apply_only:
        print("[ticker-hygiene] --tier2 is a fetch-phase fallback; drop --apply-only")
        return 2
    if args.tier3 and args.apply_only:
        print("[ticker-hygiene] --tier3 is a fetch-phase fallback; drop --apply-only")
        return 2

    stage_path = Path(args.stage)
    if args.untickered and args.stage == str(STAGE_PATH):
        # distinct default stage: the two populations must never merge
        stage_path = STAGE_PATH.with_name("ticker_hygiene_stage_untickered.jsonl")
    conn = connect(args.db)
    try:
        note_applied = note_skipped = 0
        applied = skipped = 0
        if args.apply_only:
            staged = _load_stage(stage_path)
            if not staged:
                log.error("no staged results at %s — run --fetch-only first", stage_path)
                return 1
            records = sorted(staged.values(), key=lambda r: r["name"])
            mode = "apply-only"
            if args.apply:
                applied, skipped = apply_resolved(conn, records)
                if args.untickered:
                    note_applied, note_skipped = apply_note_tickers(conn, records)
                mode = "apply-only+apply"
        else:
            candidates = (
                select_untickered(conn, notes_only=args.notes_only)
                if args.untickered
                else select_candidates(conn)
            )
            if args.company:
                needle = args.company.lower()
                candidates = [
                    (n, t) for n, t in candidates if needle in n.lower() or needle in t.lower()
                ]
            if args.limit:
                candidates = candidates[: args.limit]
            staged = _load_stage(stage_path)
            if args.retry_failed:
                todo = [
                    (n, t)
                    for n, t in candidates
                    if n not in staged or staged[n].get("status") != "resolved"
                ]
            else:
                todo = [(n, t) for n, t in candidates if n not in staged]
            log.info(
                "candidates=%d staged_skip=%d todo=%d (sleep=%.2fs)",
                len(candidates),
                len(candidates) - len(todo),
                len(todo),
                args.sleep,
            )
            fresh: list[dict] = []
            for i, (name, old) in enumerate(todo, 1):
                rec = resolve_one(
                    name,
                    old,
                    min_subset_tokens=2 if args.untickered else 1,
                    foreign_ok=args.untickered,
                )
                fresh.append(rec)
                # incremental staging: each record hits the JSONL the moment
                # it resolves, so an interrupted 4k-candidate sweep resumes
                # from the last staged name instead of losing the whole run
                _append_stage(stage_path, [rec])
                if i % 100 == 0:
                    n_ok = sum(1 for r in fresh if r["status"] == "resolved")
                    log.info("  %d/%d done (resolved so far: %d)", i, len(todo), n_ok)
                if args.sleep and i != len(todo):
                    time.sleep(args.sleep)

            if args.tier2:
                still = [k for k, r in enumerate(fresh) if r["status"] != "resolved"]
                log.info("tier2 (BSE scrip match): %d candidates", len(still))
                for j, k in enumerate(still, 1):
                    rec = fresh[k]
                    rec2 = resolve_tier2(rec["name"], rec["old_ticker"])
                    fresh[k] = rec2
                    # later stage rows supersede earlier ones on load
                    _append_stage(stage_path, [rec2])
                    if j % 50 == 0:
                        n_ok = sum(1 for r in fresh if r["status"] == "resolved")
                        log.info(
                            "  tier2 %d/%d done (resolved so far: %d)",
                            j,
                            len(still),
                            n_ok,
                        )
                    if args.sleep and j != len(still):
                        time.sleep(args.sleep)

            if args.tier3:
                kite_map = _load_kite_map()
                still = [k for k, r in enumerate(fresh) if r["status"] != "resolved"]
                log.info("tier3 (kite direct-symbol probe): %d candidates", len(still))
                for j, k in enumerate(still, 1):
                    rec = fresh[k]
                    rec2 = resolve_tier3(rec["name"], rec["old_ticker"], kite_map)
                    fresh[k] = rec2
                    _append_stage(stage_path, [rec2])  # later row supersedes on load
                    if j % 50 == 0:
                        n_ok = sum(1 for r in fresh if r["status"] == "resolved")
                        log.info(
                            "  tier3 %d/%d done (resolved so far: %d)",
                            j,
                            len(still),
                            n_ok,
                        )
                    if args.sleep and j != len(still):
                        time.sleep(args.sleep)

            merged = {**staged, **{r["name"]: r for r in fresh}}
            records = sorted(merged.values(), key=lambda r: r["name"])
            mode = "fetch-only" if args.fetch_only else ("fetch+apply" if args.apply else "dry-run")
            if args.untickered:
                mode = f"untickered-{mode}"
            if args.apply and not args.fetch_only:
                applied, skipped = apply_resolved(conn, records)
                if args.untickered:
                    note_applied, note_skipped = apply_note_tickers(conn, records)

        counts = Counter(r["status"] for r in records)
        report_path = Path(args.report)
        append_report(
            report_path,
            records,
            mode=mode,
            applied=applied if (args.apply and not args.fetch_only) else None,
            skipped=skipped,
        )
        print(
            f"[ticker-hygiene] mode={mode} candidates={len(records)} "
            f"resolved={counts.get('resolved', 0)} unresolved={counts.get('unresolved', 0)} "
            f"guard_refused={counts.get('guard_refused', 0)} "
            f"foreign_symbol={counts.get('foreign_symbol', 0)} "
            f"tier2_ok={sum(1 for r in records if r.get('via') == 'tier2' and r['status'] == 'resolved')} "
            f"tier2_fail={sum(1 for r in records if str(r.get('status', '')).startswith('tier2'))} "
            f"tier3_ok={sum(1 for r in records if r.get('via') == 'tier3' and r['status'] == 'resolved')} "
            f"tier3_fail={sum(1 for r in records if str(r.get('status', '')).startswith('tier3'))} "
            f"applied={applied} skipped_moved={skipped} "
            f"note_tickers={note_applied}/{note_applied + note_skipped}"
        )
        print(f"[ticker-hygiene] stage={stage_path} report appended -> {report_path}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
