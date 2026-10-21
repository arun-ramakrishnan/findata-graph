#!/usr/bin/python3
"""Speaker resolution — S2 of person_ontology_spine.

S1 (:mod:`helpers.graph.speaker_census`) measured the gap and wrote the
reviewable plan; this module acts on it. Two halves, deliberately
separable because only the second changes the entity population:

**S2a — the alias map (always applied).** One row per ``quotes.speaker_name``
string, naming the cluster's canonical spelling and — when the identity is
already a ``person`` entity — that entity. The map is the resolution the
corpus has never had, and it is what makes the gate in S1 meaningful:
a new spelling that clusters with a known speaker resolves through the
same row its variants use, while a genuinely new speaker stays visibly
unlinked.

**S2b — minting persons (explicit ``--mint``).** The speaker corpus and the
person registry are effectively disjoint (3 of 1,410 clusters match), so a
map alone would link three speakers. ``--mint`` creates a pathless
``person`` entity per cluster with at least ``--min-quotes`` quotes,
skipping company-shaped attributions and any name already taken by another
entity type (the ``entities.name`` PK makes a collision unrepresentable —
those are reported, never force-merged).

Defaults are the house rule: **dry run**. ``--apply`` writes the map,
``--mint`` additionally mints, and every run prints the same inventory it
would write. Re-running is idempotent: the map upserts on ``alias_name``
and keeps an existing ``person_name`` link, and minting uses
``INSERT OR IGNORE``.

Why a map and not a column on ``quotes``: resolution is many-to-one at the
SPEAKER level. A column would duplicate the same decision across 8,687
rows, require a schema bump on the hot writer table, and go stale for
every quote written before the next backfill. The view
``v_quote_speaker`` joins the map over the corpus instead, so a quote
written tomorrow resolves without any write.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import DEFAULT_DB_PATH, connect  # noqa: E402
from helpers.graph.speaker_census import speaker_census  # noqa: E402

SOURCE_REF = "speaker_resolution:S2"

SPEAKER_IDENTITY_DDL = """
CREATE TABLE IF NOT EXISTS speaker_identity (
    alias_name     TEXT PRIMARY KEY NOT NULL,
    canonical_name TEXT NOT NULL,
    person_name    TEXT REFERENCES entities(name)
                     ON DELETE SET NULL ON UPDATE CASCADE,
    source_ref     TEXT NOT NULL,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

SPEAKER_IDENTITY_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_speaker_identity_person ON speaker_identity(person_name)",
)

#: The read surface S3+ and any report consume: every quote row keeps its
#: alias, gains the cluster canonical and (when resolved) the person.
QUOTE_SPEAKER_VIEW = """
CREATE VIEW IF NOT EXISTS v_quote_speaker AS
SELECT q.id            AS quote_id,
       q.entity        AS quoted_entity,
       q.speaker_name  AS alias_name,
       q.speaker_title AS speaker_title,
       si.canonical_name,
       si.person_name
FROM quotes q
LEFT JOIN speaker_identity si ON si.alias_name = q.speaker_name
"""


def ensure_schema(con) -> None:
    """Create the map + view if absent (idempotent; no data written)."""
    con.execute(SPEAKER_IDENTITY_DDL)
    for idx in SPEAKER_IDENTITY_INDEXES:
        con.execute(idx)
    con.execute(QUOTE_SPEAKER_VIEW)


def _mint_candidates(cen: dict, con, *, min_quotes: int) -> tuple[list[dict], list[dict]]:
    """Split the mintable clusters from the ones policy refuses to mint.

    Refusals are returned as evidence, not silently dropped: a company name
    held by the ``entities`` PK is a real speaker we cannot represent
    without renaming an existing entity, and a company-shaped attribution
    is not a person at all.
    """
    taken = {r[0] for r in con.execute("SELECT name FROM entities").fetchall()}
    company_shaped = {c["canonical"] for c in cen["company_shaped"]}
    mintable: list[dict] = []
    refused: list[dict] = []
    for c in cen["clusters"]:
        if c["entity"]:
            continue  # already linked to a person entity
        if c["quotes"] < min_quotes:
            continue  # below threshold — stays a string by policy
        if c["canonical"] in company_shaped:
            refused.append({"name": c["canonical"], "reason": "company-shaped"})
            continue
        if c["canonical"] in taken:
            refused.append({"name": c["canonical"], "reason": "name taken by another entity"})
            continue
        mintable.append(
            {
                "name": c["canonical"],
                "variants": c["variants"],
                "quotes": c["quotes"],
                "line": c["line"],
            }
        )
    return mintable, refused


def _normalized(name: str) -> str:
    """``entities.normalized_name`` for a person, mirroring SHP's convention."""
    from helpers.core.parse_newsletter import normalize_name  # noqa: PLC0415

    return normalize_name(name).upper()


