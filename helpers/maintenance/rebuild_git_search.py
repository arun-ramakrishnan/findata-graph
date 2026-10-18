#!/usr/bin/env python3
"""Rebuild the `git_search` sidecar over the main branch's commit history.

The repo's commit messages are operator-authored WHY/WHAT write-ups
(median ~1.6 KB) recording gotchas, root causes and mechanism decisions in
their landed form; the diffs beside them carry the exact code/vault motion.
No search leg covered them, and per-query git mechanisms are not a
substitute — pickaxe (`git log -G/-S`) was already written off from
operator experience at much smaller history sizes (a full-history sweep
per query), and on THIS repo
patch generation is pathological whenever the parquet textconv filter
engages (a plain `git log -p` over main exceeded 7 minutes). This index
inverts the cost (proposal: doc/improvements/proposals/git_history_search.md):

- one FTS5 row per commit on `main`: sha, date, subject, touched paths
  (display) and a composed content block (message + paths + noise-excluded,
  capped diff text) for the BM25 leg, plus the message-only embedding for
  the cosine leg — diffs stay LEXICAL per the settled fusion doctrine
  (structured/identifier text is bm25's home turf; vector-basis enrichment
  was rejected in the notes trial);
- SHA-SET RECONCILIATION, not a high-water cursor: every refresh walks
  `git rev-list main` (cheap, no extraction) and set-diffs against the
  index — new SHAs are extracted + embedded, SHAs that dropped out are
  tombstoned. stgit amend/fold/refresh rewrites mid-history, so a cursor
  would miss rewrites; the reachable set is the source of truth and a
  stale or corrupted index self-heals on the next apply. Only genuinely
  new SHAs ever embed (embedding is not free — operator ruling);
- the shared (sha256(text), model) embed cache via embed_cache (source
  cohort 'git'), GEMMA-FIRST via the shared llama-server (operator
  2026-10-17 evening, S5 bank in hand: the sectioned gemma arm led
  20/24 vs 18/24 top-1 with the edge entirely in the deep-body tier;
  the box already runs the shared servers and every surface uses the
  served models — the earlier same-day granite ruling was superseded).
  Granite in-process is the fallback; a stamp-mismatched embedder
  REFUSES to write rather than silently mixing vector spaces, and a
  gemma-stamped index refuses fallback-model rebuilds
  (guard_gemma_stamp doctrine — the 2026-10-08 silent un-migration);
- the main checkout ONLY (operator scope ruling same day): the rebuilder
  resolves the primary worktree via the common git dir and refuses to run
  from a linked worktree — those sit behind main by construction, and an
  index built from a behind tree would be silently wrong.

RESIDENCE — own sidecar, same doctrine as script_search:
memory/git_search.db is gitignored via memory/, never snapshotted, never
attached to DuckDB. Derived state; delete it and one warm rebuild restores
everything (embeddings included — the cache is content-addressed).

Usage:
    python3 helpers/maintenance/rebuild_git_search.py            # incremental apply
    python3 helpers/maintenance/rebuild_git_search.py --check    # freshness report
    python3 helpers/maintenance/rebuild_git_search.py --full     # complete rewrite

Extraction pins --no-textconv everywhere: the parquet textconv driver is a
query-time convenience, not an index-time dependency (the >7 min trap).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, UTC
from pathlib import Path

# Repo root: helpers/maintenance/rebuild_git_search.py -> parents[2]. Must be
# on sys.path BEFORE the helpers.* imports below so the script works as a
# subprocess the same way it works under pytest. (House bootstrap.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.vec_codec import load_vec, pack_f32  # noqa: E402  (house bootstrap above)

GIT_SEARCH_DB = _REPO_ROOT / "memory" / "git_search.db"

REF = "main"

# Noise-exclude list (measured 2026-10-17, proposal §2): venv/ is tracked in
# this history (3,456 file-touches, 37.3 MB of patch text) and static/ carries
# 53.8 MB of built frontend assets — together with snapshots/ and the binary
# globs they cut whole-history patch text 165.6 MB → 74.5 MB while keeping the
# signal (findata 42.2 MB, doc 13.9, helpers 8.4, tests 6.5). Bump
# EXCLUDE_VERSION when the list changes: the version rides the per-row
# fingerprint, so the whole corpus re-extracts exactly once.
EXCLUDE_VERSION = 1
DIFF_CAP = 64_000  # per-commit diff text cap (head+tail), recorded in the fp
# One source of truth, two consumers: the pathspec list drives git's own diff
# generation; _excluded() mirrors it for the numstat paths so an excluded
# file's PATH never reaches the files block or the lexical content either.
_EXCLUDE_DIRS = ("venv", "snapshots", "static", "vendor", "bench_data")
_EXCLUDE_SUFFIXES = (".parquet", ".lock", ".gguf")
EXCLUDE_PATHSPECS = (
    ".",
    *(f":(exclude){d}" for d in _EXCLUDE_DIRS),
    *(f":(exclude)*{s}" for s in _EXCLUDE_SUFFIXES),
)


def _excluded(path: str) -> bool:
    return path.split("/")[0] in _EXCLUDE_DIRS or path.endswith(_EXCLUDE_SUFFIXES)


SOURCE_COHORT = "git"
_FILES_DISPLAY_CAP = 30

# Embed BASIS = the message HEAD, not the whole message. Measured 2026-10-17
# (live probe, the bili commit vs generic one-word messages): granite's mean
# pooling dilutes monotonically with message length — the on-topic long
# message scored 0.47 cos vs 0.66 for junk "Improvements" at full length, and
# 0.72 at a 350-char head. Short generic texts sit near the centroid; only a
# concentrated basis keeps specific messages above them. v1 (full message) is
# the one live generation built before this was measured. Bump the version to
# force a whole-corpus re-embed (rows whose basis text changed miss the cache
# exactly once).
EMBED_BASIS_CAP = 400
EMBED_BASIS_VERSION = 2

# BODY CHUNKS (S5, proposal §8 — the notes playbook): the head basis cannot
# see mechanism text past char 400, and the body is the point of this index
# (operator 2026-10-17: feature-history lookback). Past the head, the body is
# packed into ~BODY_CHUNK_CAP sections at line boundaries (no overlap — each
# chunk stays small enough to sit in the non-diluted zone the head probe
# measured); a commit's vector score is max(head, best-chunk). Chunks derive
# purely from the immutable message, so they drift iff the sha set drifts —
# incremental applies re-chunk only touched shas. An UNSTAMPED body basis
# (pre-S5 sidecar) counts as stale: the first S5 apply chunks the whole
# corpus exactly once. Bump BODY_BASIS_VERSION / BODY_CHUNK_CAP together —
# both ride the chunk fingerprint.
BODY_CHUNK_CAP = 600
BODY_BASIS_VERSION = 1
_BODY_TAIL_MIN = 80  # a tail shorter than this is not worth its own vector


def _embed_basis(message: str) -> str:
    """The vector leg's index-side text: the message head. The diff text
    stays out (lexical-only, fusion doctrine) and the tail stays out
    (mean-pooling dilution, measured above) — the tail's coverage is the
    body-chunk table's job."""
    return message[:EMBED_BASIS_CAP]


