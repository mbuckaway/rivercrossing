# Changelog

All notable changes to RiverCrossing are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

EPIC 4 of 9 — live ride (in-memory) — on top of EPIC 3's roster and EPIC 2's deal & score engine.

### Added — EPIC 4 (live ride)

- **The ride engine.** `rivercrossing.ride.RideEngine`: DRAFT→RUNNING→FINISHED→REOPENED state
  machine with guards, an injected wall clock (elapsed/remaining derive from `now − actual_start`,
  never a timer), set-start-time with lap-1 recompute, stop-as-a-guard with continue, and a start
  gate over the roster.
- **Crossings, dealing, flags, undo.** Plate + Enter credits a lap and deals one card from the
  seeded `Shoe` (reshuffle + audit on exhaustion); laps under `min_lap_s` flag and hold their card
  until confirmed/voided; `undo_last` reverses a crossing and returns its card to the shoe front;
  card cap X keeps laps counting past the cap while later cards stay non-scoring (R-13);
  `deal_manual` is the audited manual-deal engine path E7's dialogs consume.
- **Standings (pulled forward from E6).** `rivercrossing.standings`: best-hand ranking with the
  R-14 tie-break order, R-43 draw-required flags, DNF placement, and both leaderboards.
- **Live console.** The console runs on `EngineDataSource` (feed, counters, status from the engine)
  with a fully wired `ConsolePresenter` — accepted/flagged/error outcomes with the matching
  `ui/sound.py` cues (recorded/flagged/error, Settings-toggleable, fake-backend seam), arm-to-stop
  (R-35), hide-times, and the finish route consulting the E6.4.3 gate stub.
- **Mini acceptance.** A scripted 20-rider race (60 crossings incl. flags, undo, stop/continue,
  finish) through the real UI with a hand-verified standings fixture.

EPIC 3 of 9 — roster & CSV — on top of EPIC 2's deal & score engine and EPIC 1's runnable UI shell.

### Added — EPIC 3 (roster & CSV)

