CREATE TABLE exchange_listings(isin VARCHAR, "name" VARCHAR, industry VARCHAR, asset_type VARCHAR, exchange VARCHAR, segment VARCHAR, symbol VARCHAR, kite_tradingsymbol VARCHAR, kite_token BIGINT, tick_size VARCHAR, lot_size VARCHAR, fetched_at DATE);

CREATE TABLE index_constituents(index_name VARCHAR, index_slug VARCHAR, asset_class VARCHAR DEFAULT('equity') NOT NULL, symbol VARCHAR, isin VARCHAR, company_name VARCHAR, industry VARCHAR, series VARCHAR, as_of DATE, fetched_at DATE NOT NULL, PRIMARY KEY(index_name, symbol, as_of));

CREATE TABLE mca_cin(entity_name VARCHAR, cin VARCHAR, mca_name VARCHAR, status VARCHAR, "class" VARCHAR, pba VARCHAR, state VARCHAR, via VARCHAR, query VARCHAR, fetched_at VARCHAR);

CREATE VIEW vw_equity AS SELECT * FROM exchange_listings WHERE (asset_type = 'equity');

CREATE VIEW vw_index_constituent AS SELECT * FROM index_constituents AS c WHERE (c.as_of = (SELECT max(as_of) FROM index_constituents WHERE (index_name = c.index_name)));

CREATE VIEW vw_instruments AS SELECT "name", exchange, symbol, segment, kite_token FROM exchange_listings WHERE (kite_token IS NOT NULL);

CREATE VIEW vw_sme AS SELECT * FROM exchange_listings WHERE (segment = 'sme');
