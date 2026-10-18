#!/usr/bin/env bash
# Stop the bili proxy monitor (bili_monitor.py) safely.
#
# NEVER use `pkill -f monitor.py` (or any `pkill -f` on a script path): the
# pattern matches the calling shell's own command line ("bash -c ... monitor.py
# ...") and SIGTERMs the caller mid-tool-call. Learned the hard way (m01817).
#
# Instead match on the process NAME (python3) and filter by argv with awk, so
# only real python3 processes are candidates.
set -uo pipefail

PATTERN='bili_monitor\.py'

PIDS=$(pgrep -x python3 -a | awk -v pat="$PATTERN" '$0 ~ pat {print $1}')
if [ -z "$PIDS" ]; then
    echo "no monitor running"
    exit 0
fi

echo "stopping: $PIDS"
# shellcheck disable=SC2086
kill $PIDS
sleep 2

LEFT=$(pgrep -x python3 -a | awk -v pat="$PATTERN" '$0 ~ pat {print $1}')
if [ -n "$LEFT" ]; then
    echo "still alive after TERM: $LEFT -- sending KILL"
    # shellcheck disable=SC2086
    kill -9 $LEFT
    sleep 1
fi

FINAL=$(pgrep -x python3 -a | awk -v pat="$PATTERN" '$0 ~ pat {print $1}')
if [ -n "$FINAL" ]; then
    echo "FAILED to stop: $FINAL"
    exit 1
fi
echo "monitor stopped"
