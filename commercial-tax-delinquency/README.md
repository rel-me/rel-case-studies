# Santa Cruz commercial properties under $2 million with past-due taxes

Crawl LoopNet using the **LoopNet** REL Profile, then load official Santa Cruz
County tax records using **Datacenter**. Match each listing's street address to
the county's situs address and APN before interpreting its taxes. Only Santa
Cruz County is in scope; adjacent counties are excluded.

The runtime is now the two-stage [crawler](crawl.py), replacing the earlier
single-session Action so each source uses its requested Profile. See the
[October 20 rural follow-up](RESEARCH-2026-10-20.md),
[October 18 tax recheck](RESEARCH-2026-10-18.md) and
[October 17 listing report](RESEARCH-2026-10-17.md) for actual evidence.
**Current filter:** asking price at most $2,000,000; prioritize properties suited
to a single business/operator. Multiple rooms are fine. Over-budget properties
are excluded even if taxes are overdue. No qualifying under-budget overdue
property is verified yet.

## Search focus

Prioritize rural/outlying commercial properties around Bonny Doon, the North
Coast/Davenport outskirts, San Lorenzo Valley outskirts, Corralitos, and rural
Pajaro Valley. Exclude downtown and dense urban districts. Defer village centers
to a separate list. A Felton or Boulder Creek postal address does not establish
a rural setting. Aromas addresses need county-boundary verification.

Prefer a standalone building or a single commercial unit over a multi-tenant
investment, shopping center, or portfolio. Rooms are not tenant counts. A
single existing tenant can satisfy the single-business preference; occupancy
and lease terms remain separate facts.

Treat flexible permitted uses as a preference to investigate. Record exact
**official zoning** and overlays with their source/date separately from existing
mixed uses and an advertised flex building/layout. Commercial zoning does not
by itself prove residential occupancy or a conversion is permitted. Keep
uncertain zoning and rural status unresolved for review.

Search retail, office, industrial/flex, warehouse, hospitality, self-storage,
commercial land and documented mixed-use real estate offered for sale. Exclude
business-only sales, lease-only listings, residential-only property and land
without documented commercial use.

## Run the crawler

Requirements: REL running with existing **LoopNet** and **Datacenter** Profiles,
Python 3.11+, and matching `rel-crawlee`/`rel-playwright` adapters. All site traffic
uses REL's embedded Chromium. No direct HTTP or alternate-browser fallback is
used. Profile configuration stays in REL; this directory stores no credentials.

From the repository root, use the existing Zillow environment:

```sh
zillow/.venv/bin/python commercial-tax-delinquency/crawl.py discover
```

Or install independently:

```sh
python3 -m venv commercial-tax-delinquency/.venv
commercial-tax-delinquency/.venv/bin/pip install -r commercial-tax-delinquency/requirements.txt
commercial-tax-delinquency/.venv/bin/python commercial-tax-delinquency/crawl.py discover
```

Discovery starts at **https://www.loopnet.com/**, clicks **For Sale**, enters
**Santa Cruz County, CA**, and submits the homepage search. It follows native
pagination links, then each selected listing link and browser-history **Back**.
It keeps the same LoopNet session for the entire run; internal listing/search
URLs are never direct-navigation seeds. Smart Search can briefly use a USA URL
before redirecting to the county results; the adapter rebinds before continuing.

The cap is four result pages and 25 listing details. Numeric advertised asking
prices over $2 million and undisclosed prices are excluded from the budget
shortlist. Single-business building types are prioritized and multi-tenant facts
are retained for review. Cards labeled residential/agricultural-only are
excluded, but details are still reviewed for mislabeled residential offerings.
Area-name hints prioritize rural discovery; they are not official rural proof.
Small-town/outlying urban alternatives are kept separate from rural matches.

One Crawlee root-page job owns the browser lifecycle. The local `discovery.json`
checkpoint tracks individual detail captures and attempts; subsequent runs repeat
the homepage/search route and skip saved captures. A new root job is used each
run so persisted Crawlee job statistics cannot suppress execution. Failed detail
entries stay terminal; an intentional fresh output directory allows a new run.
Captured pages have metadata sidecars. The default selects the existing LoopNet
Profile. To borrow a session already using that Profile:

```sh
zillow/.venv/bin/python commercial-tax-delinquency/crawl.py discover \
  --loopnet-session Session11499 --max-price 2000000
```

Session IDs are local and may expire. Verify the Profile before borrowing a
session; the script does not change its configuration. Do not drive that session
from another client while the crawler runs.

## Match addresses and inspect taxes

1. Review `output/discovery.json` and listing captures. Verify real estate is
   included, record its street address, and classify rural setting and zoning.
