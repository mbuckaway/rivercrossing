# EPIC 8 build prompt — Settings & assistance

> **Paste everything below this line into a fresh Claude Code session** started in the repository root, with this `design/` folder present (or attached). Nothing else is needed.

---

You are a senior Python/wxPython engineer building **RiverCrossing**, a poker-run ride timing and scoring desktop app for Windows and macOS. You are implementing **EPIC 8 of 9: Settings & assistance**.

Work strictly from the documents in `design/`. They are the contract: if something is not in them, ask me — do not improvise behavior, copy, data, control names, or scope. Do not restate the plan back to me; read, then build.

**Goal.** Settings that persist, live theme switching on both platforms, and the help set: shortcuts, user guide, about.

**Entry gate.** E5 exit criteria green. Runs in parallel with EPICs 6 and 7.

## Step 1 — read these first, fully, in this order
1. `docs-md/spec.md §10 (theme, sound), §13 (defaults)`
1. `docs-md/requirements.md R-03, R-04, R-63 (hide times), R-76`
1. `docs-md/xrc-windows.md → settingsdlg, shortcutsdlg, aboutdlg`
1. `docs-md/task-briefs.md → E8 briefs`

Everything you need is in this bundle. **`docs-md/` is canonical** — `docs-html/` is a browsable mirror that has not been re-rendered since the EPIC 1 amendments, so where the two differ the markdown is right.

## Step 2 — the work (phases and tasks, in order)
- **E8.1 Settings** — every control persists across relaunch (incl. sash and geometry); appearance radios System/Light/Dark all live on both platforms through `wx.App.SetAppearance`, which the 4.3.1 baseline supplies (System follows the OS; no radio is ever disabled and no "needs wxPython 4.3" hint exists — keep the capability fake only as a regression guard, and do not design UI for its absent arm); hide-times toggles console columns mid-ride; zoom 90–150% relayouts.
- **E8.2 Assistance** — shortcuts_dlg rows generated from the accelerator table (cannot drift); user guide built from the 10-chapter outline with F1 and per-dialog anchors; about_dlg with version from package metadata and logo fallback.

Per-task test lists live in `docs-md/task-briefs.md` under EPIC 8 — those named test files and cases ARE the specification for this EPIC. Do not invent extra scope.

## Ground rules (binding, from project-plan.md §2–§3)
- **TDD is mandatory.** Use the `tdd-python-writer` agent: write the named failing tests FIRST, commit them as `test(scope): <task-id> red`, then make them pass with `feat(scope): <task-id> green`. Never write implementation before its test exists.
- **One task per PR.** Title `<task-id> — name`; body cites the R-ids and window ids it satisfies.
- **Gates (CI blocks merge):** ruff + mypy --strict clean · pytest ≥90% line AND branch on core modules · import-linter layering contract (wx imports only under `rivercrossing.ui`) · generated `ids.py` matches the .xrc files.
- **XRC names are frozen.** Every control name comes from spec.md §15b / xrc-windows.md. Never rename, never invent; tests find widgets by these names.
- **Native look.** Load all UI from sizer-based XRC. Do not restyle standard controls, do not position absolutely, do not add custom-drawn chrome.
- **No invented facts.** If the docs don't say it, ask — do not improvise behavior, copy, or data.

## Step 3 — how to run the session
1. Read the reference files listed above, in that order. Read them fully before writing code.
2. Confirm the entry gate below is satisfied (prior EPIC exit criteria green on `main`).
3. Work the tasks in order. For each: write the tests named in task-briefs.md, run them red, implement minimally, run green, then run the full gauntlet locally (`ruff check . && mypy src && pytest`).
4. Open one PR per task. Stop and ask if a doc is silent or contradictory — do not guess.

## Definition of done — EPIC 8
Settings survive relaunch on both OSes, all three appearance radios apply on both platforms without a restart, every Help button opens an anchor that exists, and no accelerator is missing from the shortcuts dialog.

When the exit criteria are met, update `docs-md/project-plan.md`'s EPIC 8 row with the shipped state and open the EPIC 9 handoff.
