//! SQLite data layer for the findata-graph desktop viewer.
//!
//! Read-only access to the repo's `memory/research.db`: the knowledge graph
//! (`entities` + `graph_edges`), the FTS5 note index (`note_search`), and the
//! `findata/**` markdown vault on disk. Mirrors the Flask API's semantics
//! (`app.py` `/api/graph/cloud`, `/api/graph/neighbors`, `/api/search`) so the
//! desktop UI speaks the same shapes without a server sidecar.

use rusqlite::{Connection, OpenFlags, params};
use serde::Serialize;
use std::collections::{HashMap, HashSet};
use std::fmt;
use std::path::{Path, PathBuf};
use std::time::Duration;

// --------------------------------------------------------------------------- //
// Errors
// --------------------------------------------------------------------------- //

#[derive(Debug)]
pub enum Error {
    Io(std::io::Error),
    Sql(rusqlite::Error),
    NotFound(String),
    Invalid(String),
    Root(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Io(e) => write!(f, "io error: {e}"),
            Error::Sql(e) => write!(f, "sqlite error: {e}"),
            Error::NotFound(s) => write!(f, "not found: {s}"),
            Error::Invalid(s) => write!(f, "invalid request: {s}"),
            Error::Root(s) => write!(f, "repo root: {s}"),
        }
    }
}

impl std::error::Error for Error {}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::Io(e)
    }
}

impl From<rusqlite::Error> for Error {
    fn from(e: rusqlite::Error) -> Self {
        Error::Sql(e)
    }
}

pub type Result<T> = std::result::Result<T, Error>;

// --------------------------------------------------------------------------- //
// Response types (serde-serialized over the Tauri IPC)
// --------------------------------------------------------------------------- //

#[derive(Serialize)]
pub struct Stats {
    pub entities: i64,
    pub edges: i64,
    pub entity_types: Vec<(String, i64)>,
    pub edge_types: Vec<(String, i64)>,
}

#[derive(Serialize)]
pub struct CloudNode {
    pub id: String,
    pub label: String,
    pub entity_type: String,
}

#[derive(Serialize, Clone)]
pub struct CloudEdge {
    pub source: String,
    pub target: String,
    pub edge_type: String,
}

#[derive(Serialize)]
pub struct RelType {
    pub edge_type: String,
    pub count: i64,
}

#[derive(Serialize)]
pub struct Cloud {
    pub nodes: Vec<CloudNode>,
    pub edges: Vec<CloudEdge>,
    pub relationship_types: Vec<RelType>,
    pub total_nodes: usize,
    pub total_edges: usize,
    pub as_of: Option<String>,
}

#[derive(Serialize)]
pub struct EgoNode {
    pub name: String,
    pub entity_type: String,
    pub file_path: Option<String>,
    pub focal: bool,
}

#[derive(Serialize)]
pub struct EgoEdge {
    pub edge_type: String,
    pub source: String,
    pub target: String,
    pub properties: serde_json::Value,
    pub weight: Option<f64>,
}

#[derive(Serialize)]
pub struct Ego {
    pub name: String,
    pub entity_type: String,
    pub file_path: Option<String>,
    pub edges: Vec<EgoEdge>,
    pub nodes: Vec<EgoNode>,
    pub truncated: bool,
    pub as_of: Option<String>,
}

#[derive(Serialize)]
pub struct SuggestHit {
    pub name: String,
    pub entity_type: String,
    pub file_path: Option<String>,
}

#[derive(Serialize, Clone)]
pub struct SearchHit {
    pub doc_type: String,
    pub file_path: String,
    pub title: String,
    pub sector: Option<String>,
    pub section_title: Option<String>,
    pub snippet: String,
}

#[derive(Serialize)]
pub struct SearchResults {
    pub results: Vec<SearchHit>,
    pub total: i64,
    pub query: String,
}

#[derive(Serialize)]
pub struct NoteContent {
    pub path: String,
    pub title: String,
    pub markdown: String,
}

#[derive(Serialize)]
pub struct MetricKV {
    pub metric: String,
    pub value: String,
}

#[derive(Serialize)]
pub struct CompanyMetric {
    pub label: Option<String>,
    pub value_raw: String,
    pub value_num: Option<f64>,
    pub unit: Option<String>,
    pub period: Option<String>,
}

#[derive(Serialize)]
pub struct EntityMetrics {
    pub analytics: Vec<MetricKV>,
    pub company: Vec<CompanyMetric>,
}

#[derive(Serialize)]
pub struct RankEntry {
    pub entity: String,
    pub value: f64,
}

#[derive(Serialize)]
pub struct HyperMember {
    pub name: String,
    pub role: Option<String>,
}

#[derive(Serialize)]
pub struct SimilarHit {
    pub file_path: String,
    pub title: String,
    pub sector: Option<String>,
    pub score: f64,
}

#[derive(Serialize)]
pub struct HyperEdge {
    pub id: i64,
    pub edge_type: String,
    pub label: String,
    pub role: Option<String>,
    pub members: Vec<HyperMember>,
}

