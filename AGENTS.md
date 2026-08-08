# RiverCrossing — Agent Rules

Guidance for AI coding agents (Claude, Cursor, Copilot) and human contributors working in the
**rivercrossing** repository. This is the canonical agent guidance; per-language detail lives in the
`CODINGSTANDARDS-*.md` files at the repo root, and the product contract lives in `design/`.
`CLAUDE.md` is a symlink to this file.

**RiverCrossing** is a keyboard-first poker-run ride timing and scoring desktop application for
**Windows and macOS**, written in **Python 3.14** with **wxPython**. The operator types a rider's plate
number + Enter at each lap crossing; the app records the lap, deals a card from a seeded virtual
multi-deck shoe, and at the finish scores every entry's best 5-card poker hand (jokers wild), applies
tie-breaks, and publishes results as a self-contained HTML page, PDF report, podium poster and CSV. It
survives crashes, accidental stops and restarts without losing a keystroke.

---

## `design/` is the contract

`design/` is the complete build contract — 49 numbered requirements, a §1–§15b engineering spec, 23
frozen window designs, the repo layout, a nine-EPIC plan and ~60 agent-ready task briefs. A developer
or agent who was not part of the design conversation should be able to build v1.0 from it alone.

| Path | Role |
|---|---|
| `design/docs-md/requirements.md` | Numbered **R-ids** — the acceptance authority |
| `design/docs-md/spec.md` | §1–§15b engineering spec (§14 CI stages, §15 menu map, §15b frozen name registry) |
| `design/docs-md/xrc-windows.md` | All 23 windows with frozen control names — **implementation truth for UI** |
| `design/docs-md/module-skeletons.md` | Repo layout, module public APIs, build order |
| `design/docs-md/project-plan.md` · `task-briefs.md` | EPIC plan and per-task briefs (the named tests **are** the spec) |
| `design/templates/` | Production Jinja2 templates — **ship verbatim** |
| `design/exports/*.html` | The two golden results pages; their `race-data` JSON blocks are export test fixtures |
| `design/docs-md/ui-designs-retired.md` | **Retired.** Flow/history reference only — never implement its visuals or control names |

`design/docs-md/` is canonical for agents. `design/docs-html/` is a browsable mirror that can go stale —
prefer the markdown.

---

## Answering & research discipline

- **Never invent** APIs, flags, file paths, library functions, control names or behaviour. Use only what
  is in the code, `design/`, the standards, or well-documented standard behaviour.
- When a request is uncertain, underspecified or ambiguous, **ask** — with researched options and a
  recommendation — rather than guessing.
- **If a design document is silent or two documents contradict each other, stop and ask.** Do not pick a
  reading and proceed. Where a conflict has already been resolved, the resolution and its evidence are
  recorded in the plan and written back into `design/`.

## Plans

Non-trivial work starts with a plan. State a confidence level backed by evidence; aim for ≥ 90% before
implementing. Single-prompt "vibe coding" of substantial changes is not acceptable.

---

## Coding standards — read pattern (MANDATORY)

Before writing or editing code, read the relevant standard(s) at the repo root and apply them:

```
IF editing .py                         → read ./CODINGSTANDARDS-PYTHON.md
IF editing .sh                         → read ./CODINGSTANDARDS-SHELL.md
IF writing/editing any code            → also read ./CODINGSTANDARDS-SIMPLECODE.md
IF designing/editing user-facing UI    → read ./CODINGSTANDARDS-UX.md
                                         and ./CODINGSTANDARDS-UX-DESKTOP.md
APPLY the standard. If code would violate it, STOP and ASK, naming the rule.
```

Source files carry a single SPDX line and no other licence header:

```python
# SPDX-License-Identifier: GPL-3.0-only
```

---

## TDD & testing

- **Test-first, always.** Production code is written by the **`tdd-python-writer`** agent using strict
  Red-Green-Refactor: write the named failing tests, watch them fail, make them pass, refactor. A change
  whose first commit is not tests is rejected (R-70, `project-plan.md` §2).
- **Coverage ≥ 90% line AND branch** on core modules (`cards hands standings ride store csvio htmlexport
  pdfexport`) — R-71, enforced by `--cov-branch --cov-fail-under=90`.
- **Functional tests are not optional.** The product *is* a UI; the functional suite driving real wx
  windows is the only thing that proves it runs. Never skip it to get green.
- Never write `assert True`, placeholder tests, or skips used as padding.
- Shell scripts are ShellCheck-clean; never `set -e` — check return codes explicitly.

