# EPIC 5 — Session Summary and Hand-off

**Written:** 2026-08-28 · **Branch:** `topic/epic-5-persistence` · **Version:** `0.5.0`
**Purpose:** EPIC 5 (Persistence & crash recovery) is implemented; this file records what shipped,
the decisions taken, and what the next machine needs to resume into EPIC 6.

---

## Status at a glance

| Item | State |
|---|---|
| E5.1.1 multi-ride SQLite schema + migrations | **DONE** — pushed (`097e24e`) |
| E5.1.2 event replay (`append`/`load_engine`/`apply` + equivalence property) | **DONE** — pushed (`f2c84c0`) |
| E5.1.3 crash consistency (50-kill loop, R-50) | **DONE** — pushed (`b5669da`) |
| E5.2.1+2.3 session bookkeeping + exit-with-running-ride flow | **DONE** — pushed (`abba0c1`) |
| E5.2.2 resume dialog (crash vs quit wording) + reopened banner | **DONE** — pushed (`665310c`) |
| E5.3 backups (keep 20) + R-18 delete guard | **DONE** — pushed (`769143e`) |
| E5.4.1 library live + mock-first Duplicate/Reopen dialogs + roster persistence | **DONE** — pushed (`c894b48`) |
| E5.4.2 demo retirement (tests-only import) + empty states + screenshots | **DONE** — pushed (`cfcc68e`) |
| Version 0.5.0 + design write-backs + this handoff | **DONE** |
| Headless gates (unit+property+simulations, lint, mypy, import-linter, ids drift) | **GREEN** — 1898 tests, 98.09% coverage |
| Functional suite in the Tart VM | **GREEN** apart from one documented pre-existing flake (`test_resume_dlg.py::test_resume_open_library_opens_ride_library_dlg` — a modal-dismissal hang named in that scenario's own docstring; fails on a fresh host too, outside E5's scope) |
| PR | Not yet opened (head `cfcc68e` against `master`) |

---

## 1 · What shipped

- **`rivercrossing.store`** — `Store.open` (spec §2 schema: ride/entry/rider/crossing/card/
  app_session/audit + a `schema_version` ledger; WAL · synchronous=NORMAL · foreign_keys=ON;
  linear numbered idempotent migrations; a future schema version refuses politely),
  `create_ride(config)` (DB-owned `rng_seed`, logo BLOB, tiebreak JSON, epoch instants),
  `rides()`, `append(ride_id, event)` (the `audit` log is the event store),
  `load_engine(ride_id, roster=None, *, clock=None)` (config+seed+events → identical engine),
  `save_roster`/`roster_for` (entry/rider tables; `has_data` derived, not stored),
  `duplicate_ride` (R-15: setup+roster only, fresh seed, zero timing data),
  `delete_ride(id, typed_name)` (R-18: type-name confirm, backup first, never RUNNING),
  session bookkeeping (`previous_session`/`set_active_ride`/`close_session` →
  `SessionState` CLEAN_QUIT/CRASHED/RUNNING_AT_EXIT read from the second-newest row).
- **`rivercrossing.store.backup`** — `run(path, keep=20)` (WAL-checkpointed copy into
  `<db>.backups/`, rotation), `schedule_hourly` seam (injectable clock), `restore`.
- **`RideEngine.apply(event)`** — the replay seam (dispatch on `event.action`; `shoe_reshuffle`
  is a documented no-op; unknown action raises). The equivalence property (300 stress + 50
  Hypothesis examples) proved live == replayed; it surfaced and fixed an E4 defect (undo after
  `deal_manual` raised `RestitutionError` — now retires the manual card instead).
- **Crash consistency** — `tests/simulations/test_store_crash_consistency.py` +
  `store_crash_child.py`: 50-kill loop (seeded kill points, SIGKILL/TerminateProcess, checkpoint
  protocol) proving R-50 — reopen keeps exactly a committed prefix, `integrity_check` ok.
- **Sessions/UI** — `exit_running_dlg` (three buttons: Cancel default · `finish_first_btn` →
  finish flow · Quit-keep-running stamps `closed_at`; `message_lbl` names the ride) wired into all
  quit paths; `resume_dlg` with crash-vs-quit wording (`ui/resume_flow.py`), Continue resumes with
  correct elapsed, the library path; `reopened_infobar` shown for REOPENED rides.
- **Library live** — `ride_library_dlg` on the real DB: open (console context switch),
  new (setup), duplicate (confirm → `duplicate_ride`), delete (R-18); RUNNING/no-selection gates.
- **Mock-first (the two windowless §15 routes)** — `duplicate_ride_dlg` and `reopen_ride_dlg`
  authored and their control names registered in spec.md §15b before wiring, replacing E1.4.1's
  sentinel; `ids.py` regenerated to 175 constants (26 windows).
