CREATE TABLE e_acquired(acquirer_name BIGINT, target_name BIGINT, weight VARCHAR, properties VARCHAR, source_ref VARCHAR, valid_from VARCHAR, valid_to VARCHAR, "year" VARCHAR);

CREATE TABLE e_belongs(company_name BIGINT, sector_name BIGINT, weight VARCHAR, properties VARCHAR, source_ref VARCHAR, valid_from VARCHAR, valid_to VARCHAR);

CREATE TABLE e_belongs_to(child_id BIGINT, parent_id BIGINT, weight VARCHAR, properties VARCHAR, source_ref VARCHAR, valid_from VARCHAR, valid_to VARCHAR);

CREATE TABLE e_cited_in(company_id BIGINT, edition_id BIGINT, weight VARCHAR, properties VARCHAR, source_ref VARCHAR, valid_from VARCHAR, valid_to VARCHAR);

CREATE TABLE e_comention(a_name BIGINT, b_name BIGINT, weight VARCHAR, properties VARCHAR, source_ref VARCHAR, valid_from VARCHAR, valid_to VARCHAR);

CREATE TABLE e_competes(a_name BIGINT, b_name BIGINT, weight VARCHAR, properties VARCHAR, source_ref VARCHAR, valid_from VARCHAR, valid_to VARCHAR);

CREATE TABLE e_customer(customer_name BIGINT, supplier_name BIGINT, weight VARCHAR, properties VARCHAR, source_ref VARCHAR, valid_from VARCHAR, valid_to VARCHAR);

CREATE TABLE e_exposed_to(company_id BIGINT, theme_id BIGINT, weight VARCHAR, properties VARCHAR, source_ref VARCHAR, valid_from VARCHAR, valid_to VARCHAR);

CREATE TABLE e_group(a_name BIGINT, b_name BIGINT, weight VARCHAR, properties VARCHAR, source_ref VARCHAR, valid_from VARCHAR, valid_to VARCHAR);

CREATE TABLE e_has(sector_name BIGINT, company_name BIGINT, weight VARCHAR, properties VARCHAR, source_ref VARCHAR, valid_from VARCHAR, valid_to VARCHAR);

CREATE TABLE e_jv(a_name BIGINT, b_name BIGINT, weight VARCHAR, properties VARCHAR, source_ref VARCHAR, valid_from VARCHAR, valid_to VARCHAR);

CREATE TABLE e_subsidiary(subsidiary_name BIGINT, parent_name BIGINT, weight VARCHAR, properties VARCHAR, source_ref VARCHAR, valid_from VARCHAR, valid_to VARCHAR);

CREATE TABLE e_supplier(supplier_name BIGINT, customer_name BIGINT, weight VARCHAR, properties VARCHAR, source_ref VARCHAR, valid_from VARCHAR, valid_to VARCHAR);

CREATE TABLE v_company(id BIGINT, "name" VARCHAR, sector_classification VARCHAR, market_cap VARCHAR, ticker VARCHAR);

CREATE TABLE v_edition(id BIGINT, "name" VARCHAR);

CREATE TABLE v_embeddings(company_name VARCHAR, id BIGINT, embedding FLOAT[]);

CREATE TABLE v_node(id BIGINT, "name" VARCHAR, kind VARCHAR, sector_classification VARCHAR, market_cap VARCHAR, ticker VARCHAR);

CREATE TABLE v_sector(id BIGINT, "name" VARCHAR);

CREATE TABLE v_sub_sector(id BIGINT, "name" VARCHAR);

CREATE TABLE v_super_sector(id BIGINT, "name" VARCHAR);

CREATE TABLE v_theme(id BIGINT, "name" VARCHAR);

CREATE TABLE _build_meta("key" VARCHAR PRIMARY KEY, "value" VARCHAR NOT NULL);
