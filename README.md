# REL case studies

Runnable browser research projects built with REL and rel-crawlee.
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
