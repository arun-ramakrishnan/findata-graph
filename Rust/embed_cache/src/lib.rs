//! Rust's read-only, batch lookup half of the shared embedding cache.
//!
//! Python remains responsible for cache misses, embedding, and persistence.

use rusqlite::types::ValueRef;
use rusqlite::{params, Connection, OptionalExtension};
use sha2::{Digest, Sha256};
use std::collections::HashMap;

pub fn sha256_hex(text: &[u8]) -> String {
    hex::encode(Sha256::digest(text))
}

fn decode_embedding(value: ValueRef<'_>) -> Option<Vec<f32>> {
    match value {
        ValueRef::Blob(bytes) if !bytes.is_empty() && bytes.len() % 4 == 0 => Some(
            bytes
                .chunks_exact(4)
                .map(|chunk| f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]))
                .collect(),
        ),
        ValueRef::Text(bytes) => serde_json::from_slice::<Vec<f32>>(bytes).ok(),
        _ => None,
    }
}

/// Return vectors for cache hits, paired with their input indices, and all
/// input indices whose cache entry is absent or cannot be decoded.
pub fn lookup_batch(
    conn: &Connection,
    texts: &[String],
    model: &str,
) -> rusqlite::Result<(Vec<(usize, Vec<f32>)>, Vec<usize>)> {
    let mut stmt = conn.prepare_cached(
        "SELECT embedding FROM embed_cache WHERE text_hash = ?1 AND model = ?2",
    )?;
    let mut seen: HashMap<String, Option<Vec<f32>>> = HashMap::new();
    let mut hits = Vec::new();
    let mut misses = Vec::new();

    for (index, text) in texts.iter().enumerate() {
        let hash = sha256_hex(text.as_bytes());
        if !seen.contains_key(&hash) {
            let vector = stmt
                .query_row(params![&hash, model], |row| {
                    Ok(decode_embedding(row.get_ref(0)?))
                })
                .optional()?
                .flatten();
            seen.insert(hash.clone(), vector);
        }

        match seen.get(&hash).expect("hash was inserted above") {
            Some(vector) => hits.push((index, vector.clone())),
            None => misses.push(index),
        }
    }

    Ok((hits, misses))
}

#[cfg(test)]
mod tests {
    use super::{lookup_batch, sha256_hex};
    use rusqlite::{params, Connection};

    fn packed(values: &[f32]) -> Vec<u8> {
        values.iter().flat_map(|value| value.to_le_bytes()).collect()
    }

    #[test]
    fn batch_lookup_preserves_indices_legacy_text_and_malformed_misses() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE embed_cache (
                text_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                embedding BLOB NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (text_hash, model)
            )",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO embed_cache (text_hash, model, embedding) VALUES (?1, ?2, ?3)",
            params![sha256_hex(b"hot"), "m1", packed(&[1.25, -2.0])],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO embed_cache (text_hash, model, embedding) VALUES (?1, ?2, ?3)",
            params![sha256_hex(b"legacy"), "m1", "[3.0, 4.0]"],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO embed_cache (text_hash, model, embedding) VALUES (?1, ?2, ?3)",
            params![sha256_hex(b"bad"), "m1", vec![1_u8, 2, 3]],
        )
        .unwrap();

        let texts = vec![
            "hot".to_owned(),
            "cold".to_owned(),
            "hot".to_owned(),
            "legacy".to_owned(),
            "bad".to_owned(),
        ];
        let (hits, misses) = lookup_batch(&conn, &texts, "m1").unwrap();

        assert_eq!(
            hits,
            vec![
                (0, vec![1.25, -2.0]),
                (2, vec![1.25, -2.0]),
                (3, vec![3.0, 4.0]),
            ]
        );
        assert_eq!(misses, vec![1, 4]);
    }
}
