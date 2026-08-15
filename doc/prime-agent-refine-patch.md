# Prime Agent — REFINEMENT_MAX_OUTPUT_TOKENS Patch

After any `npm install -g prime-agent` update, the refinement output budget
reverts to the upstream default (32,000 tokens). This causes `refine.run()`
failures with reasoning-capable models like glm-5.2 because reasoning tokens
consume the output budget before the JSON plan can complete.

## The Problem

`refine.run()` delegates to a background LLM call that must produce a JSON
edit plan. The output token budget for this call is:
  `Math.min(model.maxTokens, REFINEMENT_MAX_OUTPUT_TOKENS)`
With reasoning enabled, thinking tokens count toward this limit. At 32K
the model frequently runs out before finishing JSON.

## Files to Patch (2 files, same change in each)

Prime Agent root:
  `/home/arun/.nvm/versions/node/v24.15.0/lib/node_modules/prime-agent`

1. **dist/core/refinement/refinement.js** (readable source)
   Change: `const REFINEMENT_MAX_OUTPUT_TOKENS = 32_000;`
   To:     `const REFINEMENT_MAX_OUTPUT_TOKENS = 96_000;`

2. **dist/bundle/chunk-VNU2AJHD.js** (bundled runtime — what actually executes)
   Change: `var REFINEMENT_MAX_OUTPUT_TOKENS = 32e3;`
   To:     `var REFINEMENT_MAX_OUTPUT_TOKENS = 96e3;`

## Also in models.json

File: `/home/arun/.prime/agent/models.json`

For glm-5.2 (under providers → Zhipu → models), ensure:
  `"maxTokens": 131072`

This makes the effective budget: `min(131072, 96000) = 96,000`.

## Quick Check Command

```bash
grep -n "REFINEMENT_MAX_OUTPUT"   ~/.nvm/versions/node/v24.15.0/lib/node_modules/prime-agent/dist/core/refinement/refinement.js   ~/.nvm/versions/node/v24.15.0/lib/node_modules/prime-agent/dist/bundle/chunk-VNU2AJHD.js
```

If you see `32_000` / `32e3`, you need to re-patch.

## Note: Bundle filename may change

The chunk filename `chunk-VNU2AJHD.js` is a content hash and WILL change
across versions. To find the correct bundle file after an update:

```bash
grep -rl "REFINEMENT_MAX_OUTPUT_TOKENS = 32e3"   ~/.nvm/versions/node/v24.15.0/lib/node_modules/prime-agent/dist/bundle/
```

## Restore After Update (one-liner)

```bash
PA=~/.nvm/versions/node/v24.15.0/lib/node_modules/prime-agent
sed -i 's/REFINEMENT_MAX_OUTPUT_TOKENS = 32_000/REFINEMENT_MAX_OUTPUT_TOKENS = 96_000/'   "$PA/dist/core/refinement/refinement.js"
sed -i 's/REFINEMENT_MAX_OUTPUT_TOKENS = 32e3/REFINEMENT_MAX_OUTPUT_TOKENS = 96e3/'   "$PA"/dist/bundle/chunk-*.js
```

# Refine Harness state files
Global :  /home/arun/.prime/agent/harness/harness_state.json
Session : /home/arun/.prime/agent/session-artifacts/019fe9ae-4fde-70bb-b981-4f0ac8ec9f92/harness/harness_state.json
