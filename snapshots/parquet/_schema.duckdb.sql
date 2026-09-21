CREATE TABLE e_acquired(acquirer_name BIGINT, target_name BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE, "year" VARCHAR);

CREATE TABLE e_all_und(a_id BIGINT, b_id BIGINT, edge_type VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_belongs(company_name BIGINT, sector_name BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_belongs_to(child_id BIGINT, parent_id BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_cited_in(company_id BIGINT, edition_id BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_comention(a_name BIGINT, b_name BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_competes(a_name BIGINT, b_name BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_customer(customer_name BIGINT, supplier_name BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_dir(a_id BIGINT, b_id BIGINT, edge_type VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_exposed_to(company_id BIGINT, theme_id BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_group(a_name BIGINT, b_name BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_has(sector_name BIGINT, company_name BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_invested(institution_name BIGINT, company_name BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_jv(a_name BIGINT, b_name BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_listed_in(company_id BIGINT, country_id BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_listed_on_index(company_id BIGINT, index_id BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_semantic_peer(a_name BIGINT, b_name BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_subsidiary(subsidiary_name BIGINT, parent_name BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE e_supplier(supplier_name BIGINT, customer_name BIGINT, weight DOUBLE, properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE h_edge(id BIGINT, edge_type VARCHAR, "label" VARCHAR, weight DOUBLE, valid_from DATE, valid_to DATE, source_ref VARCHAR);

CREATE TABLE h_incidence(edge_id BIGINT, entity_name VARCHAR, weight DOUBLE, direction VARCHAR, "role" VARCHAR, valid_from DATE, valid_to DATE);

CREATE TABLE v_centrality_betweenness("name" VARCHAR, score DOUBLE);

CREATE TABLE v_centrality_closeness("name" VARCHAR, score DOUBLE);

CREATE TABLE v_centrality_degree("name" VARCHAR, score DOUBLE);

CREATE TABLE v_centrality_eigenvector("name" VARCHAR, score DOUBLE);

CREATE TABLE v_centrality_harmonic("name" VARCHAR, score DOUBLE);

CREATE TABLE v_centrality_katz("name" VARCHAR, score DOUBLE);

CREATE TABLE v_centrality_laplacian("name" VARCHAR, score DOUBLE);

CREATE TABLE v_centrality_local_reaching("name" VARCHAR, score DOUBLE);

CREATE TABLE v_centrality_louvain("name" VARCHAR, community_id BIGINT);

CREATE TABLE v_centrality_voterank("name" VARCHAR, score DOUBLE);

CREATE TABLE v_company(id BIGINT, "name" VARCHAR, sector_classification VARCHAR, market_cap VARCHAR, ticker VARCHAR);

CREATE TABLE v_country(id BIGINT, "name" VARCHAR);

CREATE TABLE v_edition(id BIGINT, "name" VARCHAR);

CREATE TABLE v_embeddings(company_name VARCHAR, id BIGINT, embedding FLOAT[384]);

CREATE TABLE v_index(id BIGINT, "name" VARCHAR);

CREATE TABLE v_institution(id BIGINT, "name" VARCHAR);

CREATE TABLE v_node(id BIGINT, "name" VARCHAR, kind VARCHAR, sector_classification VARCHAR, market_cap VARCHAR, ticker VARCHAR);

CREATE TABLE v_note_embeddings(file_path VARCHAR, doc_type VARCHAR, title VARCHAR, emb FLOAT[384]);

CREATE TABLE v_sector(id BIGINT, "name" VARCHAR);

CREATE TABLE v_sub_sector(id BIGINT, "name" VARCHAR);

CREATE TABLE v_super_sector(id BIGINT, "name" VARCHAR);

CREATE TABLE v_theme(id BIGINT, "name" VARCHAR);

CREATE TABLE _build_meta("key" VARCHAR PRIMARY KEY, "value" VARCHAR NOT NULL);
