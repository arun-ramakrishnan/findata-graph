---
title: "near-duplicates API: memoize the O(n^2) self-join and cap the corpus scale"
status: executed
filed: "2026-09-18"
executed: "2026-09-19"
completed_md: "249"
area: "app.py /api/graph/near-duplicates, helpers/graph/query.py"
---

# near-duplicates API: memoize the O(n²) self-join and cap the corpus
scale

**Date:** 2026-09-18 · **Status:** EXECUTED (completed.md #249) · **Area:** the
near-duplicates endpoint and its wrapper · **Follows:** Addendum 3 to
`doc/local/security/security_evaluation.md` (finding **AVAIL-1**,
CONFIRMED HIGH)

## 1. Motivation

`GET /api/graph/near-duplicates` runs an O(n²) pairwise cosine self-join
on every request. Measured on the real corpus (9,282 company docs,
43,073,121 pairs): **52.78 s of CPU per request**. The endpoint is
unauthenticated, has no compute-level cache, no rate limit, and no scale
guard; the `limit` clamp bounds the output, not the work, and the ETag
(`after_request`) strips the response body after the query has already
run. On the 4-core production box a few concurrent requests saturate the
machine for a minute.

The docstring calls it "a maintenance command, deliberately NOT an API
hot path" — the intent and the deployment disagree. This proposal makes
the smallest change that reconciles them: make repeat traffic free, and
make a too-large corpus a clean 503 instead of a multi-minute stall.

## 2. Approach

Two changes, both local to the endpoint:

1. **Memoize the wrapper, keyed on the cache generation.** Cache
   `near_duplicate_notes` results by
   `(doc_type, min_sim, limit)` **plus** the graph cache's `built_at` —
   the same key the existing ETag derives from. Drop the memo in
   `_reset_graph_connection()` next to `_graph_etag`, so a refresh
   invalidates it exactly when the data actually changes. Effect: the
   first request after a refresh pays the join; every subsequent request
   in the same generation returns the cached object. An anonymous client
   can no longer force repeated compute — the endpoint becomes the 304 it
   already advertises.
2. **Cap the corpus scale.** Before the join, count the candidate
   `doc_type` rows; above a documented ceiling (start at 10,000), return
   `503` with an explanatory body — parity with the positions endpoint's
   node ceiling (app.py:2905), which already refuses rather than stalls.
   This bounds the *first* request in a generation, which memoization
   alone leaves at ~53 s.

Neither change touches the algorithm, the SQL, or the result shape.

## 3. Regression test (rides `make qa`)

In the house route-integration pattern (e.g. `tests/test_api_graph_*`):

- **Compute-once test.** Monkeypatch `near_duplicate_notes` with a
  counting wrapper; two GETs with identical params return the same body
  and the underlying compute runs **once**. Fails without change 1.
- **Scale-guard test.** With the candidate count stubbed above the
  ceiling, the endpoint returns `503` and never calls the wrapper. Fails
  without change 2.
- **Invalidation test.** After a simulated reset (the same hook refresh
  uses), a repeat GET recomputes once. Guards the memo's lifetime.

## 4. Gates

- md-lint clean; the finding's status in Addendum 3 moves to "remediated"
  with this proposal's completion number (no separate addendum — the
  doc's own lifecycle).
- The new tests ride `make qa` in the existing per-endpoint test file; no
  new marker.
- The ceiling is set in one named constant with a comment pointing at
  AVAIL-1, so the next corpus crossing is a one-line change, not a
  re-investigation.

## 5. Non-goals

- **Authentication.** Still deploy-gated (SEC-5, Phase 4 dormant); this
  proposal does not reopen a decided deploy condition.
- **Rewriting the algorithm.** The self-join is the right tool for the
  QA tripwire at its intended scale; the defect is its *deployment*, not
  its design.
- **The other expensive endpoints.** `/api/graph/suggestions` (2.97 s,
  bounded by `top*8` over materialised tables) is a watch item, not a
  finding; it gets the same memo treatment only if it crosses into
  confirmed territory.
