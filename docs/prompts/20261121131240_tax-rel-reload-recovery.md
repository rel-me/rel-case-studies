# County tax REL configuration reload recovery

## User request

> the tax crawl stops because of some session needs reload thing in REL... i think the client should close and re create a new session if it hits thing like that.. like a retry... but bounded retry only once or twice in a row

## Delegated prompt to GPT-6 Luna

> Monitor the county tax REL reload recovery PR #25 until GitHub checks settle. Review the one-consecutive-replacement cap and add a focused offline regression test for repeated reload if coverage is missing. Diagnose and fix any failures, commit and push fixes to the existing branch, and monitor the revised head again. If no GitHub checks are configured, run the relevant local tests and report that. Do not restart any live crawler, change saved live state, or navigate a live session.

## Follow-up user request

> rel_playwright.\_rpc.RpcError: BROWSER_UNAVAILABLE: Browser configuration changed. Choose Reload in REL to apply it before continuing automation.\
> File "/Users/gabe/.codex/worktrees/1c8c/rel-case-studies/prop13-santa-cruz/scripts/crawl_county_taxes.py", line 461, in search_form
>
> Can we have it continue, recover automaticlly after reloading or creating a new session

## Follow-up delegated prompt to GPT-6 Luna

> Monitor PR #25 at the updated head using GitHub checks, or run the local suite if there are no checks. Review the inline county tax REL recovery and add a focused offline test that the handler retries the same parcel after replacement even after Crawlee's ordinary retry was used, and stops on a repeated reload. Fix failures, push any changes to the current PR branch, and monitor the final head again. Keep live crawler and REL state untouched.

### Delegated follow-ups to GPT-6 Luna

> Live restart reached configuration recovery. REL created replacement Session with identical configuration except it generates a different fingerprint_profile.seed by design, and strict validation rejected it. I will fix that validation in crawler code and add the focused seed assertion to existing replacement test, while preserving the same Project/Profile/proxy and every other fingerprint setting. Please avoid editing that existing test for now, continue your own new offline test, and tell me before committing so we can stage independently. Keep monitoring latest head.

> Seed normalization and existing mock update are in the shared files now. Please run tests and stage/commit/push both of our changes together and monitor the resulting head. I will only perform live checkpoint repair and verification.

> Verify the new documentation-only head of PR #25 and report its GitHub checks. If there are no configured checks, confirm that the code and tests match the last verified head. Keep live state untouched.