### wx testing gotchas (measured, not theoretical)

- A wxWidgets **C++ assertion aborts the interpreter**. Constructing a control with invalid parameters
  (e.g. `wx.adv.HyperlinkCtrl` with neither label nor url) kills the process and takes every later
  result with it. Isolate risky window construction, and always pass valid parameters.
- Destroy windows explicitly. `Destroy()` is deferred, so a stale window can still answer
  `FindWindowByName` in the same process and silently contaminate the next assertion.
- Use event-driven waits, never bare `sleep`.
- On macOS, run the functional suite in the Tart VM: `scripts/run_functional_tests_vm.sh`. The
  suite opens 23 real windows and takes over the host desktop, so a bare `nox -s functional`
  refuses on a Mac unless `RIVERCROSSING_HOST_FUNCTIONAL=1` is set (CI is exempt). Setup and exit
  codes: CONTRIBUTING.md.

---

## Architecture rules

- **XRC-first.** Every window, dialog, menubar and panel is authored in sizer-based XRC and loaded from
  it. No absolute positioning, no restyling of standard controls, no custom-drawn chrome (R-05).
- **Frozen names.** Snake_case control names come from `spec.md` §15b and `xrc-windows.md`. Tests find
  widgets by them via `FindWindowByName`, so a rename is a breaking change. `ui/ids.py` is **generated**
  from the `.xrc` files — never hand-edited — and drift fails CI.
- **Two XRC classes cannot be authored in XRC** (measured):
  - `wxInfoBar` — XRC yields a generic `Control`, not a `wx.InfoBar`, and drops the name. Build InfoBars
    code-side and apply the frozen name with `SetName()`.
  - `wxDataViewListCtrl` — its XRC handler hard-forces the control name to `dataviewCtrl`. Use
    **`wxDataViewCtrl`** instead (name honoured) with a code-side `DataViewIndexListModel` subclass.
    Per-row attributes (e.g. bold short-lap rows) require overriding `GetAttrByRow`; there is no setter.
- **MVP, passive view.** Views are dumb: they render view-models and forward events. Presenters are pure
  Python holding UI logic, unit-tested headless with fake views. Business logic lives below both.
- **`wx` imports only under `rivercrossing.ui`** — enforced by an import-linter contract (R-71).
- **`rivercrossing.demo`** is the removable hard-coding seam: importable only from the app bootstrap and
  tests, enforced by lint. It is deleted from the app path in EPIC 5.
- Keep functions short and single-purpose in every language.

## Comments & docstrings

- Comment only genuinely non-obvious code — unusual logic, trade-offs, domain constraints, measured
  platform quirks. Don't narrate self-explanatory code.
- Docstrings are required on public modules, functions, classes and methods (PEP 257, imperative mood).
  Match the density and tone of the file you are editing.

---

## Build & test

Python tooling only — no Makefiles. `pyproject.toml` is the whole config; `noxfile.py` is the task
runner, and `scripts/*.sh` are one-line wrappers around it.

```bash
uv venv .venv && uv pip install -e '.[dev]'   # or: python -m venv .venv && pip install -e '.[dev]'

nox -s lint typecheck importlint ids_drift    # CI stage 1 — static
nox -s unit                                   # CI stage 2 — unit + coverage gate
nox -s functional                              # CI stage 3 — real wx windows
nox -s bundle smoke                            # CI stage 5 — build, then smoke the binary
```

**Both platforms gate.** `windows-latest` and `macos-latest` run the same blocking stages (R-75 /
spec §14). The EPIC 1 deviation that made macOS the only gate was reversed in Phase 10, after every
known Windows failure was root-caused and fixed and Windows testers became available.

---

## Source control

- **Never commit to `master`** — use `topic/*` branches.
- **No new branches or PRs without explicit approval** if a working branch already exists — ask first.
- Conventional Commits, scope = module. Test-first shows in the history: `test(hands): E2.1.1 red`,
  then `feat(hands): E2.1.1 green`.
- PR title `E2.1.1 — eval5 rank table`; body cites the R-ids and window ids it satisfies.
- **No AI advertising.** Never add "Generated with Claude", AI co-author trailers, or similar marketing
  text to commits, code, comments, PR bodies or documentation.

## Internal tools

Prefer the harness's dedicated file/search tools (read, edit, write, glob, content-search) and available
MCP tools over ad-hoc `cat`/`sed`/`grep`/`find` shell scripts for file operations and searches.
