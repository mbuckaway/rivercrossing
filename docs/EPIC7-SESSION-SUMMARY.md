# EPIC 7 — Session Summary and Hand-off

**Written:** 2026-08-29 · **Branch:** `topic/epic-7-corrections-audit` · **Version:** `0.7.0`
**Purpose:** EPIC 7 (Corrections & audit) is implemented; this file records what shipped, the
decisions taken, and what the next machine needs to resume into EPIC 8.

---

## Status at a glance

| Item | State |
|---|---|
| E7.1.1 correction commands (edit/void/add-at-time/reassign/DNF/void-card + undo reason) | **DONE** |
| E7.1.2 recompute cascades + replay + DNF in `snapshot()` | **DONE** |
| E7.2.1 correction dialogs live + live menu-enablement binder | **DONE** |
| E7.2.2 REOPENED corrections-only mode + "Finish again" | **DONE** |
| E7.3.1 audit viewer (`Store.audit_rows` + presenter/view) | **DONE** |
| E7.3.2 stale-export flag | **DONE** |
| Shoe re-open on reopen (product decision) | **DONE** |
| Design write-backs (spec §4, §15, §15b; xrc-windows.md; project-plan.md E7 row) | **DONE** |
| `__version__` 0.7.0 + CHANGELOG | **DONE** |
| Headless gates (unit+property+simulations, lint, mypy, import-linter, ids drift) | **GREEN** — 2394 tests, 98.43% coverage |
| Functional suite (Tart VM) | **PENDING** — ~50 new functional tests written, collected, not yet run in the VM |
| PR | not opened (see §6) |

## 1 · What shipped

- **E7.1 command layer** (`rivercrossing/ride.py`) — `edit_crossing`, `void_crossing`,
  `add_crossing_at`, `reassign_crossing`, `mark_dnf`, `void_card`; each requires a non-empty `reason`
  and writes one audit `Event`; `undo_last` carries `reason="Undo last crossing"`; all six actions
  dispatch in `apply()`; `snapshot()` marks DNF entries (`dnf=True`) so `standings.rank` places them.
  The replay-equivalence contract now compares `Shoe.is_closed`; the "history + corrections replayed ==
  directly corrected history" property is green.
- **E7.2 correction UI** — `DetailPresenter` implemented; entry detail's six buttons + every Cards-menu
  route drive the real commands via `ui/views/corrections.py`; `ui/menu_state.py` applies
  `is_route_enabled` at runtime (the missing E1.4.2 binder). REOPENED renders corrections-only: plate
  entry disabled, corrected crossings bolded in the feed (`FeedRow.edited` +
  `corrected_crossing_keys`), `reopened_infobar`, single "Finish again" (`finish_again_labels`).
- **E7.3 audit + stale** — `Store.audit_rows(ride_id)` (newest-first, `AuditRow` view-model) +
  `AuditPresenter` + `ui/views/audit.py` with plate-or-entry-name search and the six §15 action
  buckets; entry-detail deep-link (R-38). Stale-export flag: watermark = `len(engine.events)`,
  `None` = never exported; a post-export correction shows `stale_infobar`, re-export clears.
- **Mock-first window** — `void_card_confirm_dlg` authored in `dialogs.xrc` (voids a dealt card; names
  `card_lbl` + shared `reason_input`), registered in spec §15b, `mi_void_card` retargeted; the
  `_UNAUTHORED_DIALOG` sentinel is gone.

## 2 · Decisions recorded (write-backs landed)

- **D1 — shoe re-open on reopen.** `finish()` closes the shoe; `reopen()` now re-opens it so
  `deal_manual` and `add_crossing_at` keep dealing in REOPENED (resolves the spec §4-vs-§15 tension).
  Recorded in spec §4 + project-plan.md E7.
- **D2 — `reassign_crossing`'s `seq`** is the ride-wide ordinal into `engine.crossings` (unique across
  the ride and replay-exact; a per-entry lap number would be ambiguous).
- **D3 — correction menu routes target the current entry** (`_RouteContext.detail_plate`), because the
  correction dialogs carry no plate/crossing selector. Edit/Reassign target the current entry's latest
  crossing; Void Card its latest dealt card; DNF the current entry.
- **D4 — `add_crossing_at` credits directly (never holds).**
- **D5 — "edited"** = a crossing touched by any correction event (`edit_crossing`, `add_crossing_at`,
  `reassign`); `void_crossing` removes the row (compensating write).
- **D6 — `Store.audit_rows` returns the `AuditRow` view-model** (the core→presenters seam `demo.py`
  already uses); `when` derives from the stored `at` epoch, newest-first by insert `id`.
- **D7 — the move-rider picker is built in code** (no XRC window exists in §15b).
- **D8 — `CORRECTION_ACTIONS`** = `{add_crossing_at, edit_crossing, reassign, deal_manual, dnf,
  void_card, void_crossing}`; live-entry mutators (`record_crossing`, `undo`, `set_start_time`,
  `confirm_held`/`void_held`) never trip the stale flag.

## 3 · Known reds / flakes carried forward (do not re-litigate)

- **Windows stage-3 functional**: red from the confirmed upstream wx/SIP + XRC degradation
  (EPIC3 Addendum 2); product decision 2026-08-28 — macOS is the working gate.
- **`test_functional_rerun.py`** had a one-line `ruff format` collapse applied during E7 (pre-existing
  drift; the whole-tree format gate required it).

## 4 · Resuming — EPIC 8 (Settings & assistance)

Entry gate: E7 exit (headless green). Brief: `design/epic-prompts/EPIC-8-settings-assistance.md`; task
list under `design/docs-md/task-briefs.md` E8 block. `docs/EPIC8-SESSION-SUMMARY` does not exist yet.

## 5 · Numbers

- Unit + property + simulations: **2394 passed**, coverage **98.43%** line / **98.26%** branch
  (≥90 gate), `ride.py` 100% branch.
- Functional: **~50 new tests written, NOT yet run** (Tart VM only — `scripts/run_functional_tests_vm.sh`).

## 6 · Outstanding for the orchestrator

- Run the functional suite in the Tart VM (`scripts/run_functional_tests_vm.sh`) — the ~50 new E7
  functional tests (`test_corrections.py`, `test_void_card_confirm.py`, `test_entry_detail_actions.py`,
  `test_audit.py`, `test_reopened_mode.py`, + the `test_results_exports.py` stale-banner test).
- Open the EPIC 7 PR (one PR per EPIC, per the E6 precedent) off this branch; `git commit` was NOT run.
- `uv pip install -e .` to refresh the editable install to 0.7.0 (the version-bump packaging test needs it).
