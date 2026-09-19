# Security assessment — the full scan procedure

The operator procedure for a security pass over this repo: what to run,
in what order, and how to record the result so coverage accumulates.
Normative record: `doc/local/security/security_evaluation.md` (the
findings, phases, and numbered addenda — read it first, it is the
authoritative state). This document is the *how*; that one is the *what*.

## When to run

- **Scheduled.** Once per arc, or before any change to the exposure
  posture (a new route, a new external egress, a new parser, a deploy).
- **Drift-triggered.** The record is a snapshot, not a watchman. After a
  proposal lands that touches `app.py`, any `helpers/` network path, or
  the ingestion pipeline, diff the route inventory (below) against the
  last recorded one and re-audit only the delta.
- **Never on a live or shared target.** Probes are `127.0.0.1` only,
  idempotent, and never mutate shared state. Bounded local fixtures,
  never payload chains.

## The verdict contract (apply this before writing anything down)

Every candidate gets exactly one of:

| Verdict | When |
|---|---|
| `confirmed` | Source evidence establishes the full boundary **and** a bounded local observed result reproduces the outcome. Both halves, or it is not confirmed. |
| `needs_validation` | A specific deployment, provider, or runtime fact is unavailable. State the missing fact and the owner-observed check that resolves it. |
| `rejected` | The candidate does not survive the gate — record *why*, so the next pass does not re-find it. |

**The candidate gate.** Before a candidate can be `confirmed`, answer:
which principal is affected, what is the security outcome, and is a bound
actually missing? A slow endpoint is not a finding until you show an
unauthenticated actor can force it repeatedly. A risky-looking regex is
not a finding until it blows up on generated input. If you cannot name
the outcome, it is a **hardening note**, not a finding — record those
separately; they become findings the day the deploy condition changes.

Severity is exploit-today ordering, not CVSS.

## The method, in order

### 1. Route inventory (do it the deterministic way)

```bash
rg -n --no-heading '@app\.route' app.py
```

Then read each decorator's *arguments*, not just the path literal. **The
trap:** an inventory built by matching `@app.route("<literal>")` silently
drops every route whose decorator carries a `methods=` argument — this
repo's mutating `POST /api/graph/refresh` was nearly mis-recorded as
removed that way. Count decorators, then cross-check the count against
the last recorded figure (the addenda state it).

Diff against the record: routes present now but absent from the last
inventory are the delta — they get steps 2–4 in full.

### 2. Dangerous-pattern sweep

```bash
rg -n --type py --no-heading 'shell\s*=\s*True|\beval\s*\(|\bexec\s*\(|pickle\.load|yaml\.load\(|verify\s*=\s*False|\binput\s*\(|os\.system\('
rg -n --no-heading 'innerHTML' frontend/src static templates  # ~100 sites; check escapeHtml coverage at each
rg -n --type py --no-heading 'f"SELECT|f"INSERT|f"UPDATE|f"DELETE'   # f-string SQL
```

An f-string SQL hit is **not** a finding — check what it interpolates.
This repo's verified-safe pattern interpolates only int-cast limits,
fixed dimensions, and generated `?`-placeholder lists; every user value
is a bound parameter. Trace it before you claim it.

### 3. Trace entry → control → sink

For every handler that touches the filesystem, builds SQL, or echoes
input: the request value, every transformation, and the sink. The
question is always whether user data reaches the sink as *data* or as
*structure*.

- **Path converters** (`<path:name>`) are safe when the value stays a
  bound SQL argument and never opens a file — a `findata/` prefix is a
  cosmetic convenience, not a traversal control, and must be recorded as
  such so nobody later mistakes it for one.
- **Name dispatch** (`/api/analytics/<name>`) needs an exhaustive
  hardcoded allowlist — ideally two, one at the route and one at the
  registry.
- **Echoed input** must pass through `jsonify`/`escapeHtml`. A 500 body
  that echoes `{e}` is internal-info disclosure (JSON, so no XSS path) —
  a hardening note, not a finding.

### 4. Bounded local probes (only when a candidate survives the gate)

Never probe a clean surface "to be sure." Probe only to convert a
candidate into an observed result:

- **A robustness claim in a docstring** is a candidate — exercise it. The
  FTS quoting claim was verified by running `fts_match_expr` over 13
  adversarial queries against an in-memory FTS5 table: all valid, zero
  errors.
- **A quadratic or unbounded compute** is a candidate — measure it
  against the real DB, read-only, with the route's exact SQL. Sweep the
  input size and confirm the growth law; a single timing is anecdote, a
  sweep is a verdict.
- **A risky regex** is a candidate — never reason about it from shape.
  `_ATTR_RE` and `_SPEAKER_NCT_RE` both *look* like ReDoS and are both
  linear; only randomized search (Hypothesis, targeted alphabets, a few
  thousand examples) settled it.

