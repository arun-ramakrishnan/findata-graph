# AGPL source-offer runbook

This runbook covers the AGPL-3.0 section 13 source obligation for a
network deployment of FinData. It is an operator checklist, not legal
advice; the operator remains responsible for the final release decision.

## Before a release

1. Choose an immutable release tag or commit and record it in the deployment
   manifest.
2. Publish the corresponding first-party source for that exact version,
   including build scripts, lockfiles, frontend sources, and instructions
   needed to install and run the service.
3. Publish `LICENSE`, `NOTICE`, `THIRD_PARTY_LICENSES.md`, and the exact
   third-party license/notice files shipped with the selected dependency
   wheels or bundles.
4. Record the source URL in the release notes and make it reachable without a
   password or account.
5. Verify the published source can be unpacked and built using the documented
   commands in a clean environment.

## For a modified network deployment

1. Identify the running modified version and its exact source commit.
2. Offer the corresponding source to remote users through a stable source
   link, source archive, or documented network route.
3. Keep the source available for as long as the modified service is offered.
4. Preserve third-party notices and any additional source obligations for
   GPL, LGPL, AGPL, native, model, or bundled components.
5. Do not describe the deployment as a closed/proprietary service without an
   explicit legal review of the applicable AGPL obligations.

## Incident checklist

- **Source URL unavailable:** restore the release source before continuing
  network operation.
- **Dependency version changed:** refresh the third-party inventory and
  attach the new wheel/bundle notices before release.
- **Modified build differs:** publish the modified corresponding source and
  record its commit alongside the deployment.
- **Corpus or model included:** document its separate rights and do not imply
  that the project AGPL grant covers material the project does not own.
