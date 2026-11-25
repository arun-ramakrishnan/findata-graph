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
                .as_chunks::<4>()
                .0
                .iter()
                .map(|chunk| f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]))
                .collect(),
        ),
        ValueRef::Text(bytes) => serde_json::from_slice::<Vec<f32>>(bytes).ok(),
        _ => None,
    }
}

/// Hits paired with their input indices, plus the miss indices.
pub type LookupBatch = (Vec<(usize, Vec<f32>)>, Vec<usize>);

/// Return vectors for cache hits, paired with their input indices, and all
/// input indices whose cache entry is absent or cannot be decoded.
pub fn lookup_batch(
    conn: &Connection,
    texts: &[String],
    model: &str,
) -> rusqlite::Result<LookupBatch> {
    let mut stmt = conn
        .prepare_cached("SELECT embedding FROM embed_cache WHERE text_hash = ?1 AND model = ?2")?;
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
    use super::{decode_embedding, lookup_batch, sha256_hex};
    use rusqlite::types::ValueRef;
    use rusqlite::{params, Connection};

    const CACHE_DDL: &str = "CREATE TABLE embed_cache (
                text_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                embedding BLOB NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (text_hash, model)
            )";

    /// An in-memory cache shaped like the production table.
    fn store() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(CACHE_DDL).unwrap();
        conn
    }

    fn texts(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| (*item).to_owned()).collect()
    }

    fn packed(values: &[f32]) -> Vec<u8> {
        values
            .iter()
            .flat_map(|value| value.to_le_bytes())
            .collect()
    }

    fn decode(value: ValueRef<'_>) -> Option<Vec<f32>> {
        decode_embedding(value)
    }

    #[test]
    fn legacy_text_outside_f32_range_is_still_a_hit_today() {
        // Characterisation, not a requirement: serde_json reads the number as
        // f64 and casts, so a legacy row written beyond f32 range is served
        // as a hit with a non-finite (or zeroed) component. A finite guard in
        // decode_embedding would turn both into misses; flip this pin with it.
        assert_eq!(decode(ValueRef::Text(b"[1e40]")), Some(vec![f32::INFINITY]));
        assert_eq!(decode(ValueRef::Text(b"[1e-50]")), Some(vec![0.0]));
        // "NaN" is not JSON: a miss
        assert_eq!(decode(ValueRef::Text(b"[NaN]")), None);
    }

    #[test]
    fn batch_lookup_preserves_indices_legacy_text_and_malformed_misses() {
        let conn = store();
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

    // ---- decode boundary (coverage sweep S2, 2026-11-24) -------------------
    // The Python codec (`helpers.core.vec_codec.load_vec`) decodes an empty
    // blob to a zero-dimensional vector; this side treats it as a miss, so an
    // empty row can never be served as a dimension-less hit. Each boundary
    // gets its own case: before the sweep the only malformed-input coverage
    // was the 3-byte row inside the lookup test above.

    #[test]
    fn empty_blob_is_a_miss_not_a_zero_dimensional_hit() {
        assert_eq!(decode(ValueRef::Blob(&[])), None);
    }

    #[test]
    fn blob_length_not_a_multiple_of_four_is_a_miss() {
        for len in [1_usize, 2, 3, 5, 6, 7, 9] {
            let bytes = vec![0_u8; len];
            assert_eq!(decode(ValueRef::Blob(&bytes)), None, "blob length {len}");
        }
    }

    #[test]
    fn packed_blob_is_little_endian_f32_and_round_trips_exactly() {
        let values = [1.25_f32, -2.0, 0.5, f32::MIN_POSITIVE, f32::MAX];
        assert_eq!(
            decode(ValueRef::Blob(&packed(&values))),
            Some(values.to_vec())
        );
        // byte order pinned by hand-packed words, not only by to_le_bytes
        assert_eq!(
            decode(ValueRef::Blob(&[0x00, 0x00, 0x80, 0x3F])),
            Some(vec![1.0])
        );
        assert_eq!(
            decode(ValueRef::Blob(&[0x00, 0x00, 0x00, 0xC0])),
            Some(vec![-2.0])
        );
        // one word per float: a 4-byte blob is a one-dimensional hit
        assert_eq!(
            decode(ValueRef::Blob(&[0x00, 0x00, 0x80, 0x3F])).map(|vector| vector.len()),
            Some(1)
        );
    }

    #[test]
    fn legacy_text_json_list_is_accepted() {
        assert_eq!(decode(ValueRef::Text(b"[3.0, 4.0]")), Some(vec![3.0, 4.0]));
        assert_eq!(
            decode(ValueRef::Text(b"[1, -2, 1e2]")),
            Some(vec![1.0, -2.0, 100.0])
        );
        assert_eq!(decode(ValueRef::Text(b"[]")), Some(vec![]));
        assert_eq!(
            decode(ValueRef::Text(b"  [0.5]\n")),
            Some(vec![0.5]),
            "surrounding whitespace is still JSON"
        );
    }

    #[test]
    fn legacy_text_that_is_not_a_json_list_of_numbers_is_a_miss() {
        let cases: &[&[u8]] = &[
            b"[3.0, 4.0",
            b"[3.0, 4.0]]",
            b"{\"a\": 1}",
            b"[1.0, \"x\"]",
            b"[[1.0]]",
            b"\"[1.0]\"",
            b"null",
            b"3.0",
            b"",
            b"[1.0, 2.0] trailing",
        ];
        for text in cases {
            assert_eq!(decode(ValueRef::Text(text)), None, "{text:?}");
        }
    }

    #[test]
    fn sql_values_that_are_neither_blob_nor_text_are_misses() {
        assert_eq!(decode(ValueRef::Null), None);
        assert_eq!(decode(ValueRef::Integer(4)), None);
        assert_eq!(decode(ValueRef::Real(1.5)), None);
    }

    // ---- lookup boundary --------------------------------------------------

    #[test]
    fn lookup_is_scoped_to_the_model() {
        let conn = store();
        for model in ["m1", "m2"] {
            conn.execute(
                "INSERT INTO embed_cache (text_hash, model, embedding) VALUES (?1, ?2, ?3)",
                params![sha256_hex(b"shared"), model, packed(&[0.25])],
            )
            .unwrap();
        }

        let (hits, misses) = lookup_batch(&conn, &texts(&["shared"]), "m1").unwrap();
        assert_eq!((hits, misses), (vec![(0, vec![0.25])], vec![]));
        let (hits, misses) = lookup_batch(&conn, &texts(&["shared"]), "m2").unwrap();
        assert_eq!((hits, misses), (vec![(0, vec![0.25])], vec![]));
        // a model with no row for the text: a miss, never another model's vector
        let (hits, misses) = lookup_batch(&conn, &texts(&["shared"]), "m3").unwrap();
        assert_eq!((hits, misses), (vec![], vec![0]));
    }

    #[test]
    fn every_duplicate_index_is_answered_and_an_undecodable_row_is_a_miss() {
        let conn = store();
        conn.execute(
            "INSERT INTO embed_cache (text_hash, model, embedding) VALUES (?1, ?2, ?3)",
            params![sha256_hex(b"hot"), "m1", packed(&[1.0])],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO embed_cache (text_hash, model, embedding) VALUES (?1, ?2, ?3)",
            params![sha256_hex(b"empty"), "m1", vec![0_u8; 0]],
        )
        .unwrap();

        let batch = texts(&["hot", "cold", "hot", "empty", "cold", "hot", "empty"]);
        let (hits, misses) = lookup_batch(&conn, &batch, "m1").unwrap();
        assert_eq!(hits, vec![(0, vec![1.0]), (2, vec![1.0]), (5, vec![1.0])]);
        assert_eq!(misses, vec![1, 3, 4, 6]);
    }

    #[test]
    fn an_empty_batch_returns_no_hits_and_no_misses() {
        let (hits, misses) = lookup_batch(&store(), &[], "m1").unwrap();
        assert!(hits.is_empty());
        assert!(misses.is_empty());
    }
}
