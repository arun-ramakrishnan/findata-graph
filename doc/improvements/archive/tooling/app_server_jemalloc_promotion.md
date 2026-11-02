---
title: "app_server_jemalloc_promotion: resident Flask server under LD_PRELOAD jemalloc — measured −10-12% RSS, wired as make app-server"
status: executed
filed: "2026-11-02"
executed: "2026-11-02"
completed_md: "471"
area: "Makefile (app-server target); app.py launch path"
---

# app_server_jemalloc_promotion — the #468 S3 resident trial, measured and wired

**Date:** 2026-11-02 · **Status:** EXECUTED same day (retro-filed at operator
request; the trial ran directly from the parked #468 S3 item).

## 1. Background

The #468 S3 item parked the one unmeasured jemalloc lever: the resident Python app
server (17.5K-vector loads, sqlite/duckdb buffers, imports the cosine_knn
cdylib). The 2026-10-26 Phase A trial proved RSS-neutrality only on SHORT
one-shot legs; a 2026-11-01 attempt stalled on a "localhost not found" that
belonged to the tauri desktop build (#468), never app.py.

## 2. Measurement (12-min A/B legs ×2, ABAB-interleaved)

Fixed 8-endpoint request mix (~450 reqs/leg, 1 req/s), RSS sampled every 10s
+ VmHWM; artifacts `/mnt/data/tmp/jemalloc_ab/` (leg_driver.py, leg_*.json,
summary.json):

| metric (mean ×2 reps) | glibc | jemalloc | delta |
|---|---|---|---|
| peak RSS | 83.7 MB | 75.0 MB | **−10.4%** |
| final RSS | 73.5 MB | 73.5→73.5 MB mean 83.5 | **−11.9%** |
| VmHWM | 146.1 MB | 151.6 MB | +3.7% |
| startup | 1.1s | 1.1s | neutral |

Reps consistent, non-overlapping ranges (glibc 82.9-84.6, jemalloc
73.3-76.8 MB). VmHWM +3.7% = the transient touches higher before tight decay
returns pages; end-state RSS is the resident metric. Request behavior
identical. The 2026-10-26 "short legs RSS-neutral" finding does not transfer
to the resident — the fragmentation-ratchet thesis bites exactly where #468
predicted.

## 3. Wiring

`make app-server` (Makefile, help + .PHONY + recipe): launches app.py under
the JEMALLOC_ENV prefix (LD_PRELOAD libjemalloc.so.2 + tight-decay
MALLOC_CONF), same shape as the llama resident targets; FLASK_HOST/
FLASK_PORT/FLASK_DEBUG pass through. Smoke-verified: port 5200 health 200,
libjemalloc mapped in-process (5 map lines).

Makefile trap recorded for future edits: with `.RECIPEPREFIX =>`,
continuation lines need a LITERAL backslash at EOL — an edit-tool write
collapsed `\`+newline into a mid-line `>`, which dash read as a redirect
(`cannot create LD_PRELOAD=...`). Never pattern-pkill these chains: witr
the ancestry, kill by verified pgid (pkill -f self-matches its own shell).

## 4. Never-combine rule (unchanged)

Never combine with the desktop app's compiled-in tikv-jemallocator (#468 S1)
— different processes, but the rule stays visible: one jemalloc per process,
LD_PRELOAD xor global_allocator.
