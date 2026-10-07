import { invoke } from '@tauri-apps/api/core'
import * as v from 'valibot'
import * as S from './schemas'

// Thin wrappers over the 14 Tauri commands (findata-core). Every result is
// parsed through the valibot schema mirroring the Rust return type
// (lib/schemas.js) before it resolves — shape drift throws IpcShapeError
// naming the command and dot path, which store.js's existing try/catch
// shows in the status line instead of rendering quiet `undefined`s.
// Tauri v2 camelCases invoke *arguments*: js `edgeType` ↔ rust `edge_type`.
// Response fields stay snake_case (serde emits the Rust names as-is).

export class IpcShapeError extends Error {
  /**
   * @param {string} cmd
   * @param {string} detail
   */
  constructor(cmd, detail) {
    super(`${cmd}: ${detail}`)
    this.name = 'IpcShapeError'
    this.cmd = cmd
  }
}

/**
 * Invoke a Tauri command and validate its result against the schema
 * mirroring the command's Rust return type.
 *
 * @template T
 * @param {import('valibot').GenericSchema<T>} schema
 * @param {string} cmd
 * @param {Record<string, unknown>} [args]
 * @returns {Promise<T>}
 */
async function ipc(schema, cmd, args) {
  const data = await invoke(cmd, args)
  const result = v.safeParse(schema, data)
  if (!result.success) {
    const issue = result.issues[0]
    const where = v.getDotPath(issue) ?? '(root)'
    throw new IpcShapeError(cmd, `${where}: ${issue.message}`)
  }
  return result.output
}

export const stats = () => ipc(S.StatsSchema, 'stats')

export const graphCloud = (edgeType = null, asOf = null) =>
  ipc(S.CloudSchema, 'graph_cloud', { edgeType, asOf })

export const graphEgo = (name, asOf = null) => ipc(S.EgoSchema, 'graph_ego', { name, asOf })

export const searchNotes = (q, docType = null, limit = 20) =>
  ipc(S.SearchResultsSchema, 'search_notes', { q, docType, limit })

export const suggest = (q, limit = 10) =>
  ipc(v.array(S.SuggestHitSchema), 'suggest', { q, limit })

export const sectors = () => ipc(v.array(S.SuggestHitSchema), 'sectors')

export const readNote = (path) => ipc(S.NoteContentSchema, 'read_note', { path })

export const entityNote = (name) => ipc(v.nullable(S.NoteContentSchema), 'entity_note', { name })

export const browseDocs = (filter = null) =>
  ipc(v.array(S.DocEntrySchema), 'browse_docs', { filter })

export const entityMetrics = (name) => ipc(S.EntityMetricsSchema, 'entity_metrics', { name })

export const metricValues = (metric) => ipc(v.array(S.RankEntrySchema), 'metric_values', { metric })

export const entityHyperedges = (name, asOf = null) =>
  ipc(v.array(S.HyperEdgeSchema), 'entity_hyperedges', { name, asOf })

export const similarNotes = (path, limit = 10) =>
  ipc(v.array(S.SimilarHitSchema), 'similar_notes', { path, limit })

export const searchHybrid = (q, limit = 20) =>
  ipc(S.SearchResultsSchema, 'search_hybrid', { q, limit })
