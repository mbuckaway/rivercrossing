# EPIC 8 — Session Summary and Hand-off

**Written:** 2026-08-30 · **Branches:** `topic/epic-8-settings-assistance` (merged as PR #15, `a4c691d`) then `topic/epic-8-finish` (this close-out) · **Version:** `0.8.0`
**Purpose:** EPIC 8 (Settings & assistance) is implemented and verified; this records what shipped, the decisions taken, and what the next machine needs for EPIC 9.

---

## Status at a glance

| Item | State |
|---|---|
| E8.1.1 settings persistence (per-user JSON config file) | **DONE** — merged in PR #15 |
| E8.1.2 appearance (Settings-dialog half + capability guard) | **DONE** — merged in PR #15 |
| E8.1.3 hide-times live toggle (View menu ↔ Settings) | **DONE** — merged in PR #15 |
| E8.1.4 zoom 90–150% (`ui/zoom.py`) | **DONE** — merged in PR #15 |
| E8.2.1 shortcuts dialog (rows from the accelerator table) | **DONE** — merged in PR #15 (incl. the view-lifetime fix) |
| E8.2.2 user guide (`docs/user-guide.html` + `ui/help.py` + F1) | **DONE** — `topic/epic-8-finish` |
| E8.2.3 About box (version + logo fallback + gorba link) | **DONE** — `topic/epic-8-finish` |
| Windows-agent hand-off | **DONE** — `docs/WINDOWS-AGENT-HANDOFF.md` |
| Version 0.8.0 + design write-backs + this handoff | **DONE** |
| Headless gates (lint, mypy, import-linter, ids_drift, unit ≥90%) | **GREEN** — 2520 passed, 98.05% line / 97.24% branch |
| Functional suite (Tart VM, `--no-audio`) | **GREEN** — full suite exit 0 |
| PR | `topic/epic-8-finish` — the single PR that completes EPIC 8 |

---

## 1 · What shipped

- **E8.1.1 Settings persistence.** Per-user `settings.json` under `platformdirs.user_config_dir("RiverCrossing")` — the user decision: app preferences stay out of the ride DB, so no schema migration. `ui/presenters/settings.py` gained `AppSettings.splitter_sash`/`window_geometry`, `default_path`/`load_settings`/`save_settings` (plain module functions, SIMPLECODE Rule 5), `ZOOM_LADDER`/`DEFAULT_ZOOM_PERCENT`; missing/corrupt files fall back to defaults and zoom clamps onto the 90–150 ladder. The bootstrap (`build_main_window(settings_path=…)`) loads, applies, and persists; `MainFrame`'s process-lifetime sash global is replaced by the disk-backed seam (`initial_sash`/`initial_geometry`/`on_layout_changed`). The two stale `settings`-table notes in `store/schema.py`/`store/__init__.py` now point at the config file.
- **E8.1.2 Appearance.** `views/settings.py` (`SettingsDialog`) renders and collects all settings; the System/Light/Dark radios drive the same `theme.ThemeController` as the View menu, mirrored both ways and persisted (R-03). `theme.apply()` gained the capability guard (falls back to `None` if `SetAppearance` is absent; no UI for the absent arm).
- **E8.1.3 Hide-times.** View-menu `mi_hide_times` toggles the Lap time/Total columns live mid-ride (R-63 companion), mirrored with the Settings checkbox and persisted.
- **E8.1.4 Zoom.** New `ui/zoom.py` (`ZoomController`, `percent_for_menu_id`, recursive base-font capture so re-zooming never compounds); View radios + Settings `zoom_choice` mirror and persist; every dialog opened later inherits the zoom (applied in `_open_target` before the view binds).
- **E8.2.1 Shortcuts dialog.** `views/shortcuts.py` generates `shortcuts_list` rows from `ui.accelerators.ACCELERATOR_TABLE` (cannot drift), generativity-proven by an injected-row functional test. The view is kept alive for its dialog via `dialog.shortcuts_view = self` (the `frame.console`/`frame.presenter` precedent) — without it the model's Python wrapper is collected and `GetModel()` loses `GetCount` (measured in the VM).
- **E8.2.2 User guide.** `docs/user-guide.html` is complete: 10 chapters + 2 appendices per the 6a outline, self-contained and light/dark-aware, with stable per-section anchors. New `ui/help.py` maps every window/dialog to its anchor (`ANCHOR_BY_WINDOW`), and the F1 / Help ▸ User Guide route opens the guide deep-linked to the currently active top-level window (focus path → topmost modal dialog → `GetTopWindow` fallback), defaulting to `#getting-started`.
- **E8.2.3 About box.** `views/about.py` shows the package version, the gorba.ca hyperlink, and the ride logo with a layered app-icon fallback (frame icon → stock icon → drawn placeholder) that never yields `wx.NullBitmap`.
- **VM hygiene.** `scripts/run_functional_tests_vm.sh` and `scripts/setup_functional_vm.sh` now pass `--no-audio` to `tart run` (guest WAV cues no longer reach the host speakers; `sound.py` already degrades to silence with no audio device). Every functional scenario builds the app through the hermetic `_build_app_window` helper (per-scenario tmp settings path — the real user config dir is never touched).

## 2 · Decisions recorded

- **Settings live in a per-user config file, not the ride DB** (user decision). Recorded in `project-plan.md`'s E8 row; the two `store` docstring notes now say so.
- **The E8 geometry test asserts platform-agnostically** (equivalence + round trip) instead of a hardcoded size, because wxMSW pins `main_frame` to its sizer minimum (measured: a saved 1200×800 restores as 1100×788 on the Windows runner, larger than its 1024×720 work area; macOS honours `SetSize` fully). The suspected root cause — `main_frame`/`results_frame` declare no XRC `<style>`, so `wxRESIZE_BORDER` may be missing on Windows — is handed to the Windows agent (`docs/WINDOWS-AGENT-HANDOFF.md`) for verification on a real box; no frozen `.xrc` was changed from the Mac.
- **The About-logo functional assertion is size-based**, not `IsSameAs` (macOS `wxStaticBitmap` scales the set bitmap to the control's size — an 8×8 file renders as 16×16 — and cross-decode `IsSameAs` is unreliable); the view's resolved `logo_bitmap` must be the file's own 8×8, never the 16×16 stock fallback.
- **`wx.HyperlinkCtrl` is `wx.adv.HyperlinkCtrl`** (like `wx.adv.Sound`); the XRC class name is `wxHyperlinkCtrl`. VM-caught.
- **Tart `--no-audio`** for all functional VM runs (user request).

## 3 · Known / carried forward

- **`main_frame` resizable-on-Windows question** — the primary item in `docs/WINDOWS-AGENT-HANDOFF.md`; needs a real Windows box to confirm `wxRESIZE_BORDER` and, if absent, add `<style>wxDEFAULT_FRAME_STYLE</style>` to `main.xrc`/`results.xrc`.
- **wx-churn functional flakes** — the usual files (`test_rider_editor.py`, `test_harness.py`, `test_empty_state_screenshots.py`, `test_ride_library_live.py`, `test_results_exports.py`, `test_view_support.py`, occasionally `test_settings.py`) still flake under wx/SIP wrapper-cache corruption; the fresh-process rerun wrapper (budget 4) converges each run. No in-process repair exists (WINDOWS-DEBUG-SESSION-SUMMARY Addendum 2).
- **E9 packaging** — the user guide (`docs/user-guide.html`) must be bundled (E9.1.1 asset completeness); `ui/help.guide_path()` already resolves a bundled location first.

## 4 · Resuming — EPIC 9 (Packaging & release)

Entry gate: all prior exits. External: signing credentials (contract E1.1.2). Brief: `design/epic-prompts/EPIC-9-packaging-release.md`; task list under `design/docs-md/task-briefs.md` E9 block. The unsigned halves (PyInstaller dev bundle, NSIS, dmg) already exist; E9 hardens them into release bundles (templates, guide, WAVs, license texts, version metadata) and wires signing/notarization when creds land.
