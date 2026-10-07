<template>
  <section class="flex min-h-0 flex-1 flex-col">
    <div
      v-if="!store.selected"
      class="grid flex-1 place-items-center px-8 text-center"
    >
      <p class="text-xs leading-relaxed text-slate-500">
        Click a node to see<br />its metrics here.
      </p>
    </div>
    <div
      v-else-if="!store.metrics || store.metricsFor !== store.selected.name"
      class="grid flex-1 place-items-center px-8 text-center"
    >
      <p class="font-mono text-[11px] text-slate-500">loading…</p>
    </div>
    <div v-else class="min-h-0 flex-1 overflow-y-auto px-4 py-3">
      <!-- company metrics (latest first) -->
      <div
        v-if="store.metrics.company.length"
        class="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500"
      >
        company · {{ store.metrics.company.length }}
      </div>
      <div v-if="store.metrics.company.length" class="mb-4 space-y-1">
        <div
          v-for="(m, i) in store.metrics.company"
          :key="m.label + i"
          class="flex items-baseline gap-2 text-xs"
        >
          <span class="shrink-0 font-mono text-[10px] text-slate-500">{{ m.label || '—' }}</span>
          <span class="min-w-0 flex-1 truncate text-right font-semibold text-slate-200" :title="m.value_raw">
            {{ m.value_raw }}{{ m.unit ? ' ' + m.unit : '' }}
          </span>
          <span v-if="m.period" class="shrink-0 font-mono text-[10px] text-slate-600">
            {{ m.period }}
          </span>
        </div>
      </div>

      <!-- graph analytics -->
      <div
        v-if="scalars.length"
        class="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500"
      >
        graph rank
      </div>
      <div v-if="scalars.length" class="mb-4 space-y-1">
        <div
          v-for="s in scalars"
          :key="s.metric"
          class="flex items-baseline gap-2 text-xs"
        >
          <span class="shrink-0 font-mono text-[10px] text-slate-500">{{ s.metric }}</span>
          <span class="min-w-0 flex-1 truncate text-right font-mono text-[11px] text-sky-300">
            {{ s.display }}
          </span>
        </div>
      </div>

      <!-- community / component labels -->
      <div
        v-if="labels.length"
        class="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500"
      >
        groups
      </div>
      <div v-if="labels.length" class="flex flex-wrap gap-1">
        <span
          v-for="l in labels"
          :key="l.metric"
          class="rel-chip"
          :title="l.metric"
        >
          {{ l.metric.replace(/_community|_component/g, '') }} · {{ l.display }}
        </span>
      </div>

      <div
        v-if="!store.metrics.company.length && !scalars.length && !labels.length"
        class="font-mono text-[11px] text-slate-500"
      >
        no recorded metrics
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import * as v from 'valibot'
import { AnalyticsValueSchema } from '../lib/schemas'
import { store } from '../lib/store'

// Split the raw analytics JSON into rankable scalars vs group labels.
// The inner value is schema-validated (AnalyticsValueSchema mirrors the
// {value: f64} scalar / {community|componentId|block: int} label shapes;
// payloads never arrive — the core excludes link_prediction/voterank) so
// only known shapes get interpreted; anything else degrades to raw
// display instead of being guessed at.
function parse(metric, raw) {
  try {
    const probe = v.safeParse(AnalyticsValueSchema, JSON.parse(raw))
    if (!probe.success) return { kind: 'raw', display: String(raw).slice(0, 40) }
    // The schema pinned the shape; key-probing is display-only.
    const val = /** @type {Record<string, unknown>} */ (probe.output)
    if (typeof val.value === 'number') return { kind: 'scalar', display: fmtNum(val.value) }
    for (const k of ['community', 'componentId', 'block']) {
      if (Number.isInteger(val[k])) return { kind: 'label', display: `#${val[k]}` }
    }
  } catch {
    /* invalid JSON or schema drift — fall through to raw */
  }
  return { kind: 'raw', display: String(raw).slice(0, 40) }
}

function fmtNum(x) {
  if (!Number.isFinite(x)) return '—'
  if (x !== 0 && (Math.abs(x) >= 1e6 || Math.abs(x) < 1e-3)) return x.toExponential(2)
  return String(Number(x.toPrecision(4)))
}

const scalars = computed(() =>
  (store.metrics?.analytics ?? [])
    .map((a) => ({ metric: a.metric, ...parse(a.metric, a.value) }))
    .filter((s) => s.kind === 'scalar'),
)

const labels = computed(() =>
  (store.metrics?.analytics ?? [])
    .map((a) => ({ metric: a.metric, ...parse(a.metric, a.value) }))
    .filter((s) => s.kind === 'label'),
)
</script>

<style scoped>
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
</style>
