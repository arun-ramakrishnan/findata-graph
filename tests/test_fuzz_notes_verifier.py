"""Fuzz tests — NotesVerifier invariants.

Property-based tests (via Hypothesis) for `helpers/validators/verify_notes.py`.

These tests verify that the NotesVerifier validation methods never crash
on arbitrary input and maintain their core invariants:

1. check_yaml_structure - parses arbitrary YAML, validates required fields,
   type values, tags format, and delegates to type-specific validators.
2. check_content_quality - processes arbitrary markdown content, detects
   placeholder patterns, checks for meaningful content and structure.
3. check_filename_format - validates filename constraints.
4. check_name_sync - validates normalized_name matches filename stem.
5. _norm_heading / _heading_false_positive - heading normalization and
   false-positive detection never raise on arbitrary strings.

Runs alongside regular pytest in `make qa`. Hypothesis defaults to 100
random examples per @given test; each completes in <1s.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "helpers"))

from validators.verify_notes import NotesVerifier  # noqa: E402


# ---------------------------------------------------------------------------
# Test strategies
# ---------------------------------------------------------------------------

# Valid filename stems (PascalCase, single underscores, alnum start, ≤100 chars)
_filename_st = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_"),
    min_size=1,
    max_size=100,
).filter(lambda s: s[0].isalnum() and "__" not in s)

# Arbitrary markdown/YAML content (bounded to avoid extreme cases)
_content_st = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
    min_size=0,
    max_size=5000,
)

# Arbitrary YAML block content
_yaml_st = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
    min_size=0,
    max_size=1000,
)

# Valid type values
_valid_type_st = st.sampled_from(["company", "sector", "super_sector", "sub_sector", "theme"])

# Valid tag format: namespace/value with lowercase alnum + underscore
_tag_st = st.builds(
    lambda ns, val: f"{ns}/{val}",
    st.text(alphabet=st.characters(whitelist_categories=("Ll", "Nd"), whitelist_characters="_"), min_size=1, max_size=20),
    st.text(alphabet=st.characters(whitelist_categories=("Ll", "Nd"), whitelist_characters="_"), min_size=1, max_size=30),
)

# Lists of tags
_tags_list_st = st.lists(_tag_st, min_size=0, max_size=10, unique=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def verifier(tmp_path):
    """Create a NotesVerifier instance with a temp directory."""
    return NotesVerifier(tmp_path)


# ---------------------------------------------------------------------------
# Invariant 1: check_yaml_structure never raises on arbitrary input
# ---------------------------------------------------------------------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=200, deadline=None)
@given(_content_st)
def test_fuzz_check_yaml_structure_never_raises(verifier, content: str):
    """check_yaml_structure handles arbitrary markdown/YAML content without crashing."""
    # Create a temp file
    test_file = Path(verifier.project_root) / "test_company.md"
    test_file.write_text(content, encoding="utf-8")

    try:
        verifier.check_yaml_structure(str(test_file), content)
    except Exception as e:
        pytest.fail(f"check_yaml_structure raised {type(e).__name__}: {e} on input: {content[:200]!r}")


# ---------------------------------------------------------------------------
# Invariant 2: check_yaml_structure with synthetic valid YAML structure
# ---------------------------------------------------------------------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=200, deadline=None)
@given(
    title=st.text(min_size=0, max_size=100),
    type_val=_valid_type_st,
    tags=_tags_list_st,
    normalized_name=st.text(min_size=0, max_size=100).filter(lambda s: s == "" or (s[0].isalnum() and "__" not in s)),
    permalink=st.text(min_size=0, max_size=200),
    extra_content=_content_st,
)
def test_fuzz_check_yaml_structure_with_valid_frontmatter(
    verifier, title: str, type_val: str, tags: list, normalized_name: str, permalink: str, extra_content: str
):
    """check_yaml_structure handles valid YAML frontmatter structures correctly."""
    # Build YAML frontmatter
    yaml_lines = ["---"]
    if title:
        yaml_lines.append(f"title: {title!r}")
    yaml_lines.append(f"type: {type_val}")
    if tags:
        yaml_lines.append("tags:")
        for tag in tags:
            yaml_lines.append(f"  - {tag!r}")
    if normalized_name:
        yaml_lines.append(f"normalized_name: {normalized_name!r}")
    if permalink:
        yaml_lines.append(f"permalink: {permalink!r}")
    yaml_lines.append("---")
    yaml_lines.append(extra_content)

    content = "\n".join(yaml_lines)
    test_file = Path(verifier.project_root) / "test.md"
    test_file.write_text(content, encoding="utf-8")

    try:
        verifier.check_yaml_structure(str(test_file), content)
    except Exception as e:
        pytest.fail(f"check_yaml_structure raised {type(e).__name__}: {e} on input: {content[:200]!r}")


# ---------------------------------------------------------------------------
# Invariant 3: check_content_quality never raises on arbitrary input
# ---------------------------------------------------------------------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=200, deadline=None)
@given(_content_st)
def test_fuzz_check_content_quality_never_raises(verifier, content: str):
    """check_content_quality handles arbitrary markdown content without crashing."""
    test_file = Path(verifier.project_root) / "test_company.md"
    test_file.write_text(content, encoding="utf-8")

    try:
        verifier.check_content_quality(str(test_file), content)
    except Exception as e:
        pytest.fail(f"check_content_quality raised {type(e).__name__}: {e} on input: {content[:200]!r}")


# ---------------------------------------------------------------------------
# Invariant 4: check_filename_format never raises
# ---------------------------------------------------------------------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=200, deadline=None)
@given(st.text(min_size=0, max_size=200))
def test_fuzz_check_filename_format_never_raises(verifier, filename: str):
    """check_filename_format handles arbitrary filename strings without crashing."""
    test_file = Path(verifier.project_root) / filename
    # Don't need to create the file

    try:
        verifier.check_filename_format(str(test_file))
    except Exception as e:
        pytest.fail(f"check_filename_format raised {type(e).__name__}: {e} on input: {filename!r}")


# ---------------------------------------------------------------------------
# Invariant 5: check_name_sync never raises
# ---------------------------------------------------------------------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=200, deadline=None)
@given(
    filename_st=_filename_st,
    normalized_name=st.text(min_size=0, max_size=100),
)
def test_fuzz_check_name_sync_never_raises(verifier, filename_st: str, normalized_name: str):
    """check_name_sync handles arbitrary filename/normalized_name pairs without crashing."""
    test_file = Path(verifier.project_root) / f"{filename_st}.md"
    data = {"normalized_name": normalized_name} if normalized_name else {}

    try:
        verifier.check_name_sync(str(test_file), data)
    except Exception as e:
        pytest.fail(f"check_name_sync raised {type(e).__name__}: {e} on input: filename={filename_st!r}, normalized_name={normalized_name!r}")


# ---------------------------------------------------------------------------
# Invariant 6: _norm_heading never raises and returns normalized string
# ---------------------------------------------------------------------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=500, deadline=None)
@given(st.text(min_size=0, max_size=200))
def test_fuzz_norm_heading_never_raises(verifier, heading: str):
    """_norm_heading normalizes arbitrary strings without crashing."""
    try:
        result = verifier._norm_heading(heading)
        assert isinstance(result, str)
        # Should be lowercase with only alnum and spaces
        assert result == result.lower()
    except Exception as e:
        pytest.fail(f"_norm_heading raised {type(e).__name__}: {e} on input: {heading!r}")


# ---------------------------------------------------------------------------
# Invariant 7: _heading_false_positive never raises and returns bool
# ---------------------------------------------------------------------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=500, deadline=None)
@given(
    st.text(min_size=0, max_size=100),
    st.text(min_size=0, max_size=100),
)
def test_fuzz_heading_false_positive_never_raises(verifier, h1: str, h2: str):
    """_heading_false_positive compares arbitrary headings without crashing."""
    try:
        result = verifier._heading_false_positive(h1, h2)
        assert isinstance(result, bool)
    except Exception as e:
        pytest.fail(f"_heading_false_positive raised {type(e).__name__}: {e} on input: {h1!r}, {h2!r}")


# ---------------------------------------------------------------------------
# Invariant 8: check_yaml_structure idempotency-ish (parsing same YAML twice)
# ---------------------------------------------------------------------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=100, deadline=None)
@given(_yaml_st)
def test_fuzz_check_yaml_structure_consistent(verifier, yaml_content: str):
    """Repeated parsing of same YAML yields consistent issue detection."""
    content = f"---\n{yaml_content}\n---\nBody content"
    test_file = Path(verifier.project_root) / "test.md"
    test_file.write_text(content, encoding="utf-8")

    # Capture one run as either a raised-exception type or the full issues
    # dict (so we can compare the *actual* issues, not just the bucket keys).
    def run_once():
        try:
            verifier.check_yaml_structure(str(test_file), content)
        except Exception as exc:
            # e.g. a YAML body that parses to a list/scalar raises
            # AttributeError downstream; that is a deterministic failure mode.
            return ("RAISED", type(exc).__name__)
        return ("OK", {k: list(v) for k, v in verifier.issues.items()})

    # Start from a clean slate so neither run inherits issues logged by
    # *previous* generated examples (the verifier fixture is reused across
    # examples). Both runs therefore capture only this example's own output.
    verifier.issues.clear()
    outcome1 = run_once()
    verifier.issues.clear()

    # Second run — must agree with the first (deterministic).
    outcome2 = run_once()
    assert outcome1 == outcome2, f"Inconsistent outcomes: {outcome1} vs {outcome2}"


# ---------------------------------------------------------------------------
# Invariant 9: check_content_quality handles edge cases
# ---------------------------------------------------------------------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=100, deadline=None)
@given(
    content_after_yaml=_content_st,
)
def test_fuzz_check_content_quality_various_bodies(verifier, content_after_yaml: str):
    """check_content_quality handles various body content after YAML."""
    content = f"---\ntitle: Test\ntype: company\n---\n{content_after_yaml}"
    test_file = Path(verifier.project_root) / "test_company.md"
    test_file.write_text(content, encoding="utf-8")

    try:
        verifier.check_content_quality(str(test_file), content)
    except Exception as e:
        pytest.fail(f"check_content_quality raised {type(e).__name__}: {e} on input: {content[:200]!r}")


# ---------------------------------------------------------------------------
# Invariant 10: Full verify_all on synthetic vault (smoke test)
# ---------------------------------------------------------------------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=20, deadline=None)
@given(st.integers(min_value=0, max_value=10))
def test_fuzz_verify_all_synthetic_vault(verifier, num_files: int):
    """verify_all processes a synthetic vault without crashing."""
    import random

    # Create a few synthetic markdown files
    types = ["company", "sector", "super_sector"]
    for i in range(num_files):
        t = random.choice(types)  # noqa: S311  # non-cryptographic deterministic RNG (graph algorithms/tests)
        fname = f"TestEntity{i}.md"
        test_file = Path(verifier.project_root) / fname
        content = f"""---
title: Test Entity {i}
type: {t}
normalized_name: TestEntity{i}
permalink: /{t}s/testentity{i}
tags:
  - entity_type/{t}
---
# Test Entity {i}

This is a test entity.
"""
        test_file.write_text(content, encoding="utf-8")

    try:
        result = verifier.verify_all()
        assert isinstance(result, int)  # Returns issue count
    except Exception as e:
        pytest.fail(f"verify_all raised {type(e).__name__}: {e}")

