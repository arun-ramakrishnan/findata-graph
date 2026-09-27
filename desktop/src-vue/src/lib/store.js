import { reactive } from 'vue'
import * as api from './api'

// ---------------------------------------------------------------- state ----

export const store = reactive({
  // graph
  mode: 'ego', // 'ego' | 'cloud'
  ego: null, // Ego | null
  cloud: null, // Cloud | null  (loaded once per as_of, filtered client-side)
  cloudAsOf: null, // as_of the cached cloud was fetched with
  asOf: '', // '' = unfiltered; 'YYYY' | 'YYYY-MM' | 'YYYY-MM-DD'
  hiddenEdgeTypes: {}, // { [edgeType]: true } → chip toggles
  hiddenRev: 0, // bumped on toggle so GraphView can watch cheaply

  // selection + detail
  selected: null, // { name, entity_type, file_path } | null
  selectedEgo: null, // incident edges for the selected node
  selectedSeq: 0,

  // note viewer
  note: null, // NoteContent | null

  // search
  searchQ: '',
  results: [],
  searchInfo: null, // { total, query } | null
  searching: false,
  hybrid: false, // S7: fuse BM25 ranks with cosine ranks (PRF-RRF)

  // similar notes (S7: cosine over stored section vectors)
  similar: [],
  similarFor: null,

  // nav / legend
  stats: null,
  sectors: [],

  // docs browser (S2: doc/ vault listing, not in the FTS index)
  docs: [],
  docsFilter: '',
  docsLoading: false,

  // metrics + rank tint (S3: read-only over graph_analytics/company_metrics)
  metrics: null, // { analytics: [{metric, value}], company: [{label, ...}] } | null
  metricsFor: null,
  tintMetric: 'off',
  tintValues: {}, // { [entityName]: number }
  tintMax: 0,
  tintRev: 0, // bumped on tint change so GraphView repaints cheaply

  // hypergraph lane (S5: read-only over hyper_edges/hyper_incidences)
  hyperedges: [], // HyperEdge[] for the current selection
  hyperFor: null,
  hyperFocus: null, // hyperedge id | null → halo overlay in GraphView
  hyperRev: 0, // bumped on focus change so GraphView repaints cheaply

  // chrome
  busy: false,
  status: 'Ready',
})

// ---------------------------------------------------------------- actions -- //

export async function init() {
  store.status = 'Loading stats…'
  try {
    const [st, secs] = await Promise.all([api.stats(), api.sectors()])
    store.stats = st
    store.sectors = secs
    store.status = `${st.entities.toLocaleString()} entities · ${st.edges.toLocaleString()} edges`
    // A warm opening: centre on the graph's best-known entry point.
    if (!store.ego) await centre('CEAT')
  } catch (e) {
    store.status = `init failed: ${e}`
  }
}

export async function centre(name) {
  store.busy = true
  store.status = `Loading ${name}…`
  try {
    const ego = await api.graphEgo(name, store.asOf || null)
    store.ego = ego
    store.mode = 'ego'
    store.selected = {
      name: ego.name,
      entity_type: ego.entity_type,
      file_path: ego.file_path,
    }
    store.selectedEgo = ego
    store.status = `Ego — ${name} · ${ego.edges.length} relationships${
      ego.truncated ? ' (truncated)' : ''
    }${ego.as_of ? ` · as of ${ego.as_of}` : ''}`
    void loadMetrics(name)
    void loadHyperedges(name)
    return ego
  } catch (e) {
    store.status = `Error: ${e}`
    return null
  } finally {
    store.busy = false
  }
}

export async function loadCloud(force = false) {
  if (store.cloud && store.cloudAsOf === store.asOf && !force) {
    store.mode = 'cloud'
    store.status = `Cloud — ${store.cloud.total_nodes.toLocaleString()} entities · ${store.cloud.total_edges.toLocaleString()} edges`
    return
  }
  store.busy = true
  store.status = 'Loading full graph…'
  try {
    store.cloud = await api.graphCloud(null, store.asOf || null)
    store.cloudAsOf = store.asOf
    store.mode = 'cloud'
    store.status = `Cloud — ${store.cloud.total_nodes.toLocaleString()} entities · ${store.cloud.total_edges.toLocaleString()} edges${store.cloud.as_of ? ` · as of ${store.cloud.as_of}` : ''}`
  } catch (e) {
    store.status = `Error: ${e}`
  } finally {
    store.busy = false
  }
}

export function setMode(mode) {
  if (mode === 'cloud') loadCloud()
  else if (store.ego) {
    store.mode = 'ego'
    store.status = `Ego — ${store.ego.name} · ${store.ego.edges.length} relationships`
  }
}

/** Select a node → detail panel (always fetches its incident bundle). */
export async function select(name, entityType = null, filePath = null) {
  const seq = ++store.selectedSeq
  store.selected = { name, entity_type: entityType, file_path: filePath }
  store.selectedEgo = null
  try {
    const ego = await api.graphEgo(name, store.asOf || null)
    if (store.selectedSeq !== seq) return
    store.selectedEgo = ego
    store.selected = {
      name: ego.name,
      entity_type: ego.entity_type,
      file_path: ego.file_path ?? filePath,
    }
    void loadMetrics(ego.name)
    void loadHyperedges(ego.name)
  } catch (e) {
    if (store.selectedSeq === seq) store.status = `Error: ${e}`
  }
}

