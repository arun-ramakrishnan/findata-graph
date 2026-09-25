#!/usr/bin/env python3
"""Check the repository's AGPL metadata and third-party inventory."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_LICENSE = "AGPL-3.0-or-later"


def _dependency_name(dep: str) -> str:
    return re.split(r"[\s<>=!~\[]", dep, maxsplit=1)[0].strip()


def _dependency_names(project: dict) -> list[str]:
    names: list[str] = []
    for dep in project.get("dependencies", []):
        names.append(_dependency_name(dep))
    for group in project.get("optional-dependencies", {}).values():
        for dep in group:
            names.append(_dependency_name(dep))
    return names


def _check_license(errors: list[str]) -> None:
    license_path = ROOT / "LICENSE"
    if not license_path.is_file():
        errors.append("LICENSE is missing")
        return
    text = license_path.read_text(encoding="utf-8")
    if "GNU AFFERO GENERAL PUBLIC LICENSE" not in text or "Version 3" not in text:
        errors.append("LICENSE is not the GNU AGPL version 3 text")


def _project() -> tuple[dict, str | None]:
    path = ROOT / "pyproject.toml"
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")).get("project", {}), None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return {}, str(exc)


def _check_project(errors: list[str], project: dict) -> None:
    if project.get("license") != EXPECTED_LICENSE:
        errors.append(f"pyproject project.license must be {EXPECTED_LICENSE}")
    if project.get("license-files") != ["LICENSE"]:
        errors.append('pyproject project.license-files must be ["LICENSE"]')


def _check_frontend(errors: list[str]) -> None:
    path = ROOT / "frontend" / "package.json"
    try:
        frontend = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"frontend/package.json cannot be read: {exc}")
        return
    if frontend.get("license") != EXPECTED_LICENSE:
        errors.append(f"frontend package license must be {EXPECTED_LICENSE}")


def _check_manifest(errors: list[str], project: dict) -> None:
    path = ROOT / "THIRD_PARTY_LICENSES.md"
    if not path.is_file():
        errors.append("THIRD_PARTY_LICENSES.md is missing")
        return
    manifest = path.read_text(encoding="utf-8")
    if "python-igraph" not in manifest or "First-party boundary" not in manifest:
        errors.append("third-party inventory is missing the GPL candidate or asset boundary")
    manifest_lower = manifest.lower()
    for name in _dependency_names(project):
        if name and name.lower() not in manifest_lower:
            errors.append(f"third-party inventory is missing dependency: {name}")


def _check_readme(errors: list[str]) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if EXPECTED_LICENSE not in readme or "THIRD_PARTY_LICENSES.md" not in readme:
        errors.append("README is missing the AGPL/third-party license references")


def check() -> list[str]:
    errors: list[str] = []
    _check_license(errors)
    project, project_error = _project()
    if project_error:
        errors.append(f"pyproject.toml cannot be read: {project_error}")
    _check_project(errors, project)
    _check_frontend(errors)
    _check_manifest(errors, project)
    _check_readme(errors)
    return errors


def main() -> int:
    errors = check()
    if errors:
        for error in errors:
            print(f"license-check: {error}", file=sys.stderr)
        return 1
    print("license-check: AGPL metadata and third-party inventory present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
