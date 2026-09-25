---
title: "AGPL license migration and GPL component compatibility"
status: executed
filed: "2026-09-25"
executed: "2026-09-25"
completed_md: "291"
area: "repository"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json -->

# AGPL license migration and GPL component compatibility

**Date:** 2026-09-25 · **Status:** EXECUTED · **Priority:** 0 / operator push first ·
**Area:** repository licensing, packaging metadata, dependency governance, and deployment documentation

Adopt the GNU Affero General Public License, version 3 or later
(`AGPL-3.0-or-later`), for FinData's first-party source code while keeping
third-party code, source material, data, generated databases, models, and
other external assets under their existing rights and licenses. The goal is
reciprocal source availability for network use and compatibility with
copyleft components such as python-igraph; it is **not** a prohibition on
commercial use.

This is a licensing and governance proposal, not an immediate igraph
algorithm migration. No dependency is added and no existing file is
relicensed until the ownership, scope, and compatibility gates below are
completed.

## 1. TL;DR

The repository currently has no root `LICENSE`, `COPYING`, or `NOTICE` file,
and `pyproject.toml` has no SPDX/project license metadata. That is an
ambiguity risk for a codebase that already mixes first-party Python,
TypeScript, Mojo, generated static assets, and a large source-derived
`findata/` corpus.

The proposed operating model is:

- first-party code is `AGPL-3.0-or-later`;
- commercial use remains permitted;
- network deployments must provide the corresponding source for modified
  versions, with a stable source/version route and runbook;
- third-party dependencies retain their own licenses and notices;
- source-derived market newsletters, PDFs, images, models, databases, and
  other external material are explicitly outside the first-party code grant
  unless the operator separately identifies rights to relicense them;
- python-igraph may be adopted as an optional GPL-compatible research or
  algorithm lane after license, build, GLPK/wheel, and distribution review;
  it does not replace the current Onager default in this proposal.

AGPL does not “prevent abuse by commercial use” in the sense of banning
commercial use. It creates reciprocal obligations, especially for modified
programs offered over a network. This proposal uses that mechanism
accurately.

## 2. Current state and evidence

1. **No project license.** The root has no `LICENSE*`, `COPYING*`, or
   `NOTICE*`; `pyproject.toml` declares the project but no `license` field.
   `frontend/package.json` is private and also has no license metadata.
2. **Mixed asset boundary.** `findata/` contains converted newsletter and
   company-note material, `Reports/` and `static/` contain external or
   generated assets, `models/` and `memory/` contain external models and
   derived databases, and the code directories are first-party project
   material. A blanket repository statement must not accidentally claim
   rights over source documents or third-party bundles.
3. **Existing dependency licenses.** The direct runtime graph stack is
   DuckDB, SciPy, NumPy, HypergraphX, and the Onager DuckDB community
   extension; the graph-layer evaluation records Onager as Apache-2.0 and
   HypergraphX as BSD-3. `python-igraph` is recorded as GPL-2.0-or-later,
   with possible additional obligations for binary builds linked against
   GLPK. NetworkX was retired and must not be reintroduced accidentally.
4. **Packaging/deployment surfaces.** Python is packaged through
   `pyproject.toml` with `package = false`; the frontend is a private npm
   project whose bundle is committed to `static/`; the Flask API is a network
   service and therefore the primary AGPL source-offer surface.

## 3. Design decisions

1. **SPDX choice:** use `AGPL-3.0-or-later` for first-party code, pending
   operator/legal confirmation. The `-or-later` suffix preserves the
   project's ability to use future AGPL-compatible versions and aligns with
   GPL-2.0-or-later components such as igraph; it is a policy decision, not
   an automatic compatibility guarantee.
2. **Scope:** the grant covers code authored for the project. It excludes
   third-party dependencies, imported source documents, PDFs, images,
   newsletters, model weights, generated databases, snapshots, and other
   external material unless a separate rights inventory explicitly grants
   relicensing rights.
3. **Network compliance:** a deployment guide must state how users obtain
   the corresponding source for the running version and how modified
   versions are offered. The implementation may use a public repository,
   source archive, or stable `/source`-style route, but the choice must be
   explicit and tested before network release.
4. **Commercial use:** no non-commercial restriction, no source-available
  “fair use” carve-out, and no claim that AGPL bans commercial use. The
  control is copyleft/source reciprocity, with legal review for commercial
  distribution and managed-service scenarios.
5. **Dependency policy:** GPL/LGPL/AGPL components are allowed only after
  their exact version, linking/embedding model, notices, source-offer
  obligations, and distribution artifacts are recorded. A dependency is not
  approved merely because its package metadata says “GPL”.
6. **igraph posture:** keep Onager as the default engine. Add igraph only as
  an explicitly optional, compatibility-reviewed lane after the licensing
  inventory; do not change graph algorithms, routing, or the public API in
  the license migration patch.

## 4. Slices

1. **S0 — ownership and legal inventory.** Identify first-party code,
   contributor-owned material, third-party source, source-derived data,
   model weights, generated outputs, and deployment artifacts. Confirm that
   the operator has the rights to license the proposed first-party scope and
   identify any contributor or employment agreement constraints.
2. **S1 — root license and metadata.** Add the full AGPL-3.0 text as
   `LICENSE`, add `NOTICE`/third-party attribution conventions, set the
   SPDX license metadata in `pyproject.toml`, add the license to the README,
   and document contribution/derivative-work expectations. Do not add
   per-file headers mechanically unless a later legal review requires them.
