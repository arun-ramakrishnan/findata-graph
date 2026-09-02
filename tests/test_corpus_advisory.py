"""Advisory: flag full findata walkers not using Corpus and derive_* without --stale-only.

S1b Corpus (helpers/core/corpus.py) and S1c --stale-only are advisory, not gating:
not every helper needs Corpus (verify_notes 0.43s ThreadPool already, note_search bge 1.56s hot is bge not yaml),
but future findata walkers should think incremental from S1. Like advisory ty-tests (nonblocking), this test
prints WARNING tail but does not FAIL the gate — it nudges.

- rglob("*.md") over findata without from helpers.core.corpus import -> WARNING
- helpers/graph/derive_*.py without --stale-only -> WARNING
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# Walkers that legitimately need their own rglob (exempt from Corpus advisory)
_EXEMPT_RGLOB = {
    "helpers/core/corpus.py",  # the Corpus itself
    "helpers/core/fs_walk.py",  # the safe walker Corpus uses
    "helpers/maintenance/rebuild_note_search.py",  # bge hot, not yaml
    "helpers/maintenance/rebuild_doc_search.py",  # doc/ not findata
    "helpers/maintenance/rebuild_script_search.py",  # helpers/Mojo not findata
}
# derive modules that are expected to have --stale-only (S1c)
_EXPECT_STALE = {
    "helpers/graph/derive_themes.py",
    "helpers/graph/derive_cited_in.py",
    "helpers/graph/derive_insights.py",
    # derive_co_mentions / derive_events / derive_themes etc. are next candidates
}


def test_corpus_advisory_findata_walkers():
    """Advisory: full findata rglob without Corpus import -> WARNING not FAIL."""
    warnings: list[str] = []
    for p in REPO_ROOT.rglob("helpers/**/*.py"):
        rel = p.relative_to(REPO_ROOT).as_posix()
        if rel in _EXEMPT_RGLOB:
            continue
        # Only flag likely findata walkers (graph/validators/core that touch Companies/Sectors/findata)
        if not any(s in rel for s in ("graph/", "validators/", "core/", "maintenance/")):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if 'rglob("*.md")' in txt or 'rglob("*.md' in txt or "rglob('*.md')" in txt:
            if "from helpers.core.corpus import" not in txt and "import Corpus" not in txt:
                # Heuristic: file mentions Companies/findata/Sectors or is a known walker
                if any(
                    k in txt
                    for k in (
                        "findata",
                        "Companies",
                        "Sectors",
                        "COMPANIES_DIR",
                        "DERIVED_TREES",
                        "FINDATA",
                    )
                ):
                    warnings.append(rel)
    if warnings:
        print(
            "\n[advisory] findata rglob without Corpus (S1b) — consider Corpus.load(use_cache=True):"
        )
        for w in warnings[:20]:
            print(f"  - {w}")
        print(
            f"  total {len(warnings)} file(s) — advisory, not gating (see doc/templates/python_module.py S1b)."
        )
    # Advisory: never fail the gate, just warn
    assert True


def test_stale_only_advisory_derive():
    """Advisory: derive_* without --stale-only -> WARNING not FAIL."""
    warnings: list[str] = []
    for p in REPO_ROOT.glob("helpers/graph/derive_*.py"):
        rel = p.relative_to(REPO_ROOT).as_posix()
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "--stale-only" not in txt:
            warnings.append(rel)
    if warnings:
        print(
            "\n[advisory] derive_* without --stale-only (S1c) — consider MAX(created_at) vs max(mtime) SKIP 0.12s:"
        )
        for w in warnings:
            print(f"  - {w}")
        print(
            f"  total {len(warnings)} file(s) — advisory, not gating (see doc/templates/python_module.py S1c)."
        )
    assert True
