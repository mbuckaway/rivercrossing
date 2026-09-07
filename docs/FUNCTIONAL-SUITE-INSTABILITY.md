# RiverCrossing — Functional-Suite Instability (follow-up brief)

**Date:** 2026-09-07 · **Status:** open follow-up — an agent is to investigate and fix.
**Scope:** the wx functional suite (`tests/functional`, ~1 070 items) fails intermittently on
macOS (and less often Windows) CI and in the local Tart VM. The failures are a pre-existing
wxPython/SIP wrapper-cache instability class, amplified by suite growth in the ux-polish
change (PR #39). Every *reproducible* test bug found during that work has already been fixed
(listed below); what remains is the flake class.

---

## 1. Symptom summary

The functional stage passes and fails for identical code across runs. Observed this session
(all on the same merged master code):

- PR #39 (feature): run A — macOS functional **pass** (10m43s), Windows functional **pass**
  (20m44s); run B (same commit) — macOS functional **fail** (33m25s).
- PR #40 (version bump): all checks green.
- PR #41 (CI budget 600s→1200s): all checks green after one rerun.
- PR #42 (CI `-n 2` + 2400s; still open): Windows functional **pass** (40m56s, twice);
  macOS functional **fail** (2h5m) on attempt 1.
- Tag run v1.0.10: macOS functional failed on three attempts (attempts flipped between
  macOS and Windows failing).
- Local Tart VM full runs: converge to `0 failed file(s)` on rerun *sometimes*; the most
  recent runs did not converge (one hung in a rerun pass, below).

The failing-file set changes between rerun passes of the same run — the signature of a
load/timing flake, not a deterministic test bug.

## 2. Failure modes observed (with evidence)

### 2.1 wx/SIP address-reuse LookupErrors in `find_control`
- Symptom: `LookupError: main_frame has no control named 'ride_name_lbl' (first-level
  children: 10 -- ['ride_name_lbl', 'ride_status_panel', ...])` — the control **is** in the
  child list, yet `FindWindowByName` returns `None` (or the wrong wrapper) after the 25-retry
  settle loop. Same shape on `ride_setup_dlg` (`logo_picker`), `stop_btn`, `arm_stop_chk`,
  `clock_remaining_lbl`.
- Root cause (documented in `src/rivercrossing/ui/views/_support.py`, `find_control`
  docstring): wxPython wraps C++ objects by pointer identity via SIP. XRC-loaded controls are
  C++-constructed and nothing notifies SIP when the C++ object is destroyed, so a Python
  wrapper that outlives its object (lingering reference, swallowed traceback, retained view)
  poisons every later allocation at that address. Upstream: wxWidgets/Phoenix #2931,
  Python-SIP/sip#113, wxWidgets/wxWidgets#26789. No released fix.
- The current mitigation (type-checked retry + `wx.SafeYield()` + dropping the wrapper) is
  explicitly documented as *not* a complete fix "under sustained load across a whole test
  session".
- CI evidence: `_support.py:106 LookupError` at `at setup of test_live_console_*` (6 tests
  sharing a module fixture that builds a `MainFrame`), PR #42 attempt 1 log.

### 2.2 wx-churn worker crashes (segfaults)
- Symptom: `[gwN] node down: Not properly terminated`, `OSError: cannot send (already
  closed?)`, `Fatal Python error: Segmentation fault` in `wx._core` (e.g.
  `meth_wxWindow_GetPosition`), `INTERNALERROR> KeyError: <WorkerController gwN>` (xdist
  reschedule failing after a crash).
- The repo's own docs state the suite historically suffers "4–6 wx-churn segfaults per run"
  (AGENTS.md, `noxfile.py` functional session comments, commit #31).

### 2.3 Fault-A leaked top-level windows
- Symptom: at teardown — `functional session ended with 6 top-level wx window(s) still
  alive: ['main_frame', 'main_frame', ...] -- a load+construct path leaked (Fault A)`.
- Mechanism: when a test crashes (segfault) or hangs, its `finally: close_window` never
  runs; the leaked frame's controls then poison later `FindWindowByName` results (2.1) and
  later Fault-A checks fire on unrelated tests. The leak check is in `tests/functional/
  conftest.py`; see also `harness.close_window`.

### 2.4 Modal hangs
- Symptom: a test blocks forever in a modal loop; the xdist worker produces no output.
  Observed: `test_reopened_mode.py::test_reopened_mode_finish_again_relabels_dialog_
  relocks_and_reranks` hung on a `--reruns` retry for ~40 min with zero log output (local VM
  run, 2026-09-07). Also `at setup of test_results_stale_banner_appears_after_a_post_export_
  correction_and_reexport_clears` (setup of a results-frame test) hung on CI.
- The suite previously had a "modal drives" flake class fixed in commit #31 ("Fix
  functional-suite flakiness: modal drives + Fault-B guard").

### 2.5 Pass-budget kills (the amplifier)
- `tools/functional_rerun.py` runs each pytest pass as a subprocess bounded by
  `RIVERCROSSING_FUNCTIONAL_PASS_TIMEOUT_S` (default 600 s). On timeout it kills the pass and
  marks **whatever was in flight** as failed ("pass exceeded Ns — child terminated",
  `exit 124`).
- Before the ux-polish suite growth, a full pass fit in 600 s. After growth the full pass
  takes 20–40 min at 2 workers, so with the old 600 s budget **every** pass was killed and
  the rerun logic could never converge (observed on CI and locally). Raising the budget
  (1200 s, then 2400 s in PR #42) lets passes complete but does not cure 2.1–2.4, and a
  single hang (2.4) still costs a whole pass budget.

## 3. Already fixed (reproducible bugs found while chasing the flake — do not re-investigate)

All on `master` via PRs #39/#40/#41 (plus PR #42 for the CI budget):

1. **`test_team_editor_dlg_action_buttons_sit_below_their_own_panes` segfault** — asserted on
   live controls (`GetPosition`) *after* `harness.close_window(dialog)`, segfaulting in
   `wxWindow_GetPosition` on a destroyed peer (CI). Fix: capture every position before
   close; also compare in **screen space** (`GetScreenPosition`) because `members_list` lives
   inside a `wxStaticBoxSizer` box window while the buttons are direct dialog children —
   parent-relative coordinates are not comparable.
2. **`gauges.py` float coordinates → `TypeError` on macOS only** —
   `wx.DC.DrawCircle/DrawLine` overloads take integers; float args raise on the Cocoa
   backend (MSW coerces), crashing any screen rendering the console clocks/stop-light.
   Fix: `round()` every coordinate at the draw calls (`src/rivercrossing/ui/views/
   gauges.py`).
3. **`store_staging.append_ride_events` staged riderless RUNNING rides** — hit the new
   R-79 start gate (`StartBlockedError: roster has no riders`), breaking the resume/quit/
   exit scenarios. Fix: stage one rider (`Alice`/`12`) before `engine.start()`.
4. **`test_console_live` RaceClock fraction test** asserted a clock fraction that only the
   presenter tick renders; now drives one `presenter.tick()` and asserts real RUNNING
   fractions.
5. **No-ride prompt (`no_ride_dlg`) blocked `main()` probes and store-backed scenarios** —
   the `test_main_shows_the_frame_before_entering_the_event_loop` subprocess probe timed
   out; the ride-library reopen/duplicate scenarios returned `None` data. Fixes in
   `test_app_bootstrap.py` + `console_subprocess_scenarios.py` dismiss the prompt.
6. **Fault-A hardening** in the ux-polish test builders (`test_menu_coverage`,
   `test_console_live`, `test_review_tabs`): close the frame if the construct/wire phase
   raises, so a failure cannot leak a `main_frame`.

## 4. Mitigations already applied (CI + local)

- `scripts/run_functional_tests_vm.sh` and `.github/workflows/ci.yml` already run the suite
  through `tools/functional_rerun.py` with `--reruns 2` (process-fresh reruns of failed
  files) — load-bearing, see `noxfile.py` and docs/EPIC3-SESSION-SUMMARY.md Addendum 2.
- Per-pass budget raised: 600 s → 1200 s (PR #41, merged) → **2400 s + `-n 2` workers
  (PR #42, open, branch `fix/ci-functional-deterministic`)**. PR #42 also caps xdist at 2
  workers instead of `-n auto` — the repo's own VM runner measures many concurrent wx
  windows as crash-prone and 2 workers as convergent.
- Local: `scripts/run_functional_tests_vm.sh` now propagates
  `RIVERCROSSING_FUNCTIONAL_PASS_TIMEOUT_S` into the guest (currently uncommitted in the
  working tree — needs committing with the follow-up).
- CI reruns of a failed functional job usually pass (all of PRs #39/#40/#41 reached CLEAN
  via reruns).

## 5. What remains — the flake classes to attack

1. `find_control` address-reuse LookupErrors under sustained load (2.1) — especially
   module-scoped fixtures that build a `MainFrame` late in a session.
2. Occasional modal hangs (2.4) that cost an entire pass budget.
3. Fault-A leaked windows from crashed tests (2.3) poisoning later modules.
4. Large per-pass time (the suite is > 1 000 items; a full pass is 20–40 min at 2 workers),
   which makes every hang/crash expensive and any budget a compromise.

## 6. Investigation pointers for the follow-up agent

### Files to read first
- `src/rivercrossing/ui/views/_support.py` — `find_control` docstring (the canonical write-up
  of the SIP wrapper-cache problem and current mitigation).
- `tools/functional_rerun.py` — pass-budget logic (`PASS_TIMEOUT_S`, whole-suite fallback,
  exit-124 semantics).
- `tests/functional/harness.py` — `close_window`, `load_window_verified`, Fault-A teardown
  check; `tests/functional/conftest.py` — the leak check.
- `noxfile.py` functional session + `.github/workflows/ci.yml` stages 3 (functional) and 4
  (acceptance).
- Historical fixes: commit `a097085` ("fix(tests): make the functional pass bound and worker
  count configurable (#23)") and commit `381f8bd` ("Fix functional-suite flakiness: modal
  drives + Fault-B guard (#31)"); `docs/EPIC3-SESSION-SUMMARY.md` Addendum 2.

### Suggested directions (NOT yet tried — evaluate before implementing)
- **Per-test process isolation is the only complete cure for 2.1** (a fresh process has a
  fresh SIP map). `functional_rerun.py` already respawns per *file*; consider shrinking the
  isolation granularity for the worst offenders, or running each file in its own process
  with a small worker count.
- **Reference-hygiene audit**: find wrappers that outlive their C++ objects — retained views
  in module fixtures, swallowed-exception tracebacks holding windows, `frame.console = self`
  style cycles that keep controls alive past `Destroy()`. The leak check names the leaking
  test; run with `-p no:randomly` and `faulthandler` to catch the first leaker.
- **Reproduce the hangs deterministically**: wrap suspect tests (results-frame /
  finish-flow / reopen-flow drives) with a per-test alarm/timeout in a throwaway branch and
  capture the modal stack (which dialog is open, who should dismiss it). Check whether the
  hang follows a crash in the same worker (crash → rerun → the retried test inherits a
  poisoned process state).
- **Measure the pass-time budget honestly**: log per-file wall time on a clean run; find the
  slow files; set `RIVERCROSSING_FUNCTIONAL_PASS_TIMEOUT_S` to a margin above the worst
  observed full-pass time (do not tune it down to make CI "fast").
- **Reduce concurrent wx churn**: module fixtures that build `MainFrame`/`results_frame`
  (e.g. `test_console_live.shared_live_console`, `test_menu_coverage` builders) serialize
  badly under xdist; consider `-n 2` (already in PR #42) and possibly `--dist loadfile`
  group ordering so window-heavy modules do not overlap.
- **Windows vs macOS divergence**: macOS is stricter (float coercion, slower). Keep macOS as
  the canary; fix on macOS, confirm on Windows.

### Evidence logs (this session)
- CI runs: PR #39 (34085317629 pass / 34085315152 fail-then-rerun-pass), PR #40
  (34091067581), PR #41 (34102223359), PR #42 (34114249576), tag v1.0.10 (34093231856,
  3 attempts). `gh run view <id> --log-failed` + the per-job `logs` API give full output.
- Local Tart VM logs were written to `.vm_*.log` in the repo root during the session (may
  have been deleted; rerun `RIVERCROSSING_VM_TIMEOUT=5400 RIVERCROSSING_FUNCTIONAL_PASS_
  TIMEOUT_S=2400 RIVERCROSSING_FUNCTIONAL_JOBS=2 scripts/run_functional_tests_vm.sh` to
  reproduce).

## 7. Acceptance criteria for the follow-up

1. N consecutive clean full functional runs on macOS **and** Windows (propose N=3) — CI and
   local VM — with zero `find_control` LookupErrors, zero Fault-A teardown errors, zero
   hangs.
2. The per-pass budget in `.github/workflows/ci.yml` and `scripts/run_functional_tests_vm.sh`
   is set from a measured full-pass time plus margin, and the suite completes in one pass
   without relying on reruns to reach green.
3. Any remaining `--reruns`/functional_rerun usage is a backstop, not the path to green.
