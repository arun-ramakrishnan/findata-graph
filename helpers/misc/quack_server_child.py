#!/usr/bin/env python3
"""Resident quack server child (duckdb_20_quack Slice B).

Opens ONE duckdb store read-write, serves it over the quack wire on
its own port, registers itself in the shared discovery file, and idles
until the stop file appears or SIGTERM arrives — then quack_stop,
discovery entry removal, clean close. Spawned detached by
helpers/misc/quack_servers.sh; never run interactively.

One store per server is forced by the wire protocol: quack_serve
exposes ONLY the primary database to clients (measured 2026-10-22 — a
second server-side ATTACH was invisible over the wire, and the
client's attach alias is a local label, so extra stores would be
silently mis-routed to the primary).

Lifecycle contract (mirrors the llama_servers.sh mechanism):
- discovery: {alias: {uri, token, pid, path, started_at}} — this child
  read-modify-writes ITS OWN alias entry under an exclusive fcntl lock
  (other children's entries are preserved). Readiness = entry present
  AND a wire client answers SELECT 1.
- stop: stop-file existence OR SIGTERM; both paths run quack_stop and
  remove the entry. A crash leaves the entry behind — every reader
  liveness-checks the pid (duckdb_shared.load_discovery), so a stale
  entry degrades to "no server", never to a bad route.

The read-write hold is the point: the server IS the single writer of
record while it runs; file-mode writers are refused by
duckdb_shared.assert_not_served (split-brain guard).
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from helpers.core import duckdb_shared as ds  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="store to serve (one server per store)")
    ap.add_argument("--alias", default="", help="discovery key (default: the store filename stem)")
    ap.add_argument("--discovery", required=True, help="shared discovery file")
    ap.add_argument(
        "--stop-file", required=True, help="existence of this file triggers a clean stop"
    )
    ap.add_argument(
        "--port",
        type=int,
        default=9494,
        help="quack wire port (default 9494; one port per server, tests use an explicit port)",
    )
    args = ap.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(f"quack-server: store absent: {db}", file=sys.stderr)
        return 2
    db = db.resolve()
    alias = args.alias or db.stem
    disc_path, stop_file = Path(args.discovery), Path(args.stop_file)
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.unlink(missing_ok=True)

    # Pre-flight: refuse to shadow-bind. quack_serve binds with
    # SO_REUSEPORT, so a second server on the same port SUCCEEDS at
    # binding and the kernel then load-balances client connections
    # between the two — each holding a different token (auth failures)
    # and a different catalog (wrong-store routing). Proven 2026-10-22:
    # a throwaway child shadow-bound the resident server on 9494 and
    # wire attaches failed ~50% of the time with
    # InvalidInputException: Authentication failed. Bind-probing is
    # useless here (it would succeed); ss is the kernel truth.
    listeners = subprocess.run(  # noqa: S603  — fixed argv, kernel truth for the SO_REUSEPORT guard
        ["/usr/bin/ss", "-ltnp", f"sport = :{args.port}"],
        capture_output=True,
        text=True,
    ).stdout
    foreign = [line for line in listeners.splitlines() if f"pid={os.getpid()}," not in line]
    if any("LISTEN" in line for line in foreign):
        holder = next(
            (seg for line in foreign if "LISTEN" in line for seg in line.split() if "pid=" in seg),
            "unknown",
        )
        print(
            f"quack-server: port {args.port} already served by another process "
            f"({holder}) — shadow-binding would split auth and routing. "
            "Stop it first (`make quack-stop`) or pass an explicit --port.",
            file=sys.stderr,
        )
        return 2

    stopping = False

    def _flag(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _flag)
    signal.signal(signal.SIGINT, _flag)

    # allow_unsigned_extensions is a connect-time config in duckdb 2.0 — the
    # server's con must carry it or every onager lane arriving over the wire
    # degrades to the graph fallbacks (helpers/graph/onager.py::connect_kwargs).
    try:
        from helpers.graph.onager import connect_kwargs

        con = duckdb.connect(str(db), **connect_kwargs())
    except ImportError:  # standalone child (tests) without the repo package
        con = duckdb.connect(str(db))
    con.execute("INSTALL quack")
    con.execute("LOAD quack")
    # Explicit port: the bare 'quack:localhost' form carries no port in
    # the returned uri, so clients cannot tell servers apart and would
    # all land on the default 9494. Pinning it keeps discovery unambiguous.
    served = con.execute(f"CALL quack_serve('quack:localhost:{args.port}')").fetchone()
    if served is None:
        raise RuntimeError("quack_serve returned no row — extension did not start")
    uri, _http, token = served
    entry = {
        "uri": uri,
        "token": token,
        "pid": os.getpid(),
        # Absolute path: clients resolve discovery entries against THEIR
        # cwd, which must not matter (a maint cron and an operator shell
        # live in different cwds but route to the same store).
        "path": str(db),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    # Register under the shared discovery file. fcntl-locked
    # read-modify-write: sibling children's entries must survive ours.
    fd, all_entries = ds.read_discovery_locked(disc_path)
    all_entries[alias] = entry
    ds.write_discovery_locked(fd, all_entries)

    try:
        while not stopping and not stop_file.exists():
            time.sleep(0.1)
    finally:
        try:
            con.execute("CALL quack_stop('quack:localhost')")
        except duckdb.Error as e:  # server may already be gone (SIGKILL path)
            print(f"quack-server: quack_stop failed: {e}", file=sys.stderr)
        try:
            fd, all_entries = ds.read_discovery_locked(disc_path)
            all_entries.pop(alias, None)
            ds.write_discovery_locked(fd, all_entries)
        except OSError:
            pass  # discovery already removed by the stop wrapper
        stop_file.unlink(missing_ok=True)
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
