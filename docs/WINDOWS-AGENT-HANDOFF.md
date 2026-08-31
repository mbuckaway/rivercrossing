# Windows Agent Hand-off — items from the EPIC 8 macOS session

**Written:** 2026-08-30 · **Branches:** `topic/epic-8-settings-assistance` (merged as PR #15, `a4c691d` on `master`) · current work continues on `topic/epic-8-finish`
**Purpose:** Hand the Windows agent the one product-correctness item this macOS session measured but could not verify or fix from a Mac, plus the supporting evidence and a short list of already-fixed or known items so nothing is re-investigated.

At the time of writing, PR #15 is merged and **every CI stage is green on both OSes** (static, unit, functional UI, dev bundle, GitGuardian — `macos-latest` and `windows-latest`). The items below are therefore not blocking anything; they are either (a) a suspected Windows-only product bug worth confirming, or (b) notes so you do not re-litigate what is already resolved or already documented.

---

## 1 · PRIMARY — `main_frame` appears non-resizable on Windows (pinned to its sizer minimum)

This is the one item that needs a real Windows machine. It was measured, but the mechanism could not be confirmed from a Mac and no code change was made.

### The observation (measured on windows-latest CI, 2026-08-30)

The EPIC 8 settings-persistence functional scenario (`_settings_persistence_round_trip` in `tests/functional/console_subprocess_scenarios.py`) saves a frame geometry and relaunches. The numbers from the Windows runner:

| What was attempted | Result on **windows-latest** | Result on **macOS** (Tart VM guest) |
|---|---|---|
| App restore of saved `(40, 60, 1200, 800)` | `(40, 60, 1100, 788)` — **position applied, size pinned** | `(40, 60, 1200, 800)` — full honour |
| Explicit `frame.SetSize((1250, 860))` in the scenario | `(1100, 788)` — **size ignored** | `(1250, 860)` |
| Display work area of the runner (`wx.Display(...).GetClientArea()`) | `1024 x 720` | `1024 x 681` |

Key facts:

- The pinned size `(1100, 788)` is **larger than the display work area** (`1024 x 720`), so this is **not** display clamping — a window was created bigger than the screen, which Windows permits.
- `1100` equals `MIN_SIZE.width` (`main_frame.py:84`, `MIN_SIZE = (1100, 700)`). `788` is the frame's sizer-computed minimum **height** on Windows (native fonts lay the console out taller than on macOS; the macOS sizer minimum is much smaller, ~429x373).
- Every `SetSize` to a size above the sizer minimum still lands on `(1100, 788)`. This is the signature of a frame that wxMSW keeps pinned to its sizer minimum — consistent with a frame created **without `wxRESIZE_BORDER`** (a non-resizable top-level window with a sizer snaps to the sizer minimum on MSW; macOS does not behave this way).

### Where to look

- `src/rivercrossing/ui/xrc/main.xrc` — `main_frame` (line 35) declares **no `<style>`** element. The XRC `wxFrame` handler's default style is the open question: if the default does not include `wxRESIZE_BORDER` on this wxWidgets pin (3.3.3), the console is not resizable on Windows.
- `src/rivercrossing/ui/xrc/results.xrc` — `results_frame` (line 42) also declares no `<style>`; check it the same way.
- `src/rivercrossing/ui/views/main_frame.py` — the E8.1.1 geometry restore is at `__init__` (~line 258: `SetMinSize(MIN_SIZE)` → `SetSize(MIN_SIZE)` → restore `SetPosition`/`SetSize` from the saved geometry).

### Why it matters (the requirement)

- **R-05** and **spec.md §13** require resizable windows ("dialogs resizable per R-05 — R-04's 90–150% text zoom cannot reflow inside a fixed dialog").
- **xrc-windows.md section A**: "Min frame 1100×700, fits 1366×768 — declared as `<size>` and re-applied with `SetMinSize()`" — a minimum, not a fixed size.
- **R-04 / E8.1.4** zoom relayouts the console; a non-resizable frame on Windows would defeat the zoom's purpose and the operator's ability to size the window.

### What to do on a real Windows box

1. Launch the app, grab `main_frame`'s edge and drag — can it be resized larger than ~1100×788?
2. Inspect the style at runtime: `frame.GetWindowStyleFlag()` — is `wx.RESIZE_BORDER` set?
3. Confirm the XRC frame-handler default on the 4.3.1/3.3.3 pin (check `wxWidgets/src/xrc/xh_frame.cpp`: the `GetStyle` default for `<object class="wxFrame">` when no `<style>` is declared).
4. If `wxRESIZE_BORDER` is missing, the fix is almost certainly declaring it in the `.xrc` — the explicit, self-documenting form would be:

   ```xml
   <style>wxDEFAULT_FRAME_STYLE</style>
   ```

   on `main_frame` (and `results_frame` if affected). `wxDEFAULT_FRAME_STYLE` is the documented default, so declaring it is a no-op on platforms where the default already applies (macOS) and only changes behavior where the implicit default is deficient.
5. Re-run the E8.1.1 persistence scenario on Windows and confirm the saved `(1200, 800)` restores exactly, and that a user-drag resize persists across relaunch.

### What was NOT changed, and why

The EPIC 8 test was made **platform-robust instead** (PR #15): `tests/functional/test_settings.py` + the scenario now assert the honest invariants — the file-driven restore equals directly applying the same values, the saved file equals the frame's real state, and the relaunch round-trip is exact — without hardcoding a size. CI is green on both OSes. A frame-style change to the frozen `.xrc` design was deliberately **not** made from a Mac without Windows validation; that is your call to make and verify.

---

## 2 · ALREADY FIXED on the macOS side (do not re-investigate)

- **`test_store_previous_session_crashed_without_heartbeat_uses_opened_at` (unit, Windows flake).** The test read `_session_row` *before* the second `Store.open`, so `ORDER BY id DESC LIMIT 1 OFFSET 1` returned the arrange helper's session instead of the crash session; comparing the wrong row's `opened_at` flaked across a second boundary on the slow Windows runner (`19:29:09` vs `19:29:08`). Fixed in PR #15 by reading the row after the second open (the sibling tests' pattern). Deterministic now; Windows unit is green.
- **`ShortcutsDialog` view lifetime (functional, both OSes).** `ShortcutsDialog` binds no events, so nothing kept the view — and its `DataViewIndexListModel` — alive after `_open_target` dropped the construction result; a collected model left `GetModel()` re-wrapping the C++ object as a base `DataViewModel` without `GetCount`, failing the route-level shortcuts scenario in the VM. Fixed in PR #15 with the repo's `frame.console = self` / `frame.presenter` precedent: `dialog.shortcuts_view = self` in `ui/views/shortcuts.py`.

---

## 3 · KNOWN / DOCUMENTED (already covered elsewhere — context only)

- **wx-churn flakes on Windows functional.** The functional suite still absorbs intermittent per-file failures via `tools/functional_rerun.py` (fresh-process reruns, budget 4). Files seen flaking in the 2026-08-30 runs: `test_rider_editor.py`, `test_harness.py`, `test_empty_state_screenshots.py`, `test_ride_library_live.py`, `test_results_exports.py`, `test_view_support.py`, and once `test_settings.py` (a `find_control` address-reuse `LookupError` on the second dialog construction in `settings_dialog_ok_applies_and_persists_dark` — the documented `_support.py` hazard). This is the upstream wx/SIP wrapper-cache corruption (Phoenix #2931 / sip#113), already documented in `docs/WINDOWS-DEBUG-SESSION-SUMMARY.md` (Addendum 2) and `noxfile.py`'s `functional` docstring. No action requested; noted so a red run is not mistaken for a regression.
- **Scenario settings hermeticity.** Since E8.1.1, every functional scenario builds the app with a per-scenario tmp settings path (`_build_app_window` in `console_subprocess_scenarios.py`), so no scenario reads or writes the guest's real user-config dir. If you add Windows scenarios that launch the app, use the same helper — do not let them touch the real `%APPDATA%` config.

---

## Contact points in the code

- `src/rivercrossing/ui/views/main_frame.py` — `MIN_SIZE`, the E8.1.1 geometry restore, `persist_layout`/`_restore_sash_position`.
- `src/rivercrossing/ui/xrc/main.xrc` / `results.xrc` — the two style-less frames.
- `tests/functional/console_subprocess_scenarios.py` — `_settings_persistence_round_trip`, `_build_app_window`, `_frame_geometry`.
- `tests/functional/test_settings.py` — the platform-robust geometry assertions (PR #15).
