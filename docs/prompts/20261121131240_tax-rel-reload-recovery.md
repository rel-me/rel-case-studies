# County tax REL configuration reload recovery

## User request

> the tax crawl stops because of some session needs reload thing in REL... i think the client should close and re create a new session if it hits thing like that.. like a retry... but bounded retry only once or twice in a row

## Delegated prompt to GPT-6 Luna

> Monitor the county tax REL reload recovery PR #25 until GitHub checks settle. Review the one-consecutive-replacement cap and add a focused offline regression test for repeated reload if coverage is missing. Diagnose and fix any failures, commit and push fixes to the existing branch, and monitor the revised head again. If no GitHub checks are configured, run the relevant local tests and report that. Do not restart any live crawler, change saved live state, or navigate a live session.
