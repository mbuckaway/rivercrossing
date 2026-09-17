# Python Review Findings — full-codebase review (post Standings/time-columns fixes)

_Generated: 2026-09-17 | Project: rivercrossing | Type: basic | Scope: whole repo (src + tests + tools + docs)_

## Executive Summary

Ran the full `python-review` protocol (Phases 1–4) over the whole tree with four read-only agents.
31 findings: **0 CRITICAL, 0 HIGH, 7 MEDIUM, 24 LOW**. All 31 were fixed (no review memory existed,
so no learned rules applied). `ruff` (`select = ["ALL"]`) and `mypy --strict` were already clean, so
the findings are correctness / observability / simplification refinements, not defects.

## Detection Results

| Attribute | Value |
|-----------|-------|
| Project Type | basic (no requirements.txt / Pulumi.yaml; wxPython desktop app) |
| Python Version | 3.14 |
| Agents | python-standards-reviewer (P1/P2), python-security-reviewer (P3), python-simplification-reviewer (P4c), python-docs-reviewer (P4d) |

## Findings Summary

| Severity | Count | Fixed |
|----------|-------|-------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 7 | 7 |
| Low | 24 | 24 |
| **Total** | **31** | **31** |

## Findings & Fixes — Medium (7)

| File:Line | Category | Fix |
|-----------|----------|-----|
| `store/__init__.py:1336` | input-validation / error-contract | Replay wraps `engine.apply(json.loads(...))` in `except (JSONDecodeError, KeyError, TypeError, ValueError)` → `StoreError` naming ride + audit row; `load_engine` `Raises:` documents it. 4 tests. |
| `ui/views/main_frame.py:1792` | speculative-generality | Dropped the never-read `planned_start` / `entry_mode` params from `show_ride_header`; call site + 4 test fakes updated. |
| `ui/views/main_frame.py:678` | long-function | Extracted `MainFrame.__init__` (277 lines) into 9 cohesive private steps; behaviour unchanged (source pins re-pointed). |
| `ui/app.py:3975` | long-function | Folded the 9-branch `_make_route_handler` literal chain into the existing target tables; 129→39 lines. |
| `ui/app.py:3511` | stale-comment | Rewrote the miss-Plate-prompt comment to the current commit-then-close flow. |
| `design/docs-md/xrc-windows.md:562` | stale-doc | Miss-mode Edit description corrected to `assign_plate_to_miss`. |
| `docs/user-guide.html:191` | inaccurate-instruction | Removed the non-existent dialog export buttons; "Generate HTML…" → "Export HTML…". |

## Findings & Fixes — Low (24)

| File:Line | Category | Fix |
|-----------|----------|-----|
| `tests/unit/ui/test_app_exports.py:454` | comment-convention | Capitalized the standalone comment. |
| `src/.../*.py` (repo-wide) | line-length | 163 of 183 over-99 lines reflowed (noqa explanations moved above the code line); 20 residual documented (base64 literals, `type: ignore`, code+directive > 99). |
| `tools/toolkit_probe.py:272` | missing-docstring | Docstrings added to the 4 `FeedModel` methods. |
| `store/__init__.py:1313` | input-validation | Blind `cast` replaced with real `tiebreak_order` validation (length 3 + known spellings → `StoreError`). 8 tests. |
| `ui/app.py:1402` | error-handling | Library Open guarded like the resume path (ride-named notice + alert + marker clear). 6 tests. |
| `ui/app.py:372` | silent-failure / observability | Failed XRC loads drained into the launch `Logging` (zero-arg loader contract preserved). 3 tests. |
| `ui/views/_support.py:333,403,428` | silent-failure / observability | Always-on `XRC_WARN` seam (app log, else stdlib) beside every `wx.LogWarning`. 5 tests. |
| `ui/presenters/settings.py:209` | silent-failure | `load_settings` fallback recorded through an injectable `WARN` seam. 4 tests. |
| `ui/presenters/data_source.py:379` | silent-failure | Malformed audit timestamp recorded at WARNING; blank cell kept. 2 tests. |
| `store/__init__.py:1397` | long-function | Extracted `_copy_roster_rows`. |
| `ride.py:2715` | long-function | Added `_payload_int`; used at 5 sites. |
| `ui/app.py:4428` | long-function | Extracted `_load_window_parts` from `build_main_window`. |
| `ui/presenters/riders.py:236` | dead-code | Deleted the test-only `_rider_rows`; test now exercises the production composition. |
| `ui/std_dialogs.py:280` | speculative-generality | Dropped the unused `default_cancel` / `icon` params from `show_three_choice`. |
| `htmlexport/__init__.py:439` | premature-optimization | Removed the unjustified `@lru_cache`. |
| `ui/app.py:4618` | middle-man | `_save_layout` closure replaced with `functools.partial`. |
| `ui/views/dialogs.py:428` | noise-comment | Comments that repeated the docstring trimmed. |
| `docs/user-guide.html:194` | terminology-drift | "results dialog" → "Standings dialog". |
| `docs/user-guide.html:197` | stale-doc | Publish options correctly located on the Results menu, not in the dialog. |
| `CHANGELOG.md:16` | inaccurate-changelog | Corrected the Cancel/OK contract wording. |
| `tools/check_asset_manifest.py:38` | stale-doc | "five" → "six" template artifacts. |
| `design/docs-md/project-plan.md:190` | stale-doc | "results window" → "Standings window"; dropped the retired export-buttons clause. |

## Fixes Applied

- **Severity selection:** all severities (the user's explicit instruction overrides the skill's
  approval gate).
- **Phase 5 auto-fix:** n/a — the repo is ruff-only, and ruff was already clean.
- **Phase 6 fixes:** `tdd-python-writer` (tests first) across non-overlapping batches, plus a
  dedicated mechanical line-length reflow pass.
- **Fix rate:** 31/31 (0 NEEDS_REVIEW, 0 OUT_OF_SCOPE).

## Re-Validation (Phase 7)

| Tool | Status | Details |
|------|--------|---------|
| ruff check `.` | PASS | All checks passed (`select = ALL`) |
| ruff format `--check` | PASS | 193 files already formatted |
| mypy | PASS | Success: no issues found in 65 source files |
| import-linter | PASS | 1 contract kept, 0 broken |
| ids drift | PASS | ids.py matches 229 names |
| asset manifest | PASS | all required assets present |
| pytest | PASS | **6044 passed, 1 skipped**, coverage 99.87% (≥ 90% gate) |
| nox bundle | PASS | onedir + .app build |

## Final Status

**Review Status:** PRODUCTION READY
**Total Fixed:** 31/31 (100%)
**Remaining:** 0 findings (20 residual over-99-char comment lines documented as non-relocatable
without code changes).
