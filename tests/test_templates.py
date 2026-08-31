"""Template conformance gates — the files in doc/templates/ are the
house style for new Python helpers, Mojo sources, and proposals, and
they rot exactly like code does when tooling or conventions move.

1. ``test_python_template_ruff_clean`` — the template passes BOTH ruff
   selections (the ``make lint`` default and the ``make lint-audit``
   S/UP/C901 audit), named explicitly. A template that ships with a
   noqa-able violation teaches the violation.
2. ``test_mojo_template_format_clean`` — byte-identical ``mojo format``
   copy-check (same copy-diff trick as test_lint_gates.py: the toolchain
   has no ``--check``).
3. ``test_proposal_template_has_contract_fields`` — the skeleton keeps
   the fields the archival checklist and reviewers expect (Date/Status/
   Area header, measured-evidence and acceptance sections).
4. ``test_test_module_template_contracts`` — the test seed passes both
   ruff selections (S101 exempted in pyproject, same as tests/**) and
   keeps the test-only rules: marker routing, xdist read-only DB
   discipline, conftest reuse, burst+best-of-3 timing, pinned
   hypothesis seed.
5. ``test_ts_template_has_contract_lines`` — the TypeScript seed carries
   both toolchain contract pointers and the response-contract rule
   (types/api.ts optional fields must be present — emit null).
6. ``test_note_schemas_and_templates_paired`` — the PAIRINGS registry is
   exactly the on-disk pairing: every note schema carries a resolvable
   ``x-template``, unpaired either way fails (corpus_uniformity §2.1).
7. ``test_yaml_template_keys_subset_of_schema`` — a note seed only
   demonstrates keys its schema allows (a rogue key in a seed is a bug
   in the seed).
8. ``test_every_template_declares_a_contract`` — every seed in
   doc/templates/ (except this README) declares ``# schema:`` (must
   resolve and be paired) or ``# contract:`` (toolchain).
9. ``test_readme_index_lists_every_seed`` — the README Index table
   matches the directory listing; a seed can never exist unlisted.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = REPO_ROOT / "doc" / "templates"
RUFF = REPO_ROOT / ".venv" / "bin" / "ruff"
MOJO = REPO_ROOT / ".venv" / "bin" / "mojo"
SCHEMA_DIR = REPO_ROOT / "doc" / "okf"

# Note-seed <-> schema pairing registry (corpus_uniformity §2.1). Single
# declaration point; the S1 doc/ consolidation repoints these to doc/okf/.
# UNPAIRED IS A FAILURE: a schema without its seed (or vice versa) reddens
# the guard — adding a pairing is a conscious, reviewable edit.
PAIRINGS = {
    "company": ("doc/okf/frontmatter.company.v1.json",
                "doc/templates/company_note.yaml"),
    "sector": ("doc/okf/frontmatter.sector.v1.json",
               "doc/templates/sector_note.yaml"),
    "super_sector": ("doc/okf/frontmatter.super_sector.v1.json",
                     "doc/templates/super_sector_note.yaml"),
    "newsletter": ("doc/okf/frontmatter.newsletter.v1.json",
                   "doc/templates/newsletter_note.yaml"),
    "proposal": ("doc/okf/frontmatter.proposal.v1.json",
                 "doc/templates/proposal.md"),
}

# A seed declares its pairing in its first comment block: `# schema: <path>`
# (YAML seeds; must resolve and match PAIRINGS) or `# contract: <toolchain>`
# (code seeds; prose target, existence not checked). <!-- form for markdown.
_DECL_RE = re.compile(r"^(?:#|//|<!--)\s*(schema|contract):\s*(\S+)", re.MULTILINE)


def test_python_template_ruff_clean():
    """doc/templates/python_module.py passes ruff default AND S/UP/C901."""
    template = TEMPLATES / "python_module.py"
    assert template.is_file()
    for extra in ([], ["--select", "S,UP,C901"]):
        r = subprocess.run(  # noqa: S603  # repo-local venv binary, no shell
            [str(RUFF), "check", "--no-cache", *extra, str(template)],
            capture_output=True, text=True, cwd=REPO_ROOT, check=False,
        )
        assert r.returncode == 0, (
            f"template violates ruff {extra or '(defaults)'}:\n"
            f"{r.stdout}\n{r.stderr}"
        )


def test_mojo_template_format_clean(tmp_path):
    """`mojo format` on a COPY of the template is byte-identical."""
    assert MOJO.is_file(), "mojo toolchain missing at .venv/bin/mojo"
    template = TEMPLATES / "mojo_module.mojo"
    assert template.is_file()
    copy = tmp_path / template.name
    shutil.copy2(template, copy)
    r = subprocess.run(  # noqa: S603  # repo-local venv binary, no shell
        [str(MOJO), "format", str(copy)],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    )
    assert r.returncode == 0, f"mojo format failed:\n{r.stdout}\n{r.stderr}"
    assert copy.read_bytes() == template.read_bytes(), (
        "doc/templates/mojo_module.mojo is not `mojo format`-canonical — "
        "run: mojo format doc/templates/mojo_module.mojo"
    )


def test_proposal_template_has_contract_fields():
    """The proposal skeleton keeps the fields reviewers and the archival
    checklist key off (Status header, measured evidence, acceptance)."""
    text = (TEMPLATES / "proposal.md").read_text(encoding="utf-8")
    for required in (
        "**Date:**",
        "**Status:**",
        "**Area:**",
        "## 1. Motivation",
        "## 2. Evidence",
        "## 4. Acceptance criteria",
        "## 5. Risks",
        "## 6. Non-goals",
        "## Appendix",
    ):
        assert required in text, f"proposal template lost {required!r}"


def test_test_module_template_contracts():
    """doc/templates/test_module.py passes ruff default AND S/UP/C901,
    and keeps the test-only house rules new test files get wrong."""
    template = TEMPLATES / "test_module.py"
    assert template.is_file()
    for extra in ([], ["--select", "S,UP,C901"]):
        r = subprocess.run(  # noqa: S603  # repo-local venv binary, no shell
            [str(RUFF), "check", "--no-cache", *extra, str(template)],
            capture_output=True, text=True, cwd=REPO_ROOT, check=False,
        )
        assert r.returncode == 0, (
            f"template violates ruff {extra or '(defaults)'}:\n"
            f"{r.stdout}\n{r.stderr}"
        )
    text = template.read_text(encoding="utf-8")
    for required in (
        "--strict-markers",
        "read_only=True",
        "_UNIT_SCHEMA",
        "best-of-3",
        "--hypothesis-seed=0",
        "`make perf`",
    ):
        assert required in text, f"test template lost {required!r}"


def test_ts_template_has_contract_lines():
    """ts_module.ts carries both toolchain contract pointers plus the
    api.ts response-contract rule (optional keys still present)."""
    text = (TEMPLATES / "ts_module.ts").read_text(encoding="utf-8")
    for required in (
        "// contract: frontend/tsconfig.json",
        "// contract: frontend/package.json",
        "tsc --noEmit",
        "types/api.ts",
        "Emit null, never omit",
    ):
        assert required in text, f"ts template lost {required!r}"


def test_note_schemas_and_templates_paired():
    """PAIRINGS is exactly the on-disk note-seed <-> schema pairing: every
    doc/okf/frontmatter.*.v1.json carries a resolvable x-template, and
    neither side exists unpaired (corpus_uniformity §2.1)."""
    schemas = sorted(SCHEMA_DIR.glob("frontmatter.*.v1.json"))
    assert schemas, "doc/okf frontmatter schemas went missing"
    on_disk: set[tuple[str, str]] = set()
    for schema in schemas:
        doc = json.loads(schema.read_text(encoding="utf-8"))
        target = doc.get("x-template")
        assert target, f"{schema.name} lost its x-template back-pointer"
        assert (REPO_ROOT / target).is_file(), (
            f"{schema.name} x-template target does not exist: {target}"
        )
        on_disk.add((f"doc/okf/{schema.name}", target))
    declared = {tuple(v) for v in PAIRINGS.values()}
    assert on_disk == declared, (
        f"PAIRINGS registry drifted from disk:\n"
        f"  only on disk: {sorted(on_disk - declared)}\n"
        f"  only declared: {sorted(declared - on_disk)}\n"
        f"(unpaired is a failure state — update PAIRINGS deliberately)"
    )


def test_yaml_template_keys_subset_of_schema():
    """A paired seed demonstrates only keys its schema allows — a rogue key
    in a seed teaches the violation (same doctrine as the ruff guards).
    YAML seeds parse whole-file (the --- fences make a [mapping, None]
    stream); markdown seeds extract the leading --- block (the body is
    prose, not YAML)."""
    import yaml

    for note_type, (schema_rel, template_rel) in PAIRINGS.items():
        schema = json.loads((REPO_ROOT / schema_rel).read_text(encoding="utf-8"))
        text = (REPO_ROOT / template_rel).read_text(encoding="utf-8")
        if template_rel.endswith(".md"):
            assert text.startswith("---"), f"{template_rel} lost its FM block"
            data = yaml.safe_load(text[4:text.find("\n---", 3)])
        else:
            docs = [d for d in yaml.safe_load_all(text) if d is not None]
            assert len(docs) == 1, f"{template_rel} is not a single frontmatter block"
            data = docs[0]
        assert isinstance(data, dict), f"{template_rel} did not parse to a mapping"
        rogue = set(data) - set(schema["properties"])
        assert not rogue, (
            f"{template_rel} demonstrates keys the {note_type} schema "
            f"rejects (additionalProperties: false): {sorted(rogue)}"
        )


def test_every_template_declares_a_contract():
    """Every seed in doc/templates/ (README excluded — it is the index)
    declares `# schema: <resolvable, paired path>` or `# contract: <text>`.
    A seed without a declaration is an unpaired template."""
    seeds = sorted(p for p in TEMPLATES.iterdir()
                   if p.is_file() and p.name != "README.md")
    assert seeds, "doc/templates emptied?"
    declared_pairs = {tuple(v) for v in PAIRINGS.values()}
    for seed in seeds:
        decls = _DECL_RE.findall(seed.read_text(encoding="utf-8"))
        assert decls, (
            f"{seed.name} declares no schema/contract — unpaired template"
        )
        for kind, target in decls:
            if kind != "schema":
                continue
            assert (REPO_ROOT / target).is_file(), (
                f"{seed.name} # schema target does not exist: {target}"
            )
            assert (target, f"doc/templates/{seed.name}") in declared_pairs, (
                f"{seed.name} points at {target} but is not in PAIRINGS"
            )


def test_readme_index_lists_every_seed():
    """The README Index table matches the directory listing exactly — a
    template can never exist unlisted (and the index never lists ghosts)."""
    text = (TEMPLATES / "README.md").read_text(encoding="utf-8")
    index_section = text.split("## Index", 1)[1].split("\n## ", 1)[0]
    listed = set()
    for line in index_section.splitlines():
        if not line.startswith("|"):
            continue
        cell = line.split("|")[1].strip()
        if cell and cell != "Template" and not set(cell) <= {"-", " ", ":"}:
            listed.add(cell)
    expected = {p.name for p in TEMPLATES.iterdir() if p.is_file()} - {"README.md"}
    assert listed == expected, (
        f"README Index out of sync with doc/templates/:\n"
        f"  unlisted: {sorted(expected - listed)}\n"
        f"  ghosts: {sorted(listed - expected)}"
    )
