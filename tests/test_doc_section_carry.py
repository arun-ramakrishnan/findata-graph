# Tests for rebuild_doc_search._file_rows per-section carry
"""S2 of doc_leg_embed_cache_persistence (2026-10-17): a doc file whose
mtime/hash changed must re-embed only its NEW/CHANGED sections — unchanged
sections reuse the stored embedding bytes verbatim while title, anchor
(line number), meta, links and trail are rebuilt fresh. The 2026-10-17
completed.md edit (30 lines, ~400 sections) cost ~14 min of CPU because
carry was file-grained; these tests pin the section-grained contract."""

from helpers.maintenance.rebuild_doc_search import _file_rows

DOC_V1 = """---
title: "Ledger"
---

# Ledger

Preamble body.

## Alpha

alpha body one

## Beta

beta body two
"""


def _embed_calls():
    calls: list[str] = []

    def fn(text: str) -> list[float]:
        calls.append(text)
        return [float(len(text)), 1.0]

    return fn, calls


def _write(tmp_path, text):
    d = tmp_path / "docs"
    d.mkdir(exist_ok=True)
    f = d / "ledger.md"
    f.write_text(text, encoding="utf-8")
    return f, "ledger.md"


def _by_section(rows):
    return {r[1]: r for r in rows}


def test_cold_pass_embeds_every_section(tmp_path):
    f, rel = _write(tmp_path, DOC_V1)
    fn, calls = _embed_calls()
    rows, chash, carried = _file_rows(f, rel, fn, gemma=True)
    assert carried == 0
    assert len(rows) == 3  # preamble chunk + Alpha + Beta
    assert len(calls) == 3
    assert chash


def test_unchanged_sections_carry_their_embeddings(tmp_path):
    f, rel = _write(tmp_path, DOC_V1)
    fn, calls = _embed_calls()
    rows1, _, _ = _file_rows(f, rel, fn, gemma=True)
    # Insert a new section ABOVE Alpha: Alpha/Beta bodies are unchanged but
    # their anchors shift — vectors carry, rows rebuild. The preamble chunk
    # is unchanged too, so 3 of 4 sections carry; only Zero embeds.
    f.write_text(
        DOC_V1.replace("## Alpha", "## Zero\n\nzero body\n\n## Alpha"),
        encoding="utf-8",
    )
    rows2, _, carried = _file_rows(f, rel, fn, gemma=True, prev_rows=rows1)
    assert carried == 3
    assert len(calls) == 4  # 3 cold + 1 new
    by1, by2 = _by_section(rows1), _by_section(rows2)
    assert by2["Alpha"][5] == by1["Alpha"][5]  # byte-identical stored vector
    assert by2["Beta"][5] == by1["Beta"][5]
    assert by2["Zero"][5] != by1["Alpha"][5]  # new section really embedded
    assert by2["Alpha"][3] != by1["Alpha"][3]  # anchor = line number, rebuilt
    assert set(by2) == {"", "Alpha", "Beta", "Zero"}


def test_changed_section_reembeds_changed_only(tmp_path):
    f, rel = _write(tmp_path, DOC_V1)
    fn, calls = _embed_calls()
    rows1, _, _ = _file_rows(f, rel, fn, gemma=True)
    f.write_text(DOC_V1.replace("alpha body one", "alpha body EDITED"), encoding="utf-8")
    rows2, _, carried = _file_rows(f, rel, fn, gemma=True, prev_rows=rows1)
    assert carried == 2  # preamble + Beta carried
    assert len(calls) == 4  # 3 cold + Alpha re-embedded
    by1, by2 = _by_section(rows1), _by_section(rows2)
    assert by2["Beta"][5] == by1["Beta"][5]
    assert by2["Alpha"][5] != by1["Alpha"][5]


def test_title_change_carries_nothing(tmp_path):
    # For frontmatter'd files _doc_title falls back to the filename stem,
    # and the stem rides in every _doc_basis — a renamed file (new stem,
    # same content shape) changes every basis, so nothing may carry.
    f, _rel = _write(tmp_path, DOC_V1)
    fn, calls = _embed_calls()
    rows1, _, _ = _file_rows(f, "ledger.md", fn, gemma=True)
    f.write_text(DOC_V1, encoding="utf-8")
    rows2, _, carried = _file_rows(f, "ledger_v2.md", fn, gemma=True, prev_rows=rows1)
    assert carried == 0
    assert len(calls) == 6  # 3 cold + 3 re-embedded


def test_read_error_contract_keeps_triple(tmp_path):
    missing = tmp_path / "docs" / "absent.md"
    (tmp_path / "docs").mkdir(exist_ok=True)
    rows, chash, carried = _file_rows(missing, "absent.md", lambda t: [0.0], gemma=True)
    assert rows == [] and chash is None and carried == 0
