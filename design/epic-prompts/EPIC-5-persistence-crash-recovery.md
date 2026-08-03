# EPIC 5 build prompt — Persistence & crash recovery

> **Paste everything below this line into a fresh Claude Code session** started in the repository root, with this `design/` folder present (or attached). Nothing else is needed.

---

You are a senior Python/wxPython engineer building **RiverCrossing**, a poker-run ride timing and scoring desktop app for Windows and macOS. You are implementing **EPIC 5 of 9: Persistence & crash recovery**.

Work strictly from the documents in `design/`. They are the contract: if something is not in them, ask me — do not improvise behavior, copy, data, control names, or scope. Do not restate the plan back to me; read, then build.

**Goal.** Nothing is ever lost: an event-sourced SQLite store, clean-quit vs crash detection, resume dialogs, rotating backups — and the demo seam retired.

**Entry gate.** E4 exit criteria green.

## Step 1 — read these first, fully, in this order
1. `docs-md/spec.md §6 (schema), §9 (failure matrix), §3 (resume/continue)`
1. `docs-md/requirements.md R-10, R-18, R-50…R-54`
1. `docs-md/xrc-windows.md → resumedlg, exitdlg, librarydlg, deletedlg`
1. `docs-md/task-briefs.md → E5 briefs`

Everything you need is in this bundle. **`docs-md/` is canonical** — `docs-html/` is a browsable mirror that has not been re-rendered since the EPIC 1 amendments, so where the two differ the markdown is right.

## Step 2 — the work (phases and tasks, in order)
- **E5.1 Store** — schema + migrations, event replay equivalence property, crash-consistency loop (kill mid-race, reopen intact).
- **E5.2 Session bookkeeping** — unclean-close flag, exit-with-running-ride dialog (three buttons: Cancel · Finish ride first… · Quit-keep-running, Cancel the default), resume dialog wording for crash vs quit written into `message_lbl`, reopened banner. The clean-quit signal is why `wxasync` is out (spec.md §10): a segfault on quit would be indistinguishable from a crash, and R-52 reads `closed_at` to tell them apart.
- **E5.3 Backups** — on open + hourly + manual, keep 20; type-name delete guard with backup-first.
- **E5.4 Library live + demo retirement** — remove the DemoDataSource wiring; screens now show real or empty states. **Two mock-first steps land here:** §15's Duplicate Ride… dialog and Reopen Ride confirm have no frozen window (they cite the retired 3d pattern), so mock both and register their names in spec.md §15b before writing UI code, replacing EPIC 1's flagged sentinel.
- **The wx⇄asyncio integration is chosen in this EPIC** — spec.md §10 defers it here because the async writer first appears here, and rules out `wxasync`. Decide it explicitly, with the teardown behaviour tested, before the writer lands.

Per-task test lists live in `docs-md/task-briefs.md` under EPIC 5 — those named test files and cases ARE the specification for this EPIC. Do not invent extra scope.

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

## Definition of done — EPIC 5
Fifty randomized kill-and-reopen cycles lose at most the uncommitted keystroke and never corrupt the DB; quitting mid-ride and relaunching resumes with correct elapsed time; `rivercrossing.demo` is unreachable from app code.

When the exit criteria are met, update `docs-md/project-plan.md`'s EPIC 5 row with the shipped state and open the EPIC 6 handoff.
