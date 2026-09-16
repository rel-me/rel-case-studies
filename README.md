# REL case studies

Runnable browser research projects built with REL and rel-crawlee.
The REL website is maintained in [rel-me/rel](https://github.com/rel-me/rel).

## Zillow / Santa Cruz

[Read the case study and run the crawler](zillow/README.md). Match official parcel addresses to Zillow, retaining property facts and source captures in SQLite.

```sh
make -C zillow run
```

Requires Release REL.app, Python 3.11+, Git, make, and an OxylabsDatacenter Profile. Local data and credentials are not committed.
