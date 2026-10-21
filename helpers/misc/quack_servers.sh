#!/usr/bin/env bash
# Start/stop/status for the resident quack servers (duckdb_20_quack Slice B)
# in the llama_servers.sh shape.
#
#   helpers/misc/quack_servers.sh start [store.duckdb ...]   # default: memory/graph.duckdb
#   helpers/misc/quack_servers.sh stop                       # exact-PID TERM + discovery cleanup
#   helpers/misc/quack_servers.sh status
#   helpers/misc/quack_servers.sh restart [store.duckdb ...]
#
# ONE SERVER PER STORE (quack_serve exposes only the primary database
# over the wire — a shared server silently routes every client to its
# primary, measured 2026-10-22), so N stores mean N detached children,
# each on its own port (QUACK_PORT_BASE + index). The shared discovery
# file memory/.quack.json is a JSON object keyed by alias,
# {alias: {uri, token, pid, path, started_at}}; each child registers
# its own entry under an fcntl lock and removes it on stop.
# stdout+stderr stream to $TMPDIR/quack-server.log; each start rotates
# the previous log to .prev so restarts never destroy failure evidence.
#
# Split-brain rule (proposal §2.6): while a server runs, file-mode
# writers on the served stores are refused by
# helpers/core/duckdb_shared.py::assert_not_served — stop the server
# first (`make quack-stop`) or route the write over the wire.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
TMP="${TMPDIR:-/mnt/data/tmp}"
LOG="$TMP/quack-server.log"
DISCOVERY="$REPO/memory/.quack.json"
STOP_DIR="${TMPDIR:-/mnt/data/tmp}/quack-server.stops"
CHILD="helpers/misc/quack_server_child.py"
PY="${PYTHON:-$REPO/.venv/bin/python3}"
DEFAULT_DBS=("memory/graph.duckdb")
PORT_BASE="${QUACK_PORT_BASE:-9494}"

# All pids currently registered in discovery (live ones only).
server_pids() {
    [ -f "$DISCOVERY" ] || return 1
    "$PY" -c "
import json, os, sys
try:
    disc = json.load(open('$DISCOVERY'))
except Exception:
    sys.exit(1)
pids = []
for e in disc.values() if isinstance(disc, dict) else []:
    if isinstance(e, dict):
        try:
            pid = int(e['pid']); os.kill(pid, 0); pids.append(str(pid))
        except (OSError, ValueError, TypeError, KeyError):
            pass
print(' '.join(pids))
" 2>/dev/null
}

wait_gone() {  # $1=pid, $2=deadline seconds
    local i=0
    while kill -0 "$1" 2>/dev/null; do
        i=$((i + 1))
        if [ "$i" -ge "$2" ]; then return 1; fi
        sleep 1
    done
    return 0
}

do_start() {
    shift || true
    local dbs=("$@")
    [ "${#dbs[@]}" -eq 0 ] && dbs=("${DEFAULT_DBS[@]}")
    for db in "${dbs[@]}"; do
        if [ ! -f "$REPO/$db" ] && [ ! -f "$db" ]; then
            echo "quack-server: store absent: $db" >&2
            exit 2
        fi
    done
    # Idempotent per store: a store already served (live entry) is
    # skipped, so `start` after a partial start completes the set.
    local served
    served=$("$PY" -c "
import json, os, sys
try:
    disc = json.load(open('$DISCOVERY'))
except Exception:
    raise SystemExit
for e in (disc.values() if isinstance(disc, dict) else []):
    if isinstance(e, dict):
        try: os.kill(int(e['pid']), 0)
        except (OSError, ValueError, TypeError, KeyError): continue
        print(e.get('path', ''))
" 2>/dev/null || true)
    local todo=()
    local db
    for db in "${dbs[@]}"; do
        local want="$REPO/$db"
        [ -f "$db" ] && want="$db"
        want="$(cd "$(dirname "$want")" && pwd)/$(basename "$want")"
        if grep -qxF "$want" <<<"${served:-}"; then
            echo "quack-server: $db already served — skipping"
        else
            todo+=("$db")
        fi
    done
    [ "${#todo[@]}" -eq 0 ] && { echo "quack-server: all requested stores already served"; return 0; }
    # Rotate the previous log: restarts never destroy failure evidence.
    if [ -f "$LOG" ]; then mv -f "$LOG" "$LOG.prev"; fi
    local i=0 db_args
    for db in "${todo[@]}"; do
        local port=$((PORT_BASE + i))
        i=$((i + 1))
        echo "starting quack server for $db on port $port (log $LOG) ..."
        setsid bash -c "cd \"$REPO\" && exec \"$PY\" $CHILD --db \"$db\" \
            --discovery \"$DISCOVERY\" --stop-file \"$STOP_DIR/$db.stop\" --port \"$port\" \
            >> \"$LOG\" 2>&1" </dev/null >/dev/null 2>&1 &
    done
    # Readiness = every requested store has a live entry AND answers
    # SELECT 1 over the wire.
    local db_paths=()
    for db in "${todo[@]}"; do db_paths+=("$REPO/$db"); done
    if "$PY" - "${db_paths[@]}" <<PYEOF
import sys, time
sys.path.insert(0, "$REPO")
from helpers.core import duckdb_shared as ds
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    if all(ds.wait_healthy(discovery="$DISCOVERY", deadline_s=1, path=p) for p in sys.argv[1:]):
        sys.exit(0)
sys.exit(1)
PYEOF
    then
        echo "quack servers healthy (discovery $DISCOVERY)"
    else
        echo "quack servers failed to become healthy in 60s — see $LOG" >&2
        return 1
    fi
}

do_stop() {
    local pids
    if ! pids=$(server_pids) || [ -z "$pids" ]; then
        rm -rf "$STOP_DIR" 2>/dev/null; rm -f "$DISCOVERY" 2>/dev/null || true  # stale discovery is trash, not state
        echo "no quack server running"
        return 0
    fi
    echo "stopping quack servers (pids $pids) ..."
    local pid
    for pid in $pids; do
        kill "$pid" 2>/dev/null || true
    done
    for pid in $pids; do
        if ! wait_gone "$pid" 15; then
            echo "pid $pid still alive after 15s — kill -KILL" >&2
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
    rm -f "$DISCOVERY"; rm -rf "$STOP_DIR"
    echo "quack servers stopped (discovery removed)"
}

do_status() {
    local pids
    if ! pids=$(server_pids) || [ -z "$pids" ]; then
        echo "quack servers: down (no live discovery at $DISCOVERY)"
        return 0
    fi
    local dbs
    dbs=$("$PY" -c "
import json
disc = json.load(open('$DISCOVERY'))
print(', '.join(f\"{a}={e['path']}\" for a, e in sorted(disc.items()) if isinstance(e, dict)))" 2>/dev/null || true)
    local health="down"
    if "$PY" "$HERE/quack_health.py" --discovery "$DISCOVERY" >/dev/null 2>&1; then health="ok"; fi
    echo "quack servers: pids [$pids] health=$health dbs=[$dbs]"
}

case "${1:-status}" in
    start) do_start "$@" ;;
    stop) do_stop ;;
    status) do_status ;;
    restart) do_stop; do_start "$@" ;;  # do_start shifts the action word itself; "${@:2}" here would drop the FIRST store (silently served one fewer db, seen 2026-10-22)
    *) echo "usage: $0 {start [store.duckdb ...]|stop|status|restart [store.duckdb ...]}" >&2; exit 2 ;;
esac
