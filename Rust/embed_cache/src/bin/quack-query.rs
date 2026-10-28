use duckdb::{types::Value, Connection};
use serde::{Deserialize, Serialize};
use std::error::Error;
use std::io::{self, Read};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    protocol: u8,
    #[serde(default)]
    uri: String,
    #[serde(default)]
    token: String,
    #[serde(default)]
    db_path: String,
    sql: String,
    params: Vec<serde_json::Value>,
}

#[derive(Serialize)]
struct ColumnInfo {
    name: String,
    type_name: String,
}

#[derive(Serialize)]
struct Response {
    protocol: u8,
    columns: Vec<ColumnInfo>,
    rows: Vec<Vec<serde_json::Value>>,
    row_count: usize,
}

fn sql_lit(v: &serde_json::Value) -> String {
    match v {
        serde_json::Value::Null => "NULL".to_string(),
        serde_json::Value::Bool(b) => if *b { "TRUE" } else { "FALSE" }.to_string(),
        serde_json::Value::Number(n) => n.to_string(),
        serde_json::Value::String(s) => {
            let mut out = String::with_capacity(s.len() + 2);
            out.push('\'');
            for ch in s.chars() {
                if ch == '\'' {
                    out.push('\'');
                    out.push('\'');
                } else {
                    out.push(ch);
                }
            }
            out.push('\'');
            out
        }
        _ => panic!("unsupported param type for inlining: {:?}", v),
    }
}

fn inline_params(sql: &str, params: &[serde_json::Value]) -> String {
    let mut out = String::with_capacity(sql.len() + params.len() * 8);
    let mut remaining = sql;
    let mut param_idx = 1;

    while let Some(pos) = remaining.find('?') {
        out.push_str(&remaining[..pos]);
        if param_idx <= params.len() {
            out.push_str(&sql_lit(&params[param_idx - 1]));
        } else {
            out.push('?');
        }
        remaining = &remaining[pos + 1..];
        param_idx += 1;
    }
    out.push_str(remaining);
    out
}

fn duckdb_to_json(v: &Value) -> Result<serde_json::Value, Box<dyn Error>> {
    use duckdb::types::Value;
    Ok(match v {
        Value::Null => serde_json::Value::Null,
        Value::Boolean(b) => serde_json::Value::Bool(*b),
        Value::TinyInt(i) => serde_json::Value::Number((*i as i64).into()),
        Value::SmallInt(i) => serde_json::Value::Number((*i as i64).into()),
        Value::Int(i) => serde_json::Value::Number((*i as i64).into()),
        Value::BigInt(i) => serde_json::Value::Number((*i).into()),
        Value::HugeInt(i) => serde_json::Value::String(i.to_string()),
        Value::Float(f) => {
            serde_json::Value::Number(serde_json::Number::from_f64((*f).into()).unwrap())
        }
        Value::Double(f) => serde_json::Value::Number(serde_json::Number::from_f64(*f).unwrap()),
        Value::Text(s) => serde_json::Value::String(s.clone()),
        Value::Blob(b) => {
            let hex = b.iter().map(|x| format!("{:02x}", x)).collect::<String>();
            serde_json::Value::String(format!("\\x{}", hex))
        }
        _ => return Err("unsupported column type".into()),
    })
}

fn run() -> Result<(), Box<dyn Error>> {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input)?;
    let request: Request = serde_json::from_str(&input)?;

    if request.protocol != 1 {
        return Err(format!("unsupported protocol version {}", request.protocol).into());
    }

    let conn = if !request.db_path.is_empty() {
        // Local file mode
        Connection::open(&request.db_path)?
    } else {
        // Quack wire mode
        let conn = Connection::open_in_memory()?;
        let secret_sql = format!(
            "CREATE SECRET (TYPE quack, TOKEN '{}', SCOPE '{}')",
            request.token.replace('\'', "''"),
            request.uri
        );
        conn.execute(&secret_sql, [])?;
        let attach_sql = format!("ATTACH '{}' AS g (TYPE quack)", request.uri);
        conn.execute(&attach_sql, [])?;
        conn
    };

    let inlined_sql = inline_params(&request.sql, &request.params);
    let mut stmt = conn.prepare(&inlined_sql)?;
    stmt.execute([])?;

    let column_names = stmt.column_names();
    let column_count = stmt.column_count();
    let mut columns = Vec::new();
    for i in 0..column_count {
        let name = column_names
            .get(i)
            .cloned()
            .unwrap_or_else(|| format!("col_{}", i));
        let logical_type = stmt.column_logical_type(i);
        columns.push(ColumnInfo {
            name,
            type_name: format!("{:?}", logical_type.id()),
        });
    }

    let mut rows = Vec::new();
    let mut row_iter = stmt.query([])?;
    while let Some(row) = row_iter.next()? {
        let mut row_vals = Vec::new();
        for i in 0..column_count {
            let val: Value = row.get(i)?;
            row_vals.push(duckdb_to_json(&val)?);
        }
        rows.push(row_vals);
    }

    let response = Response {
        protocol: 1,
        columns,
        row_count: rows.len(),
        rows,
    };

    println!("{}", serde_json::to_string(&response)?);
    Ok(())
}

fn main() {
    if let Err(e) = run() {
        eprintln!("{}", e);
        std::process::exit(1);
    }
}
