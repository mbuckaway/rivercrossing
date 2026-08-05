# RiverCrossing — Project Plan

*RiverCrossing · project plan & EPIC list · v1 · July 24 2026*

Sources mounted and validated (nothing below is invented): [Requirements (R-ids)](requirements.md) · [Spec §1–§15b](spec.md) · [XRC window designs (23 windows, frozen names)](xrc-windows.md) · [Module skeletons (repo layout)](module-skeletons.md) · [Jinja2 templates](../templates/base.html.j2) + [golden samples](../exports/epic-2026-results.html) · [hi-fi designs (flow reference only — retired for visuals)](ui-designs-retired.md). Codebase to reuse: none — greenfield except the templates and samples above, which ship into the package verbatim.

### 1 · Summary & EPIC overview

Nine EPICs, each a complete user-visible, testable feature — never a horizontal layer. **D1 (first deliverable) = EPIC E1: the whole UI builds and runs on Windows and macOS alike — CI builds runnable dev bundles for both on every main build — with every window, menu, dialog and control drivable by hand and by the test harness, showing hard-coded demo data behind a removable seam** — no real data libraries yet. E1 also pins the interface contracts (XRC names / presenter protocols / payload dataclasses) so every later EPIC builds against a frozen surface and nothing blocks. The only externally-owned dependency — Apple/Windows signing credentials — sits in E9, last, behind that contract.

| EPIC | Feature (user-visible) | Ships | Depends on |
|---|---|---|---|
| E1 | **Runnable UI shell (= D1)** — all 23 windows from XRC, menus routed, demo data displayed, smoke test per screen | v0.1 | — |
| E2 | Deal & score engine — shoe + poker evaluator, self-test dialog goes live | v0.2 | E1 contracts |
| E3 | Roster — riders, teams, CSV import/export, editable to start | v0.3 | E1 contracts |
| E4 | Live ride (in-memory) — start/stop/continue, crossings, laps, flags, dealing, sound | v0.4 | E2 · E3 |
| E5 | Persistence & crash recovery — SQLite, autosave, resume/reopen dialogs, kill+relaunch proof | v0.5 | E4 |
| E6 | Results & publishing — standings, tie-breaks, HTML (Jinja2) / PDF / poster / CSV exports | v0.6 | E2 · E5 |
| E7 | Corrections & audit — edit/void/reassign/manual-deal/DNF, reopen, audit viewer | v0.7 | E5 (E6 for stale-flag) |
| E8 | Settings & assistance — theme, hide-times, zoom, sound, shortcuts, user guide, About | v0.8 | E5 |
| E9 | Packaging & release — installers, signing/notarization, acceptance race, nightly | v1.0 | all · external creds |

Parallelization: after E1 exits, E2 and E3 run in parallel; E6/E7/E8 parallel after E5. Full sequencing map ships with the Part-2 task briefs.

### 2 · Method — how agents work this plan

- **One task = one agent session.** Read the task's brief (Part 2) and its cited sources first; never code from memory of them. Numbering: EPIC `E`, phase `E.P`, task `E.P.T`, subtask `E.P.T.S`.

- **TDD is mandatory** — the `tdd-python-writer` agent writes the named failing tests first (red), implementation follows (green), refactor with tests pinned. Every task's subtasks are ordered test-first; a PR whose first commit isn't tests is rejected.

- **Definition of done (every task):** named tests exist and pass · coverage gates met (§3) · ruff + mypy strict clean · XRC names untouched (or ids.py regenerated + registry test green) · trace ids (R-x, mock anchors) cited in the PR · CI green on both OSes · after E5: no `rivercrossing.demo` import outside tests (lint rule, see E1.2.4).

- **Commits/PRs:** Conventional Commits, scope = module — first commit `test(hands): E2.1.1 red`, then `feat(hands): E2.1.1 green`. PR title `E2.1.1 — eval5 rank table`; body links the plan anchor + requirement ids; squash merge; one task per PR.

