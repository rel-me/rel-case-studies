# Zillow research from Santa Cruz's official parcel inventory

The city GIS provides the address inventory. `rel-crawlee` performs Zillow
lookups through one persistent REL session. SQLite stores the official source
records, lookup outcomes, rendered captures, and property observations.

## Run

Install `/Applications/REL.app` and configure the **OxylabsDatacenter** Profile:

```sh
./zillow/run.sh
./zillow/run.sh --max-addresses 10000 --max-pages 20000
```

The first run imports GIS data if no inventory exists. The launcher checks
Release health and prevents overlapping crawls. It creates a session once,
saves its ID in `output/session.json`, then reuses it across requests, retries,
and restarts. Requests are serial; the session stays open afterward. Missing
saved sessions fail explicitly instead of silently replacing browser state.

Default limits are 50 verified home details and a 500-request safety limit,
including address searches. Navigation allows 60 seconds. Transient browser
failures wait five seconds and retry once before the next URL. The navigation
timeout and retry delay can be set with `--navigation-timeout` and `--retry-delay`.
Exhausted failures and access challenges stop the run. Out-of-area properties
and mismatched addresses are recorded without stopping the crawl.

Run the script from any directory; paths resolve relative to the case study.
Pass crawler options directly, without an `ARGS` variable. `./zillow/run.sh setup`
installs dependencies without launching REL. `./zillow/run.sh --help` shows usage.

## Inventory

```sh
./zillow/run.sh inventory
```

The importer queries the city's public ArcGIS parcel API directly. This is the
primary official-data source; all Zillow navigation remains in REL. It requests
only parcel/address/use fields and geometry, not owner names or mailing addresses.
It snapshots object IDs first, downloads batches, and rejects truncation,
duplicate IDs, or missing features before committing the import.

The September 15, 2026 snapshot contains:

- 17,314 parcels flagged `SCCityLMT=Yes` by the city.
- 14,748 parcels in the explicitly selected residential-use categories.
- 14,747 distinct normalized residential address strings.
- 14,730 addresses with a supported city-supplied Zillow lookup link.
- 17 addresses needing link review; 839 city parcels lack an address, and 1,727
  have other or unknown uses. Those parcel records are retained for review.

This is a **parcel-address inventory, not a verified dwelling-unit inventory**.
Apartment buildings, ADUs, and mobile-home parks can contain multiple homes at
one address. Unit identifiers and number fractions are preserved when present.
Refresh replaces the current parcel snapshot while preserving lookup outcomes;
addresses no longer in the selected snapshot are excluded from the active count.

[Official GIS layer](https://vwgisportal2.santacruzca.gov/arcgis/rest/services/search/MapServer/1)

## Matching and coverage

The named `zillow-inventory` Crawlee queue uses the official Zillow search link
for each address. It does not continue the earlier search-pagination queue.
When Zillow redirects to a home, its street address including unit must match the
GIS address, and its city/state must be Santa Cruz, CA. A search result must have
one exact address candidate before a detail lookup is queued. The detail record
must match its final ZPID. Missing estimates remain NULL.

Statuses are `pending`, `resolving`, `matched`, `not_found`, `ambiguous`,
`skipped`, `failed`, and `needs_review`. `not_found` means no exact candidate
appeared in the captured response, not proof that Zillow has no such property.
The final JSON reports these counts against the active official inventory.
Municipal inclusion comes from the GIS city-limit flag; Zillow matching uses
physical street/unit and city/state, not owner mailing fields. Geometry is
retained for later spatial review, not currently used to validate Zillow points.

Terminal requests remain handled in Crawlee. An ordinary restart resumes
pending requests; it does not automatically retry terminal failures. To explicitly
retry failed timeout, cancellation, or network-interruption lookups first:

```sh
./zillow/run.sh --retry-failed --max-addresses 10000 --max-pages 20000
```

Recovery requests get stable keys tied to the recorded failure and a fresh bounded
attempt budget. Repeating this option after interruption deduplicates the same
recovery batch. Completed addresses, access challenges, missing sessions, and other
nontransient failures are excluded. Current-run `run.errors` and statistics are
separate from retained `error_history`. The stop message reports the actual URL
and error; a navigation cancellation is not reported as an access block.

## Files and tests

- `output/inventory/addresses.csv`: active address roster with parcel IDs and reported unit counts.
- `output/inventory/*.json`: complete selected-field GIS snapshots with geometry.
- `output/zillow.sqlite3`: inventory parcels/addresses/runs, homes, observations,
  captures, skipped URLs, and errors.
- `output/crawlee`: persistent request queues.
- `inventory.py`: official data import, normalization, and coverage summary.
- `records.py`: Zillow extraction and storage.
- `zillow.py`: lookup matching and RelCrawler handlers.

```sh
cd zillow
.venv/bin/python -m unittest -v
```

See the [REL Crawlee guide](https://github.com/rel-me/rel-tools/blob/main/docs/CRAWLEE.md)
for the supported browser integration. Local data is ignored by Git.

## Data quality and migration

`price` is Zillow's advertised price for the recorded `home_status`, which may
be a sale, sold, rental, or off-market value. It is not necessarily an asking
price. Zero or explicitly unknown prices are NULL. Original source values stay
in `observations.facts_json`.

`lot_area` must be read with `lot_area_units` (for example, Acres or Square Feet).
`bathrooms` retains the structured property's count, including half baths.
Observations and `latest_homes` also expose `rendered_bathrooms`,
`bathrooms_source`, and `bathrooms_conflict`. A conflict of 1 means the rendered
headline disagrees; NULL means comparison was unavailable. The original HTML
and rendered text remain available for review.

After an existing crawler stops, migrate saved data without starting a crawl:

```sh
./zillow/run.sh migrate
```

This command refuses to run while the crawler owns `output/run.lock`. It creates
a complete SQLite backup alongside the database, backfills units and bathroom
evidence from saved captures, renames `asking_price` to `price`, and normalizes
unknown prices. Changes are transactional and rerunning is safe. `run.sh`
also applies the migration under the same lock before starting its next crawl.
A crawler already running keeps its loaded code until it exits. Its session,
request queue, captures, and inventory outcomes are preserved by the migration.
