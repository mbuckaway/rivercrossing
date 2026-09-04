# RiverCrossing — Requirements

*Requirements · v1.0 · July 23 2026 · binding for implementation*

MUST = release blocker · SHOULD = v1 target
Trace: spec § / [XRC window](xrc-windows.md)

Each requirement is testable and traces to the [engineering spec](spec.md) (§) and the [XRC window designs](xrc-windows.md) (window ids) — the artifacts code is built from; the retired [hi-fi designs](ui-designs-retired.md) are history only, not required reading for the build. "The app works the first time" is enforced by R-70…R-77: nothing ships unless the full acceptance race passes on both OSes in CI.

### 1 · Platform & runtime

| ID | Level | Requirement | Trace |
|---|---|---|---|
| R-01 | MUST | Runs on Windows 10/11 and macOS 13+ from installers: Authenticode-signed NSIS installer + bootloader via SignPath when `SIGNPATH_*` config is present, unsigned fallback until then (Phase 9 amendment: NSIS replaces Inno Setup — makensis compiles natively on macOS, while Inno's compiler needs Wine, whose Homebrew cask is deprecated and disabled 2026-09-01); Developer-ID-signed, notarized .dmg. One codebase, Python 3.14 + wxPython 4.3.1 (wxWidgets 3.3.3 — cp314 wheels, and the release that supplies wx.App.SetAppearance, R-03). | §10, §14 |
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
| R-20 | MUST | Rider editor: add/edit solo entries and teams (plate, rider first/last names, team membership); plate unique per ride, prefilled with next free. | §2 · [ridereditor](xrc-windows.md) |
| R-21 | MUST | CSV import and export per the §7 column spec — one unified, header-mapped format for every plate model (Phase 2; the two old shapes, relay `plate,entry_name,type,rider_1…rider_N` and pooled `plate,name,team_name`, are gone), one row per rider, header resolution and plate/team rules per R-24. Import previews counts + conflicts and writes nothing until confirmed; re-import reshapes teams freely until start. | §7 · [csvdlg](xrc-windows.md) |
| R-22 | MUST | Team relay model: one plate per entry, one rider on course at a time; crossings may record which member rode the lap. | §1/§2 · [entrydetail](xrc-windows.md) |
| R-23 | MUST | Rider names are first/last: the `rider` record stores `first_name`/`last_name` (last optional — a one-word rider renders as the first name alone), `Rider.full_name` joins them for display, and a solo entry's `display_name` mirrors its rider's `full_name`. Phase 1 split of the old single `name` — a greenfield schema reset, no migration (a database from an older build is stale and must be recreated). | §1/§2 · [ridereditor](xrc-windows.md) |
| R-24 | MUST | Unified header-mapped roster CSV (Phase 2). Canonical fields, in the export header `FIRSTNAME,LASTNAME,TYPE,TEAMNAME,NUMBER,NOTES`. Import resolves each header column to a canonical field through ordered, case-insensitive matchers (TEAMNAME contains `team\s*name` · TYPE contains both `solo` and `team`, or is exactly `type` · FIRSTNAME/LASTNAME contain `first\s*name`/`last\s*name` · NUMBER is the whole header matching `number`/`plate`/`bib` · NOTES contains `notes?`); the first matching field claims a column and an unmatched column is ignored. One row per rider; a row with neither first nor last name is skipped as a footer/blank unless it still names a plate/team/type, which is a missing-name conflict. TYPE is `solo`/`team` after case folding; blank TYPE derives team when a TEAMNAME is present, else solo. Team rows group by normalized TEAMNAME (trim, collapse whitespace, lowercase) across the whole file. Blank NUMBER auto-assigns sequential numeric plates from the roster's next free; on `rider_pooled` each row keeps its own plate, on `team_relay` a team's rows share its single plate. Export writes the same header (a finished ride appends `laps, cards, best_hand, total_time`). | §7 · [csvdlg](xrc-windows.md) |
| R-25 | MUST | Teams Editor (`mi_team_editor` → `team_editor_dlg`), routed only on mixed rides (`teams_allowed`): edits a team's record — name, notes, relay plate (relay rides only; a pooled team's plate derives from its members, never settable here), and logo: a card default and/or an optional image, image wins and either clears the other (`entry.logo_card`/`logo_png`; a team's card auto-assigns from the ride's seeded sequence at creation — no two teams share). Membership is read-only in this window (managed in the Rider Editor); add/remove are DRAFT-only and refused edits show on `teams_infobar`. | §15/§15b · [team_editor_dlg](xrc-windows.md) |

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
| R-45 | MUST | Team-mode scoring: every lap's card credits the **team entry**, never an individual rider — on `rider_pooled` a member's laps deal into the team's pooled hand (R-16); on `team_relay` the entry's crossings deal into the team's hand (R-22). Riders never hold or score a hand of their own. | §1/§5 |

