# AGENTS.md compression - rule-preservation map

**Review aid, not a permanent file.** This documents the prose-efficiency rewrite of `AGENTS.md` (firstmate's operating constitution) so firstmate and the captain can verify that no enforceable rule, invariant, script name, flag, path, or cross-reference was dropped. Firstmate decides whether to keep or delete this file before merge.

## Word count

- Original `AGENTS.md`: **13,288** words.
- Compressed `AGENTS.md`: **8,438** words.
- Reduction: **36.5%**.

Note on target: the brief asked for 40-50%. The captain reviewed progress and directed acceptance at ~36% rather than trimming further, on the basis that the remaining content is largely irreducible operational tokens (script names, flags, paths, meta fields, status verbs, command tables, the file-tree and backlog-format blocks) and hard rules, and that correctness beats hitting the budget. No rule was cut to chase the number.

## What changed (nothing removed, only re-expressed)

- Every hard rule, invariant, and operational token is retained; prose was tightened by deleting rationale, illustration, and mechanism-narration that restates what a `bin/` script does internally.
- Rules that were stated 3-4 times were collapsed to **one canonical statement + a `section N` cross-reference** (see the dedup table below).
- Section 14 (X mode) mention-handling detail that is already owned by the `fmx-respond` skill (loaded at the `x-mention` wake) was collapsed to a pointer; the firstmate-owned parts (activation, generated artifacts, cadence, wake routing, completion follow-up) stay inline. Every literal token in that block still appears in `AGENTS.md` too (`in_reply_to`, `texts`, `FMX_X_REPLY_MAX_CHARS`, `FMX_X_THREAD_MAX`, `FMX_DRY_RUN`, etc.).
- Section numbering 1-14 is unchanged; no cross-reference was renumbered. `CLAUDE.md` symlink -> `AGENTS.md` is untouched.

## Automated token-preservation check

Diffed original (git HEAD) vs new for these classes; **all present in the new file**:

- All `bin/fm-*.sh` script references (fleet-sync, bootstrap, spawn, update, merge-local, teardown, lock, wake-drain, crew-state, watch, watch-arm, guard, harness, project-mode, send, marker-lib, classify-lib, wake-lib, home-seed, ensure-agents-md, backlog-handoff, pr-check, promote, review-diff, peek, supervise-daemon, x-poll, x-reply, x-dismiss, x-link, x-followup).
- All env vars: `FM_HOME`, `FM_STATE_OVERRIDE`, `FM_ROOT_OVERRIDE`, `FM_ROOT`, `FM_FLEET_PRUNE`, `FM_FLEET_SYNC_BOOTSTRAP_TIMEOUT`, `FM_STALE_ESCALATE_SECS`, `FM_GUARD_GRACE`, `FM_CHECK_TIMEOUT`, `FM_CHECK_INTERVAL`, `FM_INJECT_MARK`, `FM_INJECT_SKIP`, `FMX_PAIRING_TOKEN`, `FMX_RELAY_URL`, `FMX_DRY_RUN`, `FMX_X_REPLY_MAX_CHARS`, `FMX_X_THREAD_MAX`.
- All meta fields: `window=`, `worktree=`, `project=`, `harness=`, `kind=`, `mode=`, `yolo=`, `home=`, `projects=`, `pr=`, `pr_head=`, `x_request=`, `x_request_ts=`.
- All status verbs/states: `needs-decision`, `blocked:`, `failed:`, `done:`, `working:`, `PR ready`, `checks green`, `ready in branch`, `merged`, `awaiting_approval`, `fix_review`, `awaiting_agent`, `checks-passed`, `outcome: passed`, `outcome: failed`, `cancelled`.
- All `tasks-axi` verbs: `add`, `start`, `done`, `update`, `block`, `unblock`, `ready`, `show`, `render`, `mv`, `--version`.
- Section headers `## 1.` through `## 14.` all present, in order, no duplicates.
- Literals: `kunchenguid/no-mistakes #351/#396`, no-mistakes `1.31.2`, tasks-axi `0.1.1`, `done_keep = 10`, `connector/followup`, `24h`, `240s`/`300s`/`600s`/`2h`, `0x1f`, `--lease`, `--force`, `--yes`, `git remote add fork ...`, `in_reply_to`, `texts`, `crew_is_provably_working`.

## Rule-by-rule map (by original section)

Legend: PRESERVED = same rule, tighter words, same section. CANONICAL = kept once, restatements replaced by cross-ref.

### Preamble
- Address captain >=1 per response, incl. bad news; never zero direct address - PRESERVED (preamble).
- Nautical seasoning optional/fit-only; never in commits/briefs/PRs/tool-read text; drop for bad news - PRESERVED (preamble).
- Escalation style -> section 9 - PRESERVED (preamble).

### 1. Identity and prime directives
- Sole point of contact; never does work itself; delegates all project work to crewmate/secondmate - PRESERVED (§1).
- Secondmate = crewmate in isolated firstmate home with charter; same lifecycle - PRESERVED (§1).
- Directive 1: Never write to a project; read-only under `projects/`/worktrees; the five sanctioned exceptions (init §6, fleet-sync `bin/fm-fleet-sync.sh`, local-HEAD secondmate sync via `bin/fm-bootstrap.sh`+`bin/fm-spawn.sh`, self-update `/updatefirstmate`+`bin/fm-update.sh`, `local-only` merge `bin/fm-merge-local.sh`), all fast-forward/guarded; project `AGENTS.md` not an exception - PRESERVED (§1, priority order intact).
- Directive 2: Never merge a PR without captain's explicit word; sole relaxation = `yolo` (§7); destructive/irreversible/security still escalates - PRESERVED (§1).
- Directive 3: Never tear down a worktree with unlanded work; `bin/fm-teardown.sh` enforces; no `--force` unless captain says discard; uncommitted never landed; full "landed" definition + scout carve-out -> §7 - CANONICAL (invariant in §1; full definition canonical in §7 teardown).
- Directive 4: Crewmates never address captain; all comms through firstmate; captain intervention authoritative, reconcile at heartbeat - PRESERVED (§1).
- Directive 5: Report outcomes faithfully, failures with evidence - PRESERVED (§1).
- May write to this repo (backlog/briefs/state/this file w/ approval); operational state `data/`/`state/`/`config/` maintained directly - PRESERVED (§1).
- Shared-tracked material list; tracking principle (gitignored personal set); delegate shared-tracked changes to a crewmate when fleet live, edit directly when empty; repo behind no-mistakes gate, captain merge rule applies; terse commits; never add agent co-author - PRESERVED (§1).

### 2. Layout and state
- `FM_HOME` semantics + `FM_STATE_OVERRIDE`/`FM_ROOT_OVERRIDE` compatibility; each secondmate own persistent `FM_HOME` - PRESERVED (§2).
- Full file/dir tree with every path and its qualifier (gitignored, READ-ONLY, never touch, safe to delete, etc.) - PRESERVED verbatim (§2 code block); a few redundant "LOCAL, gitignored" repeats under `data/` trimmed (the fact is stated by `data/` "gitignored as a whole" and §1 tracking principle).
- Task-id slug format + `fm-<id>` window naming - PRESERVED (§2).

### 3. Bootstrap
- Detect->consent->install; never install unapproved - PRESERVED (§3).
- `bin/fm-bootstrap.sh`; fleet refresh via `bin/fm-fleet-sync.sh` (`FM_FLEET_PRUNE=0`); secondmate-home sweep (local FF, gitignored/dirty/diverged untouched); `FM_FLEET_SYNC_BOOTSTRAP_TIMEOUT` default 20 - PRESERVED (§3).
- Every printed-line handler: `MISSING` (+treehouse `--lease`, no-mistakes `1.31.2`), `NEEDS_GH_AUTH`, `TANGLE`, `CREW_HARNESS_OVERRIDE`, `FLEET_SYNC skipped/recovered/STUCK`, `SECONDMATE_SYNC skipped`, `TASKS_AXI available` (`0.1.1+`), `NUDGE_SECONDMATES` (+the send command), `FMX on/off` - PRESERVED (§3, all bullets).
- Read `data/projects.md` (rebuild if stale), `data/secondmates.md`, `data/captain.md` (canonical over harness memory) - PRESERVED (§3).
- No dispatch until tools+gh auth; `gh-axi`/`chrome-devtools-axi`/`lavish-axi` usage, don't memorize flags; `config/crew-harness` switch - PRESERVED (§3).

### 4. Harness adapters
- Default to own harness; override via `config/crew-harness` (`default`=mirror); per-task override; `bin/fm-harness.sh`/`fm-harness.sh crew` - PRESERVED (§4).
- Mechanics in `bin/fm-spawn.sh`, knowledge in `harness-adapters` skill; never dispatch on unverified adapter; verify-then-commit; load `harness-adapters` at all listed triggers - PRESERVED (§4).

### 5. Recovery
- All 10 numbered steps (lock/`bin/fm-lock.sh`, drain, read state, `window=` live set, missing-window reconcile, reconcile-by-kind, no whole-tree reconstruct, `.afk`/`/afk`, surface-only-what-needs-captain, watcher checklist) - PRESERVED (§5, numbered list intact).
- Restart is a non-event; truth lives in tmux/state/backlog/secondmates/homes/treehouse - PRESERVED (§5).

### 6. Project management
- `data/projects.md` registry line format + rules - PRESERVED (§6).
- `data/secondmates.md` table format; `scope:` intake / `projects:` non-exclusive; load `secondmate-provisioning` before secondmate ops - PRESERVED (§6).
- Secondmate idle-by-default contract (reconcile own work then wait, never self-initiate survey/audit) - PRESERVED (§6; kept inline per secondmate-provisioning's own instruction that routing+idle rules stay in AGENTS.md).
- Hand-off in-scope backlog on creation via `bin/fm-backlog-handoff.sh`; not `local-only`; details -> `secondmate-provisioning` - CANONICAL (§6; handoff internals owned by skill).
- Project-intrinsic vs fleet/captain-private knowledge split; never hand-write project `AGENTS.md`; crewmates commit it via pipeline; `bin/fm-ensure-agents-md.sh`; lazy creation, no eager backfill - PRESERVED (§6).
- Delivery modes `no-mistakes`/`direct-PR`/`local-only` definitions; `+yolo` optional/off/not-recommended; default no-mistakes+yolo-off - PRESERVED (§6).
- Clone existing / create new (GitHub consent, visibility default private) / init (`no-mistakes init && no-mistakes doctor`, section-1 exception, skip for others) - PRESERVED (§6).

### 7. Task lifecycle
- Intake: resolve project (5 ordered signals) - PRESERVED (§7).
- Intake: resolve secondmate scope; `local-only` stays main; `bin/fm-send.sh fm-<id>`; bare-id resolution + `session:window`; from-firstmate marker `bin/fm-marker-lib.sh`; answer via status/doc not chat; unmarked captain chat stays conversational; no direct crewmate for scope work unless blocked/redirected; create-new-secondmate + handoff - PRESERVED (§7).
- Classify shape (Ship/Scout, report at `data/<id>/report.md`) - PRESERVED (§7).
- Classify readiness (Dispatchable/Blocked `blocked-by:`, no concurrency cap, coarse dependency judgment, rebase note) - PRESERVED (§7).
- Spawn: `bin/fm-spawn.sh` all six invocation forms; batch semantics; what the script records/asserts (isolation assert -> §8 tangle); detached-HEAD/branch; secondmate launch + pre-launch FF; peek + trust dialog; add to In flight - PRESERVED (§7).
- Supervise: `bin/fm-send.sh` short lines; secondmate escalation retargeting - PRESERVED (§7; cross-ref §8).
- Delivery modes + yolo: per-mode divergence (no-mistakes/direct-PR/local-only incl. `bin/fm-review-diff.sh`, `bin/fm-merge-local.sh`, `fm-pr-check`); pooled-clone base caveat; yolo off/on with escalation carve-out + FYI - PRESERVED (§7).
- Validate: trigger on `done`; crewmate drives pipeline; wrapper (`ask-user`->`needs-decision`, `no-mistakes axi respond`, avoid `--yes`, CI-green done line) - PRESERVED (§7).
- git-push-to-start workaround (`git push no-mistakes <branch>`, #351/#396, "Everything up-to-date" stale mirror ref) - CANONICAL (§7; §11 references it).
- crew-state judging (`bin/fm-crew-state.sh`, run-step over stale status log, never `tail`, `unknown` rule) + run-step state/outcome table - PRESERVED (§7).
- One-run-no-thrash firm rule - CANONICAL (§7; §11 references it).
- PR ready: `bin/fm-pr-check.sh` (`pr=`/`pr_head=`), full URL never `#number`, risk level, custom `state/<id>.check.sh` contract + `FM_CHECK_TIMEOUT`; merge-on-word / yolo merge+FYI - PRESERVED (§7).
- Ship teardown: `bin/fm-teardown.sh`; full "landed" definition (remote-tracking incl. fork, merged-PR-head/squash-delete flow, content-in-default, local-only merged-to-local-main); uncommitted never landed; refuse rules; gh-error fallback; fork remote+fetch benign case; post-teardown `bin/fm-fleet-sync.sh`+`STUCK`; backlog update (`tasks-axi done` / hand-edit, keep 10); re-evaluate queue - PRESERVED (§7, canonical "landed" home).
- Secondmate teardown explicit-only; persistent default; refuse on in-flight; `--force` discard path - PRESERVED (§7; internals -> `secondmate-provisioning`).
- Scout tasks flow + carve-out + Done record - PRESERVED (§7).
- Promotion `bin/fm-promote.sh` full procedure + clean-base rule + repro-as-regression - PRESERVED (§7).

### 8. Supervision protocol
- Watcher backbone via `bin/fm-watch.sh` through harness-tracked `bin/fm-watch-arm.sh` - PRESERVED (§8).
- Command reference block (`fm-watch-arm.sh` / `--restart` / `fm-watch.sh` / `fm-wake-drain.sh` / `fm-crew-state.sh`) - PRESERVED (§8 code block).
- Wake triage: absorbed set (no-verb signal / non-terminal stale only while provably working via `bin/fm-crew-state.sh` run-step-then-pane; no-change heartbeat; `state/.watch-triage.log`); actionable set (all captain-relevant verbs, no-verb signal from stopped crewmate, any check, terminal/non-provably-working stale, provably-working stale past `FM_STALE_ESCALATE_SECS` 240s, heartbeat fleet-scan backstop); writes to `state/.wake-queue`; `bin/fm-classify-lib.sh` + `crew_is_provably_working`; afk one-shot - PRESERVED (§8; internal marker-file enumeration `.seen-*` etc. remains in the §2 file tree).
- Keep exactly one live cycle; arm only via tracked bg mechanism / own bash / no `&` / no bundling; self-verifying status line (`started`/`healthy`/`FAILED`); cycle-down = WAKE REASON; singleton-safe; `--restart` home-scoped never broad `pkill`; no-turn-ends-blind; silent waiting - PRESERVED (§8, consolidated into one checklist, all rules kept).
- `bin/fm-wake-drain.sh` at start of wake/recovery turns; drained queue lossless - PRESERVED (§8).
- On-wake handling for signal/stale/check/heartbeat (peek `bin/fm-peek.sh`, `stuck-crewmate-recovery`, fleet review) - PRESERVED (§8).
- Terminal-state X follow-up hook -> §14 - PRESERVED (§8).
- Heartbeat backoff (600s->2h, reset); checks-before-signals; tmux ground truth; `kind=secondmate` idle-pane exception (`fm-watch.sh` skips stale-pane) - PRESERVED (§8).
- Liveness guard: `state/.last-watcher-beat`; guard scripts list + `bin/fm-guard.sh`; `state/*.meta`/`FM_GUARD_GRACE` 300s; banner; drain/arm response - PRESERVED (§8).
- Worktree-tangle guard: `FM_ROOT` primary on default branch; restore command; scope (detached/default never alarm); bootstrap `TANGLE` §3; two upstream guards (fm-spawn isolation, ship-brief first instruction §11) - PRESERVED (§8).
- No foreground-block while tasks in flight; crewmate synchronous opposite - PRESERVED (§8).
- Token discipline (`bin/fm-crew-state.sh` order, 40-line peeks, no repeat streaming, batch, ignore context-%, intervene only on real signals) - PRESERVED (§8).
- Away-mode stub: `/afk` triggers; skill owns procedure; all 7 inline must-survive facts (`FM_INJECT_MARK` 0x1f, daemon owns watcher, marked=escalation, `/afk` refresh, unmarked=back+flush `state/.wake-queue`/`.subsuper-escalations`/`.subsuper-inject-wedged`, afk never changes approval authority, bias-to-exit) - PRESERVED verbatim (§8).
- Stuck-crewmate recovery trigger + `stuck-crewmate-recovery` escalation ladder - PRESERVED (§8).

### 9. Escalation and captain etiquette
- Talk in outcomes; full internal-vocabulary ban list - PRESERVED (§9).
- Reaches-captain-immediately list (6 items) and does-not-reach list; batch non-urgent; lavish-axi vs chat; full URL never `#number` rule; cost courtesy >~8 jobs - PRESERVED (§9).

### 10. Backlog format
- `data/backlog.md` format block (In flight/Queued/Done, all three Done line shapes) - PRESERVED verbatim (§10).
- Re-evaluate Queued on teardown/heartbeat with time/date gate - PRESERVED (§10).
- `.tasks.toml`/`done_keep = 10`/`data/done-archive.md`; compatible probe `0.1.1+`; byte-exact in-place edit preserving item forms incl. `- **<id>**`; absent-fallback hand-edit; secondmate inheritance; keep-10/auto-prune vs manual; pruning-loses-nothing - PRESERVED (§10).
- All approved `tasks-axi` command mappings (add/start/done/update/block/unblock/ready/show/render) + handoff-not-`mv` rule - PRESERVED (§10).

### 11. Crewmate briefs
- `bin/fm-brief.sh` scaffold = contract; `{TASK}` replacement rule - PRESERVED (§11).
- Ship-brief worktree-isolation assertion + `blocked:` message (§8 upstream guard) - PRESERVED (§11).
- Mode-shaped definition of done (`fm-project-mode.sh`) - PRESERVED (§11).
- no-mistakes wrapper rules -> detailed in §7 (git-push workaround, one-run-no-thrash, `ask-user`, `--yes`, CI-green) - CANONICAL (§11 references §7).
- Project-memory contract (`bin/fm-ensure-agents-md.sh`) - PRESERVED (§11).
- Scout `--scout` swap; mode-agnostic; no project-memory step - PRESERVED (§11).
- Secondmate charter brief (`--secondmate`, `FM_SECONDMATE_CHARTER`, `FM_SECONDMATE_SCOPE`, idle contract, requests-from-firstmate contract); load `secondmate-provisioning` - PRESERVED (§11).
- Sparse status-reporting protocol - PRESERVED (§11).

### 12. Self-update
- `/updatefirstmate` skill: FF-only self+secondmate update, re-read AGENTS.md, nudge, never touch `projects/` - PRESERVED (§12).

### 13. Agent-only reference skills
- All four load-trigger entries (`harness-adapters`, `stuck-crewmate-recovery`, `secondmate-provisioning`, `fmx-respond`) - PRESERVED (§13).

### 14. X mode
- What/inert-until-opted-in - PRESERVED (§14).
- Activation via `.env` `FMX_PAIRING_TOKEN` = consent incl. standing reversible-action auth; NOT destructive/irreversible/security (trusted-channel first, yolo carve-out §1/§7); `FMX_RELAY_URL` default `https://myfirstmate.io` - PRESERVED (§14).
- Mechanism/artifacts (`state/x-watch.check.sh` execs `bin/fm-x-poll.sh`; `config/x-mode.env` `FM_CHECK_INTERVAL=30`; poll->`state/x-inbox/<request_id>.json`->`x-mention` check wake; 204 silent; `x-mode-error`; opt-out deletes; no edits to watcher backbone/afk daemon) - PRESERVED (§14).
- Cadence 30s/300s; arm-with-source snippet; transition restart snippet; bootstrap never restarts; keep armed w/o fleet work; away-mode out of scope - PRESERVED (§14).
- Wake routing: load `fmx-respond` on `x-mention`; report+don't-load on `x-mode-error`; skill drains every `state/x-inbox/*.json`, `bin/fm-x-reply.sh`/`FMX_DRY_RUN`->`state/x-outbox/`/`bin/fm-x-dismiss.sh` - PRESERVED (§14).
- Completion follow-up (firstmate-owned): `bin/fm-x-link.sh` (`x_request=`/`x_request_ts=`); on terminal wake `bin/fm-x-followup.sh --check <id>` then `<id> --text-file`; `bin/fm-x-reply.sh --followup` `connector/followup` 24h one thread-bound; past-24h skip+clear; public-safety bar; `FMX_DRY_RUN` `endpoint` marker - PRESERVED (§14).
- Skill-owned detail (owner-only routing, three-case classify, autonomous posting, public-safety limits, `--text-file`/stdin, `in_reply_to` context, `texts` thread split, `FMX_X_REPLY_MAX_CHARS`/`FMX_X_THREAD_MAX`, dry-run payloads) - CANONICAL in `fmx-respond` skill (loaded at the wake); the tokens also remain named inline in §14 so nothing depends on the skill to preserve a token. Verified `fmx-respond/SKILL.md` covers all of it.

## Deliberate non-restorations (not rules/tokens)
- Pure-rationale clauses ("this is the biggest time sink", "the terminal makes a URL clickable", "so a wrong guess costs one correction", illustrative walkthroughs of watcher behavior) were dropped. None is an enforceable rule, script, flag, path, or cross-reference.
