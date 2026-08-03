# RiverCrossing — Requirements

*Requirements · v1.0 · July 23 2026 · binding for implementation*

MUST = release blocker · SHOULD = v1 target
Trace: spec § / [XRC window](xrc-windows.md)

Each requirement is testable and traces to the [engineering spec](spec.md) (§) and the [XRC window designs](xrc-windows.md) (window ids) — the artifacts code is built from; the retired [hi-fi designs](ui-designs-retired.md) are history only, not required reading for the build. "The app works the first time" is enforced by R-70…R-77: nothing ships unless the full acceptance race passes on both OSes in CI.

### 1 · Platform & runtime

| ID | Level | Requirement | Trace |
|---|---|---|---|
| R-01 | MUST | Runs on Windows 10/11 and macOS 13+ from installers: unsigned Inno Setup .exe (no Windows cert — SmartScreen step documented in the guide); Developer-ID-signed, notarized .dmg. One codebase, Python 3.14 + wxPython 4.3.1 (wxWidgets 3.3.3 — cp314 wheels, and the release that supplies wx.App.SetAppearance, R-03). | §10, §14 |
| R-02 | MUST | Async UI: the plate entry field never blocks — every DB write, export and import runs off the UI loop (single async writer; the wx⇄asyncio integration is chosen in EPIC 5, where that writer appears — `wxasync` is ruled out per §10). | §10 |
| R-03 | MUST | Light and dark themes from one token table; follows the OS by default with manual override (System/Light/Dark radios) — all three live on both platforms, since the 4.3.1 baseline supplies wx.App.SetAppearance: no capability check, no disabled radio. Native controls, never restyled. | §10 · [mainframe](xrc-windows.md)/[settingsdlg](xrc-windows.md) |
| R-04 | SHOULD | Per-monitor DPI awareness on Windows; View menu text zoom 90–150%. | §13 |
| R-05 | MUST | **XRC-first UI generation:** every UI artifact — window, dialog, menubar, panel — is authored in sizer-based XRC resources and loaded from them (native controls, resizable, no absolute positioning); code never builds layout programmatically except the documented code-side items (DataView columns/rows, imagelists, InfoBar construction and text, window minimum sizes, radio menu-item defaults, state enabling) and the three classes XRC cannot name at all — wxInfoBar, wxDataViewListCtrl, wxMenuBar (§15b). Snake_case XRC names are the canonical control registry per §15b, stock IDs for standard buttons; ids.py is generated from the .xrc files and drift fails CI. | §15b · [XRC canvas](xrc-windows.md) |

### 2 · Rides & configuration

