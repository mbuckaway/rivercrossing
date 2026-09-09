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
| `docs-md/` | The full doc set as markdown — **the canonical text for coding agents, and the only copy kept current.** |
| `docs-html/` | The same documents as browsable HTML, richer layout — **a mirror, not a source, and stale as of EPIC 1**: it predates the amendments to the wxPython baseline, the XRC-authoring limits, the test-driving mechanism and the platform gate. Read it for layout; where it differs from `docs-md/`, the markdown is right. Re-rendering flows from Claude Design, not from hand edits. |
| `epic-prompts/` | **Nine paste-ready Claude build prompts** (one per EPIC) + `README.md` index with order and entry gates. Paste one into a fresh coding session; it carries the role, the read list, the tasks, the TDD ground rules and the exit criteria. |
| `screenshots/windows/` | JPG of each of the original 23 EPIC-1 window designs, named by XRC name (`main_frame.jpg`, `ride_setup_dlg.jpg`, …). **Not maintained for windows added since** — `exit_confirm_dlg`, `duplicate_ride_dlg`, `reopen_ride_dlg`, `void_card_confirm_dlg`, `team_editor_dlg`, `add_team_dlg`, `add_rider_dlg`, `rider_issues_dlg` and the retired `no_ride_dlg`/`stop_confirm_dlg`/`continue_or_new_dlg` have no JPG here, and retired windows keep theirs; **§15b's registry is the authoritative window list**, not this folder. |
| `exports/` | The two golden results pages. Their embedded `race-data` JSON blocks are the export test fixtures. |
| `templates/` | Production Jinja2 templates + frozen Tailwind source — **ship verbatim** into `src/rivercrossing/htmlexport/templates/`. |
| `assets/cards/` | 53 card bitmaps (24×32 plus 48×64 `-2x`) + contact sheet. Mono steel palette; hearts/diamonds in steel, no red. |
| `assets/sounds/` | The three console cue WAVs: `recorded` (70 ms tick), `flagged` (280 ms two-tone), `error` (300 ms buzz). |
| `REVIEW.md` | Findings from the final cross-document audit and how each was resolved. |

## Document roles
- **Build contract (implement FROM these):** `requirements.md` (numbered R-ids, the acceptance authority — 63 rows through R-85) · `spec.md` (§1–§15b engineering spec) · `xrc-windows.md` (every frozen window with its XRC names — implementation truth for UI; §15b's registry is the authoritative window count, 30 top-level windows at W15) · `module-skeletons.md` (repo layout, module APIs) · `project-plan.md` · `task-briefs.md`.
- **Retired:** `ui-designs-retired.md` — early hi-fi exploration, flow/history reference only. Do not implement its visuals or control names.
- **Ship verbatim:** `templates/`.

## Pinned environment (not a framework choice)
Python 3.14 · wxPython ~=4.3.1 (wxWidgets 3.3.3, cp314 wheels; it supplies `wx.App.SetAppearance`, so dark mode is live on both platforms) · all UI loaded from sizer-based XRC · stdlib sqlite3 (WAL, event-sourced) · Jinja2 (HTML export) · fpdf2 (PDF). No `wxasync`: it cannot be torn down on this stack (`spec.md` §10), and the wx⇄asyncio integration is chosen in EPIC 5 with the async writer. Windows 10/11 and macOS 13+; CI builds runnable bundles for both from EPIC 1 — macOS is the blocking gate until a Windows test machine exists (§14, R-75).

## Non-negotiables
- **XRC-first.** Every window, dialog, menubar and panel is authored in XRC and loaded from it — except the three classes whose XRC handlers drop or force the control name (wxInfoBar, wxDataViewListCtrl, wxMenuBar), listed with their code-side replacements in `spec.md` §15b. Snake_case names in §15b are frozen — tests find widgets by them; `ids.py` is generated (203 constants at W15 — 30 top-level windows, 43 `mi_*` items, 130 controls, per §15b) and drift fails CI.
- **Native look.** Standard controls are never restyled; no absolute positioning, no custom chrome. The screenshots are HTML approximations — real windows wear native platform chrome.
- **TDD.** Tests are written first by the `tdd-python-writer` agent; ≥90% line and branch coverage on core modules; ruff + mypy strict.
- **No invented facts.** If a document is silent, ask.

## Known gaps
- ~~Three `spec.md` §15 routes had no frozen window: Duplicate Ride…, Reopen Ride and the Void Card… confirm, citing the retired hi-fi "3d pattern".~~ **Closed** — Duplicate Ride… and Reopen Ride were authored mock-first in E5.4.1 (`duplicate_ride_dlg`, `reopen_ride_dlg`) and Void Card… in E7 (`void_card_confirm_dlg`); no §15 route remains on the flagged sentinel (spec §15).
- ~~**Deck-count default** was unresolved (spec §4 says 8 decks, the canvas drew 2).~~ **Closed** — the XRC declares no value and the presenter supplies 8 (E3.5 ride-setup work, spec §4); the ride-setup lap-length field gained the same presenter-supplied default (8.0, W4). `requirements.md`'s open questions list is empty: all resolved.
- `screenshots/windows/` is the EPIC-1-era set — windows added since EPIC 1 have no JPG, retired windows keep theirs, and `about_dlg.jpg` shows the blank logo slot the never-blank logo rule (R-82, ux-polish) eliminated in code. The JPGs are layout reference only; §15b's registry is the window authority.
- Card bitmaps and WAV cues are production-ready starters; tasks E1.3.2 and E4.4.3 commit the generator scripts and may regenerate them — keep the file names.
