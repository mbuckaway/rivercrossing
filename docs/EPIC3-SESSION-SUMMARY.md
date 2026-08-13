# EPIC 3 — Session Summary and Hand-off

**Written:** 2026-08-13 · **Branch:** `topic/epic-3-roster-csv` · **Head:** `690f88a` · **PR:** #8 (open, mergeable, zero review threads)
**Purpose:** development moves to another machine. This file records what is done, what is left,
and everything the next machine needs to resume. The full implementation plan (including the
PR #8 CI-fix plan) is appended verbatim at the end, so this file is self-contained.

---

## Status at a glance

| Item | State |
|---|---|
| EPIC 3 implementation (all six phases) | **DONE** |
| Design write-backs into `design/` | **DONE** |
| Version bump to 0.3.0 | **DONE** (`src/rivercrossing/__init__.py`) |
| PR #8 opened against master | **DONE** |
| CI stage 1 (static), stage 2 (unit), stage 5 (builds) | **GREEN on both OSes** |
| CI stage 3 (functional) | **RED — one known leak + one known lookup fault, both now diagnosed (see "What is left")** |
| Merge PR #8 | **LEFT** (blocked on stage 3) |
| Tag v0.3.0 | **LEFT — only after merge AND an explicit user go-ahead** (the tag pipeline publishes both installers) |
| EPIC 4 (live ride, in-memory) | **LEFT** — next epic; entry gate is E2 + E3 exits |

Everything is committed and pushed. The working tree is clean. Nothing lives only on this machine
except the environment (see "Machine move notes").

---

## What is done

### EPIC 3 — Roster & CSV (+ ride setup dialog)

All six plan phases shipped on `topic/epic-3-roster-csv` (44 task commits plus the CI-fix
commits below), strict red/green TDD throughout:

- **`src/rivercrossing/roster.py`** (new, 100% line+branch): `Entry`/`Rider`/`Roster` models,
  enums, plate rules (single namespace, pooled team adopts lowest rider plate, next-free =
  highest numeric + 1), team-size invariants, the R-15/R-17 lock matrix, `validate_for_start`,
  and an in-memory append-only audit-event log (EPIC 5's store will persist it).
- **`src/rivercrossing/csvio.py`** (new, 100%): `preview` (never writes), `commit` (atomic,
  refuses on conflicts), `export`; relay and pooled CSV forms; the clean-180 fixture plus
  malformed fixtures; Hypothesis export→re-import round-trip property.
- **`ride.py`**: frozen `RideConfig` (decks default 8, tiebreak order) beside `RideStatus`.
- **Editor + dialogs live**: `RidersPresenter`, `SetupPresenter` (headless, fake-view tested),
  `rider_editor_dlg` / `csv_preview_dlg` / `ride_setup_dlg` wired; code-side InfoBars
  `roster_infobar` / `csv_infobar` (XRC cannot author wxInfoBar); import/export flows behind
  injectable picker seams; solo/mixed presentation from `entry_mode`.
- **Tests**: 1455 unit/property/simulation tests, 98.31% branch coverage (gate ≥90);
  764 functional tests driving real wx windows, green in the local Tart VM.
- **Design write-backs**: module-skeletons S2/S3/S4/S5, spec §2/§4/§7/§11/§15b, xrc-windows,
  requirements, project-plan, task-briefs (E3.5 briefs + E3→E4 hand-off row), EPIC-3 prompt.
  All eleven user decisions from planning are recorded in the plan appendix below.
- **PR #8** opened citing R-11/12/15/16(partial)/17/20/21/22 and window ids rider_editor_dlg ·
  csv_preview_dlg · ride_setup_dlg.

### PR #8 CI-fix campaign (/mr-fix) — three iterations pushed so far

PR #8 had zero review comments and no conflicts; the whole fix scope is CI. The approved fix
plan (appendix, "MR Fix Plan" section) allows up to five fix iterations. Three are done:

**Iteration 1 — commits `de87fef`, `c0c9c1c`, `865e9e1`, `20202a1`, `20eacd2`.**
- Windows unit: `test_preview_missing_file_raises_file_not_found_error` now asserts
  `excinfo.value.filename` instead of regex-matching the repr-mangled message. **Fixed — Windows
  unit has been green since.**
- Windows functional: `harness.select_row` now posts the `DataViewEvent` explicitly after
  `Select()` (MSW's native control fires nothing on a programmatic select; macOS generic does).
  **Fixed — the four constant failures are gone.**
- `harness.close_window` gained a bounded settle loop; `--reruns` bumped 1→2 at all three sites
  (noxfile, ci.yml, VM script) with the `test_vm_scripts.py` pin updated red-first.

**Iteration 2 — commits `1e91cd0`, `b342bce`.**
- Added `harness.flush_deferred_deletions()` (activated event loop + `YieldFor` +
  `EventLoopBase.ProcessIdle()`; without a MainLoop, wx frees a `Destroy()`d window only in idle
  processing). Wired it into `pump()`.
- CI verdict: macOS unchanged, and per-pump flushing was a mistake on MSW — one Windows job ran
  the full suite in **5h59m28s** (744 passed; ~240× slowdown from `ProcessIdle`/UpdateUI chatter
  on every pump) and was killed at the 6-hour cap. Key learning preserved in harness docstrings.

**Iteration 3 — commits `ec79a7e`, `690f88a` (current head).**
- `pump()` reverted to a bare `wx.SafeYield()`; the flush now runs only in `close_window`'s
  bounded loop and `_fire_menu_event`'s settle; `_FLUSH_IDLE_ATTEMPTS` cut 25→5.
- Added failure diagnostics (no assertion weakened): the reap pin's message now names the
  residual window's owner, deletion state, and handle match; `ui/views/_support.py`'s
  `LookupError` now inventories the searched window's first-level children.
- Locally: full gauntlet green; three back-to-back VM runs, zero failures, normal (~28 s) pace.
- CI verdict (2026-08-13, runs 31713657101 / 31713662767): stages 1/2/5 green on both OSes.
  Stage 3: Windows passed one run of two; macOS failed both — **and the diagnostics identified
  both remaining faults** (next section).

---

## What is left

### 1. CI fix iteration 4 (of the approved 5) — two diagnosed faults

**Fault A — a leaked `rider_editor_dlg` (macOS, both runs, same message):**

```
AssertionError: residual name='plate_input' owner='rider_editor_dlg'
is_being_deleted=False handle=… is_this_dialogs_own_control=False active_loop=None
```

The reap pin (`test_screen_smoke.py::test_close_window_reaps_the_dialog_so_a_shared_name_no_
longer_resolves`) fails NOT because `close_window` is broken: the residual is a **fully alive
`rider_editor_dlg`** (`is_being_deleted=False` — `Destroy()` was never called on it) that an
earlier test in the same xdist worker opened and never closed. It only shows on hosted runners
because their 3 workers pack files differently than the local VM's 4.

Next actions: find which test opens `rider_editor_dlg` without closing it. Candidates: tests
firing the `mi_riders` route on `test_app_bootstrap.py`'s module-scoped frames (does the route's
dialog get closed, and is it parented to the frame?), and failure paths in
`test_rider_editor.py` / `test_csv_preview.py` that skip teardown. Fix the leak (close in
`finally` / fixture teardown), and consider a session-end sweep asserting no top-level windows
survive each test file.

**Fault B — `results_frame` missing its checkboxes (Windows, one run of two):**

```
LookupError: results_frame has no control named 'show_times_chk' (first-level children: 8 --
['tiebreak_list', 'reopen_btn', 'standings_list', '-1', 'export_html_btn', 'export_pdf_btn',
'poster_btn', 'export_csv_btn'])
```

`test_lists_demo.py::test_results_window_show_standings_repaints_the_list_after_associating_
its_model` fails all three attempts when it fails, and it failed even in iteration 2's 6-hour
run — **not a timing race**. The frame the view searched genuinely lacks `show_times_chk` and
`time_board_chk` and carries one unnamed (`'-1'`) child. Two hypotheses to separate: (a)
`harness.load_window` resolves the frame by name after loading and can grab a stale
same-name frame leaked by an earlier test (same leak class as Fault A); (b) the process-global
`wx.xrc.XmlResource` degrades in a loaded worker and builds an incomplete frame. First step:
compare this inventory against a healthy `results_frame`'s first-level children from
`results.xrc`; then make `load_window` return exactly the window `LoadFrame`/`LoadDialog`
constructed rather than anything name-resolved.

**Also carried forward:** the first fix agent's recommendation — if leaks are fixed and churn
still trips workers, split the heaviest window-churn test files (`test_app_bootstrap.py`,
`test_lists_demo.py`) so `--dist loadfile` spreads their load across workers.

### 2. Merge PR #8 (after stage 3 is green on both OSes)

### 3. Tag v0.3.0 — **only after merge and an explicit user go-ahead.** Never tag autonomously; the tag pipeline publishes both installers.

### 4. EPIC 4 — live ride, in-memory

Start from `design/epic-prompts/` and the E3→E4 hand-off row in
`design/docs-md/task-briefs.md` (stub-and-hand-off table: Roster + lock matrix +
`validate_for_start` + `RideConfig` in `ride.py`). Same methodology: plan → /plan-review →
tdd-python-writer red/green pairs, one epic branch, one PR.

---

## Machine move notes

**Travels with the repo (this branch):** all source, tests, design contract (`design/`), CI
workflows, coding standards, CONTRIBUTING.md, this summary.

**Machine-local — must be recreated on the new machine:**
- **Python env:** `uv venv .venv && uv pip install -e '.[dev]'` (Python 3.14, wxPython 4.3.1).
- **Tart VM for macOS functional runs:** setup steps and exit codes are in CONTRIBUTING.md.
  Run the suite with `scripts/run_functional_tests_vm.sh`. Never set
  `RIVERCROSSING_HOST_FUNCTIONAL=1` on a desktop Mac — the suite opens 23 real windows.
- **Plan file:** lived at `~/.claude/plans/we-completed-epic-2-linear-teacup.md` on the old
  machine; its full current content is the appendix below, so nothing is lost if it is not
  copied.
- **Agent memory:** `~/.claude/projects/-Users-markbuckaway-src-rivercrossing/memory/` on the
  old machine (epic status + methodology notes). The facts that matter are restated here.

**Verification commands (identical on both platforms):**

```bash
nox -s lint typecheck importlint ids_drift   # CI stage 1
nox -s unit                                  # CI stage 2 (coverage gate ≥90)
scripts/run_functional_tests_vm.sh           # CI stage 3 equivalent, macOS host
gh pr checks 8 --repo mbuckaway/rivercrossing
```

---

# Appendix — current plan (verbatim from the session plan file)

# EPIC 3 — Roster & CSV (+ ride setup dialog)

**Status:** READY FOR APPROVAL — reviewed by /plan-review · **Confidence: 93%** (evidence at end)
**Repo:** /Users/markbuckaway/src/rivercrossing · master @ a66248f (v0.2.0 tagged, EPIC 2 merged, clean)

## Context

EPIC 2 (deal & score engine + Tart VM functional-test lane) is merged and tagged v0.2.0. Next is
**EPIC 3: Roster & CSV** — real entries/riders/teams created in the editor or imported from CSV with
a preview-then-commit flow that never writes on preview. Driven by
`design/epic-prompts/EPIC-3-roster-csv.md` and the E3 briefs (`design/docs-md/task-briefs.md:92-106`
— the named tests ARE the spec). EPIC 1 already shipped both dialog shells: `riders.xrc` holds
`rider_editor_dlg` + `csv_preview_dlg` with every frozen name, `ui/presenters/riders.py` has the
`RidersView` protocol + a no-op `RidersPresenter` whose docstring awaits exactly this work, and
`ui/views/rider_editor.py` renders demo rows. EPIC 3 makes it all real.

**User decisions (2026-08-08)** — each resolves a design-doc silence found in exploration:
1. **Models home:** new **`src/rivercrossing/roster.py`** (no roster module existed anywhere in the
   contract; briefs name `tests/unit/test_roster.py`, project-plan L138 demands "store-less models").
   Written back same-day into module-skeletons S2/S3/S4 + spec §11 + the §3 gate list (skeletons L266 rule).
2. **Branch/PR:** single epic branch **`topic/epic-3-roster-csv`**, red/green commit pairs per task,
   push at each completed phase, one PR at the end (same approved deviation as EPIC 2).
3. **Audit pre-store:** roster mutations record frozen audit-event dataclasses in an append-only
   in-memory log on the model (mirrors skeletons' RideEngine "every mutation returns an Event the
   store persists"); EPIC 5's store persists them.
4. **clean-180 fixture:** relay form (`plate,entry_name,type,rider_1…rider_4,notes`), solo + teamN
   mix totalling **180 riders** (e.g. 120 solo + 15 team4 = 135 entries).
5. **Duplicate-plate validation:** code-side **wx.InfoBar named `roster_infobar`** via SetName (EPIC 1
   InfoBar pattern); name written back to §15b/xrc-windows.
6. **Pooled team plate:** one plate namespace per ride; a pooled team entry **adopts its
   lowest-numbered rider's plate** (matches the "77 Trail Blazers"/riders 77-78-79 mock); rule
   written back to spec §2/§7.
7. **Next-free plate:** highest numeric plate in use **+ 1** (non-numeric ignored; empty roster → 1).
8. **ride_setup_dlg pulled INTO EPIC 3** (user-added scope — no brief exists; new E3.5 briefs get
   written back), and **decks_spin default = 8** (spec §4 kept; xrc-windows canvas note amended).

**Binding ground rules** (prompt + CLAUDE.md, unchanged from EPIC 2): all production code by the
**`tdd-python-writer`** agent, strict red→green per task — `test(scope): E3.x.y red` then
`feat(scope): E3.x.y green` (convention verified in the v0.1.2..v0.2.0 history); coverage ≥90% line
AND branch (automatic: `addopts` carries `--cov-branch --cov-fail-under=90`, and `roster.py`/
`csvio.py`/presenters are all measured — only `ui/views/*`, `ui/app.py`, `__main__.py`, `ui/ids.py`
are omitted); mypy --strict; ruff (CPY001 enforces the SPDX line); import-linter; frozen XRC names +
`ids_drift`; UX standards (CODINGSTANDARDS-UX + -UX-DESKTOP) read before UI work; no AI-advertising
trailers. macOS functional runs go through **`scripts/run_functional_tests_vm.sh`** (Tart VM lane,
measured 43 s/cycle; bare `nox -s functional` refuses on a Mac without
`RIVERCROSSING_HOST_FUNCTIONAL=1`; CI exempt).

---

## Phase 1 — E3.1 Models (`src/rivercrossing/roster.py`)

- **1.1 E3.1.1 — models + invariants** (`tdd-python-writer`: write `tests/unit/test_roster.py` +
  `tests/property/test_roster_properties.py` red first, then implement green):
  - 1.1.1 Enums with spec §2's stored values: `EntryMode` (solo|mixed, default solo), `PlateModel`
    (rider_pooled|team_relay, default rider_pooled). `RideStatus` already exists —
    ride.py:12 (pre-created in E1), import it, don't redefine.
  - 1.1.2 `Entry`/`Rider` dataclasses per the spec §2 columns E3 needs (plate, display_name, type,
    team_size, status active|dnf, notes · rider: name, plate?, sort_order); NO age/category fields
    (excluded by decision, requirements.md:101).
  - 1.1.3 `Roster` aggregate holding entries+riders for one ride, constructed with
    (entry_mode, max_team_size, plate_model): plate unique per ride in **one namespace** (entry +
    pooled rider plates; pooled team entry adopts lowest rider plate — decision 6); team size
    2–max(≤10); solo default; plate shapes (relay: entry plate only; pooled: every rider carries
    one); negatives raise: 11-rider team, duplicate plate, team in solo-only ride.
  - 1.1.4 Every mutation appends a frozen audit-event record (action + payload) to an in-memory
    append-only log (decision 3).
  - 1.1.5 `next_free_plate()` = highest numeric + 1 (decision 7).
  - 1.1.6 Hypothesis property: any sequence of valid mutations preserves plate uniqueness and
    team-size bounds. Done when property + negative suite green.
- **1.2 E3.1.2 — lock matrix** (`tdd-python-writer`, red→green). Pure functions over
  (RideStatus, PlateModel, has_data): DRAFT free
  edit incl. delete; post-start relay locked; post-start pooled allows audited rider moves
  (`move_rider` travels plate+data, audit-logged — R-17); entry with recorded data undeletable
  (DNF/void only — R-15). `has_data` is a model predicate E4 will later feed from crossings.
- **1.3 Config:** add `rivercrossing.roster` to the import-linter no-wx contract
  (pyproject.toml:237-244 — the comment there documents this per-task
  growth). Coverage needs no change.

Push at phase end.

## Phase 2 — E3.2 Editor live

- **2.1 E3.2.1 + E3.2.2 — presenter, headless** (`tdd-python-writer`, red→green):
  - 2.1.1 `tests/unit/presenters/test_riders.py` red, with `FakeRidersView` (pattern:
    test_protocols.py:115-131) driving a real
    `Roster`: add with next-free prefill; save; delete blocked once entry has data (DNF/void
    path); `team_choice` population "— solo —" / teams / "New team…" sentinel; duplicate plate →
    `show_validation(message)`; solo-only ride (entry_mode) hides the Team column and team UI.
  - 2.1.2 `show_validation` is a new `RidersView` member — protocol additions are allowed; the E1
    view-model `CsvPreview(summary, conflicts)` stays as-is, the presenter formats into it.
  - 2.1.3 Implement `RidersPresenter` green.
- **2.2 Functional tests red** (`tdd-python-writer`): `tests/functional/test_rider_editor.py`
  (exemplar shape: test_selftest_dialog.py) — all
  editor flows per brief; duplicate plate shows the InfoBar, never crashes. Harness gains a
  `select_choice` helper (none exists — measured; mirrors `click`/`type_text`: SetSelection +
  posted `EVT_CHOICE`, then `pump()`).
- **2.3 View wiring green.** rider_editor.py binds
  `plate_input · name_input · team_choice · add_btn · save_btn · delete_btn` (all in riders.xrc +
  ids.py already) → presenter; code-side `wx.InfoBar` named `roster_infobar` (decision 5; EPIC 1
  SetName pattern).
- **2.4 Bootstrap.** `app.py` builds the in-memory `Roster` (seeded from demo rows — the demo
  import stays legal only in the bootstrap, import-linter contract 2) and hands it to
  `RidersPresenter`; the editor screens read only roster models, never `DemoDataSource.riders()` —
  this is how the exit criterion "demo roster unused on these screens" is satisfied while the demo
  seam survives until E5. The exactly-once demo-import pins in
  test_app_wiring.py:128-139 (verified: the two pins
  span 128-132 and 135-139) are updated red-first if
  the wiring shifts.
- **2.5 Keep-green pins:** test_dialog_behavior (save_btn default, plate_input focus),
  test_lists_demo, test_screen_smoke.

Push at phase end (functional via the VM lane locally).

## Phase 3 — E3.3 csvio (`src/rivercrossing/csvio.py`)

Frozen API (module-skeletons:190-194): `preview(path, ride) -> ImportPreview` ·
`commit(preview) -> ImportReport` · `export(ride, path) -> None`. Pre-E4 the `ride` argument is the
Roster aggregate + its config; the precise type is documented and written back (stop-and-ask if a
real contradiction emerges — E2's shoe-API precedent). Stdlib `csv` (no new deps).

- **3.1 E3.3.1 — preview** (`tdd-python-writer`, red→green). `tests/unit/test_csvio.py` red; fixture files in
  `tests/unit/fixtures/csv/`: **clean-180** (decision 4), dup-plate, missing-name, team-over-max,
  plus relay- and pooled-form samples. Form selection keys on plate_model (pooled = one row per
  rider `plate,name,team_name,notes`, blank = solo — spec §7:171). Preview counts + conflicts
  exact; **filesystem untouched** (tmpdir assert). Rows exceeding ride max → conflict report.
- **3.2 E3.3.2 — commit + re-import** (`tdd-python-writer`, red→green). Commit atomic
  (validate-then-apply, no partial mutation);
  match on plate = update in place, insert new plates; DRAFT re-import reshapes teams freely —
  moved rider → membership updated + audit events recorded; RUNNING: only new plates + name-spelling
  fixes, structural changes → conflicts, pooled team_name change = audited membership move
  (spec §7:177/171); negative: commit with conflicts refuses.
- **3.3 E3.3.3 — export round-trip** (`tdd-python-writer`, red→green).
  `tests/property/test_csvio_properties.py`: Hypothesis —
  random roster → export → preview shows 0 conflicts → commit → value-identical models incl. teams;
  both forms; finished-ride extra columns (`laps, cards, best_hand, total_time`) deferred to E6
  (no standings exist — noted in write-back).
- **3.4 Config:** import-linter contract 1 += `rivercrossing.csvio`.

Push at phase end.

## Phase 4 — E3.4 Dialogs live

- **4.1 E3.4.1 — csv_preview_dlg wired** (`tdd-python-writer`, red→green per subtask):
  - 4.1.1 Preview presenter, headless: formats `summary_lbl` ("riders.csv → 178 riders · 12 teams
    · 3 conflicts" shape from the mock), fills `conflicts_list` (Row | Problem), gates stock
    **wxID_OK "Import" disabled while conflicts > 0**.
  - 4.1.2 Import flow: `mi_import_csv`
    (commands.py:171 routes straight to the dialog today)
    becomes OS-native FileDialog → preview → commit on OK, with the picker behind an injectable
    seam so the harness supplies paths.
  - 4.1.3 Export flow: `mi_export_csv` (COMMAND `"export_riders_csv"`,
    commands.py:177-183) wires to a native
    save-dialog seam → `csvio.export`.
  - 4.1.4 Functional `tests/functional/test_csv_preview.py`: conflicts>0 → OK disabled; clean
    file → import applies to the roster.
- **4.2 E3.4.2 — solo variant** (`tdd-python-writer`, red→green). Editor solo/mixed presentation
  switch driven by the ride's `entry_mode` (replacing row-inference `is_solo_only(rows)`,
  rider_editor.py:73); harness asserts both states.

Push at phase end; green on both CI OSes.

## Phase 5 — E3.5 Ride setup dialog (user-added scope, decision 8)

No brief exists — new E3.5.1/E3.5.2 briefs are authored and written back into task-briefs.md as
part of close-out. Verified starting state (/plan-review, all confirmed against source):
`setup.xrc` holds all 23 frozen controls (setup.xrc:25-345); XRC already declares the defaults —
`solo_radio`/`pooled_radio`/`jokers_2_radio` carry `<value>1`, `team_size_spin` is min 2/max 10/
value 4 — and `decks_spin` declares **no value** (setup.xrc:274-276; the presenter supplies it,
exactly as the docs state). `SetupView` already exists with two members,
`set_team_fields_enabled` / `set_entry_locked`
(presenters/setup.py:17-21); `SetupPresenter`
is a constructor-only stub. The `mi_ride_setup` route exists
(commands.py:272-280, enabled while a ride is open);
today the dialog opens undecorated via the generic path (app.py:218-221). A `RIDE_SETUP_DLG`
WindowSpec already drives it in screen smoke (pages.py:103-134).

- **5.1 E3.5.1 — SetupPresenter live, headless** (`tdd-python-writer`, red→green). Produces a
  frozen `RideConfig` (pre-created in `ride.py` next to `RideStatus` — E1 precedent; the name is
  already reserved by module-skeletons:158 `RideEngine.__init__(config: RideConfig, …)`; fields
  from spec §2's ride row that E3/E4 need). Presenter supplies **`decks_spin` default 8**
  (decision 8); `mixed_radio` enables `team_size_spin` + `pooled_radio`/`relay_radio` via the
  existing `set_team_fields_enabled`; `cap_chk` gates `cap_spin`; `tiebreak_list` order captured
  into the config. Entry/plate-model group lock by state (post-start relay locked; pooled stays
  editable — R-17) drives the existing `set_entry_locked`, reusing the Phase 1 lock matrix.
  Protocol additions beyond the two shipped members follow the same rules as 2.1.2.
- **5.2 E3.5.2 — view wiring + functional** (`tdd-python-writer`:
  `tests/functional/test_ride_setup.py` red first, wiring green). New view class binds the setup
  controls → presenter and is attached in `app.py:_decorate`; harness drives
  radios/spins/checkbox, asserts the XRC-declared defaults plus the presenter-supplied decks=8,
  enablement rules, and that OK yields the validated config. Fallback if a native date/time
  picker (wxDatePickerCtrl / wxTimePickerCtrl — verified classes, setup.xrc:72/:82) resists event
  injection in the VM: presenter-level coverage + construction/default assertions functionally
  (documented, not skipped).

Push at phase end.

## Phase 6 — Epic close-out

- **6.1 Design write-backs** (one `docs(design)` commit — E2 precedent `8dbdf4e`):
  - 6.1.1 module-skeletons S2 tree + S3 build order + S4 public surface + S5 test tree gain
    `roster.py` (resolving the undefined "store models" term for E3).
  - 6.1.2 spec §11 roster row (same-day rule, skeletons L266) + project-plan §3 gate list gains
    roster.
  - 6.1.3 spec §2/§7: pooled team-plate rule (adopt lowest rider plate) + next-free-plate rule.
  - 6.1.4 spec §4 / xrc-windows / requirements.md:105: decks default resolved to 8.
  - 6.1.5 spec §15b + xrc-windows: register `roster_infobar`.
  - 6.1.6 task-briefs: add the E3.5 briefs (user-approved scope) and an E3 row in the
    stub-&-hand-off table (the roster surface E4 consumes — closing the missing-contract gap).
  - 6.1.7 project-plan E3 row updated with shipped state (E2-style paragraph).
  - 6.1.8 EPIC-3 prompt file amended (single-PR deviation, setup-scope addition).
- **6.2 Guides:** CHANGELOG.md; CONTRIBUTING.md test-tree note (csv fixtures dir).
- **6.3 Release:** bump `__version__` to 0.3.0 (`chore: bump version to 0.3.0` — project-plan ships
  E3 as v0.3), open the single PR `EPIC 3 — Roster & CSV` citing R-11/12/15/16(partial)/17/20/21/22
  and window ids rider_editor_dlg · csv_preview_dlg · ride_setup_dlg. **Tag v0.3.0 only after merge
  + explicit user go-ahead** (tag pipeline publishes both installers).

---

## Files touched (representative)

| Area | Files |
|---|---|
| Models | `src/rivercrossing/roster.py` (new) · `src/rivercrossing/ride.py` (RideConfig added) |
| CSV | `src/rivercrossing/csvio.py` (new) |
| UI | `ui/presenters/riders.py` · `ui/presenters/setup.py` · `ui/views/rider_editor.py` · `ui/commands.py` · `ui/app.py` (bootstrap roster) |
| Tests | `tests/unit/test_roster.py` · `tests/unit/test_csvio.py` · `tests/unit/fixtures/csv/*` · `tests/property/test_roster_properties.py` · `tests/property/test_csvio_properties.py` · `tests/unit/presenters/test_riders.py` · `tests/functional/test_rider_editor.py` · `tests/functional/test_csv_preview.py` · `tests/functional/test_ride_setup.py` · `tests/functional/harness.py` (select_choice) |
| Config | `pyproject.toml` (import-linter += roster, csvio) |
| Design | module-skeletons S2/S3/S4/S5 · spec §2/§4/§7/§11/§15b · project-plan §3 + E3 row · xrc-windows · task-briefs (E3.5, hand-off row) · EPIC-3 prompt |

## Verification

- Per task: named tests red → green (tdd-python-writer); per phase:
  `nox -s lint typecheck importlint ids_drift unit` green, coverage ≥90% line+branch on
  roster/csvio/presenters (enforced by existing addopts).
- Functional: `scripts/run_functional_tests_vm.sh` green locally (macOS Tart VM); CI stage 3 green
  on macos-latest + windows-latest. No CI file changes needed (stage 2 already runs
  unit+property+simulations; stage 3 runs tests/functional).
- E3 exit criteria (prompt + project-plan L138): 180-rider EPIC CSV imports clean · each malformed
  fixture reports exactly its conflicts and writes nothing · conflicts block commit ·
  export → re-import value-identical incl. teams · teams reshape until start · editor enforces
  every lock in the matrix · editor fully live on real store-less models · demo roster unused on
  these screens.

## Rollback

- All work lands on `topic/epic-3-roster-csv`; master is never touched. Abandoning the epic =
  close the PR, delete the branch.
- Every task is a red/green commit pair — revertible at task granularity with `git revert`.
- The design write-backs are one `docs(design)` commit (6.1), independently revertible.
- No persistence exists before E5, so reverting code cannot strand user data.
- Nothing publishes until the tag: v0.3.0 is applied only after merge + explicit user go-ahead.

## Risks & open items

- **Harness has no wx.Choice driver** — new `select_choice` helper needed; same injection pattern
  as the proven `click`/`type_text`; small, measured.
- **InfoBar inside a dialog** — EPIC 1's InfoBars live in main_frame; same code-side SetName
  pattern, XRC-cannot-author gotcha already measured. Fallback exists (validation text via a
  static label would need a name decision — stop-and-ask if InfoBar misbehaves in a dialog).
- **`csvio.preview(path, ride)`'s `ride` param** pre-E4 is the roster aggregate; documented +
  written back; stop-and-ask on real contradiction (E2 shoe-API precedent).
- **Native date/time pickers under event injection in the VM** (Phase 5) — fallback documented in 5.2.
- **Export's finished-ride columns** need standings (E6) — deferred with a write-back note, not
  silently dropped.

## Confidence: 93% — evidence

- All 8 E3 briefs read verbatim (task-briefs.md:92-106); the EPIC 3 prompt, spec §1/§2/§3/§7/§11/
  §12/§15b, R-11/12/15/16/17/20/21/22 + R-70…76, and module-skeletons read in full with line
  citations; three parallel explorers cross-corroborated the source/test/design state.
- **Zero XRC authoring risk for E3.1–E3.4:** both dialogs + all frozen control names already exist
  (riders.xrc:31-222, ids.py constants verified, pages.py WindowSpecs:197-228, screenshots on
  disk); `RidersView` protocol + no-op presenter shipped in E1 explicitly awaiting this work
  (riders.py:58 docstring names E3.3).
- Every infra edit point located by line: import-linter contract pyproject.toml:237-244 (whose own
  comment plans the csvio addition), coverage omit-list (roster/csvio auto-gated at 90%),
  commands.py routes 171/177/287, app.py bootstrap 523 / _decorate 232.
- Methodology proven twice: the v0.1.2..v0.2.0 history shows the exact red/green commit convention;
  the VM lane is measured (43 s full cycle, 678 passed in-guest).
- All nine design silences resolved by user decision (2026-08-08), each with a named write-back —
  nothing in the plan rests on a guess.
- Post-plan adversarial verification (/plan-review, 2026-08-08): **31/31 file- and line-level
  claims CONFIRMED, zero refuted** by three independent read-only verifiers over disjoint file
  sets (UI source · config/tests · design docs) — including setup.xrc's full 23-control
  enumeration, the XRC-declared defaults, the shipped SetupView members, the mi_ride_setup route,
  and the RIDE_SETUP_DLG page object.
- The 7 points withheld: select_choice + dialog-InfoBar + native-picker injection are unproven in
  this specific harness (fallbacks documented), and the csvio `ride` param shape is an authored
  resolution rather than a doc-stated one.

## Review Findings (/plan-review, 2026-08-08)

**Edited inline**
- Missing item — execution steps lacked numbered subtasks (1.1.x, 4.1.x, 6.1.x) and named the TDD
  agent only in the ground rules. Refactored to phase → N.M task → N.M.K subtask bullets with
  `tdd-python-writer` named at every TDD step.
- Missing item — no Rollback/escape-hatch section. Added (branch isolation, red/green revert
  granularity, no persistence before E5, tag gated on explicit go-ahead).
- Hole — Phase 2 originally wired the view before its functional tests existed, contradicting the
  plan's own test-first mandate. Reordered: 2.1 presenter red→green · 2.2 functional tests red ·
  2.3 view wiring green · 2.4 bootstrap · 2.5 keep-green pins.
- Inconsistency — `tests/functional/test_csv_preview.py` and `test_ride_setup.py` appeared only in
  the files-touched table. Now named in steps 4.1.4 and 5.2.
- Ambiguity — exit criterion "demo roster unused on these screens" vs bootstrap seeding from demo
  rows. Clarified in 2.4: the editor screens read only roster models; the demo seam survives only
  in the bootstrap (import-linter contract 2) until E5 deletes it.
- Assumption — Phase 5 asserted "setup.xrc + all frozen names shipped in EPIC 1" without proof.
  Verified by full enumeration (setup.xrc:25-345, 23 controls) and enriched with the verified
  starting state: XRC-declared defaults, existing SetupView members (presenters/setup.py:17-21),
  the mi_ride_setup route (commands.py:272-280), the RIDE_SETUP_DLG page object (pages.py:103-134).
- Drift — test_app_wiring pin cite corrected from :128-135 to :128-139 (the two pins span 128-132
  and 135-139).
- Verification sweep — 31/31 file- and line-level claims across UI source, config/tests, and
  design docs CONFIRMED by three independent read-only verifiers; zero refuted, so no other
  content changes were required.

**Resolved via AskUserQuestion** (planning decisions this session, restated for traceability)
- Models home → `src/rivercrossing/roster.py` · branch/PR → single epic branch, one PR · audit
  sink → in-memory events · clean-180 → relay form, 180 riders · validation UI → code-side
  `roster_infobar` · pooled team plate → adopt lowest rider plate · next-free → highest + 1 ·
  setup dialog → pulled into E3, decks default 8.

**BLOCKING**
- None.

## Confidence (re-scored by review)

93% — every verifiable file/line claim confirmed (31/31, zero refuted); every design silence
carries an explicit user decision with a named write-back; the only unproven items are the three
harness mechanics under Risks, each with a documented fallback. The ≥90% bar is met with proof.

## MR Fix Plan — PR #8 CI failures (/mr-fix, 2026-08-10)

**Scope discovered:** PR #8 (github.com/mbuckaway/rivercrossing, branch topic/epic-3-roster-csv,
head 3b6db7d) has ZERO review comments (no reviewers ran, no threads) and NO merge conflicts
(MERGEABLE). The entire fix scope is three failing CI jobs, failing on every push since the
Phase 2/3 pushes. Failure logs were downloaded per job and root-caused; nothing here is a
reviewer hallucination — all three are measured from the logs.

### Real issues (from pipeline logs)

- **Fix 1 — Windows unit: `tests/unit/test_csvio.py:521`
  `test_preview_missing_file_raises_file_not_found_error`** (1 failed / 1449 passed).
  Evidence: log shows `Expected regex: 'C:\Users\…'` vs
  `Actual message: "[Errno 2] … 'C:\\Users\\…'"` — CPython's `OSError.__str__` embeds
  `filename` via `repr()`, so on Windows the message carries escaped backslashes and
  `re.escape(str(missing))` can never match; POSIX paths have no backslashes, so macOS passed.
  Verdict REAL_ISSUE (test bug). Fix (test-only): capture `excinfo` and assert
  `excinfo.value.filename == str(missing)` — exact and platform-independent.

- **Fix 2 — Windows functional: `harness.select_row` never notifies the presenter on MSW**
  (4 failed tests, constant across every run: save-renames, delete-removes,
  delete-only-entry-empties, stale-selection — exactly the tests that use `select_row`).
  Evidence: harness.py:277-281's own docstring says the measurement "DataViewCtrl.Select already
  fires EVT_DATAVIEW_SELECTION_CHANGED" was made on this (macOS generic) build; wx's documented
  convention is that programmatic changes emit no events, and MSW's native control follows it —
  so on Windows the presenter has no selected row and Save/Delete no-op. Verdict REAL_ISSUE
  (harness bug). Fix: after `Select(item)`, post
  `wx.dataview.DataViewEvent(wxEVT_DATAVIEW_SELECTION_CHANGED, control, item)` explicitly (the
  3-arg constructor was already probe-verified in E3.2); `on_row_selected` is idempotent so the
  macOS double-fire is harmless; rewrite the docstring to record the cross-platform finding.

- **Fix 3 — macOS functional: deferred-Destroy reap is nondeterministic under hosted-runner
  load** (varied failures across runs, all one family: screen_smoke's
  `test_close_window_reaps_…` ×3, `standings_list`/`export_btn` LookupErrors, one KeyError from
  an un-run monkeypatch — the `_support.py`-documented address-reuse churn, near-deterministic
  on 3-core hosted runners now the suite is 761 tests; the local Tart VM at 4 CPUs stays green).
  Verdict REAL_ISSUE (harness determinism). Fix: `harness.close_window` currently pumps exactly
  once after `Destroy()` (harness.py:335-357); make it reap deterministically — record the
  window's name before destroying, then a bounded loop (no sleeps): `wx.GetApp().ProcessIdle()`
  + `pump()` until `wx.Window.FindWindowByName(name)` no longer resolves or attempts exhaust
  (mirror `FIND_SETTLE_ATTEMPTS = 25`, _support.py:29). Never touch the window object itself
  after Destroy (segfault, measured in E1).

- **Fix 4 (optional, defense-in-depth) — bump functional retries `--reruns 1` → `--reruns 2`**
  at the three invocation sites (noxfile.py functional session, .github/workflows/ci.yml:131,
  scripts/run_functional_tests_vm.sh:208) plus the content pin at tests/unit/test_vm_scripts.py:86
  (pin updated red-first). Rationale: `_support.py` documents the residual risk as not fully
  fixable under sustained load; one extra retry keeps a single straggler from failing a 25-minute
  CI run. Masking-risk acknowledged — offered as a user choice, not auto-applied.

### Dismissed items

- None. (No review comments exist; no .md-file noise to filter.)

### User selections (2026-08-10)

All four fixes approved: 1 (Windows path assert), 2 (select_row MSW event), 3 (deterministic
reap), 4 (--reruns 1→2 at all three sites + red-first pin update).

### Execution (after approval)

- Phase 1 (conflicts): none — PR is MERGEABLE.
- Phase 2: apply the selected fixes via `tdd-python-writer` — Fixes 1-3 are red-on-CI already
  (the failing CI tests are the red evidence); local red where reproducible (Fix 1 is
  Windows-only — assert the new form still passes on macOS; Fix 3 has its own pin,
  `test_close_window_reaps_the_dialog_so_a_shared_name_no_longer_resolves`).
- Phase 3: full local gauntlet (`nox -s lint typecheck importlint ids_drift unit`) + the whole
  functional suite in the Tart VM.
- Phase 3.5 (reply/resolve threads): nothing to resolve — no threads exist.
- Phase 4: commit-confirmation gate → commit (`fix(tests): …` / `fix(ci): …` pairs), push.
- Phase 5: watch PR #8's CI (both OSes) up to 5 iterations; on any new failure, pull logs and
  fix in-flight. **Progress note (2026-08-13): iterations 1-3 pushed (heads `20eacd2`,
  `b342bce`, `690f88a`); both remaining stage-3 faults are diagnosed — see "What is left"
  above. Iterations 4-5 remain.**