| ID | Level | Requirement | Trace |
|---|---|---|---|
| R-10 | MUST | One SQLite database holds many rides; each ride stores name, date, venue, course, lap length, organizer, scorer, logo, planned start, duration, minimum lap time. | §2 · [setupdlg](xrc-windows.md)/[librarydlg](xrc-windows.md) |
| R-11 | MUST | Entry mode per ride: **solo-only (default)** or solo + teams. Team UI is fully hidden in solo-only rides. | §1 · [setupdlg](xrc-windows.md)/[ridereditor](xrc-windows.md) |
| R-12 | MUST | Max riders per team is a per-ride number entry, 2–10, default 4. | §1/§2 · [setupdlg](xrc-windows.md) |
| R-13 | MUST | Shoe config per ride: deck count and jokers per deck (0/2/4, jokers wild); optional card cap X; laps past the cap still count. | §4/§5 · [setupdlg](xrc-windows.md) |
| R-14 | MUST | Tie-break order (① laps ② total time ③ high-card draw) set at ride setup, draggable, and changeable after the finish with instant re-ranking. | §5 · [setupdlg](xrc-windows.md)/[resultsframe](xrc-windows.md) |
| R-16 | MUST | Mixed rides choose a plate model: rider plates pooled to the team (**default** — each rider draws against their own unique plate, uncapped: one card per lap for as many laps as they ride; the team hand scores from the pooled cards, with the optional ride-level cap X applying to the pooled total) or team plate (relay — the EPIC's format). | §1/§2 · [setupdlg](xrc-windows.md) |
| R-17 | MUST | Rider-pooled rides remain editable while running: riders move between teams with their plate, crossings and cards; every move audit-logged. Relay rides keep the start lock. | §3/§7 · [entrydetail](xrc-windows.md) |
| R-18 | MUST | Ride library offers Delete: type the ride's name to confirm, automatic backup written first, never available on a RUNNING ride. | §3 · [librarydlg](xrc-windows.md)/[deletedlg](xrc-windows.md) |
| R-15 | MUST | Rides are duplicable (setup + roster, no timing data). Entries and teams are freely editable and deletable only until the start; after start, DNF/void only — nothing with recorded data is ever deleted. | §3 · [ridereditor](xrc-windows.md)/[librarydlg](xrc-windows.md) |

### 3 · Riders, teams & CSV

| ID | Level | Requirement | Trace |
|---|---|---|---|
| R-20 | MUST | Rider editor: add/edit solo entries and teams (plate, name, members); plate unique per ride, prefilled with next free. | §2 · [ridereditor](xrc-windows.md) |
| R-21 | MUST | CSV import and export per the §7 column spec (solo and teamN forms, rider_1…rider_N). Import previews counts + conflicts and writes nothing until confirmed; re-import reshapes teams freely until start. | §7 · [csvdlg](xrc-windows.md) |
| R-22 | MUST | Team relay model: one plate per entry, one rider on course at a time; crossings may record which member rode the lap. | §1/§2 · [entrydetail](xrc-windows.md) |

### 4 · Timing console

| ID | Level | Requirement | Trace |
|---|---|---|---|
| R-30 | MUST | Start by button or by setting the start time after the fact; elapsed/remaining derive from wall clock, never an in-memory timer. | §3 · [mainframe](xrc-windows.md)/[setstartdlg](xrc-windows.md) |
| R-31 | MUST | Crossings recorded by typing plate + Enter; feedback (rider, lap, lap time, card) within 100 ms perceived; audio cues per spec; focus stays in the entry field. | §6/§10/§13 · [mainframe](xrc-windows.md) |
| R-32 | MUST | Live feed shows the latest 20–30 crossings (time, plate, entry/rider, lap, lap time, total, card), newest first, plus counters (crossings, cards, on-course, shoe). | [mainframe](xrc-windows.md) |
| R-33 | MUST | Corrections: one-keystroke undo of the last crossing; edit/void any crossing; reassign plate; manual card deal/void; DNF. Every correction audit-logged with reason; undo is a compensating write. | §6 · [editcrossing](xrc-windows.md)/[reassigndlg](xrc-windows.md)/[dnfdlg](xrc-windows.md) |
| R-34 | MUST | Crossings faster than the ride's minimum lap flag for review and hold their card until confirmed/voided/reassigned. | §6 · [mainframe](xrc-windows.md) |
| R-35 | MUST | Stop is three deliberate acts: Arm checkbox enables the Stop button; Stop opens a confirm; only confirming stops. Arm auto-clears after use or 10 s. | §3 · [mainframe](xrc-windows.md)/[stopdlg](xrc-windows.md) |
| R-36 | MUST | Console renders all four states — DRAFT / RUNNING / FINISHED / REOPENED — per §13 (entry disabled outside RUNNING; REOPENED is corrections-only with edits highlighted). | §13 · [mainframe](xrc-windows.md) |
| R-38 | MUST | Read-only audit trail viewer (Ride ▸ Audit Trail…): when · who · action · entry · reason, newest first, filterable by entry/action; Entry detail deep-links pre-filtered. | §3 · [auditdlg](xrc-windows.md) |
| R-37 | MUST | Settings "Hide times on the console" — toggleable while a ride runs; lap/total columns and per-crossing times disappear, the closing-window clock stays, times keep recording underneath. | §13 · [mainframe](xrc-windows.md)/[settingsdlg](xrc-windows.md) |

### 5 · Cards & poker scoring

| ID | Level | Requirement | Trace |
|---|---|---|---|
| R-40 | MUST | Each completed lap deals one card from a seeded, shuffled multi-deck shoe; the deal is deterministic and replayable from the stored seed; empty shoe reshuffles with an audit entry. | §4 |
| R-41 | MUST | Standings rank entries by best 5-card hand from all held cards, jokers fully wild, five of a kind above royal flush; ranking table per §5 stored locally. | §5 |
| R-42 | MUST | The hand algorithm handles 0–2+ jokers and any card count up to the cap; whole-field evaluation (180 entries × 12 cards) completes in under 1 s. | §5/§11 |
| R-43 | MUST | Identical hands resolve by the configured tie-break order; unresolved ties are flagged "draw required", never silently ordered. | §5 · [resultsframe](xrc-windows.md) |
| R-44 | MUST | Evaluator self-test (7,462 ranks + joker vectors) runs at launch and on demand from Help; failure blocks finishing a ride. | §12 · [selftestdlg](xrc-windows.md) |

### 6 · Resilience

| ID | Level | Requirement | Trace |
|---|---|---|---|
| R-50 | MUST | Every crossing/card/edit commits to SQLite (WAL) as it happens; a crash or power loss loses at most the uncommitted keystroke. | §2/§9 |
| R-51 | MUST | Closing the app with a ride running is caught with the exit dialog; quitting keeps the ride running on wall time. | §3 · [exitdlg](xrc-windows.md) |
| R-52 | MUST | On launch with a running ride, a resume dialog always appears; session bookkeeping distinguishes clean quit from crash and words the dialog accordingly. Continuing preserves start time and all data. | §2/§3 · [resumedlg](xrc-windows.md) |
| R-53 | MUST | Start pressed on a ride with data asks continue-vs-new; continue loses no time or data; "new" archives first. | §3 · [continuedlg](xrc-windows.md) |
| R-54 | MUST | Automatic backups on open + hourly while running (keep 20); manual Back Up Now; restoring a backup is documented in the guide. | §2 · [settingsdlg](xrc-windows.md) |

### 7 · Results & publishing

| ID | Level | Requirement | Trace |
|---|---|---|---|
| R-60 | MUST | Results are computable the moment the ride finishes: top 10 by hand with card graphics, full field, DNFs marked. | §5 · [resultsframe](xrc-windows.md) |
| R-61 | MUST | HTML export: one self-contained file rendered with Jinja2 (autoescape, StrictUndefined; base template + macros) from frozen payload dataclasses — Tailwind 4 CSS compiled + inlined, results JSON embedded with </ escaped as <\/, logo base64, zero script logic — static markup renders with JS disabled and the JSON block is the machine-readable record; golden-file + JSON round-trip tests, zero external references (CI-checked) that opens from file:// with no network; flags for times, laps board, time board, full field, all cards drawn (every card per entry, with the drawing rider on pooled rides); the light template is the only published look — no theme option. Page CSS is compiled once at package build (Tailwind CLI in CI against the frozen template) and vendored with base64 font subsets — no Node/CDN at export or runtime; markup is plain Tailwind utilities + the frozen custom classes (§8), **no component library** (the page is read-only; daisyUI is the pre-approved CSS-only fallback if v2 adds interactive widgets). | §8 · [sample](../exports/epic-2026-results.html)/[no-times](../exports/epic-2026-results-no-times.html) |
| R-62 | MUST | PDF export via fpdf2 with the same sections and flags; deterministic output; optional one-page podium poster. | §8b · [5a–5d](ui-designs-retired.md) (design doc) |
| R-63 | MUST | Times appear in published results only when the export setting says so — hidden by default, no toggle on the page, and with times off the time data is not embedded at all; laps/time leaderboards are opt-in. | §8 · [resultsframe](xrc-windows.md) |
| R-64 | SHOULD | Finished rides reopen into a corrections-only REOPENED state (clock closed, add-at-time/edit/void, moves on pooled rides); Finish again re-locks; stale exports are flagged. | §3 · [resultsframe](xrc-windows.md) |

### 8 · Quality, testing & CI — "works the first time"

| ID | Level | Requirement | Trace |
|---|---|---|---|
| R-70 | MUST | TDD: tests written first (tdd-python-writer agent), red→green→refactor, module by module in dependency order. | §12 |
| R-71 | MUST | Core logic in pure-Python modules with zero wx imports; coverage ≥ 90%; ruff + mypy strict clean. | §11 |
| R-72 | MUST | Card algorithm verified by known vectors, Hypothesis property tests, brute-force cross-check, and seeded whole-ride simulations across entry modes, joker counts and caps. | §12 |
| R-73 | MUST | Every entry box, button, radio, menu item and dialog is reachable and drivable by the functional harness via stable snake_case XRC names — FindWindowByName for windows and controls, the loaded menubar for `mi_*` items, whose names XRC does not apply to the control (§15b); the menu-coverage test walks all §15 routes in all ride states. | §12/§15 |
| R-74 | MUST | Acceptance: a scripted full race (min lap lowered — an ordinary ride setting) runs end-to-end through the real UI incl. stop/continue, kill+relaunch, quit+relaunch, finish, reopen → correct → finish again, exports parsed and asserted — 100% pass on both OSes before any release. | §12/§14 |
| R-75 | MUST | CI per §14: GitHub Actions windows-latest + macos-latest (real desktop sessions — no virtual display), 6 stages, screenshot-on-failure artifacts, built-binary smoke test, signed installers on tags. **Temporary deviation:** macOS is currently the hard gate and windows-latest runs advisory, because no Windows test machine is available to act on a failure; the requirement keeps its MUST and the gate is restored to both platforms as soon as one exists. | §14 |
| R-76 | MUST | Dialog behavior per §13: Esc cancels, Enter = default, destructive confirms focus Cancel, focus returns to opener; asserted per dialog by the harness. | §13 |
| R-77 | SHOULD | Nightly seeded acceptance race; failures file the seed for exact reproduction. | §14 |

### Out of scope — v1

Sponsor strips on published results (v2) · age/category fields — excluded by decision, must not appear or be asked for anywhere · hardware timing (RFID/chip) · networked multi-operator scoring · e-bike/waiver policy enforcement (recorded fields only).

### Open questions

**Deck-count default.** Spec §4 states 8 decks (432 cards for a 180-entry field); the XRC canvas draws 2 in `decks_spin`. Unresolved, and deliberately not picked here: the XRC declares no value, the presenter supplies it, so the ride-setup work in E3/E4 chooses the number and amends whichever of the two is wrong.

Otherwise none. (All resolved: pooled card entitlement — uncapped, one card per lap, any rider may out-lap their teammates (R-16) · ride deletion — yes, type-to-confirm (R-18) · Windows unsigned / macOS Developer ID (R-01) · categories — excluded entirely.)

Companions: [engineering spec](spec.md) · [XRC windows](xrc-windows.md) · [module skeletons](module-skeletons.md) · [UI designs (retired)](ui-designs-retired.md) · [HTML results sample](../exports/epic-2026-results.html) ([no-times](../exports/epic-2026-results-no-times.html)).
