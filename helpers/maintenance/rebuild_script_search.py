#!/usr/bin/env python3
"""
Rebuild the `script_search` FTS5 index over the repo's own code surface.

The repo has ~57 scripts under helpers/, ~127 test modules under tests/,
the root app.py, and 44 Makefile targets — and grows weekly. codebase-memory
answers STRUCTURE questions (symbols, callers); it does not answer INTENT
questions: which script audits relation diffs, which test file covers the
yfinance driver, what `make qa` actually runs. That knowledge lives in
module docstrings, argparse declarations, and the Makefile — unindexed.
This script gives it the content-addressable treatment doc/ already has
(proposal: doc/improvements/archive/tooling/script_metadata_search.md):

- one FTS5 row per script / test module / make target, composed from the
  module docstring (purpose = first paragraph), regex-extracted argparse
  flags, AST-import-derived `tested_by` links, and Makefile wiring;
- per-row JSON embedding (local bge-small when available, deterministic
  pseudo fallback) for hybrid RRF ranking, reusing the shared
  (sha256(text), model) sidecar cache — machinery imported from
  rebuild_doc_search, NOT forked.

RESIDENCE — own sidecar DB, never research.db, same doctrine as doc_search:
memory/script_search.db is gitignored via memory/, never snapshotted, never
attached to DuckDB. Derived state; delete it and one warm rebuild restores
everything (embeddings included — the cache is content-addressed).

Usage:
    python3 helpers/maintenance/rebuild_script_search.py            # rebuild, exit 0
    python3 helpers/maintenance/rebuild_script_search.py --db PATH  # alternate sidecar
    python3 helpers/maintenance/rebuild_script_search.py --check    # freshness report
    python3 helpers/maintenance/rebuild_script_search.py --incremental

Cross-file inputs: a script row's `tested_by` / `make` fields are derived
from OTHER units (test imports, Makefile recipes), so unlike doc_search the
incremental mode cannot skip re-reading unchanged files — it always
re-extracts every unit (cheap: ~185 small files, well under a second) and
re-composes all rows, but only re-EMBEDS through the shared cache (free for
unchanged text) and only WRITES rows whose tuple actually changed
(row-keyed diff on `title`). `--check` reports the unit-level
(file set + mtime + blake2b) drift and exits 1 on it — the house gate
doctrine, enforced by the rebuild_script_search entry in make perf
(tests/run_perf_benchmarks.py). Deliberately NOT in make qa: code edits
land between maint cycles and would redden qa constantly.

Exit codes: 0 success/fresh, 1 fatal error OR --check detected drift.
"""

import argparse
import ast
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

# Repo root: helpers/maintenance/rebuild_script_search.py -> parents[2]. Must
# be on sys.path BEFORE the helpers.* imports below so the script works as a
# subprocess (make perf) the same way it works under pytest. (House bootstrap.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.core.embed_cache import CachedEmbed  # noqa: E402
from helpers.maintenance import rebuild_doc_search as rds  # noqa: E402

# Monkeypatchable (the VAULT_ROOT lesson: import-bound root constants silently
# point tests at the live tree — tests MUST retarget all of these).
HELPERS_ROOT = _REPO_ROOT / "helpers"
TESTS_ROOT = _REPO_ROOT / "tests"
APP_PY = _REPO_ROOT / "app.py"
MAKEFILE = _REPO_ROOT / "Makefile"
SCRIPT_DB = _REPO_ROOT / "memory" / "script_search.db"
BACKUP_DIR = _REPO_ROOT / "db-backup"

# FTS5 DDL, mirroring doc_search's shape (title is the match-heavy column;
# kind/rel_path/area/purpose are UNINDEXED handles + display surface, never
# tokenized). FTS5 can't ALTER TABLE ADD COLUMN, so a schema change requires
# DROP + recreate (see _migrate_schema).
SCRIPT_SEARCH_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS script_search USING fts5("
    "title, "                 # 0  rel path (scripts/tests) or 'make <target>'
    "kind UNINDEXED, "        # 1  'script' | 'test' | 'make'
    "rel_path UNINDEXED, "    # 2  repo-rooted path or bare make target name
    "area UNINDEXED, "        # 3  helpers/<area> dir | 'app' | 'test' | 'make'
    "purpose UNINDEXED, "     # 4  docstring first para / '##' annotation (display)
    "content, "               # 5  composed block (purpose/details/cli/defs/...)
    "embedding UNINDEXED, "   # 6  JSON vector for hybrid ranking; not tokenized
    "tokenize = 'porter unicode61'"
    ")"
)
_SCRIPT_SEARCH_COLUMNS = {
    "title", "kind", "rel_path", "area", "purpose", "content", "embedding",
}

# Per-unit fingerprint (py file or Makefile). The raw file text IS the change
# key — mtime first, blake2b(text) as the same-mtime-edit gate.
SCRIPT_SEARCH_META_DDL = (
    "CREATE TABLE IF NOT EXISTS script_search_meta ("
    " unit_path TEXT PRIMARY KEY,"
    " mtime REAL NOT NULL,"
    " content_hash TEXT NOT NULL"
    ")"
)

