<template>
  <div ref="wrap" class="absolute inset-0 overflow-hidden select-none">
    <canvas
      ref="canvas"
      class="block h-full w-full"
      :style="{ cursor: cursor }"
      @mousemove="onMove"
      @mouseleave="onLeave"
      @mousedown="onDown"
      @mouseup="onUp"
      @dblclick="onDbl"
      @wheel.prevent="onWheel"
      @contextmenu.prevent
    />

    <!-- node tooltip -->
    <div
      v-if="hover"
      class="pointer-events-none absolute z-20 min-w-40 max-w-72 rounded-lg border border-edge bg-panel/95 px-3 py-2 shadow-xl backdrop-blur"
      :style="{ left: hover.x + 'px', top: hover.y + 'px' }"
    >
      <div class="text-[10px] font-semibold uppercase tracking-wider" :style="{ color: nodeColor(hover.node.entity_type) }">
        {{ hover.node.entity_type }}
      </div>
      <div class="mt-0.5 text-sm font-semibold text-slate-100">
        {{ hover.node.label }}
      </div>
      <div v-if="hover.node.deg" class="mt-0.5 font-mono text-[10px] text-slate-400">
        degree {{ hover.node.deg }} · {{ hover.node.entity_type === 'company' ? 'double-click to centre' : '' }}
      </div>
    </div>

    <!-- zoom / layout controls -->
    <div
      class="absolute bottom-3 left-3 z-10 flex items-center gap-1 rounded-lg border border-edge bg-panel/90 px-1.5 py-1 backdrop-blur"
    >
      <button class="ctl" title="Zoom out" @click="zoomBy(1 / 1.4)">−</button>
      <span class="w-12 text-center font-mono text-[10px] text-slate-400">
        {{ Math.round(transform.k * 100) }}%
      </span>
      <button class="ctl" title="Zoom in" @click="zoomBy(1.4)">+</button>
      <div class="mx-1 h-4 w-px bg-edge"></div>
      <button class="ctl" title="Fit to view" @click="fitView">⤢</button>
      <button class="ctl" title="Re-run layout" @click="reheat">⟳</button>
    </div>

    <!-- empty state -->
    <div
      v-if="placeholder"
      class="pointer-events-none absolute inset-0 grid place-items-center px-8 text-center"
    >
      <div>
        <div class="mb-2 text-4xl opacity-30">◎</div>
        <p class="text-sm text-slate-500">{{ placeholder }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import * as d3 from 'd3'
import { store, select, centre } from '../lib/store'

// ---------------------------------------------------------------- refs ----- //

const wrap = ref(null)
const canvas = ref(null)
let ctx = null
let W = 0
let H = 0
let dpr = 1

let sim = null
let nodes = []
let links = []
let linkGroups = new Map() // edge colour → links[] (batched stroke)
let degree = new Map()
let labelIds = new Set()

const transform = ref({ k: 1, x: 0, y: 0 })
const hover = ref(null)

let drag = null // { node, sx, sy, moved }
let pan = null // { sx, sy, ox, oy }
let drawQueued = false
let ro = null

const TAU = Math.PI * 2

const NODE_COLORS = {
  company: '#38bdf8',
  sector: '#f472b6',
  super_sector: '#c084fc',
  sub_sector: '#818cf8',
  theme: '#34d399',
  index: '#fbbf24',
  institution: '#fb923c',
  country: '#a3e635',
  person: '#e879f9',
  edition: '#94a3b8',
  unknown: '#64748b',
}

function nodeColor(t) {
  return NODE_COLORS[t] || NODE_COLORS.unknown
}

// Rank tint (S3): log-scaled sequential sky over the active metric column.
// Heavy-tailed (pagerank spans orders of magnitude) — linear would paint
// everything but the top hub the same near-zero shade.
function tintFor(id) {
  if (store.tintMetric === 'off' || store.tintMax <= 0) return null
  const v = store.tintValues[id]
  if (v == null || !(v > 0)) return null
  const t = Math.log1p(v) / Math.log1p(store.tintMax)
  return `hsl(199 89% ${30 + 38 * Math.min(1, Math.max(0, t))}%)`
}

const edgeColorCache = new Map()
function edgeColor(t) {
  let c = edgeColorCache.get(t)
  if (!c) {
    let h = 0
    for (let i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) | 0
    c = `hsl(${(h % 360 + 360) % 360} 72% 62%)`
    edgeColorCache.set(t, c)
  }
  return c
}

// ---------------------------------------------------------------- computed - //

const focalName = computed(() =>
  store.mode === 'ego' && store.ego ? store.ego.name : null,
)
const selectedName = computed(() => store.selected?.name ?? null)

const placeholder = computed(() => {
  if (store.mode === 'ego' && !store.ego)
    return 'Search for an entity or pick a sector to centre the graph'
  if (store.mode === 'cloud' && !store.cloud) return 'Loading full graph…'
  return null
})

const cursor = computed(() => {
  if (drag) return 'grabbing'
  return hover.value ? 'pointer' : 'default'
})

// ---------------------------------------------------------------- build ---- //

function radiusOf(n, isEgo) {
  if (n.focal) return 20
  const t = n.entity_type
  let base = t === 'sector' || t === 'super_sector' ? 13 : t === 'theme' ? 12 : 8
  if (!isEgo) base += Math.min(9, Math.sqrt(n.deg || 0) * 1.3)
  return base
}

function rebuild() {
  const isEgo = store.mode === 'ego'
  const prev = new Map()
  for (const n of nodes) prev.set(n.id, { x: n.x, y: n.y })

  let rawNodes = []
  let rawLinks = []

  if (isEgo && store.ego) {
    rawNodes = store.ego.nodes.map((n) => ({
      id: n.name,
      label: n.name,
      entity_type: n.entity_type,
      focal: n.focal,
      has_note: !!n.file_path,
    }))
    rawLinks = store.ego.edges.map((e) => ({
      source: e.source,
      target: e.target,
      edge_type: e.edge_type,
    }))
  } else if (!isEgo && store.cloud) {
    const hidden = store.hiddenEdgeTypes
    const visible = store.cloud.edges.filter((e) => !hidden[e.edge_type])
    degree = new Map()
    for (const e of visible) {
      degree.set(e.source, (degree.get(e.source) || 0) + 1)
      degree.set(e.target, (degree.get(e.target) || 0) + 1)
    }
    rawLinks = visible.map((e) => ({
      source: e.source,
      target: e.target,
      edge_type: e.edge_type,
    }))
    rawNodes = store.cloud.nodes.map((n) => ({
      id: n.id,
      label: n.label,
      entity_type: n.entity_type,
      deg: degree.get(n.id) || 0,
    }))
    // Label only the hubs (top 140 by degree) until the user zooms in.
    labelIds = new Set(
      [...degree.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 140)
        .map(([id]) => id),
    )
  } else {
    nodes = []
    links = []
    linkGroups = new Map()
    scheduleDraw()
    return
  }

  // Ego: local degree from the incident edge list.
  if (isEgo) {
    degree = new Map()
    for (const l of rawLinks) {
      degree.set(l.source, (degree.get(l.source) || 0) + 1)
      degree.set(l.target, (degree.get(l.target) || 0) + 1)
    }
    for (const n of rawNodes) n.deg = degree.get(n.id) || 0
    labelIds = new Set(rawNodes.map((n) => n.id))
  }

  const present = new Set(rawNodes.map((n) => n.id))
  rawLinks = rawLinks.filter(
    (l) => present.has(l.source) && present.has(l.target),
  )

  const cx = W / 2 || 700
  const cy = H / 2 || 450
  const spread = Math.min(W || 800, H || 600) * 0.4
  nodes = rawNodes.map((n) => {
    n.r = radiusOf(n, isEgo)
    const p = prev.get(n.id)
    n.x = p ? p.x : cx + (Math.random() - 0.5) * spread
    n.y = p ? p.y : cy + (Math.random() - 0.5) * spread
    return n
  })
  links = rawLinks

  linkGroups = new Map()
  for (const l of links) {
    const c = edgeColor(l.edge_type)
    let arr = linkGroups.get(c)
    if (!arr) {
      arr = []
      linkGroups.set(c, arr)
    }
    arr.push(l)
  }

  startSim(isEgo)
}

function startSim(isEgo) {
  if (sim) sim.stop()
  sim = d3
    .forceSimulation(nodes)
    .force(
      'link',
      d3
        .forceLink(links)
        .id((d) => d.id)
        .distance(isEgo ? 125 : 42)
        .strength(isEgo ? 0.7 : 0.05),
    )
    .force(
      'charge',
      d3
        .forceManyBody()
        .strength(isEgo ? -430 : -24)
        .distanceMax(isEgo ? 900 : 460),
    )
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force(
      'collide',
      d3.forceCollide((d) => d.r + (isEgo ? 16 : 2.5)).iterations(isEgo ? 2 : 1),
    )
    .alpha(1)
    .alphaDecay(isEgo ? 0.035 : 0.02)
    .velocityDecay(0.42)
    .on('tick', scheduleDraw)
    .on('end', scheduleDraw)
  scheduleDraw()
}

// ---------------------------------------------------------------- draw ----- //

function scheduleDraw() {
  if (drawQueued) return
  drawQueued = true
  requestAnimationFrame(() => {
    drawQueued = false
    draw()
  })
}

function draw() {
  if (!ctx || !W) return
  const { k, x, y } = transform.value
  const isEgo = store.mode === 'ego'
  const sel = selectedName.value
  const hov = hover.value?.node ?? null
  const focus = hov || (sel ? nodes.find((n) => n.id === sel) : null)

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, W, H)
  ctx.translate(x, y)
  ctx.scale(k, k)

  // --- edges, batched per colour ---------------------------------------- //
  ctx.lineCap = 'round'
  ctx.lineWidth = (isEgo ? 1.5 : 0.65) / k
  ctx.globalAlpha = isEgo ? 0.55 : 0.32
  for (const [color, arr] of linkGroups) {
    ctx.strokeStyle = color
    ctx.beginPath()
    for (const l of arr) {
      const s = l.source
      const t = l.target
      if (typeof s === 'object' && typeof t === 'object') {
        ctx.moveTo(s.x, s.y)
        ctx.lineTo(t.x, t.y)
      }
    }
    ctx.stroke()
  }
  ctx.globalAlpha = 1

  // --- focus edges (hover / selection) ---------------------------------- //
  if (focus) {
    ctx.strokeStyle = 'rgba(226,232,240,0.85)'
    ctx.lineWidth = 2 / k
    ctx.beginPath()
    for (const l of links) {
      const s = l.source
      const t = l.target
      if (typeof s !== 'object') continue
      if (s === focus || t === focus) {
        ctx.moveTo(s.x, s.y)
        ctx.lineTo(t.x, t.y)
      }
    }
    ctx.stroke()
  }

  // --- nodes -------------------------------------------------------------- //
  // Hyperedge focus halo (S5): members of the focused hyperedge get a
  // ring in the hyperedge's colour (one colour per edge_type:label).
  const focusEdge =
    store.hyperFocus != null
      ? store.hyperedges.find((h) => h.id === store.hyperFocus)
      : null
  const hyperMembers = focusEdge
    ? new Set(focusEdge.members.map((m) => m.name))
    : null
  const hyperColor = focusEdge
    ? edgeColor(focusEdge.edge_type + ':' + focusEdge.label)
    : null
  const screenR = (n) => n.r * k
  for (const n of nodes) {
    ctx.beginPath()
    ctx.arc(n.x, n.y, n.r, 0, TAU)
    ctx.fillStyle = tintFor(n.id) || nodeColor(n.entity_type)
    ctx.globalAlpha = isEgo || screenR(n) > 3 ? 1 : 0.55
    ctx.fill()
    if (screenR(n) > 5) {
      ctx.lineWidth = 1.4 / k
      ctx.strokeStyle = 'rgba(11,18,32,0.85)'
      ctx.stroke()
    }
    ctx.globalAlpha = 1
    if (n.id === sel || (focalName.value && n.id === focalName.value)) {
      ctx.beginPath()
      ctx.arc(n.x, n.y, n.r + 5 / k, 0, TAU)
      ctx.lineWidth = 2 / k
      ctx.strokeStyle = n.id === focalName.value ? '#f8fafc' : '#fbbf24'
      ctx.stroke()
    }
    if (hyperMembers && hyperMembers.has(n.id)) {
      ctx.beginPath()
      ctx.arc(n.x, n.y, n.r + 9 / k, 0, TAU)
      ctx.lineWidth = 2.4 / k
      ctx.strokeStyle = hyperColor
      ctx.stroke()
    }
  }

  // --- labels -------------------------------------------------------------- //
  const showAll = isEgo || k > 1.5
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  for (const n of nodes) {
    const isFocus = hov === n || n.id === sel || n.id === focalName.value
    if (!isFocus && !showAll && !(k > 0.9 && labelIds.has(n.id))) continue
    const fontPx = (isFocus ? 13 : 11.5) / k
    ctx.font = `${isFocus ? 600 : 500} ${fontPx}px ui-sans-serif, system-ui, sans-serif`
    const label = n.label
    const tw = ctx.measureText(label).width
    const pad = 3 / k
    ctx.fillStyle = 'rgba(11,18,32,0.78)'
    ctx.fillRect(n.x - tw / 2 - pad, n.y + n.r + 3 / k, tw + pad * 2, fontPx + 4 / k)
    ctx.fillStyle = isFocus ? '#f8fafc' : '#94a3b8'
    ctx.fillText(label, n.x, n.y + n.r + 4 / k)
  }
}