- **Mock-first rule:** the 23 canvas screens have frozen mockups, but §15 routes three items at a dialog the canvas never drew — Duplicate Ride… and Reopen Ride (E5), the Void Card… confirm (E7). Those, and any future screen without a mockup, get a mock-first step that produces the mockup and registers its control names in §15b *before* any UI code.

### 3 · Coding standards & gates

Adopted verbatim from the repo's quality gauntlet — Spec §12 (TDD + harness), §14 (six CI stages), Requirements R-70…77. Standards files (created in E1.1, canonical thereafter): `pyproject.toml` ([tool.ruff] all-rules baseline, [tool.mypy] strict, [tool.coverage] gates), `.github/workflows/ci.yml`, `CONTRIBUTING.md` (this §2/§3 distilled). Gates: **≥ 90% line and ≥ 90% branch** on core modules (cards, hands, standings, ride, store, csvio, htmlexport, pdfexport — per R-71 "coverage ≥ 90%", applied to both meters; branch coverage via `coverage --branch`), Hypothesis property suites where §11 names them, headless core (wx imports forbidden outside `rivercrossing.ui` — import-linter contract). Python 3.14 · wxPython ~=4.3.1 (wxWidgets 3.3.3) · Jinja2 · fpdf2 · stdlib sqlite3.

### 4 · UI / functional test strategy (wxPython)

| Tooling option | Verdict | Why |
|---|---|---|
| pytest + wx.UIActionSimulator + FindWindowByName | **ADOPT the names; the simulator is not the driver** | FindWindowByName against the frozen XRC names is the harness spine and stays. The simulator is not: measured, from a terminal-launched interpreter it returns `True` and delivers nothing — the process never becomes the OS-active application, so no handler fires and no control value changes — and `Text(str)` raises `TypeError` on this build. Spec §12's direct event injection (`SetValue()` fires `EVT_TEXT`, a posted `wx.CommandEvent` fires `EVT_BUTTON`) is therefore the **primary** mechanism, not the CI fallback. The row stays because a signed `.app` bundle may make the app frontmost and the events land — being measured separately; re-measure before relying on it. |
| XRC load validation (each .xrc loads; every §15b name resolves) | **ADOPT** | Unit-level, headless-cheap; pairs with the CI-generated ids.py drift gate (R-05). |
| Screenshot-on-failure artifacts + one auto-retry | **ADOPT** | Flake control per §14 stage 3; event-driven waits, never bare sleeps. |
| Wx Inspection Tool (wx.lib.inspection) | ADOPT (dev-only) | Interactive widget-tree debugging; never in CI. |
| pywinauto / WinAppDriver / Appium | REJECT | Windows-only or UIA-tree gaps for wx widgets; duplicates the injection harness with more flake. |
| SikuliX / image-matching | REJECT | Brittle to theme, DPI and font rendering across the two OSes. |
| Squish for wx | REJECT | Commercial license + CI seats; the injection harness covers the need. |
| pytest-qt / dogtail / AT-SPI | REJECT | Wrong toolkit (Qt) / Linux-only — not targets. |

**Smoke test per screen from day one:** E1.3.3 adds one parametrized test over all 23 windows — load from XRC, Show(), assert every §15b name resolves (the info bars after their code-side construction; `main_menubar` through `XmlResource.LoadMenuBar()`, which is the one name XRC never applies to a control — §15b), capture a screenshot artifact, close cleanly. It runs in every CI build forever after.

### 5 · EPICs

E1 · Runnable UI shell — D1, ships first

The complete window set builds and runs on Windows and macOS — CI builds downloadable dev bundles for both — and every §15 menu route opens its window, every control is drivable by hand and by harness, and screens display hard-coded demo data through a single removable seam. No engine, no database. This is the developer's clickable, testable mockup of the whole app.

E1.1 · Repo + gauntlet bootstrap

· **E1.1.1** Repo per [skeletons](module-skeletons.md): src layout, pyproject (deps §3), CONTRIBUTING.md — S1 test: packaging smoke (`pip install -e . && import rivercrossing`) · S2 files.

