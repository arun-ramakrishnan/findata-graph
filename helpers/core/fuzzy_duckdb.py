#!/usr/bin/env python3
"""DuckDB pushdown of the fuzzy word-overlap stage (algorithms_adoption_sweep S1).

`fuzzy_match.word_overlap_match` scans every entity in a Python loop per
query, on the entity-resolve path that fires before VSS on each note
capture. This module runs the SAME scoring as ONE set-based SQL pass
over the entity corpus, using DuckDB 2.0.0 list functions
(``string_split_regex`` + ``list_filter`` + ``list_intersect``) — no new
dependencies (duckdb + pyarrow are declared).

Contract parity with ``fuzzy_match.word_overlap_match`` is exact and
tested (``tests/test_fuzzy_duckdb.py``): tokenization (`_tokenize`),
stopword stripping, the distinctive-word requirement (`_GENERIC_WORDS`),
containment short-circuit (first entity in iteration order wins, at
score 1.0, blank/whitespace query guarded), score =
|overlap| / max(|q_tokens|, |e_tokens|), strict ``>`` best-score with
first-wins tie resolution, threshold gate.

The Python path stays the degrade chain (tiny entity sets, no duckdb
import budget); callers route here when they hold a connection or face
a large corpus. Query tokens are computed in Python (one string —
nothing to push down) and passed as a parameter, which also guarantees
tokenizer parity.
"""

from __future__ import annotations

from collections.abc import Iterable

# ---------------------------------------------------------------------------
# Tokenizer / word lists — imported from fuzzy_match so the two backends
# can never drift (single definition of _tokenize/_STOPWORDS/_GENERIC_WORDS).
# ---------------------------------------------------------------------------

_REPO_ROOT = __file__[: __file__.rfind("/helpers/")] if "/helpers/" in __file__ else "."
import sys  # noqa: E402

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.fuzzy_match import _GENERIC_WORDS, _STOPWORDS, _tokenize  # noqa: E402

#: registration name for the entity table inside the DuckDB session.
#: Re-registering the same name replaces the binding (idempotent).
_ENTITY_VIEW = "fuzzy_entities_sql"


def _entity_table(entities: Iterable[str]):
    import pyarrow as pa

    names = list(entities)
    return pa.table({"idx": list(range(len(names))), "name": names})


def _register(conn, entities: Iterable[str]):
    conn.register(_ENTITY_VIEW, _entity_table(entities))


# Token expressions shared by both query arms. list_distinct gives the
# Python-set cardinality (a name like "Tata Tata Motors" must count
# 'tata' once). The `[&.-]` class mirrors _tokenize's per-char replaces.
_TOKS_SQL = (
    "list_distinct(list_filter(list_filter("
    "string_split_regex(lower(regexp_replace(name, '[&.-]', ' ', 'g')), '\\s+'), "
    "lambda t: t != ''), "
    "lambda t: NOT list_contains(?::VARCHAR[], t)))"
)


def _containment(conn, query_lower: str) -> tuple[str | None, float]:
    """First entity (iteration order) containing / contained by the query."""
    rows = conn.execute(
        f"""
        SELECT name FROM {_ENTITY_VIEW}
        WHERE ?::VARCHAR != ''
          AND (contains(lower(name), ?::VARCHAR) OR contains(?::VARCHAR, lower(name)))
        ORDER BY idx LIMIT 1
        """,  # noqa: S608  # table name is a module constant
        [query_lower, query_lower, query_lower],
    ).fetchall()  # fetchall, not fetchone — a partial result would poison a reused conn
    if rows:
        return rows[0][0], 1.0
    return None, 0.0


