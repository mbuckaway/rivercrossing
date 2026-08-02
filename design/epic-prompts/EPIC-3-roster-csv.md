# EPIC 3 build prompt — Roster & CSV

> **Paste everything below this line into a fresh Claude Code session** started in the repository root, with this `design/` folder present (or attached). Nothing else is needed.

---

You are a senior Python/wxPython engineer building **RiverCrossing**, a poker-run ride timing and scoring desktop app for Windows and macOS. You are implementing **EPIC 3 of 9: Roster & CSV**.

Work strictly from the documents in `design/`. They are the contract: if something is not in them, ask me — do not improvise behavior, copy, data, control names, or scope. Do not restate the plan back to me; read, then build.

**Goal.** Real entries, riders and teams — created in the editor or imported from CSV with a preview-then-commit flow that never writes on preview.

**Entry gate.** E1 exit criteria green. Runs in parallel with EPIC 2.

## Step 1 — read these first, fully, in this order
1. `docs-md/spec.md §1, §7 (CSV column spec, both plate models)`
1. `docs-md/requirements.md R-11, R-12, R-15…R-17, R-20, R-21`
1. `docs-md/xrc-windows.md → ridereditor, csvdlg`
1. `docs-md/task-briefs.md → E3 briefs`

Everything you need is in this bundle; `docs-md/` holds the markdown docs and `docs-html/` the same documents as browsable HTML (identical content).

## Step 2 — the work (phases and tasks, in order)
- **E3.1 Models** — plate uniqueness, team size 2–max(≤10), solo default, pooled vs relay shapes, the state × plate-model lock matrix.
- **E3.2 Editor live** — rider_editor_dlg on real models (next-free plate prefill, delete-blocked-with-data, "New team…", solo-only hides team UI).
- **E3.3 CSV** — preview with exact conflict counts (filesystem untouched), atomic commit, pre-start team reshaping via re-import, export round-trip identity property.
- **E3.4 Preview dialog + solo variant** — Import disabled while conflicts remain.

Per-task test lists live in `docs-md/task-briefs.md` under EPIC 3 — those named test files and cases ARE the specification for this EPIC. Do not invent extra scope.

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

## Definition of done — EPIC 3
A 180-rider EPIC-shaped CSV imports cleanly; each malformed fixture reports exactly the expected conflicts and writes nothing; export → re-import is a value-identical round trip including teams; the editor enforces every lock in the matrix.

When the exit criteria are met, update `docs-md/project-plan.md`'s EPIC 3 row with the shipped state and open the EPIC 4 handoff.
