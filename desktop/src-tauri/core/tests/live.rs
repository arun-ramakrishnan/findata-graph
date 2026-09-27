//! Integration tests against the live repo database (read-only).
//!
//! These assert *semantics*, not exact counts, so they stay valid as the
//! corpus evolves.

use findata_core::Db;

fn db() -> Db {
    Db::open().expect("repo root with memory/research.db must be discoverable")
}

#[test]
fn root_and_stats() {
    let db = db();
    assert!(db.root().join("memory/research.db").exists());
    let s = db.stats().unwrap();
    assert!(s.entities > 1_000, "entities: {}", s.entities);
    assert!(s.edges > 10_000, "edges: {}", s.edges);
    assert!(s.entity_types.iter().any(|(t, _)| t == "company"));
    assert!(s.edge_types.iter().any(|(t, _)| t == "competes_with"));
    assert!(s.edges == s.edge_types.iter().map(|(_, n)| n).sum::<i64>());
}

#[test]
fn ego_company_bundle() {
    let db = db();
    let ego = db.graph_ego("CEAT", None).unwrap();
    assert_eq!(ego.entity_type, "company");
    assert_eq!(ego.name, "CEAT");
    assert!(ego.file_path.is_some(), "CEAT has a dedicated note");
    assert!(!ego.edges.is_empty());
    // Known relationships: a tyre maker competes, sits in a sector, has peers.
    let types: std::collections::HashSet<&str> =
        ego.edges.iter().map(|e| e.edge_type.as_str()).collect();
    assert!(types.contains("part_of"), "CEAT part_of a sector");
    assert!(types.contains("competes_with") || types.contains("jv_with"));
    // Focal node first with the right metadata.
    let focal = &ego.nodes[0];
    assert!(focal.focal);
    assert_eq!(focal.name, "CEAT");
    // Every edge endpoint has a node slot.
    let names: std::collections::HashSet<&str> =
        ego.nodes.iter().map(|n| n.name.as_str()).collect();
    for e in &ego.edges {
        assert!(names.contains(e.source.as_str()), "{} missing", e.source);
        assert!(names.contains(e.target.as_str()), "{} missing", e.target);
    }
}

#[test]
fn ego_sector_bundle() {
    let db = db();
    let ego = db.graph_ego("Automotive", None).unwrap();
    assert_eq!(ego.entity_type, "sector");
    let member_edges = ego
        .edges
        .iter()
        .filter(|e| e.edge_type == "has_company" || e.edge_type == "part_of")
        .count();
    assert!(member_edges > 10, "sector members: {member_edges}");
}

#[test]
fn ego_unknown_entity_is_empty_not_error() {
    let db = db();
    let ego = db.graph_ego("Nonexistent Company PLC 123", None).unwrap();
    assert_eq!(ego.entity_type, "unknown");
    assert!(ego.edges.is_empty());
}

#[test]
fn cloud_filter_and_summary() {
    let db = db();
    let cloud = db.graph_cloud(Some("acquired"), None).unwrap();
    assert!(cloud.total_edges > 0 && cloud.total_edges < 5_000);
    assert!(cloud
        .edges
        .iter()
        .all(|e| e.edge_type == "acquired"));
    assert!(cloud.total_nodes >= 2);
    // Summary card always reports the whole corpus, even when filtered.
    let total: i64 = cloud.relationship_types.iter().map(|r| r.count).sum();
    assert!(total > cloud.total_edges as i64);
    assert!(cloud
        .relationship_types
        .iter()
        .any(|r| r.edge_type == "acquired"));
}

#[test]
fn search_finds_vault_content() {
    let db = db();
    let res = db.search("shrimp feed", None, 20).unwrap();
    assert!(res.total > 0, "no hits for shrimp feed");
    assert!(!res.results.is_empty());
    assert!(
        res.results
            .iter()
            .any(|h| h.file_path.contains("Avanti") || h.title.contains("Avanti")),
        "expected an Avanti Feeds hit, got {:?}",
        res.results.first().map(|h| &h.title)
    );
    assert!(res.results.iter().any(|h| h.snippet.contains("<mark>")));
}

#[test]
fn search_sectioned_dedup() {
    let db = db();
    // A company note has many section rows; results must be one per file.
    let res = db.search("CEAT tyres", None, 30).unwrap();
    let mut seen = std::collections::HashSet::new();
    for h in &res.results {
        assert!(seen.insert(h.file_path.clone()), "dup note {}", h.file_path);
    }
}

#[test]
fn suggest_ranks_exact_match_first() {
    let db = db();
    let hits = db.suggest("CEAT", 5).unwrap();
    assert!(!hits.is_empty());
    assert_eq!(hits[0].name, "CEAT");
    assert!(hits
        .iter()
        .any(|h| h.name.eq_ignore_ascii_case("ceat")));
}

#[test]
fn sectors_nav_list() {
    let db = db();
    let sectors = db.sectors().unwrap();
    assert!(sectors.len() >= 40, "got {}", sectors.len());
    assert!(sectors.iter().all(|s| matches!(
        s.entity_type.as_str(),
        "sector" | "super_sector" | "theme"
    )));
}

