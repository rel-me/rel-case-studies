# Rules for rotating session proxies

Use bounded session replacement to recover a crawler from a failed browser or
proxy connection. Keep the same existing REL Profile and proxy alias. A fresh
browser session and a fresh proxy sticky identity are separate operations.
The reusable implementation is [session_recovery.py](zillow/session_recovery.py);
the example policy is [rotation-rules.json](zillow/rotation-rules.json).

## Default policy

| Condition | Recovery |
| --- | --- |
| Target HTTP 502, 503, or 504 after the normal request attempt budget | Checkpoint the failure, wait 30 seconds, replace the managed session, retry the URL once. |
| Target HTTP 401, 403, or 429, CAPTCHA, or human-verification challenge | Stop without automatic session replacement or proxy rotation. |
| Target HTTP 404 | Record a skipped URL and continue without retrying. After three consecutive skips, replace the managed session before advancing. A successful capture resets this streak. |
| Same URL fails after its recovery retry | Keep it terminal, replace the failed session before advancing to the next URL. |
| Three consecutive qualifying failures without a successful capture | Stop and preserve the checkpoint. |
| Ten session replacements during one process run | Stop and preserve the checkpoint. |
| Explicit CAPTCHA/human-verification page, authentication failure, malformed data, or unrelated application error | Stop for inspection; do not rotate automatically. |
| Another open session uses the same proxy alias | Stop recovery before rotating shared proxy state. |

Read a **target** HTTP status from the exception's `status_code` or REL's
structured `details.target_http_status`. Do not classify REL transport HTTP 403
as a website block: `PRO_REQUIRED`, configuration errors, and other local errors
need their own resolution.

## Replacement sequence

1. Finish the active request and release the crawler's session lease. Keep
   completed captures and queue entries intact.
2. Checkpoint the pending retry with a stable deduplication key and bounded
   recovery counter. Checkpoint the old session ID and replacement settings,
   then clear the active ID before performing replacement effects.
3. Verify that the old session belongs to the crawler's group and Profile.
   Preserve its assigned proxy alias, AdBlock and image-filter settings.
4. Refuse shared-proxy rotation. Close only the old managed session through
   `DELETE /v1/sessions/{id}`.
5. The supplied policy disables proxy rotation. Retain the configured proxy
   and create a fresh browser session/connection only for qualifying service
   failures. Do not claim that replacing a session changes the exit IP.
6. Create a session with the same Profile, group, proxy alias and filtering,
   and an indefinite lifetime. Save its ID and increment the generation.
7. Durably enqueue the recovery retry at the front of the queue. Clear the
   pending recovery only after enqueue succeeds. Resume through normal browser
   navigation and content validation. History-based crawlers must restore their
   source page and readiness before resuming clicks.

Session management uses only the public loopback REL RPC API. Website traffic
stays in REL Chromium. Never invent aliases, edit credentials, switch providers,
solve challenges automatically, or close caller-owned sessions as recovery.

## Checkpoints and interruption

`session.json` stores the session generation, consecutive-failure count,
replacement phases and any pending retry. Replacement sessions use a unique
checkpointed name: after an ambiguous creation outcome, list sessions and adopt
that replacement instead of creating a duplicate. Enqueue must deduplicate the
stable retry key. Completed requests never get a fresh budget implicitly.

`session-rotations.jsonl` records UTC time, old/new IDs, alias, whether REL
rotated the proxy sticky session, and the triggering failure. It contains no
proxy credentials. Only explicit retry-failed operation grants previously
terminal URLs a fresh bounded budget.

Pass a different JSON policy to configure status codes, cooldown, run and
consecutive limits, retries per URL, and supported proxy rotation. Setting
`max_rotations_per_run` to zero disables automatic rotation. Increasing budgets
does not prove that recovery will succeed.
`skip_http_statuses` configures terminal skipped statuses; the default is 404.
`rotate_after_consecutive_skips` sets their rotation threshold, or zero to
continue skipping without session replacement.

The monitored Zillow county crawl uses `--skip-failed`: exhausted resources stay terminal, no session/proxy recovery is scheduled for them, and its persisted failure streak stops on the eleventh consecutive failure. Saved safety stops require explicit resolution.
