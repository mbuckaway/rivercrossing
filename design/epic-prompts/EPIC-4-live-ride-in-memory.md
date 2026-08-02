# EPIC 4 build prompt — Live ride (in-memory)

> **Paste everything below this line into a fresh Claude Code session** started in the repository root, with this `design/` folder present (or attached). Nothing else is needed.

---

You are a senior Python/wxPython engineer building **RiverCrossing**, a poker-run ride timing and scoring desktop app for Windows and macOS. You are implementing **EPIC 4 of 9: Live ride (in-memory)**.

Work strictly from the documents in `design/`. They are the contract: if something is not in them, ask me — do not improvise behavior, copy, data, control names, or scope. Do not restate the plan back to me; read, then build.

**Goal.** A ride you can actually run: start/stop/continue, plate+Enter crossings under 100 ms, cards dealt per lap, min-lap flags held for review, undo.

**Entry gate.** E2 and E3 exit criteria green.

## Step 1 — read these first, fully, in this order
1. `docs-md/spec.md §2 (state machine), §3 (timing rules), §10 (sound cues)`
1. `docs-md/requirements.md R-30…R-36`
1. `docs-md/xrc-windows.md → mainframe, setstartdlg, stopdlg, finishdlg, continuedlg`
1. `docs-md/task-briefs.md → E4 briefs`
1. `assets/sounds/` — the three cue WAVs (recorded / flagged / error)

Everything you need is in this bundle; `docs-md/` holds the markdown docs and `docs-html/` the same documents as browsable HTML (identical content).

## Step 2 — the work (phases and tasks, in order)
- **E4.1 State machine** — DRAFT→RUNNING→FINISHED→REOPENED with guards, injected wall clock, retro set-start recompute, stop/continue without losing time.
- **E4.2 Crossings** — lap credit + timestamps, unknown-plate rejection cue, min-lap flag holding its card, undo with shoe restitution.
- **E4.3 Dealing** — one card per counted crossing (uncapped in pooled mode); manual-deal and held-release engine paths exposed for EPIC 7.
- **E4.4 Console live** — feed/counters/review panel from the engine, arm→stop→confirm flow, sound cues behind the Settings toggle, and a scripted 20-rider mini race with min-lap lowered.

Per-task test lists live in `docs-md/task-briefs.md` under EPIC 4 — those named test files and cases ARE the specification for this EPIC. Do not invent extra scope.

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

## Definition of done — EPIC 4
The mini acceptance race (start, 60 crossings including flags and an undo, stop, continue, finish) passes through the real UI on both OSes, standings match the hand-verified fixture, and typed plates appear in the feed within 100 ms.

When the exit criteria are met, update `docs-md/project-plan.md`'s EPIC 4 row with the shipped state and open the EPIC 5 handoff.
