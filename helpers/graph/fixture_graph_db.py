#!/usr/bin/env python3
"""Fixed fixture inputs for the parity harness (ocr_remediation S4/F7).

The S2 c901 splits (#306) proved parity once, in a scratch run /tmp would
eat. Registering them as harness fixtures makes the proof re-runnable via
`make parity` — but the harness hands each side only a module object, so
every input the renders need has to be a fixed, in-repo constant. This
module is that constants file: a minimal graph-schema SQLite builder and
the note/content fixtures for the corpus-lane and extraction targets.

Determinism contract: everything here is order-fixed (sorted rows, literal
texts). Renders that consume these fixtures must sort any set-shaped output
before joining — PYTHONHASHSEED varies between the worker subprocesses.

`print_stats` is deliberately NOT fixture-backed: it opens the live
production DB through `helpers.core.db.connect()` (stats.py lines 651/666)
and the census/hygiene helpers — making it fixture-renderable needs a db
path threaded through that plumbing, which belongs to the deferred c901
D1 per-function-harness work, not to fixture registration.
"""

from __future__ import annotations

from pathlib import Path

from helpers.core.db import connect

# --- graph-schema SQLite (l1_betweenness.compute, print_stats-adjacent) ---- #

# (source, target, edge_type) — one self-loop exercises the s != t guard.
# NOTE: the "quote_noise" row does NOT exercise l1_betweenness's noise
# filter — that module carries its own INDEX_NOISE frozenset
# ({"listed_on_index"}); this row is an ordinary included edge here
# (ocr review, c83a11d9 [16]).
GRAPH_EDGES: list[tuple[str, str, str]] = [
    ("Tata Steel", "Tata Sons", "BelongsTo"),
    ("Tata Motors", "Tata Sons", "BelongsTo"),
    ("Titan", "Tata Sons", "BelongsTo"),
    ("Infosys", "Nifty 50", "BelongsTo"),
    ("Wipro", "Nifty 50", "BelongsTo"),
    ("HDFC Bank", "Nifty 50", "BelongsTo"),
    ("Tata Steel", "Tata Motors", "competes_with"),
    ("Infosys", "Wipro", "competes_with"),
    ("Tata Steel", "Tata Steel", "self_loop"),
    ("Tata Steel", "Tata Sons", "quote_noise"),
]

ENTITIES: list[tuple[str, str, str | None]] = [
    # (name, entity_type, sector_classification)
    ("Tata Steel", "company", "Metals"),
    ("Tata Motors", "company", "Auto"),
    ("Titan", "company", "Consumer"),
    ("Infosys", "company", "IT"),
    ("Wipro", "company", "IT"),
    ("HDFC Bank", "company", "Banks"),
    ("Tata Sons", "promoter", None),
    ("Nifty 50", "index", None),
]

ENTITY_TAGS: list[tuple[str, str]] = [
    ("Tata Steel", "market_cap/large"),
    ("Tata Motors", "market_cap/large"),
    ("Titan", "market_cap/mid"),
    ("Infosys", "market_cap/large"),
    ("Wipro", "market_cap/large"),
    ("HDFC Bank", "market_cap/large"),
]


