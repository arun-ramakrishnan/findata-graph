CREATE TABLE note_search(id BIGINT PRIMARY KEY, doc_type VARCHAR, file_path VARCHAR, title VARCHAR, sector VARCHAR, "content" VARCHAR, embedding BLOB, section_title VARCHAR, anchor VARCHAR, ticker VARCHAR, edge_context VARCHAR, searchtext VARCHAR);

CREATE TABLE note_search_info("key" VARCHAR PRIMARY KEY, "value" VARCHAR);

CREATE TABLE note_search_meta(file_path VARCHAR PRIMARY KEY, mtime DOUBLE, content_hash VARCHAR);
