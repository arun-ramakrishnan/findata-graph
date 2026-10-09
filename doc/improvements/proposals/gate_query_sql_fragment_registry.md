---
title: "gate_query SQL: explicit registry and membership gating for S608 filter fragments"
status: proposed
filed: "2026-10-09"
executed: null
completed_md: null
area: "helpers/misc/gate_query.py + helpers/misc/review_scan.py"
---

## 1. Motivation

`helpers/misc/gate_query.py` carries 35 `# noqa: S608` annotations across six SQL statements. Those findings are all *benign today* — every interpolated fragment is literal-built and every value is `?`-bound — but the safety contract is **implicit**, sitting in scattered ternaries and list appends. House checklist §H expects identifier/fragment interpolation to be manifest/allowlist-gated. This proposal makes that gating explicit and self-enforcing so future edits can't silently reintroduce unbounded interpolation.

## 2. Evidence (the six sites)

Every S608 statement in the file was inspected; none interpolates runtime input into an identifier position.

| Statement (line) | Function | Interpolation | Safety |
|---|---|---|---|
| `WHERE {" AND ".join(where)}` (~1385) | `cmd_artifacts` | `" AND ".join` of literal fragments (`"a.run_id = ?"` etc.) | SAFE — literals; `params` get the `?` values |
| `WHERE {" AND ".join(where)}` (~1926) | `_cluster_payload` | `" AND ".join` of literal fragments (`"tf.error_fingerprint IS NOT NULL"` etc.) | SAFE — literals; `args.fingerprint` is param-bound |
| `WHERE run_id IN ({placeholders})` (~2034) | `_test_phase_totals` | `",".join("?" for _ in run_ids)` | SAFE — only `?` placeholders |
| `WHERE b.bench = ?{status_clause}` (~2056) | `cmd_timing` | literal ternary (`" AND b.status NOT LIKE '%FAIL%' AND b.status NOT LIKE '%SKIP%'"` or `""`) | SAFE — code-built literal |
| `WHERE l.leg = ?{leg_clause}` (~2066) | `cmd_timing` | same literal ternary on `l.leg` | SAFE — code-built literal |
| `WHERE tf.run_id IN ({placeholders})` (~2105) | `cmd_timing` (critical path) | same `?` placeholder join | SAFE |

Conclusion: the current risk is nil, which is why the 35 noqas have been adequate. The hardening closes the gap between *implicit* and *assertive* safety.

## 3. Design

**Registry.** Add a single module-level mapping of allowed filter fragments:

```python
_FILTER_CLAUSES: dict[str, str] = {
    "notfail_notskip": (
        " AND {table}.status NOT LIKE '%FAIL%' AND {table}.status NOT LIKE '%SKIP%'"
    ),
}
```

**Accessor.** One helper that exposes membership as the contract:

```python
def _filter_clause(table: str, key: str) -> str:
    """Return an allowed filter fragment; KeyError if a new key is attempted."""
    return _FILTER_CLAUSES[key].format(table=table)
```

**Refactor.** Replace the two ternaries with registry lookups:

```python
status_clause = _filter_clause("b", "notfail_notskip") if pass_only else ""
leg_clause    = _filter_clause("l", "notfail_notskip") if pass_only else ""
```

**Preserve the good pattern.** The `.join(where)` builders (`cmd_artifacts`, `_cluster_payload`) already append only literal fragments with `?` params — they are the canonical safe pattern; leave them and note the precedent in the registry docstring.

**Noqa annotations.** Keep the existing `# noqa: S608` lines but annotate them to point at the registry, e.g.:

```python
WHERE {" AND ".join(where)}  # noqa: S608 (fragments are literal appends, see _FILTER_CLAUSES)
```

Empirically test whether the `{" AND ".join(literals)}` and `({"??"})` forms still trip bandit/ruff; if they pass cleanly without noqas, drop them — that is the aspirational acceptance, not the minimum.

## 4. Acceptance

