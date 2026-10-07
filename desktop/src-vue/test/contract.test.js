// S3 contract gate (desktop_ipc_shape_guards): every fixture payload must
// satisfy the valibot schema mirroring its command's Rust return type.
// `make fixtures` regenerates the payloads from the live research.db — so a
// Rust struct change (or its make_fixtures.py mirror) that outpaces
// lib/schemas.js fails HERE, naming the command and field. This file is
// node-only (node:test); it is excluded from the checkJs surface in
// jsconfig.json. Run via `make check` / `make contract`.
//
// Rust-side core/tests/live.rs remains the producer's own net; this is the
// consumer-side twin.

import test from 'node:test'
import assert from 'node:assert/strict'
import * as v from 'valibot'
import { FIXTURES } from './fixtures.js'
import * as S from '../src/lib/schemas.js'

// fixture key → { schema, cmds } — cmds are the api.js wrapper(s) the
// harness (test/main.js) serves this payload to.
const CONTRACT = {
  stats: { schema: S.StatsSchema, cmds: ['stats'] },
  sectors: { schema: v.array(S.SuggestHitSchema), cmds: ['sectors'] },
  ego: { schema: S.EgoSchema, cmds: ['graph_ego'] },
  search: { schema: S.SearchResultsSchema, cmds: ['search_notes'] },
  note: { schema: S.NoteContentSchema, cmds: ['read_note', 'entity_note'] },
  suggest: { schema: v.array(S.SuggestHitSchema), cmds: ['suggest'] },
  cloud: { schema: S.CloudSchema, cmds: ['graph_cloud'] },
  docs: { schema: v.array(S.DocEntrySchema), cmds: ['browse_docs'] },
  metrics: { schema: S.EntityMetricsSchema, cmds: ['entity_metrics'] },
  metric_values: { schema: v.array(S.RankEntrySchema), cmds: ['metric_values'] },
  hyperedges: { schema: v.array(S.HyperEdgeSchema), cmds: ['entity_hyperedges'] },
  similar: { schema: v.array(S.SimilarHitSchema), cmds: ['similar_notes'] },
  hybrid: { schema: S.SearchResultsSchema, cmds: ['search_hybrid'] },
}

// The 14 #[tauri::command]s of src-tauri/src/main.rs, in handler order.
const ALL_COMMANDS = [
  'stats',
  'graph_cloud',
  'graph_ego',
  'search_notes',
  'suggest',
  'sectors',
  'read_note',
  'entity_note',
  'browse_docs',
  'entity_metrics',
  'metric_values',
  'entity_hyperedges',
  'similar_notes',
  'search_hybrid',
]

test('every Tauri command has a fixture contract', () => {
  const covered = new Set(Object.values(CONTRACT).flatMap((c) => c.cmds))
  assert.deepEqual(
    ALL_COMMANDS.filter((cmd) => !covered.has(cmd)),
    [],
    'commands without a fixture contract',
  )
})

for (const [key, { schema, cmds }] of Object.entries(CONTRACT)) {
  test(`fixture "${key}" satisfies the ${cmds.join('/')} contract`, () => {
    assert.ok(key in FIXTURES, `fixtures.js is missing "${key}"`)
    const result = v.safeParse(schema, FIXTURES[key])
    const detail = result.success
      ? ''
      : ': ' +
        result.issues
          .slice(0, 3)
          .map((i) => `${v.getDotPath(i) ?? '(root)'}: ${i.message}`)
          .join('; ')
    assert.ok(result.success, `${cmds.join(', ')} fixture drifted${detail}`)
  })
}
