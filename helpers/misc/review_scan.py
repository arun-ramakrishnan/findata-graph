#!/usr/bin/env python3
"""Native diff-scoped scanner leg for the delegation review.

review_scan_leg (archive/tooling/review_scan_leg.md) S1–S6: runs the repo's own
scanners — the `review` optional-extra (semgrep, bandit, shellcheck-py,
sqlfluff) — over the review range, keeps only findings on changed lines,
drops findings whose cited line carries a house noqa adjudication
(NOQA_MAP), and hands the host one roster. The osv leg reads whole changed
lockfiles through the osv.dev API (no scanner binary — the dependency
class the OpenQodex trial proved was the only real yield, and the one its
changed-line filter hid).

Advisory only — never a `make qa` leg. ruff is deliberately absent: qa's
lint leg owns E,F (house §4 gate-dedup). No secrets leg: gitleaks has no
PyPI path (proposal §7).

Usage:
    review_scan.py                  # top patch
    review_scan.py --stack N        # HEAD~N..HEAD
    review_scan.py --commit <sha>   # one commit (any ref)
    review_scan.py --from X --to Y  # explicit range
    review_scan.py --offline        # skip the semgrep + osv network legs

Run from a checkout at the range head (worktree pattern) — the scanners
read the working tree while the line map comes from the range, so a tree
that does not match the head gets a warning line.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    # house bootstrap — the rule.json reuse imports helpers.misc below
    sys.path.insert(0, str(REPO_ROOT))

VENV_BIN = REPO_ROOT / ".venv" / "bin"
REVIEW_OUT_DIR = REPO_ROOT / "outputs" / "reviews"
SCANNERS = ("bandit", "shellcheck", "sqlfluff", "semgrep", "osv")

# Rule -> the ruff noqa code the arcs adjudicate it with. Each row cites the
# adjudication; a noqa for a DIFFERENT code never drops a finding (T2 pins
# both directions). Rot here silently hides real findings — revisit whenever
# an arc adds a noqa for one of the mapped codes (S608/S310/S4xx/S6xx) —
# house checklist item 1.
NOQA_MAP: dict[str, str] = {
    # f-string SQL over a schema-constant table name with ?-bound values —
    # bake-off classes 1-2; verified again on the KNN and llamacpp ranges.
    "B608": "S608",
    "python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query": "S608",
    "python.lang.security.audit.formatted-sql-query.formatted-sql-query": "S608",
    # loopback-literal URLs in the vendor health probe (qodex trial 3) and
    # the granite sidecar client (gemma adoption arc).
    "B310": "S310",
    "python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected": "S310",
    # subprocess legs — argv-list vs literal forms.
    "B404": "S404",
    "B603": "S603",
    "B607": "S607",
    # asserts in helpers carry a justified S101 (ty narrowing); S108 rows
    # cover the inline env-driven TMPDIR adjudications (never a literal /tmp).
    "B101": "S101",
    "B108": "S108",
}

SEVERITY_ORDER = ("info", "low", "medium", "high", "critical")

_NOQA_RE = re.compile(
    r"#\s*noqa(?::\s*(?P<codes>[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*))?",
    re.IGNORECASE,
)
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Finding:
    scanner: str
    rule: str
    severity: str
    path: str
    line: int | None
    message: str

    def sort_key(self) -> tuple:
        return (
            self.path,
            self.line or 0,
            -SEVERITY_ORDER.index(self.severity) if self.severity in SEVERITY_ORDER else 0,
            self.rule,
        )

    def roster_line(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        first = self.message.splitlines()[0] if self.message else ""
        return f"{self.severity:<8} {self.scanner}:{self.rule}  {where}  {first}"


# ---------------------------------------------------------------- git plumbing


def _git(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # fixed argv, no shell
        [  # noqa: S607  # git from PATH by design
            "git",
            *argv,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # ranges may touch non-UTF-8 files; never crash the leg
        check=False,
    )


def _venv_bin(name: str) -> Path | None:
    """The venv binary, or None (SKIP-if-missing, like markdown_lint.py)."""
    path = VENV_BIN / name
    return path if path.is_file() and os.access(path, os.X_OK) else None


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # resolved absolute path, fixed argv, no shell
        argv,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # ranges may touch non-UTF-8 files; never crash the leg
        check=False,
    )


# ------------------------------------------------------------- range and lines


def _resolve_range(args: argparse.Namespace) -> tuple[str, str]:
    """(base, head). Same semantics as review_selection.py's --stack mapping
    (HEAD~N..HEAD); --commit expands to <sha>^..<sha> exactly like OCR's."""
    if args.commit is not None:
        return f"{args.commit}^", args.commit
    if args.from_ref is not None:
        return args.from_ref, args.to_ref
    stack = 1 if args.stack is None else args.stack
    return f"HEAD~{stack}", "HEAD"