def resolve(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    apply: bool = False,
    mint: bool = False,
    min_quotes: int = 2,
) -> dict:
    """Build (and optionally write) the alias map; optionally mint persons.

    ``apply`` writes the map only. ``mint`` implies ``apply`` and also
    creates the person entities. Both default off.
    """
    apply = apply or mint
    con = connect(db_path)
    try:
        ensure_schema(con)
        cen = speaker_census(con)
        mintable, refused = _mint_candidates(cen, con, min_quotes=min_quotes)

        # alias -> cluster; person_name only where the identity already exists
        # or is about to be minted in this same run.
        mint_names = {c["name"] for c in mintable} if mint else set()
        rows = []
        for c in cen["clusters"]:
            person = c["entity"] or (c["canonical"] if c["canonical"] in mint_names else None)
            for alias in c["variants"]:
                rows.append((alias, c["canonical"], person))

        stats = {
            "aliases": len(rows),
            "clusters": len(cen["clusters"]),
            "linked_person": sum(1 for _, _, p in rows if p),
            "mintable": len(mintable),
            "mintable_quotes": sum(c["quotes"] for c in mintable),
            "refused": len(refused),
            "min_quotes": min_quotes,
        }
        print(
            f"aliases={stats['aliases']} clusters={stats['clusters']} "
            f"linked_person={stats['linked_person']} | mint(>={min_quotes}): "
            f"{stats['mintable']} persons / {stats['mintable_quotes']} quote rows "
            f"| refused={stats['refused']}"
        )
        for r in refused[:10]:
            print(f"  refused: {r['name']} — {r['reason']}")
        if not apply:
            print("[dry-run] nothing written — pass --apply (map) or --mint (map + persons)")
            return stats

        with con:
            # Mint BEFORE the map: speaker_identity.person_name is a real FK
            # to entities(name), so a freshly minted identity must exist by
            # the time its alias row names it.
            if mint:
                for c in mintable:
                    con.execute(
                        "INSERT OR IGNORE INTO entities "
                        "(name, entity_type, normalized_name, file_path) "
                        "VALUES (?, 'person', ?, NULL)",
                        (c["name"], _normalized(c["name"])),
                    )
            for alias, canonical, person in rows:
                con.execute(
                    "INSERT INTO speaker_identity "
                    "(alias_name, canonical_name, person_name, source_ref) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(alias_name) DO UPDATE SET "
                    "  canonical_name = excluded.canonical_name, "
                    "  person_name = COALESCE(excluded.person_name, speaker_identity.person_name), "
                    "  source_ref = excluded.source_ref",
                    (alias, canonical, person, SOURCE_REF),
                )
        written = con.execute("SELECT COUNT(*) FROM speaker_identity").fetchone()[0]
        persons = con.execute(
            "SELECT COUNT(*) FROM entities WHERE entity_type='person'"
        ).fetchone()[0]
        print(
            f"wrote {written} map row(s); person entities now {persons}"
            f"{' (minted ' + str(stats['mintable']) + ')' if mint else ''}"
        )
        return stats
    finally:
        con.close()


def report(db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """Read-only coverage: how much of the corpus the map resolves."""
    con = connect(db_path, read_only=True)
    try:
        try:
            aliases, linked = con.execute(
                "SELECT COUNT(*), COUNT(person_name) FROM speaker_identity"
            ).fetchone()
        except Exception:  # noqa: BLE001  # pre-S2 DB
            aliases, linked = 0, 0
        has_view = (
            con.execute(
                "SELECT 1 FROM sqlite_master WHERE name='v_quote_speaker' AND type='view'"
            ).fetchone()
            is not None
        )
        if has_view:
            quotes, resolved = con.execute(
                "SELECT COUNT(*) FILTER (WHERE alias_name IS NOT NULL), "
                "COUNT(person_name) FROM v_quote_speaker"
            ).fetchone()
        else:  # pre-S2 DB: nothing is resolved yet
            quotes, resolved = (
                con.execute(
                    "SELECT COUNT(*) FROM quotes WHERE speaker_name IS NOT NULL AND speaker_name <> ''"
                ).fetchone()[0],
                0,
            )
        return {
            "aliases": aliases,
            "linked_aliases": linked,
            "quotes_with_speaker": quotes,
            "quotes_resolved": resolved,
        }
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="research.db path")
    parser.add_argument("--apply", action="store_true", help="write the alias map")
    parser.add_argument(
        "--mint",
        action="store_true",
        help="also create person entities for clusters with >= --min-quotes",
    )
    parser.add_argument(
        "--min-quotes",
        type=int,
        default=2,
        help="mint threshold (default 2; singletons stay strings)",
    )
    parser.add_argument("--report", action="store_true", help="print resolution coverage")
    args = parser.parse_args(argv)
    if args.report:
        for k, v in report(args.db).items():
            print(f"{k}: {v}")
        return 0
    resolve(args.db, apply=args.apply, mint=args.mint, min_quotes=args.min_quotes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
