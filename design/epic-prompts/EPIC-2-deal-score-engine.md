# EPIC 2 build prompt — Deal & score engine

> **Paste everything below this line into a fresh Claude Code session** started in the repository root, with this `design/` folder present (or attached). Nothing else is needed.

---

You are a senior Python/wxPython engineer building **RiverCrossing**, a poker-run ride timing and scoring desktop app for Windows and macOS. You are implementing **EPIC 2 of 9: Deal & score engine**.

Work strictly from the documents in `design/`. They are the contract: if something is not in them, ask me — do not improvise behavior, copy, data, control names, or scope. Do not restate the plan back to me; read, then build.

**Goal.** A headless, exhaustively tested poker engine: 5-card evaluator, jokers wild, best-5-of-N with an optional card cap, and a seeded multi-deck shoe.

**Entry gate.** E1 exit criteria green (contracts + CI live).

## Step 1 — read these first, fully, in this order
1. `docs-md/spec.md §4 (shoe) + §5 (algorithm, joker vector table, five-of-a-kind rule)`
1. `docs-md/requirements.md R-13, R-16, R-40…R-44, R-72`
1. `docs-md/task-briefs.md → E2 briefs (E2.1.1 … E2.4.1)`
1. `docs-md/module-skeletons.md` — cards.py / hands.py / standings.py APIs

Everything you need is in this bundle. **`docs-md/` is canonical** — `docs-html/` is a browsable mirror that has not been re-rendered since the EPIC 1 amendments, so where the two differ the markdown is right.

## Step 2 — the work (phases and tasks, in order)
- **E2.1 Evaluator** — 7,462-rank table-driven eval5; joker/wild layer incl. five-of-a-kind above straight flush; best-5-of-N with cap X; Hypothesis properties (transitivity, permutation invariance, joker monotonicity).
- **E2.2 Shoe** — seeded Fisher-Yates, deal_index audit, exhaustion → reshuffle cycle, undo restitution; deck count × jokers (0/2/4) config matrix.
- **E2.3 Simulations** — seeded whole-ride sims (180 entries × 6 h; solo, pooled, relay) asserting shoe accounting and runtime budget.
- **E2.4 Self-test dialog** — selftest_dlg runs the real suite and exposes the finish-gate hook consumed by E6.4.3.

Per-task test lists live in `docs-md/task-briefs.md` under EPIC 2 — those named test files and cases ARE the specification for this EPIC. Do not invent extra scope.

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

## Definition of done — EPIC 2
The rank sweep, the 28 joker vectors, the cap fixtures, the property suite and the seeded sims are all green inside the CI time budget, and selftest_dlg reports PASS from the real evaluator on both OSes.

When the exit criteria are met, update `docs-md/project-plan.md`'s EPIC 2 row with the shipped state and open the EPIC 3 handoff.