3. **S2 — dependency and asset manifest.** Generate a checked-in inventory of
   direct and optional Python, native-extension, JavaScript, Mojo, model,
   and corpus licenses. Include SPDX identifiers, versions, source URLs,
   obligations, and whether the artifact is redistributed, linked, or merely
   fetched at runtime. Add a CI/static check for missing or unknown entries.
4. **S3 — igraph compatibility gate.** Verify the exact python-igraph and C
   core versions, wheel contents, GLPK linkage, LGPL/GPL notices, source
   availability, and compatibility with AGPL-3.0-or-later. Prototype it in
   an isolated optional extra or benchmark environment; do not make it a
   required dependency or alter the Onager default in this slice.
5. **S4 — AGPL network-source runbook.** Document source offer for the exact
   deployed commit/version, modified-source delivery, build instructions,
   third-party source offers, and the operator's release checklist. Test the
   source route against a running Flask deployment.
6. **S5 — release enforcement.** Add a lightweight repository check for
   root license presence, package metadata, third-party manifest freshness,
   and forbidden/unknown license entries. Add the check to the existing
   static/QA path without making a legal determination from a heuristic.

## 5. Acceptance criteria and shakedown

1. Root `LICENSE` is the complete AGPL-3.0-or-later text; `pyproject.toml`
   and README carry the same SPDX identifier; no contradictory project
   license statement remains.
2. The ownership inventory names the first-party code scope and explicitly
   excludes or separately handles third-party source, corpus material,
   models, generated databases, and snapshots.
3. The dependency/asset manifest covers every direct runtime dependency,
   optional dependency, native extension, frontend package, and non-code
   asset class; every GPL/LGPL/AGPL entry has a recorded compatibility and
   notice decision.
4. python-igraph is either accepted into a clearly optional lane with a
   recorded license/build decision or explicitly deferred with a named
   blocker. No unreviewed igraph dependency is merged.
5. A network deployment has a tested corresponding-source route or a signed
   operator runbook that satisfies the AGPL network obligation.
6. The license/asset check fails closed on a missing root license, unknown
   manifest entry, or changed dependency set.
7. `make static-checks` and the focused licensing test pass; `make qa` is
   run by the operator on the full arc. No eval gate is required: this
   changes licensing/governance metadata, not domain rosters, crosswalks,
   hierarchies, or extraction semantics.

## 6. Risks and legal boundaries

- **AGPL is not a commercial-use ban.** Commercial deployment is allowed;
  modified network use must satisfy the corresponding-source obligation.
  The operator should obtain legal review for the intended distribution and
  service model.
- **Existing versions remain governed by prior grants.** Adding AGPL does
  not retroactively revoke rights already granted under another license for
  earlier versions; release boundaries must be explicit.
- **Third-party content is not automatically ours.** A root code license
  must not imply ownership of newsletters, PDFs, images, model weights, or
  generated data. The inventory must keep those artifacts separate.
- **GPL compatibility is version- and distribution-specific.** igraph's
  GPL-2.0-or-later status is a strong input, not a complete legal conclusion;
  GLPK, wheels, linking, modifications, and notices require review.
- **Copyleft can reduce adoption.** That is an intentional policy tradeoff,
  not a technical failure; the proposal should make the tradeoff explicit
  rather than describe AGPL as abuse prevention.

## 7. Implementation log (2026-09-25)

The first implementation slice is present in the current working tree:

- Added the canonical AGPL-3.0 text as root `LICENSE`.
- Added `AGPL-3.0-or-later` and `license-files = ["LICENSE"]` to
  `pyproject.toml`; added the same SPDX field to the private frontend
  package metadata.
- Added `NOTICE`, `THIRD_PARTY_LICENSES.md`, and a first/third-party asset
  boundary; no corpus, model, database, or external source material was
  relicensed.
- Added a `make license-check` target and wired it into `static-checks`.
- Added `doc/procedures/agpl_source_offer.md` for the network source-offer
  and modified-deployment checklist.
- Added the README license pointer and focused regression coverage.
- igraph remains an optional candidate only; no dependency or algorithm
  change was made.

The operator has now accepted the AGPL migration as the repository policy
and closed the legal/ownership gate for this arc. igraph remains an
optional candidate only; no dependency or algorithm change was made.

## 8. Non-goals

- No immediate igraph implementation, algorithm replacement, or Onager
  removal.
- No non-commercial-use restriction.
- No automatic relicensing of third-party dependencies, source documents,
  data, models, generated databases, or prior releases.
- No legal conclusion or license compatibility determination without
  operator/legal review.
- No mass per-file header rewrite before the ownership inventory is done.

## Appendix — current audit record

| Surface | Current state | Proposal action |
|---|---|---|
| Root license | No `LICENSE`/`COPYING`/`NOTICE` found | Add AGPL text and metadata |
| Python metadata | `pyproject.toml` has no `license` field | Set SPDX `AGPL-3.0-or-later` |
| Frontend | `frontend/package.json` is private, no license field | Document code scope; inventory npm packages |
| Graph engine | Onager Apache-2.0; NetworkX retired | Preserve default; license manifest |
| igraph candidate | GPL-2.0-or-later; GLPK/wheel obligations noted | Optional compatibility gate only |
| Network service | Flask API and deployed frontend | Corresponding-source runbook |
| Corpus/assets | `findata/`, `Reports/`, `models/`, `memory/` | Separate rights scope; no blanket data relicense |
