# Zillow inventory crawler (legacy workflow)

The city GIS provides the address inventory. `rel-crawlee` performs Zillow
lookups through one persistent REL session. SQLite stores the official source
records, lookup outcomes, rendered captures, and property observations.

> [!WARNING]
> **READ BEFORE RUNNING: WEBSITE TERMS AND LEGAL COMPLIANCE**
>
> Crawling, scraping, or automated access may violate a website's Terms of
> Service. Before using this case study, review and comply with each source
> website's current terms, access policies, and all applicable laws and
> regulations, including privacy, data protection, and intellectual property
> requirements. Obtain any required permission before collecting or reusing data.
>
> Respect access restrictions, rate limits, and requests to stop. Do not use
> proxies to bypass restrictions or access data without authorization. Publicly
> accessible data does not automatically grant permission to collect, store, or
> redistribute it. You are responsible for ensuring your use is permitted;
> this example does not grant permission or constitute legal advice.

## Run

Install `/Applications/REL.app` and configure a REL Profile of your choice.
The crawler can use any proxy configured in that Profile, or no proxy.
This case study uses an **Oxylabs ISP proxy**; that provider is not required.
Replace `YourProfile` below with your configured Profile name:

```sh
./zillow/run.sh --profile "YourProfile"
./zillow/run.sh --profile "YourProfile" --max-addresses 10000 --max-pages 20000
```

The first run imports GIS data if no inventory exists. The launcher checks
Release health and prevents overlapping crawls. It saves the managed session's
ID in `output/session.json` and reuses it across requests, retries and restarts.
Requests are serial; the active session stays open afterward. Bounded recovery
can replace a failed managed session while keeping the queue and captures.
On restarts, pass the same Profile name used to create the saved session.
To use a different Profile, choose a separate `--output` directory for a new crawl.

Default limits are 50 verified home details and a 500-request safety limit,
including address searches. Navigation allows 60 seconds. Transient browser
failures wait five seconds and retry once before the next URL. The navigation
timeout and retry delay can be set with `--navigation-timeout` and `--retry-delay`.
Exhausted transient timeouts, cancellations, and network interruptions are
recorded as failed lookups, then the crawl moves to the next address. Failed
lookups stay handled until explicitly requeued with `--retry-failed`.
Explicit human-verification challenges and unrelated nontransient failures stop
the run. Out-of-area properties
and mismatched addresses are recorded without stopping the crawl.

Target HTTP 403 and 429 responses are treated as access restrictions: the crawl
stops and does not retry those URLs or rotate a proxy. Target HTTP 502, 503 and
504 can trigger the configurable bounded session recovery. Recovery waits
30 seconds, creates a replacement browser session with the same Profile and
proxy settings, and retries that URL once; it does not rotate the proxy. Three
consecutive failures or ten recoveries in one process stop recovery.

Use `--rotation-rules path/to/rules.json` to select another policy. To explicitly
replace the current managed session before continuing and retry eligible failed
lookups with fresh bounded budgets:

```sh
./zillow/run.sh --profile "YourProfile" --rotate-session --retry-failed --max-addresses 10000 --max-pages 20000
```

Pending replacement and retry state survives interruption.
`output/session-rotations.jsonl` records session IDs, proxy alias and confirms
that no sticky-session rotation occurred.

The Zillow crawler holds `output/zillow-run.lock`; the county tax and county
inventory crawlers use `output/run.lock`. This permits those two crawls to run
at once while preventing duplicate Zillow workers and duplicate county workers.
Both use separate queues and write separate tables in the shared SQLite file.

The worker stays serial and capped at 20 requests per minute. Unrelated host CPU
load does not pause scheduling, because browser work runs in REL's process;
memory and event-loop overload checks remain enabled.

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

## Dashboard

```sh
./zillow/run.sh dashboard
```

Open [the local dashboard](http://127.0.0.1:8787) to see totals, inventory progress,
searchable properties, recorded values and recent crawl errors. Click an address
for more details and its Zillow link. The dashboard refreshes every 15 seconds
and reads the existing SQLite database without changing it; the crawl can keep
running. It uses Python's standard library with no extra installation.
Use `--port 8888` or `--database /path/to/zillow.sqlite3` to select another port
or database. Missing values remain unavailable; advertised prices retain their
sale/rental context and differing bathroom counts are identified in details.

HTTP 404 lookups are recorded as skipped and the next address proceeds. Three
consecutive missing pages replace the managed session before continuing, without
retrying those skipped URLs. The threshold is
`rotate_after_consecutive_skips` in `rotation-rules.json`; a successful capture
resets the streak. The existing rotation limits still apply.

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
retry failed timeout, cancellation, network-interruption, or qualifying HTTP
lookups first:

```sh
./zillow/run.sh --profile "YourProfile" --retry-failed --max-addresses 10000 --max-pages 20000
```

Recovery requests get stable keys tied to the recorded failure and a fresh bounded
attempt budget. Repeating this option after interruption deduplicates the same
recovery batch. Completed addresses, explicit human-verification challenges,
missing sessions, and nonqualifying failures are excluded. Current-run `run.errors` and statistics are
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

This command refuses to run while either crawler owns its lock. It creates a
complete SQLite backup alongside the database, backfills units and bathroom
evidence from saved captures, renames `asking_price` to `price`, and normalizes
unknown prices. Changes are transactional and rerunning is safe. `run.sh`
also applies the migration under the same lock before starting its next crawl.
A crawler already running keeps its loaded code until it exits. Its session,
request queue, captures, and inventory outcomes are preserved by the migration.

### County inventory and continued collection

Pass `--county-inventory /path/to/county-inventory.sqlite3 --skip-failed
--max-consecutive-failures 10` through `run.py` to add completed county GIS
residential targets without resetting existing lookup outcomes. New county keys
include the parcel identifier so same-street addresses in different towns remain
separate. Matches require the recorded street/unit, California, and the county
postal code. Dougmar addresses are queued first. Missing postal codes remain for
review rather than producing speculative matches.

In this mode exhausted resources are terminal and collection continues. A
successful resource resets the persisted streak; the eleventh consecutive failed
resource stops the worker. `zillow-progress.json` retains the streak and stop
reason across restarts. Access denials and verification challenges stop
immediately. This mode does not rotate sessions or proxies in response to failed
resources. Start through `run.py` so `zillow-run.lock` protects the queue.

### Near-city queue ordering

`prioritize_inventory.py --database ... --county-inventory ... --output ...`
creates a private `zillow-priority.json` from the saved county inventory. It
keeps Dougmar first, then uses the official Santa Cruz city-limit flag, followed
by approximate outward postal-area bands. These bands are not exact parcel
measurements; the inventory has no parcel coordinates. Freedom/Watsonville are
later than Santa Cruz, Capitola/Soquel, Scotts Valley/Felton, and Aptos.

Pass the resulting file as `--priority-file` to `run.py`. The worker reorders
existing pending requests through Crawlee's supported forefront API without
clearing the queue or requeuing handled failures. Follow-up property-detail
requests take precedence over the next address, so a nearby lookup is completed
before moving outward. Keep this flag on subsequent restarts. Pause only the
Zillow worker before changing its saved queue ordering.