def build_graph_fixture_db(path: Path) -> Path:
    """Write the minimal graph-schema SQLite fixture (idempotent overwrite)."""
    path = Path(path)
    for suffix in ("", "-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    con = connect(str(path))
    try:
        con.executescript(
            """
            CREATE TABLE entities (
                name TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                normalized_name TEXT,
                sector_classification TEXT,
                file_path TEXT
            );
            CREATE TABLE entity_tags (
                entity_name TEXT NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (entity_name, tag)
            );
            CREATE TABLE graph_edges (
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                properties_json TEXT DEFAULT '{}',
                source_ref TEXT DEFAULT '',
                PRIMARY KEY (source, target, edge_type, source_ref)
            );
            """
        )
        con.executemany(
            "INSERT INTO entities (name, entity_type, normalized_name, sector_classification) "
            "VALUES (?, ?, ?, ?)",
            [(n, t, n.lower().replace(" ", "_"), s) for n, t, s in ENTITIES],
        )
        con.executemany("INSERT INTO entity_tags (entity_name, tag) VALUES (?, ?)", ENTITY_TAGS)
        con.executemany(
            "INSERT INTO graph_edges (source, target, edge_type) VALUES (?, ?, ?)", GRAPH_EDGES
        )
        con.commit()
    finally:
        con.close()
    return path


# --- note-tree fixtures (extract_theme_membership, extract_citations) ------ #

# Companies-subtree notes: frontmatter chatter block scoped by
# _extract_theme_scan_text, theme aliases _match_theme_aliases can hit.
# Alias phrasing is verified against CANONICAL_THEMES (a stale alias here
# silently vacuums the fixture to 0 rows — the renders emit whatever the
# matcher does, but a no-hit fixture proves nothing).
THEME_NOTES: dict[str, str] = {
    "Companies/Tata_Steel.md": (
        "---\ntitle: Tata Steel\n---\n"
        "<!-- BEGIN auto chatter block (derive_insights) -->\n"
        "Data center buildout and battery energy storage orders; "
        "renewable energy capacity announced.\n"
        "<!-- END auto chatter block -->\n"
    ),
    "Companies/Infosys.md": (
        "---\ntitle: Infosys\n---\n"
        "<!-- BEGIN auto chatter block (derive_insights) -->\n"
        "Electronic manufacturing services and PLI scheme momentum.\n"
        "<!-- END auto chatter block -->\n"
    ),
    "Companies/Stray_Co.md": (
        "---\ntitle: Stray Co\n---\n"
        "<!-- BEGIN auto chatter block (derive_insights) -->\n"
        "No theme aliases appear in this note.\n"
        "<!-- END auto chatter block -->\n"
    ),
}

# cited_in vault: derived trees with sources[] frontmatter.
# id must be a known edition stem; /Reports/ resources are skipped.
CITED_VAULT_NOTES: dict[str, str] = {
    "Companies/Tata_Steel.md": (
        "---\n"
        "sources:\n"
        "  - id: 2026-09-21-the-chatter\n"
        "    resource: https://example.com/a\n"
        "  - id: 2026-09-14-the-chatter\n"
        "    resource: /Reports/secret.pdf\n"
        "  - id: not-a-real-edition\n"
        "    resource: https://example.com/b\n"
        "---\nbody\n"
    ),
    "Sectors/Metals.md": (
        "---\nsources:\n  - id: 2026-09-21-the-chatter\n    resource: ''\n---\nbody\n"
    ),
    "Companies/Unmapped_Co.md": "---\nsources:\n  - id: 2026-09-21-the-chatter\n---\nbody\n",
}

CITED_EDITION_STEMS: set[str] = {"2026-09-21-the-chatter", "2026-09-14-the-chatter"}

CITED_PATH_TO_NAME: dict[str, str] = {
    "findata/Companies/Tata_Steel.md": "Tata Steel",
    "findata/Sectors/Metals.md": "Metals",
    # Unmapped_Co deliberately absent — exercises the entity-None skip.
}


def build_note_tree(root: Path, files: dict[str, str]) -> Path:
    """Materialize {relpath: text} under root and return root."""
    root = Path(root)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


# --- newsletter content fixtures (extract_relations) ----------------------- #

# One block per extraction lane the S2 split touched. Trigger phrasings are
# VERIFIED against PATTERNS (ocr review, c83a11d9 [18][19]: the original
# blocks matched nothing — "customers include" isn't a trigger, "partnered
# with" isn't a jv synonym): customer_of wants the paren form, jv_with the
# "partnership/tie-up/alliance with" forms. Zero-resolved-chunks case: no
# chunk name is known, so every chunk misses and the sidecar-first routing
# decides the output. Resolver universe = RELATION_KNOWN_NAMES.
RELATION_CONTENTS: tuple[str, ...] = (
    "## Tata Steel | Large Cap | Metals\n\nKey customers (Tata Motors, Titan, Wipro).\n",
    "## Infosys | Large Cap | IT\n\nCompetitors such as Accenture, Cognizant, and Syngenta.\n",
    "## HDFC Bank | Large Cap | Banks\n\n"
    "The bank announced a strategic alliance with Infosys for core "
    "banking modernisation.\n",
    "## Titan | Large Cap | Consumer\n\nTitan competes with Wipro in wearables.\n",
)

RELATION_KNOWN_NAMES: tuple[str, ...] = (
    "Tata Steel",
    "Tata Motors",
    "Titan",
    "Infosys",
    "Wipro",
    "HDFC Bank",
)

# --- resolver-ladder cases (derive_insights._resolve_ladder) --------------- #

# (section-canonical input, resolver_map) pairs exercising the S2 tier
# ladder: exact hit, escaped-entity miss->strip, qualifier miss, fuzzy
# suggestion, and a total miss. The render dumps (entity, tier,
# suggestions) verbatim — whatever the ladder does IS the byte contract.
LADDER_CASES: list[tuple[str, dict[str, str]]] = [
    ("Tata Steel", {"Tata Steel": "Tata Steel", "Tata_Steel": "Tata Steel"}),
    ("Tata_Steel", {"Tata Steel": "Tata Steel", "Tata_Steel": "Tata Steel"}),
    ("Infosys Ltd", {"Infosys": "Infosys"}),
    ("Zentrum Electronics", {"Tata Electronics": "Tata Electronics"}),
    ("NoSuchCompany", {"Tata Steel": "Tata Steel"}),
]
