#!/usr/bin/env python3
"""Pinned-pool vs serial embed throughput probe — is parallel ingest worth it NOW?

Answers the recurring "would EMBED_POOL_WORKERS>1 speed up ingest?"
question with a same-window measurement instead of doctrine. Samples
real section texts from the live note_search index and runs legs back to
back: serial in-process, the production pinned spawn pool (pin BEFORE
model load, n_threads=1 per worker — doc-level parallelism across cores,
never thread oversubscription), then serial again to bound window drift.
Only same-window comparisons are valid — see embed_runtime_bench for the
load-shape history.

Two modes:
  default           production bge-small via helpers.core.local_embedder
  --gguf PATH --ctx N  candidate model (e.g. granite @ 2048 for the
                    phase-2 bake-off) — same legs, probe-local pool that
                    mirrors embed_documents_parallel's shape

CALL-SHAPE WARNING (the 2026-09-06 confound): the default mode's serial
leg is the production BATCH call (embed_documents — one create_embedding
over all inputs, one accumulated decode), while pool legs and all
--gguf-mode legs embed PER-TEXT. On heterogeneous section lengths batch
pays ~max-length compute per text (batch 2.6/s vs per-text 12.3/s on the
same corpus — a 4.7x CALL-SHAPE gap that was first misread as a 2.7x
pool win). Only compare legs of the SAME call shape; per-text is the
fast serial shape.

Spawn-pool hazards baked into the shape: workers re-import this module,
so worker fns are top-level defs and every executable statement lives
under the __main__ guard (an unguarded leg re-runs the whole probe in
each worker — found live 2026-09-06 via a /tmp draft).

Usage:
    python3 helpers/bench/embed_pool_probe.py [n_texts=96] [workers=4,2]
    python3 helpers/bench/embed_pool_probe.py --gguf models/granite-...gguf --ctx 2048
Preflight: refuses to start while any prior probe instance, spawn worker,
or resource tracker is alive (shared preflight_clean_state).
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from helpers.bench.embed_runtime_bench import preflight_clean_state  # noqa: E402

# Per-process candidate-model singleton (spawn workers are fresh processes
# that re-import this module; initargs carry the path/ctx).
_GGUF: dict = {}


def _section_texts(n: int) -> list[str]:
    """n random real section texts (post-sectioning note_search rows)."""
    import sqlite3

    c = sqlite3.connect(f"file:{REPO}/memory/research.db?mode=ro", uri=True)
    rows = c.execute(
        "SELECT title, sector, content FROM note_search ORDER BY RANDOM() LIMIT ?", (n,)
    ).fetchall()
    c.close()
    return [f"{t}\n{s}\n{ct or ''}" for t, s, ct in rows]


def _normalize(vec: list[float]) -> list[float]:
    import math

    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec


def _gguf_model(gguf_path: str, n_ctx: int, n_threads: int | None = None):
    model = _GGUF.get("model")
    if model is None:
        from llama_cpp import Llama

        model = Llama(
            str(gguf_path), embedding=True, n_ctx=n_ctx, verbose=False, n_threads=n_threads
        )
        _GGUF["model"] = model
    return model


def _gguf_pool_init(core_queue, gguf_path: str, n_ctx: int) -> None:
    """Mirror local_embedder._pool_init: claim+pin a distinct core BEFORE
    the model load (affinity applies to threads created after the call),
    then load a 1-thread model."""
    import os

    core = core_queue.get()
    try:
        os.sched_setaffinity(0, {core % (os.cpu_count() or 1)})
    except OSError:
        pass  # restricted env: float (still correct, just slower)
    _gguf_model(gguf_path, n_ctx, n_threads=1)


def _gguf_pool_chunk(arg: tuple[int, list[str]]) -> tuple[int, list[list[float]]]:
    start, texts = arg
    model = _GGUF["model"]
    out = []
    for text in texts:
        vec = model.create_embedding(input=[text])["data"][0]["embedding"]
        out.append(_normalize(vec))
    return start, out


def _run_gguf_legs(texts: list[str], gguf_path: str, n_ctx: int, worker_counts: list[int]) -> None:
    import multiprocessing as mp
    import os
    import time

    model = _gguf_model(gguf_path, n_ctx)  # floating serial shape (llama.cpp thread choice)

    def leg(label, fn):
        t0 = time.perf_counter()
        vecs = fn()
        dt = time.perf_counter() - t0
        print(
            f"{label}: {len(texts)} in {dt:.1f}s = {len(texts) / dt:.2f}/s (dim={len(vecs[0])})",
            flush=True,
        )

    # Untimed warmup: the first timed leg of a cold process reads ~2.6x slow
    # (reproducible 2026-09-06: first serial leg 2.97-4.24/s, serial#2
    # 10-13/s on identical texts) — governor/first-touch, NOT box load.
    for t in texts[:4]:
        _normalize(model.create_embedding(input=[t])["data"][0]["embedding"])

    def serial():
        return [
            _normalize(model.create_embedding(input=[t])["data"][0]["embedding"]) for t in texts
        ]

    def pool(workers: int):
        ncpu = os.cpu_count() or 1
        bounds = [
            (i * len(texts) // workers, (i + 1) * len(texts) // workers) for i in range(workers)
        ]
        chunks = [(start, texts[start:end]) for start, end in bounds if end > start]
        ctx = mp.get_context("spawn")
        core_queue = ctx.Queue()
        for core in sorted({i % ncpu for i in range(workers)}):
            core_queue.put(core)
        out: list[list[float] | None] = [None] * len(texts)
        try:
            with ctx.Pool(
                workers, initializer=_gguf_pool_init, initargs=(core_queue, gguf_path, n_ctx)
            ) as proc_pool:
                for start, vecs in proc_pool.map(_gguf_pool_chunk, chunks):
                    for j, vec in enumerate(vecs):
                        out[start + j] = vec
        finally:
            core_queue.close()
            core_queue.join_thread()
        if any(v is None for v in out):
            raise RuntimeError("parallel embed left unfilled slots — chunk bug")
        return [v for v in out if v is not None]

    leg("serial    ", serial)
    for workers in worker_counts:
        leg(f"pool N={workers}  ", lambda w=workers: pool(w))
    leg("serial#2  ", serial)


def _run_bge_legs(n: int, worker_counts: list[int]) -> None:
    import time

    from helpers.core import local_embedder as le

    texts = _section_texts(n)
    print(f"sample: {n} sections, avg {sum(map(len, texts)) / n:.0f} chars", flush=True)

    def leg(label, fn):
        t0 = time.perf_counter()
        vecs = fn()
        dt = time.perf_counter() - t0
        print(f"{label}: {n} in {dt:.1f}s = {n / dt:.2f}/s (dim={len(vecs[0])})", flush=True)

    # Untimed warmup — cold first legs read ~2.6x slow (see _run_gguf_legs).
    for t in texts[:4]:
        le.embed_document(t)

    leg("serial    ", lambda: le.embed_documents(texts))
    for workers in worker_counts:
        leg(f"pool N={workers}  ", lambda w=workers: le.embed_documents_parallel(texts, workers=w))
    leg("serial#2  ", lambda: le.embed_documents(texts))


if __name__ == "__main__":
    # No leg may execute at import time — spawn workers re-import this file.
    args = sys.argv[1:]
    preflight_clean_state("embed_pool_probe.py")
    gguf = None
    n_ctx = 512
    if "--gguf" in args:
        i = args.index("--gguf")
        gguf = args[i + 1]
        args = args[:i] + args[i + 2 :]
    if "--ctx" in args:
        i = args.index("--ctx")
        n_ctx = int(args[i + 1])
        args = args[:i] + args[i + 2 :]
    n = int(args[0]) if args else 96
    worker_counts = [int(w) for w in args[1].split(",")] if len(args) > 1 else [4, 2]
    if gguf:
        texts = _section_texts(n)
        print(
            f"sample: {n} sections, avg {sum(map(len, texts)) / n:.0f} chars, "
            f"model={Path(gguf).name}, ctx={n_ctx}",
            flush=True,
        )
        _run_gguf_legs(texts, gguf, n_ctx, worker_counts)
    else:
        _run_bge_legs(n, worker_counts)
