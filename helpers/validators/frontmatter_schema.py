#!/usr/bin/env python3
"""JSON-Schema validation for findata note frontmatter (B1, 2026-08-17).

doc/okf/frontmatter.<type>.v1.json formalizes the de-facto key sets of the
Companies (1,068), Sectors (42) and Super_Sectors (9) notes into a structural
contract: required keys, value types, formats/enums, and no rogue keys. This
module loads those schemas, validates parsed frontmatter dicts, and exposes:

- ``validate_frontmatter(fm, note_type)`` — list of human-readable violations
- ``check_frontmatter_schema()`` — (fatal, advisory) walker wired into
  helpers/validators/static_checks.py CHECKS
- ``emit_key_doc()`` — deterministic Markdown key reference GENERATED from the
  schemas (doc/okf/frontmatter_keys.md), so the human docs and the validator
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
    python3 -m helpers.validators.frontmatter_schema --okf      # OKF §11 sweep + census
    python3 -m helpers.validators.frontmatter_schema --emit-doc # refresh key doc
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

# C-accelerated loader when libyaml is built into PyYAML (it is on this
# box); pure-Python fallback otherwise. This module parses the frontmatter
# of every findata note on each static_checks run (~2.4k safe_loads), where
# the pure-Python scanner dominates the wall clock (~5s of 8s before this).
try:
    from yaml import CSafeLoader as _SafeLoader
except ImportError:  # pragma: no cover - libyaml not built
    from yaml import SafeLoader as _SafeLoader

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "doc" / "okf"
KEY_DOC = SCHEMA_DIR / "frontmatter_keys.md"

# Top-level findata directory -> schema id (note type).
DIR_TO_TYPE = {
    "Companies": "company",
    "Sectors": "sector",
    "Super_Sectors": "super_sector",
    # Source newsletter trees (newsletter_notes_adoption.md S1). Future
    # source trees are registered here as they land (one line per tree).
    "The_Chatter": "newsletter",
    "The_PlotLines": "newsletter",
    "Points_And_Figures": "newsletter",
}
SCHEMA_FILES = {
    "company": "frontmatter.company.v1.json",
    "sector": "frontmatter.sector.v1.json",
    "super_sector": "frontmatter.super_sector.v1.json",
    "newsletter": "frontmatter.newsletter.v1.json",
    # corpus_uniformity S3 (option 2 FULL): proposals are NOT a findata
    # note type — DIR_TO_TYPE can't carry them. Registered here for
    # load_validator/--emit-doc; the walk is the decoupled second loop in
    # check_frontmatter_schema (proposals/ + archive/**, READMEs skipped).
    "proposal": "frontmatter.proposal.v1.json",
}
# Source trees registered above (used for chrome skipping in the corpus walk).
_NEWSLETTER_TREES = frozenset(
    d for d, t in DIR_TO_TYPE.items() if t == "newsletter"
)

_VALIDATORS: dict[str, object] = {}


def load_validator(note_type: str):
    """Return a cached Draft 2020-12 validator for a note type."""
    if note_type not in _VALIDATORS:
        import jsonschema

        schema = json.loads((SCHEMA_DIR / SCHEMA_FILES[note_type]).read_text())
        _VALIDATORS[note_type] = jsonschema.Draft202012Validator(schema)
    return _VALIDATORS[note_type]


def _normalize(fm: dict) -> dict:
    """Convert PyYAML-parsed date/datetime values to ISO strings (lossless).

    Top-level date-like keys (created / last_modified) keep the B1 semantics:
    datetime -> date-only ISO (their schema pattern is ``YYYY-MM-DD``).
    NESTED OKF v0.2 values (``generated.at``, ``verified[].at``,
    ``sources[].last_modified``) are normalized too — a hand-written YAML
    timestamp (``at: 2026-08-18T12:00:00``, no Z suffix) parses as a datetime
    object and would otherwise fail the string patterns. Nested datetimes
    keep their time component (ISO 8601 datetime pattern).
    """
    import datetime as _dt

    out = {}
    for k, v in fm.items():
        if isinstance(v, _dt.datetime):
            out[k] = v.date().isoformat()
        elif isinstance(v, _dt.date):
            out[k] = v.isoformat()
        else:
            out[k] = _normalize_nested(v)
    return out


def _normalize_nested(obj):
    """Deep date/datetime -> ISO string conversion for nested OKF values."""
    import datetime as _dt

    if isinstance(obj, _dt.datetime):
        if obj.tzinfo == _dt.UTC:
            return obj.strftime("%Y-%m-%dT%H:%M:%SZ")
        return obj.isoformat()
    if isinstance(obj, _dt.date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _normalize_nested(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_nested(v) for v in obj]
    return obj


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
        fm = yaml.load(text[4:end], Loader=_SafeLoader)
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
    # Proposal contract (corpus_uniformity S3) — a walk DECOUPLED from
    # DIR_TO_TYPE because proposals live under doc/improvements, not
    # findata/. Runs BEFORE the findata early-return: a doc-only tree
    # (tests, partial checkouts) still gets proposal validation. Live
    # proposals must carry the block; archived files that ever were
    # proposals (bold-line Status/Date header present) must too. Plain
    # archive docs without a proposal header (triage/acceptance notes)
    # are a different artifact class — outside it.
    improvements = root / "doc" / "improvements"
    if improvements.is_dir() and (SCHEMA_DIR / SCHEMA_FILES["proposal"]).exists():
        candidates = sorted(improvements.glob("proposals/*.md"))
        candidates += sorted(improvements.glob("archive/**/*.md"))
        for p in candidates:
            if p.name == "README.md":
                continue
            fm = parse_frontmatter(p)
            if fm is None:
                if p.parent.name == "proposals" or _has_proposal_header(p):
                    fatal.append(
                        f"{p.relative_to(root)}: no parsable frontmatter block")
                continue
            for err in validate_frontmatter(fm, "proposal"):
                fatal.append(f"{p.relative_to(root)}: {err}")
    findata = root / "findata"
    if not findata.is_dir():
        return fatal, advisory
    for dirname, note_type in sorted(DIR_TO_TYPE.items()):
        if not (SCHEMA_DIR / SCHEMA_FILES[note_type]).exists():
            advisory.append(f"frontmatter schema: {SCHEMA_FILES[note_type]} missing")
            continue
        for p in sorted((findata / dirname).rglob("*.md")):
            if "images" in p.parts:
                continue
            # Newsletter-tree chrome (image maps) is pipeline scaffolding,
            # not prose — same skip set as the OKF sweep / extract_relations.
            if dirname in _NEWSLETTER_TREES and p.name in _OKF_SKIP_FILES:
                continue
            fm = parse_frontmatter(p)
            if fm is None:
                fatal.append(f"{p.relative_to(root)}: no parsable frontmatter block")
                continue
            for err in validate_frontmatter(fm, note_type):
                fatal.append(f"{p.relative_to(root)}: {err}")
    return fatal, advisory


def _has_proposal_header(path: Path) -> bool:
    """True if the file carries the bold-line proposal header (Date or
    Status) in its opening lines — the pre-S3 marker of proposal-hood."""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return False
    return "**Status:**" in head or "**Date:**" in head


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


# --------------------------------------------------------------------------- #
# OKF v0.2 conformance sweep (doc/okf/README.md §1.1; --okf mode)                    #
# --------------------------------------------------------------------------- #
# OKF §11: a bundle is conformant when every non-reserved .md has parseable
# frontmatter with a non-empty `type`. This project is a strict SUPERSET for
# the schema-target trees (validated above) and a PRODUCER for newsletters
# (pdf_conv_md.py emits OKF blocks that no schema covers — this sweep is the
# only check over them). Per the spec's "must not reject" rule, optional-key
# absence is never an issue; only structural breakage is.

# OKF reserved filenames (§4): listing/change-log files with their own formats.
_OKF_RESERVED = {"index.md", "log.md"}

# Generated chrome (not prose, not part of the vocabulary surface) —
# image_map.md is newsletter-tree chrome in the same skip set the pipeline
# uses (derive_insights._NEWSLETTER_CHROME_NAMES,
# extract_relations._NEWSLETTER_SKIP_FILES); _pending_triage_report.md is
# the triage sidecar's report artifact at the findata/ root.
_OKF_SKIP_FILES = {"image_map.md", "_pending_triage_report.md"}


def _okf_newsletter_shape(fm: dict, rel: str, root: Path) -> list[str]:
    """Shape-check a newsletter note's OPTIONAL OKF block (advisory-level).

    Only runs when the note HAS an OKF provenance block; a bare newsletter
    note (pre-rollout) is legal and produces nothing. Checks the producer
    contract pdf_conv_md.py writes: non-empty ``generated.by``, ISO 8601
    ``generated.at``, and — when ``sources`` is present — bundle-relative
    resource paths that RESOLVE to real files under *root*. *fm* must
    already be ``_normalize``d: raw PyYAML loads ISO timestamps
    (``generated.at``, even Z-suffixed) as datetime OBJECTS, and the
    string-pattern checks below expect strings.
    """
    issues: list[str] = []
    gen = fm.get("generated")
    if gen is not None:
        if not isinstance(gen, dict) or not gen.get("by"):
            issues.append(f"{rel}: generated present but malformed (need by/at)")
        else:
            at = gen.get("at")
            if not isinstance(at, str) or not re.match(
                r"^\d{4}-\d{2}-\d{2}[Tt]", at
            ):
                issues.append(f"{rel}: generated.at is not an ISO 8601 datetime")
    for src in fm.get("sources") or []:
        if not isinstance(src, dict):
            issues.append(f"{rel}: sources entry is not a mapping")
            continue
        res = src.get("resource")
        if not isinstance(res, str) or not res.startswith("/"):
            issues.append(f"{rel}: sources[].resource must be bundle-relative (leading /)")
        elif not (root / res.lstrip("/")).exists():
            issues.append(f"{rel}: sources[].resource does not resolve: {res}")
    return issues


def _okf_group(p, findata) -> str:
    """Census group for a note: derived tree, OCR-source tree, or other."""
    top = (p.parts[len(findata.parts)]
           if len(p.parts) > len(findata.parts) else "")
    if top in ("Companies", "Sectors", "Super_Sectors"):
        return "derived"
    if DIR_TO_TYPE.get(top) == "newsletter":
        return "OCR sources"
    return "other"


def _okf_visit_note(p, findata, root, rel, fatal, advisory, tiers, stale,
                    pre_rollout) -> None:
    """Classify one note for check_okf_conformance (§11 + tiers + staleness).

    Mutates the passed lists/dicts: *fatal* (§11 hard-rule breaks),
    *advisory* (newsletter shape issues), *tiers* (trust census, keyed by
    group -> tier -> count), *stale* (past-due stale_after, keyed by
    group), *pre_rollout* (OCR source notes without frontmatter,
    pre-adoption).
    """
    group = _okf_group(p, findata)

    fm = parse_frontmatter(p)
    if fm is not None:
        # Raw PyYAML loads ISO timestamps as datetime OBJECTS (even the
        # Z-suffixed form) — normalize to strings before any inspection.
        fm = _normalize(fm)
    in_schema_tree = (
        p.parts[: len(findata.parts)] == findata.parts
        and p.parts[len(findata.parts)] in DIR_TO_TYPE
    )
    if fm is None:
        if in_schema_tree:
            # B1 hard contract (the schema check above already fatals on
            # this; repeated so --okf is self-contained).
            fatal.append(f"{rel}: OKF §11: no parseable frontmatter block")
        else:
            # OCR source note predating adoption — legal per gradual
            # rollout (accepted Q5); aggregated, trends to 0 over time.
            pre_rollout.append(rel)
            _okf_tier(tiers, group, "unverified")
        return
    typ = fm.get("type")
    if not isinstance(typ, str) or not typ.strip():
        fatal.append(f"{rel}: OKF §11: frontmatter has no non-empty `type`")
        return
    # Producer-shape check for the un-schema'd OCR-source surface.
    if typ == "newsletter":
        advisory.extend(_okf_newsletter_shape(fm, rel, root))
    _okf_census_note(fm, rel, tiers, stale, group)


def _okf_tier(tiers: dict, group: str, tier: str) -> None:
    """Increment the group-scoped census tier counter."""
    g = tiers.setdefault(
        group, {"human-reviewed": 0, "machine-confirmed": 0, "unverified": 0}
    )
    g[tier] += 1


def _okf_census_note(fm: dict, rel: str, tiers: dict, stale: dict,
                     group: str) -> None:
    """Count one frontmatter-bearing note's trust tier + staleness (§5.3/§5.5)."""
    import datetime as _dt

    verified = fm.get("verified") or []
    if any(isinstance(v, dict) and str(v.get("by", "")).startswith("human:")
           for v in verified):
        _okf_tier(tiers, group, "human-reviewed")
    elif fm.get("generated") or verified:
        _okf_tier(tiers, group, "machine-confirmed")
    else:
        _okf_tier(tiers, group, "unverified")
    sa = fm.get("stale_after")
    if isinstance(sa, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", sa):
        try:
            if _dt.date.today() >= _dt.date.fromisoformat(sa):
                stale.setdefault(group, []).append(rel)
        except ValueError:
            pass


def check_okf_conformance(root: Path | None = None) -> tuple[list[str], list[str]]:
    """OKF §11 conformance + producer-shape sweep over the whole vault.

    Walks EVERY non-reserved ``findata/**/*.md`` (newsletters included —
    check_frontmatter_schema deliberately scopes the three schema trees).
    Returns (fatal, advisory):

    - fatal: notes with unparsable frontmatter or an empty ``type`` —
      violations of OKF §11's only two hard rules;
    - advisory: newsletter OKF-block shape issues (actor/at/resource) —
      reported, never fatal, per the spec's must-not-reject stance — plus a
      group-scoped provenance census (derived vs OCR sources: trust tiers
      + staleness per group), so the first consumer of the vocabulary
      ships with the first check.
    """

    root = root or REPO_ROOT
    findata = root / "findata"
    if not findata.is_dir():
        return [], []
    fatal: list[str] = []
    advisory: list[str] = []
    tiers: dict[str, dict[str, int]] = {}   # group -> tier -> count
    stale: dict[str, list[str]] = {}        # group -> past-due rel paths
    pre_rollout: list[str] = []   # newsletters predating OKF adoption (Q5)
    for p in sorted(findata.rglob("*.md")):
        if p.name in _OKF_RESERVED:
            continue  # OKF listing files have their own §8/§9 formats
        if p.name in _OKF_SKIP_FILES or "images" in p.parts:
            continue
        rel = str(p.relative_to(root))
        _okf_visit_note(p, findata, root, rel, fatal, advisory,
                        tiers, stale, pre_rollout)
    n_notes = sum(sum(t.values()) for t in tiers.values())
    if pre_rollout:
        advisory.append(
            f"OKF: {len(pre_rollout)} OCR source notes (newsletter trees) lack "
            f"provenance frontmatter — pre-adoption conversions; gradual "
            f"rollout (okf_adoption Q5). New pdf_conv_md.py conversions carry "
            f"generated+sources from day one."
        )
    # Group-scoped census: derived notes and OCR source notes have different
    # provenance stories (derives vs primary sources), so tiers + staleness
    # are reported per group, not pooled into one total.
    groups = []
    for group in ("derived", "OCR sources", "other"):
        t = tiers.get(group)
        if not t:
            continue
        bits = ", ".join(f"{v} {k}" for k, v in t.items() if v)
        n_stale = len(stale.get(group, []))
        groups.append(
            f"{group}: {sum(t.values())} ({bits}"
            + (f"; {n_stale} past stale_after" if n_stale else "") + ")"
        )
    census = f"OKF census: {n_notes} notes — " + "; ".join(groups)
    advisory.append(census)
    return fatal, advisory



def emit_key_doc() -> str:
    """Deterministic Markdown key reference generated from the schemas."""
    lines = [
        "# Note frontmatter keys (GENERATED)",
        "",
        "Generated from doc/okf/frontmatter.*.v1.json by",
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
    ap.add_argument("--okf", action="store_true",
                    help="OKF v0.2 §11 conformance sweep over ALL findata notes "
                         "(newsletters included) + provenance census, instead of "
                         "the JSON-Schema check")
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="repo root (default: autodetect)")
    args = ap.parse_args(argv)
    if args.emit_doc:
        KEY_DOC.write_text(emit_key_doc(), encoding="utf-8")
        print(f"wrote {KEY_DOC}")
        return 0
    if args.okf:
        fatal, advisory = check_okf_conformance(args.root)
    else:
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
