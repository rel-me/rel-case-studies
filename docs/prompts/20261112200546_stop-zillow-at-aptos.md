# Stop Zillow upon reaching Aptos

## User request

> lets stop the zillow at just the santa cruz stuff.. stop if we've reach aptos

## Action and verification

Recent saved Zillow captures were already in Aptos (95003). Used the existing private dashboard Stop control for Zillow, then verified its worker was inactive and the run checkpoint retained the user-stop maintenance marker. Recorded the Aptos cutoff reason in the private run metadata. Monitoring must leave the worker stopped until explicitly resumed by the user.

The saved queue, pending targets and captures were preserved. Tax continued running. No crawler implementation changed and no new PR was created for this operational action.

## Delegated prompts

None; no PR or code change required monitoring.