· **E1.1.2** CI stages 1–3 skeleton + stage-5 in dev-bundle mode (ruff/mypy → pytest → functional; PyInstaller onedir, unsigned, artifact upload) on windows-latest + macos-latest — S1: a deliberately failing probe test proves the gate blocks · S2: workflow green. macOS was the blocking gate with Windows advisory while no Windows test machine existed — a temporary deviation recorded in Spec §14 and R-75 and reversed in EPIC 1 Phase 10 (both platforms now gate).

· **E1.1.3** import-linter contract: wx only under `rivercrossing.ui` — S1 red contract test · S2 config.

E1.2 · Pinned interface contracts (unblocks all later EPICs)

· **E1.2.1** ids.py generator from `ui/xrc/` + drift test (R-05) — S1 tests with fixture .xrc · S2 generator.

· **E1.2.2** Payload dataclasses frozen: ExportOptions (show_times=False, laps_board, time_board, full_field, all_cards, lap_km), standings/event records per [template contract](../templates/base.html.j2) — S1 round-trip + default tests · S2 dataclasses.

· **E1.2.3** Presenter⇄view protocols per skeleton (FakeView-tested) — S1 protocol conformance tests · S2 stubs.

· **E1.2.4** `rivercrossing.demo`: DataSource protocol + DemoDataSource with the canvas's fixture data (plates 123/77/45/212/8, EPIC 2026 ride, 1 124 crossings…) — S1: lint rule "demo importable only from ui bootstrap + tests" red-then-green · S2 fixtures. *This is the removable hard-coding seam: deleting one wiring line (and the package) later leaves zero dead references.*

E1.3 · XRC authoring — all 23 windows

· **E1.3.1** Author the 9 .xrc files exactly per [§15b file map](spec.md) and the [canvas](xrc-windows.md) (names frozen; wxRB_GROUP radios with canvas defaults; wxStdDialogButtonSizer; stock IDs) — S1: XRC-load + name-resolution tests per window (red: files absent) · S2 author main.xrc, setup.xrc · S3 riders/detail/results · S4 library/audit/settings/dialogs.xrc.

· **E1.3.2** Card bitmap assets: 53 images @1x/2x under ui/assets/cards/ + imagelist loader — S1: loader test (53 keys, joker present, 2x variants) · S2 generate assets (scripted drawing; no external licensing).

· **E1.3.3** Per-screen smoke test (§4) — S1 write parametrized test (red) · S2 window loader helpers until green on macOS · S3 CI matrix green.

E1.4 · Menus + navigation

· **E1.4.1** Menubar per §15/§15b incl. Results menu, accelerators (Enter, Ctrl+Z, F1, F5), macOS relocation — S1: menu coverage test walks every §15 row to its target (red) · S2 wire routes. The three rows with no frozen window — Duplicate Ride…, Reopen Ride, Void Card… (§15) — route to a flagged sentinel the coverage test asserts as such; E5 and E7 replace it mock-first, and no window name is invented here.

· **E1.4.2** State-based enable/disable against mocked ride states DRAFT/RUNNING/FINISHED/REOPENED — S1 parametrized enablement tests from the §15 "Enabled when" column · S2 command-state table.

E1.5 · Demo data on screen

· **E1.5.1** Console: DataView columns + demo feed rows, bold flagged row, card chips, counters, InfoBars show/hide, splitter sash persistence — S1 harness assertions per control · S2 code-side bindings.

· **E1.5.2** Library/detail/results/editor lists filled from DemoDataSource; hide-times toggle removes time columns live (mock setting) — S1 column-set tests both states · S2 bindings.

· **E1.5.3** Dialog behaviors R-76: Esc cancels, Enter default, destructive confirms focus Cancel, arm-to-stop checkbox gates stop_btn — S1 per-dialog tests · S2 wiring.

E1.6 · D1 exit

· **E1.6.1** CI dev bundles: PyInstaller onedir specs (unsigned) for Windows + macOS; packaged-app smoke runs the 23-window suite against the bundle; runnable artifacts uploaded on every main build — S1: bundle-smoke + asset-manifest tests (red) · S2 specs.

