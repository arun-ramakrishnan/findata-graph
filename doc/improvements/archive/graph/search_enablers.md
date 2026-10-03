---
title: "Search enablers — evidence lanes for triage escalation"
status: executed
filed: '2026-10-03'
area: graph
executed: '2026-10-04'
completed_md: '344'
---

# Proposal: Search enablers — evidence lanes for triage escalation

**Date**: 2026-10-03
**Status**: EXECUTED 2026-10-04 (completed.md #344; acceptance ran against
the frozen eval-v3 set rather than "the next natural queue fill" — the
queue endgame consumed that fill's rows through the decisions path first,
and the frozen 56-item set is the stricter, reproducible surface).
**Depends on**: `../archive/graph/triage_preannotation_escalation.md`
(executed #342) — this proposal executes its named follow-up: *"wiring a
real browser/search-API lane into `_search_evidence`"*. Regression key
stays the Jev-pilot eval-v3 set.
**Trigger**: the acceptance run's one flip (TCS–MHP, p 0.05 → 0.85) needed an
**operator-supplied URL** — all four autonomous search paths failed for plain
fetchers (Google JS wall, DDG html captcha, Bing brand homepages,
newsroom.publisher 403s). Operator directive (2026-09-29): fix the search lane;
operator-researched candidates evaluated in §Rejected.

## Execution results (2026-10-04, legs3/preann4-search-enablers/)

Full eval-v3 rerun (56 items, glm-5.3 batch judge + batched evidence
escalation through the new lanes) vs the acceptance record of record
(`legs3/preann3-glm-5.3/`):

- **AC#3 floors held exactly**: artifact rejection **37/37** (exact
  pilot_score3 formula), Q2 **55/56** (floor ≥53) — the same miss as the
  pre-lanes run (id 27, whose rubric clause the batch judge rejects both
  times; escalation only ever ANDs admission down, never up), so zero Q2
  regressions.
- **AC#1 TCS–MHP unaided, and improved**: id 27 fact flipped False (p 0.7)
  → **True (p 0.95)** with a verbatim-verified quote and a lane-found URL
  (investywise TCS–MHP article; direct fetch, no reader needed). The
  original acceptance needed the operator's URL for this exact row.
- **Q1 48 → 51/56**: all three verdict changes are False→True corrections
  matching truth (ids 27, 48, 54 — the latter two from the drift-probe
  dispute set); no Q1 regressions.
- **AC#4/#5 honored live**: 9 rows degraded to `needs_retry` (search or
  verification failure — never a flip), 2 rows `quote_unverified`
  (paraphrase-class quotes rejected by the verbatim gate) — the floors
  absorbed both without a single wrong verdict.
- AC#2/#6 are the landed fixture tests (30/30 module tests: lane parsers,
  `needs_retry` degradation, unverifiable-quote gate, host/relevance gate).

## Problem — two gaps, not one

1. **Search**: `web_search()` (DDG html → Bing html, regex-parsed) fails for
   agents. Reproduced live 2026-09-29 on the exact failed case: DDG returns
   `challenge/captcha` markup, 0 results.
2. **Fetch (missing entirely)**: the escalation judge receives **snippets
   only** (`{title,url,snippet}`), yet the evidence standard is a *verbatim
   supporting quote*. Snippets routinely lack the sentence, and some
   publisher pages 403 plain fetchers (re-probe 2026-10-04: *not* all of
   them — direct fetch works for several, which is why direct is the
   load-bearing transport and the reader is the extra leg). There was no
   page-text step anywhere in the pipeline.

## Probes

Original measurement 2026-09-29 (stdlib `urllib` under the repo venv,
TCS–MHP query). **Re-probed 2026-10-04** — the struck-through cells are the
ones that changed; see "Re-probed" below for the narrative.

| lane | result |
|---|---|
| DDG html (control — current lane) | ~~captcha markup, **0 results**~~ → **RE-PROBED 2026-10-04: 200, 39 KB, no captcha** — but it answers a brand query with brand homepages (`tcs.com`, `tcs.com/careers`) and a Nippon Life query with unrelated retail. *Reachable, and confidently wrong* — a worse failure than empty, because `escalate_rows` applies any verdict carrying a quote + URL. Now relevance+host gated |
| **Google News RSS** (`news.google.com/rss/search?q=…&hl=en-IN&gl=IN&ceid=IN:en`) | **10 items** ~~(re-probe: 100 items — explicit cap now)~~, incl. `newsroom.porsche.com`; headline spread — €320M (MHP-only) vs $373M vs $1.46B (total deal) — is exactly the adjudication material the judge needs. Links are `news.google.com/rss/articles/…` redirects: titles are the evidence; **do not** chain the redirects through readers (r.jina.ai 403s them) |
| **Bing News RSS** (`bing.com/news/search?q=…&format=rss`) | **11 items** (re-probe: 12), direct article URLs embedded in the `apiclick.aspx?…&url=` wrapper — decodable with a param variant of the existing `_unwrap_bing_url` (news uses `url=`, not base64 `u=a1`) |
| **r.jina.ai reader** (`r.jina.ai/<url>`) | ~~200 + text on publishers that 403 plain fetchers; follows redirects; renders JS; free tier, no key (~20 req/min IP-based)~~ → **SUPERSEDED 2026-10-04: 403 on every host tested, incl. `example.com`.** Demoted to a silent extra leg; **direct fetch is load-bearing** (Financial Express serves 200 / 521 KB to plain `urllib` with a realistic UA — publishers do not all 403) |
| **Brave Search API** (`api.search.brave.com/res/v1/web/search`) | endpoint live and reachable (clean JSON 422 param probe without key; re-probed 422 again 2026-10-04); free plan 2,000 queries/mo, 1 req/s. **Caveat: the free limit requires attribution** (Brave branding in the consuming surface) — awkward for a headless JSONL pipeline, so Brave is a BACKUP lane only |

