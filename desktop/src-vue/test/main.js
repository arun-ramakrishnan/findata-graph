// Browser smoke-test harness: stubs the Tauri IPC with fixtures generated
// from the live research.db, then mounts the real App. Lets headless Chrome
// verify rendering without the native shell.
//
//   make fixtures   (regenerate test/fixtures.js from research.db)
//   make smoke      (vite + chrome screenshot)

import { FIXTURES } from './fixtures.js'

const egoFor = (name, asOf = null) => ({
  name,
  entity_type: name === FIXTURES.ego.name ? FIXTURES.ego.entity_type : 'unknown',
  file_path: name === FIXTURES.ego.name ? FIXTURES.ego.file_path : null,
  edges: name === FIXTURES.ego.name ? FIXTURES.ego.edges : [],
  nodes:
    name === FIXTURES.ego.name
      ? FIXTURES.ego.nodes
      : [{ name, entity_type: 'unknown', file_path: null, focal: true }],
  truncated: false,
  as_of: asOf || null,
})

const handlers = {
  stats: () => FIXTURES.stats,
  sectors: () => FIXTURES.sectors,
  graph_ego: (args) => egoFor(args.name, args.asOf),
  graph_cloud: (args) => ({ ...FIXTURES.cloud, as_of: (args || {}).asOf || null }),
  search_notes: () => FIXTURES.search,
  suggest: (args) =>
    FIXTURES.suggest.filter((s) =>
      s.name.toLowerCase().includes((args.q || '').toLowerCase()),
    ),
  read_note: () => FIXTURES.note,
  entity_note: (args) => (args.name === FIXTURES.ego.name ? FIXTURES.note : null),
  entity_metrics: () => FIXTURES.metrics,
  metric_values: () => FIXTURES.metric_values,
  entity_hyperedges: () => FIXTURES.hyperedges,
  similar_notes: () => FIXTURES.similar,
  search_hybrid: () => FIXTURES.hybrid,
  browse_docs: (args) => {
    const f = ((args || {}).filter || '').trim().toLowerCase()
    if (!f) return FIXTURES.docs
    return FIXTURES.docs.filter((d) =>
      `${d.path} ${d.title}`.toLowerCase().includes(f),
    )
  },
}

window.__TAURI_INTERNALS__ = {
  invoke: (cmd, args) => {
    const h = handlers[cmd]
    if (!h) return Promise.reject(new Error(`no fixture for ${cmd}`))
    return Promise.resolve(h(args || {}))
  },
  transformCallback: (cb) => cb,
  unregisterCallback: () => {},
  convertFileSrc: (p) => p,
}

const { createApp } = await import('vue')
const { default: App } = await import('../src/App.vue')
await import('../src/style.css')
createApp(App).mount('#app')

// ---- scenario driver for headless screenshots -------------------------------
// ?scenario=search  → run the fixture search, show results
// ?scenario=note    → search + open the first note
// ?scenario=cloud   → switch to cloud mode
// ?scenario=docs    → load the docs browser listing
// ?scenario=metrics → load CEAT metrics + pagerank tint
// ?scenario=asof    → set as_of=2022 and re-invoke the ego view
// ?scenario=hyper   → load CEAT hyperedges + focus the first one
// ?scenario=opener   → open the note, click its external link (harness
//                      has no Tauri runtime, so the status-bar fallback fires)
// ?scenario=hybrid   → hybrid search + similar notes for the top hit
const scenario = new URLSearchParams(location.search).get('scenario')
if (scenario) {
  const st = await import('../src/lib/store.js')
  window.__st = st
  window.__ran = 'none'
  console.log('[driver] start', JSON.stringify({ search: location.search, scenario }))
  await new Promise((r) => setTimeout(r, 400))
  if (scenario === 'search' || scenario === 'note') {
    await st.runSearch('shrimp feed')
    window.__ran = 'search'
    console.log('[driver] search done', st.store.results.length)
  }
  if (scenario === 'note') {
    await st.openNote(st.store.results[0].file_path)
    window.__ran = 'note'
  }
  if (scenario === 'cloud') {
    await st.loadCloud()
    await st.select('CEAT')
    window.__ran = 'cloud'
  }
  if (scenario === 'docs') {
    await st.loadDocs()
    window.__ran = 'docs'
  }
  if (scenario === 'metrics') {
    await st.loadMetrics('CEAT')
    await st.setTint('pagerank')
    await st.loadCloud()
    window.__ran = 'metrics'
  }
  if (scenario === 'asof') {
    await st.setAsOf('2022')
    window.__ran = 'asof'
  }
  if (scenario === 'hyper') {
    await st.loadHyperedges('CEAT')
    if (st.store.hyperedges.length) st.toggleHyper(st.store.hyperedges[0].id)
    window.__ran = 'hyper'
  }
  if (scenario === 'opener') {
    await st.openNote(FIXTURES.note.path)
    await new Promise((r) => setTimeout(r, 300))
    const a = document.querySelector('.markdown a[href^="http"]')
    if (a instanceof HTMLElement) {
      a.click()
      await new Promise((r) => setTimeout(r, 300))
    }
    window.__ran = st.store.status.startsWith('External link:')
      || st.store.status.startsWith('Opened:')
      ? 'opener'
      : `unexpected:${st.store.status.slice(0, 40)}`
  }
  if (scenario === 'hybrid') {
    st.store.hybrid = true
    await st.runSearch('shrimp feed')
    if (st.store.results.length) await st.openNote(st.store.results[0].file_path)
    window.__ran = 'hybrid'
  }
  setTimeout(() => {
    const b = document.createElement('div')
    b.style.cssText =
      'position:fixed;bottom:40px;left:50%;transform:translateX(-50%);z-index:99;background:#f00;color:#fff;font:13px monospace;padding:3px 8px;border-radius:4px'
    b.textContent = `scen:${scenario} ran:${window.__ran} mode:${st.store.mode} results:${st.store.results.length} note:${st.store.note ? 1 : 0} cloud:${st.store.cloud ? 1 : 0} docs:${st.store.docs.length} metrics:${st.store.metrics ? 1 : 0} tint:${st.store.tintMetric} asof:${st.store.asOf || 'off'} hyper:${st.store.hyperedges.length} focus:${st.store.hyperFocus ?? 'off'} hybrid:${st.store.hybrid ? 1 : 0} similar:${st.store.similar.length}`
    document.body.appendChild(b)
  }, 1500)
}
