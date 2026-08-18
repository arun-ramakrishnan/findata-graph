#!/usr/bin/env python3
"""JSON-Schema validation for findata note frontmatter (B1, 2026-08-17).

doc/schema/frontmatter.<type>.v1.json formalizes the de-facto key sets of the
Companies (1,068), Sectors (42) and Super_Sectors (9) notes into a structural
contract: required keys, value types, formats/enums, and no rogue keys. This
module loads those schemas, validates parsed frontmatter dicts, and exposes:

- ``validate_frontmatter(fm, note_type)`` — list of human-readable violations
- ``check_frontmatter_schema()`` — (fatal, advisory) walker wired into
  helpers/validators/static_checks.py CHECKS
- ``emit_key_doc()`` — deterministic Markdown key reference GENERATED from the
  schemas (doc/schema/frontmatter_keys.md), so the human docs and the validator
  share one source of truth

Design notes:

- YAML auto-parses unquoted ISO dates into ``datetime.date`` objects. The
  live corpus mixes both spellings, so ``_normalize()`` converts date objects
  to ISO strings before validating. This is lossless: the pipeline treats both
  identically (see _check_date_one / app.parse_yaml_frontmatter).
- The schema is a VALIDATOR, not a generator: YAML stays human-first (B1
  decision). Newsletter editions (The_Chatter, Points_And_Figures,
  The_PlotLines) carry no frontmatter by design and are not schema targets.
- jsonschema is a dev-only dependency; when unavailable the corpus check
  degrades to an advisory (production/runtime imports never need this module).

Usage:
    python3 -m helpers.validators.frontmatter_schema            # validate corpus
    python3 -m helpers.validators.frontmatter_schema --emit-doc # refresh key doc
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "doc" / "schema"
KEY_DOC = SCHEMA_DIR / "frontmatter_keys.md"

# Top-level findata directory -> schema id (note type).
DIR_TO_TYPE = {
    "Companies": "company",
    "Sectors": "sector",
    "Super_Sectors": "super_sector",
}
SCHEMA_FILES = {
    "company": "frontmatter.company.v1.json",
    "sector": "frontmatter.sector.v1.json",
    "super_sector": "frontmatter.super_sector.v1.json",
}

_VALIDATORS: dict[str, object] = {}


def load_validator(note_type: str):
    """Return a cached Draft 2020-12 validator for a note type."""
    if note_type not in _VALIDATORS:
        import jsonschema

        schema = json.loads((SCHEMA_DIR / SCHEMA_FILES[note_type]).read_text())
        _VALIDATORS[note_type] = jsonschema.Draft202012Validator(schema)
    return _VALIDATORS[note_type]


def _normalize(fm: dict) -> dict:
    """Convert PyYAML-parsed date/datetime values to ISO strings (lossless)."""
    import datetime as _dt

    out = {}
    for k, v in fm.items():
        if isinstance(v, _dt.datetime):
            out[k] = v.date().isoformat()
        elif isinstance(v, _dt.date):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def validate_frontmatter(fm: dict, note_type: str) -> list[str]:
    """Validate a parsed frontmatter dict; return human-readable violations."""
    validator = load_validator(note_type)
    errs = []
    for e in sorted(validator.iter_errors(_normalize(fm)), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        errs.append(f"{loc}: {e.message}")
    return errs


def parse_frontmatter(path: Path) -> dict | None:
    """Parse the leading YAML frontmatter block of a note (None if absent)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    try:
        fm = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return None
    return fm if isinstance(fm, dict) else None


def check_frontmatter_schema(root: Path | None = None) -> tuple[list[str], list[str]]:
    """Walk the schema-target directories and validate every note.

    Returns (fatal, advisory) per static_checks conventions. Missing
    jsonschema/schema files degrade to a single advisory so runtime
    environments without the dev dependency stay green.
    """
    root = root or REPO_ROOT
    fatal: list[str] = []
    advisory: list[str] = []
    try:
        import jsonschema  # noqa: F401  # availability gate
    except ImportError:
        return [], ["frontmatter schema: jsonschema not installed (dev extra)"]
    findata = root / "findata"
    if not findata.is_dir():
        return [], []
    for dirname, note_type in sorted(DIR_TO_TYPE.items()):
        if not (SCHEMA_DIR / SCHEMA_FILES[note_type]).exists():
            advisory.append(f"frontmatter schema: {SCHEMA_FILES[note_type]} missing")
            continue
        for p in sorted((findata / dirname).rglob("*.md")):
            if "images" in p.parts:
                continue
            fm = parse_frontmatter(p)
            if fm is None:
                fatal.append(f"{p.relative_to(root)}: no parsable frontmatter block")
                continue
            for err in validate_frontmatter(fm, note_type):
                fatal.append(f"{p.relative_to(root)}: {err}")
    return fatal, advisory