· **E1.6.2** Scripted walkthrough against the CI-built bundles on both OSes: menu walk, drive every named control, screenshots; tag v0.1 with the two bundles attached.

Exit criteria the CI-built dev bundle launches and passes smoke on Windows AND macOS (artifacts downloadable; since EPIC 1 Phase 10 the Windows leg blocks like macOS — §14) · 23/23 smoke green · menu coverage green vs §15 · every §15b name resolves · demo seam lint green · zero business logic outside demo fixtures.

E2 · Deal & score engine

The poker heart goes live: seeded shoe and the best-5-of-N evaluator with jokers and five-of-a-kind; the Help ▸ Self-test dialog runs the real thing (user-visible proof).

- **E2.1 Evaluator core** — E2.1.1 rank tables + eval5 (S1: 7,462-distinct-rank sweep from tests/vectors, red · S2 implement); E2.1.2 joker wild layer + five-of-a-kind ordering (S1: 28-row joker vector table §5 · S2); E2.1.3 best-5-of-N with card-cap X semantics §5 (S1: cap fixtures incl. <5 cards · S2); E2.1.4 Hypothesis properties (rank total invariance, monotonicity under card add).

- **E2.2 Shoe** — E2.2.1 seeded Fisher-Yates, deal_index, exhaustion reshuffle cycle (S1 determinism/exhaustion tests · S2); E2.2.2 decks × jokers config per setup dialog (0/2/4).

- **E2.3 Simulation suite** — E2.3.1 seeded whole-ride sims: 180 entries × 6 h, solo + mixed, both plate models, uncapped pooled laps (R-16) — asserts hand validity, shoe accounting, timing budget (§5: field < 1 s).

- **E2.4 Self-test live** — E2.4.1 selftest_dlg runs the real suite via presenter; finish-gate hook exposed for E6 (S1 harness test: dialog shows PASS lines · S2 wire).

Exit criteria vectors + properties + sims green at gates · selftest_dlg green from real evaluator on both OSes · evaluation of 180×12 cards < 1 s in CI.

E3 · Roster — riders, teams, CSV

Real rider/team management replaces demo roster: unique plates, solo default, teams 2–10 with rider-pooled (default) or team-plate model, editable right up to start (R-17), CSV round-trip with preview-then-commit.

- **E3.1 Models + rules** — E3.1.1 entry/rider/team invariants (unique plate, team size ≤ max, solo default) S1 property tests · S2; E3.1.2 lock matrix by ride state (DRAFT free; post-start per plate model R-15/17).

- **E3.2 Editor live** — E3.2.1 add/save/delete via presenter (delete blocked once entry has data → DNF/void path); E3.2.2 team column + team_choice population incl. "New team…"; next-free-plate suggestion.

- **E3.3 CSV** — E3.3.1 csvio.preview (counts + conflicts, writes nothing) S1: conflict fixture files (dup plate, missing name, bad team size) · S2; E3.3.2 commit + re-import reshaping teams pre-start; E3.3.3 export round-trip (import→export value-identical).

- **E3.4 UI** — E3.4.1 csv_preview_dlg wired (Import disabled while conflicts > 0); E3.4.2 rider_editor solo-only variant (Team column hidden).

Exit criteria 180-rider EPIC CSV imports clean · conflicts block commit · teams reshape until start · editor fully live on real store-less models · demo roster unused on these screens.

E4 · Live ride (in-memory)

Type a plate, get a lap and a card: the full timing loop runs end-to-end in memory — start (button or set-time), crossings with min-lap flags and held cards, undo, per-lap dealing, counters, sound cues, arm-to-stop.

- **E4.1 State machine** — E4.1.1 DRAFT→RUNNING→FINISHED→REOPENED transitions + guards (S1: transition table tests from §2 · S2); E4.1.2 set-start-time retro-recompute of lap-1 (audit-logged); E4.1.3 stop/continue semantics (continue keeps clock truth).

