# Responsible X research with REL

REL can make an authorized browser research crawl bounded, resumable, and
auditable. This guide describes collection techniques, not an authorization
to collect X data or a runnable crawler.

## Establish the permitted access route

Review [X's current terms](https://x.com/en/tos), the applicable
[developer guidelines](https://docs.x.com/developer-guidelines), and your
institution's research requirements before collecting. X's terms generally
require express written permission for scraping, except as required by
applicable law. A research purpose alone does not establish that permission.
Use the X API under its applicable terms when it meets the research need;
use REL for browser collection only within the permission that applies.

## 1. Define a bounded sampling frame

Specify research questions, keywords, dates, languages, and eligible accounts
before collection. Set hard limits on posts, pages, scroll attempts, and total
runtime. Avoid open-ended traversal through replies and follower networks.
Record the inclusion criteria and any later changes to the sampling plan.

## 2. Match the REL tool to the navigation

| Collection need | Tool and approach |
| --- | --- |
| A few interactive pages | Use `rel-browser` for inspection and context checks. |
| A queue of known post URLs | Use `rel-crawlee` for persistent queues, deduplication, bounded retries, and structured datasets. |
| Open a listing item and return to its source | Use `rel-crawler` to click the exact link, wait for the target, capture it, then use browser-history Back and wait for the source. |

Keep REL as the browser and network owner. Do not add a separate browser or a
direct-HTTP fallback when X rejects a browser request. Read the maintained
[Crawlee integration guide](https://docs.rel.me/crawlee/) and
[crawler guide](https://docs.rel.me/crawler/) for supported APIs.

## 3. Capture dynamic pages incrementally

Extract each visible batch before scrolling. A dynamic timeline may remove
earlier items from its rendered page, so the final page capture should not be
treated as the complete session history. Deduplicate posts by post ID while
retaining separate timestamped observations when measuring changes.

Cap scroll attempts and stop after a predefined number of batches with no new
IDs. Save the query, batch number, and stopping reason. Record truncated
observations as incomplete; do not silently treat them as complete coverage.

## 4. Verify content readiness and identity

Wait for the expected post content and verify its canonical URL or post ID
before saving a successful result. Use readiness checks after navigation,
clicks, and Back. Fixed delays can reduce load but do not prove readiness.

Distinguish unavailable content, login requirements, access challenges, load
failures, and genuine empty results. Keep outer-post text separate from quoted
post text and incidental conversation content to avoid attributing someone
else's words to the author being studied.

## 5. Bound traffic and failure recovery

Start with concurrency one, a small pilot, conservative pacing, and a limited
retry budget. Follow the applicable access limits; no fixed request interval
guarantees permission or acceptance. Pause on rate limits and stop on access
challenges or requests to stop. Do not rotate accounts, sessions, or proxies to
defeat restrictions. Record exhausted attempts instead of retrying indefinitely.

## 6. Preserve browser state deliberately

When authorized collection needs one specific live login, `rel-crawlee`
supports a caller-owned `session_id` with serial processing. That mode cannot
be combined with a Profile or session pool. Preserve caller-owned sessions;
do not automatically close them during crawler cleanup.

Persist queues and checkpoints separately from browser state. In Crawlee, use
a named persistent queue and `purge_on_start=False` when resume is required.
Queue resume does not restore a previous live session or session pool.
Checkpoint completed items so interruption does not require fetching them again.

## 7. Keep provenance and minimize personal data

For each observation, retain the post ID, source and final URLs, capture time
in UTC, query or seed, extraction version, and collection outcome. Keep post
publication time separate from capture time. Capture HTTP status when available;
do not infer it from a successful browser navigation alone.

Collect text, media, profile details, or raw HTML only when necessary for the
research question. Restrict access to raw captures, establish a retention
period, and plan how to handle deletion and removal requirements. Exclude
credentials and session secrets from datasets and logs. Prefer aggregate
reporting when identifying individuals adds no research value, and check the
applicable redistribution terms before sharing data.

## 8. Report coverage and evidentiary limits

Treat browser results as an observed sample. Log search settings, account
context without secrets, collection times, failed pages, and stopping rules.
Report queue completion separately from exhaustive coverage. Search ranking,
reply ordering, and similar wording alone do not establish who influenced whom.
Preserve explicit source-post references separately from inferred relationships.

## Suggested pilot

Use one predefined query or URL list, one session, a small capture cap, and a
checkpoint after each item. Inspect extracted records for identity, quoted-text
attribution, and missing content before increasing scope. Verify interruption
and resume, duplicate skipping, and one failed target using local fixtures or
a fake REL client rather than generating failure traffic against X.

This guide does not execute a crawl. Recheck the linked policies and live REL
documentation when implementing a study because access terms and APIs change.
