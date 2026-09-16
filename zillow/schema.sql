PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS captures (
 id INTEGER PRIMARY KEY, requested_url TEXT NOT NULL, final_url TEXT NOT NULL,
 captured_at TEXT NOT NULL, http_status INTEGER, title TEXT, canonical_url TEXT,
 sha256 TEXT NOT NULL, html TEXT NOT NULL, text TEXT NOT NULL,
 metadata_json TEXT NOT NULL, structured_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS homes (
 zpid TEXT PRIMARY KEY, url TEXT NOT NULL, address TEXT, city TEXT, state TEXT,
 postal_code TEXT, latitude REAL, longitude REAL, home_type TEXT,
 bedrooms REAL, bathrooms REAL, living_area REAL, lot_area REAL, year_built INTEGER, lot_area_units TEXT
);
CREATE TABLE IF NOT EXISTS observations (
 capture_id INTEGER NOT NULL REFERENCES captures(id),
 zpid TEXT NOT NULL REFERENCES homes(zpid), zestimate REAL, rent_zestimate REAL,
 price REAL, home_status TEXT, zestimate_status TEXT NOT NULL,
 facts_json TEXT NOT NULL, rendered_bathrooms REAL, bathrooms_source TEXT,
 bathrooms_conflict INTEGER, PRIMARY KEY(capture_id,zpid)
);
CREATE VIEW IF NOT EXISTS latest_homes AS
 SELECT h.*, c.captured_at, o.zestimate, o.rent_zestimate, o.price,
 o.home_status, o.zestimate_status, o.rendered_bathrooms, o.bathrooms_source,
 o.bathrooms_conflict FROM homes h JOIN observations o USING(zpid)
 JOIN captures c ON c.id=o.capture_id
 WHERE o.capture_id=(SELECT MAX(x.capture_id) FROM observations x WHERE x.zpid=h.zpid);

CREATE TABLE IF NOT EXISTS crawl_errors (
 id INTEGER PRIMARY KEY, url TEXT NOT NULL, occurred_at TEXT NOT NULL,
 attempt INTEGER NOT NULL, terminal INTEGER NOT NULL, error TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_runs (
 id INTEGER PRIMARY KEY, source_url TEXT NOT NULL, fetched_at TEXT NOT NULL,
 feature_count INTEGER NOT NULL, snapshot_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inventory_addresses (
 address_key TEXT PRIMARY KEY, address TEXT NOT NULL, lookup_url TEXT,
 status TEXT NOT NULL DEFAULT 'pending', zpid TEXT, matched_url TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS inventory_parcels (
 object_id INTEGER PRIMARY KEY, apn TEXT, address_key TEXT,
 use_code TEXT, units TEXT, classification TEXT NOT NULL,
 run_id INTEGER NOT NULL REFERENCES inventory_runs(id), feature_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skipped_urls (
 url TEXT PRIMARY KEY, reason TEXT NOT NULL, captured_at TEXT NOT NULL
);
