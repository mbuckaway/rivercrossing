# Changelog

All notable changes to RiverCrossing are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Working EPIC 1 of 9 — the runnable UI shell (D1), plus the Phase 8 D1-polish follow-up.

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

### Notes — temporary platform deviation

macOS is the hard CI gate; `windows-latest` runs but does not block, because no Windows test machine is
available to act on a failure. This deviates from R-75 and `spec.md` §14, which require both platforms
green before any release, and should be reversed once a Windows machine exists.
