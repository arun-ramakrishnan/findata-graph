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
import sys
from pathlib import Path

import yaml

try:
    from helpers.core.corpus import Corpus  # S1b shared walk

    _HAS_CORPUS = True
except ImportError:  # pragma: no cover
    Corpus = None  # type: ignore[assignment]
    _HAS_CORPUS = False

# Bootstrap so bare-script invocation (`python3 helpers/validators/...`)
# resolves `helpers.*`; the -m form and the static_checks wrappers already
# have the repo root on sys.path. Mirrors the shim in verify_notes.py.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# C-preferring loader (libyaml ~10x) via the shared core helper; the
# try/except fallback dance lives there once.
from helpers.core.frontmatter import yaml_safe_load  # noqa: E402

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
_NEWSLETTER_TREES = frozenset(d for d, t in DIR_TO_TYPE.items() if t == "newsletter")

_VALIDATORS: dict[tuple[str, str], object] = {}


def _fast_engine_available() -> bool:
    """fastjsonschema import probe (sys.modules-cached after first call)."""
    try:
        import fastjsonschema  # noqa: F401

        return True
    except ImportError:
        return False


def load_validator(note_type: str, engine: str = "fast"):
    """Return a cached validator for a note type + engine.

    engine="fast" (default): fastjsonschema compiled function —
    fail-fast, single error. engine="strict": jsonschema Draft 2020-12 —
    all errors path-sorted. Verdicts agree (soundness proven 2026-09-21:
    1456/1456 corpus + 28/28 mutations); only message count differs.
    """
    key = (note_type, engine)
    if key not in _VALIDATORS:
        schema = json.loads((SCHEMA_DIR / SCHEMA_FILES[note_type]).read_text())
        if engine == "fast":
            import fastjsonschema

            _VALIDATORS[key] = fastjsonschema.compile(schema)
        else:
            import jsonschema

            _VALIDATORS[key] = jsonschema.Draft202012Validator(schema)
    return _VALIDATORS[key]


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


def validate_frontmatter(fm: dict, note_type: str, engine: str = "fast") -> list[str]:
    """Validate a parsed frontmatter dict; return human-readable violations.

    engine="fast" (default): fail-fast single error, `{path}: {message}`
    shape preserved. engine="strict": all errors path-sorted (today's
    behavior; `--strict`). Verdicts agree across engines; only the
    message count differs.
    """
    norm = _normalize(fm)
    if engine == "fast":
        import fastjsonschema

        validator = load_validator(note_type, "fast")
        try:
            validator(norm)
            return []
        except fastjsonschema.JsonSchemaException as e:
            # Base-class stubs don't declare .path/.message (present at
            # runtime on the raised subclass) — getattr keeps ty clean.
            parts = list(getattr(e, "path", None) or [])
            if parts and parts[0] == "data":
                parts = parts[1:]
            loc = "/".join(str(p) for p in parts) or "<root>"
            return [f"{loc}: {getattr(e, 'message', 'schema violation')}"]
    validator = load_validator(note_type, "strict")
    errs = []
    for e in sorted(validator.iter_errors(norm), key=lambda e: list(e.absolute_path)):
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
        fm = yaml_safe_load(text[4:end])
    except yaml.YAMLError:
        return None
    return fm if isinstance(fm, dict) else None


