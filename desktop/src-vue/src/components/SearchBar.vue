<template>
  <div class="relative z-30 shrink-0 border-b border-edge">
    <!-- input -->
    <div class="flex items-center gap-2 px-3 py-2.5">
      <span class="text-slate-500">⌕</span>
      <input
        ref="input"
        v-model="q"
        class="w-full bg-transparent text-sm text-slate-100 placeholder-slate-500 outline-none"
        placeholder="Search notes or entities…"
        @focus="focused = true"
        @blur="onBlur"
        @input="onInput"
        @keydown.down.prevent="move(1)"
        @keydown.up.prevent="move(-1)"
        @keydown.enter.prevent="enter"
        @keydown.esc="focused = false"
      />
      <span
        v-if="store.searching"
        class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-sky-400 border-t-transparent"
      />
      <kbd v-else class="rounded border border-edge px-1 font-mono text-[10px] text-slate-500">/</kbd>
    </div>

    <!-- entity suggestions -->
    <div
      v-if="showSuggest"
      class="absolute left-2 right-2 top-full max-h-72 overflow-y-auto rounded-lg border border-edge bg-panel2 shadow-2xl"
    >
      <button
        v-for="(h, i) in suggestions"
        :key="h.name"
        class="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm transition"
        :class="i === active ? 'bg-slate-700/60' : 'hover:bg-slate-800/60'"
        @mousedown.prevent="pick(h)"
      >
        <span
          class="h-2 w-2 shrink-0 rounded-full"
          :style="{ background: nodeColor(h.entity_type) }"
        />
        <span class="truncate text-slate-200">{{ h.name }}</span>
        <span class="ml-auto shrink-0 font-mono text-[10px] text-slate-500">
          {{ h.entity_type }}
        </span>
      </button>
      <div v-if="!suggestions.length" class="px-3 py-2 text-xs text-slate-500">
        no entities match
      </div>
    </div>
  </div>

  <!-- search results -->
  <div
    v-if="store.searchInfo"
    class="min-h-0 flex-1 overflow-y-auto border-b border-edge"
  >
    <div class="sticky top-0 flex items-center justify-between border-b border-edge bg-panel px-3 py-2">
      <span class="font-mono text-[10px] uppercase tracking-wider text-slate-500">
        “{{ store.searchInfo.query }}” · {{ store.searchInfo.total }} notes
      </span>
      <div class="flex items-center gap-1.5">
        <button
          class="font-mono text-[10px] uppercase tracking-wider"
          :class="store.hybrid ? 'text-sky-400' : 'text-slate-600 hover:text-slate-400'"
          title="Fuse BM25 ranks with vector-cosine ranks (RRF K=60)"
          @click="toggleHybrid"
        >
          {{ store.hybrid ? 'hybrid' : 'bm25' }}
        </button>
        <button class="text-slate-500 hover:text-slate-300" @click="clear">✕</button>
      </div>
    </div>
    <button
      v-for="hit in store.results"
      :key="hit.file_path + (hit.section_title || '')"
      class="block w-full border-b border-edge/60 px-3 py-2.5 text-left transition hover:bg-slate-800/50"
      @click="openNote(hit.file_path)"
    >
      <div class="flex items-center gap-2">
        <span class="badge" :style="{ color: docColor(hit.doc_type) }">
          {{ hit.doc_type }}
        </span>
        <span class="truncate text-[13px] font-semibold text-slate-200">
          {{ hit.title }}
        </span>
      </div>
      <div
        class="snippet mt-1 line-clamp-3 text-xs leading-relaxed text-slate-400"
        v-html="snippetHtml(hit.snippet)"
      />
      <div class="mt-1 truncate font-mono text-[10px] text-slate-600">
        {{ hit.file_path }}
      </div>
    </button>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { store, runSearch, openNote, centre, closeNote } from '../lib/store'
import { suggest as apiSuggest } from '../lib/api'
import { nodeColor } from '../lib/colors'

const q = ref('')
const focused = ref(false)
const suggestions = ref([])
const active = ref(-1)
const input = ref(null)
let debounce = null

const showSuggest = computed(
  () => focused.value && q.value.trim().length > 0,
)

const DOC_COLORS = {
  company: '#38bdf8',
  sector: '#f472b6',
  super_sector: '#c084fc',
  chatter: '#fbbf24',
  points_and_figures: '#fb923c',
  plotlines: '#34d399',
  misc: '#94a3b8',
}
function docColor(t) {
  return DOC_COLORS[t] || '#94a3b8'
}

function snippetHtml(s) {
  // FTS snippet ships `<mark>` only — strip anything else defensively.
  return (s || '').replace(/<(?!\/?mark>)[^>]*>/g, '')
}

function onInput() {
  active.value = -1
  clearTimeout(debounce)
  const v = q.value.trim()
  if (!v) {
    suggestions.value = []
    return
  }
  debounce = setTimeout(async () => {
    try {
      const hits = await apiSuggest(v, 10)
      if (q.value.trim() === v) suggestions.value = hits
    } catch {
      suggestions.value = []
    }
  }, 160)
}

function move(d) {
  if (!suggestions.value.length) return
  active.value =
    (active.value + d + suggestions.value.length) % suggestions.value.length
}

function enter() {
  if (active.value >= 0 && suggestions.value[active.value]) {
    pick(suggestions.value[active.value])
    return
  }
  focused.value = false
  void runSearch(q.value)
}

function pick(h) {
  focused.value = false
  suggestions.value = []
  q.value = h.name
  void centre(h.name)
}

function onBlur() {
  // let mousedown on a suggestion land before closing
  setTimeout(() => {
    focused.value = false
  }, 120)
}

function clear() {
  store.results = []
  store.searchInfo = null
}

function toggleHybrid() {
  store.hybrid = !store.hybrid
  if (store.searchQ) void runSearch(store.searchQ)
}

function onGlobalKey(e) {
  if (e.key === '/' && document.activeElement !== input.value) {
    const tag = document.activeElement?.tagName
    if (tag !== 'INPUT' && tag !== 'TEXTAREA') {
      e.preventDefault()
      input.value?.focus()
    }
  }
}

onMounted(() => window.addEventListener('keydown', onGlobalKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onGlobalKey))
</script>

<style scoped>
.badge {
  font-family: ui-monospace, monospace;
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  border: 1px solid currentColor;
  border-radius: 4px;
  padding: 0 4px;
  opacity: 0.85;
  flex-shrink: 0;
}
</style>