def _changed_lines(base: str, head: str) -> dict[str, set[int]]:
    """New-file line numbers the change touched, per path.

    --no-renames keeps renames as delete+add so the `+++ b/<path>` header is
    always the file's current name. A pure deletion (+c,0) also marks the
    lines either side (c, c+1) — a removed check can carry a finding
    (OpenQodex's deletion rule, adopted)."""
    diff = _git(["diff", "-U0", "--no-renames", base, head])
    if diff.returncode != 0:
        raise SystemExit(f"git diff {base}..{head} failed: {diff.stderr.strip()}")
    out: dict[str, set[int]] = {}
    path: str | None = None
    for ln in diff.stdout.splitlines():
        if ln.startswith("+++ "):
            target = ln[4:]
            path = None if target == "/dev/null" else _strip_prefix(target)
            if path is not None:
                out.setdefault(path, set())
            continue
        if path is None:
            continue
        m = _HUNK_RE.match(ln)
        if m is None:
            continue
        start = int(m.group(1))
        length = int(m.group(2) or "1")
        if length == 0:
            if start >= 1:
                out[path].add(start)
            out[path].add(start + 1)
        else:
            out[path].update(range(start, start + length))
    return {p: lines for p, lines in out.items() if lines}


def _strip_prefix(path: str) -> str:
    return path[2:] if path.startswith(("a/", "b/")) else path


def _rel_of(raw: str, rev: dict[str, str]) -> str:
    """Scanner-reported path -> repo-relative path. Scanners echo back
    exactly what they were handed (the materialized scratch absolute when
    a head-tree was passed), so the reverse map is the primary lookup;
    file:// URIs and a/b- prefixes are the fallbacks."""
    p = raw.removeprefix("file://")
    return rev.get(p) or rev.get(_strip_prefix(p)) or _strip_prefix(p)


def _content_at(head: str, path: str) -> list[str] | None:
    """File content at the range head — NOT the working tree: reviewing a
    historical commit must read its own lines, not today's."""
    proc = _git(["show", f"{head}:{path}"])
    if proc.returncode != 0:
        return None
    return proc.stdout.splitlines()


def _materialize_at(head: str, files: list[str]) -> dict[str, str]:
    """Mirror the RANGE HEAD's content of `files` under a scratch root and
    return {rel_path: abs_scratch_path} (content-less files omitted).

    The scanners can only be pointed at real paths, and the working tree
    diverges from the range the moment HEAD moved or the tree is dirty —
    scanning the tree then produces citations that do not match the gated
    content, and the noqa filter silently misses (the golden-range
    lesson: the KNN commit's fully-adjudicated sites all survived because
    semgrep's working-tree line numbers were read against commit content).
    Materializing the reviewed tree makes scanner citations, the
    changed-line map and the noqa gate the same coordinate system."""
    digest = hashlib.sha256(head.encode()).hexdigest()[:12]
    root = _scratch(f"review_scan_tree_{digest}")
    mapping: dict[str, str] = {}
    for rel in files:
        content = _content_at(head, rel)
        if content is None:
            continue  # deleted / binary / submodule at head — unscannable
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(content) + "\n", encoding="utf-8")
        mapping[rel] = str(dest)
    return mapping


# ------------------------------------------------------------------ noqa gate


