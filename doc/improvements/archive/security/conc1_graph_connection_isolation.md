---
title: "CONC-1 fix: stop sharing one graph connection across concurrent requests"
status: executed
filed: "2026-09-18"
executed: "2026-09-19"
completed_md: "251"
area: "app.py get_graph_connection, helpers/graph/query.py"
---

# CONC-1 fix: stop sharing one graph connection across concurrent requests

**Date:** 2026-09-18 · **Status:** EXECUTED (completed.md #251) · **Follows:**
Addendum 5, finding **CONC-1** (CONFIRMED). Found by the S4 verifier,
independently reproduced before filing.

## 1. Motivation

`get_graph_connection()` (`app.py:152`) hands every request the same
process-wide read-only DuckDB connection, and the fast path takes no lock
— by design: `app.py:140-145` states "only the init/reset swap needs
guarding". Concurrent `execute` on one connection is not safe.

Reproduced (two threads, distinct count queries, one shared RO connection
to the real `graph.duckdb`, 3,000 iterations): **63 requests received the
other query's rows; 126 errored.** A handler asking for the company count
(9,282) can be handed the chatter count (3,903) — a correct-looking,
wrong JSON answer.

Two consequences:

1. **Wrong data, silently.** Every `/api/graph/*` endpoint can cross-
   return rows under concurrency. Nothing downstream detects it; the
   response is well-formed.
2. **It multiplies AVAIL-1.** Under N gunicorn workers each owns a
   singleton that *does* parallelise, so one unauthenticated GET repeated
   ~1/min bills N × ~85 s of CPU. Inside one worker, concurrent
   near-duplicates requests degrade from slow to "500 or wrong rows".

## 2. Approach

Landed 2026-09-19: the **per-request connection** (option a), as a hybrid.

`get_graph_connection()` now hands out its **own** read-only connection per
request, stashed on `flask.g` and closed by a `teardown_request` hook — so
concurrent requests never share a connection object and the cross-return
class cannot occur. The direct-call singleton path (lazy init under
`_graph_lock` + the TTL error cache) is preserved unchanged for every
non-request caller — tests, CLI — which is why the existing connection and
refresh tests still pass as written. `_graph_build_etag` now prefers the
request's own connection (else the singleton, else a one-off open), and
`_reset_graph_connection` also drops a request's open connection so nothing
stale survives a refresh.

The lock alternative was rejected: it would serialise every graph read, and
a correct lock would have to span execute→fetch (the cross-return happens
between them), which is fragile across the fifteen call sites.

Verified end-to-end on the live cache: the reproduction that produced 63
wrong rows + 126 errors in 3,000 iterations now returns **0 wrong, 0 errors,
3,000 correct**. Three regression tests pin the contract: requests get
distinct connections, one request reuses one connection, teardown closes it,
and the direct-call singleton is untouched.

## 3. Regression test (rides `make qa`)

In the house route-integration pattern: two threads issuing distinct
queries through the app's connection helper, asserting every response
matches its own expected value. Fails today (63/3,000 wrong), passes with
either fix. Also assert no cross-worker amplification of the near-duplicates
cost once AVAIL-1's cap lands.

## 4. Gates

- md-lint clean; Addendum 5's CONC-1 status moves to "remediated" with
  this proposal's completion number.
- The ETag logic (`_graph_build_etag`) must keep deriving from the same
  cache `built_at` — a connection change must not change cache semantics.

## 5. Non-goals

- The DuckDB-vs-sqlite-vec storage question; only connection ownership.
- AVAIL-1's compute cap — separate proposal, filed alongside.
