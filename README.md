# REL case studies

Browser research and monitoring case studies built with REL.
Read the [case studies on the REL website](https://rel.me/case-studies)
and the [public REL tools documentation](https://github.com/rel-me/rel-tools).

Each case study documents its runtime: a crawler launcher or a native REL Action.

## Zillow property research with REL

[Read the case study and run the crawler](zillow/README.md). Match official parcel addresses to Zillow, retaining property facts and source captures in SQLite.

```sh
./zillow/run.sh
```

Requires Release REL.app, Python 3.11+, Git, and an OxylabsDatacenter Profile. Local data and credentials are not committed.

## Facebook Marketplace / Santa Cruz

[Monitor used stroller wagons within 10 miles using a native REL Action](facebook-marketplace/README.md).
Runs daily at 9 AM, researches online and other-area Marketplace prices, and
posts new matches to the REL WhatsApp group through WhatsApp Web.

Create the Action inside REL with the supplied [prompt](facebook-marketplace/ACTION.md).
REL manages scheduling and browser execution; no scripts or external scheduler
are required. Keep REL running and WhatsApp Web signed in.
