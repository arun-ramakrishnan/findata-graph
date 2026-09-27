// Shared palette — graph nodes, edges, legend chips.

export const NODE_COLORS = {
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

export function nodeColor(t) {
  return NODE_COLORS[t] || NODE_COLORS.unknown
}

const edgeColorCache = new Map()

export function edgeColor(t) {
  let c = edgeColorCache.get(t)
  if (!c) {
    let h = 0
    for (let i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) | 0
    c = `hsl(${(h % 360 + 360) % 360} 72% 62%)`
    edgeColorCache.set(t, c)
  }
  return c
}
