# Dashboard crawler identity

## User request

> the zillow crawl seems stuck?

## Finding and change

Zillow had stopped at its saved eleven-failure limit. The operating system reused its old PID for an unrelated process, and dashboard PID-existence checks falsely reported Running. Validate the process command against the expected worker script and exact private output path. Preserve queue-lock checks and the saved safety stop. No Zillow restart is authorized by this request.

## Delegated prompt

Review and monitor the dashboard worker-identity PR on branch codex/gabriel/dashboard-worker-identity in /Users/gabe/.codex/worktrees/1c8c/rel-case-studies. Verify that recycled PIDs and other output queues cannot appear as active crawlers, valid launcher and wrapped tax processes still work, and safety stops remain intact. Run relevant tests and inspect CI; fix necessary failures, commit/push, then re-monitor any changed PR. Record any further user/delegated prompts in the existing docs/prompts file. Do not signal, restart, navigate, or modify live crawlers, sessions, private output, or the dashboard server. Report the reviewed head and final checks.
