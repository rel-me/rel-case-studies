# Restart tax crawl and show dashboard

## User request

> ok restart the tax crawl and show me the dashboard

## Follow-up implementation

The dashboard was down. Starting it and explicitly restarting the saved tax command exposed REL Project API metadata on the existing saved Datacenter Session. Update the resolver to reuse that exact Session with Profile/Project validation; preserve the serial GET-form fallback for the legacy history adapter. Preserve queues, captures, terminal failures, and configured session/profile. No session creation or proxy changes.

## Delegated prompt

Review and monitor the tax restart compatibility PR on branch codex/gabriel/restart-tax-project-session in /Users/gabe/.codex/worktrees/1c8c/rel-case-studies. Verify exact saved REL Session reuse with Profile/Project ownership validation, legacy history fallback, and no session creation or route changes. Run relevant tests, inspect CI, fix necessary failures, commit/push fixes, and re-monitor after any changes. Log any further delegated prompts or user follow-ups in the existing docs/prompts file for this change. Do not stop, restart, navigate, or change live crawlers, sessions, private outputs, or the dashboard server. Report the final PR head and check state.
