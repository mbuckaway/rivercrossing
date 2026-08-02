# EPIC 6 build prompt — Results & publishing

> **Paste everything below this line into a fresh Claude Code session** started in the repository root, with this `design/` folder present (or attached). Nothing else is needed.

---

You are a senior Python/wxPython engineer building **RiverCrossing**, a poker-run ride timing and scoring desktop app for Windows and macOS. You are implementing **EPIC 6 of 9: Results & publishing**.

Work strictly from the documents in `design/`. They are the contract: if something is not in them, ask me — do not improvise behavior, copy, data, control names, or scope. Do not restate the plan back to me; read, then build.

**Goal.** Standings with tie-breaks the scorer can reorder, and four exports: self-contained HTML, PDF report, podium poster, standings CSV.

**Entry gate.** E2 and E5 exit criteria green (task E6.2.1 may start any time after E1).

## Step 1 — read these first, fully, in this order
1. `docs-md/spec.md §5 (tie-breaks), §8 (HTML export), §8b (PDF)`
1. `docs-md/requirements.md R-14, R-60…R-63`
1. `templates/base.html.j2, templates/macros.html.j2, templates/theme.css` — SHIP VERBATIM into src/rivercrossing/htmlexport/templates/
1. `exports/epic-2026-results.html and exports/epic-2026-results-no-times.html` — golden references; their embedded race-data blocks are your test fixtures
1. `docs-md/xrc-windows.md → resultsframe · docs-md/task-briefs.md → E6 briefs`

Everything you need is in this bundle; `docs-md/` holds the markdown docs and `docs-html/` the same documents as browsable HTML (identical content).

## Step 2 — the work (phases and tasks, in order)
- **E6.1 Standings** — tie-break rules ①②③ reorderable with instant re-rank; laps and fastest leaderboards; DNF block last.
- **E6.2 HTML** — CI build step compiling Tailwind + vendoring base64 Barlow subsets (staleness-gated); Jinja2 render with autoescape + StrictUndefined; golden-file and JSON round-trip tests; the `</script>`-in-a-team-name injection case; no-times variant omits time markup AND time JSON.
- **E6.3 PDF** — fpdf2 multi-section report and the one-page podium poster, byte-deterministic across runs and OSes.
- **E6.4 Results window** — publish checkboxes → ExportOptions, Results menu live with FINISHED gating, finish blocked unless the evaluator self-test is green.

Per-task test lists live in `docs-md/task-briefs.md` under EPIC 6 — those named test files and cases ARE the specification for this EPIC. Do not invent extra scope.

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

## Definition of done — EPIC 6
Rendering the committed fixtures reproduces the golden pages byte-for-byte; the exported page loads from `file://` with JavaScript disabled and makes zero network requests; PDF bytes are identical on Windows and macOS.

When the exit criteria are met, update `docs-md/project-plan.md`'s EPIC 6 row with the shipped state and open the EPIC 7 handoff.
