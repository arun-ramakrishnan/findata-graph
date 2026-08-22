#!/usr/bin/env python3
"""Derive the ``events`` timeline table (D7 — temporal spine).

Events are TIMESTAMPED HAPPENINGS (acquisitions, JVs, guidance, management
changes) that give every company a timeline. Today only the ``acquired`` edge
carries ``valid_from`` (16/25 rows); guidance and management changes have no
representation at all. This script builds the ``events`` table — the roadmap's
"first-class events concept (entity, event_type, date, magnitude, source)" —
in two arms:

  * ARM 1 (promote) — lift existing ``graph_edges`` rows into event rows.
    ``acquired`` -> ``acquisition`` and ``jv_with`` -> ``jv``. Near-zero new
    false-positive risk: the counterparty + date were already resolved by
    ``extract_relations.py`` and ``backfill_valid_from.py``.
  * ARM 2 (extract) — NEW prose extraction for ``guidance`` (bold-metric
    bullet + fiscal-period token + metric signal) and ``management_change``
    (role-change verb + executive title). Both are narrow by design: the
    roadmap warns that prose-year mining on non-``acquired`` types is ~80%
    false positives (financial-statement dates, rename events, cross-sentence
    bleed). We confine that risk by requiring TWO independent signals per hit
    (a temporal/fiscal token AND a metric/change signal), so a bare "FY27 we
    expanded" or "the CEO said" does NOT trigger.

Date parsing reuses ``_extract_year_from_context`` from ``extract_relations.py``
(the proven, plausibility-filtered helper). Earnings is deliberately absent —
no reliable date source in the corpus; deferred to D8 transcripts.

Idempotency: events have no natural UNIQUE key (a company can have the same
guidance reiterated across editions), so ``apply`` replaces all derived rows
(``source_ref LIKE 'derive:events:%'``) with the current scan via the shared
stable prefix-replace — unchanged rows keep their id and created_at, so a
no-op cycle leaves the table byte-identical. Hand-seeded rows (``manual:`` /
``migration:`` source_ref) are preserved.

Three-stage shape mirrors derive_themes.py / derive_co_mentions.py:
scan/derive -> apply.

Usage:
    python3 helpers/graph/derive_events.py            # dry-run summary
    python3 helpers/graph/derive_events.py --apply    # write event rows
    python3 helpers/graph/derive_events.py --verbose  # list every event
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# sys.path bootstrap so this works both as `python3 helpers/graph/...` (the
# Makefile form) and as a package import. Mirrors derive_themes.py:39-41.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.core.stable_write import stable_prefix_replace  # noqa: E402
# Reuse the proven date parser + regexes from extract_relations rather than
# duplicating FY/month/year handling. Imported for internal use (not re-exported).
from helpers.graph.extract_relations import _extract_year_from_context  # noqa: E402
from helpers.core.frontmatter import strip_frontmatter as _strip_frontmatter  # noqa: E402

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #
COMPANIES_DIR = _REPO_ROOT / "findata" / "Companies"

# Two source_ref prefixes: one per extraction arm. The LIKE 'derive:events:%'
# sweep in apply() clears BOTH on re-run (the idempotency contract).
PROMOTE_SOURCE_REF = "derive:events:edge-promotion"
GUIDANCE_SOURCE_REF = "derive:events:guidance-prose"
MGMT_SOURCE_REF = "derive:events:management-prose"
_DERIVED_PREFIX = "derive:events:"  # the LIKE sweep matches all three arms

# Edge type -> event_type mapping for the promote arm.
_EDGE_TO_EVENT_TYPE = {
    "acquired": "acquisition",
    "jv_with": "jv",
}

# --------------------------------------------------------------------------- #
# ARM 2 — prose extraction patterns                                           #
# --------------------------------------------------------------------------- #
# Fiscal-period tokens. "FY27", "FY2027", "Q1 FY26", "Q1FY26", "Q4CY25".
# These are the temporal anchor for guidance events — a guidance bullet
# without a fiscal/quarter token is just commentary, not a dated projection.
_FY_TOKEN_RE = re.compile(r"\b(?:Q[1-4]\s*)?FY\s?\d{2,4}\b", re.IGNORECASE)
_CY_QUARTER_RE = re.compile(r"\bQ[1-4]\s*CY\s?\d{2,4}\b", re.IGNORECASE)

# Metric signals for guidance. A bullet is a guidance event iff it has a
# fiscal token AND a metric signal AND a forward-looking signal (below). Match
# either a percent range/figure or a money/keyword signal. The "10-12%" /
# "Rs 4,000 cr" shapes are the canonical guidance forms (see the corpus
# evidence in the D7 plan).
_PCT_RE = re.compile(r"\b\d[\d,]*\s*[-–to ]+\s*\d+\s*%|\b\d+\s*%")
_MONEY_OR_KEYWORD_RE = re.compile(
    r"₹|rs\.?\s|inr|\brevenue\b|\bmargin[s]?\b|\border book\b|\bgrowth\b|"
    r"\btarget\b|\bcapex\b|\baum\b|\bmarket share\b|\bcapacity\b",
    re.IGNORECASE,
)
# Forward-looking signal — REQUIRED for guidance (the third leg of the
# precision guard). A bullet with a fiscal token + a metric but NO forward
# signal is a *historical result / current state* ("FY2025: ₹275 delivered",
# "capacity running at 85%"), not a projection. The roadmap's value is
# "guidance tracking (did they hit it next quarter?)" — mixing actuals in
# defeats that. Projection verbs + future-tense + "by FYxx" targets qualify.
_FORWARD_RE = re.compile(
    r"\b(target|targets|targeting|expect|expects|expected|guidance|guide|"
    r"guided|plan|plans|planning|aim|aims|project|projects|projected|eye|eyes|"
    r"reiterate|reiterated|outlook|aspire|aspiration|aspirational|"
    r"conservatively|capex|ramp|will\s+(?:reach|cross|hit|grow|deliver|"
    r"achieve|generate|be|do))\b",
    re.IGNORECASE,
)

# Role-change verbs (management_change). Must co-occur with an executive title.
# Deliberately EXCLUDES generic "will lead/will head" (matches "will lead to
# growth" boilerplate) — a real succession is phrased "takes over as CEO" /
# "appointed CEO" / "succeeds <predecessor>". Excludes "named" alone too (too
# broad: "named in the suit"); require "named ... <title>" via the title co-req.
# "incoming" (G2, 2026-08) captures the dominant CEO-succession form "Incoming
# CEO" / "incoming MD" — it has no verb, so it is listed as a verb-equivalent
# here; the title co-requirement below rejects non-title uses ("incoming
# revenue", "incoming shipment"). This is the cheaper alternative to D6 (person
# nodes) for lifting the management_change table.
_CHANGE_VERB_RE = re.compile(
    r"\b(appointed|takes over|took over|taking over|stepped down|stepping down|"
    r"resigned|rejoined|succeeds|to succeed|promoted to|elevated to|"
    r"assumes charge|set to (?:lead|head|take over)|incoming)\b",
    re.IGNORECASE,
)
# "appointed" used as a JOB-TITLE ADJECTIVE — "Appointed Actuary", "Appointed
# Auditor" — is a role attribution, not a management change. Reject windows
# where "appointed" is immediately followed by one of these titles.
_APPOINTED_TITLE_ATTR = re.compile(
    r"\bappointed\s+(actuary|auditor|receiver|liquidator|trustee)s?\b",
    re.IGNORECASE,
)
# Executive titles. Kept explicit (not free-text) to avoid matching "chairman
# of the audit committee said" boilerplate without a change verb.
_TITLE_RE = re.compile(
    r"\b(CEO|CFO|COO|CTO|MD|Managing Director|Chief Executive Officer|"
    r"Chief Financial Officer|Chief Operating Officer|Chief Technology Officer|"
    r"Chairman|Executive Chairman|Vice Chairman|Whole.time Director|Director)\b",
    re.IGNORECASE,
)
# Person name: 2-3 capitalised words immediately preceding a change verb or
# following "appointed". Used for the properties.person audit trail. Tolerant
# of initials and the Indian "X. Y. Surname" shape; bounded so it doesn't run.
_PERSON_RE = re.compile(
    r"([A-Z][a-zA-Z.]+(?:\s+[A-Z][a-zA-Z.]+){1,3})"
)

# P1 perf: compiled patterns for _iter_bullets + _capture_period_token (were
# inline re.split / re.search per call — 169K + several K calls respectively).
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_MONTH_YEAR_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\.?\s+(20\d{2})\b",
    re.IGNORECASE,
)


@dataclass
class Event:
    """A derived event row (mirrors the events table columns)."""
    entity: str
    event_type: str
    event_date: str | None = None
    period: str | None = None
    date_precision: str | None = None
    magnitude: str | None = None
    counterparty: str | None = None
    source_quote: str | None = None
    as_of_edition: str | None = None
    source_ref: str = PROMOTE_SOURCE_REF
    properties: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Date helpers (wrap _extract_year_from_context to derive period + precision)  #
# --------------------------------------------------------------------------- #
def _parse_event_date(text: str) -> tuple[str | None, str | None, str | None]:
    """Extract ``(event_date, period, date_precision)`` from a text window.

    Delegates year/date parsing to the proven ``_extract_year_from_context``
    helper, then derives a ``period`` label (the raw matched token, e.g.
    "FY27", "Q1FY26", "Mar 2026") and a ``date_precision`` bucket from the
    ISO date shape:
      - "YYYY-MM-DD" with non-01 day  -> day
      - "YYYY-MM-01" (month resolved)  -> month
      - "YYYY-01-01" (year only)       -> year
      - no date                       -> none
    """
    year, iso = _extract_year_from_context(text)
    if not iso:
        return None, _capture_period_token(text), "none"
    period = _capture_period_token(text) or (str(year) if year else None)
    if iso.endswith("-01-01"):
        precision = "year"
    elif iso[8:10] == "01":
        precision = "month"
    else:
        precision = "day"
    return iso, period, precision


def _capture_period_token(text: str) -> str | None:
    """Find the raw fiscal/quarter/month-year token in the window."""
    for pat in (_FY_TOKEN_RE, _CY_QUARTER_RE):
        m = pat.search(text)
        if m:
            return m.group(0).strip()
    # Month-year (e.g. "Mar 2026") — compiled at module level (_MONTH_YEAR_RE).
    m = _MONTH_YEAR_RE.search(text)
    if m:
        return m.group(0).strip()
    return None


# --------------------------------------------------------------------------- #
# ARM 1 — promote existing graph_edges into events                            #
# --------------------------------------------------------------------------- #
def promote_from_edges(conn) -> list[Event]:
    """Lift ``acquired``/``jv_with`` edges into event rows (near-zero new FP).

    Reuses the counterparty resolution + valid_from that extract_relations.py
    and backfill_valid_from.py already produced. ``valid_from`` -> event_date;
    ``properties.year`` refines date_precision to "year"; the JSON properties
    feed magnitude (stake/amount), counterparty (edge target), and quote.
    """
    rows = conn.execute(
        """
        SELECT source, target, edge_type, valid_from, properties
        FROM graph_edges
        WHERE edge_type IN ('acquired', 'jv_with')
        """
    ).fetchall()

    events: list[Event] = []
    for source, target, edge_type, valid_from, props_json in rows:
        try:
            props = json.loads(props_json) if props_json else {}
        except (ValueError, TypeError):
            props = {}
        event_type = _EDGE_TO_EVENT_TYPE.get(edge_type)
        if event_type is None:
            continue
        # Date precision: year-known -> "year"; month-precise valid_from -> "month".
        precision = None
        if valid_from:
            if props.get("year") and valid_from.endswith("-01-01"):
                precision = "year"
            elif valid_from[5:7] != "01" or valid_from[8:10] != "01":
                precision = "month"
            else:
                precision = "year"
        magnitude = None
        for key in ("stake", "amount", "aum", "value", "premium", "consideration"):
            if props.get(key):
                magnitude = str(props[key])
                break
        events.append(Event(
            entity=source,
            event_type=event_type,
            event_date=valid_from,
            period=(str(props["year"]) if props.get("year") else None),
            date_precision=precision,
            magnitude=magnitude,
            counterparty=target,
            source_quote=props.get("quote"),
            as_of_edition=props.get("edition") or props.get("ref"),
            source_ref=PROMOTE_SOURCE_REF,
            properties=props,
        ))
    return events


# --------------------------------------------------------------------------- #
# ARM 2 — prose extraction (guidance + management_change)                     #
# --------------------------------------------------------------------------- #
def _iter_bullets(body: str):
    """Yield individual markdown bullets / sentences from the note body.

    Guidance lives in bold-lead bullets ("**FY27 guidance reiterated (10-12%):**
    ..."); management changes can be bullets or sentences. We scan per-line for
    bullet content, then fall back to splitting prose sentences, so both shapes
    are covered. Trailing whitespace + blank lines are skipped.

    P1 perf fix (2026-08-10): previously iterated body.splitlines() TWICE
    (once for lines, once for sentences) yielding single-sentence bullets
    twice. Now iterates once: yields each non-blank line, then yields any
    sentence splits that aren't identical to the whole line. The seen set
    spans both phases so nothing is yielded twice.
    """
    seen: set[str] = set()
    lines = body.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        yield stripped
        seen.add(stripped)
    # Also yield sentence-split prose (management changes often run in
    # multi-clause sentences inside ## The Chatter that aren't bullets).
    for line in lines:
        for sentence in _SENTENCE_SPLIT_RE.split(line.strip()):
            sentence = sentence.strip()
            if len(sentence) < 25 or sentence in seen:
                continue
            seen.add(sentence)
            yield sentence


def _extract_guidance(company: str, body: str, path: str | None,
                     windows: list[str] | None = None) -> list[Event]:
    """Guidance events: fiscal token AND a metric AND a forward-looking signal.

    The three-leg precision guard (fiscal + metric + forward) is what keeps
    historical results / current-state bullets out: "FY2025: ₹275 (22.2%
    growth)" has fiscal + metric but no forward signal; "FY27 guidance
    reiterated (10-12%)" has all three. This is the D7 analogue of D4's
    bare-PLI guard.

    P1 perf: ``windows`` lets the caller pass a pre-iterated bullet/sentence
    list so both extractors share one ``_iter_bullets`` pass (avoids 2x
    iteration of 1051 files).
    """
    events: list[Event] = []
    for window in (windows if windows is not None else _iter_bullets(body)):
        has_fiscal = bool(_FY_TOKEN_RE.search(window) or _CY_QUARTER_RE.search(window))
        if not has_fiscal:
            continue
        pct = _PCT_RE.search(window)
        has_metric = bool(pct or _MONEY_OR_KEYWORD_RE.search(window))
        if not has_metric:
            continue
        if not _FORWARD_RE.search(window):
            continue
        event_date, period, precision = _parse_event_date(window)
        # magnitude: the percent range/figure (pct already found above).
        magnitude = pct.group(0).strip() if pct else None
        events.append(Event(
            entity=company,
            event_type="guidance",
            event_date=event_date,
            period=period,
            date_precision=precision,
            magnitude=magnitude,
            counterparty=None,
            source_quote=window,
            as_of_edition=path,
            source_ref=GUIDANCE_SOURCE_REF,
        ))
    # De-dup identical guidance (same period + magnitude + quote) per company.
    return _dedup(events)


def _extract_management(company: str, body: str, path: str | None,
                        windows: list[str] | None = None) -> list[Event]:
    """Management-change events: role-change verb AND an executive title."""
    events: list[Event] = []
    for window in (windows if windows is not None else _iter_bullets(body)):
        if not _CHANGE_VERB_RE.search(window):
            continue
        # Reject "Appointed Actuary / Auditor / ..." — a role-title adjective,
        # not a management change (a real attribution-line false positive).
        if _APPOINTED_TITLE_ATTR.search(window):
            continue
        title_m = _TITLE_RE.search(window)
        if not title_m:
            continue
        event_date, period, precision = _parse_event_date(window)
        person = None
        pm = _PERSON_RE.search(window)
        if pm:
            person = pm.group(1).strip()
        props: dict = {"role": title_m.group(0)}
        if person:
            props["person"] = person
        events.append(Event(
            entity=company,
            event_type="management_change",
            event_date=event_date,
            period=period,
            date_precision=precision,
            magnitude=title_m.group(0),
            counterparty=None,
            source_quote=window,
            as_of_edition=path,
            source_ref=MGMT_SOURCE_REF,
            properties=props,
        ))
    return _dedup(events)


def _dedup(events: list[Event]) -> list[Event]:
    """Drop near-identical events within one company.

    Key is (type, period, magnitude) — the *factual* identity of the event —
    so multiple paraphrases of the same guidance ("18,000 new customers, a 12%
    decline" restated 3 ways across Chatter bullets) collapse to one. Keeps the
    first (longest quote) occurrence. ``entity`` is fixed per call so it isn't
    in the key.
    """
    out: list[Event] = []
    seen: set[tuple] = set()
    # Prefer the occurrence with the longest source_quote (richest audit trail).
    for ev in sorted(events, key=lambda e: len(e.source_quote or ""), reverse=True):
        key = (ev.event_type, ev.period, ev.magnitude)
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    # Restore a stable order (by date then quote).
    out.sort(key=lambda e: (e.event_date or "", e.source_quote or ""))
    return out


def extract_from_prose(root: Path = COMPANIES_DIR,
                       path_to_name: dict[str, str] | None = None) -> list[Event]:
    """Scan company notes and derive guidance + management_change events.

    Args:
        root: Directory to scan for ``*.md`` (default findata/Companies).
        path_to_name: Map of posix-relative note path -> entity display name
            (the sync_tags / entities.file_path join contract). When provided,
            a note is only emitted if its path resolves to a known company —
            this skips stray .md files and resolves the display name (entity
            names use spaces, e.g. "ABB India", while file stems use
            underscores, e.g. "ABB_India"). When None, the file stem is used
            (test convenience).

    Returns a flat list of Event rows. Sector notes are SKIPPED (events are
    company-scoped, mirroring derive_themes / extract_relations).
    """
    events: list[Event] = []
    for note in sorted(root.rglob("*.md")):
        try:
            text = note.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            rel = note.resolve().relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            rel = note.stem
        company = None
        if path_to_name is not None:
            company = path_to_name.get(rel)
        else:
            company = note.stem
        if company is None:
            continue
        body = _strip_frontmatter(text)
        if not body.strip():
            continue
        # P1 perf: iterate bullets ONCE and share across both extractors
        # (previously each extractor called _iter_bullets independently, so
        # 1051 files were iterated 4x: 2 extractors × 2 splitlines passes).
        windows = list(_iter_bullets(body))
        events.extend(_extract_guidance(company, body, rel, windows=windows))
        events.extend(_extract_management(company, body, rel, windows=windows))
    return events


# --------------------------------------------------------------------------- #
# Stage 3 — persist events                                                    #
# --------------------------------------------------------------------------- #
_INSERT_SQL = """
INSERT INTO events
    (entity, event_type, event_date, period, date_precision, magnitude,
     counterparty, source_quote, as_of_edition, source_ref, properties)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_EVENT_CONTENT_COLS = ("entity", "event_type", "event_date", "period",
                       "date_precision", "magnitude", "counterparty",
                       "source_quote", "as_of_edition", "source_ref",
                       "properties")


def apply(events: list[Event], *, conn=None, dry_run: bool = True) -> int:
    """Persist derived events into the ``events`` table.

    Idempotency uses the prefix-scoped stable replace (shared with
    derive_insights): all rows under ``source_ref LIKE 'derive:events:%'``
    (the three derive arms) are multiset-matched against the current scan —
    unchanged rows keep their id AND created_at, stale rows are deleted by
    id, only genuinely new rows are inserted. Hand-seeded rows (``manual:``
    / ``migration:`` source_ref) are preserved. ``dry_run=True`` (default)
    counts what would land without writing — the derive-* convention.
    """
    own_conn = conn is None
    if own_conn:
        conn = connect()

    try:
        if dry_run:
            # Count what would land; no writes. Existing derived rows would be
            # replaced, so the net delta isn't simply len(events); report the
            # raw derived count (consistent with the dry-run summary).
            return len(events)
        new_rows = [
            (ev.entity, ev.event_type, ev.event_date, ev.period,
             ev.date_precision, ev.magnitude, ev.counterparty,
             ev.source_quote, ev.as_of_edition, ev.source_ref,
             json.dumps(ev.properties, ensure_ascii=False, sort_keys=True))
            for ev in events
        ]
        with conn:
            return stable_prefix_replace(
                conn, "events", _DERIVED_PREFIX, _EVENT_CONTENT_COLS,
                _INSERT_SQL, new_rows)
    finally:
        if own_conn:
            conn.close()


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Derive the events timeline table (acquisition/jv/guidance/"
                    "management_change) from graph_edges + company-note prose.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write event rows (default: dry-run summary only).",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print every event in addition to the summary.",
    )
    args = p.parse_args(argv)

    conn = connect()
    try:
        # Arm 1: promote from edges (no path map needed — edges already carry
        # resolved entity names).
        promoted = promote_from_edges(conn)

        # Arm 2: build file_path -> display-name map (the sync_tags join
        # contract) so notes resolve to the entity display name.
        path_to_name = {
            r[1]: r[0] for r in conn.execute(
                "SELECT name, file_path FROM entities "
                "WHERE entity_type = 'company' AND file_path IS NOT NULL"
            ).fetchall()
        }
        extracted = extract_from_prose(COMPANIES_DIR, path_to_name)
        all_events = promoted + extracted

        by_type: dict[str, int] = defaultdict(int)
        for ev in all_events:
            by_type[ev.event_type] += 1
        by_entity: dict[str, int] = defaultdict(int)
        for ev in all_events:
            by_entity[ev.entity] += 1
        dated = sum(1 for ev in all_events if ev.event_date)

        print(
            f"companies_scanned={len(path_to_name)} "
            f"derived_events={len(all_events)} "
            f"(dated={dated}, undated={len(all_events) - dated}) "
            f"({'apply' if args.apply else 'dry-run'})",
            file=sys.stderr,
        )
        print("  by_type:", file=sys.stderr)
        for et in ("acquisition", "jv", "guidance", "management_change"):
            print(f"    {by_type.get(et, 0):4d}  {et}", file=sys.stderr)
        print(
            f"  companies_with_events={len(by_entity)} "
            f"(avg {len(all_events) / max(len(by_entity), 1):.1f} events/co)",
            file=sys.stderr,
        )

        written = apply(all_events, conn=conn, dry_run=not args.apply)
        action = "inserted" if args.apply else "would insert"
        print(
            f"{written} events {action} "
            f"(promoted={len(promoted)}, extracted={len(extracted)}).",
            file=sys.stderr,
        )

        if args.verbose:
            for ev in sorted(all_events, key=lambda e: (e.entity, e.event_date or "")):
                print(
                    f"{ev.entity}\t{ev.event_type}\t{ev.event_date}\t"
                    f"{ev.period}\t{ev.magnitude}\t{ev.counterparty}\t"
                    f"{(ev.source_quote or '')[:80]}"
                )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(_cli())
