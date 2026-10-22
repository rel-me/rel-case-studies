# Zillow property research with REL Actions

Import [ACTION.json](ACTION.json) into REL to research up to 20 Santa Cruz
properties and produce a sourced report in the session chat. REL owns the
browser session, AI execution, and run history. A custom **Profile is optional**.

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

## Set up in REL

1. Use a REL build with **Settings → Actions → Import Action** and configure a
   working AI provider/model.
2. Open **REL → Settings → Actions → Import Action** and paste the complete
   contents of [ACTION.json](ACTION.json).
3. Select **Santa Cruz Zillow property research** and choose **Use Action**.
4. Choose an existing **Session**, or **New session**. For a new session, leave
   **Profile → REL default** selected or optionally choose your own Profile.
   Existing sessions keep their current browser configuration.
5. Click **Add Action**. Review the setup checklist and the installed steps in
   the session's **Actions** panel. Imported actions start disabled.
6. Review the session model, keep **When → Manual** and **On Error → Stop**,
   then enable the Action and use its context menu → **Run Now**.

Keep REL running while the Action executes. Importing the JSON adds a reusable
library definition; using it installs a separate session Action. Neither importing
nor checking out this repository starts a research run. If **Import Action** is
missing, update to a build supporting the Actions library and text transfers.

## Optional Profile

The supplied `rel.action` version 1 configuration contains `configuration.setup`
and deliberately omits the optional `configuration.profile`. No custom Profile,
proxy subscription, or specific provider is required. A Profile can supply your
preferred browser, network, and model settings when creating a new session.

To reuse a preferred Profile, edit the library Action's **Default Profile**.
**Attached Profiles** adds the steps, disabled, to future sessions created from
those Profiles; existing session actions retain their own steps and progress.
Use **Export Action** to share the definition with its optional browser Profile
configuration. Exports exclude credentials, browser data, local attachments, and
run history; a referenced proxy alias must be configured on the receiving Mac.
The earlier crawler used an Oxylabs ISP proxy, but this Action does not require it.

## What each run does

The Action has three steps:

1. Discover up to 20 distinct property links across at most two Santa Cruz
   search-result pages, deduplicated by ZPID.
2. Verify each detail page's identity and Santa Cruz, CA address, preserving
   unit identifiers, and collect supported property facts with source links.
3. Report prices labeled by home status, estimates, bedrooms, bathrooms, floor
   and lot areas with units, observation times, skipped pages, and coverage.

Missing values remain unavailable. Conflicting bathroom counts retain their
source labels. Login requirements, access challenges, and navigation failures
are reported with any available partial results. No external messages are sent.

This is a bounded sample of visible Zillow results. It does not establish city
boundary membership, enumerate all dwellings, or promise complete coverage.
These are agent instructions; site changes and model interpretation can affect
results. Each run is a fresh observation and can include previously seen homes.
Inspect the session chat and Action run history to assess the actual outcome;
a completed run alone does not establish complete property coverage.

## Inventory crawler and existing data

The [inventory crawler guide](CRAWLER.md) retains the separate Python workflow
for official GIS address matching, persistent request queues, rendered captures,
SQLite storage, retries, and data migration. Its code and saved output remain
available. The native Action does not read or update that crawler's database or
resume its queue. Python, Git, and crawler dependencies are only needed for that
workflow.

## Artifact validation

[ACTION.json](ACTION.json) follows REL's `rel.action` version 1 text-transfer
format with a version 1 setup, three prompts, no schedule, and no bundled Profile.
The setup is manual and stops on errors. A live Zillow run is not part of this
repository update; import and run it in your own REL session to verify access.