- **E4.2 Crossings** — E4.2.1 plate entry → lap credit + timestamps (wall-clock source injectable); E4.2.2 min-lap flag → card held not dealt (§3); E4.2.3 undo last (R-33) with shoe restitution; E4.2.4 unknown-plate rejection cue.

- **E4.3 Dealing** — E4.3.1 one card per crossing incl. uncapped pooled teams (R-16); E4.3.2 held-card release on review accept / manual deal path stub for E7.

- **E4.4 Console live** — E4.4.1 feed (newest-first 30), counters, last_crossing_lbl, review panel from real engine; E4.4.2 arm_stop_chk gate + stop/finish confirm flows; E4.4.3 sound cues per §10 (recorded/flagged/error) behind Settings toggle; E4.4.4 mini acceptance: scripted 20-rider, min-lap-lowered race in memory through the real UI.

Exit criteria mini-race green on both OSes · flags/undo/held cards behave per §3 · console needs no demo data while a ride runs · latency plate-Enter→row < 100 ms.

E5 · Persistence & crash recovery

The ride survives anything: SQLite event store with replay, autosave/backup, session_state, and the resume/reopened flows — proven by killing the process mid-race and continuing. Two contracts land here because this is where they first bite: the **wx⇄asyncio integration** behind the async writer (`wxasync` is ruled out — Spec §10) and, mock-first per §2, the two windows §15 routes to without a frozen design — **Duplicate Ride…** and **Reopen Ride** — names registered in §15b before any UI code.

- **E5.1 Store** — E5.1.1 schema + migrations (multi-ride, §6); E5.1.2 event append + replay→RideEngine equivalence (S1: replay == live state property); E5.1.3 WAL + crash-consistency (kill subprocess mid-transaction, reopen, last commit intact — R-50s).

- **E5.2 Sessions** — E5.2.1 session_state records clean/unclean close + running ride; E5.2.2 resume_dlg (crash vs quit wording) + reopened_infobar wiring; E5.2.3 exit_running_dlg flow (quit keeps ride timing on wall clock).

- **E5.3 Backups** — E5.3.1 open + hourly + manual "Back up now", keep 20 (R-54); E5.3.2 delete-ride guard: backup first, type-name confirm, never RUNNING (R-18).

- **E5.4 Library live + demo retirement** — E5.4.1 ride_library_dlg on real DB (open/new/duplicate/delete); E5.4.2 remove DemoDataSource from app wiring — lint proves demo imports = tests only.

Exit criteria kill+relaunch and quit+relaunch continue the ride with zero loss · library manages multiple rides · backups rotate · demo seam out of the app path.

E6 · Results & publishing

Finish the ride, publish in minutes: standings with tie-breaks ①②③ (reorderable, re-ranks live), and the four exports — self-contained HTML (Jinja2, offline), PDF report, podium poster, standings CSV — matching the golden samples.

- **E6.1 Standings** — E6.1.1 ordering + tie-break rules incl. high-card-draw records (S1: crafted tie fixtures, reorder re-runs, DNF block last · S2); E6.1.2 leaderboards (laps; fastest = most laps then shortest elapsed).

- **E6.2 HTML export** — E6.2.1 vendor CSS build step: Tailwind CLI in CI against the frozen [templates](../templates/base.html.j2) + [theme.css](../templates/theme.css) → compiled_css + font subsets packaged; E6.2.2 render() with racejson filter — S1: golden-file tests from the committed sample fixtures + JSON round-trip + zero-external-ref check (R-61) · S2 implement; E6.2.3 no-times variant omits markup and JSON fields (R-63).

- **E6.3 PDF** — E6.3.1 fpdf2 report per 5a–5c (deterministic bytes, R-62); E6.3.2 podium poster 5d.

- **E6.4 Results window live** — E6.4.1 results_frame standings + publish checkboxes drive ExportOptions; E6.4.2 Results menu items (Generate HTML / Export PDF / Poster / Standings CSV / Preview in Browser) with FINISHED gating; E6.4.3 finish gate: evaluator self-test must be green (§2).