def _body_chunks(message: str) -> list[str]:
    """The message past the head, packed into ≤BODY_CHUNK_CAP sections at
    line boundaries (greedy fill, no overlap, keepends so the join is the
    exact tail). Deterministic in the message alone."""
    tail = message[EMBED_BASIS_CAP:]
    if len(tail) < _BODY_TAIL_MIN:
        return []
    chunks: list[str] = []
    cur: str = ""
    for line in tail.splitlines(keepends=True):
        if cur and len(cur) + len(line) > BODY_CHUNK_CAP:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        chunks.append(cur)
    return chunks


GIT_SEARCH_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS git_search USING fts5("
    "sha UNINDEXED, "  # 0  full commit sha (key + display)
    "adate UNINDEXED, "  # 1  author date, ISO 'YYYY-MM-DD' (display + --since)
    "subject UNINDEXED, "  # 2  first message line (display)
    "files UNINDEXED, "  # 3  newline-joined touched paths (display + --path)
    "content, "  # 4  message + paths + capped diff (tokenized BM25 body)
    "embedding UNINDEXED, "  # 5  vec_codec blob, message-only vector
    "tokenize = 'porter unicode61'"
    ")"
)
# Reconciliation bookkeeping: one row per indexed sha. fp covers everything
# that CAN drift for a given sha (message never changes for a sha; only the
# exclude-version/cap knobs do) — diff text is content-addressed by the sha
# itself and needs no hash of its own.
GIT_SEARCH_META_DDL = (
    "CREATE TABLE IF NOT EXISTS git_search_meta ("
    " sha TEXT PRIMARY KEY,"
    " adate REAL NOT NULL,"
    " fp TEXT NOT NULL"
    ")"
)
GIT_SEARCH_INFO_DDL = (
    "CREATE TABLE IF NOT EXISTS git_search_info ( key TEXT PRIMARY KEY, value TEXT NOT NULL)"
)
# S5 body chunks: one row per (sha, chunk_idx). embedding is the chunk's
# vector under the SAME model stamp as the head table (single vector space);
# chunk_fp covers the chunk policy inputs (basis version + cap) over the
# chunk text so a policy bump re-chunks exactly once.
GIT_SEARCH_BODY_DDL = (
    "CREATE TABLE IF NOT EXISTS git_search_body ("
    " sha TEXT NOT NULL,"
    " chunk_idx INTEGER NOT NULL,"
    " embedding BLOB NOT NULL,"
    " chunk_fp TEXT NOT NULL,"
    " PRIMARY KEY (sha, chunk_idx)"
    ")"
)

_ROW_COLS = "sha, adate, subject, files, content, embedding"


def connect_git_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the git_search sidecar via the house
    connection helper (standard pragmas: row factory, WAL, busy_timeout)."""
    path = Path(db_path) if db_path is not None else GIT_SEARCH_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    from helpers.core.db import connect as _db_connect

    return _db_connect(path)


# --------------------------------------------------------------------------- #
# git plumbing
# --------------------------------------------------------------------------- #


def _git(root: Path, *args: str) -> str:
    """One read-only git invocation against `root`; stdout as text."""
    git = shutil.which("git")
    if git is None:  # narrows list[str] — ty's run overload rejects Sequence[str | None]
        raise RuntimeError("git executable not found on PATH")
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [git, "-C", str(root), "--no-pager", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def primary_repo_root(from_root: Path | None = None) -> Path:
    """The main checkout this index is allowed to read.

    Resolves the common git dir and refuses anything that is not the
    primary worktree: linked worktrees carry their own (often behind or
    diverged) HEAD, and an index built from one would silently describe a
    tree the operator never landed. RuntimeError names both roots so the
    refusal is actionable.
    """
    base = Path(from_root) if from_root is not None else _REPO_ROOT
    try:
        out = _git(base, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    except (subprocess.CalledProcessError, OSError) as exc:
        raise RuntimeError(f"cannot resolve the git common dir under {base}: {exc}") from exc
    common = Path(out)
    if common.name != ".git":
        raise RuntimeError(f"unexpected common git dir {common} (bare repo?) under {base}")
    primary = common.parent.resolve()
    if primary != base.resolve():
        raise RuntimeError(
            f"git_search indexes the MAIN checkout only: {base} is a linked "
            f"worktree (primary: {primary}). Run the rebuilder from {primary}."
        )
    return base


def _messages(root: Path) -> dict[str, tuple[int, str]]:
    """sha -> (author-date epoch, full message body) for every commit on
    REF, newest first. One cheap pass — no diff generation."""
    raw = _git(root, "log", "--format=%x1f%H %at%n%B", REF)
    out: dict[str, tuple[int, str]] = {}
    for rec in ("\n" + raw).split("\n\x1f")[1:]:
        head, _, body = rec.partition("\n")
        sha, _, adate = head.partition(" ")
        out[sha] = (int(adate), body.rstrip("\n"))
    return out


def _numstats(root: Path) -> dict[str, list[tuple[str, int | None, int | None]]]:
    """sha -> [(path, ins, del)] with None for binary entries. One pass,
    --no-textconv (the parquet textconv driver is the >7 min trap)."""
    raw = _git(root, "log", "--numstat", "--no-textconv", "--format=%x1f%H", REF)
    out: dict[str, list[tuple[str, int | None, int | None]]] = {}
    cur: str | None = None
    for line in raw.splitlines():
        if line.startswith("\x1f"):
            cur = line[1:]
            out[cur] = []
            continue
        if cur is None or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        ins, dele, path = parts
        out[cur].append(
            (path, None if ins == "-" else int(ins), None if dele == "-" else int(dele))
        )
    return out


def _diffs_for(root: Path, shas: list[str]) -> dict[str, str]:
    """sha -> noise-excluded patch text (--no-textconv) for the given
    commits, one `git log -p --no-walk` process for the whole batch. Merge
    commits yield no patch by git's default and map to ''."""
    if not shas:
        return {}
    raw = _git(
        root,
        "log",
        "-p",
        "--no-textconv",
        "--format=%x1f%H",
        "--no-walk=unsorted",
        *shas,
        REF,
        *EXCLUDE_PATHSPECS,
    )
    out: dict[str, str] = {sha: "" for sha in shas}
    cur: str | None = None
    chunks: list[str] = []
    for line in raw.splitlines(keepends=True):
        if line.startswith("\x1f"):
            if cur is not None:
                out[cur] = _cap_diff("".join(chunks))
            cur = line[1:].strip()
            chunks = []
            continue
        if cur is not None:
            chunks.append(line)
    if cur is not None:
        out[cur] = _cap_diff("".join(chunks))
    return out


