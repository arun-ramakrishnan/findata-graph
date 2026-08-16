#!/usr/bin/env python3
"""Shared helpers for the PDF/markdown ingestion scripts.

`slugify` was duplicated (byte-identical) in `pdf_conv_md.py` and
`capture_newsletter_images.py`; it now lives here and is imported by both.
"""
from __future__ import annotations

import re


def slugify(stem: str) -> str:
    """Convert an arbitrary stem into a safe Obsidian filename fragment.

    spaces -> underscore; collapse runs; strip; keep existing case/underscores.
    """
    s = re.sub(r"\s+", "_", stem.strip())
    s = re.sub(r"__+", "_", s).strip("_")
    return s
