"""Advisory: vault-scaling tier tripwire (vault_scaling.md §7, Phase 0).

Watches the three scaling axes against the proposal's tier thresholds so
§1's "record the actual driver" is a measurement, not a discipline:

- e_all_und doubled rows — T1 ~1M (CSR build due), T2 ~10M
  (rebuild-at-scale due), T3 100M (validation legs mandatory).
- note count — Phase A corpus work fires at ~10k notes.
- embedding-matrix MB — Phase C similarity work fires at tens of MB.

corpus-advisory pattern: prints an [advisory] report and NEVER fails the
gate — missing DBs/artifacts print their absence. Read-only: the graph
DB is opened duckdb-read-only directly; research.db goes through
helpers.core.db.connect (B2 gate — no raw sqlite3.connect).

Density baseline 27.7 doubled rows/note (34,392 rows / 1243 notes,
2026-09-04). note_search count is the indexed proxy for note count
(~2-file lag vs the vault while the index catches up is fine for an
advisory).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]

# Tier thresholds — vault_scaling.md §2/§4/§6 (rows are e_all_und DOUBLED rows).
T1_ROWS = 1_000_000  # CSR substrate fires (full BFS > 100 ms)
T2_ROWS = 10_000_000  # rebuild-at-scale fires (materialize > 5 s)
T3_ROWS = 100_000_000  # validation legs mandatory pre-promotion
PHASE_A_NOTES = 10_000  # corpus frontmatter-only load + lazy Iterator
PHASE_C_MATRIX_MB = 50  # "tens of MB" (proposal §6) — advisory line
DENSITY_BASELINE = 27.7  # doubled rows/note at filing (2026-09-04)


def _graph_rows() -> int | None:
    db = REPO_ROOT / "memory" / "graph.duckdb"
    if not db.exists():
        return None
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute("SELECT COUNT(*) FROM e_all_und").fetchone()[0]
    except duckdb.Error:
        return None  # pre-substrate build / migrated shape — advisory only
    finally:
        con.close()


def _note_count() -> int | None:
    db = REPO_ROOT / "memory" / "research.db"
    if not db.exists():
        return None
    from helpers.core import db as db_mod

    conn = db_mod.connect(db, read_only=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM note_search").fetchone()[0]
    except Exception:  # noqa: BLE001 — advisory: shape drift must not fail
        return None
    finally:
        conn.close()


def _matrix_mb() -> float | None:
    f = REPO_ROOT / "memory" / "embed_matrix.f32"
    return f.stat().st_size / (1024 * 1024) if f.exists() else None


def _axis(name: str, value: float | None, thresholds: list[tuple[float, str]]) -> str:
    """One report line: current value vs its thresholds, nearest-first."""
    if value is None:
        return f"  {name:<12} (absent — artifact/DB missing; advisory skips)"
    armed = [(v, label) for v, label in thresholds if value >= v]
    if armed:
        hit = ", ".join(label for _, label in armed)
        return f"  {name:<12} {value:>14,.0f}  *** {hit} — tier threshold crossed"
    v, label = min(thresholds, key=lambda t: t[0])
    pct = value / v * 100
    return f"  {name:<12} {value:>14,.0f}  {pct:5.1f}% of {label}"


def test_tier_tripwire_advisory():
    """Advisory: scaling axes vs tier thresholds — report, never fail."""
    rows = _graph_rows()
    notes = _note_count()
    matrix_mb = _matrix_mb()
    print("\n[advisory] vault-scaling tier tripwire (vault_scaling.md §2 ladder):")
    print(
        _axis(
            "rows",
            rows,
            [
                (T1_ROWS, "T1 ~1M: CSR build due"),
                (T2_ROWS, "T2 ~10M: rebuild-at-scale due"),
                (T3_ROWS, "T3 100M: validation legs mandatory"),
            ],
        )
    )
    print(_axis("notes", notes, [(PHASE_A_NOTES, "Phase A ~10k: corpus work due")]))
    print(
        _axis("matrix MB", matrix_mb, [(PHASE_C_MATRIX_MB, "Phase C tens-of-MB: similarity due")])
    )
    if rows is not None and notes:
        density = rows / notes
        driver = (
            "density-driven (rows/note above baseline — edges growing faster than notes)"
            if density > DENSITY_BASELINE * 1.1
            else "note-volume-driven (density near/below baseline)"
        )
        print(
            f"  density      {density:>14,.1f} rows/note vs {DENSITY_BASELINE} baseline"
            f" → {driver} (single snapshot; record trend here when a trigger fires)"
        )
    print("  advisory, not gating — thresholds live in vault_scaling.md §2/§4/§6")
    assert True
