# EPIC build prompts — how to use these

Nine paste-ready prompts, one per EPIC. Each is self-contained: it tells Claude what it is building, which files in `design/` to read first, the phases and tasks in order, the binding ground rules (TDD, coverage gates, frozen XRC names, native look), how to run the session, and the exit criteria that end the EPIC.

## Usage
1. Start a fresh Claude Code session in the repository root with this `design/` folder present (or attached).
2. Open the prompt for the EPIC you are on and paste everything below its `---` divider.
3. Let it work task by task — one PR per task, tests first. Do not run two EPICs in one session.
4. When the definition of done is met, start a new session with the next prompt.

## Order and dependencies
| # | Prompt | Entry gate |
| --- | --- | --- |
| 1 | `EPIC-1-runnable-ui-shell-d1.md` | none — start here (**D1**: whole UI on both OSes, demo data) |
| 2 | `EPIC-2-deal-score-engine.md` | EPIC 1 done |
| 3 | `EPIC-3-roster-csv.md` | EPIC 1 done — may run in parallel with 2 |
| 4 | `EPIC-4-live-ride-in-memory.md` | EPICs 2 + 3 done |
| 5 | `EPIC-5-persistence-crash-recovery.md` | EPIC 4 done |
| 6 | `EPIC-6-results-publishing.md` | EPICs 2 + 5 done (its CSS build step may start after 1) |
| 7 | `EPIC-7-corrections-audit.md` | EPIC 5 done |
| 8 | `EPIC-8-settings-assistance.md` | EPIC 5 done — may run in parallel with 6 and 7 |
| 9 | `EPIC-9-packaging-release.md` | all others done; needs Apple Developer ID credentials |

## If you only have one session
Run EPIC 1. It is the first deliverable on its own: every window, menu and dialog running on Windows and macOS with demo data behind a removable seam — enough to click through and validate the whole UI before any engine exists.
