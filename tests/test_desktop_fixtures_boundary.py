"""Publication-boundary guard for the committed desktop smoke fixtures.

`desktop/src-vue/test/fixtures.js` is generated from the live SQLite
sidecars and committed to a public repo, so it must carry content only
from git-tracked trees. The generator (src-vue/test/make_fixtures.py)
canonicalizes every docs-leg path and excludes the gitignored
local-notes tree; this test asserts the boundary on the ARTIFACT, so a
generator regression (or a hand edit that re-adds such rows) fails the
gate instead of shipping.

Mutation-verified twice: the pre-fix artifact (through cad9ec027~1)
fails on the raw prefix assertion; bypass shapes (`./doc/local/x`,
`doc/local`, absolute) are covered by the normalized sweep, which a
prefix-only generator regression cannot pass.
"""

import posixpath
import re
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "desktop/src-vue/test/fixtures.js"

_PATH_RE = re.compile(r'"path":\s*"([^"]+)"')


def test_fixtures_carry_only_tracked_tree_content():
    assert FIXTURES.is_file(), "desktop smoke fixtures missing — run `make fixtures` in desktop/"
    body = FIXTURES.read_text(encoding="utf-8")
    leaked = [line.strip()[:80] for line in body.splitlines() if '"doc/local/' in line]
    assert not leaked, (
        "committed desktop fixtures embed rows from the gitignored "
        f"local-notes tree ({len(leaked)} lines, first: {leaked[0]!r}) — "
        "regenerate via `make fixtures` after checking the generator's "
        "docs-leg exclusion"
    )


def test_fixtures_doc_paths_survive_normalization():
    """The prefix test above is not enough (MEDIUM-1, 272477d4a review):
    `./doc/local/x`, `doc/local`, and absolute paths bypass a raw
    substring check. Normalize every embedded docs path the way the
    generator is required to, then re-assert the boundary and
    containment."""
    assert FIXTURES.is_file()
    body = FIXTURES.read_text(encoding="utf-8")
    paths = _PATH_RE.findall(body)
    assert paths, "no docs paths found — fixtures stale or shape changed"
    bad = []
    for raw in paths:
        norm = posixpath.normpath(raw)
        if posixpath.isabs(norm) or norm.startswith(".."):
            bad.append(f"non-canonical: {raw!r}")
        elif norm == "doc/local" or norm.startswith("doc/local/"):
            bad.append(f"private tree: {raw!r}")
    assert not bad, (
        f"{len(bad)} fixture path(s) cross the publication boundary after "
        f"normalization (first: {bad[0]}) — regenerate via `make fixtures`"
    )
