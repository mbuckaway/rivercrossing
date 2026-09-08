# Python Code Review Report

_Generated: 2026-09-07 | Project: rivercrossing (worktree: topic/fix-functional-suite-flakiness) | Type: basic | Scope: src/rivercrossing/** + tests/** + tools/*.py (full-codebase audit, user-requested)_

## Executive Summary

Full-codebase review across 218 Python files (~78K lines). The codebase is exceptionally disciplined: ruff ALL + ruff format + mypy strict + import-linter are all green (re-verified), SPDX-only headers everywhere, no bare excepts, no secrets, no shell=True, parameterized SQL, atomic writes, bounded subprocesses almost everywhere, and dense why-comments with measured evidence. The findings concentrate in four places: (1) the CSV import boundary (3 MEDIUM — non-numeric plates crash assembly, UTF-8 BOM silently drops the plate column, non-UTF-8 files escape the ImportConflict contract), (2) wx-swallowed write failures in the app layer (2 MEDIUM — unguarded CSV export and store writes vanish without operator signal), (3) stale docstring/doc numeric claims (the largest class, ~15 findings — "39 rows" vs 40, "at most twice" vs _MAX_RERUNS=4, "PR #42 open" vs merged), and (4) test-infra hygiene (unbounded subprocess spawns in unit tests, duplicated live-context builders across 8 functional modules that have already drifted into a real bug). Zero CRITICAL/HIGH findings. One finding — the duplicated live-context builder — is deferred into the functional-flakiness work (Phase 3 F3) where it belongs, because those builders are exactly the reference-leak sites that work is fixing.

## Detection Results

| Attribute | Value |
|-----------|-------|
| Project Type | basic (desktop wxPython app) |
| Python Version | 3.14 |
| General Patterns | No |
| Lambda Patterns | No |
| Pulumi Project | No |

## Phase Execution

| Phase | Agent | Status | Findings |
|-------|-------|--------|----------|
| Phase 1+2 - Standards & Type Safety (core) | python-standards-reviewer | passed | 2 |
| Phase 1+2 - Standards & Type Safety (ui+tools) | python-standards-reviewer | findings present | 7 |
| Phase 1+2 - Standards & Type Safety (tests) | python-standards-reviewer | passed | 1 |
| Phase 3 - Security & Monitoring (core) | python-security-reviewer | passed | 3 |
| Phase 3 - Security & Monitoring (ui+tools) | python-security-reviewer | passed | 7 |
| Phase 3 - Security & Monitoring (tests) | python-security-reviewer | passed | 10 |
| Phase 4c - Simplification (core) | python-simplification-reviewer | passed | 3 |
| Phase 4c - Simplification (ui+tools) | python-simplification-reviewer | passed | 1 |
| Phase 4c - Simplification (tests) | python-simplification-reviewer | passed | 3 |
| Phase 4d - Documentation | python-docs-reviewer | passed | 10 |

## Findings Summary

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High | 0 |
| Medium | 11 |
| Low | 27 |
| **Total (deduped)** | **38** |

## Findings

### Medium

| File | Line | Category | Description |
|------|------|----------|-------------|
| csvio.py | 1051 (+roster.py:272,1108) | unhandled-value-error | Non-numeric plate on a pooled team makes `min(plates, key=int)` crash assembly with a raw ValueError instead of a per-row ImportConflict. Fix: reject non-digit NUMBER cells at preview, or make the lowest-plate helper numeric-tolerant, or raise a domain error preview can carry. |
| csvio.py | 201 (+preview :378) | input-validation | The NUMBER header matcher is the only `fullmatch`; a UTF-8 BOM survives into the first header cell and silently disables the plate column (plates reassigned, no conflict). Fix: open with `encoding="utf-8-sig"` or strip a leading `\ufeff`. |
| csvio.py | 378-389, 568 | undocumented-boundary-exception | Non-UTF-8 (e.g. cp1252) or `csv.Error` files escape the module's ImportConflict contract as untranslated exceptions. Fix: decode defensively, translate UnicodeDecodeError/csv.Error into a file-level conflict or a named CsvIoError. |
| views/rider_editor.py | 768 | swallowed-io-error | `csvio.export` unguarded at both call sites (app.py:1141-1143, rider_editor.py:328); a failed write raises OSError inside a wx handler that swallows it — operator believes the export succeeded. Fix: try/except OSError and post `Export failed: {exc}` (mirror `_handle_backup_database`). |
| app.py | 767-769 (+816,654,1123,1159,2246,704) | swallowed-db-error | Store writes (`create_ride`, `duplicate_ride`, `delete_ride`, `save_roster`, `close_session`, the `on_event` append sink) are unguarded; a DB/disk error in a menu/button handler is swallowed by wx with zero signal — worst case the operator is told an import succeeded while the roster silently stays unpersisted. Fix: wrap in try/except (OSError, sqlite3.Error) and post a status notice. |
| tools/timer_repro.py | 22 | import-contract | Module-level `import wx` outside rivercrossing.ui breaches R-71 and is invisible to import-linter. Fix: defer the import into `main()`, matching ci_gui_probe.py / toolkit_probe.py. |
| tools/resume_scenario_repro.py | 15 | import-contract | Same R-71 breach; defer `import wx` into `main()`. |
| commands.py | 5 | documentation-accuracy | Docstring says "39 menu rows" twice; ROUTE_TABLE has 40 (pinned by tests/unit/ui/test_commands.py:118). Fix: 39 → 40. |
| menu_state.py | 33 | documentation-accuracy | Docstring says "(49 ids)"; ROUTE_TABLE carries 50. Fix: 49 → 50 (or drop the count). |
| dialogs.py | 43 | documentation-accuracy | Module docstring enumerates five no-`<default>` dialogs; DEFAULT_BUTTON_DECISIONS lists six (rider_issues_dlg added, R-76). Fix: add the sixth, "these six". |
| functional_rerun.py | 17 | documentation-accuracy | Docstring says failed files rerun "at most twice"; `_MAX_RERUNS = 4` (:65) with measured rationale. Fix: name `_MAX_RERUNS` in the docstring (also noxfile.py:127 comment — deferred to Phase 3 F4 area). |
| test_corrections.py | 109 (+7 sibling modules) | premature-abstraction | The live-context builder is duplicated 79%-verbatim across 8 functional modules and already drifted into a real bug (aware-vs-naive clock TypeError). **DEFERRED to Phase 3 F3** — these builders are the reference-leak sites; the shared builder + `harness.release_main_window` land together. |
| test_demo.py | 72 | missing-subprocess-timeout | Unbounded `subprocess.run(mypy)` in an always-run unit test can stall CI forever. Fix: `timeout=` (120 s) + named TimeoutExpired failure. |
| test_protocols.py | 470 | missing-subprocess-timeout | Same unbounded mypy spawn. Fix: `timeout=120`. |

### Low

| File | Line | Category | Description |
|------|------|----------|-------------|
| htmlexport/__init__.py | 452 | comment-quality | False noqa justification ("this 3.14 build lacks datetime.UTC"); the module-level `UTC` exists and store/__init__.py:166 imports it. Fix: use `datetime.now(UTC)`, drop the noqa+comment (or reword honestly). |
| pdfexport.py | 1194, 1248 | comment-quality | Same false "mypy lacks datetime.UTC" justifications. Fix as above. |
| ride.py | 1651 | dead-code | Unreachable cross-entry `else` arm in `_replace_crossing` plus its logic-coverage-exempt comment. Fix: delete the arm and pin the same-entry invariant in the docstring. |
| roster.py | 1048 | dead-code | Fused `in_use or seen` guard whose conjunction is impossible, forcing a 9-line exemption comment. Fix: split into two sequential guards; delete the comment. |
| store/__init__.py | 796 (+871,1218,1246) | dead-code | `lastrowid is None` narrowing guard + exemption comment duplicated 4×. Fix: extract one `_require_rowid(cursor)` helper. |
| console.py | 78 (+36-38; app.py:1498) | noise-comment | Stale "stub returns True until E6.4.3 wires the real evaluator" comments; the gate runs the real `hands.self_test()`. Fix: delete/reword the three stale sentences. |
| ride_library.py | 387 | comment-accuracy | Comment cites nonexistent `_update_delete_enablement`; the method is `_update_action_enablement` (:285). Fix: rename in comment. |
| dialogs.py | 231 | documentation-accuracy | "Four already-authored dialogs" → six. Fix: update count or reference DEFAULT_BUTTON_DECISIONS by name. |
| settings.py | 117 | legacy-syntax-trap | `except OSError, ValueError:` (PEP 758 legacy form) reads as a bug. Fix: `except (OSError, ValueError):`. |
| riders.py | 297 | swallowed-input-error | Unreadable picked CSV propagates OSError out of the presenter into a swallowing handler; nothing happens. Fix: catch (OSError, ValueError) around csvio.preview and route to show_validation/notice. |
| main_frame.py | 728 (+709,720; app.py:837) | swallowed-settings-write | `persist_layout`'s unguarded save fires from sash/move/size/close handlers; a failure can stall quit or re-raise on every drag event. Fix: try/except OSError + status notice; let EVT_CLOSE proceed. |
| app.py | 3103-3112 | observability-gap | No `OnExceptionInMainLoop`/excepthook — swallowed handler exceptions vanish in a windowed bundle. Fix: write traceback to a per-user log next to settings.json + one-line notice; sys.excepthook for bootstrap. |
| toolkit_probe.py | 398-404 | timeout-handling | `subprocess.TimeoutExpired` unhandled — one hung check aborts the whole matrix, defeating the tool's isolation design. Fix: catch it, emit the CRASH line, continue. |
| test_demo.py | 356 | missing-subprocess-timeout | Import-probe spawn unbounded. Fix: timeout=60. |
| test_layering.py | 62 | missing-subprocess-timeout | Same. Fix: timeout=60. |
| test_protocols.py | 506 | missing-subprocess-timeout | Same. Fix: timeout=60. |
| test_vm_scripts.py | 185 | missing-subprocess-timeout | Unbounded shellcheck spawn. Fix: timeout=30 + named failure. |
| test_release_signing.py | 73,111,140,180 | missing-subprocess-timeout | codesign/hdiutil/spctl spawns unbounded. Fix: timeout=60 each. |
| test_dmg_smoke.py | 64,77,109 | missing-subprocess-timeout | hdiutil attach/detach/verify unbounded (attach in a fixture blocks teardown too). Fix: timeout=60-120. |
| test_bundle_smoke.py | 830 | missing-subprocess-timeout | lipo spawn unbounded (sibling at :768 has one). Fix: timeout=30. |
| store_staging.py | 50 (+scenarios :182,1591,1701; bootstrap :176) | uncleaned-temp-artifacts | `mkdtemp()` trees never removed — temp-dir litter and stale-state confusion. Fix: atexit/weakref.finalize cleanup or deliberate-documentation. |
| pages.py | 53 | dead-code | `Page` class never instantiated; find() merely forwards to harness.find_control. Fix: delete Page, keep WindowSpec/WINDOWS. |
| test_console.py | 76 (+~21 files) | premature-abstraction | The 13-field GORBA RideConfig literal repeated ~28× across 21 files; copies drifted (min_lap_s 1 vs 60 vs 1080, seeds). Fix: hoist one shared factory (gorba_config(...)) in tests/conftest.py or store_staging. |
| test_menu_coverage.py | 87 | missing-type-hint | Fixture `frame_with_menubar` lacks a return annotation while 32/33 siblings annotate. Fix: annotate `-> Iterator[tuple[Any, Any]]` (or `-> Any` matching app_frame). |
| docs/FUNCTIONAL-SUITE-INSTABILITY.md | 126 | stale-doc | "uncommitted … needs committing" — committed in 285693b. Fix: restate as committed. **Deferred to Phase 5 doc pass (plan F8/Phase 5).** |
| docs/FUNCTIONAL-SUITE-INSTABILITY.md | 121 (+:21) | stale-doc | PR #42 marked "open" — merged (f252a5a). **Deferred to Phase 5 doc pass.** |
| docs/FUNCTIONAL-SUITE-INSTABILITY.md | 56 | inaccurate-doc | "4-6 segfaults per run" citation points at AGENTS.md/noxfile/commit #31; the figure lives in scripts/run_functional_tests_vm.sh header. **Deferred to Phase 5 doc pass.** |
| CONTRIBUTING.md | 187 | contradicts-code | "one auto-retry" — suite runs `--reruns 2`. Fix: two auto-retries. |
| CONTRIBUTING.md | 26 (+AGENTS.md:142) | inaccurate-doc | Stage-1 list omits `css_drift`, so "green local = green CI" is false. Fix: add css_drift to both lists. |
| README.md | 20 | stale-doc | Status "EPIC 9 of 9 — v1.0.0"; installed version 1.0.10. Fix: update status line. |
| CHANGELOG.md | 7 | stale-doc | No [1.0.10] section; CONTRIBUTING requires one per release. Fix: add [1.0.10] + Unreleased for PRs #41-#44. |
| docs/EPIC8-SESSION-SUMMARY.md | 49 | broken-link | Cites WINDOWS-DEBUG-SESSION-SUMMARY "Addendum 2" — it has none; canonical is EPIC3-SESSION-SUMMARY.md Addendum 2 (:714). Fix: repoint. |
| docs/WINDOWS-DEBUG-SESSION-SUMMARY.md | 49, 70 | broken-link | Bare "(Addendum 2)" refs point at a section the file lacks. Fix: spell out EPIC3-SESSION-SUMMARY.md Addendum 2. |

## Patterns Observed

- Docstring numeric claims drift (row counts, id counts, retry budgets, dialog enumerations) — the single largest class; ruff/mypy cannot see them, only review catches them.
- The CSV import boundary (`csvio.py`) is the one untrusted-input surface and holds all three core-partition security findings.
- wx's handler-exception swallow (the repo's own measured documentation) turns any unguarded write in a handler into a silent false success — the app layer has ~8 unguarded write sites vs the guarded export/backup exemplars.
- Test-infra spawns are bounded nearly everywhere except a handful of macOS tooling and mypy calls.
- `logic-coverage-exempt` comments are used scrupulously, but a few guard constructions exist only to need the exemption — simplification findings remove the tax.

## Statistics

- Files scanned: 218 (16 core + 63 ui/tools + 138 tests + docs/*.md)
- Total findings (deduped): 38
- Clean files: ~180 (83%)
- Most violated category: documentation-accuracy / stale-doc (15 of 38)

## Deferrals (deliberate, per the approved plan)

- `test_corrections.py:109` live-context builder extraction → Phase 3 F3 (shared builder + `harness.release_main_window` together; these are the reference-leak sites).
- `tools/functional_rerun.py` `stalled_file` logic + noxfile.py:127 comment → Phase 3 F4.
- `docs/FUNCTIONAL-SUITE-INSTABILITY.md` stale claims (:56, :121, :126) → Phase 5 doc pass.

## Fixes Applied (Stage 2)

### User Selection

Severity levels selected for fix: **All** (38 findings; user confirmed via approval gate).

### Phase 5: Auto-Fix

Not applicable — the repo gates formatting via ruff (all-rules), which was already green; no formatting-category findings existed.

### Phase 6: TDD Code Fixes

| Status | Count | Notes |
|--------|-------|-------|
| FIXED | 31 | Batch A (core): csvio 3 MEDIUM (PlateShapeError doors + per-row ImportConflict; utf-8-sig BOM fix; UnicodeDecodeError/csv.Error → file-level conflicts), ride/roster/store dead-code removal (exemption-tax deleted), htmlexport/pdfexport UTC comment fixes — 10 new tests, 808 passed. Batch B (ui): rider_editor export guard (on_error seam), app.py store-write guards + status notices (11 new tests in test_app_write_guards.py), excepthook-based crash log (4 new tests in test_app_crash_log.py; measured: wxPython 4.3.1 never dispatches OnExceptionInMainLoop — sys.excepthook is the working hook), riders.py preview guard, settings-write guard (app-side `_save_layout_settings`), stale E6.4.3 comments reworded, commands/menu_state/dialogs counts corrected, ride_library comment renamed — 2854 unit passed. Batch C (tools/tests): wx imports deferred into main() in both repro tools, functional_rerun/noxfile docstrings name `_MAX_RERUNS`, 10 subprocess spawns bounded with named TimeoutExpired failures, gorba_config factory hoisted to tests/conftest.py (4 files converted), menu_coverage fixture annotated, mkdtemp sites registered for atexit cleanup, dead Page class deleted — 337 targeted passed. Docs batch: CONTRIBUTING (retries, css_drift), README status, CHANGELOG [1.0.10]+Unreleased, EPIC8/WINDOWS-DEBUG Addendum-2 link repoints, AGENTS.md css_drift. |
| NEEDS_REVIEW | 2 | (1) `settings.py:117` legacy-syntax finding **refuted by the repo standard**: CODINGSTANDARDS-PYTHON.md explicitly accepts the PEP 758 form, and ruff 0.16.6 with target-version py314 rewrites the parenthesized form back — applying the suggested fix would fail `nox -s lint`. No change made; semantics identical on 3.14. (2) gorba_config factory created but only the 4 in-scope files converted; ~17 remaining literal sites documented as follow-up (full conversion is churn with no behavior change). |
| OUT_OF_SCOPE | 0 | |

**Fix Summary:** Fixed: 31/33 actionable (94%). Needs review: 2 (1 refuted-by-standard, 1 partial-by-design).

### Functional (VM) proof pending before commit

Batch B's wx-facing arms are seam-tested headless but per the approved plan's per-fix gate, these are proven in a targeted Tart-VM run before commit (folded into Phase 3's targeted runs, which cover the same files): export-failure notice (menu route + editor infobar), store-write failure notices through live dialogs, MainLoop exception → status notice + crash log beside settings.json, csv_preview read-failure surface, persist_layout failure during sash/close.

## Re-Validation Results (Phase 7)

| Tool | Status | Details |
|------|--------|---------|
| ruff (lint) | PASS | `nox -s lint` green |
| ruff format | PASS | `nox -s lint` includes format check |
| mypy (strict) | PASS | 61 source files, no issues |
| import-linter | PASS | contracts green |
| ids_drift | PASS | ids.py matches .xrc |
| pytest unit+property+simulations | PASS | 2933 passed, 2 skipped; coverage 98.26% (gate 90) |
| bandit / pip-audit | NOT RUN | not installed in this environment; static equivalents performed by the security reviewers (no secrets, no shell=True, parameterized SQL) |

## Final Status

**Review Status:** NEEDS ATTENTION until the VM proof + Phase 3–5 land (per plan); code gates are all green on the worktree as of 2026-09-07.
**Total Fixed:** 31/33 actionable; 2 NEEDS_REVIEW (1 refuted-by-standard, 1 partial-with-documented-remainder).
