CREATE TABLE exchange_listings(isin VARCHAR, "name" VARCHAR, industry VARCHAR, asset_type VARCHAR, exchange VARCHAR, segment VARCHAR, symbol VARCHAR, kite_tradingsymbol VARCHAR, kite_token BIGINT, tick_size VARCHAR, lot_size VARCHAR);

CREATE TABLE mca_cin(entity_name VARCHAR, cin VARCHAR, mca_name VARCHAR, status VARCHAR, "class" VARCHAR, pba VARCHAR, state VARCHAR, via VARCHAR, query VARCHAR, fetched_at VARCHAR);

CREATE VIEW vw_equity AS SELECT * FROM exchange_listings WHERE (asset_type = 'equity');

CREATE VIEW vw_instruments AS SELECT "name", exchange, symbol, segment, kite_token FROM exchange_listings WHERE (kite_token IS NOT NULL);

CREATE VIEW vw_sme AS SELECT * FROM exchange_listings WHERE (segment = 'sme');
