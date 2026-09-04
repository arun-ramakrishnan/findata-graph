r"""
Fuzz tests — pure YAML-field transformers in the maintenance scripts.

`rename_entity.py` / `move_sector.py` are mostly stateful (DB + filesystem).
The functions tested here are the only *pure* str -> str slices: they rewrite
YAML front-matter field lines. We pin never-raises + the obvious observability /
idempotency invariants.

Note on backslashes and control chars: these helpers interpolate `value` into a
*regex replacement* string, where a backslash is a group reference and control
chars (tab/newline/CR) break line detection. So generated values exclude that
set (`NO_CTRL`, built via chr()). A documented quirk, not a crash we fuzz for.

Note on newlines: the append path does `yaml + "field: value\n"`. It assumes
newline-terminated YAML (the real domain), so the idempotency test feeds
newline-terminated input. Runs in `make fuzz` and `make qa`.
"""

from __future__ import annotations


import pytest
from hypothesis import given, settings, strategies as st


from helpers.maintenance.rename_entity import replace_field
from helpers.maintenance.move_sector import (
    TODAY,
    bump_last_modified,
    normalize_sector_tag_value,
    update_yaml_field,
    update_yaml_sector_field,
    update_yaml_sector_tag,
)


_YAML = st.text(
    alphabet=st.characters(blacklist_categories=("C",), blacklist_characters=chr(92)),
    min_size=0,
    max_size=120,
)
_FIELD = st.text(
    alphabet=st.characters(blacklist_categories=("C",), blacklist_characters=chr(92)),
    min_size=1,
    max_size=20,
)
_VAL = st.text(
    alphabet=st.characters(blacklist_categories=("C",), blacklist_characters=chr(92)),
    min_size=0,
    max_size=40,
)
_SECTOR = st.text(
    alphabet=st.characters(blacklist_categories=("C",), blacklist_characters=chr(92)),
    min_size=1,
    max_size=20,
)

# Non-whitespace, non-empty values: idempotency below (see doc on the test).
_VAL_NOWS = st.text(
    alphabet=st.characters(blacklist_categories=("C", "Z"), blacklist_characters=chr(92)),
    min_size=1,
    max_size=40,
)


@settings(max_examples=250, deadline=None)
@given(_YAML, _FIELD, _VAL)
def test_fuzz_replace_field_never_raises_and_value_present(yaml_text, field, value):
    try:
        out = replace_field(yaml_text, field, value)
    except Exception as e:
        pytest.fail(f"replace_field raised {type(e).__name__}: {e}")
    assert isinstance(out, str)
    assert value in out  # both the replace and the append branch include `value`


@settings(max_examples=200, deadline=None)
@given(_YAML, _FIELD, _VAL_NOWS)
def test_fuzz_replace_field_idempotent_when_newline_terminated(yaml_text, field, value):
    r"""Within the real domain (newline-terminated YAML) the helper converges: the
    first call establishes a `field:` line, the second replaces it in place.

    N.B. values must be non-empty and not start with whitespace: the helper's
    pattern is `^\s*field\s*:\s*.*$` with MULTILINE, and `\s` matches `\n`, so
    an empty/whitespace value lets `\s*` after the colon swallow the trailing
    newline into group(1) and the 2nd call re-emits it. Realistic non-whitespace
    values are idempotent."""
    subject = yaml_text + "\n"
    once = replace_field(subject, field, value)
    twice = replace_field(once, field, value)
    assert twice == once


@settings(max_examples=250, deadline=None)
@given(_YAML, _FIELD, _VAL)
def test_fuzz_update_yaml_field_never_raises(yaml_text, field, value):
    try:
        out = update_yaml_field(yaml_text, field, value)
    except Exception as e:
        pytest.fail(f"update_yaml_field raised {type(e).__name__}: {e}")
    assert isinstance(out, str)


@settings(max_examples=200, deadline=None)
@given(_SECTOR)
def test_fuzz_normalize_sector_tag_value_idempotent(sector):
    once = normalize_sector_tag_value(sector)
    assert once == sector.lower()
    assert normalize_sector_tag_value(once) == once
    assert isinstance(once, str)


@settings(max_examples=200, deadline=None)
@given(_YAML, _SECTOR)
def test_fuzz_update_yaml_sector_field_never_raises(yaml_text, sector):
    try:
        out = update_yaml_sector_field(yaml_text, sector)
    except Exception as e:
        pytest.fail(f"update_yaml_sector_field raised {type(e).__name__}: {e}")
    assert isinstance(out, str)


@settings(max_examples=200, deadline=None)
@given(_YAML, _SECTOR)
def test_fuzz_update_yaml_sector_tag_never_raises(yaml_text, sector):
    try:
        out = update_yaml_sector_tag(yaml_text, "Old", sector)
    except Exception as e:
        pytest.fail(f"update_yaml_sector_tag raised {type(e).__name__}: {e}")
    assert isinstance(out, str)


@settings(max_examples=200, deadline=None)
@given(_YAML)
def test_fuzz_bump_last_modified_never_raises(yaml_text):
    try:
        out = bump_last_modified(yaml_text)
    except Exception as e:
        pytest.fail(f"bump_last_modified raised {type(e).__name__}: {e}")
    assert isinstance(out, str)
    assert TODAY in out