Exit criteria goldens byte-identical · exports open offline from file:// · times appear only when the setting says (R-63) · poster + CSV ship · finish-to-published < 60 s on the acceptance race.

E7 · Corrections & audit

Every scorer's-table mistake is fixable with a reason and a trail: edit/void crossings, add-at-time, reassign plates, manual deal, void card, DNF, reopen-for-corrections, and the filterable audit viewer. The **Void Card…** confirm is the third §15 route with no frozen window: E7 authors it mock-first per §2 and registers its names in §15b.

- **E7.1 Command layer** — E7.1.1 each correction = audited command with reason (S1: audit-row assertions per command · S2); E7.1.2 recompute cascades (laps/times/cards) with property test: corrections then replay == direct history.

- **E7.2 Dialogs live** — E7.2.1 edit_crossing (edit/add-at-time titles), reassign, manual_deal, dnf_confirm, void flows from entry_detail; E7.2.2 REOPENED mode: entry off, corrections on, edited rows highlighted, "Finish again".

- **E7.3 Audit viewer** — E7.3.1 audit_dlg filters (plate search + action choice), newest-first; E7.3.2 stale-export flag after corrections (stale_infobar in results_frame).

Exit criteria all §15 Cards-menu routes live with reasons enforced · reopen→correct→finish-again produces re-ranked, un-stale exports · audit rows for every mutation.

E8 · Settings & assistance

The operator-comfort layer: appearance radios live on both platforms, hide-times live toggle, text zoom, sound toggle, keyboard-shortcuts dialog fed by the accelerator table, bundled user guide (F1 + per-dialog anchors), About.

- **E8.1 Settings live** — E8.1.1 persistence of all settings; E8.1.2 appearance: System/Light/Dark all applied through `wx.App.SetAppearance` on the 4.3.1 baseline — System follows the OS, no capability check, no disabled radio, per-OS matrix asserted; E8.1.3 hide-times toggles console columns mid-ride (R-63 companion); E8.1.4 zoom 90–150% re-layouts.

- **E8.2 Assistance** — E8.2.1 shortcuts_dlg rows generated from the accelerator table (cannot drift); E8.2.2 user guide: build docs/user-guide.html from the 6a outline, F1 + Help-button anchors, screenshots regenerated by the harness each release; E8.2.3 About box (ride logo fallback to app icon, version from package).

Exit criteria settings persist across relaunch · theme matrix verified per-OS · guide opens to the right anchor from every Help button · shortcuts table matches accelerators by construction.

E9 · Packaging & release — external creds live here, last

Installables and the full-dress rehearsal: PyInstaller apps, NSIS .exe (per-user, unsigned — Phase 9: NSIS replaces Inno Setup) and notarized .dmg, CI stages 5–6, the complete acceptance race, and the nightly seeded run.

- **E9.1 Bundles** — E9.1.1 harden the E1.6.1 dev-bundle specs into release bundles (assets, templates, guide, WAVs complete; smoke: launch packaged app, open a ride); E9.1.2 NSIS installer; E9.1.3 dmgbuild + Developer ID codesign + notarization — *owned externally: org supplies Apple cert/creds; interface = CI secrets contract defined in E1.1.2; until provided, stage produces unsigned artifacts and the gate is advisory.*

- **E9.2 Acceptance** — E9.2.1 full race R-74: CSV in, hundreds of typed crossings, stop/continue, kill+relaunch, quit+relaunch, finish, all four exports verified; E9.2.2 nightly seeded race, failures file the seed (R-77); E9.2.3 release drafting on version tags.

Exit criteria installers install and run on clean Windows 11 + macOS images · acceptance + nightly green · v1.0 tagged with artifacts attached.

### 6 · Traceability — EPIC ⇄ requirements ⇄ mockups

