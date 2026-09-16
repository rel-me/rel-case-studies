# REL case studies

Runnable browser research projects built with REL, rel-crawlee, and rel-crawler.
Read the [case studies on the REL website](https://rel.me/case-studies)
and the [public REL tools documentation](https://github.com/rel-me/rel-tools).

Each case study provides an executable `run.sh` in its own directory to run or
resume the project. Pass options directly to that script.

## Zillow / Santa Cruz

[Read the case study and run the crawler](zillow/README.md). Match official parcel addresses to Zillow, retaining property facts and source captures in SQLite.

```sh
./zillow/run.sh
```

Requires Release REL.app, Python 3.11+, Git, and an OxylabsDatacenter Profile. Local data and credentials are not committed.

## Facebook Marketplace / Santa Cruz

[Find a used stroller wagon within 10 miles and alert the REL WhatsApp group](facebook-marketplace/README.md).
Uses REL browser captures, conservative location checks, and persistent alert deduplication.

```sh
./facebook-marketplace/run.sh setup
cp facebook-marketplace/config.example.json facebook-marketplace/config.json
./facebook-marketplace/run.sh
```

Preview mode is the default. Link your WhatsApp account with `wacli auth`, then use
`--send --watch` for recurring group alerts. Includes dated online and other-area Marketplace price comparisons. Requires wacli and REL.
