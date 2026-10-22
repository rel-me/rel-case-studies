# Santa Cruz Property Tax Study

Public advocacy and observational research site at
https://rel.me/case-studies/prop13-santa-cruz.

The project compares reported annual tax and reported assessed values with
third-party home-value estimates. It deliberately distinguishes annual tax
amounts from capital values. It does not infer annual payments from assessments.

## Data

The snapshot contains captured estimates and comparable assessments; counts and
source years depend on the most recent local export. Unknown assessment years
stay unknown. Bill and payment data remain private.

A sanitized server-side snapshot supports the public paginated API; it is not a static download. Raw SQLite databases, page captures,
browser state, source-specific identifiers and the local crawl dashboard are
excluded. The dashboard remains bound to loopback on port 8787.

Regenerate the export from a local capture database:

```sh
python3 scripts/export_data.py --database /absolute/path/to/capture-database.sqlite3
```

Read the methodology in `public/index.html`. The sanitized server snapshot is in
`data/data.json`; its CSV companion is available locally for research requests.
Neither file is in the static asset directory or exposed by an HTTP route. No owner names are
included. The public site describes valuation observations generically as
third-party estimates.

## Preview and deploy

```sh
npm ci
PYTHON=/path/to/crawl-venv/bin/python npm test
# Export the ignored server snapshot before preview/deploy:
python3 scripts/export_data.py --database /absolute/path/to/capture-database.sqlite3 --output data
npm run dev
```

Open http://localhost:8790/case-studies/prop13-santa-cruz.
Use the existing crawler environment for Python tests; it includes the
`selectolax` parser used by the county collector. A standalone test environment
can install `requirements-test.txt` instead.

The dedicated Cloudflare Worker serves only the path configured in
`wrangler.jsonc`; the existing `rel.me` origin Worker is left in place. An exact
path guard rejects lookalike paths captured by the wildcard route. Deployment
requires an authenticated Cloudflare account with access to the `rel.me` zone:

```sh
npm run deploy
```

Remove the dedicated Worker route to roll back this publication without changing
the main website. Future main-site deployments do not include this dataset.

## Official county tax enrichment

`scripts/crawl_county_taxes.py` visits the county's public Tax Collector lookup
through REL, submits a parcel-number search, and captures the current listing
and latest annual bill detail without loading older history unnecessarily.
It never clicks payment controls. A separate configured `Datacenter` profile session for the
`prop13-county-taxes` collector retains its configured connection settings. It leaves the valuation-crawl session
and source inventory database alone.

The queue includes every matched residential parcel whose latest recorded
observation has a positive value estimate. It records exact annual bill totals,
each installment's due date and paid date, and the sum of installments marked
paid. Supplemental/default/escape bills are excluded from annual totals. The
currently open year can be entirely unpaid; that is not a zero annual tax bill.

Use the crawler's existing Python environment (with `rel-crawlee`,
`rel-playwright`, and `selectolax` installed):

```sh
python scripts/crawl_county_taxes.py \
  --inventory /absolute/path/to/capture-database.sqlite3 \
  --tax-database /absolute/path/to/capture-database.sqlite3 \
  --output /absolute/path/to/private-output \
  --max-properties 12000 --rate 6
```

The Zillow records and private county tax records can share the same SQLite
file. County tables use the `county_tax_` prefix to avoid collisions; the
Zillow inventory connection remains read-only. Pass that same file to
`--inventory` and `--tax-database`. The Crawlee queue and REL session remain
separate files in the private output directory. A shared `run.lock` prevents
concurrent inventory/tax crawlers when using that directory. Repeat the command
to resume unfinished work; terminal failures are retained. Access denials and
challenges stop the worker without proxy rotation. The persisted failure streak
stops collection after more than ten consecutive failed resources (the eleventh).

For an older separate tax database, `scripts/merge_county_tax_database.py`
copies its tables into the Zillow database with the `county_tax_` prefix and
checks row counts. It preserves the source file as a rollback copy; after
migration, use the Zillow database for active tax writes and lookups.
Incomplete historical years and conflicting duplicate bills are retained as
explicit review issues, not inferred or silently overwritten. Raw capture
evidence is kept even when parsing fails. For example, the county can present
older installments as separate annual bills; those years require review.

Add `--summary` for status counts without browser navigation. The original
public export remains a third-party snapshot until the official records are
explicitly integrated with fiscal-year and billed-versus-paid provenance.

County sources:

- https://ttc.co.santa-cruz.ca.us/TaxBills/
- https://www.assessor.santacruzcountyca.gov/About/BulkDataPurchases.aspx

The Assessor also offers a paid secured-roll export. The public bill lookup
provides precise annual billing and payment dates without purchasing that file.

Full private evidence is retained for **all bill types**, including supplemental,
escape and default bills, even though these do not enter annual comparisons.
For each parcel, the collector saves the complete rendered history HTML and
follows each freshly discovered read-only **See Details** link in the same
county session. Every detail capture includes full rendered HTML, searchable
text, table rows, links, source URL, UTC capture time and a SHA-256 checksum.
This preserves assessments, exemptions, tax rates, agency distributions,
charges and any other details the county displays. It does not fetch PDF or
other attachments. Session-specific county link IDs are not durable bill IDs.

The `listed_bills` table retains the installment labels, original due/paid date
text, amounts and explicit paid/unpaid/unknown status for every bill. Partial
progress is checkpointed per detail page. Earlier summary-only parcels are
upgraded without discarding history. Complete detail captures are reused when
resuming an unchanged bill. Unpaid records can include installments not yet due;
these must not automatically be described as delinquent. All statuses reflect
the capture time and can subsequently change at the county.

Private lookups use standard Python and open the database read-only:

