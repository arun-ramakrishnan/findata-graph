#!/usr/bin/env python3
"""Embedding runtime-shape bench: serial vs threaded throughput + RSS.

Born of the 2026-09-06 pool-artifact find (doc/local/embed_model_eval.txt):
under desktop load a PINNED spawn-pool worker cannot migrate off a stolen
core and ran 3x slower than a floating single process (3.65/s serial vs
1.2/s pinned pool, same window). #173's pinning doctrine is idle-box
advice; on a loaded box, serial in-process (1T == 4T for bert forwards,
sync-bound) is the fast shape. Rates are WINDOW-DEPENDENT — only
same-window comparisons are valid (a concurrent-window bge probe read
0.33/s vs its clean ~3.4/s).

Also measures per-model resident-memory delta (load + 1 embed, statm).
Batch-1 numbers for calibration: bge +118MB, MiniLM +107, gte +119,
granite +431 (modern-bert materializes ~4x its 115MB Q8 file), nomic +289.

Usage:
    python3 helpers/bench/embed_runtime_bench.py <gguf-path> [n_texts]
    python3 helpers/bench/embed_runtime_bench.py --rss <gguf-path>...
Texts are the first n real note texts from the live note_search index
(sample-length caveat: short-text samples inflate rates — use n >= 256
for representative lengths; the 64-text first-notes sample overrated
granite 1.54/s vs its ~0.6-0.8/s docs reality).
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _rss_mb() -> float:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * 4096 / 1e6


def _note_texts(n: int) -> list[str]:
    import sqlite3

    c = sqlite3.connect(f"file:{REPO}/memory/research.db?mode=ro", uri=True)
    rows = c.execute("SELECT title, sector, content FROM note_search LIMIT ?", (n,)).fetchall()
    c.close()
    return [f"{t}\n{s}\n{(ct or '')[:8000]}" for t, s, ct in rows]


def bench_throughput(path: str, n: int) -> None:
    import time

    from llama_cpp import Llama

    texts = _note_texts(n)
    for threads in (4, 1):
        m = Llama(path, embedding=True, n_ctx=512, verbose=False, n_threads=threads)
        t0 = time.perf_counter()
        for t in texts:
            m.create_embedding(input=[t])
        dt = time.perf_counter() - t0
        print(f"{threads}T: {len(texts)} texts in {dt:.1f}s = {len(texts) / dt:.2f}/s", flush=True)
        del m


def bench_rss(paths: list[str]) -> None:
    from llama_cpp import Llama

    base = _rss_mb()
    print(f"python baseline: {base:.0f}MB")
    for p in paths:
        m = Llama(p, embedding=True, n_ctx=512, verbose=False, n_threads=1)
        m.create_embedding(input=["rss probe text"])["data"][0]["embedding"]
        loaded = _rss_mb()
        del m
        print(f"{Path(p).name}: +{loaded - base:.0f}MB resident (load + 1 embed)")


def preflight_clean_state(marker: str) -> None:  # noqa: C901  # bench preflight: linear guard chain
    """Refuse to start unless no prior run's leftovers are alive.

    A cancelled wrapper shell does NOT kill the bench tree (2026-09-06:
    a cancelled embed run kept embedding for 7 minutes and its spawn
    workers outlived it) — background model contention would poison the
    same-window numbers this bench exists to produce. Aborts with
    exact-PID kill commands (pkill is both overbroad and self-kill-prone).
    Shared by the embed benches (embed_pool_probe imports this). Public
    so sibling bench modules can reuse it without a private import.
    """
    import os

    me = os.getpid()

    def _ppid(pid: int) -> int:
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("PPid:"):
                        return int(line.split()[1])
        except OSError:
            pass
        return 1

    ancestry = set()
    cur = me
    while cur > 1:
        ancestry.add(cur)
        cur = _ppid(cur)

    stale: list[tuple[int, str]] = []
    for entry in os.listdir("/proc"):
        pid = int(entry) if entry.isdigit() else 0
        if not pid or pid == me or pid in ancestry:
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            continue
        if (
            marker in cmd
            or "multiprocessing.spawn" in cmd
            or "multiprocessing.resource_tracker" in cmd
        ):
            stale.append((int(entry), cmd))
    if stale:
        print(
            "DIRTY START — prior bench instance/workers still alive; "
            "kill by exact PID before benchmarking:",
            file=sys.stderr,
        )
        for pid, cmd in stale:
            print(f"  kill -TERM {pid}  # {cmd[:120]}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    preflight_clean_state("embed_runtime_bench.py")
    if args[0] == "--rss":
        bench_rss(args[1:])
    else:
        bench_throughput(args[0], int(args[1]) if len(args) > 1 else 64)


if __name__ == "__main__":
    main()