// ---------------------------------------------------------------- input ---- //

function nodeAt(px, py) {
  const { k, x, y } = transform.value
  const sx = (px - x) / k
  const sy = (py - y) / k
  for (let i = nodes.length - 1; i >= 0; i--) {
    const n = nodes[i]
    const dx = n.x - sx
    const dy = n.y - sy
    const r = n.r + 4 / transform.value.k + 2
    if (dx * dx + dy * dy <= r * r) return n
  }
  return null
}

function onMove(e) {
  const px = e.offsetX
  const py = e.offsetY
  const { k, x, y } = transform.value

  if (drag) {
    if (Math.abs(px - drag.sx) + Math.abs(py - drag.sy) > 4) drag.moved = true
    drag.node.fx = (px - x) / k
    drag.node.fy = (py - y) / k
    scheduleDraw()
    return
  }
  if (pan) {
    transform.value = {
      k: transform.value.k,
      x: pan.ox + (px - pan.sx),
      y: pan.oy + (py - pan.sy),
    }
    hover.value = null
    scheduleDraw()
    return
  }

  const n = nodeAt(px, py)
  if (n) {
    const prev = hover.value
    if (!prev || prev.node !== n || prev.x !== px + 14) {
      hover.value = { node: n, x: px + 16, y: py + 12 }
      if (!prev || prev.node !== n) scheduleDraw()
    }
  } else if (hover.value) {
    hover.value = null
    scheduleDraw()
  }
}

