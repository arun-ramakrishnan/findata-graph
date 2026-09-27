<template>
  <!-- selected entity detail -->
  <section
    v-if="store.selected"
    class="shrink-0 border-b border-edge px-4 py-3"
  >
    <div class="flex items-start gap-2.5">
      <span
        class="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full"
        :style="{ background: nodeColor(store.selected.entity_type) }"
      />
      <div class="min-w-0 flex-1">
        <div class="font-mono text-[10px] uppercase tracking-wider text-slate-500">
          {{ store.selected.entity_type || 'unknown' }}
        </div>
        <h2 class="truncate text-base font-semibold text-slate-100">
          {{ store.selected.name }}
        </h2>
      </div>
    </div>

    <div class="mt-2.5 flex flex-wrap gap-1.5">
      <button class="btn-primary" @click="centre(store.selected.name)">
        Centre graph
      </button>
      <button
        v-if="store.selected.file_path"
        class="btn-secondary"
        @click="openEntityNote(store.selected.name)"
      >
        Open note →
      </button>
    </div>

    <!-- relationship counts -->
    <div v-if="relGroups.length" class="mt-3">
      <div class="mb-1.5 flex items-center justify-between">
        <span class="font-mono text-[10px] uppercase tracking-wider text-slate-500">
          relationships
        </span>
        <span class="font-mono text-[10px] text-slate-600">
          {{ store.selectedEgo.edges.length }}{{
            store.selectedEgo.truncated ? '+' : ''
          }}
        </span>
      </div>
      <div class="flex flex-wrap gap-1">
        <span
          v-for="g in relGroups"
          :key="g.type"
          class="rel-chip"
          :title="`${g.type} — ${g.count}`"
        >
          <span class="dot" :style="{ background: edgeColor(g.type) }" />
          {{ g.type }}
          <span class="count">{{ g.count }}</span>
        </span>
      </div>
    </div>
    <div
      v-else-if="store.selectedEgo === null"
      class="mt-3 font-mono text-[11px] text-slate-500"
    >
      loading…
    </div>
    <div v-else class="mt-3 font-mono text-[11px] text-slate-500">
      no recorded relationships
    </div>

    <!-- hypergraph lane (S5): click a chip to halo its members -->
    <div v-if="hyperedges.length" class="mt-3">
      <div class="mb-1.5 flex items-center justify-between">
        <span class="font-mono text-[10px] uppercase tracking-wider text-slate-500">
          hyperedges
        </span>
        <span class="font-mono text-[10px] text-slate-600">
          {{ hyperedges.length }}
        </span>
      </div>
      <div class="flex flex-wrap gap-1">
        <button
          v-for="h in hyperedges"
          :key="h.id"
          class="rel-chip"
          :class="{ on: store.hyperFocus === h.id }"
          :title="`${h.edge_type} · ${h.label} — ${h.members.length} members`"
          @click="toggleHyper(h.id)"
        >
          <span class="dot" :style="{ background: edgeColor(h.edge_type + ':' + h.label) }" />
          {{ h.label }}
          <span class="count">{{ h.members.length }}</span>
        </button>
      </div>
    </div>
  </section>

  <!-- note viewer -->
  <section class="flex min-h-0 flex-1 flex-col">
    <template v-if="store.note">
      <div class="flex shrink-0 items-center gap-2 border-b border-edge px-4 py-2.5">
        <span class="font-mono text-[10px] uppercase tracking-wider text-sky-400">
          note
        </span>
        <span class="min-w-0 flex-1 truncate text-[13px] font-semibold text-slate-200">
          {{ store.note.title }}
        </span>
        <button
          class="rounded px-1.5 text-slate-500 hover:bg-slate-700/60 hover:text-white"
          title="Close note"
          @click="closeNote()"
        >
          ✕
        </button>
      </div>
      <div class="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <div
          class="markdown"
          v-html="rendered"
          @click="onMarkdownClick"
        />
        <!-- similar notes (S7: stored-vector cosine, self excluded) -->
        <div v-if="store.similarFor === store.note.path && store.similar.length" class="mt-4 border-t border-edge pt-2.5">
          <div class="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500">
            similar notes
          </div>
          <button
            v-for="s in store.similar"
            :key="s.file_path"
            class="block w-full truncate px-1 py-1 text-left text-xs text-slate-400 transition hover:bg-slate-800/50 hover:text-slate-200"
            :title="`${s.file_path} — cosine ${s.score.toFixed(3)}`"
            @click="openNote(s.file_path)"
          >
            {{ s.title }}
            <span class="font-mono text-[10px] text-slate-600">{{ s.score.toFixed(2) }}</span>
          </button>
        </div>
        <div class="mt-4 border-t border-edge pt-2 font-mono text-[10px] text-slate-600">
          {{ store.note.path }}
        </div>
      </div>
    </template>

    <div
      v-else
      class="grid flex-1 place-items-center px-8 text-center"
    >
      <div>
        <div class="mb-2 text-3xl opacity-25">▤</div>
        <p class="text-xs leading-relaxed text-slate-500">
          Search a note, or click a node<br />to inspect it and open its file.
        </p>
        <p v-if="store.stats" class="mt-3 font-mono text-[10px] text-slate-600">
          {{ store.stats.entities.toLocaleString() }} entities ·
          {{ store.stats.edges.toLocaleString() }} edges
        </p>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import { openUrl } from '@tauri-apps/plugin-opener'