def _under(p: Path, root: Path) -> bool:
    """True when p lives under root (scope-driven iteration guard)."""
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def check_frontmatter_schema(
    root: Path | None = None, scope: set[Path] | None = None, strict: bool = False
) -> tuple[list[str], list[str]]:
    """Walk the schema-target directories and validate every note.

    Returns (fatal, advisory) per static_checks conventions. Missing
    jsonschema/schema files degrade to a single advisory so runtime
    environments without the dev dependency stay green. A scope restricts
    both the findata walk and the proposal units to the dirty set
    (dirty-gating); None = full. strict=False (default) validates with
    the fast engine; strict=True uses jsonschema (`--strict`). A missing
    fastjsonschema degrades to strict with an advisory (minimal envs
    stay green).
    """
    root = root or REPO_ROOT
    fatal: list[str] = []
    advisory: list[str] = []
    if scope is not None and not scope:
        # Dirty-gating fast path (import-trim slice 1): nothing to validate.
        # Returns BEFORE the jsonschema availability import — importing it
        # to validate zero files costs ~59 ms per fresh process. The two
        # advisories this skips (jsonschema-absent, missing schema files)
        # only matter with a non-empty scope: schema changes force a full
        # run at the builder, so a schema-affecting state never arrives
        # here as an empty scope.
        return fatal, advisory
    engine = "strict" if strict else "fast"
    if engine == "fast" and not _fast_engine_available():
        engine = "strict"
        advisory.append("frontmatter schema: fastjsonschema not installed — degraded to strict")
    if engine == "strict":
        try:
            import jsonschema  # noqa: F401  # availability gate
        except ImportError:
            return [], ["frontmatter schema: jsonschema not installed (dev extra)"]
    _check_proposal_units(root, fatal, scope, engine)
    findata = root / "findata"
    if not findata.is_dir():
        return fatal, advisory
    findata = root / "findata"
    if not findata.is_dir():
        return fatal, advisory
    for dirname, note_type in sorted(DIR_TO_TYPE.items()):
        if not (SCHEMA_DIR / SCHEMA_FILES[note_type]).exists():
            advisory.append(f"frontmatter schema: {SCHEMA_FILES[note_type]} missing")
            continue
        sub = findata / dirname
        if scope is not None:
            # Scope-driven iteration (gate_latency_followups Slice C):
            # no rglob — clean trees enumerate nothing.
            files = sorted(p for p in scope if p.suffix == ".md" and p.is_file() and _under(p, sub))
        else:
            files = sorted(sub.rglob("*.md"))
        for p in files:
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
            for err in validate_frontmatter(fm, note_type, engine):
                fatal.append(f"{p.relative_to(root)}: {err}")
    return fatal, advisory


def _check_proposal_units(
    root: Path, fatal: list[str], scope: set[Path] | None = None, engine: str = "fast"
) -> None:
    """corpus_uniformity S3: validate proposal frontmatter under
    doc/improvements — live proposals must carry the block; archived
    files with proposal headers must too; headerless archive docs stay
    outside the contract. A scope restricts to dirty proposals."""
    improvements = root / "doc" / "improvements"
    if not (improvements.is_dir() and (SCHEMA_DIR / SCHEMA_FILES["proposal"]).exists()):
        return
    candidates = sorted(improvements.glob("proposals/*.md"))
    candidates += sorted(improvements.glob("archive/**/*.md"))
    if scope is not None:
        # Scope-driven iteration: skip the archive rglob on clean trees.
        candidates = sorted(
            p
            for p in scope
            if p.suffix == ".md"
            and p.is_file()
            and (_under(p, improvements / "proposals") or _under(p, improvements / "archive"))
        )
    for p in candidates:
        if p.name == "README.md":
            continue
        fm = parse_frontmatter(p)
        if fm is None:
            if p.parent.name == "proposals" or _has_proposal_header(p):
                fatal.append(f"{p.relative_to(root)}: no parsable frontmatter block")
            continue
        for err in validate_frontmatter(fm, "proposal", engine):
            fatal.append(f"{p.relative_to(root)}: {err}")


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
            if not isinstance(at, str) or not re.match(r"^\d{4}-\d{2}-\d{2}[Tt]", at):
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
    top = p.parts[len(findata.parts)] if len(p.parts) > len(findata.parts) else ""
    if top in ("Companies", "Sectors", "Super_Sectors"):
        return "derived"
    if DIR_TO_TYPE.get(top) == "newsletter":
        return "OCR sources"
    return "other"


def _okf_visit_note(p, findata, root, rel, fatal, advisory, tiers, stale, pre_rollout) -> None:
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
    g = tiers.setdefault(group, {"human-reviewed": 0, "machine-confirmed": 0, "unverified": 0})
    g[tier] += 1


def _okf_census_note(fm: dict, rel: str, tiers: dict, stale: dict, group: str) -> None:
    """Count one frontmatter-bearing note's trust tier + staleness (§5.3/§5.5)."""
    import datetime as _dt

    verified = fm.get("verified") or []
    if any(isinstance(v, dict) and str(v.get("by", "")).startswith("human:") for v in verified):
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


