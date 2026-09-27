//! Tauri entry point — thin command layer over `findata-core`.
//!
//! Every command maps 1:1 to a data-layer call; errors stringify to the
//! frontend as rejected promises. No state: `Db` resolves the repo root and
//! opens a fresh read-only connection per call (WAL readers never block the
//! live writers, and a SQLite writer swap can't leave us holding a stale
//! handle).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use findata_core::{Cloud, Db, DocEntry, Ego, EntityMetrics, HyperEdge, NoteContent, RankEntry, SearchResults, SimilarHit, SuggestHit, Stats};

fn db() -> Result<Db, String> {
    Db::open().map_err(|e| e.to_string())
}

#[tauri::command]
fn stats() -> Result<Stats, String> {
    db()?.stats().map_err(|e| e.to_string())
}

#[tauri::command]
fn graph_cloud(edge_type: Option<String>, as_of: Option<String>) -> Result<Cloud, String> {
    db()?
        .graph_cloud(
            edge_type.as_deref().filter(|s| !s.is_empty()),
            as_of.as_deref(),
        )
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn graph_ego(name: String, as_of: Option<String>) -> Result<Ego, String> {
    db()?.graph_ego(&name, as_of.as_deref()).map_err(|e| e.to_string())
}

#[tauri::command]
fn search_notes(q: String, doc_type: Option<String>, limit: Option<usize>) -> Result<SearchResults, String> {
    db()?
        .search(&q, doc_type.as_deref(), limit.unwrap_or(20).clamp(1, 50))
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn suggest(q: String, limit: Option<usize>) -> Result<Vec<SuggestHit>, String> {
    db()?
        .suggest(&q, limit.unwrap_or(10).clamp(1, 25))
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn sectors() -> Result<Vec<SuggestHit>, String> {
    db()?.sectors().map_err(|e| e.to_string())
}

#[tauri::command]
fn read_note(path: String) -> Result<NoteContent, String> {
    db()?.read_note(&path).map_err(|e| e.to_string())
}

#[tauri::command]
fn entity_note(name: String) -> Result<Option<NoteContent>, String> {
    db()?.entity_note(&name).map_err(|e| e.to_string())
}

#[tauri::command]
fn browse_docs(filter: Option<String>) -> Result<Vec<DocEntry>, String> {
    db()?
        .browse_docs(filter.as_deref())
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn entity_metrics(name: String) -> Result<EntityMetrics, String> {
    db()?.entity_metrics(&name).map_err(|e| e.to_string())
}

#[tauri::command]
fn metric_values(metric: String) -> Result<Vec<RankEntry>, String> {
    db()?.metric_values(&metric).map_err(|e| e.to_string())
}

#[tauri::command]
fn entity_hyperedges(name: String, as_of: Option<String>) -> Result<Vec<HyperEdge>, String> {
    db()?
        .entity_hyperedges(&name, as_of.as_deref())
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn similar_notes(path: String, limit: Option<usize>) -> Result<Vec<SimilarHit>, String> {
    db()?
        .similar_notes(&path, limit.unwrap_or(10).clamp(1, 25))
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn search_hybrid(q: String, limit: Option<usize>) -> Result<SearchResults, String> {
    db()?
        .search_hybrid(&q, limit.unwrap_or(20).clamp(1, 50))
        .map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            stats,
            graph_cloud,
            graph_ego,
            search_notes,
            suggest,
            sectors,
            read_note,
            entity_note,
            browse_docs,
            entity_metrics,
            metric_values,
            entity_hyperedges,
            similar_notes,
            search_hybrid,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
