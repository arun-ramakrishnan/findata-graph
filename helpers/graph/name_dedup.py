#!/usr/bin/python3
"""Entity-name dedup: census, merge plan, standing gate (entity_name_dedup).

``graph_edges`` carries names inline (no node table), and three writers
(vigil bulk reports, newsletter extraction, derived lanes) have drifted
into spelling variants of the same real-world entity: 1,575 collision
clusters over 22,067 distinct names, 11.2% of edge rows touched
(2026-10-15 census). Variant names defeat every exact-name surface —
suggest_relations dedup, A² pairing, co-mention pairing, fuzzy match.

Slices (proposal ``entity_name_dedup.md``):

- **S1** ``--census`` / ``--plan`` — normalisation-key clustering and a
  human-reviewable merge plan. DRY-RUN ONLY: nothing here writes to the
  graph. The plan artifact is the gate: S2 runs only on operator
  sign-off.
- **S2** ``--apply`` — re-point ``graph_edges`` per the SIGNED plan
  (requires ``--yes``), collapse exact-duplicate rows, report deltas.
  Cache refresh (duckdb ``e_*``) rides the standard graph rebuild after.
- **S4** ``--check`` / ``--baseline`` — collision-cluster count vs the
  recorded baseline; a rise means a writer is regressing.

Conservative by construction: the normalisation key folds case,
punctuation and footnote markers only — ``HDFC Bank`` vs ``HDFC
Securities`` never cluster; token-transposition pairs are REPORTED
separately and never auto-merged.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core import jsonio  # noqa: E402  (orjson parse cutover; dumps stay stdlib per db_scaling_tuning §6.3 criteria)
from helpers.core.db import connect  # noqa: E402
from helpers.core.db_engine import is_duckdb_conn, table_exists  # noqa: E402  (S3: DuckDB engine-agnostic probes)
from helpers.core.entity_identity import (  # noqa: E402
    FOOTNOTE_RE,
    is_person_name,
    name_key as _name_key,
    speaker_names as _speaker_names,
    token_sort_key as _token_sort_key,
)

PLAN_PATH = _REPO_ROOT / "findata" / "Misc" / "_name_merge_plan.txt"
BASELINE_PATH = _REPO_ROOT / "findata" / "Misc" / "_name_dedup_baseline.txt"
LEDGER_PATH = _REPO_ROOT / "findata" / "Misc" / "_name_fold_ledger.jsonl"
CANON_PLAN_PATH = _REPO_ROOT / "findata" / "Misc" / "_name_canonical_plan.txt"
NSE_SOURCE = _REPO_ROOT / "memory" / "data" / "sources.duckdb"

log = logging.getLogger(__name__)

# canonical-name classifier token classes (canonical_name_resolution S1):
# tokens a variant may add over the winner and still be the same company
# (legal-tail / display-form noise) vs tokens that signal a DIFFERENT
# legal entity (foreign-arm forms) or need a human (real-name
# constituents like Corporation that are part of some companies' own
# legal names -- Hindustan Petroleum Corporation -- and of other
# groups' parents -- Bosch Corporation).
_CANON_SAFE_TAILS = frozenset({"the", "company", "co", "limited", "ltd"})
_CANON_REVIEW_TAILS = frozenset({"corp", "corporation"})


def normalise(name: str) -> str:
    """The census key — delegates to the one home
    (helpers.core.entity_identity.name_key): the dedup census, the
    write paths and the resolver must never fold differently."""
    return _name_key(name)


def token_sort_key(name: str) -> str:
    """Order-insensitive secondary key (report-only clustering — never
    a merge key): 'IndianOil Adani Gas' ≡ 'Adani Gas IndianOil'."""
    return _token_sort_key(name)


QUOTES_PATH = _REPO_ROOT / "findata" / "Super_Sectors" / "Quotes.md"


def is_footnote_variant(name: str) -> bool:
    """True when stripping the marker changes the name (a `**` class
    variant). Such names can never win canonical selection."""
    return bool(FOOTNOTE_RE.search(name.rstrip()))


def _load_graph(con) -> tuple[list[str], dict[str, int], set[str]]:
    """(distinct names, degree per name, listed-entity set)."""
    names = sorted(
        {
            r[0]
            for r in con.execute(
                "SELECT source FROM graph_edges UNION SELECT target FROM graph_edges"
            ).fetchall()
            if r[0]
        }
    )
    degree: dict[str, int] = dict.fromkeys(names, 0)
    for (n,) in con.execute(
        "SELECT source FROM graph_edges UNION ALL SELECT target FROM graph_edges"
    ).fetchall():
        if n in degree:
            degree[n] += 1
    listed = {
        r[0]
        for r in con.execute(
            "SELECT DISTINCT source FROM graph_edges WHERE edge_type = 'listed_on_index'"
        ).fetchall()
    }
    return names, degree, listed


def cluster_names(names: list[str]) -> dict[str, list[str]]:
    """Group names by the normalisation key; only multi-member groups
    are collisions."""
    groups: dict[str, list[str]] = defaultdict(list)
    for n in names:
        groups[normalise(n)].append(n)
    return {k: sorted(v) for k, v in groups.items() if len(v) > 1}


def canonical_of(cluster: list[str], degree: dict[str, int], listed: set[str]) -> str:
    """Canonical choice, first rule wins (proposal §S1):

    (a) an exact listed-entity name; (b) highest total edge degree
    (ties → lexicographic, stable); footnote-marker variants can never
    win. The outcome is deterministic for a given graph state.
    """
    eligible = [n for n in cluster if not is_footnote_variant(n)]
    if not eligible:  # pathological all-footnote cluster: base-form wins
        eligible = sorted(cluster, key=len)
    for n in eligible:
        if n in listed:
            return n
    return max(eligible, key=lambda n: (degree.get(n, 0), n))


def census(con) -> dict:
    """Cluster census + per-cluster canonical + variant stats. Person
    clusters are EXCLUDED from the mergeable census (the #293
    person-resolver arc owns them) and reported separately."""
    names, degree, listed = _load_graph(con)
    clusters = cluster_names(names)
    speakers = _speaker_names()
    plan = []
    persons = []
    edge_rows_touched = 0
    for key in sorted(clusters):
        members = clusters[key]
        # A listed entity is never a person: the exemption un-blocks
        # company clusters whose spelling merely LOOKS person-shaped
        # (A. O. Smith, Dr. Reddy's subsidiaries).
        if any(m in listed for m in members):
            persons_check = False
        else:
            persons_check = any(is_person_name(m) or normalise(m) in speakers for m in members)
        if persons_check:
            persons.append({"key": key, "variants": members})
            continue
        cluster_edge_rows = sum(degree.get(m, 0) for m in members)
        plan.append(
            {
                "key": key,
                "canonical": canonical_of(members, degree, listed),
                "variants": members,
                "listed_hit": any(m in listed for m in members),
                "edge_rows": cluster_edge_rows,
            }
        )
        edge_rows_touched += cluster_edge_rows
    transpositions = _transposition_clusters(names, clusters)
    return {
        "n_names": len(names),
        "n_clusters": len(plan),
        "clusters": plan,
        "edge_rows_touched": edge_rows_touched,
        "footnote_names": sum(1 for n in names if is_footnote_variant(n)),
        "transpositions": transpositions,
        "person_clusters": persons,
    }


def _transposition_clusters(names: list[str], already: dict[str, list[str]]) -> list[dict]:
    """Token-sort collisions among names NOT already in a normalise
    multi-cluster (report-only — never merged without operator ruling).
    Membership in a reported cluster disqualifies a name here: the
    section stays strictly additive."""
    multi_members = {n for v in already.values() for n in v}
    groups: dict[str, list[str]] = defaultdict(list)
    for n in names:
        if n in multi_members:
            continue
        groups[token_sort_key(n)].append(n)
    out = []
    for k, v in groups.items():
        if len(v) > 1:
            out.append({"key": k, "variants": sorted(v)})
    return sorted(out, key=lambda d: d["key"])


def write_plan(cen: dict, path: Path = PLAN_PATH) -> Path:
    """Human-reviewable plan artifact (the S2 gate) + the machine form.
    Header carries the census + rule; one block per cluster;
    transpositions appended as a review-only section. The JSONL twin
    (``_name_merge_plan.jsonl``) carries VERBATIM names — some entity
    names contain raw newlines, which a line-format artifact cannot
    (and the S2 apply must not guess about)."""
    jsonl_path = path.with_suffix(".jsonl")
    lines = [
        "# Entity-name merge plan (entity_name_dedup S1 — DRY-RUN artifact)",
        f"# clusters: {cen['n_clusters']} | names: {cen['n_names']} | "
        f"footnote names: {cen['footnote_names']} | "
        f"edge rows touched: {cen['edge_rows_touched']}",
        "# canonical rule: listed-entity > degree > lex; footnote `**` variants never win",
        "# S2 applies ONLY on operator sign-off of this file (name_dedup.py --apply --yes)",
        f"# machine form: {jsonl_path.name} (verbatim names; regenerate together)",
        "",
    ]
    jsonl_rows = []
    for c in cen["clusters"]:
        tag = " [listed]" if c["listed_hit"] else ""
        display = lambda s: s.replace("\n", " ")  # noqa: E731  # txt readability only
        lines.append(
            f"== {display(c['canonical'])}{tag}  (key: {c['key']}; edge rows {c['edge_rows']})"
        )
        for v in c["variants"]:
            mark = "" if v == c["canonical"] else "  -> fold"
            lines.append(f"   {display(v)}{mark}")
        lines.append("")
        jsonl_rows.append(
            {
                "canonical": c["canonical"],
                "variants": [v for v in c["variants"] if v != c["canonical"]],
                "key": c["key"],
                "listed_hit": c["listed_hit"],
            }
        )
    if cen["transpositions"]:
        lines.append("# -- token-transposition pairs (REPORT-ONLY, never auto-merged) --")
        for t in cen["transpositions"]:
            lines.append(f"   {' | '.join(t['variants'])}")
        lines.append("")
    if cen.get("person_clusters"):
        lines.append(
            "# -- person-name clusters: EXCLUDED from this merge (route to the "
            "#293 person-resolver arc) --"
        )
        for pc in cen["person_clusters"]:
            lines.append(f"   {' | '.join(pc['variants'])}")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    jsonl_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in jsonl_rows),
        encoding="utf-8",
    )
    return path


def _plan_rows(jsonl_path: Path) -> list[dict]:
    if not jsonl_path.exists():
        raise SystemExit(f"plan machine form missing: {jsonl_path} — run --plan first")
    rows = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(jsonio.loads(line))
    return rows


def ledger_path_for(plan_path: Path) -> Path:
    """The append-only fold ledger a given plan's apply extends.

    ``_name_merge_plan.*``   → ``_name_fold_ledger.jsonl``
    ``_person_merge_plan.*`` → ``_person_fold_ledger.jsonl``

    Derived from the plan stem so the company arc and the person arc keep
    separate ledgers (a merged spelling never routes across arcs)."""
    name = plan_path.name
    for suffix in ("_merge_plan.jsonl", "_merge_plan.txt"):
        if name.endswith(suffix):
            return plan_path.with_name(name[: -len(suffix)] + "_fold_ledger.jsonl")
    return plan_path.with_name(plan_path.stem + "_fold_ledger.jsonl")


def _db_path(con) -> Path | None:
    """The on-disk file behind ``con``, or None for an in-memory database."""
    if is_duckdb_conn(con):
        row = con.execute(
            "SELECT file_path FROM duckdb_databases() WHERE file_path IS NOT NULL LIMIT 1"
        ).fetchone()
        return Path(row[0]) if row else None
    row = con.execute("PRAGMA database_list").fetchone()
    return Path(row[2]) if row and row[2] else None


def _ledger_write_allowed(con, ledger_path: Path) -> tuple[bool, str]:
    """Refuse to append to a REPO-TRACKED ledger from a non-live database.

    The ledger is the durable half of the fold contract (``fold_registry``
    routes old spellings to the canonical *forever*), and it lives in the
    tracked tree at ``findata/Misc/``. A rehearsal — ``apply_plan`` against a
    byte-copy for exactly the reason the FK repair exists — otherwise writes
    rows asserting folds that never happened, and because the append is
    idempotent the REAL run then appends nothing and the audit trail lies.

    So: a ledger inside the repository may only be extended by the live
    store it describes. Rehearsal ledgers belong next to their copy."""
    db = _db_path(con)
    live = _REPO_ROOT / "memory" / "research.db"
    if db is None:
        return False, "in-memory database (rehearsal) — ledger not written"
    try:
        inside_repo = ledger_path.resolve().is_relative_to(_REPO_ROOT.resolve())
    except OSError:
        inside_repo = False
    if not inside_repo:
        return True, ""
    try:
        same = db.resolve() == live.resolve()
    except OSError:
        same = False
    if not same:
        return False, f"ledger is repo-tracked but db is {db} (not {live}) — not written"
    return True, ""


def _append_ledger(folded: dict[str, str], ledger_path: Path, con=None) -> int:
    """EXTEND the append-only fold ledger — never replace it.

    The plan artifact is REGENERATED by ``--plan`` after every merge
    (clusters→0), which would silently drop the fold map the write paths
    consult — ``entity_identity.fold_registry`` promises a signed fold
    keeps routing old spellings to the canonical *forever*. The ledger is
    the durable half: one JSON row per folded spelling, VERBATIM (names
    may contain raw newlines, which only the machine form survives),
    appended once and never rewritten. Idempotent: an already-recorded
    (variant, canonical) pair is not re-appended, so re-running ``--apply``
    on an unchanged plan is a no-op here. Returns rows appended.

    ``folded`` must be the variants that ACTUALLY folded, not the plan's
    intent: a row for an entity that survived (a unique collision, a person
    exclusion) would claim a merge that did not happen.

    ``con`` enables the rehearsal guard — see ``_ledger_write_allowed``.
    Without it the historical behaviour is kept (the toy tests append to a
    tmp ledger with no live store)."""
    if con is not None:
        allowed, why = _ledger_write_allowed(con, ledger_path)
        if not allowed:
            log.warning("fold ledger NOT extended: %s", why)
            return 0
    have: set[tuple[str, str]] = set()
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = jsonio.loads(line)
                have.add((row["variant"], row["canonical"]))
    fresh = sorted((v, c) for v, c in folded.items() if (v, c) not in have)
    if not fresh:
        return 0
    stamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    with ledger_path.open("a", encoding="utf-8") as fh:
        for variant, canonical in fresh:
            fh.write(
                json.dumps(
                    {
                        "variant": variant,
                        "canonical": canonical,
                        "folded_at": stamp,
                        "kind": "canonical",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return len(fresh)


#: Every ``entities(name)`` foreign key the fold must RE-POINT before the
#: variant row is deleted. ``(table, column, unique_key)`` where
#: ``unique_key`` is the UNIQUE constraint the re-point can violate (the
#: canonical already holds the same tag/metric/identifier/quote) — the
#: variant's colliding duplicate then drops, because the fact survives
#: through the canonical row. ``None`` = no unique constraint.
#:
#: Why this table exists: the fold loop re-points ``graph_edges`` and
#: ``hyper_incidences`` (the relation payload), and every OTHER foreign key
#: onto ``entities(name)`` is ``ON DELETE CASCADE`` or ``SET NULL``. Deleting
#: the variant row without re-pointing these silently destroys authored
#: rows. Measured while planning the 2026-11-16 RPT-spelling fold: the
#: 14 duplicate-name groups would have taken 16 ``events``/
#: ``event_participants`` rows and 7 ``speaker_identity`` links with them.
_REFOLD_COLUMNS: tuple[tuple[str, str, str | None], ...] = (
    ("entity_tags", "entity_name", "entity_name, tag"),
    ("hyper_incidences", "entity_name", "edge_id, entity_name"),
    ("entity_identifiers", "entity_name", "entity_name, identifier_type, identifier_value"),
    ("company_metrics", "entity", None),
    ("quotes", "entity", "entity, quote_text, as_of_edition"),
    ("events", "entity", None),
    ("events", "counterparty_entity", None),
    ("event_participants", "participant", "event_id, participant, role"),
    ("co_mention_editions", "entity_name", "edition_title, entity_name"),
    ("speaker_identity", "person_name", None),
    ("speaker_identity", "alias_name", "alias_name"),
    ("speaker_identity", "canonical_name", None),
    ("ppr_results", "entity", "seed, entity"),
    ("graph_analytics", "entity_name", "metric, entity_name"),
    ("graph_analytics_contract", "entity_name", "metric, entity_name"),
    # Derived-cohort tables with NO foreign key onto entities: they hold the
    # name as a bare string, so the entity delete leaves them pointing at a
    # row that no longer exists. Re-pointed here for the same reason. The
    # UNIQUE keys are the tables' PRIMARY KEYs, so a re-point that lands the
    # variant on a canonical row that already exists collapses instead of
    # raising (collide-before-move).
    ("hidden_connections", "a", "a, b"),
    ("hidden_connections", "b", "a, b"),
    ("ultimate_parent", "company", "company"),
    ("ultimate_parent", "ultimate_parent", None),
    ("competitive_overlap", "a", "a, b, c"),
    ("competitive_overlap", "b", "a, b, c"),
    ("competitive_overlap", "c", "a, b, c"),
    ("ppr_results", "seed", "seed, entity"),
    ("supply_chokepoints", "supplier", "supplier"),
)


def _table_names(con) -> set[str]:
    if is_duckdb_conn(con):
        return {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'"
            )
        }
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _constraint_error_types() -> tuple[type[Exception], ...]:
    """Integrity/constraint error classes across engines (SQLite + DuckDB).

    DuckDB parse-rejects CASCADE/SET NULL (see swap_rewrite_flows), so it
    raises ``duckdb.ConstraintException`` where SQLite raises
    ``sqlite3.IntegrityError``; fold paths must treat both as "retain".
    """
    import duckdb  # noqa: PLC0415  (canonical lane only; keep module import light)

    return (sqlite3.IntegrityError, duckdb.ConstraintException)


#: Column names that CAN carry an entity name in a derived table. SQLite has no
#: type-level signal (these are plain ``TEXT``), so the registry is a code-side
#: convention — ``derived_name_columns`` intersects it with "has no FK to
#: entities(name)". ``hyper_edges.label`` is deliberately absent here and
#: handled by family below: the column is polymorphic (see
#: :data:`_HYPEREDGE_ENTITY_LABEL_FAMILIES`).
_NAME_SHAPED_COLUMNS: frozenset[str] = frozenset(
    {"a", "b", "c", "company", "ultimate_parent", "supplier", "seed"}
)

#: Name-shaped columns that are NOT entity names. ``concept_schemes.label`` is
#: a SKOS scheme title ("sector", "entity type"), not a company.
_NON_ENTITY_NAME_COLUMNS: frozenset[tuple[str, str]] = frozenset({("concept_schemes", "label")})

#: ``hyper_edges.label`` families whose label IS an entity name (measured
#: 2026-11-17: every label in these families resolves to ``entities.name``).
#: The remaining families carry free text by design — ``event`` holds
#: ``acquisition:12`` ids, ``group`` holds promoter-group nicknames ("Adani"),
#: ``merger`` holds prose ("merger: X + Y"), ``edition`` holds newsletter
#: titles, ``industry``/``sub_sector`` hold taxonomy strings. Treating the
#: column as uniformly entity-shaped would fire on 2,089 legitimate rows.
_HYPEREDGE_ENTITY_LABEL_FAMILIES: frozenset[str] = frozenset(
    {
        "country",
        "demerger",
        "index",
        "org_membership",
        "sector",
        "sub_sector",
        "subsidiary",
        "supply",
        "theme",
    }
)

#: The declared answer to :func:`derived_name_columns` — the two-way guard.
#: Derived tables store entity names as bare ``TEXT`` with no FK, so a rename
#: leaves them silently mixed between old and new spellings. This tuple is what
#: ``tests/test_name_dedup.py::TestDerivedNameCoverage`` asserts against the
#: machine enumeration, so a NEW unguarded column fails the test instead of
#: escaping the way ``hidden_connections`` did on 2026-11-16 (9 pairs lost,
#: invisible to the FK-based integrity check; see
#: ``doc/improvements/archive/database/derived_name_columns_guard.md``).
_DERIVED_NAME_COLUMNS: tuple[tuple[str, str], ...] = (
    ("competitive_overlap", "a"),
    ("competitive_overlap", "b"),
    ("competitive_overlap", "c"),
    ("hidden_connections", "a"),
    ("hidden_connections", "b"),
    ("hyper_edges", "label"),
    ("ppr_results", "seed"),
    ("supply_chokepoints", "supplier"),
    ("ultimate_parent", "company"),
    ("ultimate_parent", "ultimate_parent"),
)


def derived_name_columns(con) -> set[tuple[str, str]]:
    """Every ``(table, column)`` holding an entity name with NO FK to entities.

    The FK-less counterpart of :func:`entity_fk_children`. A name column with
    an FK is protected by CASCADE/SET NULL (and covered by
    :data:`_REFOLD_COLUMNS`); a name column WITHOUT one is invisible to
    ``PRAGMA foreign_key_list`` and therefore to the integrity check, so it
    must be enumerated structurally instead of remembered.

    ``hyper_edges.label`` is returned when any of
    :data:`_HYPEREDGE_ENTITY_LABEL_FAMILIES` is live, because the column is
    polymorphic — the family decides whether a label is an entity name."""
    out: set[tuple[str, str]] = set()
    guarded = _foreign_keys_to(con, "entities")
    tables = _table_names(con)
    for table in sorted(tables):
        for col in (r[1] for r in con.execute(f"PRAGMA table_info('{table}')")):
            if (table, col) in guarded:
                continue
            if col in _NAME_SHAPED_COLUMNS and (table, col) not in _NON_ENTITY_NAME_COLUMNS:
                out.add((table, col))
    if "hyper_edges" in tables:
        fam_list = ",".join(f"'{f}'" for f in sorted(_HYPEREDGE_ENTITY_LABEL_FAMILIES))
        has_entity_family = con.execute(
            f"SELECT 1 FROM hyper_edges WHERE edge_type IN ({fam_list}) LIMIT 1"  # noqa: S608  # fam_list is a module-constant vocabulary, never user input
        ).fetchone()
        if has_entity_family is not None:
            out.add(("hyper_edges", "label"))
    return out


def _foreign_keys_to(con, ref_table: str) -> set[tuple[str, str]]:
    """Every ``(child_table, child_column)`` FK referencing ``ref_table``.

    Engine-agnostic: SQLite reads ``PRAGMA foreign_key_list`` (rows are
    ``(id, seq, table, from, to, on_update, on_delete, match)`` — ``from``
    at index 3, ``to`` at index 2); DuckDB has no such pragma and exposes
    the same edges through ``information_schema`` (verified on 2.x:
    table_constraints ⋈ key_column_usage ⋈ referential_constraints ⋈
    constraint_column_usage).
    """
    out: set[tuple[str, str]] = set()
    if is_duckdb_conn(con):
        rows = con.execute(
            "SELECT tc.table_name, kcu.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            "  AND tc.table_schema = kcu.table_schema "
            "JOIN information_schema.referential_constraints rc "
            "  ON tc.constraint_name = rc.constraint_name "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON rc.unique_constraint_name = ccu.constraint_name "
            "WHERE tc.constraint_type = 'FOREIGN KEY' "
            "  AND ccu.table_name = ? AND ccu.column_name = 'name'",
            (ref_table,),
        ).fetchall()
        out.update((tbl, col) for tbl, col in rows)
        return out
    for table in sorted(_table_names(con)):
        for fk in con.execute(f"PRAGMA foreign_key_list('{table}')").fetchall():
            if fk[2] == ref_table:
                out.add((table, fk[3]))
    return out


def entity_fk_children(con) -> set[tuple[str, str]]:
    """Every ``(table, column)`` foreign key pointing at ``entities(name)``.

    The coverage guard for :data:`_REFOLD_COLUMNS` — a new FK child added to
    the schema without a re-point entry would turn the next fold into silent
    data loss, so ``tests/test_name_dedup.py`` asserts this set is covered."""
    return _foreign_keys_to(con, "entities")


def _refold_entity_refs(
    con, variant: str, canonical: str, present: set[str]
) -> tuple[dict[str, int], int]:
    """Re-point every surviving reference to ``variant`` at ``canonical``.

    Returns ``({table.column: rows_repointed}, rows_collapsed)``. Tables
    absent from this database (the toy fixtures) are skipped, so the same
    function serves the live store and the tests.

    COLLIDE-BEFORE-MOVE, not move-then-dedupe: a UNIQUE constraint is
    enforced per statement, so ``UPDATE ... SET <col> = canonical`` raises
    ``IntegrityError`` the moment the canonical already holds that tag /
    metric / identifier — and because the caller runs inside one ``with con``
    block, that raise rolls back the re-points already made for OTHER
    tables. The variant's colliding rows are therefore dropped first, while
    the two spellings are still distinguishable."""
    touched: dict[str, int] = {}
    collapsed = 0
    for table, column, unique in _REFOLD_COLUMNS:
        if table not in present:
            continue
        if unique:
            # Drop the variant's rows whose remaining UNIQUE columns the
            # canonical already holds — the fact survives through the
            # canonical row. Scoped to this (canonical, variant) pair, so
            # pre-existing duplicates elsewhere are never swept up.
            others = [c.strip() for c in unique.split(",") if c.strip() != column]
            if others:
                match = " AND ".join(f'c."{c}" IS v."{c}"' for c in others)
                collapsed += con.execute(
                    f'DELETE FROM "{table}" AS v WHERE v."{column}" = ? AND EXISTS ('  # noqa: S608  # table/column from the closed module-level tuple, never input; values parameterized
                    f'  SELECT 1 FROM "{table}" c'
                    f'  WHERE c."{column}" = ? AND {match})',
                    (variant, canonical),
                ).rowcount
            else:
                # UNIQUE is the column ITSELF (a key column): the variant row
                # cannot move onto a canonical row that already exists.
                collapsed += con.execute(
                    f'DELETE FROM "{table}" WHERE "{column}" = ? AND EXISTS ('  # noqa: S608
                    f'  SELECT 1 FROM "{table}" WHERE "{column}" = ?)',
                    (variant, canonical),
                ).rowcount
        n = con.execute(
            f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?',  # noqa: S608
            (canonical, variant),
        ).rowcount
        if n:
            touched[f"{table}.{column}"] = n
    return touched, collapsed


def _stamp_repointed_cohorts(con, touched: dict[str, int]) -> None:
    """Mark every derived cohort the fold just re-pointed (S3, chosen behaviour).

    Re-pointing keeps rows from orphaning — that is why the columns are in
    ``_REFOLD_COLUMNS`` at all — but it is NOT equivalent to re-deriving: the
    deriver recomputes pairs/triangles from the current graph, so a patched
    table can hold a different count (2026-11-16: 41,319 repointed vs 41,328
    re-derived). Stamping ``repointed`` keeps that distinction visible instead
    of letting the table silently claim to be derived.

    The marker table belongs to ``derive_relational_insights``; when it does
    not exist yet (a store whose deriver never ran) the fold proceeds without
    stamping rather than inventing schema mid-transaction."""
    from helpers.graph.derive_relational_insights import (  # noqa: PLC0415
        DERIVED_COHORT_TABLES,
        MODE_REPOINTED,
        stamp_cohort,
    )

    if not table_exists(con, "derived_cohort_meta"):
        return
    for table in sorted({key.split(".", 1)[0] for key in touched} & DERIVED_COHORT_TABLES):
        stamp_cohort(con, table, MODE_REPOINTED)


def apply_plan(  # noqa: C901
    con,
    plan_path: Path = PLAN_PATH,
    *,
    yes: bool = False,
    skip_persons: bool = True,
    ledger_path: Path | None = None,
) -> dict:
    """S2: re-point variants → canonical per the plan artifacts, then
    collapse exact-duplicate rows. Gated: refuses without ``yes`` and
    refuses missing artifacts. Folds by VERBATIM name from the JSONL
    machine form (newlines and all). ``skip_persons`` is the company
    merge's person defense — the PERSON arc's own apply passes False
    (its plan is all person folds by construction). Row accounting is
    exact: re-pointing never deletes; collisions against UNIQUE(source,
    target, edge_type) and intra-variant self-loop rows are
    consolidated first (both reported)."""
    jsonl_path = plan_path.with_suffix(".jsonl")
    if not plan_path.exists() or not jsonl_path.exists():
        raise SystemExit(f"plan artifacts missing: {plan_path} (+.jsonl) — run --plan first")
    if not yes:
        raise SystemExit("S2 is gated on operator sign-off: review the plan, then --apply --yes")
    folded: dict[str, str] = {}
    for row in _plan_rows(jsonl_path):
        canon = row["canonical"]
        if skip_persons and is_person_name(canon):  # stale artifact: person cluster → skip
            continue
        for variant in row["variants"]:
            if skip_persons and is_person_name(variant):
                continue
            folded[variant] = canon
    # name_key_letter_run_collapse §4 acceptance #3: ZERO cross-entity_type
    # merges across the whole triage — enforced by assertion, not by review.
    # A plan that would fold two spellings of DIFFERENT entity kinds is
    # almost always a normaliser over-reach; refuse the whole batch before
    # any write rather than trust the human reader to spot it.
    if "entities" in _table_names(con) and folded:
        _et_by_name = dict(con.execute("SELECT name, entity_type FROM entities").fetchall())
        for _variant, _canon in sorted(folded.items()):
            _vt = _et_by_name.get(_variant)
            _ct = _et_by_name.get(_canon)
            if _vt is not None and _ct is not None and _vt != _ct:
                raise AssertionError(
                    f"cross-entity_type fold refused: {_variant!r} ({_vt}) -> {_canon!r} ({_ct})"
                )
    if not folded:
        # An all-person (stale) plan defuses to a no-op, not an error:
        # the S2 gate is operator sign-off, and a signed-but-person-only
        # plan must still be inert.
        before = con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        return {
            "variants_folded": 0,
            "rows_repointed": 0,
            "duplicate_rows_removed": 0,
            "collapsing_duplicates": 0,
            "entities_folded": 0,
            "entities_retained_referenced": 0,
            "entities_retained_names": [],
            "entities_already_absent": [],
            "refs_repointed": {},
            "refs_collapsed": 0,
            "rows_before": before,
            "rows_after": before,
            "zero_loss_ok": True,
            "fold_ledger_appended": 0,
            "fold_ledger": str(ledger_path or ledger_path_for(plan_path)),
            "note": "no applicable fold rows (person clusters excluded)",
        }
    before = con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
    repointed = 0
    collapsed = 0
    with con:
        for variant, canon in sorted(folded.items()):
            # Relations already stored under BOTH spellings: the re-point
            # would violate UNIQUE(source, target, edge_type). Consolidate
            # first — the canonical row keeps its id/properties, the
            # variant row drops (its relation survives through the
            # canonical row; count reported).
            for side in ("source", "target"):
                other = "target" if side == "source" else "source"
                collapsed += con.execute(
                    "DELETE FROM graph_edges WHERE id IN ("  # noqa: S608  # column names from the closed ("source", "target") literal tuple; values parameterized
                    "  SELECT v.id FROM graph_edges v JOIN graph_edges c"
                    f"   ON c.{side} = ? AND v.{side} = ?"
                    f"  AND c.{other} = v.{other} AND c.edge_type = v.edge_type"
                    ")",
                    (canon, variant),
                ).rowcount
            # Edges BETWEEN the two spellings themselves: post-fold these
            # would be self-loops (CHECK source != target) — intra-variant
            # artifacts, dropped and reported.
            collapsed += con.execute(
                "DELETE FROM graph_edges WHERE (source = ? AND target = ?)"
                " OR (source = ? AND target = ?)",
                (variant, canon, canon, variant),
            ).rowcount
            cur = con.execute(
                "UPDATE graph_edges SET source = ? WHERE source = ?", (canon, variant)
            ).rowcount
            cur += con.execute(
                "UPDATE graph_edges SET target = ? WHERE target = ?", (canon, variant)
            ).rowcount
            repointed += cur
        # Collapse exact duplicates (oldest id kept) — the re-point's
        # expected consequence, reported not hidden.
        dupes = con.execute(
            "DELETE FROM graph_edges WHERE id NOT IN ("
            "  SELECT MIN(id) FROM graph_edges"
            "  GROUP BY source, target, edge_type, source_ref, symmetric)"
        ).rowcount
    # Registry fold: with the edges re-pointed, no edge references a
    # variant entity — the registry rows go too, or the next vigil sync
    # re-creates the variants from them (the reversibility the proposal
    # flags). Every OTHER foreign key onto entities(name) is re-pointed
    # first (_REFOLD_COLUMNS): they are ON DELETE CASCADE / SET NULL, so
    # deleting the row without that step silently destroys authored rows.
    # Rows still referenced by a NON-cascade estate are retained and
    # reported; the fold map keeps resolving them regardless.
    entities_folded = 0
    entities_retained = 0
    refs_repointed: dict[str, int] = {}
    refs_collapsed = 0
    #: Variants whose registry row was ACTUALLY deleted, and variants that
    #: survived (unique collision). Only the former may enter the ledger —
    #: the latter never folded, so recording them would assert a merge that
    #: did not happen.
    #:
    #: A third outcome exists and must NOT be conflated with a collision: the
    #: variant row is ABSENT because an earlier run already folded it (the
    #: ledger sweep's whole purpose). Reporting those as "retained" is a lie
    #: in the other direction — it claims work is stuck when it is done.
    folded_now: dict[str, str] = {}
    retained_variants: list[str] = []
    absent_variants: list[str] = []
    has_entities = table_exists(con, "entities")
    if has_entities:
        present = _table_names(con)
        for variant in folded:
            exists = (
                con.execute("SELECT 1 FROM entities WHERE name = ?", (variant,)).fetchone()
                is not None
            )
            if not exists:
                absent_variants.append(variant)
                continue
            try:
                with con:
                    touched, collapsed_refs = _refold_entity_refs(
                        con, variant, folded[variant], present
                    )
                    deleted = con.execute(
                        "DELETE FROM entities WHERE name = ?", (variant,)
                    ).rowcount
                    if not deleted:
                        raise sqlite3.IntegrityError("entity row vanished mid-fold")
                    entities_folded += deleted
                    # S3 of derived_name_columns_guard: a re-point is NOT a
                    # re-derivation. Stamp every derived cohort we patched so a
                    # reader can tell patched rows from recomputed ones (the
                    # 2026-11-16 gap was 41,319 repointed vs 41,328 derived).
                    _stamp_repointed_cohorts(con, touched)
                folded_now[variant] = folded[variant]
                for key, n in touched.items():
                    refs_repointed[key] = refs_repointed.get(key, 0) + n
                refs_collapsed += collapsed_refs
            except _constraint_error_types():
                entities_retained += 1
                retained_variants.append(variant)
    # Ledger sweep (the invariant the S2 tests assert): variants folded by
    # EARLIER runs can be re-minted later with zero edges (a replay or a
    # source-side writer); the census reads graph_edges names, so such rows
    # are invisible to every later plan and would survive forever. Retire
    # them here — but FOLD FIRST: hyper_incidences.entity_name is ON DELETE
    # CASCADE, so deleting the entity row without re-pointing its
    # memberships silently drops them from every derived surface (seen live
    # 2026-11-12: family:Tata lost a member). Re-point to the canonical,
    # collapse exact duplicates, and only then remove the row.
    ledger = ledger_path or ledger_path_for(plan_path)
    ledger_swept = 0
    ledger_folds: dict[str, str] = {}
    if has_entities and ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            variant = rec.get("variant")
            if variant and variant not in folded and rec.get("canonical"):
                ledger_folds[variant] = rec["canonical"]
        for variant, canon in sorted(ledger_folds.items()):
            if not con.execute("SELECT 1 FROM entities WHERE name = ?", (variant,)).fetchone():
                continue
            # A stale ledger row can name a canonical that is itself ALREADY
            # absent from the registry (folded away in an earlier arc, or a
            # source that never minted it here). Re-pointing a surviving
            # variant onto a missing canonical would violate the FK on every
            # child table and abort the whole apply AFTER the signed plan has
            # folded. Skip such rows: they cannot be swept into a canonical
            # that does not exist, and the variant stays a live entity rather
            # than being deleted. (name_key_letter_run_collapse S2 — found on
            # the live store 2026-11-16: 5 canonicals absent pre-fold.)
            if not con.execute("SELECT 1 FROM entities WHERE name = ?", (canon,)).fetchone():
                continue
            with con:
                # hyper_incidences is a _REFOLD_COLUMNS entry now, so the
                # same collide-before-move path re-points memberships AND
                # every other FK/bare-name child in one pass.
                touched, collapsed_refs = _refold_entity_refs(
                    con, variant, canon, _table_names(con)
                )
            for key, n in touched.items():
                refs_repointed[key] = refs_repointed.get(key, 0) + n
            refs_collapsed += collapsed_refs
            refs = con.execute(
                "SELECT (SELECT COUNT(*) FROM graph_edges WHERE source = ? OR target = ?)"
                " + (SELECT COUNT(*) FROM hyper_incidences WHERE entity_name = ?)",
                (variant, variant, variant),
            ).fetchone()[0]
            if refs:
                continue
            try:
                with con:
                    ledger_swept += con.execute(
                        "DELETE FROM entities WHERE name = ?", (variant,)
                    ).rowcount
            except _constraint_error_types():
                entities_retained += 1
    after = con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
    return {
        "variants_folded": len(folded),
        "ledger_swept": ledger_swept,
        "rows_repointed": repointed,
        "duplicate_rows_removed": dupes + collapsed,
        "collapsing_duplicates": collapsed,
        "entities_folded": entities_folded,
        "entities_retained_referenced": entities_retained,
        "entities_retained_names": sorted(retained_variants),
        "entities_already_absent": sorted(absent_variants),
        "refs_repointed": refs_repointed,
        "refs_collapsed": refs_collapsed,
        "rows_before": before,
        "rows_after": after,
        "zero_loss_ok": before - (dupes + collapsed) == after,
        "fold_ledger_appended": _append_ledger(folded_now, ledger, con),
        "fold_ledger": str(ledger),
    }


def _nse_names() -> list[str]:
    """NSE equity listing display names from the sources DB (empty when
    the gitignored store is absent, e.g. a fresh clone)."""
    if not NSE_SOURCE.exists():
        return []
    import duckdb  # noqa: PLC0415  (canonical lane only; keep module import light)

    con = duckdb.connect(str(NSE_SOURCE), read_only=True)
    try:
        return [
            r[0]
            for r in con.execute(
                "SELECT name FROM exchange_listings "
                "WHERE exchange = 'NSE' AND asset_type = 'equity' AND name IS NOT NULL"
            ).fetchall()
        ]
    finally:
        con.close()


def _diff_class(variant: str, winner: str) -> str:
    """'safe' | 'review' | 'reject' for the tokens a variant adds over
    the winner. Missing tokens are harmless shortening and never
    classify; the deciding set is what the variant ADDS."""

    def toks(s: str) -> set[str]:
        return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t}

    extra = toks(variant) - toks(winner)
    if not extra:
        return "safe"
    if extra & _CANON_REVIEW_TAILS:
        return "review"
    # "Private" is safe only as the 'Private Limited' legal unit: alone it
    # distinguishes real neighbours (Nifty Bank vs Nifty Private Bank).
    standalone_private = (extra & {"private", "pvt"}) and not (extra & {"limited", "ltd"})
    if standalone_private or extra - _CANON_SAFE_TAILS - {"private", "pvt"}:
        return "reject"
    return "safe"


def canonical_census(con, *, nse_names: list[str] | None = None) -> dict:  # noqa: C901  # one census pass (loose-key clusters + NSE fold eligibility)
    """Duplicate-spelling clusters over the ENTITIES table keyed by the
    loader's own loose key (helpers.maintenance.related_party_sync
    ._loose_key -- the same rule that would mint a variant), so the
    canonical fold and the mint path can never disagree. A cluster is
    foldable only when an NSE equity listing shares its loose key (a
    listed company certifies the cluster is one real issuer's
    spellings); the winner is an exact-normalised NSE match, else the
    highest-ref member. Variants classify per _diff_class; mixed
    entity_type clusters and any 'review' variant keep the whole
    cluster out of the plan (report-only)."""
    from helpers.maintenance.related_party_sync import _loose_key  # noqa: PLC0415

    if nse_names is None:
        nse_names = _nse_names()
    nse_keys: dict[str, str] = {}
    for n in nse_names:
        nse_keys.setdefault(_loose_key(n), n)

    ents = con.execute("SELECT name, entity_type FROM entities").fetchall()
    refs: dict[str, int] = dict.fromkeys((n for n, _ in ents), 0)
    for (n,) in con.execute(
        "SELECT source FROM graph_edges UNION ALL SELECT target FROM graph_edges"
    ).fetchall():
        if n in refs:
            refs[n] += 1
    try:
        incidences = con.execute("SELECT entity_name FROM hyper_incidences").fetchall()
    except sqlite3.OperationalError:  # minimal fixtures may lack the table
        incidences = []
    for (n,) in incidences:
        if n in refs:
            refs[n] += 1

    clusters: dict[str, list[str]] = defaultdict(list)
    etypes: dict[str, set[str]] = defaultdict(set)
    for n, t in ents:
        clusters[_loose_key(n)].append(n)
        etypes[_loose_key(n)].add(t)

    plan, review, reject, unlisted = [], [], [], []
    for key, members in sorted(clusters.items()):
        if len(members) < 2:
            continue
        nse = nse_keys.get(key)
        if nse is None:
            unlisted.append({"key": key, "members": sorted(members)})
            continue
        exact = [m for m in members if _name_key(FOOTNOTE_RE.sub("", m)) == _name_key(nse)]
        if len(exact) == 1:
            winner = exact[0]
        else:  # refs desc, then the shorter display form, then stable lex
            winner = sorted(
                members,
                key=lambda m: (
                    -refs[m],
                    len(re.findall(r"[a-z0-9]+", m.lower())),
                    m,
                ),
            )[0]
        mixed = len(etypes[key]) > 1
        blocks, revs, rejs = [], [], []
        for v in sorted(members):
            if v == winner:
                continue
            cls = _diff_class(v, winner)
            (blocks if cls == "safe" else revs if cls == "review" else rejs).append(v)
        if mixed or revs or not blocks:
            review.append(
                {
                    "key": key,
                    "winner": winner,
                    "nse": nse,
                    "safe": blocks,
                    "review": revs,
                    "reject": rejs,
                    "entity_types": sorted(etypes[key]),
                    "refs": {m: refs[m] for m in members},
                }
            )
            continue
        plan.append(
            {
                "canonical": winner,
                "variants": blocks,
                "key": key,
                "listed_hit": True,
                "refs_moved": sum(refs[v] for v in blocks),
            }
        )
        if rejs:
            reject.append({"key": key, "winner": winner, "reject": rejs})
    return {
        "n_clusters": len([c for c in clusters.values() if len(c) > 1]),
        "plan": plan,
        "review": review,
        "reject": reject,
        "unlisted": unlisted,
        "refs_moved": sum(p["refs_moved"] for p in plan),
    }


def write_canonical_plan(cc: dict, path: Path = CANON_PLAN_PATH) -> Path:
    """Same artifact contract as write_plan: a human-reviewable txt plus
    the JSONL machine form --apply consumes verbatim. REVIEW/REJECT/
    unlisted clusters are REPORT-ONLY and never reach the machine form."""
    display = lambda s: s.replace(chr(10), " ")  # noqa: E731  (txt readability only)
    jsonl_path = path.with_suffix(".jsonl")
    lines = [
        "# Canonical-name merge plan (canonical_name_resolution -- DRY-RUN artifact)",
        f"# clusters: {cc['n_clusters']} | foldable: {len(cc['plan'])} | "
        f"review: {len(cc['review'])} | foreign-form rejects: {len(cc['reject'])} | "
        f"unlisted: {len(cc['unlisted'])} | edge/incidence refs re-pointed: {cc['refs_moved']}",
        "# winner rule: exact-normalised NSE listing match > refs; NSE loose-key match certifies the cluster",
        "# variant rule: only legal-tail/display noise (the/company/co/private/pvt/limited/ltd) folds;",
        "#   Corp/Corporation differences -> review; any other added token (Inc, LLC, ...) -> reject (subsidiary-shaped)",
        "# REVIEW and REJECT sections are report-only; S2 applies ONLY the foldable blocks",
        f"# machine form: {jsonl_path.name}",
        "",
    ]
    for p_ in cc["plan"]:
        lines.append(
            f"== {display(p_['canonical'])} [listed]  (key: {p_['key']}; refs moved {p_['refs_moved']})"
        )
        for v in p_["variants"]:
            lines.append(f"   {display(v)}  -> fold")
        lines.append("")
    if cc["review"]:
        lines.append("# -- REVIEW (report-only: mixed entity_type or Corp-form differences) --")
        for r_ in cc["review"]:
            bits = [
                f"{m}({r_['refs'][m]})"
                for m in sorted(r_["refs"], key=r_["refs"].get, reverse=True)
            ]
            note = (
                "mixed-types " + "/".join(r_["entity_types"]) if len(r_["entity_types"]) > 1 else ""
            )
            lines.append(
                f"   {r_['winner']} <- {'; '.join(r_['safe'])}"
                f" ; review: {'; '.join(r_['review'])}"
                f" ; reject: {'; '.join(r_['reject'])}"
                + (f" ; {note}" if note else "")
                + f"  [{'; '.join(bits)}]"
            )
        lines.append("")
    if cc["reject"]:
        lines.append(
            "# -- foreign-form variants never folded (subsidiary-shaped: Inc/LLC/other tokens) --"
        )
        for r_ in cc["reject"]:
            lines.append(f"   {r_['winner']}  !=  {'; '.join(r_['reject'])}")
        lines.append("")
    path.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    jsonl_path.write_text(
        "".join(
            json.dumps(
                {k: p_[k] for k in ("canonical", "variants", "key", "listed_hit")},
                ensure_ascii=False,
            )
            + chr(10)
            for p_ in cc["plan"]
        ),
        encoding="utf-8",
    )
    return path


def check(con, baseline_path: Path = BASELINE_PATH) -> int:
    """S4 gate: exit 1 when the live cluster count exceeds the recorded
    baseline. Missing baseline → report only (exit 0): the baseline is
    set post-S2 via ``--baseline``."""
    cen = census(con)
    if not baseline_path.exists():
        print(f"dup-census: {cen['n_clusters']} clusters (no baseline recorded — informational)")
        return 0
    baseline = int(baseline_path.read_text(encoding="utf-8").strip())
    if cen["n_clusters"] > baseline:
        print(
            f"FAIL: dup-census {cen['n_clusters']} > baseline {baseline} — "
            f"a writer is creating name variants"
        )
        return 1
    print(f"ok: dup-census {cen['n_clusters']} <= baseline {baseline}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Entity-name dedup: census / plan / check / apply")
    p.add_argument("--census", action="store_true", help="print the collision census")
    p.add_argument("--plan", action="store_true", help="write the merge-plan artifact (dry-run)")
    p.add_argument(
        "--transpositions",
        action="store_true",
        help="include token-sort pairs in the plan",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="S4 gate: census vs baseline (exit 1 on growth)",
    )
    p.add_argument(
        "--baseline",
        action="store_true",
        help="record the CURRENT count as the S4 baseline",
    )
    p.add_argument(
        "--canonical-plan",
        action="store_true",
        help="canonical_name_resolution S1: duplicate-spelling census vs NSE winners; writes the plan artifacts (dry-run)",
    )
    p.add_argument(
        "--plan-path",
        type=Path,
        default=None,
        help="apply a non-default plan artifact (e.g. the canonical plan)",
    )
    p.add_argument("--apply", action="store_true", help="S2: apply the plan (requires --yes)")
    p.add_argument("--yes", action="store_true", help="confirm the S2 write")
    args = p.parse_args(argv)

    con = connect()
    try:
        if args.canonical_plan:
            cc = canonical_census(con)
            path = write_canonical_plan(cc)
            print(f"canonical plan -> {path}")
            print(
                f"clusters={cc['n_clusters']} foldable={len(cc['plan'])} "
                f"review={len(cc['review'])} rejects={len(cc['reject'])} "
                f"unlisted={len(cc['unlisted'])} refs_moved={cc['refs_moved']}"
            )
            print("operator review gates S2: --apply --yes --plan-path <the canonical plan>")
            return 0
        if args.apply:
            plan_path = args.plan_path or PLAN_PATH
            ledger = LEDGER_PATH if args.plan_path else None
            res = apply_plan(con, plan_path, yes=args.yes, ledger_path=ledger)
            print(json.dumps(res, indent=2))
            print("next: refresh the duckdb graph caches (standard graph rebuild)")
            return 0
        cen = census(con)
        if args.census:
            print(
                f"names={cen['n_names']} clusters={cen['n_clusters']} "
                f"person_clusters={len(cen['person_clusters'])} "
                f"edge_rows_touched={cen['edge_rows_touched']} "
                f"footnote_names={cen['footnote_names']} "
                f"transpositions={len(cen['transpositions'])}"
            )
            for c in cen["clusters"][:10]:
                print(
                    f"  {c['canonical']}  <- {len(c['variants'])} variants, {c['edge_rows']} edge rows"
                )
            return 0
        if args.plan:
            if not args.transpositions:
                cen["transpositions"] = []
            path = write_plan(cen)
            print(f"plan -> {path}")
            print(f"clusters={cen['n_clusters']} (operator review gates S2: --apply --yes)")
            return 0
        if args.baseline:
            BASELINE_PATH.write_text(str(cen["n_clusters"]) + "\n", encoding="utf-8")
            print(f"baseline <- {cen['n_clusters']} ({BASELINE_PATH})")
            return 0
        if args.check:
            return check(con)
        p.print_help()
        return 2
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