def _cap_diff(text: str) -> str:
    """Head+tail window over DIFF_CAP with an explicit truncation marker —
    a silently clipped diff and a short diff look identical without it."""
    if len(text) <= DIFF_CAP:
        return text
    head, tail = DIFF_CAP // 2, DIFF_CAP // 2
    marker = "\n… [diff truncated by git_search cap] …\n"
    return text[:head] + marker + text[-tail:]


def _files_block(entries: list[tuple[str, int | None, int | None]]) -> str:
    """Display + lexical block: one touched path per line (capped), with
    the overflow named so a capped row never looks complete. Excluded
    paths (the measured noise set) drop out here as well as from the diff
    text — machine churn must not readable as history."""
    paths = [e[0] for e in entries if not _excluded(e[0])]
    lines = paths[:_FILES_DISPLAY_CAP]
    if len(paths) > _FILES_DISPLAY_CAP:
        lines.append(f"… +{len(paths) - _FILES_DISPLAY_CAP} more files")
    return "\n".join(lines)


def _fp(message: str, files_block: str) -> str:
    """Per-row fingerprint over the drift-capable inputs only. A sha's
    message/paths/diff are immutable; what can drift is the extraction
    policy — the exclude list and the cap — so those lead the hash and a
    retune re-extracts every row exactly once."""
    blob = f"v{EXCLUDE_VERSION}|cap{DIFF_CAP}\n{message}\n{files_block}"
    return hashlib_blake2b(blob)


