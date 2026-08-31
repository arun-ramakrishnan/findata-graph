# doc/templates — house seeds

A **template** (seed) answers "how do I start a NEW artifact correctly?";
a **contract** (schema, `doc/okf/frontmatter.*.v1.json`) answers "is
this EXISTING artifact valid?" — different questions, both gated: seeds
by the guard tests in `tests/test_templates.py`, contracts by their
validators (`frontmatter_schema` under `make static-checks`). Pairing is
bidirectional and enforced — every note seed declares `# schema:` up
top, every note schema carries an `x-template` back-pointer, and the
`PAIRINGS` registry in `tests/test_templates.py` is the single
declaration point. **Unpaired is a failure state, not a default**: a new
schema or seed without its partner reddens the guard, so adding a
pairing is a conscious, reviewable edit.

## Index

| Template | Contract | Validator | Gate |
|---|---|---|---|
| company_note.yaml | doc/okf/frontmatter.company.v1.json | frontmatter_schema | make static-checks |
| sector_note.yaml | doc/okf/frontmatter.sector.v1.json | frontmatter_schema | make static-checks |
| super_sector_note.yaml | doc/okf/frontmatter.super_sector.v1.json | frontmatter_schema | make static-checks |
| newsletter_note.yaml | doc/okf/frontmatter.newsletter.v1.json | frontmatter_schema | make static-checks |
| python_module.py | pyproject.toml [tool.ruff] | tests/test_templates.py | make lint + lint-audit (qa) |
| test_module.py | pytest.ini + ruff S101 ignore | tests/test_templates.py | default pytest (qa) |
| mojo_module.mojo | mojo format | copy-diff gate | tests/test_lint_gates.py (qa) |
| ts_module.ts | tsconfig + esbuild bundle doctrine | tests/test_templates.py | advisory (frontend-check) |
| proposal.md | doc/okf/frontmatter.proposal.v1.json | frontmatter_schema + Proposal lifecycle | make static-checks |

## Language matrix

Adopting a language means adding a row and following it — never
inventing a new gate pattern (corpus_uniformity proposal §7).

| Language | Seed | Format gate | Types gate | Doc extraction | Placement |
|---|---|---|---|---|---|
| Python | python_module.py / test_module.py | ruff format --check (S7, pending) | ty (make types / types-tests) | AST docstrings → script_search | qa |
| Mojo | mojo_module.mojo | mojo format copy-diff | compiler | mojo doc → kind='mojo' | qa |
| TypeScript | ts_module.ts | prettier --check (frontend-check) | tsc --noEmit | extract_ts_docs.mjs → kind='ts' | advisory |
| JavaScript | none — no first-party JS (rule, not omission) | prettier ignores static/ | — | — | — |
| Markdown notes | YAML seeds above | — | frontmatter validators | doc_search | static-checks |

## Notes on the seeds

- The four note YAMLs are SEEDS for hand-written notes; the newsletter
  one is a documentation seed (its tree has exactly one producer pair —
  `pdf_conv_md.py` and the okf_backfill process — never hand-write it).
- `doc/procedures/markdown_parse.md` keeps the frontmatter RULES and
  links these seeds; it no longer carries an inline YAML copy
  (duplicated contracts drift).