## Re-probed 2026-10-04 (parallel relations-queue session; handoff notes
`/mnt/data/tmp/search_concerns.md`, probe scripts `/mnt/data/tmp/search-enablers/`)

- **r.jina.ai: 403 on every host tested** (incl. `example.com`) — the reader
  is demoted to a silent extra leg; **direct fetch is the load-bearing leg**
  (verified: Financial Express serves 200 / 521 KB to plain `urllib` with a
  realistic UA — publishers do not all 403).
- **DDG html is reachable but confidently wrong** — no captcha today; it
  answers a brand query with brand homepages (tcs.com, tcs.com/careers) and a
  Nippon Life query with unrelated retail pages. Confident noise is worse than
  empty: the html lanes are now **relevance+host gated** and prefer honest
  `needs_retry` over garbage evidence.
- **Google News RSS measured 100 items** (probe day said 10) — explicit cap.
- **Evidence integrity gate (new)**: a judge flip applies only when the
  support quote is **verbatim-in-evidence** (snippets + fetched page text,
  whitespace/punctuation-normalized); otherwise `needs_retry` +
  `quote_unverified`. Expected side effect once page text lands: more flips —
  the eval-v3 floor must be re-held before the change is trusted (AC#3).

## Evaluated and rejected

- **hyperbeam (PyPI)** — a wrapper over `duckduckgo-search` (the same lane
  that is captcha'd), paid ScraperAPI, and an OpenAI call; packaged with
  black/flake8/twine as runtime deps. No unique reach; dependency and
  supply-chain drag. Rejected.
- **brave-search-python-client (PyPI)** — sound API, but the client drags
  pydantic-settings/tenacity/typer for one authenticated GET. The module is
  stdlib-only by convention; a 15-line `urllib` call with
  `X-Subscription-Token` replaces it. Client declined; **API adopted**.
- **Headless browsers / playwright** — r.jina.ai covers JS rendering without
  a browser fleet. Rejected for now.

## Design

1. **`_search_evidence` lane order** (same `{title,url,snippet}` contract,
   `needs_retry` semantics unchanged) — zero-key lanes primary, Brave backup:
   Bing News RSS (`url=`-unwrapped) → Google News RSS (titles+snippets) →
   current DDG/Bing html (degraded fallback) → **Brave (backup only**, keyed
   via `BRAVE_SEARCH_API_KEY` in `memory/.env`; the free limit requires
   attribution, which a headless JSONL pipeline cannot surface — reached only
   if both RSS lanes fail). All stdlib; no new dependencies.
2. **NEW `_fetch_page_text(url)`**: direct urllib with a realistic UA →
   r.jina.ai reader fallback; size-capped; returns page text. Used by the
   escalation re-judge to pull **verbatim quotes from result URLs** — the
   evidence standard ("flips require quoted evidence + URL") becomes
   implementable instead of snippet-luck.
3. **Evidence upgrade**: the batched evidence re-judge receives fetched page
   text (term-windowed truncations) in addition to snippets.
4. **Secrets**: none required. The optional Brave key (`BRAVE_SEARCH_API_KEY`
   in `memory/.env`, house channel) only arms the backup lane.

## Acceptance criteria

1. **TCS–MHP live probe** — the exact failed case: search → fetch → verbatim
   quote + resolved URL in one run, no operator-supplied URL.
2. Offline fixture tests for each parser: Bing news `url=` unwrap, Google
   News RSS XML, r.jina.ai markdown, Brave JSON (no network in tests).
3. Escalation acceptance re-run holds the floor: artifact rejection 37/37,
   Q2 ≥ 53/56 — with lane evidence instead of operator URLs.
4. Total search failure still degrades to `needs_retry` (never hangs, never
   blocks the human gate).
5. Unverifiable quotes never flip a verdict: `needs_retry` + `quote_unverified`
   (test: `test_escalate_unverifiable_quote_needs_retry_no_flip`).
6. Host gate: a source entity's own domain never evidences its row
   (test: `test_relevance_filter_tokens_and_host`).

## Cost and doctrine

$0 on the probed lanes; Brave free plan is optional headroom. Advisory only —
annotations remain report fields; `--apply-decisions` stays the only write
gate; nothing enters `make qa`.
