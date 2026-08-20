"""
Security-regression tests (security proposal Phase 5,
the private security review under doc/local (untracked)).

One test per finding so a reintroduction fails the suite:

  SEC-1  /debug/entity removed (reflected XSS route)         -> TestSec1
  SEC-2  400 HTML fallback escapes the description           -> TestSec2
  SEC-3  CSP + nosniff headers; zero remote refs in templates-> TestSec3
  SEC-4  the three previously-unescaped frontend
         interpolations route through escapeHtml()            -> TestSec4
  SEC-6  image fetch skips non-https schemes                  (in
         tests/test_capture_newsletter_images.py)

Routes under test are filesystem/template-backed, so a bare test client
suffices (same pattern as test_api_docs.py); no DB seeding needed.
"""

from pathlib import Path

import pytest
from werkzeug.exceptions import BadRequest

import app as A

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = PROJECT_ROOT / "templates"
BUNDLE = PROJECT_ROOT / "static" / "findata.bundle.js"
ENTITY_JS = PROJECT_ROOT / "static" / "entity_detail.js"


@pytest.fixture
def client():
    with A.app.test_client() as c:
        yield c


class TestSec1DebugEntityRemoved:
    """SEC-1: the /debug/entity echo route is gone; anything there 404s."""

    @pytest.mark.parametrize("path", [
        "/debug/entity/HDFC%20Bank",
        "/debug/entity/%3Cscript%3Ealert(1)%3C/script%3E",
        "/debug/entity/anything/else",
    ])
    def test_debug_entity_404s(self, client, path):
        r = client.get(path)
        assert r.status_code == 404, "debug route must not exist"
        body = r.get_data(as_text=True)
        assert "<script>alert(1)" not in body


class TestSec2BadRequestEscape:
    """SEC-2: the non-API 400 fallback escapes the description."""

    def test_html_fallback_escapes_description(self):
        payload = "<script>alert(1)</script> & <b>bold</b>"
        with A.app.test_request_context("/some/page"):
            resp = A._api_bad_request(BadRequest(payload))
        html, code = resp
        assert code == 400
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "<b>bold</b>" not in html

    def test_json_path_returns_description_verbatim_but_jsonified(self, client):
        # /api paths return jsonify — JSON escaping is the client's concern.
        r = client.get("/api/search?q=%3Cscript%3E")  # malformed FTS query -> 400
        if r.status_code == 400:
            assert r.is_json


class TestSec3SecurityHeaders:
    """SEC-3: CSP + nosniff on every response; templates reference no CDNs."""

    CSP_EXPECTED = [
        "default-src 'self'",
        "script-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
    ]

    @pytest.mark.parametrize("path", ["/", "/findata"])
    def test_csp_and_nosniff_present(self, client, path):
        r = client.get(path)
        csp = r.headers.get("Content-Security-Policy", "")
        for fragment in self.CSP_EXPECTED:
            assert fragment in csp, f"CSP missing {fragment!r} on {path}"
        assert r.headers.get("X-Content-Type-Options") == "nosniff"

    def test_templates_have_no_remote_asset_refs(self):
        """Static scan: no http(s):// asset src/href may appear in templates."""
        import re

        offenders = []
        for tpl in TEMPLATES.glob("*.html"):
            text = tpl.read_text(encoding="utf-8")
            # asset-bearing tags only (script/link/img); plain <a href>
            # hyperlinks (e.g. the GitHub footer link) are fine.
            for m in re.finditer(
                r'<(?:script|link|img)\b[^>]+(?:src|href)="(https?://[^"]+)"', text
            ):
                offenders.append(f"{tpl.name}: {m.group(1)}")
        assert not offenders, f"remote asset refs found: {offenders}"

    def test_vendored_assets_exist(self):
        """Every vendor path referenced by templates exists on disk."""
        import re

        for tpl in TEMPLATES.glob("*.html"):
            text = tpl.read_text(encoding="utf-8")
            for m in re.finditer(r"filename='(vendor/[^']+)'", text):
                assert (PROJECT_ROOT / "static" / m.group(1)).is_file(), m.group(1)


class TestSec4FrontendEscapes:
    """SEC-4: the three previously-unescaped sites use escapeHtml()."""

    def test_bundle_escapes_entity_type_and_sector(self):
        bundle = BUNDLE.read_text(encoding="utf-8")
        assert "escapeHtml(entity.entity_type" in bundle
        assert "escapeHtml(entity.sector_classification" in bundle
        # and no bare interpolation remains at those sites
        assert '${entity.entity_type || "company"}' not in bundle
        assert '${entity.sector_classification || "Unknown"}' not in bundle

    def test_entity_detail_js_escapes_type_badge(self):
        js = ENTITY_JS.read_text(encoding="utf-8")
        assert "escapeHtml(this.entity.entity_type || 'Entity')" in js