```sh
python scripts/query_county_taxes.py --database /private/output/zillow.sqlite3 --apn 009-123-26
python scripts/query_county_taxes.py --database /private/output/zillow.sqlite3 --unpaid
python scripts/query_county_taxes.py --database /private/output/zillow.sqlite3 --search '"Homeowners Exemption"'
python scripts/query_county_taxes.py --database /private/output/zillow.sqlite3 --capture-id 42 --html-output /private/bill-42.html
```

The results include capture IDs, full detail text, history capture time and
installment records. HTML export refuses to overwrite an existing file. This
lookup tooling and raw data are not served by the public Cloudflare Worker.

To prioritize an explicitly requested street outside the original city inventory,
pass `--priority-file /private/output/street-priority.json`. The JSON contains
`source` (the official county parcel-layer URL above) and `parcels`, an array of
`{ "apn": "02625202", "address": "1215 DOUGMAR DR" }` records captured from that
layer. These additions may lack estimates and are recorded separately in
`priority_parcels`; they must not be counted as estimate-backed comparisons.
Unfinished priority parcels move ahead of the saved queue. Completed records
and terminal failures are preserved. Stop the current worker gracefully and
wait for `run.lock` to release before restarting with the priority file.

## County coverage and city boundaries

`crawl_county_inventory.py` captures the full official county parcel layer in
validated object-ID batches through a separate Native REL session/queue. It
stores only address, parcel-use and boundary fields, with raw response HTML,
in `county-inventory.sqlite3`. It does not collect owner fields. Obtain the
layer's `returnIdsOnly=true` response through REL, save its rendered HTML, then:

```sh
python scripts/crawl_county_inventory.py --output /private/output --ids-html /private/output/county-inventory-ids.html
```

A complete inventory is required before adding county tax targets using
`crawl_county_taxes.py --county-inventory /private/output/county-inventory.sqlite3`.
This adds addressed parcels with the explicit residential use codes listed in
the inventory collector. Other/unknown use codes remain in the private inventory
for review; a parcel inventory is not a census of individual homes or units.
County additions do not require an existing third-party estimate. Existing tax
checkpoints and terminal failures are retained; Dougmar priority stays first.
Set `--max-properties` appropriately for the larger queue.

The public export accepts `--county-inventory` and `--county-taxes`; when tax
tables are in the Zillow database, pass that same database for both
`--database` and `--county-taxes`. It uses the
county `CITYLIMITS` spatial field, falling back to the original GIS `SCCityLMT`
flag for existing matches. It never derives jurisdiction from postal city.
`in_santa_cruz_city` distinguishes Santa Cruz city from all other county areas;
`municipality` retains other incorporated cities. The county’s `n/a` boundary value denotes unincorporated areas; blank boundary
values remain unknown. Unmatched or
conflicting boundaries remain unknown. The public location filter supports all
county records, Santa Cruz city limits, outside Santa Cruz city, and unknown.

Verified county annual bills prefer fiscal 2025–26 and retain billed and recorded
paid amounts separately. County-only records can be searched without an estimate;
comparisons remain blank until an estimate exists. The export does not publish
raw pages, temporary bill-link IDs, parcel IDs or owner fields. Inventory and tax
collection coverage are reported separately from estimate coverage.

## Map view

The List / Map buttons share all comparison, address, jurisdiction and property-type
filters. Map mode is linkable with `layout=map` alongside the existing `view`
comparison parameter. The map uses locally served Leaflet 1.9.4 and
Leaflet.markercluster 1.5.3 with OpenStreetMap raster tiles and visible attribution.
Tiles load only in Map mode for the visible viewport, with normal browser caching.
No address searches are sent to a geocoding service. Vendor licenses are served
alongside the library files in `public/vendor`.

The exporter includes validated latitude/longitude pairs and coordinate provenance
from existing property records, with a county-area sanity check and recorded city parcel bounding-box centers as a fallback for missing or out-of-area coordinates. The map shows approximate points, not parcel
boundaries. County-only records without coordinates remain in the list and count
as unmapped; their locations are never guessed. Clusters show record counts,
not average tax rates. Individual marker colors show the selected comparison
ratio, with gray for unavailable estimates. Popup links open the same property
details as the list. Future data refreshes must preserve coordinate fields and
report missing-location coverage.

## Bounded public access

The exporter now defaults to `data/`, outside the `public/` asset directory.
The Worker bundles this sanitized snapshot for server-side filtering and serves
only allowlisted site assets. `/data.json`, `/data.csv`, source files and export
routes return 404. There are no download controls in the public UI.

The API returns at most 25 records per page. Oversized, nonnumeric and duplicate
page-size parameters are rejected. Summary requests return aggregate statistics
and ten featured chart records. The map returns up to 64 aggregate groups or
25 minimal point references for the current viewport; full details are fetched
one record at a time. API responses use `private, no-store` and have no CORS
permission. A dedicated Cloudflare rate-limit binding allows 60 API requests per
minute per client IP (an abuse deterrent, not an exact global quota or authentication).
Displayed public information can still be copied through ordinary browsing.

Keep this access model during data refreshes: run `scripts/export_data.py` with
its default output or `--output data`, never `--output public`. Do not restore
raw download routes, bulk client fetches or client-side CSV export.

The county collector accepts `--max-consecutive-failures 10`: it skips exhausted
resources and stops on the eleventh consecutive failed parcel, or immediately on
an access restriction. A completed parcel resets the streak. The private
`county-progress.json` keeps this streak and its stop reason across interrupted
worker restarts; monitoring must honor a saved safety stop. Existing failed
parcels are not automatically requeued.

Tests use synthetic server snapshots and require no private export. Preview and deployment require the local `data/data.json` export; it is deliberately ignored by Git.