def word_overlap_match_sql(
    query: str,
    entities: Iterable[str],
    threshold: float = 0.5,
    conn=None,
) -> tuple[str | None, float]:
    """SQL twin of :func:`fuzzy_match.word_overlap_match` — exact contract.

    Args:
        query: Search query string
        entities: Entity name strings (materialised once per call)
        threshold: Minimum overlap ratio (0.0 to 1.0)
        conn: Optional DuckDB connection to reuse; a transient in-memory
            connection is opened otherwise. When a connection is passed,
            the entity table is registered under ``fuzzy_entities_sql``
            for the session (re-registration on the next call replaces it).

    Returns:
        (best_match_name, score) or (None, 0.0) — identical semantics.
    """
    import duckdb

    own_conn = conn is None
    if own_conn:
        conn = duckdb.connect()
    try:
        _register(conn, entities)

        query_lower = query.lower().strip()
        q_tokens = sorted(_tokenize(query))
        q_lower_param = query_lower
        stop = sorted(_STOPWORDS)
        generic = sorted(_GENERIC_WORDS)

        # Arm 1 — containment short-circuit (first-wins, score 1.0).
        match, score = _containment(conn, q_lower_param)
        if match is not None:
            return match, score

        # Arm 2 — word overlap with the distinctive-word requirement.
        # Python's `overlap - _GENERIC_WORDS` non-empty check is the
        # proper-subset test on the distinct intersection. ORDER BY
        # score DESC, idx ASC reproduces the strict-`>` first-wins scan.
        rows = conn.execute(
            f"""
            WITH e AS (
                SELECT idx, name, {_TOKS_SQL} AS toks
                FROM {_ENTITY_VIEW}
            ), scored AS (
                SELECT idx, name, toks,
                       length(list_intersect(?::VARCHAR[], toks))::DOUBLE
                       / greatest(length(?::VARCHAR[]), length(toks)) AS score,
                       length(list_intersect(?::VARCHAR[], toks)) AS overlap_n
                FROM e
            )
            SELECT name, score FROM scored
            WHERE length(?::VARCHAR[]) > 0
              AND length(toks) > 0
              AND overlap_n > 0
              AND length(list_intersect(list_intersect(?::VARCHAR[], toks),
                                        ?::VARCHAR[])) < overlap_n
            ORDER BY score DESC, idx ASC LIMIT 1
            """,  # noqa: S608  # table name is a module constant
            [
                stop,  # _TOKS_SQL stopword filter (e CTE, first positional)
                q_tokens,  # list_intersect(left, toks)
                q_tokens,  # greatest(left length, ...)
                q_tokens,  # overlap_n intersect
                q_tokens,  # WHERE length(q) > 0
                q_tokens,  # outer intersect (distinctive)
                generic,  # against generic list
            ],
        ).fetchall()  # fetchall — drain the result so a reused conn stays usable

        if rows and rows[0][1] > threshold:
            return rows[0][0], float(rows[0][1])
        return None, 0.0
    finally:
        if own_conn:
            conn.close()


def fuzzy_match_sql(
    query: str,
    entities: Iterable[str],
    threshold: float = 0.5,
    spellfix_conn=None,
    duckdb_conn=None,
) -> tuple[str | None, str | None, float]:
    """Whole-pipeline twin of :func:`fuzzy_match.fuzzy_match` with the
    word-overlap stage pushed down to DuckDB. Stages 1/2/4 (exact,
    abbreviation, spellfix1) are identical Python/SQLite code paths.

    Returns:
        (matched_name, method, score); method is 'exact', 'abbreviation',
        'word_overlap_sql' or 'spellfix1'.
    """
    from helpers.core.fuzzy_match import ABBREVIATIONS

    # 1. Exact match (case-insensitive)
    query_lower = query.lower().strip()
    for entity in entities:
        if entity.lower() == query_lower:
            return entity, "exact", 1.0

    # 2. Abbreviation lookup (exact or substring)
    upper_query = query.upper().strip()
    if upper_query in ABBREVIATIONS:
        return ABBREVIATIONS[upper_query], "abbreviation", 1.0
    for abbr, entity in ABBREVIATIONS.items():
        if len(abbr) >= 3 and abbr.upper() in upper_query:
            return entity, "abbreviation", 1.0

    # 3. Word-overlap heuristic — DuckDB pushdown
    match, score = word_overlap_match_sql(query, entities, threshold, conn=duckdb_conn)
    if match:
        return match, "word_overlap_sql", score

    # 4. Spellfix1 fallback for typos (same as the Python pipeline)
    if spellfix_conn is not None:
        try:
            cursor = spellfix_conn.execute(
                """
                SELECT word, score FROM entities_fuzzy
                WHERE word MATCH ? AND distance < 200
                ORDER BY score LIMIT 1
                """,
                (query,),
            )
            result = cursor.fetchone()
            if result:
                return result[0], "spellfix1", 1.0
        except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
            pass  # spellfix not available, skip

    return None, None, 0.0