def _noqa_codes(line: str) -> set[str] | None:
    """None = no noqa; {"*"} = bare `# noqa` (ruff suppresses everything)."""
    m = _NOQA_RE.search(line)
    if m is None:
        return None
    codes = m.group("codes")
    if not codes:
        return {"*"}
    return {c.strip().upper() for c in codes.split(",")}


_NOQA_SPAN_CAP = 12  # lines; a runaway paren walk stops rather than scanning the file


def _statement_span(content: list[str], line: int) -> range:
    """Lines of the statement CITED at `line` (1-based) — the citation line
    plus any continuation while parentheses stay open.

    semgrep anchors sqlalchemy-execute-raw-query at the `conn.execute(`
    line while the house noqa sits on the f-string argument below (ruff's
    S608 anchors on the string, so that is where the arcs placed it), so a
    same-line gate alone cannot drop the adjudicated sites. Paren balance
    keeps a NEIGHBOURING statement's noqa from leaking in."""
    idx = line - 1
    depth = content[idx].count("(") - content[idx].count(")")
    end = idx
    while depth > 0 and end + 1 < len(content) and (end - idx) < _NOQA_SPAN_CAP:
        end += 1
        depth += content[end].count("(") - content[end].count(")")
    return range(idx, end + 1)


def _adjudicated(rule: str, content: list[str] | None, line: int | None) -> bool:
    wanted = NOQA_MAP.get(rule)
    if wanted is None or content is None or line is None or line > len(content):
        # Unknown rule, or content unavailable (deleted/binary): keep the
        # finding — never drop on ignorance.
        return False
    for i in _statement_span(content, line):
        codes = _noqa_codes(content[i])
        if codes is not None and ("*" in codes or wanted in codes):
            return True
    return False


# ------------------------------------------------------------- severity mapping


def _semgrep_severity(result: dict, rules: dict[str, dict]) -> str:
    for holder in (
        result.get("properties"),
        rules.get(result.get("ruleId", ""), {}).get("properties"),
    ):
        score = (holder or {}).get("security-severity")
        if score:
            try:
                return _score_band(float(score))
            except ValueError:
                pass
    return {
        "error": "high",
        "warning": "medium",
        "note": "low",
        "none": "info",
    }.get(result.get("level", ""), "medium")


def _score_band(score: float) -> str:
    if score >= 9:
        return "critical"
    if score >= 7:
        return "high"
    if score >= 4:
        return "medium"
    return "low" if score > 0 else "info"


def _bandit_severity(issue: dict) -> str:
    return {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}.get(
        issue.get("issue_severity", "").upper(), "medium"
    )


def _shellcheck_severity(level: str) -> str:
    return {
        "error": "high",
        "warning": "medium",
        "info": "low",
        "style": "info",
    }.get(level, "medium")


def _osv_severity(vuln: dict) -> str:
    for entry in vuln.get("severity", []):
        try:
            return _score_band(float(entry.get("score", "0")))
        except TypeError, ValueError:
            continue
    return "medium"


# -------------------------------------------------------------------- the legs


# House per-file adjudications (§3.7 inverted): a code the house ruff
# config ignores for a path is config-adjudicated there — pyproject
# [tool.ruff.lint.per-file-ignores], ruff code -> bandit equivalent.
# "tests/**: S101 asserts are the contract; S311 — seeded random."
HOUSE_PER_FILE_IGNORES: tuple[tuple[str, set[str]], ...] = (
    ("tests/", {"B101", "B311", "B404", "B603"}),
    ("doc/templates/test_module.py", {"B101"}),
)


def _config_adjudicated(rule: str, path: str) -> bool:
    return any(
        path.startswith(prefix) and rule in codes for prefix, codes in HOUSE_PER_FILE_IGNORES
    )


