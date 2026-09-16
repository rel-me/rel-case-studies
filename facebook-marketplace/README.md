# A used stroller wagon near Santa Cruz, with WhatsApp group alerts

Find a used **stroller wagon within 10 miles of Santa Cruz**, then post the price,
condition, approximate distance, listing link, and available price comparisons to
**the WhatsApp group REL**. REL handles Facebook browsing; `wacli` sends to the
linked WhatsApp account's group. SQLite deduplicates by listing ID and group JID.

## Run

Requires macOS, Python 3.11+, Git, an open Release REL.app, an existing REL
Profile, and [wacli](https://wacli.sh). The example selects the installed
`OxylabsDatacenter` Profile; change it to an existing Profile on your Mac if needed.

```sh
./facebook-marketplace/run.sh setup
cp facebook-marketplace/config.example.json facebook-marketplace/config.json
./facebook-marketplace/run.sh                 # preview only; no WhatsApp needed
```

For group alerts, link the CLI from WhatsApp on your phone under **Linked devices**:

```sh
wacli auth
wacli groups refresh
./facebook-marketplace/run.sh --send
./facebook-marketplace/run.sh --send --watch  # repeat every 30 minutes
```

WhatsApp desktop login does not automatically authenticate `wacli`. The CLI is
installed on this Mac but was not linked during setup, so no messages were sent.
The code checks authentication before starting a live scan. It refreshes joined
groups and requires exactly one group named `REL`. No fuzzy recipient matching
is used. If names collide, set `whatsapp_group_jid` to the intended group's JID
from `wacli --json groups list --query REL`. The name must still match exactly.

The watch loop runs in the foreground; keep the Mac awake and stop with Ctrl-C.
It exits visibly on browser, extraction, or sending errors. `interval_minutes`
changes the frequency (minimum five minutes). No background job is installed.

`--session-id Session123` adopts an existing REL session. Otherwise the monitor
creates one using the configured Profile and saves it in `output/session.json`.
A missing session fails explicitly. If Facebook requires login, sign in in REL
and resume. A dismissible public login overlay is closed automatically.

## Matching and distance

The search URL uses Santa Cruz's observed Marketplace location ID and Facebook's
10-mile radius parameter (`radius=16`, in kilometers). Search results can include
recommendations far outside that radius. Each listing is opened by clicking its
rendered link, captured, and followed by browser-history Back before proceeding.

Only the current **Marketplace Listing Viewer** is parsed. Search-page JSON can
remain in the DOM after navigation and describe entirely different listings.
The monitor requires all query words in the title, an explicit used condition,
a Message action, and no sold/pending marker. Accessories, rentals, wanted ads,
and pet wagons are excluded conservatively; bundles mentioning accessories may
also be excluded. Availability is the page's advertised state, not a seller's
confirmation. The monitor never contacts sellers.

Distance is measured in a straight line from `(36.9741, -122.0308)` in central
Santa Cruz, using the listing's rendered approximate map. The entire displayed
uncertainty circle must fit inside 10 miles; missing/ambiguous maps and boundary
cases are excluded. Facebook's approximate map is not an exact pickup address.
`latitude`, `longitude`, and `radius_miles` configure the local distance check;
update the search URL's radius too when expanding the search.

A pass checks at most 40 rendered listing links. It does not scroll indefinitely
or promise complete Marketplace coverage. Empty visible results stop for review
because the monitor cannot reliably distinguish an empty search from an access
or layout change. A failed target also stops alerts for that pass. A completed
pass starts a fresh checkpoint on the next scan, so new listings can be found.
An interrupted pass resumes its checkpoint. Captures older than 15 minutes are
never used to send alerts; rerunning starts a fresh pass when this guard fires.

## Price comparisons

[Read the current Santa Cruz Pronto comparison](PRICE-COMPARISON.md).
`price_references.json` contains dated, sourced observations for comparable
Pronto One wagons: a new online price and used Facebook listings in Los Angeles
and Durham, CA. Pronto alerts show the dollar and percentage difference against
each reference, with source links and a model/condition caveat.

These are researched snapshots, **not automatically refreshed prices**. They
expire after seven days; the alert then says the comparison needs refreshing.
Other brands and explicitly different Pronto models receive “comparison unavailable”
instead of an unrelated discount claim. Recheck source pages and update the JSON's
prices, notes and `checked_at` together to refresh the comparison. An unconfirmed
local Pronto model/year is always labeled as such.

## WhatsApp and duplicate prevention

The first live pass alerts on existing matches. Preview mode prints the message
without consuming an alert. Live mode reserves a listing/group pair before sending
and marks it `submitted` only after `wacli` acknowledges the destination and message
ID. This is not proof that every group member received or read it. Price changes
alone do not trigger another alert. Website text is passed literally as an argument.

An uncertain send stays reserved to prevent automatic duplicates. After checking
the group yourself, allow another attempt for that listing:

```sh
./facebook-marketplace/run.sh --reset-alert LISTING_ID
./facebook-marketplace/run.sh --send
```

The reset clears that listing's WhatsApp reservations across groups. Previous
Messages-app alert records are retained separately and do not suppress WhatsApp
alerts. For an exhausted browser pass, inspect its captures, then move
`output/active.json` aside to start a new pass.

## Files and validation

- `output/session.json`: saved Facebook REL session.
- `output/passes/`: checkpoints, rendered HTML, metadata and matching decisions.
- `output/marketplace.sqlite3`: observations and WhatsApp alert status.
- `output/run.lock`: prevents overlapping monitors.
- `config.json`: local settings; ignored by Git.
- `price_references.json`: dated public price references; no account credentials.

```sh
cd facebook-marketplace
.venv/bin/python -m unittest -v
```

Tests use synthetic HTML, a fake sender, and mocked CLI responses. They cover
matching, distance boundaries, availability, recovery, group routing, ambiguous
sends, price calculations and stale references. Tests do not send messages.

The browser integration uses the pinned public
[REL crawler](https://github.com/rel-me/rel-tools/tree/29678853803a37b7eb13cdeafcb40612d4ca40a3/crawler).
