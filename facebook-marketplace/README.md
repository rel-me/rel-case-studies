# Marketplace research in REL

Find a used stroller wagon within **10 miles of Santa Cruz**, with current online
and other-area Facebook Marketplace price comparisons. The intended notification
destination is the **REL WhatsApp group**, using REL's native WhatsApp connection.

## Current capabilities

REL 0.1.68 imports Profile setup definitions and creates disabled native Actions.
The Action editor supports **Repeat every → 30 minutes → Ends: Never**. Profile
setup version 1 only encodes weekday/clock schedules, so the portable template
creates a manual Action; set the interval after creating the session.

Native WhatsApp pairing and saved group selection work in Settings, but native
WhatsApp delivery is not yet an Action completion option. This package therefore
performs research and returns candidate alerts in REL chat only. It does not open
WhatsApp Web or send messages. Automatic WhatsApp notifications remain blocked
until native delivery and durable duplicate tracking are implemented.

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
   It can run while automatic scheduling is disabled. Results belong in REL chat;
   no WhatsApp alert is sent by this package.

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