def _leg_bandit(files: list[str], changed: dict[str, set[int]], head: str, tree=None):
    binary = _venv_bin("bandit")
    if binary is None:
        return [], "bandit: not installed (uv sync --extra review)"
    tree = tree or {f: f for f in files}
    if not tree:
        return [], "bandit: no matching files"
    rev = {a: r for r, a in tree.items()}
    proc = _run([str(binary), "-f", "json", *tree.values()])
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return [], f"bandit: failed (unparseable output, rc={proc.returncode})"
    findings = []
    for issue in payload.get("results", []):
        path = _rel_of(issue.get("filename", ""), rev)
        line = int(str(issue.get("line_number", "0")).split("-")[0])
        if path not in changed or line not in changed[path]:
            continue
        if _config_adjudicated(issue["test_id"], path):
            continue
        if _adjudicated(issue["test_id"], _content_at(head, path), line):
            continue
        findings.append(
            Finding(
                scanner="bandit",
                rule=issue["test_id"],
                severity=_bandit_severity(issue),
                path=path,
                line=line,
                message=issue.get("issue_text", ""),
            )
        )
    return findings, f"bandit: ran ({len(findings)} kept on changed lines)"


def _leg_shellcheck(files: list[str], changed: dict[str, set[int]], head: str, tree=None):
    binary = _venv_bin("shellcheck")
    if binary is None:
        return [], "shellcheck: not installed (uv sync --extra review)"
    tree = tree or {f: f for f in files}
    if not tree:
        return [], "shellcheck: no matching files"
    rev = {a: r for r, a in tree.items()}
    proc = _run([str(binary), "--format=json1", *tree.values()])
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return [], f"shellcheck: failed (unparseable output, rc={proc.returncode})"
    findings = []
    for c in payload.get("comments", []):
        path = _rel_of(c.get("file", ""), rev)
        line = c.get("line")
        if path not in changed or line not in changed[path]:
            continue
        if _adjudicated(c.get("code", ""), _content_at(head, path), line):
            continue
        findings.append(
            Finding(
                scanner="shellcheck",
                rule=c.get("code", ""),
                severity=_shellcheck_severity(c.get("level", "")),
                path=path,
                line=line,
                message=c.get("message", ""),
            )
        )
    return findings, f"shellcheck: ran ({len(findings)} kept on changed lines)"


def _sql_engine(path: str) -> str | None:
    """sqlfluff dialect for a .sql path — the schema/ engine directory IS
    the dialect selector (schema_ddl_review_surface §3). Everything else
    keeps the --dialect default."""
    if path.startswith("schema/sqlite/"):
        return "sqlite"
    if path.startswith("schema/duckdb/"):
        return "duckdb"
    return None


def _leg_sqlfluff(files: list[str], changed: dict[str, set[int]], dialect: str, tree=None):
    binary = _venv_bin("sqlfluff")
    if binary is None:
        return [], "sqlfluff: not installed (uv sync --extra review)"
    tree = tree or {f: f for f in files}
    if not tree:
        return [], "sqlfluff: no matching files"
    rev = {a: r for r, a in tree.items()}
    findings = []
    # one sqlfluff invocation per engine group: schema/sqlite/** wants the
    # sqlite dialect, schema/duckdb/** duckdb, the rest the --dialect default
    groups: dict[str, list[str]] = {}
    for rel, abs_p in tree.items():
        groups.setdefault(_sql_engine(rel) or dialect, []).append(abs_p)
    for group_dialect, abs_paths in sorted(groups.items()):
        proc = _run([str(binary), "lint", "--dialect", group_dialect, "--format", "json", *abs_paths])
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return [], f"sqlfluff: failed (unparseable output, rc={proc.returncode})"
        for entry in payload if isinstance(payload, list) else []:
            path = _rel_of(entry.get("filepath", ""), rev)
            for v in entry.get("violations", []):
                # sqlfluff 4.x emits start_line_no; older/other formatters
                # used line_no. Reading only line_no returned None for every
                # violation and the changed-lines check silently dropped
                # them all — the golden run on c77fe5e0f reported "0 kept"
                # while the raw file had 298. Never trust a clean verdict
                # without this kind of raw-vs-kept crosscheck.
                line = v.get("start_line_no") or v.get("line_no")
                if path not in changed or line not in changed[path]:
                    continue
                rule = v.get("code", "")
                if rule == "PRS" and _extension_syntax(line, _content_at("HEAD", path) if path in tree else None):
                    # documented skip (schema_ddl_review_surface §4 S5): fts5
                    # UNINDEXED columns and vec0 FLOAT[]/distance_metric are
                    # extension syntax no sqlfluff grammar knows; the DDL is
                    # generated from live catalogs so it cannot carry a noqa
                    continue
                findings.append(
                    Finding(
                        scanner="sqlfluff",
                        rule=rule,
                        severity="low",
                        path=path,
                        line=line,
                        message=v.get("description", ""),
                    )
                )
    return findings, f"sqlfluff: ran ({len(findings)} kept on changed lines)"


