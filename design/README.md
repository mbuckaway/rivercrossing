# RiverCrossing — design package

RiverCrossing is a keyboard-first timing and scoring desktop application for poker-run rides (built for the GORBA EPIC & MTB Festival, not limited to it). The operator types a rider's plate number + Enter at each lap crossing; the app records the lap, deals a card from a virtual multi-deck shoe, and at the finish scores every entry's best 5-card poker hand (jokers wild), applies tie-breaks, and publishes results as a self-contained HTML page, PDF report, podium poster and CSV. It survives crashes, accidental stops and restarts without losing a keystroke.

This folder is the complete build contract. A developer or coding agent who was not part of the design conversation should be able to build v1.0 from here alone.

## Start here
1. `epic-prompts/README.md` — how to use the build prompts, and the order to run them.
2. `epic-prompts/EPIC-1-runnable-ui-shell-d1.md` — paste everything below its divider into a fresh Claude Code session to start building.
3. `docs-md/project-plan.md` — EPIC overview, working method, coding standards, UI-test tooling verdicts, traceability, risks.
4. `docs-md/task-briefs.md` — ~60 agent-ready briefs (tests first, named files and cases).

**First deliverable (D1)** = EPIC 1: the whole UI running on Windows *and* macOS with demo data behind a removable seam — no engine, no database.

## What is in here

| Folder | Contents |
| --- | --- |
| `docs-md/` | The full doc set as markdown — **the canonical text for coding agents.** |
| `docs-html/` | The same documents as browsable HTML (identical content, richer layout). Open in any browser. |
| `epic-prompts/` | **Nine paste-ready Claude build prompts** (one per EPIC) + `README.md` index with order and entry gates. Paste one into a fresh coding session; it carries the role, the read list, the tasks, the TDD ground rules and the exit criteria. |
| `screenshots/windows/` | JPG of each of the 23 window designs, named by XRC name (`main_frame.jpg`, `ride_setup_dlg.jpg`, …). |
| `exports/` | The two golden results pages. Their embedded `race-data` JSON blocks are the export test fixtures. |
| `templates/` | Production Jinja2 templates + frozen Tailwind source — **ship verbatim** into `src/rivercrossing/htmlexport/templates/`. |
| `assets/cards/` | 53 card bitmaps (24×32 plus 48×64 `-2x`) + contact sheet. Mono steel palette; hearts/diamonds in steel, no red. |
| `assets/sounds/` | The three console cue WAVs: `recorded` (70 ms tick), `flagged` (280 ms two-tone), `error` (300 ms buzz). |
| `REVIEW.md` | Findings from the final cross-document audit and how each was resolved. |

## Document roles
- **Build contract (implement FROM these):** `requirements.md` (numbered R-ids, the acceptance authority) · `spec.md` (§1–§15b engineering spec) · `xrc-windows.md` (all 23 windows, frozen XRC names — implementation truth for UI) · `module-skeletons.md` (repo layout, module APIs) · `project-plan.md` · `task-briefs.md`.
- **Retired:** `ui-designs-retired.md` — early hi-fi exploration, flow/history reference only. Do not implement its visuals or control names.
- **Ship verbatim:** `templates/`.

## Pinned environment (not a framework choice)
Python 3.14 · wxPython ~=4.2.5 (wxWidgets 3.2 stable; 4.3 / wx 3.3 is the pinned dark-mode upgrade path) · all UI loaded from sizer-based XRC · stdlib sqlite3 (WAL, event-sourced) · Jinja2 (HTML export) · fpdf2 (PDF) · wxasync. Windows 10/11 and macOS 13+; CI builds runnable bundles for both from EPIC 1.

## Non-negotiables
- **XRC-first.** Every window, dialog, menubar and panel is authored in XRC and loaded from it. Snake_case names in `spec.md` §15b are frozen — tests find widgets by them; `ids.py` is generated and drift fails CI.
- **Native look.** Standard controls are never restyled; no absolute positioning, no custom chrome. The screenshots are HTML approximations — real windows wear native platform chrome.
- **TDD.** Tests are written first by the `tdd-python-writer` agent; ≥90% line and branch coverage on core modules; ruff + mypy strict.
- **No invented facts.** If a document is silent, ask.

## Known gaps
- `screenshots/windows/about_dlg.jpg` shows a blank logo slot (the GORBA logo is an external fetch in the design doc). Layout is otherwise exact.
- Card bitmaps and WAV cues are production-ready starters; tasks E1.3.2 and E4.4.3 commit the generator scripts and may regenerate them — keep the file names.