# Model stamp home inside the sidecar (never research.db). --check never
# writes it: the stamp must describe the table's CONTENT.
SCRIPT_SEARCH_INFO_DDL = (
    "CREATE TABLE IF NOT EXISTS script_search_info ("
    " key TEXT PRIMARY KEY,"
    " value TEXT NOT NULL"
    ")"
)

# Composed-content caps: enough for BM25 + the 4K-char embed cap in
# rds._embedding_json to stay meaningful, without storing whole modules.
_DETAILS_CAP = 3000
_RECIPE_CAP = 3000
_DEFS_CAP = 40
_CLI_CAP = 30

# argparse surface, regex over source (discovery surface, not contract
# surface — `--help` remains ground truth).
_ADD_ARGUMENT = re.compile(r"add_argument\(\s*[\"'](-[\w-]+)[\"']")
_ADD_PARSER = re.compile(r"add_parser\(\s*[\"']([\w-]+)[\"']")

_ROW_COLS = "title, kind, rel_path, area, purpose, content, embedding"


def connect_script_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the script_search sidecar via the house
    connection helper (standard pragmas: Row factory, WAL, busy_timeout)."""
    path = Path(db_path) if db_path is not None else SCRIPT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    from helpers.core.db import connect as _db_connect

    return _db_connect(path)


def _iter_py_units(helpers_root: Path | None = None,
                   tests_root: Path | None = None,
                   app_py: Path | None = None):
    """Yield (rel, abs_path, kind) for every indexable Python file.

    Repo-rooted rel paths ("helpers/misc/x.py", "tests/test_x.py",
    "app.py") built from each root's own NAME, so monkeypatched tmp roots
    behave identically to the live tree. __init__.py files are skipped
    (pure package markers); conftest.py is KEPT (it carries the fixtures
    a session most often needs to find).
    """
    for root, kind in ((Path(helpers_root or HELPERS_ROOT), "script"),
                       (Path(tests_root or TESTS_ROOT), "test")):
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.py")):
            if p.name == "__init__.py":
                continue
            yield f"{root.name}/{p.relative_to(root).as_posix()}", p, kind
    app = Path(app_py or APP_PY)
    if app.is_file():
        yield "app.py", app, "script"


def _area_of(rel: str) -> str:
    """Directory-derived area: helpers/<area>/x.py -> <area>; a py directly
    under helpers/ -> 'helpers'; app.py -> 'app'; every tests/** row -> 'test'."""
    parts = rel.split("/")
    if rel == "app.py":
        return "app"
    if rel.startswith("tests/"):
        return "test"
    return parts[1] if len(parts) > 2 else "helpers"


def _dotted(rel: str) -> str:
    """Module dotted name for import matching (helpers/misc/x.py ->
    'helpers.misc.x'; app.py -> 'app')."""
    return rel[:-len(".py")].replace("/", ".")


def _split_docstring(doc: str | None) -> tuple[str, str]:
    """(first paragraph, remaining paragraphs) of a module docstring.

    Paragraphs split on blank lines; both sides whitespace-normalized per
    line but line structure kept (code blocks read better verbatim)."""
    if not doc:
        return "", ""
    parts = [p.strip("\n") for p in re.split(r"\n\s*\n", doc.strip())]
    parts = [p for p in parts if p.strip()]
    if not parts:
        return "", ""
    return parts[0], "\n\n".join(parts[1:])


def _top_level_names(tree: ast.Module | None) -> list[str]:
    """Top-level def/class names — row ENRICHMENT only, not a symbol index
    (codebase-memory owns symbols; this just lets 'the script with
    rebuild()' match without reading it)."""
    if tree is None:
        return []
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]


def _import_candidates(tree: ast.Module | None) -> set[str]:
    """Dotted module names an AST references, as import-match candidates.

    `import a.b.c` -> {a.b.c}; `from a.b import c` -> {a.b, a.b.c}. This is
    the ONLY test->script mapping signal (no grep-mention: comments and
    string literals would make `tested_by` noisy, and it is only useful if
    precise)."""
    if tree is None:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def _extract_py_unit(abs_path: Path, rel: str, kind: str) -> dict | None:
    """Read + AST-parse one Python file into its unit dict, or None on read
    error (caller treats unreadable files as absent, mirroring doc_search).
    A SyntaxError degrades to a filename-derived row — findable by path
    beats dropped."""
    try:
        src = abs_path.read_text(encoding="utf-8", errors="replace")
        mtime = abs_path.stat().st_mtime
    except OSError:
        return None
    try:
        tree = ast.parse(src)
    except SyntaxError:
        tree = None
    purpose, details = _split_docstring(ast.get_docstring(tree) if tree else None)
    if not purpose:
        purpose = abs_path.stem.replace("_", " ")
    flags = sorted(set(_ADD_ARGUMENT.findall(src)))
    subs = sorted(set(_ADD_PARSER.findall(src)))
    return {
        "rel": rel,
        "kind": kind,
        "area": _area_of(rel),
        "dotted": _dotted(rel),
        "purpose": " ".join(purpose.split())[:240],
        "details": details,
        "cli": flags[:_CLI_CAP],
        "subcommands": subs,
        "defs": _top_level_names(tree),
        "imports": _import_candidates(tree),
        "mtime": mtime,
        "hash": hashlib.blake2b(src.encode("utf-8", errors="replace"), digest_size=8).hexdigest(),
    }


def _parse_makefile(text: str) -> list[dict]:
    """Parse targets + recipes out of the house Makefile.

    House dialect: '>' recipe prefix, '## ...' help annotations on target
    lines, occasional target-line vars (`relations-enrich ARGS=...:`).
    Skipped: ':=' assignments, dot-pseudo-targets (.PHONY/.RECIPEPREFIX),
    and lines without a colon. Returns [{'name', 'purpose', 'recipe'}]."""
    targets: list[dict] = []
    current: dict | None = None
    for line in text.split("\n"):
        if line.startswith(">"):
            if current is not None:
                current["recipe"].append(line[1:].lstrip())
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":=" in line:
            current = None
            continue
        head = line.split(":", 1)[0]
        if head.startswith(".") or not head.strip():
            current = None
            continue
        name = head.split()[0]
        if "=" in name:
            # '=' in the NAME is an assignment ('FOO = x' has no colon, but
            # a colon-form line like 'FOO = a:b' lands here); an '=' after
            # the name is a target-line var (relations-enrich ARGS=...) and
            # is fine — the target is the first token.
            current = None
            continue
        ann = re.search(r"##\s*(.+?)\s*$", line)
        current = {
            "name": name,
            "purpose": (ann.group(1) if ann else "").strip(),
            "recipe": [],
        }
        targets.append(current)
    for t in targets:
        t["recipe"] = "\n".join(t["recipe"]).strip()
    return targets


def _make_refs(targets: list[dict], known_paths: list[str]) -> dict[str, list[str]]:
    """Map each make target to the indexed unit paths its recipe invokes
    (plain substring — recipes name scripts verbatim)."""
    refs: dict[str, list[str]] = {}
    for t in targets:
        hits = sorted(p for p in known_paths if p in t["recipe"])
        if hits:
            refs[t["name"]] = hits
    return refs


def _compose_rows(py_units: list[dict], make_unit: dict | None, embed_fn) -> list[tuple]:
    """Compose every FTS row from the extracted units (pure function of the
    units — deterministic, so row-level diffs are meaningful)."""
    scripts = [u for u in py_units if u["kind"] == "script"]
    tests = [u for u in py_units if u["kind"] == "test"]
    dotted_to_rel = {u["dotted"]: u["rel"] for u in scripts}

    # test -> imported script rels; inverse onto script rows (tested_by).
    imports_by_test: dict[str, list[str]] = {}
    tested_by: dict[str, set[str]] = {}
    for t in tests:
        matched = sorted(
            dotted_to_rel[d] for d in t["imports"] if d in dotted_to_rel
        )
        imports_by_test[t["rel"]] = matched
        for rel in matched:
            tested_by.setdefault(rel, set()).add(t["rel"])

    # make wiring: target -> scripts, scripts -> targets.
    known_paths = [u["rel"] for u in py_units]
    make_rows: list[tuple] = []
    make_by_script: dict[str, set[str]] = {}
    targets = make_unit["targets"] if make_unit else []
    refs = _make_refs(targets, known_paths)
    for t in targets:
        for rel in refs.get(t["name"], []):
            make_by_script.setdefault(rel, set()).add(t["name"])
        purpose = t["purpose"] or t["recipe"].split("\n")[0][:120]
        content = (
            f"purpose: {purpose}\n"
            f"recipe:\n{t['recipe'][:_RECIPE_CAP]}\n"
            f"scripts: {' '.join(refs.get(t['name'], []))}"
        )
        make_rows.append(_row(
            title=f"make {t['name']}", kind="make", rel_path=t["name"],
            area="make", purpose=purpose, content=content, embed_fn=embed_fn,
        ))

    rows = list(make_rows)
    for u in scripts:
        rows.append(_row(
            title=u["rel"], kind="script", rel_path=u["rel"], area=u["area"],
            purpose=u["purpose"], content=_module_content(
                u,
                make_targets=sorted(make_by_script.get(u["rel"], [])),
                tests=sorted(tested_by.get(u["rel"], [])),
            ),
            embed_fn=embed_fn,
        ))
    for u in tests:
        rows.append(_row(
            title=u["rel"], kind="test", rel_path=u["rel"], area=u["area"],
            purpose=u["purpose"], content=_module_content(
                u,
                imports=sorted(
                    d for d in u["imports"] if d in dotted_to_rel
                ),
            ),
            embed_fn=embed_fn,
        ))
    return rows


def _module_content(u: dict, *, make_targets: list[str] | None = None,
                    tests: list[str] | None = None,
                    imports: list[str] | None = None) -> str:
    """Labeled content block for a py row: purpose/details/cli/defs plus the
    cross-file wiring (make targets, tested_by / imports)."""
    parts = [f"purpose: {u['purpose']}"]
    if u["details"]:
        parts.append(f"details: {u['details'][:_DETAILS_CAP]}")
    if u["cli"]:
        parts.append(f"cli: {' '.join(u['cli'])}")
    if u["subcommands"]:
        parts.append(f"subcommands: {' '.join(u['subcommands'])}")
    if u["defs"]:
        shown = ", ".join(u["defs"][:_DEFS_CAP])
        if len(u["defs"]) > _DEFS_CAP:
            shown += f" (+{len(u['defs']) - _DEFS_CAP} more)"
        parts.append(f"defs: {shown}")
    if imports is not None and imports:
        parts.append(f"imports: {' '.join(imports)}")
    if make_targets:
        parts.append(f"make: {' '.join(make_targets)}")
    if tests:
        parts.append(f"tested_by: {' '.join(tests)}")
    return "\n".join(parts)


def _row(*, title: str, kind: str, rel_path: str, area: str,
         purpose: str, content: str, embed_fn) -> tuple:
    """One FTS row tuple, embedding via the doc_search helper (title +
    purpose + capped content as the vector basis — the purpose paragraph
    dominates, which is exactly the intent signal this index exists for)."""
    return (
        title, kind, rel_path, area, purpose, content,
        rds._embedding_json(embed_fn, title, purpose, content),
    )


def _extract_make_unit(makefile: Path | None) -> dict | None:
    try:
        text = Path(makefile or MAKEFILE).read_text(encoding="utf-8", errors="replace")
        mtime = Path(makefile or MAKEFILE).stat().st_mtime
    except OSError:
        return None
    return {
        "rel": "Makefile",
        "mtime": mtime,
        "hash": hashlib.blake2b(
            text.encode("utf-8", errors="replace"), digest_size=8
        ).hexdigest(),
        "targets": _parse_makefile(text),
    }


def _stamp_model(conn: sqlite3.Connection, model_label: str, dims: int) -> None:
    """Record the embedding model + dims in script_search_info (apply only)."""
    conn.execute(SCRIPT_SEARCH_INFO_DDL)
    conn.executemany(
        "INSERT OR REPLACE INTO script_search_info (key, value) VALUES (?, ?)",
        [("embed_model", model_label), ("embed_dims", str(dims))],
    )


def _backup_last_good_index(db_path: Path) -> None:
    """Last-good-state recovery copy into gitignored db-backup/ after a
    successful FULL rewrite (same semantics as rebuild_doc_search; the
    single-file copier is imported, not forked). Best-effort. Index only:
    the embed cache rides in the shared embed store, backed up centrally
    (see rebuild_doc_search._backup_last_good_index).

    Completeness guard: never back up an EMPTY index (a freshly-migrated
    or truncated build must not displace the last-good archive). The real
    historical leak was test-fixture rebuilds writing this backup via the
    un-redirected module BACKUP_DIR — fixed at the source with an autouse
    BACKUP_DIR isolation fixture in test_rebuild_script_search."""
    try:
        conn = connect(db_path, read_only=True)
        try:
            rows = conn.execute(
                "SELECT COUNT(*) FROM script_search").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        rows = 0
    if not rows:
        print(f"WARNING: {db_path.name} empty ({rows} rows) — last-good "
              "backup skipped (recovery point kept; rebuild continues)",
              file=sys.stderr)
        return
    dests = [
        (db_path, Path(BACKUP_DIR) / "script_search_backup.db"),
    ]
    try:
        Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    except OSError:
        print(f"WARNING: cannot create backup dir {BACKUP_DIR} "
              "(recovery point skipped; rebuild continues)", file=sys.stderr)
        return
    for src, dest in dests:
        if src.exists() and not rds._backup_file(src, dest):
            print(f"WARNING: could not back up {src.name} to {dest} "
                  "(recovery point skipped; rebuild continues)", file=sys.stderr)


def _migrate_schema(conn: sqlite3.Connection) -> bool:
    """Drop a stale script_search so the new DDL applies (FTS5 can't ALTER
    TABLE ADD COLUMN; the rebuild repopulates anyway). True if dropped."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='script_search'"
    ).fetchone()
    if not row:
        return False
    if all(col in row[0] for col in _SCRIPT_SEARCH_COLUMNS):
        return False
    conn.execute("DROP TABLE script_search")
    return True


def _collect_units(helpers_root, tests_root, app_py, makefile):
    """Extract every unit: py files + the Makefile. Returns (py_units,
    make_unit, units_meta) where units_meta keys the --check/incremental
    fingerprint."""
    py_units = []
    for rel, path, kind in _iter_py_units(helpers_root, tests_root, app_py):
        unit = _extract_py_unit(path, rel, kind)
        if unit is not None:
            py_units.append(unit)
    make_unit = _extract_make_unit(makefile)
    units_meta = {u["rel"]: (u["mtime"], u["hash"]) for u in py_units}
    if make_unit is not None:
        units_meta[make_unit["rel"]] = (make_unit["mtime"], make_unit["hash"])
    return py_units, make_unit, units_meta


def _unit_bases(helpers_root, tests_root, app_py, makefile) -> list[Path]:
    """Candidate repo roots for resolving a stored unit path ('helpers/x.py'
    etc.) back to an abs path — the PARENTS of the four corpus roots, so
    monkeypatched tmp trees resolve like the live tree."""
    bases = []
    for p in (helpers_root or HELPERS_ROOT, tests_root or TESTS_ROOT,
              app_py or APP_PY, makefile or MAKEFILE):
        base = Path(p).parent
        if base not in bases:
            bases.append(base)
    return bases


def _stored_meta(conn: sqlite3.Connection) -> dict[str, tuple[float, str]]:
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT unit_path, mtime, content_hash FROM script_search_meta")}


def _write_full(conn: sqlite3.Connection, all_rows: list[tuple],
                units_meta: dict, model_label: str | None, embed_dims: int) -> bool:
    """Full rewrite (the convergence pass). Returns content_changed — the
    zero-churn stat (the maint_full_zero_churn lesson); tuple() each stored
    row because sqlite3.Row never == a plain tuple."""
    from collections import Counter

    stored = [
        tuple(r) for r in conn.execute(
            f"SELECT {_ROW_COLS} FROM script_search"  # noqa: S608  # interpolates the fixed column-list constant only
        )
    ]
    content_changed = Counter(stored) != Counter(all_rows)
    with conn:
        conn.execute("DELETE FROM script_search")
        conn.executemany(
            f"INSERT INTO script_search ({_ROW_COLS}) "  # noqa: S608  # fixed column list
            f"VALUES (?, ?, ?, ?, ?, ?, ?)",
            all_rows,
        )
        conn.execute("DELETE FROM script_search_meta")
        conn.executemany(
            "INSERT OR REPLACE INTO script_search_meta "
            "(unit_path, mtime, content_hash) VALUES (?, ?, ?)",
            [(u, m, h) for u, (m, h) in sorted(units_meta.items())],
        )
        if model_label is not None:
            _stamp_model(conn, model_label, embed_dims)
    return content_changed


def _write_incremental(conn: sqlite3.Connection, all_rows: list[tuple],
                       units_meta: dict, stored_meta: dict,
                       model_label: str | None, embed_dims: int) -> tuple[int, int]:
    """Row-keyed diff write: only titles whose tuple moved get
    DELETE+INSERT; meta is refreshed for changed units and GC'd for
    vanished ones. Returns (upserts, deletes)."""
    stored_by_title: dict[str, tuple] = {
        r[0]: tuple(r) for r in conn.execute(
            f"SELECT {_ROW_COLS} FROM script_search"  # noqa: S608  # interpolates the fixed column-list constant only
        )
    }
    new_by_title = {r[0]: r for r in all_rows}
    to_delete = [t for t in stored_by_title if t not in new_by_title]
    to_upsert = [
        t for t, row in new_by_title.items()
        if stored_by_title.get(t) != row
    ]
    changed_units = [
        u for u in units_meta
        if u not in stored_meta or stored_meta[u] != units_meta[u]
    ]
    with conn:
        for t in to_delete:
            conn.execute("DELETE FROM script_search WHERE title = ?", (t,))
        for t in to_upsert:
            conn.execute("DELETE FROM script_search WHERE title = ?", (t,))
            conn.executemany(
                f"INSERT INTO script_search ({_ROW_COLS}) "  # noqa: S608  # fixed column list
                f"VALUES (?, ?, ?, ?, ?, ?, ?)",
                [new_by_title[t]],
            )
        for u in stored_meta:
            if u not in units_meta:
                conn.execute("DELETE FROM script_search_meta WHERE unit_path = ?", (u,))
        conn.executemany(
            "INSERT OR REPLACE INTO script_search_meta "
            "(unit_path, mtime, content_hash) VALUES (?, ?, ?)",
            [(u, *units_meta[u]) for u in changed_units],
        )
        if (to_upsert or to_delete) and model_label is not None:
            _stamp_model(conn, model_label, embed_dims)
    return len(to_upsert), len(to_delete)


def rebuild(db_path: Path | None = None, write: bool = True, incremental: bool = False,
            embed_fn=None, helpers_root: Path | None = None,
            tests_root: Path | None = None, app_py: Path | None = None,
            makefile: Path | None = None) -> dict:
    """Rebuild the script_search FTS index. Returns a stats dict."""
    db_path = Path(db_path) if db_path is not None else SCRIPT_DB
    conn = connect_script_db(db_path)
    stats: dict = {}
    try:
        migrated = _migrate_schema(conn)
        conn.execute(SCRIPT_SEARCH_DDL)
        conn.execute(SCRIPT_SEARCH_META_DDL)
        conn.execute(SCRIPT_SEARCH_INFO_DDL)
        # Resolve the embedder once; internally-resolved embedders get the
        # shared (sha256, model) sidecar cache (Attached as <sidecar>_vec.db —
        # shared text populations with the other indexers are free cache hits).
        embed_dims = rds._PSEUDO_DIMS
        model_label: str | None = None
        if embed_fn is None:
            embed_fn, embed_dims, model_label = rds.resolve_embedder()
            stats["embed_model"] = model_label
            if model_label != f"dry-run-v{rds._PSEUDO_DIMS}":
                embed_fn = CachedEmbed(embed_fn, model_label, conn, source="script")

        py_units, make_unit, units_meta = _collect_units(
            helpers_root, tests_root, app_py, makefile
        )
        all_rows = _compose_rows(py_units, make_unit, embed_fn)

        if isinstance(embed_fn, CachedEmbed):
            stats["embed_cache_hits"] = embed_fn.hits
            stats["embed_cache_misses"] = embed_fn.misses
            if embed_fn.dirty:
                # Commit cache rows NOW (the --check pre-warm lesson).
                conn.commit()

        stats["total_units"] = len(units_meta)
        stats["total_rows"] = len(all_rows)
        stats["embedded"] = sum(1 for r in all_rows if r[6])
        stats["migrated"] = migrated

        # Freshness verdict: unit-level diff of corpus vs stored meta
        # (hash-exact; every content change flows through some unit's text,
        # including the cross-file inputs of script rows).
        stored_meta = _stored_meta(conn)
        stale_new = sorted(u for u in units_meta if u not in stored_meta)
        stale_deleted = sorted(u for u in stored_meta if u not in units_meta)
        stale_changed = sorted(
            u for u in units_meta
            if u in stored_meta and stored_meta[u] != units_meta[u]
        )
        stats["stale_new"] = stale_new
        stats["stale_changed"] = stale_changed
        stats["stale_deleted"] = stale_deleted
        stats["index_stale"] = bool(stale_new or stale_changed or stale_deleted)

        if not write:
            print(
                f"(--check mode: would index {stats['total_units']} units / "
                f"{stats['total_rows']} rows)",
                file=sys.stderr,
            )
            _print_staleness(stats)
            return stats

        if not incremental:
            stats["mode"] = "full"
            stats["content_changed"] = _write_full(
                conn, all_rows, units_meta, model_label, embed_dims
            )
            stats["indexed"] = conn.execute(
                "SELECT COUNT(*) FROM script_search").fetchone()[0]
            _backup_last_good_index(db_path)
            return stats

        # Incremental: rows were recomposed for everyone (cross-file
        # inputs), but WRITES are row-keyed — only titles whose tuple moved
        # get DELETE+INSERT, so a no-change cycle writes nothing.
        stats["mode"] = "incremental"
        stats["upserts"], stats["deletes"] = _write_incremental(
            conn, all_rows, units_meta, stored_meta, model_label, embed_dims
        )
        stats["indexed"] = conn.execute(
            "SELECT COUNT(*) FROM script_search").fetchone()[0]
        return stats
    finally:
        conn.close()


def _print_staleness(stats: dict) -> None:
    """--check verdict: FRESH, or the drift breakdown + remediation
    (mirrors rebuild_doc_search / sync_sector_wikilinks --check shape)."""
    new = stats.get("stale_new", [])
    changed = stats.get("stale_changed", [])
    deleted = stats.get("stale_deleted", [])
    if not (new or changed or deleted):
        print(f"index state: FRESH ({stats.get('total_units', 0)} units unchanged)",
              file=sys.stderr)
        return
    print(
        f"index state: STALE — {len(changed)} changed, {len(new)} new, "
        f"{len(deleted)} deleted",
        file=sys.stderr,
    )
    drift = ([(u, "changed") for u in changed]
             + [(u, "new") for u in new]
             + [(u, "deleted") for u in deleted])
    for u, kind in drift[:10]:
        print(f"  {kind:8s} {u}", file=sys.stderr)
    if len(drift) > 10:
        print(f"  … and {len(drift) - 10} more", file=sys.stderr)
    print("refresh: python3 helpers/maintenance/rebuild_script_search.py", file=sys.stderr)


# --- read-path gates (script_query CLI; an /api endpoint would reuse these) ---

def script_index_ready(conn: sqlite3.Connection) -> bool:
    """True when the script_search table exists (at least one rebuild ran)."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='script_search'"
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _scan_disk_units(bases: list[Path]) -> set[str]:
    """Unit paths visible on disk under the candidate roots ('helpers/x.py',
    'tests/y.py', 'app.py', 'Makefile') — the stale probe's corpus walk
    (mirrors _iter_py_units' rel-path convention)."""
    on_disk: set[str] = set()
    for base in bases:
        for name in ("helpers", "tests"):
            root = base / name
            if not root.is_dir():
                continue
            for p in root.rglob("*.py"):
                if p.name != "__init__.py":
                    on_disk.add(f"{name}/{p.relative_to(root).as_posix()}")
        if (base / "app.py").is_file():
            on_disk.add("app.py")
        if (base / "Makefile").is_file():
            on_disk.add("Makefile")
    return on_disk


def _units_current(meta: dict[str, float], on_disk: set[str],
                   bases: list[Path]) -> bool:
    """True when every on-disk unit's mtime matches the stored meta."""
    for rel in on_disk:
        prev = meta.get(rel)
        if prev is None:
            return False
        abs_path = next((b / rel for b in bases if (b / rel).exists()), None)
        if abs_path is None or abs_path.stat().st_mtime != prev:
            return False
    return True


def script_index_stale(conn: sqlite3.Connection, *,
                       helpers_root: Path | None = None,
                       tests_root: Path | None = None,
                       app_py: Path | None = None,
                       makefile: Path | None = None) -> bool:
    """True when the corpus differs from script_search_meta (unit set or
    mtimes) — the cheap read-path staleness probe (~190 stats). Any error
    counts as stale (safe side)."""
    try:
        meta = {r[0]: r[1] for r in conn.execute(
            "SELECT unit_path, mtime FROM script_search_meta")}
    except sqlite3.Error:
        return True
    if not meta:
        return True
    bases = _unit_bases(helpers_root, tests_root, app_py, makefile)
    on_disk = _scan_disk_units(bases)
    if meta.keys() - on_disk:
        return True
    return not _units_current(meta, on_disk, bases)


# --- query core (shared by helpers/misc/script_query; future /api/scripts) ---

def _script_stored_embed_dims(conn: sqlite3.Connection) -> int | None:
    """Dims of the first stored script_search embedding, or None when empty.

    Same contract as rebuild_doc_search.stored_embed_dims — duplicated (not
    imported) because that helper hardcodes the doc_search table name; a
    mismatch here means the cosine leg must degrade to BM25-only."""
    try:
        row = conn.execute(
            "SELECT embedding FROM script_search "
            "WHERE embedding IS NOT NULL AND embedding != '' LIMIT 1"
        ).fetchone()
    except Exception:  # noqa: S110  # missing table / corrupt index -> None
        return None
    if not row or not row[0]:
        return None
    try:
        vec = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return len(vec) if isinstance(vec, list) and vec else None


def _cosine_leg(conn: sqlite3.Connection, q: str) -> tuple[list[tuple[int, float]], dict[int, float]]:
    """Cosine ranking: (scored [(rowid, sim)] sorted desc, sims map).

    ([], {}) when the embedder is unavailable or the stored dims mismatch —
    the BM25 leg then carries the whole ranking (same degradation contract
    as rebuild_doc_search.search_docs)."""
    try:
        embed_q, _dims = rds.query_embedder()
        q_vec = embed_q(q)
        if _script_stored_embed_dims(conn) != len(q_vec):
            return [], {}
    except Exception:  # noqa: S110  # embedder unavailable -> BM25 only
        return [], {}
    sims: dict[int, float] = {}
    scored: list[tuple[int, float]] = []
    norm_q = sum(x * x for x in q_vec) ** 0.5 or 1.0
    for rid, emb in conn.execute(
        "SELECT rowid, embedding FROM script_search "
        "WHERE embedding IS NOT NULL AND embedding != ''"
    ):
        try:
            vec = json.loads(emb)
        except (TypeError, ValueError):
            continue
        if not isinstance(vec, list) or len(vec) != len(q_vec):
            continue
        norm_v = sum(x * x for x in vec) ** 0.5 or 1.0
        sim = sum(a * b for a, b in zip(q_vec, vec)) / (norm_q * norm_v)
        scored.append((rid, sim))
        sims[rid] = sim
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored, sims


def _rows_by_rid(conn: sqlite3.Connection, kind: str | None,
                 area: str | None) -> dict[int, sqlite3.Row]:
    """rowid -> row map for cosine-only candidates, filtered like the page
    (UNINDEXED kind/area can't take part in MATCH, but a plain Python-side
    predicate is free at this scale)."""

    def _match(r) -> bool:
        return (kind is None or r[2] == kind) and (area is None or r[4] == area)

    return {r[0]: r for r in conn.execute(
        "SELECT rowid, title, kind, rel_path, area, purpose, content "
        "FROM script_search"
    ) if _match(r)}


def _fuse_scores(candidates: list[tuple[int, sqlite3.Row, str]],
                 cos_rank: dict[int, int] | None) -> list[tuple[float, sqlite3.Row, str]]:
    """RRF-fuse (bm25_pos, row, snippet) candidates into sorted hits
    (rds._RRF_K, same formula as search_docs)."""
    worst = len(cos_rank) if cos_rank else 0
    fused = []
    for bm25_pos, row, snippet in candidates:
        if cos_rank is not None:
            rrf = (1.0 / (rds._RRF_K + bm25_pos + 1)) + (
                1.0 / (rds._RRF_K + cos_rank.get(row[0], worst + bm25_pos) + 1)
            )
        else:
            rrf = 1.0 / (rds._RRF_K + bm25_pos + 1)
        fused.append((rrf, row, snippet))
    fused.sort(key=lambda t: t[0], reverse=True)
    return fused


def _fused_hits(conn: sqlite3.Connection, page: list[sqlite3.Row],
                scored: list[tuple[int, float]], cos_rank: dict[int, int] | None,
                *, kind: str | None, area: str | None,
                limit: int, offset: int) -> list[tuple[float, sqlite3.Row, str]]:
    """Final hit page: union the BM25 page with cosine-only candidates
    (content-head snippets — no lexical match to mark), fuse, window by
    offset+limit. The fused head is pool-independent, so pagination stays
    consistent."""
    candidates: list[tuple[int, sqlite3.Row, str]] = [
        (pos, row, row[8]) for pos, row in enumerate(page)
    ]
    if cos_rank is not None:
        page_rids = {row[0] for row in page}
        rows_by_rid = _rows_by_rid(conn, kind, area)
        extra_pos = 0
        for rid, _sim in scored[: limit + offset]:
            if rid in page_rids:
                continue
            row = rows_by_rid.get(rid)
            if row is None:
                continue
            head = " ".join((row[6] or "").split())[:200]
            candidates.append((len(page) + extra_pos, row, head))
            extra_pos += 1
    return _fuse_scores(candidates, cos_rank)[offset: offset + limit]


def _hit_dicts(hits: list[tuple[float, sqlite3.Row, str]],
               sims: dict[int, float]) -> list[dict]:
    """Shape fused hits into result dicts — keys always present (null when
    no cosine leg) so future API surfaces get uniform shapes (the
    TS-contract lesson)."""
    return [
        {
            "path": row[3],
            "title": row[1],
            "kind": row[2],
            "area": row[4],
            "purpose": row[5],
            "snippet": snippet,
            "score": round(rrf, 6),
            "similarity": round(sims[row[0]], 6) if row[0] in sims else None,
        }
        for rrf, row, snippet in hits
    ]


def search_scripts(conn: sqlite3.Connection, q: str, limit: int = 25,
                   offset: int = 0, *, kind: str | None = None,
                   area: str | None = None, hybrid: bool = True) -> dict:
    """Hybrid BM25 + cosine search over script_search. Never raises.

    Same candidate-union + RRF design as rebuild_doc_search.search_docs
    (the vector leg is a co-equal retriever for OR-joined question-shaped
    tokens); differs in filtering on UNINDEXED kind/area columns (cheap at
    ~200 rows) and NO per-file diversification cap — every row already IS
    a distinct script/test/target."""
    expr = rds.fts_match_expr(q)
    if not expr:
        return {"mode": "bm25", "results": []}
    where = "script_search MATCH ?"
    params: list = [expr]
    if kind:
        where += " AND kind = ?"
        params.append(kind)
    if area:
        where += " AND area = ?"
        params.append(area)
    try:
        page = conn.execute(
            f"SELECT rowid, title, kind, rel_path, area, purpose, embedding, rank, "  # noqa: S608  # WHERE is fully parameterized; f-string interpolates fixed column list only
            f"snippet(script_search, 5, '<mark>', '</mark>', ' … ', 16) AS snip "
            f"FROM script_search WHERE {where} "
            f"ORDER BY bm25(script_search, 2.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0) "
            f"LIMIT ?",
            [*params, limit + offset],
        ).fetchall()
    except sqlite3.Error:
        return {"mode": "bm25", "results": []}

    scored, sims = _cosine_leg(conn, q) if hybrid else ([], {})
    cos_rank = {rid: pos for pos, (rid, _s) in enumerate(scored)} if scored else None
    hits = _fused_hits(
        conn, page, scored, cos_rank, kind=kind, area=area,
        limit=limit, offset=offset,
    )
    mode = "hybrid" if cos_rank is not None else "bm25"
    return {"mode": mode, "results": _hit_dicts(hits, sims)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--db", default=str(SCRIPT_DB),
        help="Path to the script_search sidecar (default: memory/script_search.db).",
    )
    p.add_argument(
        "--check", action="store_true",
        help="Dry-run: count units/rows, report index freshness "
             "(changed/new/deleted), no writes. Exits 1 when stale.",
    )
    p.add_argument(
        "--incremental", action="store_true",
        help="Incremental rebuild (row-keyed diff; unchanged rows not rewritten).",
    )
    args = p.parse_args(argv)

    try:
        stats = rebuild(
            Path(args.db), write=not args.check, incremental=args.incremental
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        f"script_search: {stats.get('total_units', 0)} units / "
        f"{stats.get('total_rows', 0)} rows "
        f"({stats.get('embed_model', 'n/a')})",
        file=sys.stderr,
    )
    if not args.check:
        print(f"indexed {stats.get('indexed', 0)} rows", file=sys.stderr)
        if stats.get("migrated"):
            print("(schema migrated: script_search recreated)", file=sys.stderr)
        emb = stats.get("embedded")
        if emb is not None:
            print(f"embedded {emb} rows", file=sys.stderr)
            if "embed_cache_hits" in stats:
                print(
                    f"embed cache: {stats['embed_cache_hits']} hits, "
                    f"{stats['embed_cache_misses']} misses",
                    file=sys.stderr,
                )
        if stats.get("index_stale"):
            print(
                f"index was STALE before this rebuild: "
                f"{len(stats.get('stale_changed', []))} changed, "
                f"{len(stats.get('stale_new', []))} new, "
                f"{len(stats.get('stale_deleted', []))} deleted — now fresh",
                file=sys.stderr,
            )
        return 0
    return 1 if stats.get("index_stale") else 0


if __name__ == "__main__":
    sys.exit(main())
