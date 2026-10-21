#!/usr/bin/env python3
"""One-shot health probe for the resident quack servers (duckdb_20_quack
Slice B — `make quack-health`), in the llamacpp_health.py shape.

One server per store: iterates every live discovery entry
(`memory/.quack.json`, {alias: {uri, token, pid, path}}, entries for
dead pids dropped), wire-attaches each on its own connection, and
asserts the served store actually answers:

- identity: `SELECT 1` round-trip per server;
- routing coherence: the server's own store file is held rw (the
  read-write hold is the server's identity) — recorded by the entry's
  absolute path; a live pid + wire SELECT 1 is the contract;
- catalog resolution: duckdb_tables() resolves on the attached
  catalog (0 tables is legal for a fresh store — the assertion is
  that the catalog query resolves, not that rows exist).

Exit 0 only when everything answers. Usable from maint / cron /
operator shell; read-only over loopback. With no live servers this is
a clean, immediately-failing probe (exit 1, one line) — never a hang.

Usage: python3 helpers/misc/quack_health.py [--discovery memory/.quack.json]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from helpers.core import duckdb_shared as ds  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--discovery", default=None, help="discovery file (default memory/.quack.json)")
    args = ap.parse_args(argv)

    disc = ds.load_discovery(Path(args.discovery) if args.discovery else None)
    if not disc:
        print(f"FAIL quack: no live server (discovery {args.discovery or ds.discovery_path()})")
        return 1

    errs: list[str] = []
    for alias, entry in sorted(disc.items()):
        try:
            con = ds._attach_wire(entry, alias)
        except Exception as e:  # noqa: BLE001 — probe reports, never raises
            errs.append(f"{alias!r} ({entry.get('uri')}) wire attach failed: {e}")
            continue
        try:
            con.execute("SELECT 1").fetchall()
            con.execute(
                f"SELECT COUNT(*) FROM duckdb_tables() WHERE database_name = {ds.sql_lit(alias)}"  # noqa: S608  — alias is a code constant via ds.sql_lit
            ).fetchone()
        except Exception as e:  # noqa: BLE001 — probe reports, never raises
            errs.append(f"{alias!r} probe failed: {e}")
        finally:
            con.close()

    if errs:
        for e in errs:
            print(f"FAIL quack: {e}")
        return 1
    pids = ",".join(str(e.get("pid")) for e in disc.values())
    print(f"ok   quack dbs={sorted(disc)} pids=[{pids}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
