# Marketplace research in REL

Find a used stroller wagon within **10 miles of Santa Cruz**, with current online
and other-area Facebook Marketplace price comparisons. The intended notification
destination is the **REL WhatsApp group**, using REL's native WhatsApp connection.

## Current capabilities

REL 0.1.68 imports Profile setup definitions and creates disabled native Actions.
The Action editor supports **Repeat every → 30 minutes → Ends: Never**. Profile
setup version 1 only encodes weekday/clock schedules, so the portable template
creates a manual Action; set the interval after creating the session.

Native WhatsApp completion works in REL 0.1.69. In the Action editor select
**When finished → WhatsApp** to send the final response to the saved group.
The [quiet-result fix](https://github.com/rel-me/rel/pull/478) suppresses a leading
`NO_ALERT` response; it must be installed before using this prompt unattended.
Durable tracking of previously alerted listing IDs remains necessary to prevent
repeated alerts. Automatic operation stays paused until that is implemented.

## Install

1. Import [Marketplace-monitor.relprofile](Marketplace-monitor.relprofile) with
   REL's Profile import command or file importer. The archive was exported by
   installed REL 0.1.68 and contains no browser authentication or credentials.
   [PROFILE.json](PROFILE.json) is the readable JSON transfer equivalent.
2. In REL, choose **New → New Session from Profile → Marketplace monitor**.
   Review the search, location, radius, and intended group inputs, then create.
   If you imported under another name, select that name instead.
3. Choose a working AI provider/model and sign in to Facebook in this session.
4. In **Actions → Edit Action**, choose **Repeat every**, **30 minutes**, and
   **Ends → Never**. Leave disabled while access and delivery are incomplete.
5. Inspect **Review Setup**, then use **Run Now** for a one-time research check.
   REL 0.1.69 requires the Action to be enabled for Run Now; PR #478 fixes this.
   Selecting WhatsApp makes a successful run send its final response to the saved group.

Import creates a Profile only. Creating the session installs its Action with
fresh IDs. Existing Action edits and run history are not transferred. REL must
remain open and the Mac awake for enabled schedules; missed runs are skipped and
runs do not overlap. No external scheduler, script runner, or browser-based
WhatsApp automation is used.

## Matching and comparisons

The [Action prompt](ACTION.md) limits inspection to 20 relevant listing links and
two search-result scrolls. It excludes accessories, rentals, wanted ads, pet
wagons, new goods, sold/pending items, and shipping-only offers. Detail-page
location evidence must support the 10-mile radius; the search filter alone is
insufficient. Uncertain locations are skipped.

For eligible items, compare the exact brand/model with current in-stock retailer
or manufacturer offers and up to three used Marketplace listings elsewhere in
California. Include links, observation date, condition, accessories, dollar and
percentage differences. Label broader model-family comparisons explicitly and
leave unavailable comparisons unknown. The historical
[Pronto comparison](PRICE-COMPARISON.md) is an example, not a current price feed.

## Installed-app verification: September 17, 2026

- Installed REL 0.1.68 reports build `rel-release-0.1.68-2f1cb731-1afa70c80055`.
- Native WhatsApp Settings shows **Connected**, with **REL** saved as the group.
- Supported RPC creation, binary export, and CLI import preserved the setup.
- Creating a session from the imported **Santa Cruz stroller wagon** Profile
  created **Session2565** and one disabled Action with correctly substituted
  Santa Cruz, 10-mile, and REL inputs.
- The Action editor saved **Repeat every: 30 minutes; Ends: Never**, disabled.
- **Run Now** immediately displayed **Failed** before a chat response. Its cause
  was not exposed in the observed Action UI; this is not a successful execution.
- A separate native REL browser check reached Facebook's required login page in
  Session2565. No listings were verified and no messages were sent.

Facebook sign-in, a successful full Action run, and native notification delivery
still require validation. Local session IDs are evidence for this Mac only.

## Retest on REL 0.1.69

Signed-in Marketplace access and native WhatsApp completion were verified on
September 17. Run Now originally failed because the Action was disabled; the
error popover exposed that cause. Enabling it allowed execution. A broad request
hit the 24,000-token budget. Bounded single-listing research completed, but its
first answer invented a date and rejected a usable wagon for lack of explicit
seller confirmation. The prompt now avoids those unsupported requirements and
limits browser calls and response size.

The final native delivery validation used a researched alert: Santa Cruz Pronto
at $500, an in-stock manufacturer Pronto One grey/white starter package at $850,
and a used Pronto One in Eagle Rock/Los Angeles at $375. It states model, condition,
and approximate-location caveats. REL reported Completed after native WhatsApp
sending. This validates the send path using a supplied verified alert; the full
automated research-to-delivery workflow is not yet validated unattended.

Session2565 retains the 30-minute configuration and native WhatsApp completion,
but is disabled. The old daily WhatsApp Web Action in Session2564 is also disabled.
Listing 1380767834231130 was added to the local prompt's already-alerted list.
This manual record is not durable automatic deduplication.
