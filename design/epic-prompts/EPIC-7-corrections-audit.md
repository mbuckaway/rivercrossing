# EPIC 7 build prompt — Corrections & audit

> **Paste everything below this line into a fresh Claude Code session** started in the repository root, with this `design/` folder present (or attached). Nothing else is needed.

---

You are a senior Python/wxPython engineer building **RiverCrossing**, a poker-run ride timing and scoring desktop app for Windows and macOS. You are implementing **EPIC 7 of 9: Corrections & audit**.

Work strictly from the documents in `design/`. They are the contract: if something is not in them, ask me — do not improvise behavior, copy, data, control names, or scope. Do not restate the plan back to me; read, then build.

**Goal.** Every operator mistake is fixable and every fix is recorded: edit/void/add crossings, reassign plates, manual deals, DNF, plus a filterable audit trail and REOPENED mode.

**Entry gate.** E5 exit criteria green (the stale-export flag also needs task E6.4.1).

## Step 1 — read these first, fully, in this order
1. `docs-md/spec.md §3 (corrections), §15 Cards menu rows`
1. `docs-md/requirements.md R-33, R-36, R-38`
1. `docs-md/xrc-windows.md → editcrossing, reassigndlg, dealdlg, dnfdlg, auditdlg, entrydetail`
1. `docs-md/task-briefs.md → E7 briefs`

Everything you need is in this bundle. **`docs-md/` is canonical** — `docs-html/` is a browsable mirror that has not been re-rendered since the EPIC 1 amendments, so where the two differ the markdown is right.

## Step 2 — the work (phases and tasks, in order)
- **E7.1 Audited command layer** — every correction requires a reason and writes exactly one audit row; recompute cascades proven by a replay-equivalence property.
- **E7.2 Dialogs live** — all Cards-menu routes and entry-detail buttons wired to real commands; REOPENED mode allows corrections only and re-ranks on "Finish again". **One mock-first step:** §15's Void Card… confirm has no frozen window (it cites the retired 3d pattern), so mock it and register its names in spec.md §15b before wiring, replacing EPIC 1's flagged sentinel.
- **E7.3 Audit viewer + stale-export flag** — newest-first with plate/action filters; corrections after an export raise the stale banner until re-export.

Per-task test lists live in `docs-md/task-briefs.md` under EPIC 7 — those named test files and cases ARE the specification for this EPIC. Do not invent extra scope.

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

## Definition of done — EPIC 7
Every correction path writes an audit row with who/what/when/reason, replaying history with corrections equals the directly corrected state, and a post-export correction visibly marks published results stale.

When the exit criteria are met, update `docs-md/project-plan.md`'s EPIC 7 row with the shipped state and open the EPIC 8 handoff.
