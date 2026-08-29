#!/usr/bin/env python3

"""Streaming zstd file compression for recovery artifacts.

House helper for the db-backup/ copies (db_maint, sidecar rebuilds):
compresses the WAL-consistent plain staging copy into ``<name>.zst``.
stdlib ``compression.zstd`` (PEP 784, Python 3.14) at the LIBRARY
DEFAULT level — per the #174 level policy there is deliberately no
explicit level switch (see
doc/improvements/proposals/zstd_binary_backups.md §2).

Manual recovery of a compressed backup:
  zstd -dc db-backup/research_backup.db.zst > memory/research.db
"""
from __future__ import annotations

import shutil
from pathlib import Path

_CHUNK = 1 << 20  # 1 MiB streaming chunks


def zst_path(path: Path) -> Path:
    """`backup.db` → `backup.db.zst` (append, not suffix-replace)."""
    return path.with_name(path.name + ".zst")


def compress_file(src: Path, dst: Path) -> int:
    """Stream-compress `src` into `dst`; returns the compressed size."""
    from compression import zstd

    with src.open("rb") as fin, zstd.open(dst, "wb") as fout:
        shutil.copyfileobj(fin, fout, _CHUNK)
    return dst.stat().st_size


def decompress_file(src: Path, dst: Path) -> int:
    """Stream-decompress `src` into `dst`; returns the decompressed size."""
    from compression import zstd

    with zstd.open(src, "rb") as fin, dst.open("wb") as fout:
        shutil.copyfileobj(fin, fout, _CHUNK)
    return dst.stat().st_size