export function toggleEdgeType(edgeType) {
  if (store.hiddenEdgeTypes[edgeType]) delete store.hiddenEdgeTypes[edgeType]
  else store.hiddenEdgeTypes[edgeType] = true
  store.hiddenRev++
}

export async function runSearch(q) {
  const query = (q ?? '').trim()
  if (!query) {
    store.results = []
    store.searchInfo = null
    return
  }
  store.searching = true
  store.searchQ = query
  try {
    const res = store.hybrid
      ? await api.searchHybrid(query)
      : await api.searchNotes(query)
    store.results = res.results
    store.searchInfo = { total: res.total, query: res.query }
    store.status = `${store.hybrid ? 'Hybrid' : 'Search'} — “${res.query}” · ${res.total} notes`
  } catch (e) {
    store.results = []
    store.searchInfo = null
    store.status = `Search error: ${e}`
  } finally {
    store.searching = false
  }
}

export async function openNote(path) {
  try {
    store.note = await api.readNote(path)
    store.status = `Note — ${store.note.title}`
    void loadSimilar(path)
  } catch (e) {
    store.status = `Note error: ${e}`
  }
}

/** Similar notes for the open note (stored-vector cosine, no query model). */
export async function loadSimilar(path) {
  if (!path) {
    store.similar = []
    store.similarFor = null
    return
  }
  try {
    store.similar = await api.similarNotes(path, 5)
    store.similarFor = path
  } catch {
    store.similar = []
    store.similarFor = null
  }
}

export async function openEntityNote(name) {
  try {
    const note = await api.entityNote(name)
    if (note) {
      store.note = note
      store.status = `Note — ${note.title}`
    } else {
      store.status = `No dedicated note for ${name}`
    }
  } catch (e) {
    store.status = `Note error: ${e}`
  }
}

export function closeNote() {
  store.note = null
}

/** Chronoscope: set the temporal filter and re-invoke the current view
 * with the same args + as_of. centre/loadCloud swallow core errors into
 * status, so success is detected from results: a bad shape leaves the
 * previous asOf in place (status already holds the core's message). */
export async function setAsOf(v) {
  const next = (v ?? '').trim()
  if (next === store.asOf) return
  const prev = store.asOf
  store.asOf = next
  let ok = true
  if (store.mode === 'cloud') {
    await loadCloud(true)
    ok = store.cloudAsOf === next
  } else if (store.ego) {
    ok = (await centre(store.ego.name)) !== null
  }
  if (!ok) store.asOf = prev
}

/** Metrics bundle for the current selection (fire-and-forget). */
export async function loadMetrics(name) {
  if (!name) {
    store.metrics = null
    store.metricsFor = null
    return
  }
  try {
    store.metrics = await api.entityMetrics(name)
    store.metricsFor = name
  } catch (e) {
    store.metrics = null
    store.metricsFor = null
    store.status = `Metrics error: ${e}`
  }
}

/** Cloud rank tint: full-column scalar values, log-scaled at paint time. */
export async function setTint(metric) {
  if (!metric || metric === 'off') {
    store.tintMetric = 'off'
    store.tintValues = {}
    store.tintMax = 0
    store.tintRev++
    return
  }
  try {
    const rows = await api.metricValues(metric)
    const values = {}
    let max = 0
    for (const r of rows) {
      values[r.entity] = r.value
      if (r.value > max) max = r.value
    }
    store.tintMetric = metric
    store.tintValues = values
    store.tintMax = max
    store.tintRev++
    store.status = `Tint — ${metric} · ${rows.length.toLocaleString()} ranked`
  } catch (e) {
    store.status = `Tint error: ${e}`
  }
}

/** Hypergraph lane: incident hyperedges for the current selection. */
export async function loadHyperedges(name) {
  if (!name) {
    store.hyperedges = []
    store.hyperFor = null
    store.hyperFocus = null
    return
  }
  try {
    store.hyperedges = await api.entityHyperedges(name, store.asOf || null)
    store.hyperFor = name
    if (!store.hyperedges.some((h) => h.id === store.hyperFocus)) {
      store.hyperFocus = null
    }
  } catch (e) {
    store.hyperedges = []
    store.hyperFor = null
    store.hyperFocus = null
    store.status = `Hyperedge error: ${e}`
  }
}

export function toggleHyper(id) {
  store.hyperFocus = store.hyperFocus === id ? null : id
  store.hyperRev++
}

/** Docs browser: flat doc/ listing, optional substring filter. */
export async function loadDocs(filter = '') {
  store.docsLoading = true
  store.docsFilter = filter ?? ''
  try {
    const f = (filter ?? '').trim()
    store.docs = await api.browseDocs(f || null)
  } catch (e) {
    store.docs = []
    store.status = `Docs error: ${e}`
  } finally {
    store.docsLoading = false
  }
}
