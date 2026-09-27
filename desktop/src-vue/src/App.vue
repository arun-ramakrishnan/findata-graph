<template>
  <div class="flex h-full flex-col bg-ink text-slate-200">
    <!-- ── header ─────────────────────────────────────────────────────── -->
    <header
      class="flex h-12 shrink-0 items-center gap-4 border-b border-edge bg-panel px-4"
    >
      <div class="flex items-center gap-2">
        <span class="text-lg leading-none text-sky-400">◎</span>
        <span class="text-sm font-bold tracking-tight text-slate-100">
          findata<span class="text-sky-400">-graph</span>
        </span>
        <span
          class="rounded border border-edge px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-widest text-slate-500"
        >
          desktop
        </span>
      </div>

      <!-- mode tabs -->
      <div class="flex rounded-lg border border-edge bg-ink p-0.5">
        <button
          class="tab"
          :class="{ 'tab-on': store.mode === 'ego' }"
          @click="setMode('ego')"
        >
          Ego
        </button>
        <button
          class="tab"
          :class="{ 'tab-on': store.mode === 'cloud' }"
          @click="setMode('cloud')"
        >
          Cloud
        </button>
      </div>

      <div class="flex-1"></div>

      <!-- chronoscope (S4): re-invokes the current view + as_of -->
      <div class="flex shrink-0 items-center gap-1.5">
        <input
          v-model="asOfQ"
          class="w-28 rounded-md border border-edge bg-ink px-2 py-1 font-mono text-[11px] text-slate-200 placeholder-slate-600 outline-none focus:border-sky-700"
          :class="{ 'border-amber-600/60': store.asOf }"
          placeholder="as of…"
          title="Temporal filter: YYYY, YYYY-MM, or YYYY-MM-DD (Enter to apply)"
          @keydown.enter="applyAsOf"
        />
        <button
          v-if="store.asOf"
          class="rounded px-1 font-mono text-[11px] text-amber-400/80 hover:text-amber-200"
          :title="`Clear as_of ${store.asOf}`"
          @click="clearAsOf"
        >
          ✕
        </button>
      </div>

      <div
        class="max-w-[46%] truncate font-mono text-[11px] text-slate-400"
        :title="store.status"
      >
        {{ store.status }}
      </div>
      <div
        v-if="store.busy"
        class="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-sky-400 border-t-transparent"
      />
    </header>

    <div class="flex min-h-0 flex-1">
      <!-- ── left sidebar ─────────────────────────────────────────────── -->
      <aside
        class="flex w-[310px] shrink-0 flex-col border-r border-edge bg-panel"
      >
        <SearchBar />

        <!-- relationship legend -->
        <section
          v-if="legend.length"
          class="shrink-0 border-b border-edge px-3 py-2.5"
        >
          <div class="mb-1.5 flex items-center justify-between">
            <span class="font-mono text-[10px] uppercase tracking-wider text-slate-500">
              {{ store.mode === 'cloud' ? 'edge filter' : 'relationships' }}
            </span>
            <span class="font-mono text-[10px] text-slate-600">
              {{ store.mode === 'cloud' ? 'click to hide' : 'current ego' }}
            </span>
          </div>
          <div v-if="store.mode === 'cloud'" class="mb-1.5 flex items-center gap-2">
            <span class="font-mono text-[10px] uppercase tracking-wider text-slate-500">
              rank tint
            </span>
            <select
              class="min-w-0 flex-1 rounded-md border border-edge bg-ink px-1.5 py-0.5 font-mono text-[10px] text-slate-300 outline-none focus:border-sky-700"
              :value="store.tintMetric"
              @change="onTint"
              title="Tint nodes by a scalar graph metric (log-scaled)"
            >
              <option value="off">off</option>
              <option v-for="t in TINTS" :key="t" :value="t">{{ t }}</option>
            </select>
          </div>
          <div class="flex flex-wrap gap-1">
            <button
              v-for="g in legend"
              :key="g.type"
              class="legend-chip"
              :class="{ off: store.hiddenEdgeTypes[g.type] }"
              :title="`${g.type} — ${g.count}`"
              @click="store.mode === 'cloud' && toggleEdgeType(g.type)"
            >
              <span class="dot" :style="{ background: edgeColor(g.type) }" />
              {{ g.type }}
              <span class="count">{{ g.count }}</span>
            </button>
          </div>
        </section>

        <!-- browse: sectors / themes -->
        <section class="flex min-h-0 flex-1 flex-col">
          <div
            class="shrink-0 px-3 pb-1.5 pt-2.5 font-mono text-[10px] uppercase tracking-wider text-slate-500"
          >
            browse — sectors & themes
          </div>
          <div class="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
            <button
              v-for="s in store.sectors"
              :key="s.name"
              class="browse-row"
              :class="{ on: store.selected?.name === s.name }"
              @click="centre(s.name)"
            >
              <span
                class="h-1.5 w-1.5 shrink-0 rounded-full"
                :style="{ background: nodeColor(s.entity_type) }"
              />
              <span class="truncate">{{ s.name }}</span>
              <span v-if="s.entity_type !== 'sector'" class="tag">
                {{ s.entity_type.replace('_', ' ') }}
              </span>
            </button>
          </div>
        </section>

        <!-- browse: docs (S2 — doc/ vault, not in the FTS index) -->
        <section class="flex min-h-0 shrink-0 flex-col border-t border-edge">
          <div
            class="shrink-0 px-3 pb-1 pt-2.5 font-mono text-[10px] uppercase tracking-wider text-slate-500"
          >
            browse — docs · {{ store.docs.length }}
          </div>
          <div class="shrink-0 px-3 pb-1.5">
            <input
              v-model="docsQ"
              class="w-full rounded-md border border-edge bg-ink px-2 py-1 text-xs text-slate-200 placeholder-slate-600 outline-none focus:border-sky-700"
              placeholder="Filter docs…"
              @input="onDocsInput"
            />
          </div>
          <div class="max-h-56 min-h-0 overflow-y-auto px-3 pb-3">
            <button
              v-for="d in store.docs"
              :key="d.path"
              class="browse-row"
              :title="d.path"
              @click="openNote(d.path)"
            >
              <span class="truncate">{{ d.title }}</span>
            </button>
            <div
              v-if="!store.docs.length && !store.docsLoading"
              class="px-2 py-1 text-xs text-slate-600"
            >
              no docs match
            </div>
          </div>
        </section>
      </aside>

      <!-- ── graph canvas ─────────────────────────────────────────────── -->
      <main class="relative min-w-0 flex-1">
        <GraphView />
      </main>

      <!-- ── right panel ──────────────────────────────────────────────── -->
      <aside
        class="flex w-[400px] shrink-0 flex-col border-l border-edge bg-panel"
      >
        <div class="flex shrink-0 items-center gap-1 border-b border-edge px-3 py-1.5">
          <button
            class="tab"
            :class="{ 'tab-on': railTab === 'note' }"
            @click="railTab = 'note'"
          >
            Note
          </button>
          <button
            class="tab"
            :class="{ 'tab-on': railTab === 'metrics' }"
            @click="railTab = 'metrics'"
          >
            Metrics<span v-if="store.metricsFor" class="tab-count">{{ store.metrics?.company.length ?? 0 }}</span>
          </button>
        </div>
        <NotePanel v-if="railTab === 'note'" />
        <MetricsPanel v-else />
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import GraphView from './components/GraphView.vue'
import SearchBar from './components/SearchBar.vue'
import NotePanel from './components/NotePanel.vue'
import MetricsPanel from './components/MetricsPanel.vue'
import { store, init, setMode, centre, toggleEdgeType, loadDocs, openNote, setTint, setAsOf } from './lib/store'
import { nodeColor, edgeColor } from './lib/colors'

