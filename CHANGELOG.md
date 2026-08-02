# Changelog

All notable changes to RiverCrossing are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Working EPIC 1 of 9 — the runnable UI shell (D1).

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
