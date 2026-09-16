# A repeating Marketplace monitor entirely inside REL

A native REL Action checks for a **used stroller wagon within 10 miles of Santa
Cruz**, compares its price with current online offers and other Facebook
Marketplace areas, and posts new matches to the **REL WhatsApp group** through
WhatsApp Web in the same browser session.

REL owns the schedule, browser session, AI execution, and run history. This case
study has no Python runner, shell loop, wacli dependency, GitHub Action, or
external scheduler.

## Set up in REL

1. Open a persistent REL session and configure a working AI provider/model.
2. Sign in to [WhatsApp Web](https://web.whatsapp.com) in that session and verify
   that the intended group is named exactly **REL**. Facebook must also be
   accessible in that session; sign in if required.
3. Open the session's **Bottom Panel → Actions → Add Action**.
4. Name the Action **Santa Cruz stroller wagon** and paste [ACTION.md](ACTION.md)
   into **Step 1**.
5. Set **When → Repeat every**, **Minutes → 30**, and **Ends → Never**.
   Edit the existing daily Action if one is already installed; do not create
   additional clock-time actions.
6. Leave **Enabled** on, **Shortcut** and **Webhook** off, and **On Error → Stop**
   under Advanced. Save.
7. Use the Action's context menu → **Run Now** to validate its browser access and
   group destination. This is a live action: it can post a verified new match.

This setup requires a REL build containing repeating actions (REL change #461).
The prior daily Action lives in local **Session2564**; session IDs are local,
so readers should use their own persistent session. As of the September 16
configuration check, the installed REL 0.1.65 app still offered only Manual,
Schedule, and Event. Its updater reported no newer version. The local Action
therefore remains daily at 9 AM until a compatible app is installed and the
repeat setting can be saved. The instructions above describe the intended
30-minute configuration, not a verified change to that older running app.

REL must remain running and the Mac awake. Native repeat scheduling skips
missed runs and prevents overlapping runs; it does not create catch-up bursts.
Use a single repeating Action. This is not a cloud-hosted or always-on job.

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

The checked-in artifact is the reusable prompt plus these native setup
instructions. There is no automatic installer and checking out this repository
does not create or enable an Action on another Mac.

## Local validation

A native **Run Now** validation on September 16, 2026 reached WhatsApp Web and
reported that QR-code or phone-number login was required. It inspected no
Facebook listings and sent no messages. The Action table displayed **Completed**
because the agent finished reporting the blocker; that status does not mean an
alert was delivered. Link WhatsApp in Session2564 before the next scheduled run.
An end-to-end group alert has not yet been validated.