- **Demo retirement** — import-linter contract tightened to "only tests import
  `rivercrossing.demo`" (red-proven then green); every screen shows real data or a documented
  empty state; empty-state screenshots under `tests/functional/_screenshots/`.

## 2 · Decisions recorded (write-backs landed)

- **wx⇄asyncio integration** (spec §10's EPIC-5 decision point): `wxasync` remains ruled out;
  E5 keeps the sqlite3 writer **synchronous** (one fast WAL transaction per `append`; the 50-kill
  loop proves durability), and when a genuinely async writer is needed (E6 exports/imports
  progress) the mechanism is **`wx.CallAfter` + a background thread**. Recorded in project-plan.md
  §7 (risk row resolved).
- **Replay ordering** is insert-id, never `at` (back-dated set_start_time sorts earlier in `at`).
- **`duplicate_ride` is not audited** (audit is the replay channel; an unknown action would break
  `load_engine`).
- **Backup location** `<db>.backups/`; keep floor 1; busy WAL checkpoint raises rather than
  silently backing up stale data.
- **`has_data` derived** at load (crossing/card rows), never stored.
- **Library status column** = stored `ride.status` (engine-sync deferred).
- **Demo module retained** as test-only fixture data.

## 3 · Known reds / flakes carried forward (do not re-litigate without the user)

- **Windows stage-3 functional**: red from the confirmed upstream wx/SIP + XRC degradation
  (EPIC3-SESSION-SUMMARY Addendum 2); **deprioritized by product decision 2026-08-28** — macOS is
  the working gate.
- **`test_resume_dlg.py::test_resume_open_library_opens_ride_library_dlg`**: pre-existing
  modal-dismissal flake (its scenario docstring names the hang; fails on fresh hosts, store-backed
  path unchanged by E5.4.2). Root-cause it or quarantine it early in E6 if it keeps tripping.
- **`test_mini_acceptance.py::test_mini_acceptance_finish_confirm_cancel_leaves_ride_running`**:
  intermittent native segfault under wx churn (passes on some workers).
- **Testing discipline (HARD)**: on macOS, functional tests run ONLY in the Tart VM via
  `scripts/run_functional_tests_vm.sh` — never `pytest tests/functional/...` on the host (real
  windows; crash children can wedge the host WindowServer). Headless tests run locally.

## 4 · Resuming — EPIC 6 (Results & publishing)

Entry gate: E2 + E5 exits. macOS is green; open the E5 PR (`EPIC 5 — Persistence & crash
recovery`, head `cfcc68e`), merge, branch `topic/epic-6-results-publishing` from `master`.

- **Brief**: `design/epic-prompts/EPIC-6-results-publishing.md`; task list under
  `design/docs-md/task-briefs.md` E6 block.
- **E6 highlights**: `rivercrossing.standings` core already shipped in E4 (rank/tie-breaks/
  leaderboards) — E6.1 adds the prose hand-name renderer, the results-window live wiring, and the
  draggable tie-break reorder UI; **E6.2.1 vendored Tailwind CSS build step** (can start after
  E1 — build the `compiled_css` artifact + staleness gate); E6.2.2 HTML render + goldens
  (templates already ship verbatim: `design/templates/base.html.j2` + the two golden samples in
  `design/exports/` — their `race-data` JSON blocks are the fixtures); E6.3 PDF via fpdf2
  (deterministic bytes); E6.4 results window live + the finish gate (`FINISH_GATE` stub in
  `ui/presenters/console.py` — E6.4.3 wires the real evaluator self-test).
- **Seams E5/E4 left for E6**: `results_win.py` shows an empty state (E5.4.2) waiting for E6.1/
  E6.4; `StandingsRow.hand` is a short code awaiting the E6 prose renderer; the
  `EngineDataSource.standings()` maps `standings.rank(engine.snapshot())` already.
- **Gotchas**: `format_duration` lives in `ui/presenters/data_source.py`; the `ExportOptions`
  dataclass + `to_record()` already exist (`tests/unit/test_payload.py`) from E1.2.2 — E6.2
  consumes them; the mini-acceptance's hand-verified standings fixture is the model for E6.1's
  fixtures.

## 5 · Numbers

- Unit + property + simulations: **1898 passed**, coverage **98.09%** (line+branch ≥ 90% gate).
- Functional (Tart VM): full suite green apart from the one documented `test_resume_dlg` flake;
  empty-state screenshots captured.
- Core changed-scope branch coverage: `store` 100%, `backup` 100%, `ride.apply` 100%, `resume_flow`
  100%.
