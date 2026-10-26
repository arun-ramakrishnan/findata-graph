use embed_cache::lookup_batch;
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::error::Error;
use std::io::{self, Read, Write};
use std::path::PathBuf;
use std::time::Duration;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    protocol: u8,
    db_path: PathBuf,
    model: String,
    texts: Vec<String>,
}

#[derive(Serialize)]
struct Hit {
    index: usize,
    vector: Vec<f32>,
}

#[derive(Serialize)]
struct Response {
    protocol: u8,
    hit_vectors: Vec<Hit>,
    miss_indices: Vec<usize>,
}

fn run() -> Result<(), Box<dyn Error>> {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input)?;
    let request: Request = serde_json::from_str(&input)?;
    if request.protocol != 1 {
        return Err(format!("unsupported protocol version {}", request.protocol).into());
    }
    if request.db_path.as_os_str().is_empty() {
        return Err("db_path must not be empty".into());
    }

    let conn = Connection::open_with_flags(&request.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    conn.busy_timeout(Duration::from_secs(5))?;
    let (hit_vectors, miss_indices) = lookup_batch(&conn, &request.texts, &request.model)?;
    let response = Response {
        protocol: 1,
        hit_vectors: hit_vectors
            .into_iter()
            .map(|(index, vector)| Hit { index, vector })
            .collect(),
        miss_indices,
    };

    let stdout = io::stdout();
    let mut output = stdout.lock();
    serde_json::to_writer(&mut output, &response)?;
    output.write_all(b"\n")?;
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("embed-cache-lookup: {error}");
        std::process::exit(2);
    }
}
