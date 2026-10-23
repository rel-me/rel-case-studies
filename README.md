# REL case studies

Browser research and monitoring case studies built with REL.
Read the [case studies on the REL website](https://rel.me/case-studies)
and the [public REL tools documentation](https://github.com/rel-me/rel-tools).

Each case study documents its runtime: a crawler launcher or a native REL Action.

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

## Zillow property research with REL

[Research Santa Cruz properties with a native REL Action](zillow/README.md).
Import the supplied [Action JSON](zillow/ACTION.json) in **Settings → Actions**,
then use it in an existing session or a new session with an optional Profile.
REL produces a sourced property report in chat; no scripts or custom Profile
are required for the Action.

The separate [inventory crawler](zillow/CRAWLER.md) remains available for official
GIS address matching, persistent queues, and SQLite storage.
Reusable [session/proxy rotation rules](SESSION_PROXY_ROTATION.md) describe
bounded recovery and checkpoint handling for REL crawlers.

## Facebook Marketplace / Santa Cruz

[Monitor used stroller wagons within 10 miles using a native REL Action](facebook-marketplace/README.md).
Uses native 30-minute repeats, researches online and other-area Marketplace prices, and
posts new matches to the REL WhatsApp group through WhatsApp Web.

Create the Action inside REL with the supplied [prompt](facebook-marketplace/ACTION.md).
REL manages scheduling and browser execution; no scripts or external scheduler
are required. Keep REL running and WhatsApp Web signed in.

## Public X narrative propagation

[Collect and study observable sharing among a public political sample](x-propagation/README.md).
Uses an authenticated caller-owned REL session, bounded Crawlee cycles, raw
captures, versioned observations, and separately reviewed propagation evidence.
The initial episode examines the Talarico hospital allegation and the center's
denial; the case-study setup records pilot and scheduling status.
