#!/usr/bin/env python3
"""Shared gate-run artifact retention for the non-runner gates.

```text
intent        gate_retain     the one place that decides WHERE a run's
                              machine-readable artifacts live and how a
                              report names them
cell          helpers/misc/gate_retain.py
role          helper
portability   pure stdlib + git CLI; no repo imports
```

Three gate lanes never ride `tests/run_gate_report.py`:

| lane | writer | what it produced before this module |
|---|---|---|
| `make perf` | `tests/run_perf_benchmarks.py` | one stderr tail per red leg |
| `make maint` | `helpers/maintenance/maint.py` | one 60-line tail per red step |
| integrity / verify | their own executables | their own markdown report |

The runner retained BOTH the junit AND `<gate>.<label>.log` (full stdout)
into `outputs/.artifacts/<gate>/<run-id>/`, and wrote `**Artifacts:**` into
the report meta line. The three lanes above retained nothing, so
`gate_query output --run N --leg L` could not reach a single one of their
legs (perf: 173/173 runs `artifact_state='not_collected'`; measured
2026-11-09). This module gives them the same layout, the same meta
contract, and the same "which dir did I just write" answer.

Layout contract (read by `helpers/misc/gate_query._artifact_base`):
the value stamped in `**Artifacts:**` is RELATIVE to the outputs root —
`.artifacts/<gate>/<run-id>` — on both a main checkout and a linked
worktree (where the outputs root is `<main>/outputs/wt/<name>/`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

#: Per-step stdout logs land here under `<gate>.<label>.log` — the exact
#: name `gate_query output --leg` derives, so the writer and the reader
#: cannot drift apart.
STEP_LOG_SUFFIX = ".log"
#: Run directory name shape. Kept identical to `tests/run_gate_report.py`
#: so one `.artifacts/<gate>/` tree holds every gate's runs.
RUN_ID_FMT = "%Y%m%dT%H%M%S-%f"

_UNSAFE_LABEL = re.compile(r"[/\\\x00]")


def artifacts_root_for(report_path: Path) -> Path:
    """`<dir of the report>/.artifacts` — the tree gate_query resolves
    `**Artifacts:**` against.

    Key off the REPORT FILE, never a worktree guess: a lane that writes
    `<tree>/outputs/x_report.md` must retain into `<tree>/outputs/.artifacts`
    or the index finds the block and then cannot find its artifacts.
    """
    return report_path.parent / ".artifacts"


def run_id_now() -> str:
    """The run id to file this run's artifacts under."""
    from datetime import datetime

    return datetime.now().strftime(RUN_ID_FMT)


def safe_label(label: str) -> str:
    """Filesystem-safe leg label: separators become `_`.

    Leg labels are benchmark names, check names and verify buckets, so a
    path separator would be a bug — but `gate_query output` derives the
    filename from the leg label recorded in the INDEX. Any sanitization
    here must therefore be mirrored there (`helpers/misc/gate_query`'s
    `_step_log_name`) or the lookup silently misses.
    """
    return _UNSAFE_LABEL.sub("_", label)


def step_log_name(gate: str, label: str) -> str:
    """`<gate>.<label>.log` — the one filename writer and reader agree on."""
    return f"{gate}.{safe_label(label)}{STEP_LOG_SUFFIX}"


def step_log_path(artifacts_root: Path, gate: str, run_id: str, label: str) -> Path:
    """Where one leg's full output is retained."""
    return artifacts_root / gate / run_id / step_log_name(gate, label)


def run_dir(artifacts_root: Path, gate: str, run_id: str) -> Path:
    """Create + return this run's artifact directory."""
    d = artifacts_root / gate / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def artifact_meta_value(base: Path, run_dir_path: Path) -> str:
    """The `**Artifacts:**` value: run dir RELATIVE to `base` (the OUTPUTS
    root, i.e. the parent of `.artifacts`), never the artifacts root itself.

    gate_query resolves the stamped value as `<outputs root>/<value>` and
    refuses anything that escapes the root, so an absolute path reads as
    'invalid' and a `..` path as an escape. Passing the artifacts root here
    would stamp `.artifacts/<gate>/<run-id>` twice.
    """
    try:
        return str(run_dir_path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(run_dir_path.name)


def write_step_log(path: Path, header: str, body: str) -> Path:
    """Write one leg's complete output (header line + full text)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"--- {header}\n{body}" if body else f"--- {header}\n", errors="replace")
    return path


def section_name(heading: str, prefix: str) -> str:
    """Leg label carried by a section heading — the LAST ` (...)` is dropped.

    Integrity headings read `## RELATIONS (ERROR-level; gate-failing)` and
    verify buckets `### yaml_structure (1)`; gate_query derives its leg
    label from the same heading, so both sides must strip identically or
    `output --leg L` misses a log that exists on disk.
    """
    text = heading[len(prefix) :].strip()
    head, sep, _tail = text.rpartition(" (")
    return head.strip() if sep and head.strip() else text


def split_sections(lines: list[str], prefix: str = "## ") -> list[tuple[str, list[str]]]:
    """`(name, body_lines)` per `<prefix> <heading>` section, preamble first.

    The preamble (title + meta) is returned under `"report"` so the retained
    tree is complete even for a heading-less report.
    """
    out: list[tuple[str, list[str]]] = []
    name = "report"
    body: list[str] = []
    for ln in lines:
        if ln.startswith(prefix):
            out.append((name, body))
            name = section_name(ln, prefix)
            body = [ln]
            continue
        body.append(ln)
    out.append((name, body))
    return out


def write_section_logs(
    run_dir: Path, gate: str, lines: list[str], *, prefix: str = "## "
) -> list[Path]:
    """One log per report section, named exactly as `output --leg` looks it up."""
    written: list[Path] = []
    for name, body in split_sections(lines, prefix):
        text = "\n".join(body).strip("\n") + "\n"
        if not text.strip():
            continue
        written.append(
            write_step_log(
                run_dir / step_log_name(gate, name), f"{gate} report section {name!r}", text
            )
        )
    return written


def write_json_artifact(path: Path, payload: dict) -> Path:
    """Write a machine-readable sidecar (`perf.json`, `maint.json`, ...)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def prune_old_runs(artifacts_root: Path, gate: str, keep: int) -> list[Path]:
    """Delete all but the newest `keep` run dirs for `gate`.

    Retention is bounded so a daily gate cannot silently fill the disk the
    way the report corpus did (rotated by `gate_query rotate`). Newest is
    by run-id string, which is a sortable timestamp by construction.
    """
    base = artifacts_root / gate
    if not base.is_dir() or keep <= 0:
        return []
    dirs = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    removed = []
    for stale in dirs[keep:]:
        for child in sorted(stale.rglob("*"), reverse=True):
            try:
                child.unlink() if child.is_file() else child.rmdir()
            except OSError:
                pass
        try:
            stale.rmdir()
            removed.append(stale)
        except OSError:
            pass
    return removed
