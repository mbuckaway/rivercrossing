# Unit-Test Coverage & Logic-Coverage-Score Audit

**Date:** 2026-09-07 · **Branch:** `topic/fix-functional-suite-flakiness` · **Gate:** T-14 hard gate (90% line+branch, enforced) + T-1 LCS ≥85% (advisory target, per user direction treated as a requirement)

Two metrics per `CODINGSTANDARDS-PYTHON.md` §Testing Requirements:
- **Code coverage** — `--cov-branch --cov-fail-under=90` (T-14): 90% line AND branch; core modules (`cards hands standings ride roster store csvio htmlexport pdfexport`) at 90/90 per R-71.
- **Logic coverage** — LCS = 0.50 × branch% + 0.50 × rule_score (T-2..T-13 attestation over the module's AST decision atoms), target ≥85%, LOGIC COVERAGE REPORT block per module.

## Result — every measured module passes both meters

Full run: `pytest tests/unit tests/property tests/simulations -m "not functional"` → **3038 passed, 2 skipped** (skips: pinned Tailwind CLI absent, Windows-only `.cmd` shims); aggregate coverage 98.3% line (gate 90).

| Module | line% | branch% | LCS | Notes |
|---|---|---|---|---|
| cards | 100 | 100 | 100 | `Card.parse` joker/natural arms closed this audit |
| hands | 100 | 100 | 100 | |
| standings | 100 | 100 | 100 | |
| ride | 99 | 100 | 96+ | voided-card reassign + `_crossing_from` loop arcs closed |
| roster | 100 | 100 | 100 | |
| rider_issues | 100 | 100 | 100 | |
| demo | 100 | 100 | 97+ | `results_stale` nullable-watermark arm closed |
| csvio | 100 | 100 | 100 | |
| pdfexport | 99 | 97.1 | 99.5 | 1 T-15-exempted deflate arc (zlib-ng divergence pass) |
| htmlexport | 100 | 100 | 100 | |
| store | 99.5 | 98.1 | 99.1 | `_require_rowid` None-arm T-15-exempt (would need sqlite3 mocking) |
| store/backup | 100 | 100 | 100 | |
| store/migrations | 100 | 100 | 100 | |
| store/schema | 100 | 100 | 100 | |
| ui/cards_imagelist | 100 | 100 | 100 | was 75/80 |
| ui/commands | 100 | 100 | 100 | |
| ui/help | 100 | 100 | 100 | |
| ui/accelerators | 100 | N/A | N/A | constant table, no decision atoms |
| ui/feed_model | 100 | N/A | 100 | |
| ui/menu_state | 100 | 100 | 100 | was 80/83.3 |
| ui/quit_flow | 100 | 100 | 100 | |
| ui/resume_flow | 100 | 100 | 100 | |
| ui/sound | 100 | 100 | 100 | was 86/66.7 |
| ui/theme | 100 | 100 | 100 | was 84/80 |
| ui/zoom | 100 | 100 | 100 | was 58/0 |
| presenters/audit | 100 | 100 | 100 | |
| presenters/console | 98 | 100 | 91.7 | 1 T-15-exempt arm; T-8 comment added |
| presenters/data_source | 100 | 100 | 93+ | dedicated `test_data_source.py` created |
| presenters/detail | 99 | 97.5 | 90.4 | remaining atom T-15-exempt in source |
| presenters/library | 100 | N/A | N/A | Protocol-only |
| presenters/riders | 100 | 100 | 100 | |
| presenters/teams | 100 | 100 | 100 | |
| presenters/setup | 100 | 100 | 100 | |
| presenters/settings | 100 | 100 | 100 | |
| presenters/results | 100 | 100 | 100 | |
| presenters/rider_issues | 100 | 100 | 100 | was 91.7 branch |
| presenters/selftest | 100 | N/A | 100 | |

Omitted by config (not gated): `ui/app.py`, `ui/views/*`, `ui/ids.py` (generated), `__main__.py`.

## Gap-closure work performed (TDD)

- **Six sub-90 modules** brought to ≥90/90 (store 95→99.5 / 88.9→98.1; cards_imagelist 75→100 / 80→100; menu_state 80→100 / 83→100; sound 86→100 / 67→100; theme 84→100 / 80→100; zoom 58→100 / 0→100): 26 tests added across `test_store.py` (+5), new `test_cards_imagelist_wx.py` (+7; unique basename — a same-basename sibling under `tests/unit/ui/` would abort collection), `test_menu_state.py` (+1), `test_sound.py` (+2), `test_theme.py` (+5), `test_zoom.py` (+6 incl. one `@given`).
- **Advisory LCS gaps closed** (cheapest one-or-two per module per T-1): cards (+2), demo (+1), ride (+2), presenters/detail (+1; remaining atom T-15-exempt in source), presenters/rider_issues (+2), new `presenters/test_data_source.py` (+4), console T-8 exemption comment (+1); pdfexport +2 characterization tests and one T-15 exemption comment.
- **No production behavior changed** anywhere in this audit; every new test is a characterization test over existing behavior (the audit's purpose was measurement and coverage, not feature work). No test exposed a production defect.

## Verification

`nox -s lint typecheck importlint ids_drift unit` green at the audit's end (3038 passed; ruff ALL, mypy strict, import-linter, ids-drift all clean). Coverage JSON written to `coverage.json`.
