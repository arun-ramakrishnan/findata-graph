"""Desktop security-posture pins (desktop_security_hardening S4).

Static assertions over the two Tauri shell configs, riding `make qa` so
the DESK-1/DESK-2 hardening cannot silently regress. These check the
DECLARED posture; runtime behaviour (zero console CSP violations, opener
rejections) is verified per-release under the smoke harness + release
binary pixel-proof, as in the original S6 shakedown.
"""

from __future__ import annotations

import json
from pathlib import Path

_TAURI = Path(__file__).parent.parent / "desktop/src-tauri"


def _conf() -> dict:
    return json.loads((_TAURI / "tauri.conf.json").read_text(encoding="utf-8"))


def _capabilities() -> dict:
    return json.loads((_TAURI / "capabilities/default.json").read_text(encoding="utf-8"))


def test_webview_csp_is_set_and_tight():
    """DESK-1: csp must be non-null, same-origin-default, and must not
    weaken script-src (the class that would turn any future injection
    into full IPC access)."""
    security = _conf()["app"]["security"]
    csp = security.get("csp")
    assert isinstance(csp, str) and csp, "csp must be a non-empty string, not null"
    directives = {}
    for part in csp.split(";"):
        tokens = part.split()
        if tokens:
            directives[tokens[0]] = tokens[1:]
    assert directives["default-src"] == ["'self'"]
    assert directives["script-src"] == ["'self'"]
    # ipc origins allowed only in connect-src
    for name, values in directives.items():
        if name != "connect-src":
            assert "ipc:" not in values and "http://ipc.localhost" not in values, name


def test_opener_url_permission_is_scheme_scoped():
    """DESK-2: the opener permission must carry an explicit https/http
    allow-scope at the capability layer — the frontend regex is UX, not
    the control."""
    perms = _capabilities()["permissions"]
    opener = [
        p for p in perms if isinstance(p, dict) and p.get("identifier") == "opener:allow-open-url"
    ]
    assert len(opener) == 1, "opener:allow-open-url must be the scoped object form"
    allowed = opener[0].get("allow", [])
    urls = sorted(str(e.get("url", "")) for e in allowed)
    assert urls == ["http://**", "https://**"], f"scheme-only scope expected, got {urls}"
    # the bare string form (scope-free) is exactly the regression class
    assert "opener:allow-open-url" not in [p for p in perms if isinstance(p, str)]
