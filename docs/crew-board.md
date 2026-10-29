# Crew board and pane labels

Firstmate keeps `data/board.md` as a compact, local view of work, with a persistent terminal pane beside firstmate in Herdr.
The board is a presentation of the canonical fleet snapshot used by bearings, including structured backlog decisions and the registered secondmate summaries.
It makes no additional GitHub calls and does not authorize an action or change task state.

The existing supervision loop and status-surfacing path refresh the presentation automatically.
The board viewer also checks for changes between supervision cycles, with a 30-second backstop for CI or secondmate state that changes without a local status event.
Run `bin/fm-board.sh refresh` from the firstmate pane to initialize or restore it immediately.
Pane operations safely do nothing on other backends; `bin/fm-board.sh render` can still generate the Markdown file there.
Python 3 with its standard curses module is required for the terminal viewer.

## Reading the labels

Each live local Herdr worker pane has its task id followed by a state label.
`WORKING` and `CI RUNNING` distinguish implementation from checks.
`NEEDS ANSWER` means a question, review, or approval needs attention.
`READY: merge?` requires reconciled green-check evidence; `READY: apply + merge?` also identifies an unapplied production database change explicitly reported with that evidence.
An opened PR alone never claims green checks.
`PAUSED` is a declared external wait, `BLOCKED` needs intervention or a current-state check, and `DONE` is completed work.
A structured open decision takes precedence over a completion label unless the canonical state owner has already resolved it.

Questions use the `needs-answer` notification class, separately configurable from the `pr-ready` review or merge tag in [notification configuration](configuration.md#producer-tag-notifications-confignotify).

## Reading the board

Groups are Continuo, Cadenza, ISP, AAA, MCA website, Firstmate, and Other.
Continuo further groups work into multi-firm programme, gaps and competitor features, compensation, onboarding/legal, and other.
Each nonempty group shows the relevant sections: Waiting on the captain, Running, Done recently, and Parked with a bring-back trigger.
Completed backlog entries stay visible for the current UTC date and the preceding two dates.
Unconfirmed or incomplete worker summaries ask for a state check rather than silently claiming there is no work.
PR entries include their full HTTPS URL on a separate wrapped line.

The terminal view uses colour for headings, wraps to the pane width, and scrolls with `j`/`k`, arrow keys, Page Up/Page Down, Home, and End.
It updates the terminal's changed cells rather than clearing and repainting the display.
The adjacent split stays in firstmate's current tab without taking focus.
Repeated refreshes reuse the same home-specific pane; closing it allows a subsequent supervision refresh to recreate it.
If that pane was moved to another tab, refresh refuses to create a duplicate; return it beside firstmate or close it for recreation.
This is a persistent split, not a separate workspace or an Obsidian page.
The adapter verifies firstmate's live pane, tab, workspace, and session socket before creating anything.
Missing or contradictory identity refuses placement instead of guessing by workspace label.
The viewer environment is stored in a generated mode-0700 script under `state/`, and only its short launch command is sent after the shell prompt is ready.

## Local group mapping and notes

Create `config/board-groups.json` in the operational home to override grouping.
Like other `config/` files, it is private and gitignored.
Custom rules run first, in order, before the built-in defaults.
The exact schema and matching rules belong to the header of `bin/fm-board.py`.
For example:

```json
{
  "rules": [
    {"repo": "mcatamney-site", "group": "MCA website"},
    {"repo": "continuo", "match": "allowances", "group": "Continuo", "category": "compensation"}
  ]
}
```

Invalid configuration leaves the existing board intact and prints a diagnostic when refreshed manually.
Run `bin/fm-board.sh --help` for rendering and offline snapshot-preview commands.
Set `FM_BOARD_DISABLED=1` to suppress the board helper, including its automatic calls.

Add personal lines under the final `## My notes` heading in `data/board.md`.
Regeneration preserves that section verbatim, including indentation and blank lines.
Everything above that heading is generated.
The board file is replaced atomically, unchanged output is not rewritten, and an edit detected during generation causes that refresh to stop and retry later.