def check_okf_conformance(
    root: Path | None = None, scope: set[Path] | None = None
) -> tuple[list[str], list[str]]:
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

    A scope restricts the walk to the dirty set (dirty-gating); the
    census is then computed over the subset and labelled partial — it is
    advisory-only, so a partial census is acceptable, but it must never
    read as a whole-corpus count.
    """

    root = root or REPO_ROOT
    findata = root / "findata"
    if not findata.is_dir():
        return [], []
    fatal: list[str] = []
    advisory: list[str] = []
    tiers: dict[str, dict[str, int]] = {}  # group -> tier -> count
    stale: dict[str, list[str]] = {}  # group -> past-due rel paths
    pre_rollout: list[str] = []  # newsletters predating OKF adoption (Q5)
    if scope is not None:
        # Scope-driven iteration (gate_latency_followups Slice C).
        files = sorted(p for p in scope if p.suffix == ".md" and p.is_file() and _under(p, findata))
    else:
        files = sorted(findata.rglob("*.md"))
    for p in files:
        if p.name in _OKF_RESERVED:
            continue  # OKF listing files have their own §8/§9 formats
        if p.name in _OKF_SKIP_FILES or "images" in p.parts:
            continue
        rel = str(p.relative_to(root))
        _okf_visit_note(p, findata, root, rel, fatal, advisory, tiers, stale, pre_rollout)
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
            + (f"; {n_stale} past stale_after" if n_stale else "")
            + ")"
        )
    census = f"OKF census: {n_notes} notes" + (f" — {'; '.join(groups)}" if groups else "")
    if scope is not None:
        census += " (dirty subset — partial; whole-corpus census in --full runs)"
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
        lines += ["| key | required | type | constraint | description |", "|---|---|---|---|---|"]
        for key in sorted(schema["properties"]):
            prop = _resolve(dict(schema["properties"][key]), schema)
            typ = _type_str(prop) or "—"
            cons = _constraint_str(prop) or "—"
            desc = (prop.get("description") or "").replace("|", "/").replace("\n", " ")
            lines.append(f"| `{key}` | {'yes' if key in req else 'no'} | {typ} | {cons} | {desc} |")
        lines.append("")
    # Exactly one trailing newline, no trailing blank line: MD012 flags the
    # blank, hand-stripping it broke the checked-in-freshness pin (loop seen
    # twice 2026-09-01) — emit md-lint-clean by construction instead.
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="findata frontmatter JSON-Schema tooling")
    ap.add_argument(
        "--emit-doc", action="store_true", help=f"write {KEY_DOC} from the schemas and exit"
    )
    ap.add_argument(
        "--okf",
        action="store_true",
        help="OKF v0.2 §11 conformance sweep over ALL findata notes "
        "(newsletters included) + provenance census, instead of "
        "the JSON-Schema check",
    )
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="repo root (default: autodetect)")
    ap.add_argument(
        "--dirty",
        action="store_true",
        help="run the sweeps over the git-status dirty set only "
        "(the default since 2026-09-21; flag kept for explicitness)",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="force a full-corpus run — opts out of the dirty-gated default",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="validate with the jsonschema engine (all violations per file; "
        "default: fastjsonschema fail-fast — same verdicts, first error only)",
    )
    ap.add_argument(
        "--report",
        action="store_true",
        help="advisory mode for maint: full corpus (scope ignored), "
        "violations printed as warnings, exit 0 always (never blocks); "
        "the schema sweep uses the strict engine",
    )
    args = ap.parse_args(argv)
    if args.emit_doc:
        KEY_DOC.write_text(emit_key_doc(), encoding="utf-8")
        print(f"wrote {KEY_DOC}")
        return 0
    scope = None
    if not args.full and not args.report:
        from helpers.validators.static_checks import get_dirty_scope

        scope = get_dirty_scope()
        if scope is None:
            print("… no dirty scope (git absent / schemas dirty) — full run")
        else:
            print(f"… dirty-gated: {len(scope)} file(s)")
    if args.okf:
        fatal, advisory = check_okf_conformance(args.root, scope)
    else:
        fatal, advisory = check_frontmatter_schema(
            args.root, scope, strict=args.strict or args.report
        )
    if args.report:
        for line in fatal:
            print(f"advise {line}")
        for line in advisory:
            print(f"advise {line}")
        print(
            f"0 fatal, {len(fatal) + len(advisory)} advisory (report mode — advisory, never blocks)"
        )
        return 0
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
