# Shared REL Project for property crawlers

## User request

> "Final error: session_recovery.RelApiError: VALIDATION_FAILED: project_id is required when creating a Session "
>
> Create a project and use that project.. use same project for tax crawl too

## Implementation and operational scope

Create Santa Cruz property study through the supported loopback Project API. Provision two independent indefinite Sessions through the installed REL MCP adapter, using the existing Zillow and Datacenter Profiles. Pause both queues before rebinding saved checkpoints and commands; retain the prior Session/checkpoint backup and all captures, queues, terminal failures, limits, and priorities. The tax worker ignored SIGINT, so terminate only the verified worker between parcels before rebinding; add explicit CLI interrupt setup for future launches. Resume both crawlers using the saved commands and verify new captures in the shared Project. Do not close old caller-owned Sessions or change proxies/credentials.

Add --project-id support to both scripts, include the bound Project on Session creation/replacement, validate ownership, and retain modern history fallback. Legacy compatibility paths remain for old saved states/API versions.

## Delegated prompt

Review and monitor the shared REL crawl Project PR on branch codex/gabriel/shared-rel-crawl-project in /Users/gabe/.codex/worktrees/1c8c/rel-case-studies. Verify explicit Project IDs on creation and replacement, Profile/Project ownership checks, persisted CLI scope for both crawlers, and retained compatibility behavior. Check the inherited SIGINT reset and offline regression tests. Run relevant tests, inspect CI, fix required failures, commit/push fixes, and re-monitor after changes. Append any new delegated prompts or user follow-ups to the existing docs/prompts file. Do not stop, restart, navigate, create, close, or modify live crawlers, Projects, Sessions, private output, or the dashboard server. Report final PR head and check state.


## Review fixes

The delegated review added offline coverage for fresh county Project-session creation, persisted Project reuse, rejection of explicit Project scope on legacy Sessions without Project metadata, and rejection of replacement checkpoints from another or unspecified Project. Fresh county state now supplies its configured Profile/group before resolver validation. These corrections do not change live state or create a Session outside isolated fake-API tests.
