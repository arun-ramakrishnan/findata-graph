#!/usr/bin/env bash
# Start/stop/status for the owned llama-servers (canonical flags live in
# the Makefile targets — this script never carries model flags itself).
#
#   helpers/misc/llama_servers.sh start [--teleocr]   # gemma :8732 + granite :8733 (teleocr :8731 opt-in only)
#   helpers/misc/llama_servers.sh stop                # all three legs, exact-PID TERM + port-free wait
#   helpers/misc/llama_servers.sh status              # processes + per-port health
#   helpers/misc/llama_servers.sh restart [--teleocr]
#
# Detached via setsid (survives the invoking shell). Server stdout+stderr
# stream to $TMPDIR/<target>.log (canonical names: embgemma-server.log,
# granite-server.log, teleocr-server.log); each start rotates the previous
# log to <target>.log.prev so restarts never destroy failure evidence.
# Teleocr is NOT started by default (OCR is on-demand; the embedding legs
# are the always-on pair).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
TMP="${TMPDIR:-/mnt/data/tmp}"

# leg -> "make-target port"
LEGS_MAIN=("embgemma-server 8732" "granite-server 8733")
LEG_TELEOCR="teleocr-server 8731"

pids_for_port() {  # server binary only: anchored so the make/sh
    # wrapper (whose cmdline merely MENTIONS the binary) never matches
    pgrep -f "^models/llamacpp/bin/llama-server.*--port $1" 2>/dev/null || true
}

holders_of_port() {  # kernel truth: pids actually holding the listen socket
    # NOTE: trailing `|| true` is load-bearing — under `set -euo pipefail`
    # a no-match grep exits 1 and would otherwise kill the caller mid-loop
    # (2026-10-10: `stop` died silently with rc=1 on the first DOWN port).
    ss -ltnp 2>/dev/null | grep -E ":$1( |$)" | grep -oE "pid=[0-9]+" | cut -d= -f2 | sort -u | tr '\n' ' ' || true
}

wait_port_free() {  # $1=port, $2=deadline seconds
    local port=$1 deadline=$2 i=0
    while ss -ltn 2>/dev/null | grep -q ":$port"; do
        i=$((i + 1))
        if [ "$i" -ge "$deadline" ]; then return 1; fi
        sleep 1
    done
    return 0
}

wait_healthy() {  # $1=port, $2=deadline seconds
    local port=$1 deadline=$2 i=0 code=""
    while [ "$i" -lt "$deadline" ]; do
        code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$port/health" 2>/dev/null || true)
        if [ "$code" = "200" ]; then return 0; fi
        i=$((i + 1))
        sleep 2
    done
    return 1
}

do_status() {
    local any=0
    for spec in "${LEGS_MAIN[@]}" "$LEG_TELEOCR"; do
        set -- $spec
        local target=$1 port=$2
        local pids
        pids=$(pids_for_port "$port")
        local health
        health=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$port/health" 2>/dev/null || true)
        [ "$health" = "200" ] || health="down"
        local flags=""
        if [ -n "$pids" ]; then
            flags=$(ps -o args= -p "$(echo "$pids" | head -1)" 2>/dev/null | grep -oE "\-c [0-9]+.*-t [0-9]+" | head -1 || true)
            any=1
        fi
        printf "%-16s :%-5s pids=[%s] health=%s %s\n" "$target" "$port" "${pids:-none}" "$health" "$flags"
    done
    return 0
}

do_stop() {
    local killed=0
    for port in 8731 8732 8733; do
        local pids
        pids=$(holders_of_port "$port")
        if [ -n "${pids// /}" ]; then
            echo "stopping :$port (pid ${pids})"
            # shellcheck disable=SC2086
            kill $pids || true  # a PID race must not abort the sweep; the port-free wait below is the verdict
            killed=1
        fi
    done
    for port in 8731 8732 8733; do
        if ! wait_port_free "$port" 15; then
            echo ":$port still bound after 15s — kill -KILL $(holders_of_port "$port")" >&2
            return 1
        fi
    done
    [ "$killed" = "0" ] && echo "no llama-servers running"
    echo "all ports free (8731/8732/8733)"
}

do_start() {  # $@ = list of "target port" specs
    local spec target port held
    for spec in "$@"; do
        set -- $spec
        target=$1 port=$2
        # Source of truth is the BOUND PORT, not the process table: after
        # a stop, the make/sh wrappers linger briefly (their cmdlines still
        # mention the binary) while holding no socket. A free port means
        # start; a bound port means skip. (2026-10-10: cmdline match made
        # `restart` skip both legs right after stopping them.)
        held=$(holders_of_port "$port")
        if [ -n "${held// /}" ]; then
            echo ":$port already bound (pid ${held}) — skipping $target (stop first to restart)"
            continue
        fi
        # Rotate the previous log so a restart never destroys the failure
        # evidence a post-mortem needs; the live log is always $target.log.
        if [ -f "$TMP/$target.log" ]; then
            mv -f "$TMP/$target.log" "$TMP/$target.log.prev"
        fi
        echo "starting $target (:$port, log $TMP/$target.log) ..."
        setsid bash -c "cd \"$REPO\" && make \"$target\" > \"$TMP/$target.log\" 2>&1" </dev/null >/dev/null 2>&1 &
        if ! wait_healthy "$port" 60; then
            echo "$target failed to reach health in 120s — see $TMP/$target.log" >&2
            return 1
        fi
        echo "$target healthy (:$port)"
    done
}

case "${1:-status}" in
    status) do_status ;;
    stop) do_stop ;;
    start)
        shift
        legs=("${LEGS_MAIN[@]}")
        if [ "${1:-}" = "--teleocr" ]; then legs+=("$LEG_TELEOCR"); fi
        do_start "${legs[@]}"
        do_status
        ;;
    restart)
        shift
        do_stop
        legs=("${LEGS_MAIN[@]}")
        if [ "${1:-}" = "--teleocr" ]; then legs+=("$LEG_TELEOCR"); fi
        do_start "${legs[@]}"
        do_status
        ;;
    *) echo "usage: $0 {start [--teleocr]|stop|status|restart [--teleocr]}" >&2; exit 2 ;;
esac