// Chronoscope input (S4): free text, core validates the shape.
const asOfQ = ref('')
function applyAsOf() {
  void setAsOf(asOfQ.value)
}
function clearAsOf() {
  asOfQ.value = ''
  void setAsOf('')
}

// Right-rail tab + rank tint (S3).
const railTab = ref('note')
const TINTS = ['pagerank', 'betweenness_centrality', 'degree_centrality', 'eigenvector_centrality']
function onTint(e) {
  void setTint(e.target.value)
}

// Docs filter (debounced into the backend listing).
const docsQ = ref('')
let docsDebounce = null
function onDocsInput() {
  clearTimeout(docsDebounce)
  const v = docsQ.value
  docsDebounce = setTimeout(() => void loadDocs(v), 180)
}

// Legend: edge filters in cloud mode (global counts), ego rollup otherwise.
const legend = computed(() => {
  if (store.mode === 'cloud') {
    if (!store.stats) return []
    return store.stats.edge_types.slice(0, 14).map(([type, count]) => ({
      type,
      count,
    }))
  }
  const ego = store.ego
  if (!ego) return []
  const counts = new Map()
  for (const e of ego.edges) counts.set(e.edge_type, (counts.get(e.edge_type) || 0) + 1)
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 14)
    .map(([type, count]) => ({ type, count }))
})

onMounted(() => {
  void init()
  void loadDocs()
})
</script>

<style scoped>
.tab {
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
  background: transparent;
  border: none;
  border-radius: 6px;
  padding: 4px 14px;
  cursor: pointer;
  transition: all 0.12s;
}
.tab:hover { color: #cbd5e1; }
.tab-on {
  color: #0b1220;
  background: #38bdf8;
}
.tab-count {
  margin-left: 5px;
  font-family: ui-monospace, monospace;
  font-size: 9px;
  opacity: 0.7;
}
.legend-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: ui-monospace, monospace;
  font-size: 10px;
  color: #94a3b8;
  background: #131f38;
  border: 1px solid #1e293b;
  border-radius: 999px;
  padding: 2px 8px;
  cursor: default;
  transition: all 0.12s;
}
button.legend-chip { cursor: pointer; }
.legend-chip .dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
}
.legend-chip .count { color: #64748b; }
.legend-chip.off {
  opacity: 0.35;
  text-decoration: line-through;
}
.legend-chip:not(.off):hover { border-color: #33455f; }
.browse-row {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: #94a3b8;
  background: transparent;
  border: none;
  border-radius: 6px;
  padding: 4px 8px;
  cursor: pointer;
  text-align: left;
  transition: all 0.1s;
}
.browse-row:hover {
  background: #1a2438;
  color: #e2e8f0;
}
.browse-row.on {
  background: #1a2438;
  color: #f1f5f9;
}
.browse-row .tag {
  margin-left: auto;
  font-family: ui-monospace, monospace;
  font-size: 9px;
  color: #475569;
  text-transform: uppercase;
  flex-shrink: 0;
}
</style>