### 6 · Resilience

| ID | Level | Requirement | Trace |
|---|---|---|---|
| R-50 | MUST | Every crossing/card/edit commits to SQLite (WAL) as it happens; a crash or power loss loses at most the uncommitted keystroke. | §2/§9 |
| R-51 | MUST | The app never exits without confirmation: closing with a ride running is caught with the exit dialog (quitting keeps the ride running on wall time); otherwise the quit confirm (`exit_confirm_dlg`) appears. On macOS the window × hides the app (Dock reopens it) rather than quitting. | §3 · [exitdlg](xrc-windows.md) |
| R-52 | MUST | On launch with a running ride, a resume dialog always appears; session bookkeeping distinguishes clean quit from crash and words the dialog accordingly. Continuing preserves start time and all data. | §2/§3 · [resumedlg](xrc-windows.md) |
| R-53 | MUST | Start pressed on a ride with data asks continue-vs-new; continue loses no time or data; "new" archives first. | §3 · [continuedlg](xrc-windows.md) |
| R-54 | MUST | Automatic backups on open + hourly while running (keep 20); manual Back Up Now; restoring a backup is documented in the guide. | §2 · [settingsdlg](xrc-windows.md) |
| R-55 | MUST | Fresh-launch console state: with no running ride to resume, the bootstrap console opens in **DRAFT with no ride running** — File ▸ Exit / ⌘Q shows the plain quit confirm (`exit_confirm_dlg`, R-51's no-ride-running path), never the ride-running exit dialog. The clock labels (`clock_elapsed_lbl`/`clock_remaining_lbl`) reserve a fixed minimum width so long elapsed/remaining text never overlaps the Start/Stop buttons. | §3/§13 · [mainframe](xrc-windows.md) |

### 7 · Results & publishing

| ID | Level | Requirement | Trace |
|---|---|---|---|
| R-60 | MUST | Results are computable the moment the ride finishes: top 10 by hand with card graphics, full field, DNFs marked. | §5 · [resultsframe](xrc-windows.md) |
| R-61 | MUST | HTML export: one self-contained file rendered with Jinja2 (autoescape, StrictUndefined; base template + macros) from frozen payload dataclasses — Tailwind 4 CSS compiled + inlined, results JSON embedded with </ escaped as <\/, logo base64, zero script logic — static markup renders with JS disabled and the JSON block is the machine-readable record; golden-file + JSON round-trip tests, zero external references (CI-checked) that opens from file:// with no network; flags for times, laps board, time board, full field, all cards drawn (every card per entry, with the drawing rider on pooled rides); the light template is the only published look — no theme option. Page CSS is compiled once at package build (Tailwind CLI in CI against the frozen template) and vendored with base64 font subsets — no Node/CDN at export or runtime; markup is plain Tailwind utilities + the frozen custom classes (§8), **no component library** (the page is read-only; daisyUI is the pre-approved CSS-only fallback if v2 adds interactive widgets). | §8 · [sample](../exports/epic-2026-results.html)/[no-times](../exports/epic-2026-results-no-times.html) |
| R-62 | MUST | PDF export via fpdf2 with the same sections and flags; deterministic output; optional one-page podium poster. | §8b · [5a–5d](ui-designs-retired.md) (design doc) |
| R-63 | MUST | Times appear in published results only when the export setting says so — hidden by default, no toggle on the page, and with times off the time data is not embedded at all; laps/time leaderboards are opt-in. | §8 · [resultsframe](xrc-windows.md) |
| R-64 | SHOULD | Finished rides reopen into a corrections-only REOPENED state (clock closed, add-at-time/edit/void, moves on pooled rides); Finish again re-locks; stale exports are flagged. | §3 · [resultsframe](xrc-windows.md) |
| R-65 | MUST | Mixed-ride results split into **two sections — Teams and Solo**: teams rank among teams and solos among solos, each section renumbered from 1 with its own DNF tail (`standings.rank_by_kind`) — never one combined field. The results window, the HTML full field and the PDF report render the two sections (a kind absent from the ride has no section); the standings CSV carries the `type` column: `place, plate, entry, type, laps, hand[, total_time]`. | §5/§8/§8b · [resultsframe](xrc-windows.md) |

### 8 · Quality, testing & CI — "works the first time"

| ID | Level | Requirement | Trace |
|---|---|---|---|
| R-70 | MUST | TDD: tests written first (tdd-python-writer agent), red→green→refactor, module by module in dependency order. | §12 |
| R-71 | MUST | Core logic in pure-Python modules with zero wx imports; coverage ≥ 90%; ruff + mypy strict clean. | §11 |
| R-72 | MUST | Card algorithm verified by known vectors, Hypothesis property tests, brute-force cross-check, and seeded whole-ride simulations across entry modes, joker counts and caps. | §12 |
| R-73 | MUST | Every entry box, button, radio, menu item and dialog is reachable and drivable by the functional harness via stable snake_case XRC names — FindWindowByName for windows and controls, the loaded menubar for `mi_*` items, whose names XRC does not apply to the control (§15b); the menu-coverage test walks all §15 routes in all ride states. | §12/§15 |
| R-74 | MUST | Acceptance: a scripted full race (min lap lowered — an ordinary ride setting) runs end-to-end through the real UI incl. stop/continue, kill+relaunch, quit+relaunch, finish, reopen → correct → finish again, exports parsed and asserted — 100% pass on both OSes before any release. | §12/§14 |
| R-75 | MUST | CI per §14: GitHub Actions macos-latest + windows-latest + windows-11-arm (real desktop sessions — no virtual display), 6 stages, screenshot-on-failure artifacts, built-binary smoke test, signed installers on tags (Phase 11 amendment: tags publish an Authenticode-signed NSIS installer + bootloader via SignPath when the `SIGNPATH_*` config is present, with an unsigned fallback until then; macOS signing awaits the org credentials per E9.1.3). Both Windows architectures (x64 + ARM64) and Apple Silicon macOS gate, publishing arch-tagged installers. (The EPIC 1 temporary deviation — macOS-only gate while no Windows test machine existed — was reversed in EPIC 1 Phase 10: both platforms gate.) | §14 |
| R-76 | MUST | Dialog behavior per §13: Esc cancels, Enter = default, destructive confirms focus Cancel, focus returns to opener; asserted per dialog by the harness. | §13 |
| R-77 | SHOULD | Nightly seeded acceptance race; failures file the seed for exact reproduction. | §14 |

### Out of scope — v1

Sponsor strips on published results (v2) · age/category fields — excluded by decision, must not appear or be asked for anywhere · hardware timing (RFID/chip) · networked multi-operator scoring · e-bike/waiver policy enforcement (recorded fields only).

### Open questions

None. (All resolved: pooled card entitlement — uncapped, one card per lap, any rider may out-lap their teammates (R-16) · ride deletion — yes, type-to-confirm (R-18) · Windows unsigned / macOS Developer ID (R-01) · categories — excluded entirely · deck-count default — 8, chosen by the E3.5 ride-setup work; the presenter supplies it, Spec §4 stands and the canvas's 2 was a mock artifact.)

Companions: [engineering spec](spec.md) · [XRC windows](xrc-windows.md) · [module skeletons](module-skeletons.md) · [UI designs (retired)](ui-designs-retired.md) · [HTML results sample](../exports/epic-2026-results.html) ([no-times](../exports/epic-2026-results-no-times.html)).
