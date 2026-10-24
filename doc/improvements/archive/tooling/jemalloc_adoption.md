---
title: "jemalloc adoption — preload for resident servers + opt-in for batch chains"
status: executed
filed: "2026-10-24"
executed: "2026-10-24"
completed_md: "436"
area: "Makefile (jemalloc block, server-target prefixes, quack-start)"
---

# jemalloc adoption — preload for resident servers + opt-in for batch chains

**Date:** 2026-10-24 · **Status:** EXECUTED same day — archived (completed.md #436)

## 1. Motivation

The 2026-10-21 DuckDB 2.0 cutover silently dropped the bundled allocator:
the 2.0.0 wheel has zero jemalloc symbols (no `duckdb_extensions()` row, no
`je_*` strings in the `.so`, no `memory_allocator` setting — probed
2026-10-24), so every DuckDB chain in this repo — embedded connections,
quack-served stores, rebuild/snapshot/ingest legs — allocates through glibc
malloc. jemalloc 5.3.0 is already installed system-wide
(`libjemalloc.so.2`), so the LD_PRELOAD route needs no install step.

The resident processes are exactly the long-running large-footprint profile
where glibc arena fragmentation ratchets RSS: the embgemma sidecar at probe
time had **34 h uptime at 640 MB RSS** (granite 48 MB). CPython here is
built `--with-mimalloc` (small-object path); the preload covers the
large-block side — numpy buffers, DuckDB result/Arrow buffers, and DuckDB's
own engine allocations, embedded and served.

Smoke-proofs (2026-10-24): venv python + duckdb + numpy run clean under
`LD_PRELOAD`; the vendored llama-server (0.6.0-dev, build 78651c4) runs
`--version` clean under it.

## 2. A/B measurement (2026-10-24, back-to-back arms, `/usr/bin/time -v`)

Legs: `make search-fresh APPLY=1 ARGS="--allow-stale"` (5-rebuild python
chain, cache-warm) and `make snapshot` (duckdb-heavy export). One
unmeasured warm pass first so both arms see identical cache state. NOTE:
box load drifted 1.55 → 5.60 mid-run (operator-confirmed: a parallel
session's compression job) — **timing numbers are indicative only
(parked per perf doctrine); RSS numbers are load-independent**.

| leg | arm | wall | peak RSS (max over tree) |
|---|---|---|---|
| search-fresh | A baseline | 15.7 s | 870.7 MB |
| search-fresh | B jemalloc, decay 30 s | 15.8 s | 930.0 MB (+6.8%) |
| snapshot | A baseline | 16.4 s | 1,393.5 MB |
| snapshot | B jemalloc, decay 30 s | 14.1 s | 1,547.9 MB (+11.1%) |
| snapshot | C jemalloc, decay **0** | 15.8 s | 1,411.9 MB (+1.3% ≈ parity) |

**Findings:**

1. **Time-neutral** everywhere (B's snapshot "gain" is load-drift noise).
2. **The +7–11% peak-RSS inflation is a decay-config artifact, not
   allocator overhead**: with `dirty_decay_ms:30000` the decay window
   never fires inside a 15 s process, so jemalloc retains dirty pages
   glibc would have returned. With tight decay (arm C) jemalloc is
   RSS-neutral. This is the **config law**: batch chains want
   `dirty_decay_ms:0`; long residents want a moderate window
   (default `0` here too — residents return pages between bursts anyway
   with `background_thread:true`, and a zero window never *hurts* RSS,
   it only trades a little allocation reuse).
3. Short legs cannot exhibit the fragmentation-ratchet benefit at all —
   the thesis (day-scale residents) is untested by this A/B and is
   deliberately not claimed from it. The sidecar restart (operator's
   next `make embgemma-server`) is the live trial.

## 3. Landed shape (Makefile)

- **Opt-in block** near the head: `JEMALLOC=1 make <target>` exports
  `LD_PRELOAD=$(JEMALLOC_LIB)` + `MALLOC_CONF=$(JEMALLOC_CONF)` into the
  whole recipe tree. `JEMALLOC_LIB ?=
  /usr/lib/x86_64-linux-gnu/libjemalloc.so.2`,
  `JEMALLOC_CONF ?= background_thread:true,dirty_decay_ms:0,muzzy_decay_ms:0`
  — both `?=` overridable per invocation.
- **Unconditional prefix** `$(JEMALLOC_ENV)` (env-var form, not export)
  on the three resident llama-server launches (`embgemma-server` :8732,
  `granite-server` :8733, `teleocr-server` :8731) and on `quack-start`
  (propagates through `quack_servers.sh`'s plain `setsid bash -c exec` —
  no env scrub, verified). Covers embedded + served DuckDB 2.0 chains.
  **Gap caught 2026-10-24 evening**: `quack-restart` is a separate
  recipe line (calls the script's internal `restart` branch, bypassing
  `quack-start`) and initially launched WITHOUT the prefix — a
  restarted server came up with zero jemalloc mappings; fixed and
  verified (5 mappings, wire health ok). sqlite legs need no separate
  wiring: sqlite is in-process, so the preload covers it under the
  same opt-in (exercised by the snapshot A/B arms).
- Help line added (`jemalloc (knob)`); no new targets, `.PHONY` unchanged.

## 4. Acceptance

- `make -n` expansion verified on `embgemma-server` and `JEMALLOC=1
  snapshot`; live smoke `JEMALLOC=1 make tmp-sweep` rc=0 under preload.
- Targeted: MakefileHelp static check, md-lint on this doc, search-fresh
  APPLY convergence. No pytest surface (no Python code changed).

## 5. Non-goals / follow-ups

- No `--with-jemalloc` CPython rebuild (mimalloc already owns the
  small-object path; the preload covers the rest).
- No tcmalloc alternative evaluated (jemalloc present + measured).
- The resident servers are operator-restartable: the preload engages on
  the next `make embgemma-server` / `make quack-restart`; no live service
  was restarted by this arc.
- **Live trial STARTED 2026-10-24 ~17:55** (operator restart through the
  new targets; `/proc/PID/maps` shows 5 libjemalloc mappings per server,
  `MALLOC_CONF` = tight-decay default confirmed in env, llamacpp-health
  ok on both). Baseline: embgemma 412 MB / granite 202 MB fresh. The
  comparison point is RSS growth over day-scale uptime vs the pre-arc
  ratchet reference (640 MB @ 34 h glibc).
- **Scratch-server A/B (2026-10-24, ~18:00–19:40)** — two embgemma
  instances, identical flags (`-c 16384 -b 16384 -ub 16384 -np 2 -t 3`),
  :8742 glibc vs :8743 jemalloc; identical 360-text burst of real
  2000–4000-char doc chunks (120 singles + 30 batch-8), warmup, 45 s
  idle settle; box under load 4–6 (parallel compression), so timings
  indicative — arms back-to-back.

  | metric | glibc | jemalloc |
  |---|---|---|
  | RSS idle before | 602 MB | 582 MB |
  | singles throughput | 0.207 texts/s (p50 4.73 s) | 0.204 texts/s (p50 4.83 s) |
  | batch-8 throughput | 0.203 texts/s (p50 37.7 s) | 0.222 texts/s (p50 35.6 s) |
  | RSS after burst | 4,176 MB | 4,125 MB |
  | RSS after 45 s idle | 4,176 MB (no return) | 4,125 MB (no return) |

  **Verdict: allocator-neutral on every axis.** The 0.6 GB → 4.2 GB
  growth is llama.cpp's own per-batch-shape compute-buffer cache
  (allocated-and-held from the allocator's view — neither allocator can
  return what the server never frees), NOT fragmentation: the mid-run
  hypothesis that glibc was ratcheting was retracted when the jemalloc
  arm hit the same plateau. Batching is slot-serialized either way
  (~no throughput gain). Throughput/latency parity within load noise.
  Consequence: the server-side jemalloc prefix is kept as
  measured-harmless — **operator ruling 2026-10-24: KEEP, no quals** —
  and the day-scale fragmentation thesis remains
  OPEN (unproven either way at 35-min scale); the durable justification
  for the adoption is the DuckDB-2.0-allocator gap + the Python chain
  opt-in.
- If the sidecar's day-scale RSS profile under jemalloc wants a looser
  decay window, override `JEMALLOC_CONF` per invocation — no code change.
