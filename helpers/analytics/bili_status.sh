#!/usr/bin/env bash
# bili (billion-context) ONLY status report. Read-only.
#
# Scope: the proxy's own behaviour -- thresholds, injections, folds, burn,
# cache. No repo/tree/patch state.
#
#   helpers/analytics/bili_status.sh
#
# Reads the live proxy log plus the state written by helpers/analytics/
# bili_monitor.py under $BILI_WATCH_DIR (default: ${TMPDIR:-/tmp}/bili_watch).
# Never `pkill -f` (see bili_stop_monitor.sh for why).
#
# The log path is DISCOVERED, not hardcoded: billion-context is an npm module,
# so its cwd (and thus bili.log) lives wherever it was started from. We walk
# /proc for the running proxy and read its cwd; BILI_LOG overrides, then the
# known state roots. This is the same logic as helpers/analytics/bili_logpath.py.
set -uo pipefail

T="${BILI_WATCH_DIR:-${TMPDIR:-/tmp}/bili_watch}"

# Discover the live bili.log: env override, then /proc, then known roots.
# Newest-mtime wins when several proxies are up (matches bili_logpath.py);
# plain glob order is LEXICAL in pid -- 999 sorts before 1000 -- so a silent
# first-match could watch a stale proxy while the live one folds unseen.
L="${BILI_LOG:-}"
if [ -z "$L" ]; then
    cands=()
    for p in /proc/[0-9]*/cwd/bili.log; do
        [ -f "$p" ] && cands+=("$p")
    done
    if [ "${#cands[@]}" -gt 1 ]; then
        echo "bili_status: ${#cands[@]} running proxies -- using the newest log:" >&2
        printf '  %s\n' "${cands[@]}" >&2
        L="${cands[0]}"
        for p in "${cands[@]}"; do
            [ "$p" -nt "$L" ] && L="$p"
        done
    elif [ "${#cands[@]}" -eq 1 ]; then
        L="${cands[0]}"
    fi
fi
if [ -z "$L" ]; then
    for root in "$HOME/.local/state/billion-context" \
        "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)/bench_data/billion-context"; do
        if [ -f "$root/bili.log" ]; then
            L="$root/bili.log"
            break
        fi
    done
fi
L="${L:-$HOME/.local/state/billion-context/bili.log}"
OLD=$L.old

echo "=== watchers"
pid=$(pgrep -x python3 -a | awk '/bili_monitor\.py/ {print $1}' | tr '\n' ' ')
echo "monitor pid : ${pid:-NONE}"
echo "watch dir   : $T"
[ -f "$T/HEARTBEAT" ] && echo "heartbeat   : $(cat "$T/HEARTBEAT")"
# monitor.exit is only a problem when it is NEWER than the heartbeat -- a stale
# marker from a monitor we deliberately stopped must not read as a dead watcher.
if [ -f "$T/monitor.exit" ] && [ "$T/monitor.exit" -nt "$T/HEARTBEAT" ]; then
    echo "!! monitor exited: $(cat "$T/monitor.exit") (marker newer than heartbeat)"
fi

echo
echo "=== effective policy (last seen in log)"
printf 'interval    : %s\n' "$(grep -hoE 'interval=[0-9]+' "$L" | tail -1)"
printf 'pendingT1   : %s\n' "$(grep -hoE 'pendingT1=[0-9]+/[0-9]+' "$L" | tail -1)"
printf 'growth      : %s\n' "$(grep -hoE 'growth=-?[0-9]+/[0-9]+' "$L" | tail -1)"

echo
echo "=== injection/fold ledger, last 8 (whole family)"
grep -hE "nudge INJECT|acp-compress-obs" "$L" "$OLD" 2>/dev/null | tail -8 | cut -c1-210

echo
echo "=== per-session live state (live log only)"
for s in $(grep -hoE "ses_[A-Za-z0-9]+" "$L" | sort -u); do
    u=$(grep "$s" "$L" | grep "acp-usage" | tail -1)
    n=$(grep "$s" "$L" | grep "nudge idle" | tail -1)
    [ -z "${u}${n}" ] && continue
    echo "--- $s"
    [ -n "$u" ] && echo "    usage: ${u##*\[acp-usage\] }"
    [ -n "$n" ] && echo "    nudge: ${n##*nudge idle: }"
done

echo
echo "=== events.log tail"
tail -5 "$T/events.log" 2>/dev/null
