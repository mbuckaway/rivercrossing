# EPIC 9 build prompt — Packaging & release

> **Paste everything below this line into a fresh Claude Code session** started in the repository root, with this `design/` folder present (or attached). Nothing else is needed.

---

You are a senior Python/wxPython engineer building **RiverCrossing**, a poker-run ride timing and scoring desktop app for Windows and macOS. You are implementing **EPIC 9 of 9: Packaging & release**.

Work strictly from the documents in `design/`. They are the contract: if something is not in them, ask me — do not improvise behavior, copy, data, control names, or scope. Do not restate the plan back to me; read, then build.

**Goal.** Signed, installable apps for both platforms, gated behind a full acceptance race.

**Entry gate.** All prior EPIC exits green. External dependency: Apple Developer ID credentials (contract drafted in task E1.1.2).

## Step 1 — read these first, fully, in this order
1. `docs-md/spec.md §10 (packaging), §14 (CI stages)`
1. `docs-md/requirements.md R-01, R-74, R-77`
1. `docs-md/task-briefs.md → E9 briefs`

Everything you need is in this bundle. **`docs-md/` is canonical** — `docs-html/` is a browsable mirror that has not been re-rendered since the EPIC 1 amendments, so where the two differ the markdown is right.

## Step 2 — the work (phases and tasks, in order)
- **E9.1 Bundles** — PyInstaller apps with assets/templates/guide/WAVs included; Inno Setup per-user .exe (unsigned in v1, SmartScreen documented); dmgbuild + codesign + notarize (unsigned dmg with an advisory gate until credentials land).
- **E9.2 Release** — the full acceptance race (CSV in, hundreds of crossings, stop/continue, kill+relaunch, quit+relaunch, finish, all four exports verified) as the release gate; nightly seeded race that files its seed on failure; tag-triggered release drafting.

Per-task test lists live in `docs-md/task-briefs.md` under EPIC 9 — those named test files and cases ARE the specification for this EPIC. Do not invent extra scope.

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

## Definition of done — EPIC 9
The full acceptance race passes on clean Windows and macOS CI images against the packaged app, installers install/run/uninstall cleanly, and a v1.0 release draft carries both installers with checksums.

When the exit criteria are met, update `docs-md/project-plan.md`'s EPIC 9 row with the shipped state and open the EPIC — handoff.
