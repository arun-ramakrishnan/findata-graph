"""Fuzz tests — get_tickers formatting / path helpers.

Property-based tests (via Hypothesis) for the pure, never-crash helpers in
`helpers/core/get_tickers.py`. The display formatters are fed raw yfinance
data (None, NaN, inf, strings, Decimals, ...) and must always return a string
without raising; the path helper must survive arbitrary file paths.

Runs in `make fuzz` and `make qa`. No network / DB required (only the module
import, which pulls in yfinance).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "helpers"))

from core.get_tickers import (  # noqa: E402
    _fmt_number,
    _fmt_pct,
    extract_company_name_from_path,
)

# Heterogeneous "value" stream a formatter might receive from yfinance / JSON.
_VAL = st.one_of(
    st.none(),
    st.integers(-(10**12), 10**12),
    st.floats(allow_nan=True, allow_infinity=True, width=64),
    st.booleans(),
    st.decimals(min_value=-(10**6), max_value=10**6, allow_nan=False),
    st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=12),
)
_FMT = st.text(alphabet=st.characters(max_codepoint=0x2000), max_size=3)
_PATH = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"), max_size=200
)


# ---------------------------------------------------------------------------
# 1. _fmt_number — never raises, always str, across heterogeneous values
# ---------------------------------------------------------------------------
@settings(
    max_examples=400, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(val=_VAL, prefix=_FMT, suffix=_FMT)
def test_fmt_number_never_raises(val, prefix, suffix):
    try:
        result = _fmt_number(val, prefix, suffix)
    except Exception as e:
        pytest.fail(f"_fmt_number raised {type(e).__name__}: {e} on {val!r}")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 2. _fmt_pct — never raises, always str
# ---------------------------------------------------------------------------
@settings(
    max_examples=400, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(val=_VAL)
def test_fmt_pct_never_raises(val):
    try:
        result = _fmt_pct(val)
    except Exception as e:
        pytest.fail(f"_fmt_pct raised {type(e).__name__}: {e} on {val!r}")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 3. extract_company_name_from_path — never raises, returns str
# ---------------------------------------------------------------------------
@settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(_PATH)
def test_extract_company_name_from_path_never_raises(path: str):
    try:
        result = extract_company_name_from_path(path)
    except Exception as e:
        pytest.fail(f"extract_company_name_from_path raised {type(e).__name__}: {e} on {path!r}")
    assert isinstance(result, str)


@settings(
    max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(_PATH)
def test_extract_company_name_strips_extension_and_underscores(path: str):
    """The helper drops the extension and turns underscores into spaces."""
    result = extract_company_name_from_path(path)
    assert ".md" not in result and ".txt" not in result
    assert "_" not in result
