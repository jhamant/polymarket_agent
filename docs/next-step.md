# Next Step

## Recommended Immediate Step

Add the first real evidence connector inside the new document-first Codex CLI workflow.

The runner and file contract now exist. The next leverage point is making one family-specific research path produce real evidence instead of only local-document analysis.

## Why This Step Comes First

- The stage contract is now in place, so new capability should plug into that contract instead of changing the architecture again.
- Research is still conservative because the specialist memos mostly reason from local artifacts and missing-evidence notes.
- The highest-value improvement is to give one market family a real external evidence document that later stages can cite.

## What To Build Next

Pick one market family and add one real evidence artifact to the run sequence:

1. Choose one market family with a clean official evidence path.
2. Add a stage artifact such as `04-evidence-<family>.json` or `04-evidence-<family>.md`.
3. Update the relevant specialist prompt to read that evidence artifact directly.
4. Update the research aggregate memo to distinguish local artifact evidence from external evidence.
5. Keep execution dry-run.

## Concrete Deliverables

- one family-specific evidence artifact written into the run folder
- one specialist prompt updated to consume that artifact explicitly
- one dry run showing the new evidence document referenced by research, assessment, and review
- minimal validation that blocks non-`HOLD` actions when the evidence artifact is stale or missing

## Exit Criteria

The next step is done when:

- the chosen family has a real external evidence artifact inside the run folder
- the specialist memo cites that artifact directly
- the aggregate research memo distinguishes real evidence from missing evidence
- assessment and review can explain their stance by pointing to the evidence file
- non-`HOLD` trades are still blocked when the evidence path is incomplete

## Suggested Sequence

1. Choose the first family to operationalize.
2. Add one deterministic evidence fetch or evidence-writing step for that family.
3. Place the evidence artifact in the run folder.
4. Update the matching specialist prompt and aggregate research prompt to use it.
5. Review several dry runs before touching authenticated trading.