function onLeave() {
  if (hover.value) {
    hover.value = null
    scheduleDraw()
  }
}

function onDown(e) {
  const n = nodeAt(e.offsetX, e.offsetY)
  if (n) {
    const { k, x, y } = transform.value
    drag = { node: n, sx: e.offsetX, sy: e.offsetY, moved: false }
    n.fx = (e.offsetX - x) / k
    n.fy = (e.offsetY - y) / k
    sim?.alphaTarget(0.18).restart()
  } else {
    pan = {
      sx: e.offsetX,
      sy: e.offsetY,
      ox: transform.value.x,
      oy: transform.value.y,
    }
  }
}

function onUp(e) {
  if (drag) {
    const n = drag.node
    const wasClick = !drag.moved
    n.fx = null
    n.fy = null
    sim?.alphaTarget(0)
    drag = null
    if (wasClick) void select(n.id, n.entity_type, n.has_note ? n.id : null)
  }
  pan = null
  scheduleDraw()
}

function onDbl(e) {
  const n = nodeAt(e.offsetX, e.offsetY)
  if (n) void centre(n.id)
}

function onWheel(e) {
  const { k, x, y } = transform.value
  const px = e.offsetX
  const py = e.offsetY
  const factor = Math.exp(-e.deltaY * 0.0016)
  const nk = Math.min(5, Math.max(0.08, k * factor))
  const sx = (px - x) / k
  const sy = (py - y) / k
  transform.value = { k: nk, x: px - sx * nk, y: py - sy * nk }
  scheduleDraw()
}