def _extension_syntax(line_no: int | None, content: list[str] | None) -> bool:
    """True when the cited line sits inside a `USING fts5(...)`/`USING
    vec0(...)` clause — measured 2026-10-09: sqlfluff's sqlite grammar
    rejects UNINDEXED (fts5) and FLOAT[512]/distance_metric (vec0); the
    DDL is machine-generated so an inline noqa is impossible."""
    if content is None or line_no is None or line_no > len(content):
        return False
    start = max(0, line_no - 4)
    window = "\n".join(content[start:line_no])
    return "USING fts5(" in window or "USING vec0(" in window


def _leg_semgrep(files: list[str], changed: dict[str, set[int]], head: str, tree=None):
    binary = _venv_bin("semgrep")
    if binary is None:
        return [], "semgrep: not installed (uv sync --extra review)"
    tree = tree or {f: f for f in files}
    if not tree:
        return [], "semgrep: no matching files"
    rev = {a: r for r, a in tree.items()}
    with tempfile.TemporaryDirectory(prefix="review_scan_") as tmp:
        report = Path(tmp) / "semgrep.sarif"
        proc = _run(
            [
                str(binary),
                "scan",
                "--metrics=off",
                "--disable-version-check",
                "--config",
                "p/default",
                "--config",
                "p/security-audit",
                "--config",
                "p/secrets",
                "--sarif",
                "--output",
                str(report),
                *tree.values(),
            ]
        )
        try:
            payload = json.loads(report.read_text())
        except OSError, json.JSONDecodeError:
            return [], f"semgrep: failed (no SARIF at {report}, rc={proc.returncode})"
    findings = []
    for run in payload.get("runs", []):
        rules = {r["id"]: r for r in run.get("tool", {}).get("driver", {}).get("rules", [])}
        for result in run.get("results", []):
            loc = result.get("locations", [{}])[0].get("physicalLocation", {})
            path = _rel_of(loc.get("artifactLocation", {}).get("uri", ""), rev)
            line = loc.get("region", {}).get("startLine")
            rule = result.get("ruleId", "")
            if path not in changed or line not in changed[path]:
                continue
            if _adjudicated(rule, _content_at(head, path), line):
                continue
            findings.append(
                Finding(
                    scanner="semgrep",
                    rule=rule,
                    severity=_semgrep_severity(result, rules),
                    path=path,
                    line=line,
                    message=result.get("message", {}).get("text", ""),
                )
            )
    return findings, f"semgrep: ran ({len(findings)} kept on changed lines)"


# ---------------------------------------------------------------- osv lockfiles


def _uv_lock_packages(text: str) -> list[tuple[str, str]]:
    out = []
    for block in text.split("[[package]]")[1:]:
        name = re.search(r'^name = "([^"]+)"', block, re.MULTILINE)
        version = re.search(r'^version = "([^"]+)"', block, re.MULTILINE)
        if name and version:
            out.append((name.group(1), version.group(1)))
    return out


def _npm_lock_packages(text: str) -> list[tuple[str, str]]:
    payload = json.loads(text)
    out = []
    packages = payload.get("packages")
    if isinstance(packages, dict):
        for key, entry in packages.items():
            if not key or not isinstance(entry, dict):
                continue
            name = key.rsplit("node_modules/", 1)[-1]
            version = entry.get("version")
            if name and version:
                out.append((name, version))
    for name, entry in (payload.get("dependencies") or {}).items():
        if isinstance(entry, dict) and entry.get("version"):
            out.append((name, entry["version"]))
    return out


