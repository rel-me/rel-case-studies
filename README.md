# REL case studies

Runnable browser research projects built with REL and rel-crawlee.
Read the [case studies on the REL website](https://rel.me/case-studies)
and the [public REL tools documentation](https://github.com/rel-me/rel-tools).

## Zillow / Santa Cruz

[Read the case study and run the crawler](zillow/README.md). Match official parcel addresses to Zillow, retaining property facts and source captures in SQLite.

```sh
make -C zillow run
```

Requires Release REL.app, Python 3.11+, Git, make, and an OxylabsDatacenter Profile. Local data and credentials are not committed.
