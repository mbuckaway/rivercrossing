# EPIC 1 build prompt — Runnable UI shell (D1)

> **Paste everything below this line into a fresh Claude Code session** started in the repository root, with this `design/` folder present (or attached). Nothing else is needed.

---

You are a senior Python/wxPython engineer building **RiverCrossing**, a poker-run ride timing and scoring desktop app for Windows and macOS. You are implementing **EPIC 1 of 9: Runnable UI shell (D1)**.

Work strictly from the documents in `design/`. They are the contract: if something is not in them, ask me — do not improvise behavior, copy, data, control names, or scope. Do not restate the plan back to me; read, then build.

**Goal.** Ship the entire application shell — all 23 windows, the full menu system, every dialog — running on Windows AND macOS with demo data behind a removable seam. No engine, no database.

**Entry gate.** None — this is the first work in the repo.

## Step 1 — read these first, fully, in this order
1. `docs-md/module-skeletons.md` — repo tree, pyproject, module boundaries (build this layout exactly)
1. `docs-md/xrc-windows.md` — all 23 windows: control hierarchy, snake_case names, radio defaults, code-side notes
1. `docs-md/spec.md §15 + §15b` — menu route map (38 rows) and the frozen name registry
1. `docs-md/requirements.md R-01…R-05, R-70…R-77` — platform, XRC-first rule, CI gates
1. `docs-md/task-briefs.md → E1 briefs (E1.1.1 … E1.6.1)` — your test list
1. `screenshots/windows/*.jpg` — visual reference for each window (native chrome will differ)

Everything you need is in this bundle. **`docs-md/` is canonical** — `docs-html/` is a browsable mirror that has not been re-rendered since the EPIC 1 amendments, so where the two differ the markdown is right.

## Step 2 — the work (phases and tasks, in order)
- **E1.1 Foundations** — repo skeleton, pyproject (wxPython~=4.3.1, fpdf2, jinja2 — no `wxasync`; spec.md §10 says why and defers the wx⇄asyncio choice to EPIC 5), CI stages 1–3 on windows-latest + macos-latest with macOS as the blocking gate and Windows advisory until a test machine exists (spec.md §14), import-linter layering contract.
- **E1.2 Contracts** — generated ids.py from .xrc, frozen payload dataclasses, presenter protocols, the removable `rivercrossing.demo` seam (lint rule: importable only from app bootstrap + tests).
- **E1.3 XRC authoring** — 9 .xrc files covering all 23 windows, 53 card bitmaps as an imagelist, the per-screen smoke test (load → show → screenshot → close).
- **E1.4 Menus** — menubar from XRC, every §15 row routed, accelerators (Enter/Ctrl+Z/F1/F5), state-based enablement table. Three rows (Duplicate Ride…, Reopen Ride, Void Card…) have no frozen window: route them to a flagged sentinel and invent no name — EPIC 5 and EPIC 7 author those dialogs mock-first.
- **E1.5 Demo display** — console feed, lists, dialog mechanics (Esc/Enter/focus-return/destructive-confirm) all showing demo data.
- **E1.6 D1 walkthrough** — scripted tour of every control on both OSes; tag v0.1.

Per-task test lists live in `docs-md/task-briefs.md` under EPIC 1 — those named test files and cases ARE the specification for this EPIC. Do not invent extra scope.

## Ground rules (binding, from project-plan.md §2–§3)
- **TDD is mandatory.** Use the `tdd-python-writer` agent: write the named failing tests FIRST, commit them as `test(scope): <task-id> red`, then make them pass with `feat(scope): <task-id> green`. Never write implementation before its test exists.
- **One task per PR.** Title `<task-id> — name`; body cites the R-ids and window ids it satisfies.
- **Gates (CI blocks merge):** ruff + mypy --strict clean · pytest ≥90% line AND branch on core modules · import-linter layering contract (wx imports only under `rivercrossing.ui`) · generated `ids.py` matches the .xrc files.
- **XRC names are frozen.** Every control name comes from spec.md §15b / xrc-windows.md. Never rename, never invent; tests find widgets by these names.
- **Native look.** Load all UI from sizer-based XRC. Do not restyle standard controls, do not position absolutely, do not add custom-drawn chrome.
- **Three classes XRC cannot author** (measured on the 4.3.1 baseline — spec.md §15b has the detail): `wxInfoBar` and `wxMenuBar` lose their `name`, and `wxDataViewListCtrl`'s handler forces the name `dataviewCtrl`. Build the four info bars in code and name them with `SetName()`, author every list as `wxDataViewCtrl`, and load `main_menubar` through `XmlResource.LoadMenuBar()`. Also measured: no window-level `minsize` (use `SetMinSize()`), `<checked>` is a no-op on radio menu items, `wxStdDialogButtonSizer` positions only stock OK/Cancel-family ids, and a bare `&` in a label is a mnemonic (write `&&`).
- **No invented facts.** If the docs don't say it, ask — do not improvise behavior, copy, or data.

## Step 3 — how to run the session
1. Read the reference files listed above, in that order. Read them fully before writing code.
2. Confirm the entry gate below is satisfied (prior EPIC exit criteria green on `main`).
3. Work the tasks in order. For each: write the tests named in task-briefs.md, run them red, implement minimally, run green, then run the full gauntlet locally (`ruff check . && mypy src && pytest`).
4. Open one PR per task. Stop and ask if a doc is silent or contradictory — do not guess.

## Definition of done — EPIC 1
On a clean Windows machine and a clean Mac, the app launches from a CI-built bundle; every menu item opens its window or its flagged sentinel; every dialog obeys Esc/Enter and returns focus; demo data renders in the console feed, rider editor, results and library; the smoke suite is green on both OSes (macOS blocking, Windows reporting until a test machine exists — spec.md §14); deleting `rivercrossing/demo.py` breaks only that seam.

When the exit criteria are met, update `docs-md/project-plan.md`'s EPIC 1 row with the shipped state and open the EPIC 2 handoff.
