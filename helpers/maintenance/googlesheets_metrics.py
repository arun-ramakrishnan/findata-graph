#!/usr/bin/env python3
"""GOOGLEFINANCE-via-Google-Sheets metrics batch — S3 of the combined
market-data resolution proposal
(doc/improvements/proposals/market_data_resolution.md).

Mechanism proven live 2026-08-25 on the 'Search Test' scratch sheet:
service account (gitignored memory/goog_svc_account.json) -> gspread ->
ONE batch write of ``=GOOGLEFINANCE("<EXC>:<SYM>","<attr>")`` formulas
(raw=False — the default stores them as literal text) -> poll the
computed values back. Two API calls per batch: the batch is the unit
(bulk doctrine).

gspread is imported LAZILY: it is a venv-only dependency until S3 ships
it via ``uv add`` (tests always inject client_factory, so the test
suite never needs it).
"""

from __future__ import annotations

import time
from pathlib import Path

SA_KEY_PATH = Path(__file__).resolve().parents[2] / "memory" / "goog_svc_account.json"
SHEET_TITLE = "Search Test"  # scratch sheet, cleared and rewritten per batch
GF_ATTRIBUTES = ("price", "marketcap", "pe", "eps", "high52", "low52")


def to_sheets_ticker(gf_slug: str) -> str:
    """'544399:BOM' -> 'BOM:544399' (GOOGLEFINANCE wants exchange first)."""
    stem, sep, suffix = gf_slug.rpartition(":")
    return f"{suffix}:{stem}" if sep else gf_slug


def parse_cell(value: object) -> float | None:
    """Computed cell -> float. Numbers pass through; numeric strings are
    cleaned; '#N/A ...' error text and anything else -> None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def fetch_gf_metrics(
    requests: list[tuple[str, str]],
    *,
    sheet_title: str = SHEET_TITLE,
    client_factory=None,
    poll_seconds: float = 2.0,
    attempts: int = 4,
) -> dict[tuple[str, str], float | None]:
    """One Sheets batch: write the formula grid, read computed values.

    ``requests`` are (gf_slug, attribute) pairs (e.g. ('544399:BOM',
    'pe')); returns {(slug, attribute): value-or-None} — error cells
    (#N/A, e.g. NSE:SRIGEE) map to None, not failure.

    ``client_factory`` defaults to the real gspread service-account
    client; tests inject a fake with .open(title).sheet1 exposing
    clear/update/get_values.
    """
    if not requests:
        return {}
    if client_factory is None:
        import gspread  # lazy: venv-only dependency (see module docstring)

        def client_factory():  # noqa: E731  # rebind of the param, on purpose
            return gspread.service_account(filename=str(SA_KEY_PATH))

    ws = client_factory().open(sheet_title).sheet1
    rows = [
        [to_sheets_ticker(slug), attr, f'=GOOGLEFINANCE("{to_sheets_ticker(slug)}","{attr}")']
        for slug, attr in requests
    ]
    ws.clear()
    ws.update(
        values=[["ticker", "attribute", "value"]] + rows,
        range_name=f"A1:C{1 + len(rows)}",
        raw=False,
    )

    values: list[list] = []
    for _attempt in range(attempts):
        time.sleep(poll_seconds)
        values = ws.get_values(f"A1:C{1 + len(rows)}", value_render_option="UNFORMATTED_VALUE")[1:]
        if len(values) == len(rows) and all(len(r) >= 3 and r[2] != "" for r in values):
            break
    return {req: (parse_cell(v[2]) if len(v) >= 3 else None) for req, v in zip(requests, values)}