_LOCK_PARSERS = {
    "uv.lock": ("PyPI", _uv_lock_packages),
    "package-lock.json": ("npm", _npm_lock_packages),
}


def _fixed_in(vuln: dict) -> str:
    """First fixed version across the vuln's ranges ("" when unfixed)."""
    for affected in vuln.get("affected", []):
        for rng in affected.get("ranges", []):
            for event in rng.get("events", []):
                if event.get("fixed"):
                    return event["fixed"]
    return ""


def _collect_osv_queries(tree: dict[str, str]) -> tuple[list[dict], list[tuple[str, str, str]]]:
    """(querybatch payload, (lockfile, name, version) keys) from the
    materialized lockfiles. Never raises — unparsable files are skipped."""
    queries: list[dict] = []
    keys: list[tuple[str, str, str]] = []  # (lockfile, name, version)
    for lock, abs_path in tree.items():
        parser = _LOCK_PARSERS.get(Path(lock).name)
        if parser is None:
            continue
        ecosystem, parse = parser
        # The RANGE HEAD's lockfile — the same materialized tree the
        # scanner legs read. Never the working tree and never a literal
        # HEAD ref, or a historical range would report deps it never had.
        abs_path_p = Path(abs_path)
        if not abs_path_p.exists():
            continue
        try:
            packages = parse(abs_path_p.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError, ValueError, OSError:
            continue
        for name, version in packages:
            queries.append({"package": {"name": name, "ecosystem": ecosystem}, "version": version})
            keys.append((lock, name, version))
    return queries, keys


def _osv_finding(lock: str, name: str, version: str, vuln: dict) -> Finding:
    msg = f"{name}@{version}"
    if fixed := _fixed_in(vuln):
        msg += f" — fixed in {fixed}"
    summary = vuln.get("summary") or vuln.get("id", "")
    return Finding(
        scanner="osv",
        rule=vuln.get("id", ""),
        severity=_osv_severity(vuln),
        path=lock,
        line=None,
        message=f"{msg}; {summary}",
    )


def _leg_osv(lockfiles: list[str], tree: dict[str, str] | None = None) -> tuple[list[Finding], str]:
    tree = tree or {f: f for f in lockfiles}
    queries, keys = _collect_osv_queries(tree)
    if not queries:
        return [], "osv: no parseable dependencies in the changed lockfiles"
    req = urllib.request.Request(  # noqa: S310  # fixed https URL, no user data
        "https://api.osv.dev/v1/querybatch",
        data=json.dumps({"queries": queries}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            payload = json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return [], f"osv: unavailable ({type(e).__name__}: {e})"
    findings = [
        _osv_finding(lock, name, version, vuln)
        for (lock, name, version), result in zip(keys, payload.get("results", []))
        for vuln in result.get("vulns", [])
    ]
    # whole-lockfile reporting: the changed-line filter is deliberately NOT
    # applied — a dependency's vulnerable version is never the changed line,
    # which is exactly how the OpenQodex trial's one real finding got hidden.
    return findings, f"osv: ran ({len(queries)} deps queried, {len(findings)} vulnerable)"


# ------------------------------------------------------------------------- main


def _scratch(name: str) -> Path:
    base = Path(os.environ.get("TMPDIR", "/tmp"))  # noqa: S108  # env-driven, never a literal /tmp
    base.mkdir(parents=True, exist_ok=True)
    return base / name


def _validate_range_args(args: argparse.Namespace) -> int | None:
    """Arg-shape validation for the range selectors. Returns an exit code
    when the flags are contradictory, else None (proceed)."""
    chosen = sum(
        (
            args.commit is not None,
            args.from_ref is not None or args.to_ref is not None,
            args.stack is not None,
        )
    )
    if chosen > 1:
        print("specify one of --stack, --commit or --from/--to", file=sys.stderr)
        return 2
    if bool(args.from_ref) != bool(args.to_ref):
        print("--from and --to go together", file=sys.stderr)
        return 2
    return None


def _rule_scope(changed: dict[str, set[int]]) -> set[str]:
    """rule.json include/exclude scoping (the repo's ONE selection
    authority — `review_selection._families`, imported, never re-derived).
    The osv leg deliberately overrides the lockfile excludes downstream:
    whole-lockfile reporting is the trial's CVE lesson (§3.5)."""
    # Fallbacks first: if the import fails, the loop below still calls
    # _rule_excluded — an except branch that only reset the glob lists left
    # the name unbound and crashed the leg (golden run, 03743294b worktree,
    # where review_selection.py does not exist yet).
    include_roots: list[str] = []
    exclude_globs: list[str] = []
    _rule_excluded = lambda _p, _globs: False  # noqa: E731
    try:
        from helpers.misc.review_selection import _families, _rule_excluded

        include_roots, exclude_globs = _families()
    except Exception:  # noqa: BLE001  # missing/corrupt rule.json must not kill the leg
        include_roots, exclude_globs = [], []

    def _rule_included(path: str) -> bool:
        return any(path == r.rstrip("/") or path.startswith(r) for r in include_roots)

    rule_scanned: set[str] = set()
    for p in sorted(changed):
        if _rule_excluded(p, exclude_globs):
            print(f"  - {p} (rule.json excluded)")
        elif include_roots and not _rule_included(p):
            print(f"  - {p} (outside rule.json include)")
        else:
            rule_scanned.add(p)
    return rule_scanned


def _group_files(changed: dict[str, set[int]], rule_scanned: set[str], tree: dict[str, str]):
    """(py, sh, sql, locks) rel-path groups plus their materialized trees."""
    py = sorted(p for p in rule_scanned if p.endswith((".py", ".pyi")) and p in tree)
    sh = sorted(p for p in rule_scanned if p.endswith((".sh", ".bash")) and p in tree)
    sql = sorted(p for p in rule_scanned if p.endswith(".sql") and p in tree)
    locks = sorted(p for p in changed if Path(p).name in _LOCK_PARSERS and p in tree)
    return (
        py,
        sh,
        sql,
        locks,
        {p: tree[p] for p in py},
        {p: tree[p] for p in sh},
        {p: tree[p] for p in sql},
        {p: tree[p] for p in locks},
    )


def _run_enabled_legs(legs, enabled, offline: bool) -> tuple[list[Finding], list[str]]:
    """Run every enabled leg; disabled ones become status lines. Exit 0
    always — a scanner failure is a status line, never a crash."""
    findings: list[Finding] = []
    statuses: list[str] = []
    for name, leg in legs:
        if not enabled(name):
            statuses.append(
                f"{name}: skipped (--offline)"
                if offline and name in ("semgrep", "osv")
                else f"{name}: skipped"
            )
            continue
        found, status = leg()
        findings.extend(found)
        statuses.append(status)
    return findings, statuses


def _print_report(findings: list[Finding], statuses: list[str]) -> None:
    """Stdout roster: per-scanner status lines, then one line per finding
    (or the clean bill). Always exit-0 territory — advisory only."""
    for status in statuses:
        print(f"  {status}")
    if findings:
        findings.sort(key=Finding.sort_key)
        print(f"\n{len(findings)} findings:")
        for f in findings:
            print(f"  {f.roster_line()}")
        worst = max(
            findings,
            key=lambda f: SEVERITY_ORDER.index(f.severity) if f.severity in SEVERITY_ORDER else 0,
        )
        print(f"\nworst severity: {worst.severity} — operator adjudicates; the leg is advisory")
    else:
        print("\nall scanners clean on the changed lines")


def _write_roster(base: str, head: str, findings: list[Finding], statuses: list[str]) -> Path:
    """Durable roster under outputs/reviews/ (digest-keyed per range).

    Review OUTPUT home (operator ruling 2026-10-09: the roster is the
    deliverable, not stdout-ephemera): machine-local durable (survives
    reboots, outside git like every outputs/ report), digest-keyed per
    range so reruns overwrite their own generation.
    """
    digest = hashlib.sha256(f"{base}..{head}".encode()).hexdigest()[:12]
    REVIEW_OUT_DIR.mkdir(parents=True, exist_ok=True)
    md_path = REVIEW_OUT_DIR / f"review_scan_{base[:8]}..{head[:8]}_{digest}.md"
    md_path.write_text(
        "\n".join(
            [
                f"# review-scan {base}..{head}",
                "",
                *(f"  {s}" for s in statuses),
                "",
                *(f"  {f.roster_line()}" for f in sorted(findings, key=Finding.sort_key)),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return md_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stack", type=int, default=None, help="applied stgit patches (default 1)")
    ap.add_argument("--commit", help="review exactly one commit (any ref)")
    ap.add_argument("--from", dest="from_ref", help="range base (with --to)")
    ap.add_argument("--to", dest="to_ref", help="range head (with --from)")
    ap.add_argument("--offline", action="store_true", help="skip the semgrep + osv network legs")
    ap.add_argument(
        "--only", action="append", choices=SCANNERS, help="run only this scanner (repeatable)"
    )
    ap.add_argument(
        "--skip", action="append", choices=SCANNERS, help="skip this scanner (repeatable)"
    )
    ap.add_argument("--dialect", default="ansi", help="sqlfluff SQL dialect (default ansi)")
    ap.add_argument("--json", action="store_true", help="write the findings artifact under $TMPDIR")
    args = ap.parse_args(argv)

    if (rc := _validate_range_args(args)) is not None:
        return rc

    base, head = _resolve_range(args)
    changed = _changed_lines(base, head)
    print(f"native scan {base}..{head}: {len(changed)} changed files")
    if not changed:
        return 0

    head_sha = _git(["rev-parse", head]).stdout.strip()
    cur_sha = _git(["rev-parse", "HEAD"]).stdout.strip()
    if head_sha != cur_sha or bool(_git(["status", "--porcelain"]).stdout.strip()):
        print(
            f"  note: tree differs from {head} — scanning the RANGE HEAD's content (materialized), not the working tree"
        )

    only = set(args.only or [])
    skip = set(args.skip or [])
    if args.offline:
        skip.update(("semgrep", "osv"))

    def enabled(name: str) -> bool:
        return name not in skip and (not only or name in only)

    findings: list[Finding] = []
    statuses: list[str] = []
    if all(not enabled(n) for n in SCANNERS):
        statuses = [f"{n}: skipped" for n in SCANNERS]
    else:
        # One materialization for every leg: scanners, the noqa gate and the
        # osv parser all read the SAME head-content coordinate system (see
        # _materialize_at for why the working tree cannot be trusted here).
        tree = _materialize_at(head, sorted(changed))
        rule_scanned = _rule_scope(changed)
        py, sh, sql, locks, py_tree, sh_tree, sql_tree, lock_tree = _group_files(
            changed, rule_scanned, tree
        )
        legs = (
            ("bandit", lambda: _leg_bandit(py, changed, head, py_tree)),
            ("shellcheck", lambda: _leg_shellcheck(sh, changed, head, sh_tree)),
            ("sqlfluff", lambda: _leg_sqlfluff(sql, changed, args.dialect, sql_tree)),
            ("semgrep", lambda: _leg_semgrep(py, changed, head, py_tree)),
            ("osv", lambda: _leg_osv(locks, lock_tree)),
        )
        findings, statuses = _run_enabled_legs(legs, enabled, args.offline)

    _print_report(findings, statuses)

    md_path = _write_roster(base, head, findings, statuses)
    if args.json:
        digest = hashlib.sha256(f"{base}..{head}".encode()).hexdigest()[:12]
        artifact = REVIEW_OUT_DIR / f"review_scan_{digest}.json"
        artifact.write_text(
            json.dumps(
                {
                    "range": {"base": base, "head": head},
                    "findings": [asdict(f) for f in findings],
                    "statuses": statuses,
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"\nartifact: {artifact}")
    print(f"roster: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