### 5. Attack-class sweep (the class-by-class model)

Route-by-route audit finds injection. It does **not** find
availability — that class had zero coverage until a dedicated sweep
found AVAIL-1, the repo's highest-severity finding since the original
pass. Review by attack class, not by route:

| Class | Status here | Notes |
|---|---|---|
| Injection (SQL/command/XSS/template) | covered, clean | 2026-08-17 + 13-route re-audit 2026-09-18 |
| Web protocol / auth | covered | SEC-1..5; Phase 4 dormant, deploy-gated |
| Client-side / DOM | covered | SEC-4 + full `innerHTML`/`escapeHtml` audit |
| Supply chain / release | covered, beyond skill | `pip-audit`, extension pinning (D8), key hygiene (SEC-9, closed) |
| Secrets / PII in vault | covered | vault + snapshots content scan |
| Resource exhaustion / availability | covered 2026-09-18 | Addendum 3; **AVAIL-1 confirmed** |
| AI / LLM | n/a | embeddings only, no chat completion, no tool-calling |
| Protocols / RPC / messaging | n/a | no gRPC / GraphQL / protobuf |
| Desktop / mobile / IPC | n/a | — |
| Memory safety / binary | watch | parsers are vendored C++ (PyMuPDF, DuckDB, sqlite-vec) — Python surface reviewed, internals not |
| Cloud / deployment | partial | nixpacks/Railway config; SEC-5 deploy-gated |

When you run a class for the first time, expect the finding there: every
confirmed finding in this repo's history came from a class that had never
been swept, not from re-sweeping a covered one.

### 6. Verified-clean needs evidence too

A clean verdict is a claim. Record the method that produced it (source
review vs. observed result) so the next pass knows its strength — and
promote one-off measurements into regression tests (extend
`tests/test_fuzz_regex.py` rather than leaving a measurement in prose).

### 7. Regression test per confirmed finding

One test per confirmed finding, in the house Hypothesis / route-integration
pattern, riding `make qa`. A test that passes today by asserting current
unbounded behaviour is not a guard — file the test with the fix, not with
the finding.

## Trap list (failure modes this repo has already hit)

1. **Literal-match route inventories drop `methods=` routes.** Count
   decorators, read arguments.
2. **Coverage is a snapshot.** 13 routes shipped after the record closed
   with no review. Diff against the record; the delta is the work — or
   run the validator (below), which fails on any route with no ledger
   row.
3. **A clamp on output is not a clamp on work.** `limit` bounds the result
   set, not the compute. Bound the work, or memoize, or cap the scale.
4. **An `after_request` hook cannot save CPU.** ETag/304 logic runs after
   the view; it strips bodies, it does not skip queries.
5. **Shape analysis of regexes is worth nothing.** Measure with
   randomized search; two textbook-ReDoS shapes were both linear.
6. **A docstring's cost claim goes stale silently.** Quadratic cost under
   corpus growth; re-measure against the live DB, do not cite the number.
7. **A noqa or prefix that reads like a control invites
   misattribution.** Record explicitly when something is *not* a security
   control.

## Recording the result

- Append a numbered **Addendum** to
  `doc/local/security/security_evaluation.md`. Do not open a parallel
  record — one document accumulates the state.
- Each addendum carries: a verdict table for every surface screened, the
  full source trace + observed result for each confirmed finding, the
  trigger model (remote vs. operator-triggered) for every note, and the
  coverage consequence.
- A confirmed finding spawns its **own small fix proposal** carrying the
  regression test; the assessment changes no source.
- md-lint clean; `make search-fresh` advisory after the edit (operator
  applies).
- **Run the coverage validator** — the completeness gate:

  ```bash
  .venv/bin/python3 helpers/validators/coverage_ledger.py check
  ```

  It fails if any `app.py` route has no row in
  `doc/local/security/coverage-ledger.json` (a new route shipped
  unreviewed), if a reviewed handler changed since its review
  (`--strict`), or if a row names a route that no longer exists. Seed new
  rows by hand after a review — the `seed` mode recomputes fingerprints
  only and never invents coverage. This is what makes the record
  cumulative instead of a snapshot.

## Pointers

- Record + findings: `doc/local/security/security_evaluation.md`
- Current fix proposals: `doc/improvements/proposals/` (the AVAIL-1 cap
  and the coverage-expansion arc)
- External reference (selectively imported, not adopted wholesale):
  `cloudflare/security-audit-skill` — the verdict taxonomy, the candidate
  gate, and the coverage-ledger concept are what this procedure carries
  over from it; the six-phase orchestration and promotion procedure are
  not.
