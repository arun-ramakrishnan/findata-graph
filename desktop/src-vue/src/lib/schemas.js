import * as v from 'valibot'

// Valibot mirrors of the 20 findata-core wire structs (core/src/lib.rs
// "Response types" block; the 21st `pub struct`, `Db`, is the connection
// handle and never crosses the IPC). The Rust struct is normative — this
// file is its JS mirror, same doctrine as the SPA's api.ts ↔ Flask
// jsonify. Field names stay snake_case: serde emits the Rust names as-is
// (only invoke *arguments* are camelCased by Tauri v2).
//
// Option<T> → nullable, Vec<T> → array, (String, i64) → tuple,
// serde_json::Value → unknown. Integral Rust fields pin intSchema() so a
// float where an id/count belongs fails loudly. Extra keys are tolerated
// (v.object is non-strict) — the contract is "these fields, these shapes",
// not "no more fields".
//
// Consumed by api.js (every invoke result parses before it returns) and by
// test/contract.test.js, which asserts test/fixtures.js against these
// schemas — `make fixtures` regeneration is the drift alarm: change a Rust
// struct (or its make_fixtures.py mirror) without updating a schema here
// and the contract test fails naming the command and field.

// intSchema() is a refinement ACTION in valibot (composes inside v.pipe),
// not a standalone schema — this wraps it so it can sit in entry/tuple
// positions while still pinning the wire's integral fields.
const intSchema = () => v.pipe(v.number(), v.integer())

export const StatsSchema = v.object({
  entities: intSchema(),
  edges: intSchema(),
  entity_types: v.array(v.tuple([v.string(), intSchema()])),
  edge_types: v.array(v.tuple([v.string(), intSchema()])),
})

export const CloudNodeSchema = v.object({
  id: v.string(),
  label: v.string(),
  entity_type: v.string(),
})

export const CloudEdgeSchema = v.object({
  source: v.string(),
  target: v.string(),
  edge_type: v.string(),
})

export const RelTypeSchema = v.object({
  edge_type: v.string(),
  count: intSchema(),
})

export const CloudSchema = v.object({
  nodes: v.array(CloudNodeSchema),
  edges: v.array(CloudEdgeSchema),
  relationship_types: v.array(RelTypeSchema),
  total_nodes: intSchema(),
  total_edges: intSchema(),
  as_of: v.nullable(v.string()),
})

export const EgoNodeSchema = v.object({
  name: v.string(),
  entity_type: v.string(),
  file_path: v.nullable(v.string()),
  focal: v.boolean(),
})

export const EgoEdgeSchema = v.object({
  edge_type: v.string(),
  source: v.string(),
  target: v.string(),
  properties: v.unknown(),
  weight: v.nullable(v.number()),
})

export const EgoSchema = v.object({
  name: v.string(),
  entity_type: v.string(),
  file_path: v.nullable(v.string()),
  edges: v.array(EgoEdgeSchema),
  nodes: v.array(EgoNodeSchema),
  truncated: v.boolean(),
  as_of: v.nullable(v.string()),
})

export const SuggestHitSchema = v.object({
  name: v.string(),
  entity_type: v.string(),
  file_path: v.nullable(v.string()),
})

export const SearchHitSchema = v.object({
  doc_type: v.string(),
  file_path: v.string(),
  title: v.string(),
  sector: v.nullable(v.string()),
  section_title: v.nullable(v.string()),
  snippet: v.string(),
})

export const SearchResultsSchema = v.object({
  results: v.array(SearchHitSchema),
  total: intSchema(),
  query: v.string(),
})

export const NoteContentSchema = v.object({
  path: v.string(),
  title: v.string(),
  markdown: v.string(),
})

export const MetricKVSchema = v.object({
  metric: v.string(),
  value: v.string(),
})

export const CompanyMetricSchema = v.object({
  label: v.nullable(v.string()),
  value_raw: v.string(),
  value_num: v.nullable(v.number()),
  unit: v.nullable(v.string()),
  period: v.nullable(v.string()),
})

export const EntityMetricsSchema = v.object({
  analytics: v.array(MetricKVSchema),
  company: v.array(CompanyMetricSchema),
})

export const RankEntrySchema = v.object({
  entity: v.string(),
  value: v.number(),
})

export const HyperMemberSchema = v.object({
  name: v.string(),
  role: v.nullable(v.string()),
})

export const SimilarHitSchema = v.object({
  file_path: v.string(),
  title: v.string(),
  sector: v.nullable(v.string()),
  score: v.number(),
})

export const HyperEdgeSchema = v.object({
  id: intSchema(),
  edge_type: v.string(),
  label: v.string(),
  role: v.nullable(v.string()),
  members: v.array(HyperMemberSchema),
})

export const DocEntrySchema = v.object({
  path: v.string(),
  title: v.string(),
})

// Inner shape of an EntityMetrics analytics `value` string (the raw JSON
// graph_analytics rows carry): {"value": f64} ranks vs group labels
// {community|componentId|block: int}. Payload metrics never arrive (the
// core excludes link_prediction/voterank), so anything else is drift —
// MetricsPanel degrades to raw display instead of guessing.
export const AnalyticsValueSchema = v.union([
  v.object({ value: v.number() }),
  v.object({ community: intSchema() }),
  v.object({ componentId: intSchema() }),
  v.object({ block: intSchema() }),
])