| EPIC | Requirements | Design ground truth |
|---|---|---|
| E1 | R-01/02/05, R-70/73/75/76 | XRC canvas (all 23 windows) · Spec §13–§15b |
| E2 | R-40…44, R-72 | Spec §5 · selftest_dlg · tests/vectors |
| E3 | R-11/12/15/17, R-20/21/22 | rider_editor_dlg, csv_preview_dlg · Spec §7 |
| E4 | R-13/16, R-30…36 (undo = R-33; audio cues live in R-31) | main_frame states · Spec §2–§4 |
| E5 | R-50…54, R-18 | resume/exit/continue dialogs, library · Spec §6/§9 |
| E6 | R-14, R-60…63 | results_frame · golden samples + Jinja templates · UI Designs 5a–5d · Spec §8/§8b |
| E7 | R-15/17, R-33/34, R-36 (REOPENED), R-38 | edit/reassign/deal/dnf dialogs, audit_dlg, entry_detail |
| E8 | R-03/04, R-63 companion, help rows §15 | settings_dlg, shortcuts_dlg, about_dlg · UI Designs 6a |
| E9 | R-01, R-74/75/77 · Spec §10 installers, §14 stages 5–6 | — |

R-numbers cite the requirements doc's tables; where a band (R-30…36, R-50…54) is named, the brief for that EPIC enumerates the exact rows before work starts.

### 7 · Risks & mitigations

| Risk | Mitigation |
|---|---|
| wxPython 4.3 (dark mode) slips further | **Resolved** — 4.3.0 shipped 2026-07-28 and 4.3.1 on 2026-07-30, both with cp314 wheels; the baseline is 4.3.1 / wxWidgets 3.3.3 and `wx.App.SetAppearance` ships in it, so R-03's three radios are live on both platforms with no capability check and no disabled control. |
| wx⇄asyncio integration undecided | `wxasync` is out (Spec §10 — one teardown path segfaults, another hangs, and a segfault on quit would forge a crash signal against R-52). E5 chooses the mechanism where the async writer first appears; E1–E4 hold no database, so nothing built before then depends on the choice. |
| UIActionSimulator does not deliver events | Not flake — measured: no OS-active app, no delivery (§4). Direct event injection is the harness's primary driver; event-driven waits, one auto-retry, screenshot artifacts (§14); harness helpers centralized so fixes are one-place. |
| wxDataViewCtrl per-row attributes differ mac/Windows | E1.5.1 smoke asserts bold-flag rendering on both OSes early — the riskiest widget lands in D1, not v0.9. (wxDataViewListCtrl is not an option at all: its XRC handler forces the control name — §15b; attributes come from a `DataViewIndexListModel.GetAttrByRow` override.) |
| Tailwind CLI / Node only at package build | Compiled CSS is committed as a build artifact with checksum; exports never need Node at runtime (R-61); CI fails loudly if the vendored CSS is stale vs theme.css. |
| Apple notarization credentials unavailable | E9 gate advisory until secrets land (contract from E1.1.2); unsigned dev builds flow the whole time. |
| PyInstaller × wxPython packaging quirks (hidden imports, plists, DataView backends) | Dev bundles are built and smoke-tested from D1 (E1.6.1) — packaging breakage surfaces in the first EPIC, not E9. |
| fpdf2 nondeterminism (timestamps/ids) | R-62 pins deterministic output; golden-byte tests with fixed metadata. |
| XRC name drift breaking tests | Names frozen in §15b; ids.py generated in CI, drift fails the build (R-05). |
| Demo data leaking into production paths | Import-lint rule from E1.2.4 (demo importable only from bootstrap/tests); E5.4.2 removes the bootstrap wiring. |

### 8 · Next step — Part 2 task briefs

On approval of this plan, expand per EPIC into agent-ready briefs (one doc per EPIC): Goal · References (exact files/mock ids, read first) · Tests first (named files + concrete cases incl. negative and property-based — the tests are the spec) · Then implement · Done when. Each brief repeats the §2 ground rules, states its entry gate (the prior EPIC's exit criteria), carries the sequencing/parallelization map, and names an owner for every stub or cross-EPIC hand-off. E1's brief is the first to run. **Written:** [Task briefs — all nine EPICs](task-briefs.md) (incl. the numbered review of the set, TB-1…8).