The private dashboard shows remaining Zillow addresses and tax parcels by the
same geographic groups used for queue priority: Santa Cruz city limits, Live Oak
area, Capitola, Soquel, then other postal areas. Santa Cruz uses the official
city-limit flag. Live Oak is an explicit approximation: unincorporated ZIP 95062,
not an exact community polygon. Capitola prefers official city limits with a
postal-name fallback; other groupings use saved postal names. Multi-parcel Zillow
addresses count once, in their earliest priority group. Only pending/in-progress
work counts as remaining; terminal failures remain excluded. Counts refresh with
the crawler status every 15 seconds. Tax estimates and Dougmar are prioritized
within their geographic group, without overriding Santa Cruz city limits.
Regenerate the private Zillow priority JSON using `prioritize_inventory.py` and
the existing county inventory; both crawlers apply their order on startup without
clearing queues or retrying handled resources.

### Terminal dashboard

Run `./monitor.sh --output /absolute/path/to/private/output` from this directory
with Python 3 (no package installation). `PYTHON=/path/to/python3` selects an
interpreter. The default output is `CRAWL_OUTPUT`, then this directory's `output`
when it contains the database, then an existing
`~/projects.next/rel-case-studies/zillow/output` installation.

The dashboard independently shows Zillow and county-tax status, remaining work,
saved pages, database error totals, failure streaks, and saved stop reasons. The
live feed includes every newly saved URL and database error. Startup exceptions
and ERROR/CRITICAL/FATAL lines from the crawler logs are shown too, marked
`[log:filename]`; these diagnostics may also describe an error saved in SQLite.
Historical log entries without timestamps display as `-- --:--:--`; new log
errors use their observation time. ANSI escapes, control characters, and line
breaks are removed so each event occupies one terminal line.

Press `z` for Zillow or `t` for tax, then `y` to confirm a start/restart. Any
other key cancels. A stopped crawler launches its saved command; an active one
receives SIGINT and must exit and release its queue lock before a replacement
starts. Zillow's child is interrupted first so its launcher keeps the lock until
shutdown completes. The monitor stays responsive during the action, reports
failures in its notice/feed, and never force-kills an unresponsive worker. If
shutdown takes more than 45 seconds, no replacement is started; wait for the old
worker to exit before trying again. Quit is deferred while a confirmed action
finishes. Workers run independently of the monitor after startup.

Controls preserve the configured profiles, sessions, queue paths, priorities,
limits, captures, saved terminal failures, and persisted failure streaks. They
refuse maintenance, saved safety/access stops, eleven consecutive failures,
exhausted queues, conflicting locks, unrelated PIDs, and retry/rotation commands.
These controls do not clear a safety stop; it must be resolved separately. Both
the web dashboard and terminal controls share `dashboard-restart.lock` to avoid
simultaneous starts. Run `--read-only` to disable interactive controls.

Other keys: `q` quits, Space freezes/resumes the view, `1/2/3` filter all/Zillow/tax,
`e/s/a` filter errors/saves/both, arrows and PageUp/PageDown scroll, End follows
the live tail, and left/right reveal long lines. The display retains 5,000 events
and initially reads the latest 60 records per table (`--history N` changes that).
Polling defaults to every two seconds (`--interval 5` changes it). New records
are drained in bounded batches without skipping IDs.

Use `./monitor.sh --plain` for complete, unwrapped event lines suitable for a pipe
or `tee`. Use `./monitor.sh --once --history 10` for one status snapshot and recent
events. Noninteractive output automatically selects plain mode. These modes have
no controls. Missing/busy databases are reported and retried; the monitor never
creates a database. Quitting does not stop either crawler. Output contains private
crawl URLs and errors; keep terminal recordings and redirected logs private.

### Stop controls in the private web dashboard

Each active crawler card has its own Stop button. It verifies the saved worker
identity, sends SIGINT (to the Zillow child before its launcher), and waits for
its process and queue lock to finish. No force-kill or replacement is sent.
The card reports Stopping while shutdown is pending. A manual maintenance marker
keeps heartbeat monitoring from restarting it; the dashboard Restart button
explicitly resumes that user pause. Other maintenance remains blocked.
Logs, captures, terminal failures and failure streaks are retained. The other
crawler and REL sessions are left running.
