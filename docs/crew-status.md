# Crew status reports

`bin/fm-brief.sh` owns the generated status protocol, terminal field format, and delivery-specific definitions of done for ship, scout, and secondmate work.
Run its `--help` for scaffold options.
Terminal reports explain the delivered outcome or exact outstanding request, the PR and observed check state, and the next action with its responsible owner.
A worker waiting for CI continues watching or reports why observation must be handed off; an open PR with running checks is not evidence of green CI.
An explicit decision report asks a concrete question with options and a recommended default, and a blocker names the missing input and who must provide it.

`bin/fm-classify-lib.sh` owns field parsing and the existing keyed decision fold.
Legacy free-text events remain valid, while malformed, incomplete, duplicated, or unsupported terminal fields remain prose rather than structured evidence.
`bin/fm-crew-state.sh` exposes parsed terminal fields only when the status log is the reconciled current-state source.
A busy worker or an authoritative pipeline result does not inherit stale terminal evidence from an old report.
`bin/fm-fleet-snapshot.sh` retains both the historical event fields and reconciled current evidence without treating an append-only event log as current state.

The [crew board](crew-board.md) uses those structured fields before prose and confirms delivery state through its forge cache.
The forge result wins over a worker's older reported CI result.
Terminal fields do not close unrelated keyed decisions, authorize a merge, or change the supervision continuation for any harness or runtime backend.
Regression entry points are `tests/fm-crew-state.test.sh`, `tests/fm-brief-modes.test.sh`, and `tests/fm-board.test.sh`.