function zoomBy(factor) {
  const { k, x, y } = transform.value
  const nk = Math.min(5, Math.max(0.08, k * factor))
  const cx = W / 2
  const cy = H / 2
  const sx = (cx - x) / k
  const sy = (cy - y) / k
  transform.value = { k: nk, x: cx - sx * nk, y: cy - sy * nk }
  scheduleDraw()
}

function fitView() {
  if (!nodes.length) return
  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity
  for (const n of nodes) {
    if (n.x < x0) x0 = n.x
    if (n.y < y0) y0 = n.y
    if (n.x > x1) x1 = n.x
    if (n.y > y1) y1 = n.y
  }
  const pad = 60
  const k = Math.min(
    3,
    Math.max(0.08, Math.min((W - pad * 2) / (x1 - x0 || 1), (H - pad * 2) / (y1 - y0 || 1))),
  )
  transform.value = {
    k,
    x: W / 2 - ((x0 + x1) / 2) * k,
    y: H / 2 - ((y0 + y1) / 2) * k,
  }
  scheduleDraw()
}

function reheat() {
  sim?.alpha(0.9).restart()
}

// ---------------------------------------------------------------- lifecycle //

function resize() {
  if (!wrap.value || !canvas.value) return
  const rect = wrap.value.getBoundingClientRect()
  W = Math.max(1, rect.width)
  H = Math.max(1, rect.height)
  dpr = window.devicePixelRatio || 1
  canvas.value.width = Math.round(W * dpr)
  canvas.value.height = Math.round(H * dpr)
  if (sim) sim.force('center', d3.forceCenter(W / 2, H / 2))
  scheduleDraw()
}

watch(
  () => [store.mode, store.ego, store.cloud, store.hiddenRev],
  () => rebuild(),
)
watch(
  () => store.tintRev,
  () => scheduleDraw(),
)
watch(
  () => store.hyperRev,
  () => scheduleDraw(),
)
watch(
  () => [store.selected?.name, store.ego?.name],
  () => scheduleDraw(),
)

onMounted(() => {
  ctx = canvas.value.getContext('2d')
  resize()
  ro = new ResizeObserver(resize)
  ro.observe(wrap.value)
  rebuild()
})

onBeforeUnmount(() => {
  sim?.stop()
  ro?.disconnect()
})
</script>

<style scoped>
.ctl {
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 14px;
  color: #cbd5e1;
  background: transparent;
  border: none;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}
.ctl:hover {
  background: rgba(51, 65, 85, 0.65);
  color: #fff;
}
</style>