#[test]
fn note_read_and_entity_note() {
    let db = db();
    let note = db.entity_note("CEAT").expect("entity_note must not error");
    let note = note.expect("CEAT has a note file");
    assert!(note.markdown.len() > 100);
    assert!(note.path.starts_with("findata/"));
    assert!(!note.title.is_empty());
    // Explicit path read matches the entity lookup.
    let direct = db.read_note(&note.path).unwrap();
    assert_eq!(direct.markdown, note.markdown);
}

#[test]
fn path_traversal_rejected() {
    let db = db();
    assert!(db.read_note("../memory/.env").is_err());
    assert!(db.read_note("/etc/passwd").is_err());
    assert!(db.read_note("Makefile").is_err(), "outside findata/ and doc/");
}

#[test]
fn docs_browser_lists_tracked_docs() {
    let db = db();
    let docs = db.browse_docs(None).unwrap();
    assert!(docs.len() >= 100, "doc vault listing: {}", docs.len());
    assert!(docs.iter().all(|d| d.path.starts_with("doc/")
        && (d.path.ends_with(".md") || d.path.ends_with(".txt"))));
    assert!(docs.iter().all(|d| !d.title.is_empty()));
    assert!(
        docs.windows(2).all(|w| w[0].path <= w[1].path),
        "unfiltered listing sorted by path"
    );
    assert!(
        docs.iter()
            .any(|d| d.path == "doc/design/architecture.md"),
        "a stable tracked doc is browsable (the arc proposal churns \
         faster than the sidecar rebuilds — never pin it here)"
    );
    // A listed doc opens through the guarded reader.
    let first = docs
        .iter()
        .find(|d| d.path == "doc/design/architecture.md")
        .unwrap();
    let note = db.read_note(&first.path).unwrap();
    assert_eq!(note.path, first.path);
    // FTS filter narrows (porter-stemmed MATCH over title/sections/content).
    let filtered = db.browse_docs(Some("architecture")).unwrap();
    assert!(!filtered.is_empty() && filtered.len() < docs.len());
    assert!(filtered
        .iter()
        .any(|d| d.path == "doc/design/architecture.md"));
    // Empty filter == no filter; tokenless filter matches nothing.
    assert_eq!(db.browse_docs(Some("  ")).unwrap().len(), docs.len());
    assert!(db.browse_docs(Some("---")).unwrap().is_empty());
}

#[test]
fn docs_browser_missing_sidecar_is_hard_error() {
    let db = Db::open_at("/nonexistent-root-xyz");
    assert!(db.browse_docs(None).is_err(), "never a silent empty list");
}

#[test]
fn entity_metrics_bundle() {
    let db = db();
    let m = db.entity_metrics("CEAT").unwrap();
    // Analytics: scalar ranks parse, label groups present, payloads out.
    assert!(m.analytics.iter().any(|a| a.metric == "pagerank"));
    let pr = m
        .analytics
        .iter()
        .find(|a| a.metric == "pagerank")
        .unwrap();
    let v: serde_json::Value = serde_json::from_str(&pr.value).unwrap();
    assert!(v
        .get("value")
        .and_then(|x| x.as_f64())
        .is_some_and(|x| x.is_finite()));
    assert!(m.analytics.iter().any(|a| a.metric == "louvain_community"));
    assert!(m
        .analytics
        .iter()
        .all(|a| a.metric != "link_prediction" && a.metric != "voterank"));
    // Company: latest-first fundamentals with provenance-ready fields.
    // (Guidance rows can carry a NULL label — value/usability intact.)
    assert!(!m.company.is_empty());
    assert!(m
        .company
        .iter()
        .all(|c| !c.value_raw.is_empty()));
    assert!(m.company.iter().any(
        |c| c.label.as_deref() == Some("q_revenue") && c.period.as_deref() == Some("FY27Q1")
    ));
}

#[test]
fn metric_ranking_is_scalar_sorted() {
    let db = db();
    let rows = db.metric_values("pagerank").unwrap();
    assert!(rows.len() > 1000, "pagerank coverage: {}", rows.len());
    assert!(rows.windows(2).all(|w| w[0].value >= w[1].value));
    assert!(rows.iter().all(|r| r.value.is_finite()));
    // Case-insensitive metric names, like the Flask route.
    assert_eq!(db.metric_values("PageRank").unwrap().len(), rows.len());
    // Label + unknown metrics are hard errors, never an all-zero tint.
    assert!(db.metric_values("louvain_community").is_err());
    assert!(db.metric_values("not_a_metric").is_err());
}