def _resolve(prop: dict, schema: dict) -> dict:
    """Resolve a top-level $ref (isoDate) into its definition."""
    if "$ref" in prop:
        name = prop["$ref"].rsplit("/", 1)[-1]
        return dict(schema["$defs"][name])
    return prop


def _type_str(prop: dict) -> str:
    """Human type label for a property schema ("string?" = nullable string)."""
    if "type" in prop:
        typ = prop["type"]
        if isinstance(typ, list):
            types = [str(x) for x in typ]
            if "null" in types and len(types) == 2:
                return next(x for x in types if x != "null") + "?"
            return "/".join(types)
        return str(typ)
    if "oneOf" in prop:
        alts = [_type_str(dict(a)) for a in prop["oneOf"] if isinstance(a, dict)]
        if "null" in alts and len(alts) == 2:
            return next(x for x in alts if x != "null") + "?"
        return "/".join(alts)
    if "enum" in prop or "const" in prop:
        return "string"
    return ""


def _array_constraints(prop: dict) -> list[str]:
    """Constraint strings for array-typed properties."""
    inner = prop.get("items", {})
    sub = _constraint_str(inner)
    out = [f"items: {'string' if not sub else sub}"]
    if "minItems" in prop:
        out.append(f"min {prop['minItems']} item(s)")
    return out


def _oneof_constraints(prop: dict) -> list[str]:
    """Constraint strings from oneOf alternatives (nullable unions)."""
    out = []
    for alt in prop.get("oneOf", []):
        if isinstance(alt, dict):
            sub = _constraint_str(alt)
            if sub:
                out.append(sub)
    return out


def _constraint_str(prop: dict) -> str:
    """Human constraint summary (no type info) for a property schema."""
    bits: list[str] = []
    if "const" in prop:
        bits.append(f"always `{prop['const']}`")
    if "enum" in prop:
        bits.append("one of " + ", ".join(f"`{v}`" for v in prop["enum"]))
    if "pattern" in prop:
        # "/" for "|" so regex alternations survive the Markdown table cell.
        bits.append("pattern `" + prop["pattern"].replace("|", "/") + "`")
    if "minLength" in prop:
        bits.append(f"min length {prop['minLength']}")
    if prop.get("type") == "array":
        bits.extend(_array_constraints(prop))
    bits.extend(_oneof_constraints(prop))
    return "; ".join(dict.fromkeys(bits))  # de-dup, keep order


def emit_key_doc() -> str:
    """Deterministic Markdown key reference generated from the schemas."""
    lines = [
        "# Note frontmatter keys (GENERATED)",
        "",
        "Generated from doc/schema/frontmatter.*.v1.json by",
        "`python3 -m helpers.validators.frontmatter_schema --emit-doc`.",
        "Do not edit by hand — edit the schema and regenerate.",
        "Relational rules (normalized_name == filename, permalink sector ==",
        "directory) live in helpers/validators/verify_notes.py + static_checks.py.",
        "",
    ]
    for note_type, fname in SCHEMA_FILES.items():
        schema = json.loads((SCHEMA_DIR / fname).read_text())
        req = set(schema["required"])
        lines += [f"## {note_type}", "", f"Source: [`{fname}`]({fname})", ""]
        lines += ["| key | required | type | constraint | description |",
                  "|---|---|---|---|---|"]
        for key in sorted(schema["properties"]):
            prop = _resolve(dict(schema["properties"][key]), schema)
            typ = _type_str(prop) or "—"
            cons = _constraint_str(prop) or "—"
            desc = (prop.get("description") or "").replace("|", "/").replace("\n", " ")
            lines.append(
                f"| `{key}` | {'yes' if key in req else 'no'} | {typ} | {cons} | {desc} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="findata frontmatter JSON-Schema tooling")
    ap.add_argument("--emit-doc", action="store_true", help=f"write {KEY_DOC} from the schemas and exit")
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="repo root (default: autodetect)")
    args = ap.parse_args(argv)
    if args.emit_doc:
        KEY_DOC.write_text(emit_key_doc(), encoding="utf-8")
        print(f"wrote {KEY_DOC}")
        return 0
    fatal, advisory = check_frontmatter_schema(args.root)
    n = 0
    for line in fatal:
        print(f"FATAL {line}")
        n += 1
    for line in advisory:
        print(f"advise {line}")
    print(f"{n} fatal, {len(advisory)} advisory")
    return 1 if fatal else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
