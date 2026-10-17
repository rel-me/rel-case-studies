# Santa Cruz Property Tax Study

Public advocacy and observational research site at
https://rel.me/case-studies/prop13-santa-cruz.

The project compares reported annual tax and reported assessed values with
third-party home-value estimates. It deliberately distinguishes annual tax
amounts from capital values. It does not infer annual payments from assessments.

## Data

The public snapshot contains 11,746 positive value estimates, 11,605 comparable
assessments and 24 positive annual tax-history amounts. Annual tax histories are
for 2025; value capture dates differ. Unknown assessment years stay unknown.
These are third-party reported figures, not verified tax-payment receipts.

Only the explicit public export is deployed. Raw SQLite databases, page captures,
browser state, source-specific identifiers and the local crawl dashboard are
excluded. The dashboard remains bound to loopback on port 8787.

Regenerate the export from a local capture database:

```sh
python3 scripts/export_data.py --database /absolute/path/to/capture-database.sqlite3
```

Read the source methodology in `public/index.html` and `data.json` metadata.
`public/data.csv` contains the same public property records. No owner names are
included. The public site describes valuation observations generically as
third-party estimates.

## Preview and deploy

```sh
npm ci
PYTHON=/path/to/crawl-venv/bin/python npm test
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
through REL, submits a parcel-number search, and opens **Show Tax Bill History**.
It never clicks payment controls. A separate `Native` profile session in the
`prop13-county-taxes` group uses no proxy. It leaves the valuation-crawl session
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
  --output /absolute/path/to/private-output \
  --max-properties 12000 --rate 6
```

Results are private in `county-tax.sqlite3`, with a separate persistent Crawlee
queue and `county-session.json`. The original inventory database is opened
read-only. A shared `run.lock` prevents concurrent inventory/tax crawlers when
using the same output directory. Repeat the command to resume unfinished work;
terminal failures are retained. Access denials and challenges stop the worker
without proxy rotation; three consecutive terminal errors also stop it.
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
python scripts/query_county_taxes.py --database /private/output/county-tax.sqlite3 --apn 009-123-26
python scripts/query_county_taxes.py --database /private/output/county-tax.sqlite3 --unpaid
python scripts/query_county_taxes.py --database /private/output/county-tax.sqlite3 --search '"Homeowners Exemption"'
python scripts/query_county_taxes.py --database /private/output/county-tax.sqlite3 --capture-id 42 --html-output /private/bill-42.html
```

The results include capture IDs, full detail text, history capture time and
installment records. HTML export refuses to overwrite an existing file. This
lookup tooling and raw data are not served by the public Cloudflare Worker.
