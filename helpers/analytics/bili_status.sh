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
# so its cwd (and thus bili.log) lives wherever it was started from.
# helpers/analytics/bili_logpath.py owns that discovery (env override -> /proc
# cwd walk -> known roots, newest-mtime wins) and THIS SCRIPT USES IT, rather
# than keeping a shell twin: the twin globbed /proc in LEXICAL pid order
# (999 sorts before 1000), so the two readers could disagree about which log
# was live. One implementation, one answer.
set -uo pipefail

T="${BILI_WATCH_DIR:-${TMPDIR:-/tmp}/bili_watch}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
PY="${REPO_ROOT}/.venv/bin/python3"
[ -x "$PY" ] || PY=python3
BILI_CONFIG="${BILI_CONFIG:-$HOME/.config/billion-context/billion-context.json}"

# Discover the live bili.log + its rotated sibling through the one owner.
paths="$("$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); from helpers.analytics.bili_logpath import discover_all; l, o = discover_all(); print(l); print(o)' "$REPO_ROOT" 2>/dev/null || true)"
L="$(printf '%s\n' "$paths" | sed -n 1p)"
OLD="$(printf '%s\n' "$paths" | sed -n 2p)"
if [ -z "$L" ]; then
    L="${BILI_LOG:-$HOME/.local/state/billion-context/bili.log}"
    OLD="${BILI_LOG_OLD:-$L.old}"
fi

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
echo "=== effective ladder bars (from the config the proxy reads live)"
"$PY" -c 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from helpers.analytics.bili_tier_nudge import Bars; b = Bars.from_file(Path(sys.argv[2])); s = b.source or {}; print("bar1 (T1)   : %d  [%s]" % (b.t1, s.get("t1", "?"))); print("bar2 (T2)   : %d  [%s]" % (b.t2, s.get("t2", "?"))); print("bar3 (T3)   : %d  [%s]" % (b.t3, s.get("t3", "?"))); print("growth floor: %d  [%s]" % (b.growth_floor, s.get("growthFloor", "?"))); print("nudgeGrowth : %d  [%s]" % (b.nudge_growth, s.get("nudgeGrowthTokens", "?")))' "$REPO_ROOT" "$BILI_CONFIG" 2>/dev/null || echo "  (config unreadable: $BILI_CONFIG)"
echo "--- last seen in the log. NOTE: the log prints nudgeGrowthTokens as the"
echo "    'interval' and as the 'pendingT1' denominator -- it does NOT print"
echo "    tierNudgeTokens.t1, so those two lines are not the T1 bar."
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

# A status REPORT never fails the caller: the exit code used to be whatever the
# last pipeline returned (tail's rc=1 when the watch dir has no events.log
# yet), which made `make bili-status` look like a failure. Findings are lines
# in the report, not the exit status.
exit 0
