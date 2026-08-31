"""Fuzz tests — Newsletter parsers (parse_newsletter.py).

Property-based tests (via Hypothesis) for the pure, untrusted-input surfaces
in `helpers/core/parse_newsletter.py`. These functions consume OCR'd / hand-
written newsletter markdown and must never crash, and must uphold structural
invariants:

1. extract_companies      - yields only (name, line) tuples with valid shapes.
2. guess_sector_for       - returns str|None, and any non-None result is a
                            member of the supplied `sector_dirs` set.
3. render_stub            - never raises; with well-formed entity inputs the
                            emitted frontmatter is valid YAML (round-trips
                            through yaml.safe_load and keeps type: company).

Runs in `make fuzz` and `make qa`. No DB / network required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings, strategies as st, HealthCheck

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "helpers"))

from core.parse_newsletter import (  # noqa: E402
    extract_companies,
    guess_sector_for,
    render_stub,
)

# Sectors that appear in the real 42-sector taxonomy. "Diversified" is the
# documented fallback the classifier returns when no keyword matches.
SECTOR_POOL = [
    "Banking",
    "NBFC",
    "Insurance",
    "Capital_Markets",
    "Fintech_Payments",
    "Financial_Services",
    "Pharma",
    "Healthcare",
    "Hospitals",
    "Diagnostics",
    "Renewables",
    "Energy",
    "Technology",
    "IT_Services",
    "Manufacturing",
    "Automobile",
    "Consumer",
    "Retail",
    "Real_Estate",
    "Diversified",
]

# Arbitrary markdown / prose (surrogate codepoints excluded — they can't be
# written to a file anyway; CR excluded so line counting is stable).
_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
    min_size=0,
    max_size=2000,
)


# ---------------------------------------------------------------------------
# 1. extract_companies — shape + never raises
# ---------------------------------------------------------------------------
@settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(_TEXT)
def test_extract_companies_shape(content: str):
    """Every yielded item is a (name:str>=3, line:int>=1) pair in-range."""
    n_lines = content.count("\n") + 1
    for name, line in extract_companies(content):
        assert isinstance(name, str), f"name not str: {name!r}"
        assert len(name) >= 3, f"name too short: {name!r}"
        assert isinstance(line, int) and line >= 1, f"bad line: {line!r}"
        assert line <= n_lines, f"line {line} exceeds {n_lines} lines"


# ---------------------------------------------------------------------------
# 2. guess_sector_for — returns a member of sector_dirs or None
# ---------------------------------------------------------------------------
@settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    content_window=_TEXT,
    sector_dirs=st.sets(st.sampled_from(SECTOR_POOL), max_size=len(SECTOR_POOL)),
)
def test_guess_sector_for_in_dirs_or_none(content_window: str, sector_dirs: set):
    """Returned sector (if any) is guaranteed to be in the allowed set."""
    try:
        result = guess_sector_for("unused-name", content_window, sector_dirs)
    except Exception as e:
        pytest.fail(
            f"guess_sector_for raised {type(e).__name__}: {e} "
            f"on window={content_window!r}, dirs={sector_dirs!r}"
        )
    assert result is None or isinstance(result, str)
    assert result is None or result in sector_dirs, (
        f"guess_sector_for returned {result!r} not in {sector_dirs!r}"
    )


# ---------------------------------------------------------------------------
# 3. render_stub — never raises + emits valid YAML frontmatter
# ---------------------------------------------------------------------------
@settings(
    max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(_TEXT)
def test_render_stub_never_raises(content: str):
    """render_stub is a pure string builder and must not raise."""
    try:
        out = render_stub(content, "Entity_Name", "Energy", None, "/x/y")
    except Exception as e:
        pytest.fail(f"render_stub raised {type(e).__name__}: {e}")
    assert isinstance(out, str) and "title:" in out


# Constrained, entity-like inputs so the generated frontmatter is real YAML.
_SAFE_TXT = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=" -_"),
    min_size=1,
    max_size=40,
)
_TICKER = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="._-"),
        min_size=1,
        max_size=12,
    ),
)


@settings(
    max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    name=_SAFE_TXT,
    normalized_name=_SAFE_TXT,
    sector=st.sampled_from(SECTOR_POOL),
    ticker=_TICKER,
)
def test_render_stub_valid_yaml(name, normalized_name, sector, ticker):
    """With well-formed inputs the frontmatter round-trips as YAML."""
    doc = render_stub(name, normalized_name, sector, ticker, "/companies/x")
    # frontmatter sits between the first and second '---' delimiters
    fm = doc.split("---", 2)[1]
    try:
        data = yaml.safe_load(fm)
    except Exception as e:
        pytest.fail(f"render_stub produced invalid YAML: {e}\n--- frontmatter ---\n{fm}")
    assert isinstance(data, dict)
    assert data.get("type") == "company"