2. Resolve missing/ambiguous APNs through the [county assessor](https://sccounty01.co.santa-cruz.ca.us/ASR/)
   or official GIS links from the county. Verify county boundary and geometry.
   The assessor currently fails through Datacenter with a proxy CONNECT
   rejection; automatic missing-APN resolution is not implemented or validated.
3. Save reviewed candidates in `candidates.json`, following
   [candidates.example.json](candidates.example.json). Include `county: "Santa Cruz"`,
   `street_address` without city/ZIP, `apn_hint` and `listing_url`. Use one row
   per parcel, at most 25 per run. The examples are workflow samples, not leads.
4. Run the tax lookup, which selects **Datacenter**:

```sh
zillow/.venv/bin/python commercial-tax-delinquency/crawl.py taxes \
  --input commercial-tax-delinquency/candidates.json
```

Use `--tax-session SESSION_ID` only for a verified existing Datacenter session;
borrowed sessions are not closed by the adapter. Keep sessions exclusive during
a run. The default creates a session from the Datacenter Profile.

The tax phase enters the APN hint into the public
[county bill lookup](https://ttc.co.santa-cruz.ca.us/TaxBills/), verifies official
APN and situs street, and clicks **Show Tax Bill History**. It saves returned
annual/supplemental installments, payment dates, displayed due dates and detail
links, plus the account default-message block. Both **Not Paid** and **Past Due**
are unpaid signals; **Defaulted** in an amount cell is not a numeric zero. Matching preserves numbers, ranges, fractions and units, with narrow
street-type aliases. A listing's `-000` APN suffix is removed only to create a
search hint; official APN and address still must match.

The script marks unpaid prior years `prior_year_unpaid_needs_default_review`.
It does **not automatically qualify leads**. Inspect default balances,
redemption/plan status, pending payments and current quotes through the county,
and cross-check [default publications](https://www.santacruzcountyca.gov/Departments/TaxCollector/TaxCollectionDivision/PublicNotices.aspx).
Retain publication and balance dates: historical default is not proof of
current debt, and absence from a notice does not establish payment.

`no_prior_year_arrears_shown` describes only displayed history, not a guaranteed
zero redemption balance. Unpaid current installments with future due dates are
not overdue. Do not double-count installments within a redemption total, infer
property-wide taxes from one parcel, or infer owner distress/auction eligibility.

## Outputs, validation and limits

The gitignored `output/` contains `discovery.json`, `tax-results.json`, a persistent
`queue/` and local HTML captures. URLs and UTC check times accompany results.
Review captures before sharing. The tax stage replaces its result JSON each run;
use a new `--output` directory to retain separate summaries.

The final report should show address, listing ID/link/status/ask, official APNs,
rural evidence, exact zoning/source, unpaid fiscal years, amounts with quote
dates, plan status and checked time. Keep historical, unresolved, paid and
excluded properties separate.

Tests cover address mismatches, unsupported APNs, missing/defaulted history,
future/current versus prior-year bills, duplicate totals, locality hints and
budget parsing. The corrected homepage route successfully read all three county
result pages (97 cards) and captured listing details. County lookups found an
over-budget default at 22990 Highway 17; the current budget excludes it. After an
October 17 proxy HTTP 429, October 18 tax checks succeeded for the known parcel
hints. The advertised Unit D APN resolved to Unit 2A, so its taxes remain
unverified. See the dated reports for exact coverage. This is a bounded sample,
not a complete parcel inventory or title review.

```sh
zillow/.venv/bin/python -m unittest discover -s commercial-tax-delinquency -p 'test_*.py'
```

Review current source-site terms and access restrictions before running. The
crawler stops after a source failure and preserves partial work. It does not
rotate Profiles/proxies to evade denial, select bills for payment, pay taxes,
contact owners/brokers, send messages or schedule itself. Only public record
lookup and tax-history forms are submitted by the crawler.

## Current REL compatibility

The October 20 follow-up used REL Release 0.1.139. Its browser-group/session
API differs from the pinned adapter: session creation/list decoding and SDK
Back can fail even though native browser input and captures work. Borrowing
verified existing LoopNet and Datacenter tabs allowed search and tax checks.
Automatic Back failed with `group_id must use the Group<number> format`; native
REL Back restored the source for interactive research. Do not claim a successful
end-to-end automated run on that release. The default profile-creation workflow
needs a matching REL adapter before it can be relied on again.

Saved research HTML and account exports are local-only and are not distributed
with this repository. Reports provide public source entry points and lookup
steps; code-formatted `output/` paths refer to optional local run artifacts.