def hashlib_blake2b(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8", errors="replace"), digest_size=16).hexdigest()


def _iso_date(adate: int) -> str:
    return datetime.fromtimestamp(adate, tz=UTC).date().isoformat()


# --------------------------------------------------------------------------- #
# embedder (granite default — operator ruling 2026-10-17)
# --------------------------------------------------------------------------- #


GIT_EMBEDDER_ENV = "GIT_EMBEDDER"


def _is_gemma_label(label: str) -> bool:
    from helpers.core.gemma_embedder import MODEL_LABEL

    return label == MODEL_LABEL


def _head_embed_text(label: str, subject: str, message: str) -> str:
    """Index-side basis for a commit HEAD, per model. Gemma carries the
    two-sided prefix contract (title/subject + purpose, mirroring the
    script surface's `_gemma_basis` shape — byte-parity with the §8.1
    bank arm); granite/pseudo embed the raw head."""
    if _is_gemma_label(label):
        return f"title: {subject or 'none'} | text: commit message head\n{_embed_basis(message)}"
    return _embed_basis(message)


def _chunk_embed_text(label: str, subject: str, chunk: str) -> str:
    """Index-side basis for one body chunk (same per-model contract)."""
    if _is_gemma_label(label):
        return f"title: {subject or 'none'} | text: commit message body\n{chunk}"
    return chunk


def resolve_git_embedder(already_warned: bool = False) -> tuple:
    """(embed_batch, dims, model_label, warned_now) for the git surface.

    Batch-capable counterpart of rebuild_common.resolve_embedder: the
    cached_batch wrapper needs list[str] -> list[vec]. GEMMA-FIRST
    (operator 2026-10-17: the box already runs the two shared
    llama-servers and every surface should use the served models; the
    §8.1 bank had the gemma sectioned arm ahead 20/24 vs 18/24 top-1,
    the edge sitting entirely in the deep-body tier this index exists
    for). Granite (in-process, sidecar-first per its own selector) is
    the fallback; 64-dim deterministic pseudo last (fixtures/CI — loudly
    warned, stamped as dry-run). GIT_EMBEDDER=auto|gemma|granite forces
    a lane. A gemma-stamped index additionally refuses the fallback at
    the write path (guard_gemma_stamp — the 2026-10-08 un-migration
    class; GIT_EMBEDDER=granite is the explicit un-migration).
    """
    import os

    from helpers.core import gemma_embedder, local_embedder

    choice = os.environ.get(GIT_EMBEDDER_ENV, "auto").strip().lower() or "auto"
    if choice in ("auto", "gemma") and gemma_embedder.available():

        def _gemma_batch(texts: list[str]) -> list[list[float]]:
            return [gemma_embedder.embed_document(t) for t in texts]

        return _gemma_batch, gemma_embedder.DIM, gemma_embedder.MODEL_LABEL, already_warned
    if choice == "gemma" and not already_warned:
        print(
            "WARNING: GIT_EMBEDDER=gemma but the gemma sidecar is down "
            "(make embgemma-server) — falling through.",
            file=sys.stderr,
        )
        already_warned = True
    if choice in ("auto", "granite") and local_embedder.available():
        return (
            local_embedder.embed_documents_parallel,
            local_embedder.DIM,
            local_embedder.MODEL_ID,
            already_warned,
        )
    if not already_warned:
        print(
            "WARNING: no real embedder available — using 64-dim "
            "pseudo-embeddings (hybrid ranking stays lexical-ish). Setup: "
            "helpers/core/local_embedder.py module docstring or the "
            "embgemma-server sidecar.",
            file=sys.stderr,
        )
    from helpers.graph.embeddings import _pseudo_embedding

    dims = 64

    def _batch(texts: list[str]) -> list[list[float]]:
        return [_pseudo_embedding(t, dims) for t in texts]

    return _batch, dims, f"dry-run-v{dims}", True


def _stored_embed_model(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute("SELECT value FROM git_search_info WHERE key = 'embed_model'").fetchone()
    except Exception:  # noqa: BLE001  # missing table on a fresh sidecar -> None
        return None
    return row[0] if row else None


def _stored_embed_dims(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute("SELECT value FROM git_search_info WHERE key = 'embed_dims'").fetchone()
    except Exception:  # noqa: BLE001
        return None
    try:
        return int(row[0]) if row else None
    except TypeError, ValueError:
        return None


def _stored_embed_basis(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute("SELECT value FROM git_search_info WHERE key = 'embed_basis'").fetchone()
    except Exception:  # noqa: BLE001
        return None
    return row[0] if row else None


def _stored_body_basis(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute("SELECT value FROM git_search_info WHERE key = 'body_basis'").fetchone()
    except Exception:  # noqa: BLE001
        return None
    return row[0] if row else None


# --------------------------------------------------------------------------- #
# rebuild core (SHA-set reconciliation)
# --------------------------------------------------------------------------- #


def rebuild(
    db_path: Path | str = GIT_SEARCH_DB,
    write: bool = True,
    incremental: bool = True,
    git_root: Path | None = None,
) -> dict:
    """Reconcile the index against `git rev-list main`. Returns the stats
    dict the shared CLI reports on. `write=False` is the --check verdict
    (no diff extraction, no embedding); `incremental=False` is the --full
    rewrite (every reachable sha re-extracted and re-embedded);
    `git_root` overrides the repo root (fixture/test injection — the
    default resolves the primary checkout, refusing linked worktrees)."""
    root = primary_repo_root(git_root)
    messages = _messages(root)
    numstats = _numstats(root)
    reachable = set(messages)

    conn = connect_git_db(db_path)
    try:
        conn.execute(GIT_SEARCH_DDL)
        conn.execute(GIT_SEARCH_META_DDL)
        conn.execute(GIT_SEARCH_INFO_DDL)
        conn.execute(GIT_SEARCH_BODY_DDL)
        conn.commit()
        meta = {
            r[0]: (r[1], r[2]) for r in conn.execute("SELECT sha, adate, fp FROM git_search_meta")
        }

        ordered = list(messages)  # newest first
        # Drift is a property of the sha/fp sets, NOT of the mode: the
        # shared CLI maps --check to incremental=False, and an
        # incremental-gated reconcile here would report the whole corpus
        # drifted on every check. `incremental` only scopes the embed
        # targets below.
        dropped = sorted(set(meta) - reachable)
        new_shas = [s for s in reversed(ordered) if s not in meta]
        changed = []
        for s in ordered:
            if s in meta:
                _adate, body = messages[s]
                if _fp(body, _files_block(numstats.get(s, []))) != meta[s][1]:
                    changed.append(s)
        changed.reverse()  # oldest first, matching new_shas order

        stats: dict = {
            "total_units": len(reachable),
            "stale_new": list(new_shas),
            "stale_changed": list(changed),
            "stale_deleted": dropped,
        }
        touched = new_shas + changed
        # Body-basis staleness rides the check verdict too: a pre-S5
        # sidecar (no body stamp) or a bumped chunk policy IS pending
        # work — `make search-fresh` check must name it and exit 1.
        prev_body_basis = _stored_body_basis(conn)
        body_stale = prev_body_basis != str(BODY_BASIS_VERSION)
        stats["stale_body_basis"] = body_stale
        stats["index_stale"] = bool(touched or dropped or body_stale)
        if not write:
            _check_verdict(conn, stats, body_stale)
            return stats

        # Embed targets: the touched rows, or the WHOLE corpus when the
        # embed basis version moved (every row's vector text changed —
        # the cache absorbs only texts that are genuinely identical).
        # An UNSTAMPED basis (v1-era index built before the key existed)
        # counts as stale — that generation carried full-message vectors.
        prev_basis = _stored_embed_basis(conn)
        basis_stale = prev_basis != str(EMBED_BASIS_VERSION)
        ordered_oldest_first = list(reversed(ordered))
        embed_shas = ordered_oldest_first if (basis_stale or not incremental) else touched

        # Body-chunk targets: same reconciliation shape, SEPARATE staleness
        # axis — a head-basis bump (cap move) does not re-chunk (chunk texts
        # derive from the immutable message, not the head cap), and a
        # body-basis bump re-chunks without touching head vectors. An
        # unstamped body basis is a pre-S5 sidecar: chunk everything once.
        chunk_shas = ordered_oldest_first if (body_stale or not incremental) else list(touched)
        chunk_plan = [
            (s, i, t) for s in chunk_shas for i, t in enumerate(_body_chunks(messages[s][1]))
        ]

        # Extract diffs + embed only what needs it (embedding is not
        # free — the whole point of reconciliation).
        diffs = _diffs_for(root, touched)
        label, vec_by_sha, chunk_vec_map = _embed_pass(
            conn,
            root,
            messages,
            reachable,
            stats,
            embed_shas,
            chunk_shas,
            chunk_plan,
            basis_stale,
            body_stale,
            incremental,
        )

        _write_rows(
            conn,
            messages,
            numstats,
            diffs,
            dropped,
            touched,
            embed_shas,
            chunk_shas,
            chunk_plan,
            vec_by_sha,
            chunk_vec_map,
        )
        conn.commit()
        stats["indexed"] = len(touched)
        stats["total_rows"] = conn.execute("SELECT COUNT(*) FROM git_search").fetchone()[0]
        stats["embed_model"] = label
        return stats
    finally:
        conn.close()


def _check_verdict(conn: sqlite3.Connection, stats: dict, body_stale: bool) -> None:
    """The `write=False` (--check) verdict: print staleness, close stats.

    Split out of `rebuild` for C901; semantics are the 2026-10-18 shape:
    drift prints via the shared printer (plus a body-basis note when
    heads drift too), a body-basis bump ALONE prints its own
    heads-unchanged line, and the clean case still prints the zero-drift
    verdict (the shared CLI maps --check here and must always say why)."""
    import sys as _sys

    from helpers.maintenance import rebuild_common as rbc

    has_drift = bool(stats["stale_new"] or stats["stale_changed"] or stats["stale_deleted"])
    if has_drift:
        rbc.print_staleness(
            stats,
            count_key="total_units",
            unit="commits",
            refresh_cmd="python3 helpers/maintenance/rebuild_git_search.py",
        )
        if body_stale:
            print(
                "  + body-chunk basis missing/bumped — apply re-chunks the corpus",
                file=_sys.stderr,
            )
    elif body_stale:
        print(
            f"index state: STALE — body-chunk basis (all {stats['total_units']} "
            "commits need re-chunking; heads unchanged)",
            file=_sys.stderr,
        )
        print(
            "refresh: python3 helpers/maintenance/rebuild_git_search.py",
            file=_sys.stderr,
        )
    else:
        rbc.print_staleness(
            stats,
            count_key="total_units",
            unit="commits",
            refresh_cmd="python3 helpers/maintenance/rebuild_git_search.py",
        )
    stats["indexed"] = 0
    stats["total_rows"] = conn.execute("SELECT COUNT(*) FROM git_search").fetchone()[0]


def _embed_pass(
    conn: sqlite3.Connection,
    root: Path,
    messages: dict,
    reachable: set,
    stats: dict,
    embed_shas: list,
    chunk_shas: list,
    chunk_plan: list,
    basis_stale: bool,
    body_stale: bool,
    incremental: bool,
) -> tuple[str, dict, dict]:
    """Resolve the embedder, run the head/chunk batches, stamp the bases.

    Split out of `rebuild` for C901. Returns (label, vec_by_sha,
    chunk_vec_map). The demotion guard and the mixed-vector-space refusal
    keep their exact wording — they are doctrine, not diagnostics."""
    # The embedder resolves BEFORE the texts compose: the basis is
    # per-model (gemma's prefix contract), so label must be known first.
    embed_missing, dims, label, _ = resolve_git_embedder()
    prev_model = _stored_embed_model(conn)
    # Demotion guard for the gemma-adopted state: the follow-availability
    # selector must never silently populate the fallback model over a
    # gemma stamp (the 2026-10-08 un-migration class). GIT_EMBEDDER=
    # granite is the explicit un-migration path.
    from helpers.core import gemma_embedder as _gemma

    _gemma.guard_gemma_stamp(prev_model, label, escape=os.environ.get(GIT_EMBEDDER_ENV))
    if (embed_shas or chunk_plan) and prev_model is not None and prev_model != label:
        raise RuntimeError(
            f"git_search is stamped {prev_model} but the resolved embedder "
            f"is {label} — refusing to mix vector spaces. Adoption cutover: "
            "delete the sidecar (derived state) and rebuild to re-stamp; "
            "un-migration: GIT_EMBEDDER=granite "
            "(guard_gemma_stamp doctrine; the 2026-10-08 silent un-migration)."
        )
    texts = [
        _head_embed_text(label, messages[s][1].split("\n", 1)[0], messages[s][1])
        for s in embed_shas
    ]

    vec_by_sha: dict[str, list[float]] = {}
    chunk_vecs: list[list[float]] = []
    if embed_shas or chunk_plan:
        from helpers.core.embed_cache import cached_embed_batch

        if embed_shas:
            vecs, emb_stats = cached_embed_batch(
                conn, texts, label, embed_missing, source=SOURCE_COHORT
            )
            vec_by_sha = dict(zip(embed_shas, vecs))
            stats["embedded"] = emb_stats["unique_misses"]
            stats["embed_cache_hits"] = emb_stats["hits"]
            stats["embed_cache_misses"] = emb_stats["misses"]
        if chunk_plan:
            chunk_vecs, body_stats = cached_embed_batch(
                conn,
                [
                    _chunk_embed_text(label, messages[s][1].split("\n", 1)[0], t)
                    for s, _i, t in chunk_plan
                ],
                label,
                embed_missing,
                source=SOURCE_COHORT,
            )
            stats["embedded_body"] = body_stats["unique_misses"]
            stats["body_cache_hits"] = body_stats["hits"]
            stats["body_chunks"] = len(chunk_plan)
        conn.execute(
            "INSERT OR REPLACE INTO git_search_info (key, value) VALUES ('embed_model', ?)",
            (label,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO git_search_info (key, value) VALUES ('embed_dims', ?)",
            (str(dims),),
        )

    # The basis stamps must describe the table's CONTENT (the model
    # stamp's own rule): stamp only when this pass covered every
    # reachable row. A partial pass under a moved basis would otherwise
    # brand v1 vectors as v2 and the upgrade would never fire again (hit
    # live: a partial apply stamped v2 over 414 full-basis rows).
    # Deliberately OUTSIDE the pending-embed block: a basis bump over a
    # corpus whose texts all cache-hit (or that yields no chunks at all)
    # still owes the stamp its upgrade.
    if embed_shas and (basis_stale or not incremental or set(embed_shas) >= reachable):
        conn.execute(
            "INSERT OR REPLACE INTO git_search_info (key, value) VALUES ('embed_basis', ?)",
            (str(EMBED_BASIS_VERSION),),
        )
    # Stamp on TARGET coverage, not on chunk texts existing: a corpus of
    # short messages yields zero chunks and is still fully covered (zero
    # chunks IS their chunk set) — gating on chunk_plan would leave such
    # an index permanently "stale".
    if body_stale or not incremental or set(chunk_shas) >= reachable:
        conn.execute(
            "INSERT OR REPLACE INTO git_search_info (key, value) VALUES ('body_basis', ?)",
            (str(BODY_BASIS_VERSION),),
        )
    chunk_vec_map = {(s, i): v for (s, i, _t), v in zip(chunk_plan, chunk_vecs, strict=True)}
    return label, vec_by_sha, chunk_vec_map


def _write_rows(
    conn: sqlite3.Connection,
    messages: dict,
    numstats: dict,
    diffs: dict,
    dropped: list,
    touched: list,
    embed_shas: list,
    chunk_shas: list,
    chunk_plan: list,
    vec_by_sha: dict,
    chunk_vec_map: dict,
) -> None:
    """The write pass: drop, upsert touched rows, basis-only re-embeds,
    body-chunk rewrite. Split out of `rebuild` for C901; row-for-row the
    previous inline tail."""
    for sha in dropped:
        conn.execute("DELETE FROM git_search WHERE sha = ?", (sha,))
        conn.execute("DELETE FROM git_search_meta WHERE sha = ?", (sha,))
        conn.execute("DELETE FROM git_search_body WHERE sha = ?", (sha,))
    for sha in touched:
        adate, body = messages[sha]
        subject = body.split("\n", 1)[0]
        fblock = _files_block(numstats.get(sha, []))
        content = f"{body}\n{fblock}\n{diffs.get(sha, '')}"
        conn.execute("DELETE FROM git_search WHERE sha = ?", (sha,))
        conn.execute(
            f"INSERT INTO git_search ({_ROW_COLS}) VALUES (?, ?, ?, ?, ?, ?)",  # noqa: S608  # constant column list
            (sha, _iso_date(adate), subject, fblock, content, pack_f32(vec_by_sha[sha])),
        )
        conn.execute(
            "INSERT OR REPLACE INTO git_search_meta (sha, adate, fp) VALUES (?, ?, ?)",
            (sha, float(adate), _fp(body, fblock)),
        )
        conn.execute("DELETE FROM git_search_body WHERE sha = ?", (sha,))
    # Basis-only re-embed: rows whose extraction did not change but whose
    # vector text did.
    for sha in embed_shas:
        if sha in touched:
            continue
        conn.execute(
            "UPDATE git_search SET embedding = ? WHERE sha = ?",
            (pack_f32(vec_by_sha[sha]), sha),
        )
    # Body chunks: full rewrite per target sha (touched rows deleted
    # above; body-stale passes overwrite in place via the same DELETE).
    for sha in chunk_shas:
        rows = [
            (
                sha,
                i,
                pack_f32(chunk_vec_map[(sha, i)]),
                hashlib_blake2b(f"v{BODY_BASIS_VERSION}|cap{BODY_CHUNK_CAP}\n{t}"),
            )
            for i, t in enumerate(_body_chunks(messages[sha][1]))
        ]
        conn.execute("DELETE FROM git_search_body WHERE sha = ?", (sha,))
        conn.executemany(
            "INSERT INTO git_search_body (sha, chunk_idx, embedding, chunk_fp) VALUES (?, ?, ?, ?)",
            rows,
        )


# --------------------------------------------------------------------------- #
# query core (shared by helpers/misc/git_query and the search_tui git lane)
# --------------------------------------------------------------------------- #


def git_index_ready(conn: sqlite3.Connection) -> bool:
    """True when the git_search table exists (at least one rebuild ran)."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='git_search'"
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def git_index_stale(conn: sqlite3.Connection, *, root: Path | None = None) -> bool:
    """Cheap read-path probe: the indexed sha SET vs the reachable set.

    Deliberately cheaper than the --check verdict (message pass only,
    ~0.02 s — no numstat, no fp): a query-time warning must not cost
    seconds. Fingerprint drift (an exclude-list retune) is the --check's
    business. Any error counts as stale (safe side)."""
    try:
        meta = {r[0] for r in conn.execute("SELECT sha FROM git_search_meta")}
    except sqlite3.Error:
        return True
    if not meta:
        return True
    try:
        base = primary_repo_root(root)
        reachable = set(_messages(base))
    except Exception:  # noqa: BLE001  # worktree / bare / broken git -> safe side
        return True
    return meta != reachable


def _cosine_query_vector(conn: sqlite3.Connection, q: str, query_vec: object) -> list[float] | None:
    """Resolve the query vector for the cosine leg: a caller-supplied
    QueryVector when usable, else a fresh embed through the stored model's
    contract. None = unavailable or dims mismatch (the BM25 leg then
    carries the ranking). Split out of `_cosine_leg` for C901."""
    from helpers.maintenance import rebuild_common as rbc

    try:
        idx_dims = _stored_embed_dims(conn)
        q_vec: list[float] | None = None
        if query_vec is not None:
            qv = query_vec if isinstance(query_vec, rbc.QueryVector) else None
            q_vec = rbc.check_query_vector(_stored_embed_model(conn), idx_dims, qv)
        if q_vec is None:
            from helpers.core import gemma_embedder

            if _stored_embed_model(conn) == gemma_embedder.MODEL_LABEL:
                # commit prose is search-retrieval, not code — the
                # task prefix is part of the vector contract
                embed_q = lambda query: gemma_embedder.embed_query(query, task="search")  # noqa: E731  # match the local_embedder callable shape
            else:
                from helpers.core import local_embedder

                embed_q = local_embedder.embed_query
            candidate = embed_q(q)
            if idx_dims is not None and idx_dims != len(candidate):
                return None
            q_vec = candidate
        return q_vec
    except Exception:  # noqa: BLE001  # embedder unavailable -> BM25 only
        return None


def _cosine_leg(
    conn: sqlite3.Connection, q: str, query_vec: object = None
) -> tuple[list[tuple[int, float]], dict[int, float], set[int]]:
    """Cosine ranking over head + body-chunk vectors: a row's score is
    max(head cos, best-chunk cos) — the S5 deep-body rule. Returns
    (scored [(rowid, sim)] sorted desc, sims map, rowids whose best sim
    came from a BODY chunk). ([], {}, set()) when the embedder is
    unavailable or the stored dims mismatch — the BM25 leg then carries
    the ranking."""
    q_vec = _cosine_query_vector(conn, q, query_vec)
    if q_vec is None:
        return [], {}, set()
    sims: dict[int, float] = {}
    from_body: set[int] = set()
    norm_q = sum(x * x for x in q_vec) ** 0.5 or 1.0

    def _consider(rid: int, vec: list[float], body: bool) -> None:
        if not vec or len(vec) != len(q_vec):
            return
        norm_v = sum(x * x for x in vec) ** 0.5 or 1.0
        sim = sum(a * b for a, b in zip(q_vec, vec)) / (norm_q * norm_v)
        prev = sims.get(rid)
        if prev is None or sim > prev:
            sims[rid] = sim
            (from_body.add if body else from_body.discard)(rid)

    rid_by_sha: dict[str, int] = {}
    for rid, sha, emb in conn.execute(
        "SELECT rowid, sha, embedding FROM git_search WHERE embedding IS NOT NULL AND embedding != ''"
    ):
        rid_by_sha[sha] = rid
        vec = load_vec(emb)
        if vec is not None:
            _consider(rid, vec, body=False)
    for sha, emb in conn.execute("SELECT sha, embedding FROM git_search_body"):
        rid = rid_by_sha.get(sha)
        if rid is not None:
            vec = load_vec(emb)
            if vec is not None:
                _consider(rid, vec, body=True)
    scored = sorted(sims.items(), key=lambda t: (-t[1], t[0]))
    return scored, sims, from_body


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# A content term this rare (rows matched, whole content incl. diffs) is
# near-unique lexical evidence — the query routes lexical-only (S5 live
# hit: SessionWriter → 2 rows; the identifier router's rationale made
# symmetric). Counted per query, ms-class at this corpus size.
_RARE_LITERAL_ROWS = 3
_ROUTER_STOPWORDS = frozenset(
    {"the", "and", "for", "with", "into", "from", "when", "then", "that", "this"}
)


def _has_rare_literal(conn: sqlite3.Connection, q: str) -> bool:
    """True when any ≥4-char query term matches 1.._RARE_LITERAL_ROWS
    rows — the lexical leg then holds near-unique evidence and cosine
    generics are noise (they bury it under RRF extras). COUNT probes are
    cheap at this corpus size; any sqlite error answers False (hybrid
    stays the default shape)."""
    from helpers.maintenance import rebuild_doc_search as rds

    try:
        terms = re.findall(r'"([^"]+)"', rds.fts_match_expr(q))
        for term in terms:
            if len(term) < 4 or term.lower() in _ROUTER_STOPWORDS:
                continue
            n = conn.execute(
                "SELECT COUNT(*) FROM git_search WHERE git_search MATCH ?",  # noqa: S608  # quoted literal
                (f'"{term}"',),
            ).fetchone()[0]
            if 0 < n <= _RARE_LITERAL_ROWS:
                return True
    except sqlite3.Error:
        return False
    return False


def _where_and_params(since: str | None, path: str | None) -> tuple[str, list]:
    """WHERE suffix for the UNINDEXED display columns (cheap scans at this
    corpus size), with LIKE wildcards escaped — a path query containing %
    must not turn into a truncation scan."""
    clauses: list[str] = []
    params: list = []
    if since:
        clauses.append("adate >= ?")
        params.append(since)
    if path:
        clauses.append(r"files LIKE ? ESCAPE '\'")
        params.append(f"%{_like_escape(path)}%")
    return ("".join(f" AND {c}" for c in clauses), params)


def _rows_by_rid(
    conn: sqlite3.Connection, since: str | None, path: str | None
) -> dict[int, sqlite3.Row]:
    """rowid -> row map for cosine-only candidates, filtered like the page
    (Python-side predicate — free at this scale)."""
    where, params = _where_and_params(since, path)
    rows = conn.execute(
        f"SELECT rowid, sha, adate, subject, files, content FROM git_search WHERE 1=1{where}",  # noqa: S608  # where is built from parameterized clauses only
        params,
    ).fetchall()
    return {r[0]: r for r in rows}


def _fuse_scores(
    candidates: list[tuple[int, sqlite3.Row, str, bool]],
    cos_rank: dict[int, int] | None,
) -> list[tuple[float, sqlite3.Row, str]]:
    """RRF-fuse (bm25_pos, row, snippet, is_extra) candidates (rds._RRF_K,
    the shared formula).

    An EXTRA (a cosine-only row outside the BM25 page) fuses on its cosine
    term ALONE. The S5 live bank caught why: extras were handed synthetic
    bm25 positions, a fabricated second leg that let 0.6-cosine generic
    body chunks outrank the bm25-#1 row on rare-literal queries
    (SessionWriter: bm25 -17.1 vs -9.6 runner-up, yet ranked ~6th fused).
    Real two-leg page evidence must always outrank a synthetic one."""
    from helpers.maintenance import rebuild_doc_search as rds

    worst = len(cos_rank) if cos_rank else 0
    fused = []
    for bm25_pos, row, snippet, is_extra in candidates:
        if cos_rank is None:
            rrf = 1.0 / (rds._RRF_K + bm25_pos + 1)
        elif is_extra:
            rrf = 1.0 / (rds._RRF_K + cos_rank.get(row[0], worst + bm25_pos) + 1)
        else:
            rrf = (1.0 / (rds._RRF_K + bm25_pos + 1)) + (
                1.0 / (rds._RRF_K + cos_rank.get(row[0], worst + bm25_pos) + 1)
            )
        fused.append((rrf, row, snippet))
    fused.sort(key=lambda t: t[0], reverse=True)
    return fused


def _hit_dicts(
    hits: list[tuple[float, sqlite3.Row, str]],
    sims: dict[int, float],
    from_body: set[int] | None = None,
) -> list[dict]:
    """Shape fused hits into result dicts — keys always present (null when
    no cosine leg) so every surface gets a uniform shape. `vector_from`
    names which vector won the row's max ('body' = a deep-body chunk
    outranked the head — the S5 marker)."""
    from_body = from_body or set()
    return [
        {
            "sha": row[1],
            "date": row[2],
            "subject": row[3],
            "files": row[4],
            "snippet": snippet,
            "score": round(rrf, 6),
            "similarity": round(sims[row[0]], 6) if row[0] in sims else None,
            "vector_from": "body" if row[0] in from_body else ("head" if row[0] in sims else None),
        }
        for rrf, row, snippet in hits
    ]


def _vector_hits(
    conn: sqlite3.Connection,
    scored: list[tuple[int, float]],
    sims: dict[int, float],
    since: str | None,
    path: str | None,
    limit: int,
    offset: int,
) -> list[tuple[float, sqlite3.Row, str]]:
    """Vector-only page: cosine-ranked rows, score = similarity (no RRF —
    there is nothing to fuse). A dead cosine leg yields an empty page,
    never a silent BM25 substitution."""
    rows_by_rid = _rows_by_rid(conn, since, path)
    hits: list[tuple[float, sqlite3.Row, str]] = []
    for rid, sim in scored[: limit + offset]:
        row = rows_by_rid.get(rid)
        if row is None:
            continue
        head = " ".join((row[5] or "").split())[:200]
        hits.append((sim, row, head))
    return hits[offset : offset + limit]


def search_commits(
    conn: sqlite3.Connection,
    q: str,
    limit: int = 25,
    offset: int = 0,
    *,
    since: str | None = None,
    path: str | None = None,
    hybrid: bool = True,
    query_vec: object = None,
    vector_only: bool = False,
) -> dict:
    """Hybrid BM25 + cosine search over git_search. Never raises.

    Same candidate-union + RRF design as search_docs/search_scripts. The
    corpus is granite-stamped by ruling, so the default shape is hybrid
    (the message prose wants the vector leg and the diff identifiers want
    BM25 in the same ranking); `--bm25` keeps the lexical shape and
    `vector_only=True` forces the cosine shape. Two ROUTES drop to
    lexical-only (same rationale — when one leg holds near-unique
    evidence, the other's generic mass is noise): IDENTIFIER-SHAPED
    queries (the script-leg router; live hit 2026-10-17:
    `_bili_namespace` — right BM25 page, junk cosine extras, 0.032 vs
    0.017 RRF) and RARE-LITERAL queries (a content term matching
    ≤ _RARE_LITERAL_ROWS commits — live hit same day: `SessionWriter`
    matches 2 commits, bm25 -17.1 vs -9.6 runner-up, yet S5's chunk-dense
    cosine generics buried the truth under synthetic extra positions).
    `since` is a cutoff ISO date (YYYY-MM-DD), `path` a touched-path
    substring (LIKE-escaped)."""
    from helpers.maintenance import rebuild_doc_search as rds
    from helpers.maintenance import rebuild_script_search as rss

    expr = rds.fts_match_expr(q)
    if not expr:
        return {"mode": "bm25", "results": []}
    routed_lexical = (
        hybrid and not vector_only and (rss.is_identifier_query(q) or _has_rare_literal(conn, q))
    )
    where, params = _where_and_params(since, path)
    try:
        page = conn.execute(
            "SELECT rowid, sha, adate, subject, files, embedding, rank, "  # noqa: S608  # where is parameterized clauses only
            "snippet(git_search, 4, '<mark>', '</mark>', ' … ', 16) AS snip "
            f"FROM git_search WHERE git_search MATCH ?{where} "
            "ORDER BY bm25(git_search) LIMIT ?",
            [expr, *params, limit + offset],
        ).fetchall()
    except sqlite3.Error:
        return {"mode": "bm25", "results": []}

    run_cosine = (hybrid and not routed_lexical) or vector_only
    scored, sims, from_body = _cosine_leg(conn, q, query_vec) if run_cosine else ([], {}, set())
    if vector_only:
        return {
            "mode": "vector",
            "results": _hit_dicts(
                _vector_hits(conn, scored, sims, since, path, limit, offset), sims, from_body
            ),
        }
    candidates: list[tuple[int, sqlite3.Row, str, bool]] = [
        (pos, row, row[7], False) for pos, row in enumerate(page)
    ]
    cos_rank: dict[int, int] | None = None
    if scored:
        cos_rank = {rid: pos for pos, (rid, _s) in enumerate(scored)}
        page_rids = {row[0] for row in page}
        rows_by_rid = _rows_by_rid(conn, since, path)
        # An EXTRA earns its synthetic second leg only by outlying the page:
        # its cosine must beat the best cosine among the lexical candidates.
        # Stronger-than-lexical extras surface (live: person-resolver query,
        # extra cos 0.785 vs page max 0.673, rescued from rank 4 to 2);
        # generic-band extras (≤ page max, the 0.6-cosine chunk noise) stay
        # single-leg and cannot bury confident lexical rows.
        page_max_sim = max((sims.get(row[0], 0.0) for row in page), default=0.0)
        extra_pos = 0
        for rid, _sim in scored[: limit + offset]:
            if rid in page_rids:
                continue
            row = rows_by_rid.get(rid)
            if row is None:
                continue
            head = " ".join((row[5] or "").split())[:200]
            earned = sims.get(rid, 0.0) > page_max_sim
            candidates.append((len(page) + extra_pos, row, head, not earned))
            extra_pos += 1
    hits = _fuse_scores(candidates, cos_rank)[offset : offset + limit]
    mode = "hybrid" if cos_rank is not None else "bm25"
    return {"mode": mode, "results": _hit_dicts(hits, sims, from_body)}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _summary_line(stats: dict) -> str:
    return (
        f"git_search: {stats.get('total_units', 0)} commits on {REF} / "
        f"{stats.get('total_rows', 0)} rows "
        f"({stats.get('embed_model', _stamp_label())})"
    )


def _stamp_label() -> str:
    try:
        conn = connect_git_db(GIT_SEARCH_DB)
        try:
            return _stored_embed_model(conn) or "n/a"
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return "n/a"


def main(argv: list[str] | None = None) -> int:
    from helpers.maintenance import rebuild_common as rbc

    # stamp_table deliberately None: the shared APPLY guard refuses
    # behind/diverged HEADs on the theory that the delta would rewrite
    # shared content backward — unsound for a HISTORY index. This index's
    # corpus IS the reachable set of main; reconciliation is exact for any
    # HEAD, history rewrites are the normal case it exists to absorb
    # (three live rewrites on day one), and worktree tramples are already
    # impossible via primary_repo_root's refusal. allow_stale_noop keeps
    # the flag accepted (no-op) so the search-fresh loop can pass
    # ARGS="--allow-stale" uniformly to all five rebuilders.
    return rbc.run_rebuild_cli(
        argv,
        rbc.RebuildCliSpec(
            description=__doc__.split("\n\n")[0],
            default_db=str(GIT_SEARCH_DB),
            db_help="Path to the git_search sidecar (default: memory/git_search.db).",
            check_help="Dry-run: count reachable commits, report index freshness "
            "(new/changed/deleted shas), no writes. Exits 1 when stale.",
            incremental_help="Incremental apply (SHA-set reconciliation; only new/changed shas extracted).",
            full_help="Full rewrite: every reachable sha re-extracted and re-embedded.",
            rebuild_fn=rebuild,
            summary=_summary_line,
            migrated_msg="(schema migrated: git_search recreated)",
            allow_stale_noop=True,
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