import { store, centre, openNote, openEntityNote, closeNote, toggleHyper } from '../lib/store'
import { nodeColor, edgeColor } from '../lib/colors'

const md = new MarkdownIt({ linkify: true, html: false, breaks: false })

// Vault notes carry `---` YAML frontmatter — render the body only.
function stripFrontmatter(text) {
  if (!text.startsWith('---')) return text
  const end = text.indexOf('\n---', 3)
  if (end === -1) return text
  const after = text.slice(end + 4)
  return after.startsWith('\n') ? after.slice(1) : after
}

const rendered = computed(() =>
  store.note ? md.render(stripFrontmatter(store.note.markdown)) : '',
)

const relGroups = computed(() => {
  const ego = store.selectedEgo
  if (!ego) return []
  const counts = new Map()
  for (const e of ego.edges) counts.set(e.edge_type, (counts.get(e.edge_type) || 0) + 1)
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12)
    .map(([type, count]) => ({ type, count }))
})

// Hyperedges incident to the selection (loaded alongside metrics).
const hyperedges = computed(() =>
  store.hyperFor === store.selected?.name ? store.hyperedges : [],
)

// Keep the webview inside the app: internal links jump to a note path,
// external links open in the system browser (S6 opener plugin). Under the
// smoke harness there is no Tauri runtime — openUrl rejects and the URL
// lands in the status bar instead, which the harness can assert.
async function openExternal(href) {
  try {
    await openUrl(href)
    store.status = `Opened: ${href}`
  } catch {
    store.status = `External link: ${href}`
  }
}

function onMarkdownClick(e) {
  const a = e.target.closest?.('a[href]')
  if (!a) return
  e.preventDefault()
  const href = a.getAttribute('href') || ''
  if (/^(https?:)?\/\//.test(href)) {
    void openExternal(href)
    return
  }
  const clean = href.replace(/^\/+/, '')
  if (clean.startsWith('findata/') || clean.startsWith('doc/')) {
    void openNote(clean)
  } else {
    store.status = `Link not resolvable in demo: ${href}`
  }
}
</script>

<style scoped>
.btn-primary {
  font-size: 12px;
  font-weight: 600;
  color: #0b1220;
  background: #38bdf8;
  border: none;
  border-radius: 6px;
  padding: 5px 10px;
  cursor: pointer;
}
.btn-primary:hover { background: #7dd3fc; }
.btn-secondary {
  font-size: 12px;
  font-weight: 600;
  color: #cbd5e1;
  background: #1a2438;
  border: 1px solid #26344d;
  border-radius: 6px;
  padding: 5px 10px;
  cursor: pointer;
}
.btn-secondary:hover { background: #24314a; color: #fff; }
.rel-chip {
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
}
.rel-chip .dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
}
.rel-chip .count {
  color: #64748b;
}
button.rel-chip { cursor: pointer; }
.rel-chip.on {
  border-color: #38bdf8;
  color: #e2e8f0;
}
</style>
