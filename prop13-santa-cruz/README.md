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
npm test
npm run dev
```

Open http://localhost:8790/case-studies/prop13-santa-cruz.

The dedicated Cloudflare Worker serves only the path configured in
`wrangler.jsonc`; the existing `rel.me` origin Worker is left in place. An exact
path guard rejects lookalike paths captured by the wildcard route. Deployment
requires an authenticated Cloudflare account with access to the `rel.me` zone:

```sh
npm run deploy
```

Remove the dedicated Worker route to roll back this publication without changing
the main website. Future main-site deployments do not include this dataset.
