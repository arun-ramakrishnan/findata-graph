#!/usr/bin/env python3
"""memory/.env loader — single home for all API credentials.

Consolidation (2026-08-25): per-secret dotfiles under memory/
(``finnhub_api.key``, ``paddle_api.key``) are retired; every credential
lives as ``NAME="value"`` in gitignored ``memory/.env`` instead. Modules
that need a secret call :func:`load_memory_env` once, then read
``os.environ`` normally — explicit ``--token`` flags still win because
the loader never overrides variables the caller already set.

python-dotenv is already a declared runtime dependency (pyproject.toml),
so this wrapper is deliberately thin: path pinning plus house semantics,
nothing more.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Repo root: helpers/core/env.py -> parents[2].
MEMORY_ENV = Path(__file__).resolve().parents[2] / "memory" / ".env"


def load_memory_env(
    env_file: Path | str | None = None,
    *,
    override: bool = False,
) -> bool:
    """Populate os.environ from memory/.env (or an explicit path).

    Returns True when the file existed and was read. Existing environment
    variables are preserved unless ``override=True`` — CLI-supplied
    secrets always take precedence over the file. Missing file is NOT an
    error: callers decide whether their secret is mandatory.
    """
    path = Path(env_file) if env_file is not None else MEMORY_ENV
    if not path.is_file():
        return False
    return load_dotenv(path, override=override)


def require_env(name: str, *, what: str) -> str:
    """Fetch a mandatory secret from os.environ with a house error."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"no {what}: set {name} in gitignored {MEMORY_ENV} or export it")
    return value


if __name__ == "__main__":
    import sys

    load_memory_env()
    names = [k for k in os.environ if k.endswith("_API_KEY") and k in MEMORY_ENV.read_text()]
    print(f"{MEMORY_ENV}: loaded ({len(names)} keys: {', '.join(names)})")
    sys.exit(0)
