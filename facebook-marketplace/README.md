# A daily Marketplace monitor entirely inside REL

A native REL Action checks for a **used stroller wagon within 10 miles of Santa
Cruz**, compares its price with current online offers and other Facebook
Marketplace areas, and posts new matches to the **REL WhatsApp group** through
WhatsApp Web in the same browser session.

REL owns the schedule, browser session, AI execution, and run history. This case
study has no Python runner, shell loop, wacli dependency, GitHub Action, or
external scheduler.

## Load the Profile into REL

The reusable [PROFILE.json](PROFILE.json) includes the Action, daily schedule,
editable inputs, and setup checklist. It requires a REL build supporting Profile
setup version 1 and Profile JSON transfer version 2. Older versions of REL reject
this package; the manual instructions below remain available.

1. Copy the complete contents of `PROFILE.json`.
2. In **Settings → Profiles → Import Profile**, paste the JSON and import it.
3. Choose **New Session from Profile → Marketplace monitor**. Customize the
   search, location, radius, and WhatsApp group, then create the session.
4. Choose a working AI provider/model, sign in to WhatsApp and Facebook in that
   session, and verify access and the intended group yourself.
5. Open **Actions → Review Setup**, review the prompt and the daily 9 AM schedule,
   confirm the checklist, and choose **Enable Actions**. Times follow your Mac's
   time zone. Enabling makes the Action eligible for future scheduled runs.
6. **Run Now** is a live run and can post a verified new match. Reviewing setup
   does not run the Action or send a message.

Import creates a Profile only. Session creation makes independent, disabled
Actions with fresh IDs. The package contains no cookies, API keys, webhook
secrets, or session IDs. Logins, runtime history, and Action edits stay local.
This version uses WhatsApp Web and does not configure a webhook.

## Set up in REL

1. Open a persistent REL session and configure a working AI provider/model.
2. Sign in to [WhatsApp Web](https://web.whatsapp.com) in that session and verify
   that the intended group is named exactly **REL**. Facebook must also be
   accessible in that session; sign in if required.
3. Open the session's **Bottom Panel → Actions → Add Action**.
4. Name the Action **Santa Cruz stroller wagon** and paste [ACTION.md](ACTION.md)
   into **Step 1**.
5. Set **When → Schedule**, select all seven days, and choose **9:00 AM**.
6. Leave **Enabled** on, **Shortcut** and **Webhook** off, and **On Error → Stop**
   under Advanced. Save.
7. Use the Action's context menu → **Run Now** to validate its browser access and
   group destination. This is a live action: it can post a verified new match.

This configuration was saved in local **Session2564** on September 16, 2026.
The action list showed one daily action with its next run at **Thursday 9:00 AM**.
The temporary half-hourly actions were removed. Session IDs are local; readers
should create the action in their own persistent session.

REL must remain running and the Mac awake at the scheduled time. The clock
follows the Mac's time zone (America/Los_Angeles on this setup). Daily execution
is intentional until native interval scheduling is available. This is not a
cloud-hosted or always-on job.

## What each run does

The prompt first verifies WhatsApp access and an unambiguous group destination.
It then searches Marketplace, inspecting up to 20 relevant listing links and
two search-result scrolls. It requires a complete used child stroller wagon,
availability and local collection, and credible detail-page location evidence.
It skips accessories alone, rentals, new goods, sold/pending items and uncertain
locations. Facebook can recommend distant listings despite the radius setting;
the search filter alone is never treated as distance evidence.

For an eligible item, REL researches its brand/model again on current retailer
or manufacturer pages and up to three Marketplace listings elsewhere in
California. Alerts include links, observation date, price differences and
condition/accessory caveats. Unknown comparisons stay unknown. The historical
[Pronto price comparison](PRICE-COMPARISON.md) is an example, not a price feed.

Before posting, REL searches the group for the stable listing ID or URL, verifies
the destination again, and checks the outgoing message afterward. It sends no
WhatsApp message when there are no new matches. Login failures, unclear group
identity or uncertain delivery stop the run and are reported in REL chat.

These checks are agent instructions, not deterministic database constraints.
Group-history availability, model interpretation, map precision and website
changes can limit matching and duplicate detection. When evidence is incomplete,
the prompt tells the agent to stop or skip instead of guessing. It does not
promise complete Marketplace coverage, exact pickup distances, or exactly-once
delivery.

## Operation

Inspect the Action status and the session chat for results. Edit the same Action
to change its time or criteria; disable it to pause. Preserve the session and its
WhatsApp login. No local CLI authentication or phone number configuration is
needed. A WhatsApp Cloud API webhook is not used for this existing consumer group.

The checked-in artifacts are the Profile package, reusable prompt, and native
setup instructions. Importing `PROFILE.json` and creating a session installs
disabled Actions.
Checking out this repository alone does not create or enable an Action.

## Local validation

A native **Run Now** validation on September 16, 2026 reached WhatsApp Web and
reported that QR-code or phone-number login was required. It inspected no
Facebook listings and sent no messages. The Action table displayed **Completed**
because the agent finished reporting the blocker; that status does not mean an
alert was delivered. Link WhatsApp in Session2564 before the next scheduled run.
An end-to-end group alert has not yet been validated.

The Profile package was validated in an isolated REL Debug build: Profile JSON
import, binary archive export/import, customized radius substitution, and creation
of a disabled session Action all passed. The setup review showed the daily 9 AM
schedule and left activation disabled until checklist confirmation. This validates
installation, not live Marketplace research or WhatsApp delivery.