#[derive(Serialize)]
pub struct DocEntry {
    pub path: String,
    pub title: String,
}

// --------------------------------------------------------------------------- //
// Repo root discovery
// --------------------------------------------------------------------------- //

/// Resolve the repo root: `$FINDATA_GRAPH_ROOT`, else walk up from the cwd,
/// else walk up from the executable. A root qualifies when `memory/research.db`
/// exists beneath it.
pub fn find_root() -> Result<PathBuf> {
    if let Ok(p) = std::env::var("FINDATA_GRAPH_ROOT") {
        let p = PathBuf::from(p);
        if p.join("memory/research.db").exists() {
            return Ok(p);
        }
        return Err(Error::Root(format!(
            "FINDATA_GRAPH_ROOT={} has no memory/research.db",
            p.display()
        )));
    }
    if let Ok(cwd) = std::env::current_dir() {
        if let Some(root) = walk_up(&cwd) {
            return Ok(root);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            if let Some(root) = walk_up(dir) {
                return Ok(root);
            }
        }
    }
    Err(Error::Root(
        "could not locate memory/research.db — set FINDATA_GRAPH_ROOT".into(),
    ))
}

fn walk_up(from: &Path) -> Option<PathBuf> {
    let mut dir = from.to_path_buf();
    loop {
        if dir.join("memory/research.db").exists() {
            return Some(dir);
        }
        if !dir.pop() {
            return None;
        }
    }
}

// --------------------------------------------------------------------------- //
// Db handle
// --------------------------------------------------------------------------- //

pub struct Db {
    root: PathBuf,
}

const EGO_EDGE_CAP: usize = 800;

impl Db {
    pub fn open() -> Result<Self> {
        Ok(Self {
            root: find_root()?,
        })
    }