1. `ruff check --select S608 helpers/misc/gate_query.py` clean; bandit B608 clean on any range touching these statements. **MET** — ruff clean; all 8 bandit sites adjudicated (verified against the real `review_scan._adjudicated`).
2. No f-string braces interpolate a computed Python value outside the registry (grep guard in CI: `f"{...}"` in an execute call implies either the registry accessor or a literal join of literals). **MET** — the two `status_clause`/`leg_clause` sites route through `_filter_clause`; the remaining five interpolate literal-join builders and `?`-placeholder joins.
3. `keyerror`/assertion fires when an unknown fragment key is referenced (mutation test). **MET** — `test_filter_clause_unknown_key_raises_keyerror`, plus the unknown-table guard.
4. All existing gate_query tests continue to pass, exercising both pass-only and non-pass-only branches. **MET** — 82 pass across `tests/test_gate_query.py` + `tests/test_review_scan.py`.
5. `make review-scan` on a range touching gate_query reports zero new S608/B608 findings. **MET** — `review_scan.py STACK=1` surfaces no B608/S608.

## 5. Slices

- **S1** — Enumerate and classify all six fragment sites (done in evidence; recorded on filing).
- **S2** — Add `_FILTER_CLAUSES` registry + `_filter_clause()` accessor + docstring precedent note. **DONE.**
- **S3** — Refactor `status_clause`/`leg_clause` ternaries to registry lookups. **DONE.**
- **S4** — Tests: unknown-key membership assertion; regression on pass_only True/False for bench and leg paths; mutation: delete an entry → assertion. **DONE** (5 tests).
- **S5** — Annotate existing noqas to reference the registry; empirically attempt noqa removal for the literal-join forms. **DONE, with a finding — see §8.** Removal is not achievable: ruff anchors S608 on the multi-line f-string's *first* line, and a directive there lands inside the literal (DuckDB rejects `#` in SQL). The directives stay, on the closing line where ruff's span ends, and the review gate's statement walk was widened to match (S7).
- **S6** — Verify ruff S608 + bandit B608 clean (acceptance 1–2). **DONE.**
- **S7** — Review-scan regression (acceptance 5). **DONE** — required fixing `review_scan._statement_span`; see §8.

## 8. Execution finding: the two linters anchor on different lines

The S5 experiment produced a real defect in the review gate, not just a placement nuisance.

- **bandit** cites the **first** physical line of a multi-line f-string. Paren depth there is 0, so the existing `_statement_span` walk stopped immediately and never reached the house noqa on the closing line — every gate_query B608 finding survived *unadjudicated* while looking, in the source, exactly like an adjudicated one. (This is why the 4fd3a20f..bda590995 roster showed 7 B608 sites carrying S608 noqas.)
- **ruff** anchors the diagnostic on the same first line, but honours a directive on the line where the multi-line string *ends*.

The two anchor lines therefore disagree, and no single-line placement satisfies both. `_statement_span` now also covers the body of a triple-quoted literal opened at the cited line (`_string_close_line`), bounded by the existing `_NOQA_SPAN_CAP`, so both tools' anchors land inside one span. Pinned by 4 new tests, including the neighbour-leak guard that keeps the wider walk from becoming a new suppression channel; mutation-checked by disabling the walk (the drop reverts to False).

An intermediate attempt put `# noqa: S608` on the f-string's first line to match bandit's anchor. That is **invalid**: the directive became SQL text, and DuckDB rejects it (`ParserException: syntax error at or near "#"`). Reverted — all 7 directives now sit after the closing quotes, and no directive text reaches any query string.

## 6. Risks / blast radius

One module, six statements, ~100 lines changed. Assertions add a dict lookup per query call — negligible. The one genuine risk is regressions if a code path builds a fragment dynamically somewhere else in the module; the accessor catches those at runtime the first time they're exercised, so test coverage of `cmd_timing` (both branches) is the key coverage lever.

Realised: the restore-the-SQL-text step after moving directives did corrupt one statement's column aliases (`r.src_rel`/`r.junit_path`) and two indents. Caught by `tests/test_gate_query.py::test_failure_clusters_use_normalized_fingerprints` (BinderException), not by any lint gate — the lesson recorded for future slices: verify these edits by diffing against the pre-change file, not by trusting the green linter.

## 7. Relation to prior work

The six findings come from the `4fd3a20f..bda590995` review (all three classes already adjudicated/resolved at HEAD via noqas or code rewrites). This is the §H hardening the operator flagged as the only non-defect improvement worth doing; it is deliberately not a security fix — it makes the existing implicit safety explicit so the noqas become a documented registry rather than scattered assertions.
