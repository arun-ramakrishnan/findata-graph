import { invoke } from '@tauri-apps/api/core'

// Thin typed-ish wrappers over the Tauri commands (findata-core).
// Tauri v2 camelCases: js `edgeType` ↔ rust `edge_type`.

export const stats = () => invoke('stats')

export const graphCloud = (edgeType = null, asOf = null) =>
  invoke('graph_cloud', { edgeType, asOf })

export const graphEgo = (name, asOf = null) => invoke('graph_ego', { name, asOf })

export const searchNotes = (q, docType = null, limit = 20) =>
  invoke('search_notes', { q, docType, limit })

export const suggest = (q, limit = 10) => invoke('suggest', { q, limit })

export const sectors = () => invoke('sectors')

export const readNote = (path) => invoke('read_note', { path })

export const entityNote = (name) => invoke('entity_note', { name })

export const browseDocs = (filter = null) => invoke('browse_docs', { filter })

export const entityMetrics = (name) => invoke('entity_metrics', { name })

export const metricValues = (metric) => invoke('metric_values', { metric })

export const entityHyperedges = (name, asOf = null) =>
  invoke('entity_hyperedges', { name, asOf })

export const similarNotes = (path, limit = 10) =>
  invoke('similar_notes', { path, limit })

export const searchHybrid = (q, limit = 20) =>
  invoke('search_hybrid', { q, limit })