- **Real entries, riders and teams.** `rivercrossing.roster`: in-memory models for one ride with a
  single plate namespace (a pooled team adopts its lowest rider's plate), the state × plate-model
  lock matrix (R-15/R-17), audited mutations the EPIC 5 store will persist, next-free-plate
  (highest + 1) and `validate_for_start` — R-12's team-size floor moved to CSV commit and ride
  start, so the editor can assemble a team one rider at a time in DRAFT.
- **The rider editor is live** (R-20): add/save/delete on the real roster, team_choice with
  "New team…" (native name prompt), duplicate plates surface on a code-side `roster_infobar`
  instead of crashing, the Team column and team UI hide on solo-only rides (R-11), and the
  dialog's own Import/Export buttons share one CSV flow with the File menu.
- **CSV import/export per spec §7** (R-21): `rivercrossing.csvio` previews counts and exact
  per-row conflicts without writing anything, commits atomically through the roster's audited
  mutators (insert, rename, team reshapes including solo ⇄ team conversion in DRAFT and the
  RUNNING rider-pooled carve-outs), and exports either plate model's column form. A Hypothesis
  property proves export → import is value-identical, teams included — it also caught that
  `team_name` is the pooled merge key, now recorded in spec §7. `csv_preview_dlg` gates its stock
  Import button while conflicts remain.
- **Ride setup is live** (approved added scope, E3.5): `RideConfig` — defined beside `RideStatus`
  for the EPIC 4 engine — built and validated by `SetupPresenter`/`RideSetup`, with deck
  default 8 (closing the spec §4 open question), jokers 2, pooled and solo-only defaults, and the
  R-17 entry-group lock.
- **App-wide fixes surfaced by the build:** every dialog opened from a real menu route now gets
  its recorded default button and first-field focus (closing the E1.5.3 gap); every code-side
  wx.InfoBar disables its slide effect — the default effect hangs `ShowMessage()`/`Dismiss()` on
  this wx build (measured); and a test-double parameter-name mismatch that wx silently swallowed
  inside menu handlers was found and pinned.
- `__version__` bumped to **0.3.0**.

### Added — EPIC 2 (deal & score engine)

- **The poker heart is live.** `rivercrossing.hands`: a 7,462-rank five-card evaluator over
  `phevaluator` with a fully-wild joker layer (five of a kind above royal flush), best-5-of-N for
  any pool size, the partial-hand rule, and card-cap-by-slicing (R-13, R-40…R-44, R-72).
  `rivercrossing.cards`: the seeded Fisher-Yates `Shoe` with deal-index audit,
  exhaustion → derived-seed reshuffle cycles, undo restitution, close-on-finish, and `replay`.
- **Two spec gaps were found, ruled on, and written back.** Within-entry duplicate cards
  (multi-deck) rank as physical cards — two identical nines are a pair, five same-rank cards are
  five of a kind, wild or natural. The previously-referenced 28-row joker vector table now
  actually exists: authored, shipped, and recorded in spec §5.
- **The evaluator constructs hands analytically** (one candidate per hand class from rank
  multiplicities and per-suit windows) instead of enumerating subsets: subset enumeration
  saturated its own pruning past ~20 pooled cards — measured 22 s for one 60-card team hand, and
  it silently dropped straight-completing cards (a real mis-scoring, caught by the simulation
  suite's scoping spike and a brute-force oracle). Now: a 60-card pool in ~0.25 ms, the whole
  180×12 field in ~16 ms (R-42's < 1 s with 60× margin).
- **Whole-ride confidence**: seeded simulations (180 entries × 6 h; solo, mixed-pooled,
  mixed-relay) assert exact shoe accounting across reshuffle cycles, evaluable hands for every
  pool (largest simulated team pool: 113 cards, uncapped per R-16), and the out-lapping-rider
  pooling rule. Hypothesis property suites fuzz evaluator invariants and shoe determinism.
- **Help ▸ Run Evaluator Self-test is real** (R-44): `hands.self_test()` — the finish-gate hook
  EPIC 6 will consume — runs the rank sweep, the 28 joker vectors, five-of-a-kind ordering and
  the field-timing budget in ~0.1 s; `selftest_dlg` renders it live with rerun, and a failed
  check surfaces the dialog at launch. The vector CSVs ship inside the package
  (`rivercrossing/vectors/`) so the frozen app self-tests against the same data the tests use.
- `__version__` bumped to **0.2.0**.

### Added — macOS VM functional-test lane

- **Local macOS functional runs now go through a disposable Tart VM.** The suite opens 23 real
  windows and used to take over the developer's desktop; a crashed run could foul the session
  (measured on a sibling project). `scripts/setup_functional_vm.sh` provisions a reusable
  template once; `scripts/run_functional_tests_vm.sh` clones it per run (APFS copy-on-write),
  runs the CI stage-3 pytest command headless in the guest, pulls the screenshots back, and
  deletes the clone. A bare `nox -s functional` on a Mac now refuses unless
  `RIVERCROSSING_HOST_FUNCTIONAL=1` is set (CI is exempt via `CI`). Local-dev only — CI stage 3
  is unchanged on both hosted runners.

### EPIC 1 (shipped as v0.1.2)

Working EPIC 1 of 9 — the runnable UI shell (D1), plus the Phase 8 D1-polish, Phase 9
Windows-installer, Phase 10 Windows-parity and Phase 11 tagged-release follow-ups.

### Added — Phase 11 (tagged releases)

- **A version tag now publishes the release with both installers.** Tagging previously built
  nothing — no workflow listened to tags (the v0.1.1 release shipped with zero assets, on code
  still versioned 0.1.0). `ci.yml` now triggers on `v*` tags: the tag runs the full two-OS
  gauntlet and, only when green, a new stage-6 `release` job publishes the GitHub release with
  `RiverCrossing-<version>-macos.dmg`, `RiverCrossing-<version>-windows-setup.exe` and
  `SHA256SUMS.txt` (auto-generated notes). The job hard-fails when the tag does not match
  `rivercrossing.__version__`, so a mistagged push produces a red run instead of a wrong-artifact
  release. Signing remains EPIC 9 — the published installers are the unsigned R-01 builds.
- `__version__` bumped to **0.1.2** (0.1.1 is retired: its tag was cut over 0.1.0 code before the
  automation existed).

### Changed — Phase 10 (Windows parity)

- **Windows now gates CI equally with macOS.** The advisory machinery (two non-blocking Windows
  jobs, twelve `continue-on-error` steps, summary tables standing in for red checks) is gone:
  stages 1–3 run as a two-OS matrix with the same desktop-session probe, coverage and
  failure-screenshot artifacts on both platforms, and both stage-5 build jobs block while producing
  the installable artifacts (unsigned DMG, unsigned NSIS setup `.exe`). The design-doc deviation
  (spec §14, R-75) is reversed — Windows testers are available and the deviation's premise is gone.
- **Every Windows failure the advisory mask had been hiding was root-caused and fixed** (all
  measured on windows-latest, run 31015653629 → green in run 31036832940: unit 846 passed,
  functional 678 passed / 0 failed):
  - The dialog-harness safety net clobbered every successful modal result with its `-999` sentinel
    on wxMSW (47 tests): `IsModal()` does not clear within the pending-events pass that runs the
    action's own `EndModal`, so the sentinel now re-queues one pass later and fires only while the
    return code is unset.
  - MSW `GetDefaultItem()` follows the focused control and ignores a bare `<default>1</default>`
    (3 tests): the three dialogs missing `<focused>1</focused>` now focus their intended default —
    which also closes a latent §13 gap (`finish_confirm_dlg` never focused Cancel).
  - Scenario subprocesses hung in the Windows close-confirmation modal at cleanup and died with
    empty pipes (13 tests): scenario cleanup now closes through the `really_quitting` seam, the
    child flushes its JSON envelope and enables `faulthandler`, and the triplicated runner lives
    once in `tests/functional/scenario_runner.py`.
  - `entry_detail` was the only window with a purely `Fit()`-derived width and measured 650 under
    Segoe UI metrics (1 test): it now forces its measured 726 px minimum like its three siblings.
  - mypy's output arrived ANSI-colorized under CI's `FORCE_COLOR=1` on win32 (1 test): the protocol
    probe passes `--no-color-output`.
- **Platform-divergent behaviors are pinned by platform-specific tests both ways**: win32-only
  tests assert the Windows close button runs the same confirmation as File ▸ Exit (cancel stays,
  confirm quits) and that a theme change posts the "takes effect at next launch" notice with the
  radio checked; the macOS hide-on-close/reopen and live theme-switch tests are darwin-only.

### Added — Phase 9 (Windows installer)

- **An unsigned per-user Windows installer** (the unsigned half of E9.1.2, pulled forward):
  `installers/windows.nsi` installs the dev bundle under `%LOCALAPPDATA%\Programs\RiverCrossing` with
  a Start-menu entry and a Programs-and-Features uninstall entry — no administrator prompt — and its
  uninstaller removes the directory, the shortcut and the registry key. Version, payload and output
  path arrive as guarded `makensis` defines (single source: `rivercrossing.__version__`).
- **A native local compile loop**: `brew install makensis`, then `nox -s winsetup` compiles the script
  on macOS against a synthetic payload under `build/`; `nox -s winsetup_smoke` runs the compile smoke
  everywhere and the install/launch/uninstall tests on win32.
- **A Windows packaging CI job** (advisory at introduction, blocking since Phase 10): dev bundle →
  bundle smoke → `choco install nsis` → compile → E9.1.2's silent install/launch/uninstall suite,
  uploading the setup `.exe`, the Windows dev bundle (spec §14's runnable Windows artifact, for the
  first time) and `build/winsetup-logs/` diagnostics on every outcome.
- **SmartScreen instructions** for testers in README ▸ Testing on Windows — the R-01 "More info →
  Run anyway" step lives there until the E8 user guide exists.

### Added — Phase 8 (D1 polish)

- **Quitting always confirms.** File ▸ Exit / ⌘Q / app-menu Quit now work: with a ride running the
  three-button `exit_running_dlg` appears (Cancel · Finish ride first… · Quit — keep ride running);
  otherwise the new `exit_confirm_dlg` asks before exiting. Dock ▸ Quit and log-out run the same
  confirmation. This amends the original "otherwise quits" contract (R-51, spec §15) — the design
  documents were updated first.
- **macOS close button hides, never quits.** The red ✕ hides the window and the app keeps running;
  clicking the Dock icon brings it back (a `MacReopenApp` override — wx's default only restores
  minimized windows). On Windows the ✕ runs the quit confirmation.
- **The console entry row is now the "Record crossing" frame**: a native `wxStaticBoxSizer` around a
  larger plate field (1.5× relative type, wider minimum, "Plate number" hint) with a new
  "Record (Enter)" button — Enter and the button both submit exactly once, clear the field, and
  return focus. The console now starts with the entry focused and its ride state applied.
- **Live dark mode.** View ▸ Theme ▸ System/Light/Dark apply through `wx.App.SetAppearance` — on
  macOS the switch is immediate and restyles existing windows; on Windows (wxWidgets 3.3.3 pin)
  appearance can only be set before the first window, so the app posts an honest "takes effect at
  next launch" notice. Selecting System re-applies on OS appearance changes (best-effort — wx pins
  the current appearance rather than truly following the system). The documented `mi_theme_system` /
  `mi_zoom_100` menu defaults are now actually checked at launch.
- **A real app icon** (playing card + stopwatch): SVG sources under `installers/branding/svg/`,
  generated `RiverCrossing.icns` / `rivercrossing.ico` committed (no PNG enters git; intermediates
  live under `build/`), applied to the .app bundle and the Windows executable by the PyInstaller
  spec. `tools/gen_app_icons.py` + `nox -s gen_branding` regenerate everything.
- **An unsigned drag-to-Applications DMG**: `dmgbuild` config at `installers/dmg_settings.py`
  (Applications symlink, dual-resolution background with a drag arrow, volume icon), built by
  `nox -s dmg`, smoked by `nox -s dmg_smoke` (mounts, verifies contents, detaches), and produced by
  the macOS CI build job as `dist/RiverCrossing-<version>.dmg`. Code-signing and notarization remain
  EPIC 9 (E9.1.3).

### Added

- Repository foundations: `pyproject.toml` as the whole project configuration, `noxfile.py` as the single
  task runner, `scripts/*.sh` wrappers, and the `src/rivercrossing` package skeleton.
- CI (`.github/workflows/ci.yml`) covering stages 1, 2, 3 and 5 of the six-stage gauntlet in
  `design/docs-md/spec.md` §14.
- `AGENTS.md` (with `CLAUDE.md` as a symlink to it), `CONTRIBUTING.md` and
  `CODINGSTANDARDS-UX-DESKTOP.md`.
- `tools/toolkit_probe.py` — the wxPython API-surface probe used for the toolkit gate below, kept so the
  matrix can be re-run after any wxPython upgrade.
- `tools/ci_gui_probe.py` — proves a CI runner really has a usable desktop session before the functional
  suite depends on one.
- The `design/` build contract is now tracked in git.

### Changed

- **The Windows installer toolchain is NSIS, not the Inno Setup named in the design documents** —
  amended docs-first in Phase 9 (R-01, spec §10/§14, module-skeletons, task-briefs E9.1.2,
  project-plan). Evidence: `makensis` 3.12 compiles Windows installers natively on macOS (Homebrew
  arm64 bottle — measured in the Phase 9 toolchain probe), while Inno's ISCC runs on macOS only under
  Wine, and Homebrew's `wine-stable` cask is deprecated (fails the Gatekeeper check) and is disabled on
  2026-09-01. NSIS is absent from the windows-2025 runner image, so CI installs it with the
  preinstalled Chocolatey (`choco` nsis 3.12.0 matches the local brew 3.12). Measured en route and
  encoded in the nox session: `makensis` crashes with `std::bad_alloc` under an unset locale (NSIS
  bug 1165), so every invocation forces a UTF-8 locale.
- **wxPython pinned to `~=4.3.1` (wxWidgets 3.3.3), not the `~=4.2.5` in the design documents.** The
  documents justify 4.2.5 with "no stable 4.3 wheel exists (July 2026)"; 4.3.0 shipped 2026-07-28 and
  4.3.1 on 2026-07-30. 4.3.1 provides `wx.App.SetAppearance`, so R-03's dark mode works on both platforms
  instead of being disabled on Windows behind a capability check.
- `.gitignore` no longer ignores `installers/*.spec`. The stock Python template's `*.spec` rule would have
  silently excluded the hand-authored PyInstaller spec, which is tracked source.

### Removed

- **`wxasync` is not a dependency.** Version 0.49 (released 2023-04-25) is functional under wxPython
  4.3.1 / Python 3.14 — `WxAsyncApp` constructs and an `AsyncBind` coroutine fires — but it cannot be
  torn down: one teardown path segfaults (SIGSEGV) and another hangs. A plain `wx.App` on the identical
  stack exits cleanly, so the fault is wxasync's. Beyond CI, a segfault on quit would make every clean
  exit indistinguishable from a crash, which is exactly the signal `app_session.closed_at` and the resume
  dialog wording depend on (R-52). The async writer decision moves to EPIC 5.

### Notes — toolkit gate findings (macOS arm64, Python 3.14.6)

`wxPython 4.3.1 osx-cocoa (phoenix), wxWidgets 3.3.3, sip 6.15.1` — 14/14 probe checks passed. Two
limitations were found that the XRC authoring has to work around, and both would apply on 4.2.5 as well:

- **`wxInfoBar` cannot be authored in XRC.** The resource loads, but yields a generic `Control` wrapping
  the generic info-bar internals rather than a `wx.InfoBar`, and the `name` attribute is dropped —
  so `FindWindowByName` cannot reach it. The four info bars (`resume_infobar`, `reopened_infobar`,
  `finished_infobar`, `stale_infobar`) are therefore constructed in code and named with `SetName()`.
  Contradicts `spec.md` §15b, which states XRC owns "wxInfoBar shells".
- **`wxDataViewListCtrl`'s XRC handler hard-forces the control name** to `dataviewCtrl`, discarding the
  authored name. All ten list controls are authored as `wxDataViewCtrl`, whose `name` is honoured.
  Required in any case: `DataViewListStore.SetAttrByRow` and `DataViewListCtrl.SetItemAttr` do not exist,
  so the bold short-lap row required by R-34 needs a `DataViewIndexListModel` subclass overriding
  `GetAttrByRow` — verified working.

Also confirmed: XRC name resolution including negative lookups, `wxRB_GROUP` radio defaults,
`wxStdDialogButtonSizer` with stock IDs and custom labels, a menubar loaded from XRC with an `F5`
accelerator plus check and radio items, a 53-bitmap `ImageList`, splitter sash round-tripping,
`wx.adv.HyperlinkCtrl`, `DatePickerCtrl`, `TimePickerCtrl`, `EditableListBox`, `wx.adv.Sound`,
`wx.UIActionSimulator`, and screenshot capture.

### Notes — temporary platform deviation (reversed in Phase 10)

From EPIC 1 until Phase 10, macOS was the hard CI gate and `windows-latest` ran without blocking,
because no Windows test machine was available to act on a failure. Phase 10 reversed the deviation:
every hidden Windows failure was root-caused and fixed (see Changed — Phase 10), Windows testers are
available, and both platforms now gate as R-75 and `spec.md` §14 always required.
