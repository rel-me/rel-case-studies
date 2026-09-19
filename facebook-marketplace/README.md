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

## Install the reusable Action

Requires a REL build with **Settings → Actions** and `rel.action` imports.

1. Copy [ACTION.json](ACTION.json) into **Settings → Actions → Import Action**.
   This adds reusable steps and inputs without creating a browser profile.
2. Select the action and choose **Use Action**. Choose an existing session, or
   create a new session using REL's default profile or a profile you select.
   Existing sessions keep their current browser configuration.
3. Review the search, location, radius, and intended group inputs. Choose
   **Add Action**, configure an AI provider/model, and sign in to Facebook.
4. Review the installed steps and access checklist. In the session's
   **Actions → Edit Action**, choose **Repeat every**, **30 minutes**, and
   **Ends → Never**. Leave disabled while access and delivery are incomplete.
5. Use **Run Now** for a one-time research check. Selecting **When finished →
   WhatsApp** makes a successful run send its final response to the saved group.

The library editor lets you choose an optional default profile and attach the
same definition to existing profiles. New sessions created from those profiles
receive disabled steps. Existing session edits and run history stay independent.
REL must remain open and the Mac awake for enabled schedules; missed runs are
skipped and runs do not overlap.

[PROFILE.json](PROFILE.json) and
[Marketplace-monitor.relprofile](Marketplace-monitor.relprofile) are historical
REL 0.1.68 profile packages. They remain available for reproducing the verification
below; use ACTION.json for the current Action import flow.

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
