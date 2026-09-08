# Functional-Suite Flakiness — Full Session Record & Hand-off for Review

**Written:** 2026-09-08 (session of 2026-09-07 20:00 → 2026-09-08 ~03:00, continued to ~07:00)
**Branch:** `topic/fix-functional-suite-flakiness` (13 commits, see §1) · **PR:** [#45](https://github.com/mbuckaway/rivercrossing/pull/45)
**Predecessor:** `docs/FUNCTIONAL-SUITE-INSTABILITY.md` (the brief this session followed)
**Purpose:** record every functional test run, every issue found, every fix applied (and every fix tried and reverted), and the exact remaining defect — so a fresh agent can review the whole body of work without re-deriving it. The session looped for hours on one remaining leak; the goal of this doc is that the next agent does not loop again.

---

## 1. Branch state

13 commits on `topic/fix-functional-suite-flakiness` (oldest first):

| Commit | Scope |
|---|---|
| `ab2d124` | fix(core): harden CSV import boundary and drop dead guard code (python-review batch A) |
| `2cb1944` | fix(ui): surface CSV-preview refusals and correct stale docstrings (batch B, presenter part) |
| `b4be483` | fix(tools): defer wx imports, bound subprocess spawns, clean temp dirs (batch C) |
| `728668f` | test(unit): close branch gaps and attest logic coverage across the suite |
| `abe5fbd` | docs: python-review Stage 2 report, coverage/LCS audit, stale-doc fixes |
| `39e7c78` | fix(app): surface write failures as notices + crash-log excepthook (app.py / rider_editor.py) |
| `9432031` | fix(tests): modal-dismisser hardening and reference hygiene (Phase 3 F2/F3/F5/F6) |
| `9baf5ee` | fix(tools): stalled_files set attribution for killed passes (Phase 3 F4) |
| `be56976` | feat(tools): per-file fresh-process functional runner (fallback, plan-approved) |
| `72c2bc3` | ci: per-file functional wiring, collection guard, honest budgets |
| `611d900` | docs: python-review findings updated through the Phase 3/4 cycle |
| `3ab6cca` | fix(tests): release-time window inventory behind the close-debug env |
| `e85abec` | revert(tests): drop the release-time modal sweep |

Worktree: `/Users/mark/src/rivercrossing-functional-flakes` (venv at `.venv`). Main checkout: `/Users/mark/src/rivercrossing` (master, unchanged except VM logs). Raw VM logs named `.vm_*.log` sit in both repo roots — **not committed** (they are the evidence; keep them for review).

The branch also carries the unit-coverage/LCS audit doc: `docs/UNIT-COVERAGE-LCS-AUDIT.md`, and the python-review report: `python-review-findings.md`.

---

## 2. Everything tested (chronological run ledger)

VM = the Tart clone of `rivercrossing-func-template`, driven by `scripts/run_functional_tests_vm.sh`. Unless noted, the run used the worktree code via rsync. Env knobs: `RIVERCROSSING_VM_TIMEOUT` (host watchdog), `RIVERCROSSING_FUNCTIONAL_PASS_TIMEOUT_S` (old in-process per-pass bound), `RIVERCROSSING_FUNCTIONAL_PERFILE_TIMEOUT_S` (per-file bound), `RIVERCROSSING_FUNCTIONAL_PERFILE_JOBS` (per-file concurrency), `RIVERCROSSING_CLOSE_DEBUG` (env-gated harness diagnostics).

### A. Baseline — master, unchanged (`/Users/mark/src/rivercrossing/.vm_baseline.log`, 2026-09-07)
- Command: `RIVERCROSSING_VM_TIMEOUT=5400 RIVERCROSSING_FUNCTIONAL_PASS_TIMEOUT_S=2400 RIVERCROSSING_FUNCTIONAL_JOBS=2 scripts/run_functional_tests_vm.sh` (old in-process runner).
- Result: initial pass killed at 2400 s with 8 failed files (app_bootstrap, console_demo, delete_ride_dlg, menu_coverage, results_exports, ride_library_live, rider_editor, view_support); rerun 1 → 10 failures; whole-suite fallback killed again; the 5400 s watchdog killed the run before convergence. **188 LookupError occurrences** (`_support.py:106`), dominated by `delete_btn` (8), `crossings_list` (5), `add_btn` (4); Fault-A teardown with 3 × `main_frame`; the reopened-mode hang stalled a worker at ~99 %; collection reported **1070 items** (test_review_tabs dead, 1079 − 9).

### B. In-process `-n 2` proof cycles after F1–F3 (`.vm_proof1.log`, `.vm_proof2.log`, `.vm_full1..3.log`)
- All converged toward the same profile: 2–8 window-heavy files fail per pass with LookupError / Fault-A; `--reruns 2` and whole-suite fallback passes did not reliably converge.
- `.vm_full2.log`: first faulthandler dumps. **Two decisive captures:**
  1. `test_reopened_mode_finish_again_*` hung on the *no-ride-style* prompt path — thread dump showed the test thread stuck in `dialogs.run_dialog → ShowModal` (a modal whose dismisser never fired because the probe's click raised and wx swallowed it).
  2. `ERROR test_menu_state ... Timeout (0:10:00)` — a leaked modal from an earlier test held the worker; the Fault-A sweep reported `['main_frame', 'main_frame']`.
- Full suite never completed a single clean one-pass at `-n 2` on this branch during the session.

### C. Single-file runs (jobs=1, per-file runner) — the isolation evidence
All of these passed when run alone, in a fresh process:
- `test_reopened_mode.py` — 2/3 green single-file (`.vm_rm.log`, `.vm_rm2.log`, `.vm_mr_{1..3}.log` — MR-1 red, MR-2/3 green).
- `test_review_tabs.py` (after the F1 fix + the page-switch fix) — green (`.vm_rt2.log`).
- `test_menu_state.py` — green (`.vm_menu_state.log`).
- `test_app_bootstrap.py` — the no-ride tests pass alone; the file is just slow (~20 min at jobs=1 with subprocess scenarios) (`.vm_appboot.log`).
- `test_harness.py` — 31 tests, green in isolation.
**Conclusion:** every window-heavy file passes alone in a fresh process; multi-file in-process accumulation is what fails them.

### D. Full-suite runs on the per-file runner (the fallback)
- `.vm_perfile1.log`: 8 files failed with `AttributeError: 'App' object has no attribute 'main_frame'` in `release_main_window` (mirror builders never set it) — fixed with `getattr` (commit `9432031`).
- `.vm_perfile2.log`: about_dialog import-stall held a round until its bound; per-file bound tightened 2400 → 900 s.
- `.vm_perfile3.log`: **2 failed files, retry round fixed both** — first convergent full run.
- N=3 sequences (`.vm_n3*.log`, `.vm_n3f*.log`, `.vm_n3g*.log`, `.vm_n3h*.log`, `.vm_gate_*.log`, `.vm_ok_*.log`, `.vm_sw_*.log`, `.vm_clean_*.log`, `.vm_j1.log`, `.vm_rev1.log`): full-suite runs converge to green **~60–75 % of the time** within 3 fresh-process retry rounds, in **3–10 minutes** (vs. 2 h+ non-convergent on master). Best streak: **2 consecutive greens** (`.vm_sw_1.log`, `.vm_sw_2.log` — both with the 100-attempt close settle + session-end sweep reap).
- `.vm_clean_1.log`: **catastrophic regression** — 24 Fault-A sweeps (3 × `main_frame` per file). Cause: the release-time modal sweep (see §5). Reverted (`e85abec`); `.vm_rev1.log` after the revert: normal profile again (3 files failed initial, 2 recovered, reopened_mode the sole 4-round holdout).

### E. Headless gates (run on the worktree between every VM cycle)
- `nox -s lint typecheck importlint ids_drift unit` — always green after the review/audit phase; final counts 3251 passed / 2 skipped; coverage 99.8 % line.
- Unit coverage/LCS audit results in `docs/UNIT-COVERAGE-LCS-AUDIT.md`.

---

## 3. Issues found (with evidence) and the fixes applied

1. **Deterministic modal hang** — `test_reopened_mode.py:281`: `wx.CallAfter(_drive)` where `_drive(dialog)` required a positional arg → TypeError inside the modal loop, swallowed by wx → `finish_confirm_dlg` never dismissed → every pass containing the test hung until the budget kill (baseline evidence, doc §2.4). Introduced `a23fbb0` (EPIC 7). **Fixed** (commit `9432031`): zero-arg callback that looks the dialog up by name, with event-driven re-arm + EndModal leak-guard; the test now passes in the VM.
2. **Dead test file on CI** — `test_review_tabs.py:48` `datetime.UTC` on the imported class → collection error; run-mode xdist reports it only in the session-end summary, which a budget-killed pass never prints → 9 tests silently absent from CI since PR #39 (arithmetic: 1079 − 9 = 1070 items). **Fixed** (commit `9432031` + `72c2bc3`): module-level `UTC` import; CI collection-completeness guard (collect-only, no xdist, pinned count) before the functional step.
3. **Reference leaks pinning the SIP wrapper cache** (doc §2.1) — `app._bind_process_quit_paths` keeps `app.main_frame` and an app-level `QUERY_END_SESSION` closure per build; in-process teardowns never cleared them. **Fixed** (commit `9432031`): `harness.release_main_window(app, frame)` (really_quitting → close → clear main_frame via getattr → unbind → **restore really_quitting**) called from every in-process `build_main_window` teardown and mirror-builder teardown.
4. **Rerun attribution gap** (doc §2.5) — `functional_rerun.stalled_file` kept only the last started-but-unfinished test. **Fixed** (commit `9baf5ee`): `stalled_files` returns the full set; 124-path unions all.
5. **Modal-dismisser fragility class** — every `wx.CallAfter` modal dismisser could fail silently: not-yet-shown dialog (narrow race) or a click raising on a poisoned/dead wrapper (LookupError/RuntimeError) that wx swallows inside the modal loop → hang. **Fixed** (commit `9432031`): all dismissers re-arm event-driven and end with an EndModal leak-guard; probe excepts are broad (`Exception`) because dead C++ objects raise RuntimeError, not LookupError (measured).
6. **`really_quitting` session leak** — `release_main_window` (first version) left `really_quitting=True` on the session-scoped bare `wx.App`; every later in-process close then skipped its confirm dialog (sash/quit-flow tests misbehaved). **Fixed**: save/restore in the helper; harness contract tests updated.
7. **`main_frame` attribute missing on mirror apps** — `release_main_window` read `app.main_frame` directly; the live-context mirror builders never set it → AttributeError across 8 files in per-file run 1. **Fixed**: `getattr`.
8. **macOS tab-view visibility** — `test_review_tabs` page-switch asserted `IsShown()` on a page child; macOS tab views clip rather than un-show → the restored file's test failed deterministically on its first real execution. **Fixed**: settle on `IsShownOnScreen()` with idle drains.
9. **XmlResource subtree degradation** (upstream class, doc §2.1) — fresh loads intermittently drop one subtree (a different control each time); typed view-constructor lookups trip after Fault-B name verification passes. Mitigated: builder-level rebuild retry in `_build_live_console`; the real cure is process freshness (§4).
10. **Sweep fired on mid-deletion frames** — session-end Fault-A asserted while a `Close()`'d frame was still pending deletion. **Fixed** (commit `9432031`): the sweep reaps (pump + flush + collect, 25 rounds) before asserting; genuine survivors still fail.

---

## 4. The architectural change: `tools/functional_perfile.py`

The upstream wx/SIP wrapper-cache corruption and XRC degradation are **process-granular** (no released fix: wxWidgets/Phoenix #2931, Python-SIP/sip#113, wxWidgets/wxWidgets#26789). Every in-process mitigation reduced but never eliminated failures under a full session. The plan's approved fallback was implemented and is now the CI/VM runner:

- Each `tests/functional/test_*.py` file runs in its **own fresh pytest process** — `[python, -m, pytest, <file>, -v, --no-cov, --reruns 2, -o faulthandler_timeout=600]` (the faulthandler flag converts any hung test into a 600 s failure with a thread dump — no new dependency).
- **One file at a time by default** (jobs=1). jobs=2 (two concurrent wx processes) measured delayed frame reaping that leaked windows into the sweep; jobs=1 converges (measured 2026-09-08). `RIVERCROSSING_FUNCTIONAL_PERFILE_JOBS` overrides.
- Per-file bound 900 s (`RIVERCROSSING_FUNCTIONAL_PERFILE_TIMEOUT_S`); a stalled file costs one bound, not a whole pass.
- Up to **3 fresh-process retry rounds** of the still-failed files (`_MAX_RETRY_ROUNDS`); repo evidence says ~4 fresh runs converge (p^4 residual).
- Exit: 0 all green after retries, 1 otherwise, 2 usage. Accepts a directory or a single test file.
- 32 unit tests, 100 % branch. Wired into `noxfile.py` (functional session), `.github/workflows/ci.yml` stage 3, and `scripts/run_functional_tests_vm.sh` (guest command; defaults 900 s / 1 job / 5400 s VM watchdog).

**Measured end state:** full suite converges to green in ~5–10 min in ~60–75 % of runs; the residual is one test (see §6).

---

## 5. Fixes tried and REVERTED (do not re-attempt without new evidence)

1. **`close_window(strict=True)` in `release_main_window`** — a single leaked same-named `main_frame` made every later close raise "different window" → cascading failures (182 WindowStillResolvingErrors in one run). Reverted; `strict` remains available as an opt-in.
2. **Periodic `gc.collect()` inside the close settle loop** — broke the deterministic contract of the harness's own gc-counting tests under load. Reverted; the 100-attempt bound stayed.
3. **Release-time modal sweep** (`EndModal` every surviving modal before closing the frame) — caused the catastrophic 24-Fault-A regression (`.vm_clean_1.log`). Reverted (`e85abec`). **Why it backfired is not understood** — worth an investigation, not a blind retry.
4. **`RIVERCROSSING_CLOSE_DEBUG=1` on full-suite runs** — the stderr prints perturb wx timing (screen_smoke and harness fail deterministically under it). The env is for single-file diagnosis only.

---

## 6. The remaining defect (the circle this session ran in)

**`test_reopened_mode.py::test_reopened_mode_finish_again_relabels_dialog_relocks_and_reranks` leaks one `main_frame` at the session-end Fault-A sweep, in ~50–70 % of fresh per-file processes.** Consequences: the file fails ~50–70 % of initial rounds and survives the 3 retry rounds often enough to keep the N=3 gate at max 2 consecutive greens.

### Measured facts (all from `.vm_*` logs with `RIVERCROSSING_CLOSE_DEBUG=1`)
- Every `close_window` in the failing processes exits the settle at **attempt 0** with `closed=True, was_deleting=False` and `found_handle != handle` — i.e., every close of every *targeted* frame succeeds; the settle's "a different window now answers the name" exit fires.
- The survivor is a `main_frame`, **shown=True**, that is **never the target of any close in the process**:
  `RELEASE-DEBUG: releasing 'main_frame' (handle 47475193600); top-levels: 'main_frame':Frame(shown=True,... handle=47471887616), 'main_frame':Frame(shown=True,... handle=47475193600)`
  No dialog is ever present in the inventory. The extra frame appears between two tests and persists to the sweep.
- The sweep reap (25 × pump+flush+collect) cannot kill it, and the 100-attempt close settle (500 idle drains) cannot reap it during its owning test.

### Refuted hypotheses (with the evidence that killed them)
- **Slow deferred deletion** → not it: 100-attempt settle (500 idle drains) and sweep reaps don't help; the frame is shown=True (alive), not mid-deletion.
- **A pinned-by-modal parent** → the release-time modal sweep made everything worse (see §5.3); the inventory never shows a dialog.
- **Concurrency (jobs=2)** → jobs=1 single-file still leaks ~50 % (MR-1).
- **Teardown-path defect** → all closes of all *targeted* frames succeed (attempt-0 exits); the leaker is never targeted.
- **Cross-file accumulation** → leaks in fresh single-file processes.

### The lead (where the next agent should point)
The survivor is created by something **outside the test's own builder/release cycle**. Prime suspect: **`harness.load_window_verified`'s rebuild path** — when Fault-B verification fails a degraded first load it builds a second frame; if the first frame is not fully destroyed (or a second build happens when a stale same-named window still resolves), an orphan shown=True `main_frame` appears that no test ever releases. Secondary suspect: a route or presenter in the finish-again flow (results window via `MI_STANDINGS` at the end of the test) constructing an extra frame.

---

## 7. What the next agent should do (in order)

1. **Read** this doc, `docs/FUNCTIONAL-SUITE-INSTABILITY.md`, and the run ledger logs listed in §2 (keep the `.vm_*.log` files; do not delete).
2. **Reproduce in a loop, single-file, with diagnostics:**
   ```
   cd /Users/mark/src/rivercrossing-functional-flakes
   for i in 1 2 3 4 5; do
     RIVERCROSSING_CLOSE_DEBUG=1 RIVERCROSSING_VM_TIMEOUT=900 \
       RIVERCROSSING_FUNCTIONAL_PERFILE_TIMEOUT_S=900 \
       RIVERCROSSING_FUNCTIONAL_PERFILE_JOBS=1 \
       RIVERCROSSING_VM_TEST_PATHS="tests/functional/test_reopened_mode.py" \
       scripts/run_functional_tests_vm.sh > .vm_diag_$i.log 2>&1
   done
   ```
   Expect ~half the runs to fail at the sweep; each failing log contains the RELEASE-DEBUG inventories.
3. **Instrument the creation side** (the fix target, not the teardown side):
   - `tests/functional/harness.py::load_window_verified` and its rebuild path (`_fresh_resource`): log every frame creation — name, handle, shown state, whether a previous same-named frame was closed first and whether the close reaped.
   - Determine who creates the orphan: add the same inventory log to `MainFrame` construction in the finish-again flow (the `MI_STANDINGS` results frame at `test_reopened_mode.py:~327` is the nearest exotic creation; verify the results frame's close at ~336 fully reaps, and check whether the finish route itself opens a results frame that the test never sees).
4. **Fix at the creation side** — e.g., make `load_window_verified`'s rebuild close the first frame with the reap guarantee before building the second (or assert no same-named survivor before building), and/or make the finish-again test's results-frame handling reap-verified.
5. **Re-verify**: (a) 5× single-file reopened_mode loop, expect 5/5 green; (b) the N=3 gate command:
   ```
   CONSEC=0; for i in 1 2 3 4 5 6; do
     RIVERCROSSING_VM_TIMEOUT=5400 RIVERCROSSING_FUNCTIONAL_PERFILE_TIMEOUT_S=900 \
       RIVERCROSSING_FUNCTIONAL_PERFILE_JOBS=1 \
       scripts/run_functional_tests_vm.sh > .vm_gate_$i.log 2>&1
     rc=$?; echo "gate-$i: $rc"
     [ $rc -eq 0 ] && CONSEC=$((CONSEC+1)) || CONSEC=0
     [ $CONSEC -ge 3 ] && { echo "N=3 MET"; break; }
   done
   ```
   Expect 3 consecutive green full runs. (A green run may use the tool's fresh-process retry rounds as the documented backstop; log the retry lines.)
6. **Only after the local gate:** update this doc's §6/§7, then Windows validation on the PR's CI leg; if Windows-specific failures appear, package them for the Windows agent per `docs/WINDOWS-AGENT-HANDOFF.md`.

## 8. Open questions for the reviewer

- Why did the release-time modal sweep (EndModal of all surviving modals) cause a 24-Fault-A catastrophe? Understanding this may reveal the leak mechanism itself.
- Is `load_window_verified`'s rebuild path leaking its first-load frame when a degraded load triggers a rebuild, and does the sweep-reap in conftest interact with it?
- Does the finish-again flow (finish route → results) construct an extra top-level `main_frame`-named frame that the test neither sees nor releases?

## 9. Environment facts (for reproduction)

- Host: macOS (Tart VM `rivercrossing-func-template`, stopped between runs; clones `rivercrossing-func-$$`; `--no-graphics --no-audio`).
- Guest: Python 3.14.7, pytest 9.1.1, xdist 3.8.0, wxPython 4.3.1 — identical to CI.
- VM runner env knobs and defaults (current branch): `RIVERCROSSING_VM_TIMEOUT` 5400, `RIVERCROSSING_FUNCTIONAL_PERFILE_TIMEOUT_S` 900, `RIVERCROSSING_FUNCTIONAL_PERFILE_JOBS` 1 (perfile default), `RIVERCROSSING_CLOSE_DEBUG` forwarded when set.
- Headless gates: `nox -s lint typecheck importlint ids_drift unit` (3251 passed on the branch tip).
- The functional suite: 45 files, 929 items collected (928 + 1 smoke test deselected from unit runs by its marker).