#[test]
fn as_of_filters_dated_ego_edges() {
    let db = db();
    let full = db.graph_ego("CEAT", None).unwrap();
    assert!(full.as_of.is_none());
    assert!(
        full.edges.iter().any(|e| e.edge_type == "acquired"
            && (e.source == "Camso" || e.target == "Camso")),
        "Camso acquisition present unfiltered"
    );
    // Pre-2023: the Camso acquisition (2023-01-01) + 2026 index listings drop.
    let past = db.graph_ego("CEAT", Some("2022")).unwrap();
    assert_eq!(past.as_of.as_deref(), Some("2022-01-01"));
    assert!(past.edges.iter().all(|e| !(e.edge_type == "acquired"
        && (e.source == "Camso" || e.target == "Camso"))));
    assert!(
        past.edges.len() < full.edges.len(),
        "2022: {} < unfiltered {}",
        past.edges.len(),
        full.edges.len()
    );
    // Year-month agrees with year when no edges fall between the cutoffs.
    assert_eq!(
        db.graph_ego("CEAT", Some("2022-06")).unwrap().edges.len(),
        past.edges.len()
    );
    // Bad shapes are hard errors; blank == unfiltered.
    assert!(db.graph_ego("CEAT", Some("next-friday")).is_err());
    assert!(db.graph_ego("CEAT", Some("22")).is_err());
    assert_eq!(
        db.graph_ego("CEAT", Some("  ")).unwrap().edges.len(),
        full.edges.len()
    );
}

#[test]
fn entity_hyperedges_bundle() {
    let db = db();
    let hs = db.entity_hyperedges("CEAT", None).unwrap();
    assert!(hs.len() >= 10, "CEAT hyperedges: {}", hs.len());
    // Sector membership carries CEAT among its members.
    let sector = hs.iter().find(|h| h.edge_type == "sector").unwrap();
    assert_eq!(sector.label, "Automotive");
    assert!(sector.members.iter().any(|m| m.name == "CEAT"));
    // Event hyperedge records CEAT's role.
    let ev = hs.iter().find(|h| h.edge_type == "event").unwrap();
    assert_eq!(ev.role.as_deref(), Some("acquirer"));
    // Bounded, non-empty, ordered member lists.
    assert!(hs
        .iter()
        .all(|h| !h.members.is_empty() && h.members.len() <= 50));
    assert!(hs.windows(2).all(|w| (
        w[0].edge_type.clone(),
        w[0].label.clone()
    ) <= (w[1].edge_type.clone(), w[1].label.clone())));
    // as_of 2022 drops the 2023 acquisition hyperedge, nothing else dated.
    let past = db.entity_hyperedges("CEAT", Some("2022")).unwrap();
    assert!(past.len() < hs.len());
    assert!(past.iter().all(|h| h.label != "acquisition:1"));
    assert!(db.entity_hyperedges("CEAT", Some("soon")).is_err());
}

#[test]
fn similar_notes_ranking() {
    let db = db();
    let path = "findata/Companies/Agriculture/Avanti_Feeds.md";
    let sim = db.similar_notes(path, 10).unwrap();
    assert_eq!(sim.len(), 10);
    assert!(sim.iter().all(|h| h.file_path != path), "self excluded");
    assert!(sim.windows(2).all(|w| w[0].score >= w[1].score));
    assert!(sim.iter().all(|s| (-1.0..=1.0).contains(&s.score)));
    assert!(sim.iter().all(|h| !h.title.is_empty()));
    // Deterministic across calls (no sampling, full sort).
    let again = db.similar_notes(path, 10).unwrap();
    let fps = |v: &Vec<findata_core::SimilarHit>| {
        v.iter().map(|h| h.file_path.clone()).collect::<Vec<_>>()
    };
    assert_eq!(fps(&sim), fps(&again));
    // Paths outside note_search (e.g. docs) return empty, not an error.
    assert!(db
        .similar_notes("doc/design/architecture.md", 10)
        .unwrap()
        .is_empty());
}

#[test]
fn hybrid_fuses_bm25_with_cosine() {
    let db = db();
    let bm = db.search("shrimp feed", None, 20).unwrap();
    assert!(!bm.results.is_empty());
    let hy = db.search_hybrid("shrimp feed", 20).unwrap();
    assert_eq!(hy.total, bm.total);
    assert_eq!(hy.query, bm.query);
    assert_eq!(hy.results.len(), bm.results.len().min(20));
    // PRF reinforcement: the cosine leg is seeded by the BM25 top hit,
    // which ranks itself first — the fused head keeps the BM25 head.
    assert_eq!(hy.results[0].file_path, bm.results[0].file_path);
}

#[test]
fn as_of_cloud_shrinks_only_dated_edges() {
    let db = db();
    let full = db.graph_cloud(None, None).unwrap();
    assert!(full.as_of.is_none());
    let past = db.graph_cloud(None, Some("2021")).unwrap();
    assert_eq!(past.as_of.as_deref(), Some("2021-01-01"));
    assert!(
        past.total_edges < full.total_edges,
        "2021: {} < unfiltered {}",
        past.total_edges,
        full.total_edges
    );
    // Relationship summary stays unfiltered (Flask parity).
    let sum = |c: &findata_core::Cloud| {
        c.relationship_types.iter().map(|r| r.count).sum::<i64>()
    };
    assert_eq!(past.relationship_types.len(), full.relationship_types.len());
    assert_eq!(sum(&past), sum(&full));
}