    pub fn open_at(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    fn connect(&self) -> Result<Connection> {
        let db = self.root.join("memory/research.db");
        let conn = Connection::open_with_flags(
            db,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        conn.busy_timeout(Duration::from_secs(5))?;
        Ok(conn)
    }

    // -- stats / legend ----------------------------------------------------- //

    pub fn stats(&self) -> Result<Stats> {
        let conn = self.connect()?;
        let entities = conn.query_row("SELECT COUNT(*) FROM entities", [], |r| r.get(0))?;
        let edges = conn.query_row("SELECT COUNT(*) FROM graph_edges", [], |r| r.get(0))?;
        let entity_types = group_counts(
            &conn,
            "SELECT entity_type, COUNT(*) AS n FROM entities GROUP BY 1 ORDER BY n DESC",
        )?;
        let edge_types = group_counts(
            &conn,
            "SELECT edge_type, COUNT(*) AS n FROM graph_edges GROUP BY 1 ORDER BY n DESC",
        )?;
        Ok(Stats {
            entities,
            edges,
            entity_types,
            edge_types,
        })
    }

    // -- whole-graph cloud (Flask /api/graph/cloud) ------------------------- //

    pub fn graph_cloud(
        &self,
        edge_type: Option<&str>,
        as_of: Option<&str>,
    ) -> Result<Cloud> {
        let iso = normalise_as_of(as_of)?;
        let conn = self.connect()?;
        // One static query: NULL params disable their clause. Temporal
        // predicate mirrors Flask `_as_of_predicate` — undated edges are
        // always-valid, so filtering never nukes the structural backbone.
        let et = edge_type.filter(|s| !s.is_empty()).map(str::to_string);
        let mut stmt = conn.prepare(
            "SELECT source, target, edge_type FROM graph_edges \
             WHERE (?1 IS NULL OR edge_type = ?1) \
               AND (?2 IS NULL OR valid_from IS NULL OR valid_from <= ?2) \
               AND (?3 IS NULL OR valid_to IS NULL OR valid_to >= ?3)",
        )?;
        let rows = stmt.query_map(params![et, iso.clone(), iso.clone()], |r| {
            Ok(CloudEdge {
                source: r.get(0)?,
                target: r.get(1)?,
                edge_type: r.get(2)?,
            })
        })?;
        let mut edges: Vec<CloudEdge> = Vec::new();
        for row in rows {
            edges.push(row?);
        }

        // Entity types for every node incident to the (filtered) edge set.
        let mut seen: HashSet<&str> = HashSet::new();
        for e in &edges {
            seen.insert(&e.source);
            seen.insert(&e.target);
        }
        let mut type_map: HashMap<String, String> = HashMap::new();
        {
            let mut stmt = conn.prepare("SELECT name, entity_type FROM entities")?;
            let rows = stmt.query_map([], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
            })?;
            for row in rows {
                let (name, et) = row?;
                if seen.contains(name.as_str()) {
                    type_map.insert(name, et);
                }
            }
        }
        let mut nodes: Vec<CloudNode> = seen
            .into_iter()
            .map(|name| {
                let entity_type = type_map
                    .get(name)
                    .cloned()
                    .unwrap_or_else(|| "unknown".into());
                CloudNode {
                    id: name.to_string(),
                    label: name.to_string(),
                    entity_type,
                }
            })
            .collect();
        nodes.sort_by(|a, b| (&a.entity_type, &a.id).cmp(&(&b.entity_type, &b.id)));

        // Full-corpus relationship summary (always unfiltered, like Flask).
        let mut relationship_types: Vec<RelType> = Vec::new();
        {
            let mut stmt = conn.prepare(
                "SELECT edge_type, COUNT(*) FROM graph_edges GROUP BY 1 ORDER BY 2 DESC",
            )?;
            let rows = stmt.query_map([], |r| {
                Ok(RelType {
                    edge_type: r.get(0)?,
                    count: r.get(1)?,
                })
            })?;
            for row in rows {
                relationship_types.push(row?);
            }
        }

        let total_edges = edges.len();
        let total_nodes = nodes.len();
        Ok(Cloud {
            nodes,
            edges,
            relationship_types,
            total_nodes,
            total_edges,
            as_of: iso,
        })
    }

    // -- ego network (Flask /api/graph/neighbors) --------------------------- //

    pub fn graph_ego(&self, name: &str, as_of: Option<&str>) -> Result<Ego> {
        let iso = normalise_as_of(as_of)?;
        let conn = self.connect()?;
        let mut stmt = conn.prepare(
            "SELECT edge_type, source, target, properties, weight \
             FROM graph_edges WHERE (source = ?1 OR target = ?1) \
               AND (?3 IS NULL OR valid_from IS NULL OR valid_from <= ?3) \
               AND (?4 IS NULL OR valid_to IS NULL OR valid_to >= ?4) \
             ORDER BY COALESCE(weight, 0) DESC, id LIMIT ?2",
        )?;
        let raw = stmt
            .query_map(
                params![name, EGO_EDGE_CAP as i64, iso.clone(), iso.clone()],
                |r| {
                    Ok(EgoEdge {
                        edge_type: r.get(0)?,
                        source: r.get(1)?,
                        target: r.get(2)?,
                        properties: parse_json(r.get::<_, Option<String>>(3)?),
                        weight: r.get(4)?,
                    })
                },
            )?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        let truncated = raw.len() >= EGO_EDGE_CAP;

        // Neighbor vertex metadata (type + note file), focal first.
        let mut names: Vec<String> = vec![name.to_string()];
        let mut seen: HashSet<String> = HashSet::new();
        seen.insert(name.to_string());
        for e in &raw {
            for n in [e.source.as_str(), e.target.as_str()] {
                if n != name && seen.insert(n.to_string()) {
                    names.push(n.to_string());
                }
            }
        }
        let mut entity_map: HashMap<String, (String, Option<String>)> = HashMap::new();
        for chunk in names.chunks(400) {
            let placeholders = vec!["?"; chunk.len()].join(",");
            let sql = format!(
                "SELECT name, entity_type, file_path FROM entities WHERE name IN ({placeholders})"
            );
            let mut stmt = conn.prepare(&sql)?;
            let rows = stmt.query_map(rusqlite::params_from_iter(chunk), |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, Option<String>>(2)?,
                ))
            })?;
            for row in rows {
                let (n, t, f) = row?;
                entity_map.insert(n, (t, f));
            }
        }

        let mut nodes: Vec<EgoNode> = Vec::with_capacity(names.len());
        for n in names {
            let (entity_type, file_path) = entity_map
                .remove(&n)
                .unwrap_or_else(|| ("unknown".into(), None));
            nodes.push(EgoNode {
                focal: n == name,
                name: n,
                entity_type,
                file_path,
            });
        }
        let (entity_type, file_path) = match nodes.first() {
            Some(f) if f.focal => (f.entity_type.clone(), f.file_path.clone()),
            _ => ("unknown".into(), None),
        };

        Ok(Ego {
            name: name.to_string(),
            entity_type,
            file_path,
            edges: raw,
            nodes,
            truncated,
            as_of: iso,
        })
    }

    // -- entity suggest / browse ------------------------------------------- //

    pub fn suggest(&self, q: &str, limit: usize) -> Result<Vec<SuggestHit>> {
        let conn = self.connect()?;
        let q = q.trim();
        if q.is_empty() {
            return Ok(vec![]);
        }
        let like_q = format!("%{}%", escape_like(q));
        let prefix = format!("{}%", escape_like(q));
        let mut stmt = conn.prepare(
            "SELECT name, entity_type, file_path FROM entities \
             WHERE name LIKE ?1 ESCAPE '\\' \
             ORDER BY CASE WHEN name = ?2 THEN 0 WHEN name LIKE ?3 ESCAPE '\\' THEN 1 ELSE 2 END, \
                      length(name), name \
             LIMIT ?4",
        )?;
        let rows = stmt
            .query_map(params![like_q, q, prefix, limit as i64], |r| {
                Ok(SuggestHit {
                    name: r.get(0)?,
                    entity_type: r.get(1)?,
                    file_path: r.get(2)?,
                })
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    /// Nav list: sectors, super-sectors and themes for the sidebar.
    pub fn sectors(&self) -> Result<Vec<SuggestHit>> {
        let conn = self.connect()?;
        let mut stmt = conn.prepare(
            "SELECT name, entity_type, file_path FROM entities \
             WHERE entity_type IN ('sector', 'super_sector', 'theme') \
             ORDER BY CASE entity_type WHEN 'super_sector' THEN 0 WHEN 'sector' THEN 1 ELSE 2 END, name",
        )?;
        let rows = stmt
            .query_map([], |r| {
                Ok(SuggestHit {
                    name: r.get(0)?,
                    entity_type: r.get(1)?,
                    file_path: r.get(2)?,
                })
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    // -- FTS note search (Flask /api/search) -------------------------------- //

    pub fn search(&self, q: &str, doc_type: Option<&str>, limit: usize) -> Result<SearchResults> {
        let conn = self.connect()?;
        let Some(and_expr) = fts_expr(q, " ") else {
            return Err(Error::Invalid("empty search query".into()));
        };
        let or_expr = fts_expr(q, " OR ").unwrap_or(and_expr.clone());

        // AND-first precision pass; the OR space fills to `limit` (the Flask
        // deep-probe lesson: sectioned FTS5 AND starves question-shaped
        // queries, so recall comes from OR with BM25 still on top).
        let mut hits = search_page(&conn, &and_expr, doc_type, limit)?;
        if hits.len() < limit {
            let seen: HashSet<String> = hits.iter().map(|h| h.file_path.clone()).collect();
            for h in search_page(&conn, &or_expr, doc_type, limit)? {
                if !seen.contains(&h.file_path) {
                    hits.push(h);
                    if hits.len() >= limit {
                        break;
                    }
                }
            }
        }

        let total = count_notes(&conn, &or_expr, doc_type)?;
        Ok(SearchResults {
            results: hits,
            total,
            query: q.trim().to_string(),
        })
    }

    // -- note vault --------------------------------------------------------- //

    /// Read a markdown note. Paths are repo-relative and must live under
    /// `findata/` or `doc/`.
    ///
    /// Prefix + `..` checks are vault discipline, NOT a sandbox: symlinks
    /// inside the tree are not canonicalized, so this is not a hard
    /// boundary against a hostile local filesystem (accepted for the
    /// operator-local threat model; see desktop_security_hardening DESK-3).
    pub fn read_note(&self, rel: &str) -> Result<NoteContent> {
        let rel = rel.trim().trim_start_matches('/');
        if rel.contains("..") || Path::new(rel).is_absolute() {
            return Err(Error::Invalid(format!("path escapes the vault: {rel}")));
        }
        if !(rel.starts_with("findata/") || rel.starts_with("doc/")) {
            return Err(Error::Invalid(format!(
                "only findata/ and doc/ notes are readable, got: {rel}"
            )));
        }
        let abs = self.root.join(rel);
        let meta = std::fs::metadata(&abs)?;
        if meta.len() > 4 * 1024 * 1024 {
            return Err(Error::Invalid(format!("note too large: {}", meta.len())));
        }
        let markdown = std::fs::read_to_string(&abs)?;
        Ok(NoteContent {
            path: rel.to_string(),
            title: note_title(rel, &markdown),
            markdown,
        })
    }

    /// The dedicated note for an entity (`entities.file_path`), if any.
    pub fn entity_note(&self, name: &str) -> Result<Option<NoteContent>> {
        let conn = self.connect()?;
        let path: Option<String> = conn
            .query_row(
                "SELECT file_path FROM entities WHERE name = ?1",
                params![name],
                |r| r.get(0),
            )
            .optional()?;
        match path {
            Some(p) if !p.is_empty() => Ok(Some(self.read_note(&p)?)),
            _ => Ok(None),
        }
    }

    // -- metrics + rank (S3) ---------------------------------------------------- //
    //
    // Read-only views over the writer-owned tables: `graph_analytics`
    // (refreshed by `make recompute-graph`/`recompute-hyper`) and
    // `company_metrics` (derive_insights). No derivation here — mirrors the
    // Flask `/api/graph/metrics/<metric>` shapes (scalar/label/payload
    // split), All-SQLite, no DuckDB.

    /// Structured-payload metrics have no scalar reading (`link_prediction`
    /// candidates, `voterank` seeds) — excluded from the metrics tab.
    const PAYLOAD_METRICS: &[&str] = &["link_prediction", "voterank"];

    /// Per-entity metrics bundle: graph analytics (raw JSON values) +
    /// latest-first company metrics.
    pub fn entity_metrics(&self, name: &str) -> Result<EntityMetrics> {
        let conn = self.connect()?;
        let mut stmt = conn.prepare(
            "SELECT metric, value FROM graph_analytics WHERE entity_name = ?1 ORDER BY metric",
        )?;
        let rows = stmt.query_map(params![name], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
        })?;
        let mut analytics = Vec::new();
        for row in rows {
            let (metric, value) = row?;
            if !Self::PAYLOAD_METRICS.contains(&metric.as_str()) {
                analytics.push(MetricKV { metric, value });
            }
        }
        let mut stmt = conn.prepare(
            "SELECT metric_label, value_raw, value_num, unit, period \
             FROM company_metrics WHERE entity = ?1 ORDER BY id DESC LIMIT 100",
        )?;
        let rows = stmt.query_map(params![name], |r| {
            Ok(CompanyMetric {
                label: r.get(0)?,
                value_raw: r.get(1)?,
                value_num: r.get(2)?,
                unit: r.get(3)?,
                period: r.get(4)?,
            })
        })?;
        let mut company = Vec::new();
        for row in rows {
            company.push(row?);
        }
        Ok(EntityMetrics { analytics, company })
    }

    /// Corpus-wide scalar ranking for one metric (cloud rank tint).
    /// Reads `$.value` as REAL — the Flask scalar branch pushed into SQL
    /// (json_extract + CAST + ORDER BY); label/payload metrics have no
    /// numeric `$.value` and come back empty → hard error, never a
    /// misleading all-zero tint.
    pub fn metric_values(&self, metric: &str) -> Result<Vec<RankEntry>> {
        let conn = self.connect()?;
        let mut stmt = conn.prepare(
            "SELECT entity_name, CAST(json_extract(value, '$.value') AS REAL) AS v \
             FROM graph_analytics WHERE metric = ?1 \
               AND json_extract(value, '$.value') IS NOT NULL \
             ORDER BY v DESC",
        )?;
        let rows = stmt.query_map(params![metric.to_lowercase()], |r| {
            Ok(RankEntry {
                entity: r.get(0)?,
                value: r.get(1)?,
            })
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        if out.is_empty() {
            return Err(Error::NotFound(format!(
                "unknown or non-scalar metric: {metric}"
            )));
        }
        Ok(out)
    }

    // -- hypergraph lane (S5) --------------------------------------------------- //
    //
    // Read-only views over `hyper_edges` + `hyper_incidences` (writer-owned,
    // like S3 metrics). Edge-level `valid_from/valid_to` filter under
    // `as_of`; incidences carry no dates (0 dated rows), so the edge
    // validity governs. Members capped per edge — overlays paint halos,
    // not full rosters.

    /// Hyperedges incident to one entity, with capped member lists.
    pub fn entity_hyperedges(
        &self,
        name: &str,
        as_of: Option<&str>,
    ) -> Result<Vec<HyperEdge>> {
        const MEMBER_CAP: i64 = 50;
        let iso = normalise_as_of(as_of)?;
        let conn = self.connect()?;
        let mut stmt = conn.prepare(
            "SELECT he.id, he.edge_type, he.label, hi.role \
             FROM hyper_edges he JOIN hyper_incidences hi ON hi.edge_id = he.id \
             WHERE hi.entity_name = ?1 \
               AND (?2 IS NULL OR he.valid_from IS NULL OR he.valid_from <= ?2) \
               AND (?3 IS NULL OR he.valid_to IS NULL OR he.valid_to >= ?3) \
             ORDER BY he.edge_type, he.label LIMIT 100",
        )?;
        let rows = stmt.query_map(params![name, iso.clone(), iso.clone()], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, Option<String>>(3)?,
            ))
        })?;
        let mut edges: Vec<(i64, String, String, Option<String>)> = Vec::new();
        for row in rows {
            edges.push(row?);
        }
        let mut out = Vec::with_capacity(edges.len());
        for (id, edge_type, label, role) in edges {
            let mut mstmt = conn.prepare(
                "SELECT entity_name, role FROM hyper_incidences \
                 WHERE edge_id = ?1 ORDER BY entity_name LIMIT ?2",
            )?;
            let mrows = mstmt.query_map(params![id, MEMBER_CAP], |r| {
                Ok(HyperMember {
                    name: r.get(0)?,
                    role: r.get(1)?,
                })
            })?;
            let mut members = Vec::new();
            for m in mrows {
                members.push(m?);
            }
            out.push(HyperEdge {
                id,
                edge_type,
                label,
                role,
                members,
            });
        }
        Ok(out)
    }

    // -- docs browser (S2) ---------------------------------------------------- //

    // -- hybrid semantic search (S7) -------------------------------------------- //
    //
    // Pure-Rust cosine over the stored note-section vectors (`note_search`
    // embedding BLOBs: 384 f32 LE, full 17k-row coverage) fused with BM25
    // ranks at RRF K=60 — the `rebuild_doc_search.py:857` rule mirrored.
    // No query embedding yet (S7 option i deferred): the cosine leg runs
    // pseudo-relevance feedback — similarity to the top BM25 hit's best
    // section — so all fusion machinery ships with stored vectors only.
    // Scoring is best-section-pair per file (max cosine), mirroring the
    // sectioned-dedup philosophy: one row per file, best section wins.

    /// Reciprocal-rank fuse two rank positions (0-based). Missing cosine
    /// leg degrades to BM25-only — same branch as the Python hybrid.
    const RRF_K: f64 = 60.0;

    /// Notes most similar to one file (max section-pair cosine, self excluded).
    pub fn similar_notes(&self, path: &str, limit: usize) -> Result<Vec<SimilarHit>> {
        let conn = self.connect()?;
        let sections = load_vectors(&conn)?;
        let query: Vec<&Vec<f32>> = sections
            .iter()
            .filter(|s| s.file_path == path)
            .map(|s| &s.vector)
            .collect();
        if query.is_empty() {
            return Ok(Vec::new());
        }
        let mut best: HashMap<&str, (f64, &str, Option<&str>)> = HashMap::new();
        for cand in &sections {
            if cand.file_path == path {
                continue;
            }
            let mut top = f64::NEG_INFINITY;
            for q in &query {
                let s = cosine(q, &cand.vector);
                if s > top {
                    top = s;
                }
            }
            match best.get_mut(cand.file_path.as_str()) {
                Some(e) if e.0 >= top => {}
                _ => {
                    best.insert(
                        cand.file_path.as_str(),
                        (top, cand.title.as_str(), cand.sector.as_deref()),
                    );
                }
            }
        }
        let mut out: Vec<SimilarHit> = best
            .into_iter()
            .map(|(fp, (score, title, sector))| SimilarHit {
                file_path: fp.to_string(),
                title: title.to_string(),
                sector: sector.map(str::to_string),
                score,
            })
            .collect();
        out.sort_by(|a, b| b.score.total_cmp(&a.score));
        out.truncate(limit.clamp(1, 25));
        Ok(out)
    }

    /// Hybrid text search: BM25 ranks fused with cosine ranks (similarity
    /// to the top BM25 hit) at RRF K=60. Same `SearchResults` shape as
    /// `search()` — the fused ORDER carries the fusion; `total` counts
    /// BM25 matches (cosine extras may fill the page, as in Python).
    pub fn search_hybrid(&self, q: &str, limit: usize) -> Result<SearchResults> {
        let pool = limit.clamp(1, 50).max(20);
        let bm = self.search(q, None, pool)?;
        if bm.results.is_empty() {
            return Ok(bm);
        }
        let conn = self.connect()?;
        let sections = load_vectors(&conn)?;
        let top_file = &bm.results[0].file_path;
        let query: Vec<&Vec<f32>> = sections
            .iter()
            .filter(|s| &s.file_path == top_file)
            .map(|s| &s.vector)
            .collect();
        // Cosine ranks over the pool (best section-pair per file).
        let mut cos_rank: HashMap<&str, usize> = HashMap::new();
        if !query.is_empty() {
            let mut scored: Vec<(&str, f64)> = Vec::new();
            let mut seen: HashSet<&str> = HashSet::new();
            for cand in &sections {
                if !seen.insert(cand.file_path.as_str()) {
                    continue;
                }
                let mut top = f64::NEG_INFINITY;
                for qv in &query {
                    let s = cosine(qv, &cand.vector);
                    if s > top {
                        top = s;
                    }
                }
                scored.push((cand.file_path.as_str(), top));
            }
            scored.sort_by(|a, b| b.1.total_cmp(&a.1));
            for (pos, (fp, _)) in scored.into_iter().enumerate() {
                cos_rank.insert(fp, pos);
            }
        }
        let worst = cos_rank.len();
        let mut fused: Vec<(f64, usize)> = bm
            .results
            .iter()
            .enumerate()
            .map(|(bm_pos, h)| {
                let cos_pos = cos_rank
                    .get(h.file_path.as_str())
                    .copied()
                    .unwrap_or(worst + bm_pos);
                let rrf = 1.0 / (Self::RRF_K + bm_pos as f64 + 1.0)
                    + 1.0 / (Self::RRF_K + cos_pos as f64 + 1.0);
                (rrf, bm_pos)
            })
            .collect();
        fused.sort_by(|a, b| b.0.total_cmp(&a.0));
        let results: Vec<SearchHit> = fused
            .into_iter()
            .take(limit.clamp(1, 50))
            .map(|(_, i)| bm.results[i].clone())
            .collect();
        Ok(SearchResults {
            results,
            total: bm.total,
            query: bm.query,
        })
    }

    // -- docs browser (S2) ---------------------------------------------------- //
    /// Flat listing of the `doc/**` vault via the `doc_search` FTS5 sidecar
    /// (`memory/doc_search.db`), optionally filtered by full-text MATCH.
    /// One row per file (best-BM25 section wins); unfiltered listings sort
    /// by path. The sidecar is gitignored and rebuilt by
    /// `rebuild_doc_search.py` (`make search-fresh`) — it deliberately
    /// lives outside `research.db` so `doc/local/` plaintext never enters
    /// the snapshotted DB. A missing sidecar is a hard error, never a
    /// silent empty list.
    pub fn browse_docs(&self, filter: Option<&str>) -> Result<Vec<DocEntry>> {
        let sidecar = self.root.join("memory/doc_search.db");
        if !sidecar.exists() {
            return Err(Error::NotFound(format!(
                "doc_search sidecar missing at {} — run make search-fresh",
                sidecar.display()
            )));
        }
        let conn = Connection::open_with_flags(
            &sidecar,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        conn.busy_timeout(Duration::from_secs(5))?;
        let needle = filter.map(str::trim).filter(|f| !f.is_empty());
        let Some(q) = needle else {
            let mut stmt = conn.prepare(
                "SELECT file_path, max(title) FROM doc_search \
                 GROUP BY file_path ORDER BY file_path",
            )?;
            return stmt
                .query_map([], |r| {
                    Ok(DocEntry {
                        path: r.get(0)?,
                        title: r.get(1)?,
                    })
                })?
                .collect::<std::result::Result<Vec<_>, _>>()
                .map_err(Error::from);
        };
        // FTS filter: AND-first precision pass, OR-fill to the cap (same
        // shape as `search()` — sectioned FTS5 AND starves, OR recalls).
        let Some(and_expr) = fts_expr(q, " ") else {
            return Ok(Vec::new());
        };
        let or_expr = fts_expr(q, " OR ").unwrap_or(and_expr.clone());
        const CAP: usize = 200;
        let mut hits = docs_page(&conn, &and_expr, CAP)?;
        if hits.len() < CAP {
            let seen: HashSet<String> = hits.iter().map(|h| h.path.clone()).collect();
            for h in docs_page(&conn, &or_expr, CAP)? {
                if !seen.contains(&h.path) {
                    hits.push(h);
                    if hits.len() >= CAP {
                        break;
                    }
                }
            }
        }
        Ok(hits)
    }
}

// --------------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------------- //

use rusqlite::OptionalExtension;

fn group_counts(conn: &Connection, sql: &str) -> Result<Vec<(String, i64)>> {
    let mut stmt = conn.prepare(sql)?;
    let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row?);
    }
    Ok(out)
}

fn parse_json(s: Option<String>) -> serde_json::Value {
    match s {
        Some(raw) if !raw.is_empty() => {
            serde_json::from_str(&raw).unwrap_or(serde_json::Value::Null)
        }
        _ => serde_json::Value::Null,
    }
}

fn escape_like(q: &str) -> String {
    q.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_")
}

/// Build an FTS5 MATCH expression from free text: quoted tokens joined by
/// `joiner` (`" "` → implicit AND, `" OR "` → recall). None when no tokens.
fn fts_expr(q: &str, joiner: &str) -> Option<String> {
    let tokens: Vec<String> = q
        .split(|c: char| !(c.is_alphanumeric() || c == '_'))
        .filter(|t| !t.is_empty())
        .map(|t| format!("\"{}\"", t.replace('"', "")))
        .collect();
    if tokens.is_empty() {
        None
    } else {
        Some(tokens.join(joiner))
    }
}

/// Normalise an `as_of` temporal filter: 'YYYY' → YYYY-01-01,
/// 'YYYY-MM' → YYYY-MM-01, full date passes through. None/blank →
/// None (unfiltered). Anything else is Invalid — mirrors
/// `helpers/graph/query.py::_normalise_as_of` exactly.
fn normalise_as_of(as_of: Option<&str>) -> Result<Option<String>> {
    let Some(raw) = as_of.map(str::trim).filter(|s| !s.is_empty()) else {
        return Ok(None);
    };
    let b = raw.as_bytes();
    let digits = |r: std::ops::Range<usize>| b.get(r).is_some_and(|s| s.iter().all(|c| c.is_ascii_digit()));
    if raw.len() == 4 && digits(0..4) {
        return Ok(Some(format!("{raw}-01-01")));
    }
    if raw.len() == 7 && digits(0..4) && b[4] == b'-' && digits(5..7) {
        return Ok(Some(format!("{raw}-01")));
    }
    if raw.len() == 10 && digits(0..4) && b[4] == b'-' && digits(5..7) && b[7] == b'-' && digits(8..10) {
        return Ok(Some(raw.to_string()));
    }
    Err(Error::Invalid(
        "as_of must be a year (YYYY), year-month (YYYY-MM), or date (YYYY-MM-DD)".into(),
    ))
}

/// One sectioned-dedup FTS page: best-ranked section per note (Flask shape —
/// inner MATCH + rank LIMIT, window ROW_NUMBER over file_path, rn = 1).
fn search_page(
    conn: &Connection,
    expr: &str,
    doc_type: Option<&str>,
    limit: usize,
) -> Result<Vec<SearchHit>> {
    let filter = if doc_type.is_some() {
        " AND doc_type = ?2"
    } else {
        ""
    };
    let sql = format!(
        "SELECT doc_type, file_path, title, sector, section_title, anchor, snip FROM (\
           SELECT doc_type, file_path, title, sector, section_title, anchor, snip, rank, \
                  ROW_NUMBER() OVER (PARTITION BY file_path ORDER BY rank) AS rn FROM (\
             SELECT doc_type, file_path, title, sector, section_title, anchor, rank, \
                    snippet(note_search, 4, '<mark>', '</mark>', '…', 12) AS snip \
             FROM note_search WHERE note_search MATCH ?1{filter} \
             ORDER BY rank LIMIT 1024\
           )\
         ) WHERE rn = 1 ORDER BY rank LIMIT ?{}",
        if doc_type.is_some() { 3 } else { 2 }
    );
    let mut stmt = conn.prepare(&sql)?;
    let map_row = |r: &rusqlite::Row<'_>| {
        Ok(SearchHit {
            doc_type: r.get(0)?,
            file_path: r.get(1)?,
            title: r.get(2)?,
            sector: r.get(3)?,
            section_title: r.get(4)?,
            snippet: r.get(6)?,
        })
    };
    let rows = match doc_type {
        Some(dt) => stmt
            .query_map(params![expr, dt, limit as i64], map_row)?
            .collect::<std::result::Result<Vec<_>, _>>()?,
        None => stmt
            .query_map(params![expr, limit as i64], map_row)?
            .collect::<std::result::Result<Vec<_>, _>>()?,
    };
    Ok(rows)
}

fn count_notes(conn: &Connection, expr: &str, doc_type: Option<&str>) -> Result<i64> {
    let (sql, n) = match doc_type {
        Some(_) => (
            "SELECT COUNT(DISTINCT file_path) FROM note_search \
             WHERE note_search MATCH ?1 AND doc_type = ?2",
            2,
        ),
        None => (
            "SELECT COUNT(DISTINCT file_path) FROM note_search WHERE note_search MATCH ?1",
            1,
        ),
    };
    let count = match n {
        2 => conn.query_row(sql, params![expr, doc_type], |r| r.get(0))?,
        _ => conn.query_row(sql, params![expr], |r| r.get(0))?,
    };
    Ok(count)
}

/// One deduped doc_search page: best-BM25 section per file (same
/// sectioned shape as `search_page` — window ROW_NUMBER over file_path).
fn docs_page(conn: &Connection, expr: &str, limit: usize) -> Result<Vec<DocEntry>> {
    let mut stmt = conn.prepare(
        "SELECT file_path, title FROM (\
           SELECT file_path, title, rank, \
                  ROW_NUMBER() OVER (PARTITION BY file_path ORDER BY rank) AS rn FROM (\
             SELECT file_path, title, rank \
             FROM doc_search WHERE doc_search MATCH ?1 \
             ORDER BY rank LIMIT 2048\
           )\
         ) WHERE rn = 1 ORDER BY rank LIMIT ?2",
    )?;
    let limit_i = limit as i64;
    let rows = stmt.query_map(params![expr, limit_i], |r| {
        Ok(DocEntry {
            path: r.get(0)?,
            title: r.get(1)?,
        })
    })?;
    rows.collect::<std::result::Result<Vec<_>, _>>()
        .map_err(Error::from)
}

/// One embedded note section: 384 f32 LE from the `note_search` BLOB.
struct SectionVec {
    file_path: String,
    title: String,
    sector: Option<String>,
    vector: Vec<f32>,
}

/// All stored section vectors (full 17k-row coverage today). Rows with a
/// short/missing BLOB are skipped — the cosine leg degrades, never errors.
fn load_vectors(conn: &Connection) -> Result<Vec<SectionVec>> {
    let mut stmt = conn.prepare(
        "SELECT file_path, title, sector, embedding FROM note_search \
         WHERE embedding IS NOT NULL",
    )?;
    let rows = stmt.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, Option<String>>(2)?,
            r.get::<_, Vec<u8>>(3)?,
        ))
    })?;
    let mut out = Vec::new();
    for row in rows {
        let (file_path, title, sector, blob) = row?;
        if blob.len() != 384 * 4 {
            continue;
        }
        let vector: Vec<f32> = blob
            .chunks_exact(4)
            .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
            .collect();
        out.push(SectionVec {
            file_path,
            title,
            sector,
            vector,
        });
    }
    Ok(out)
}

fn cosine(a: &[f32], b: &[f32]) -> f64 {
    let (mut dot, mut na, mut nb) = (0.0f64, 0.0f64, 0.0f64);
    for (x, y) in a.iter().zip(b.iter()) {
        let (x, y) = (*x as f64, *y as f64);
        dot += x * y;
        na += x * x;
        nb += y * y;
    }
    if na <= 0.0 || nb <= 0.0 {
        return 0.0;
    }
    dot / (na.sqrt() * nb.sqrt())
}

fn note_title(rel: &str, markdown: &str) -> String {    for line in markdown.lines().take(30) {
        let t = line.trim();
        if let Some(rest) = t.strip_prefix("# ") {
            let rest = rest.trim();
            if !rest.is_empty() {
                return rest.to_string();
            }
        }
    }
    Path::new(rel)
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| rel.to_string())
}
