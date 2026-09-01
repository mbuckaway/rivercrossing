# Python Code Review Report — RiverCrossing

_Generated: 2026-09-01 | Project: rivercrossing | Type: basic (wxPython 3.14 desktop app)_

## Executive Summary

The codebase is unusually disciplined: `ruff` select-ALL, `mypy --strict` (src), ≥90% branch+line
coverage, import-linter, and ids/css drift all pass. The full review surfaced 26 findings — 0
CRITICAL, 0 HIGH, 8 MEDIUM, 18 LOW — none of which are security holes or data-corruption bugs. The
fix set is mostly consistency (missing `__all__`, comment capitalization), dead-code removal, two
robustness gaps (unbounded subprocess timeouts, non-atomic export writes), and stale documentation.

## Detection Results

| Attribute | Value |
|---|---|
| Project Type | basic |
| Python Version | 3.14 |
| General/Lambda indicators | none (wxPython/fpdf2/jinja2/phevaluator/platformdirs) |
| Pulumi | no |

## Phase Execution

| Phase | Agent | Status | Findings |
|---|---|---|---|
| Phase 1 — Code Standards | python-standards-reviewer | COMPLETED | 13 |
| Phase 2 — Type Safety | python-standards-reviewer | COMPLETED | 1 |
| Phase 3 — Security | python-security-reviewer | COMPLETED | 2 |
| Phase 4a — General Performance | python-performance-reviewer-general | COMPLETED | 2 |
| Phase 4c — Simplification | python-simplification-reviewer | COMPLETED | 2 |
| Phase 4d — Documentation | python-docs-reviewer | COMPLETED | 6 |

## Findings Summary

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 0 |
| Medium | 8 |
| Low | 18 |
| **Total** | **26** |

## Findings

### Medium

| File | Line | Category | Description |
|---|---|---|---|
| tools/resume_scenario_repro.py | 26 | incorrect-type-hint | `main() -> int` returns a dict |
| tools/gen_css.py | 175 | missing-timeout | `subprocess.run` (Tailwind) has no `timeout=` |
| src/rivercrossing/ride.py | 1492 | quadratic-crossing-replay | `_laps_for` re-scans crossings → O(C²) replay — **OUT-OF-SCOPE** (premature optimization, SIMPLECODE Rule 11; measured 0.14s @5.4K, 0.5s @10K crossings) |
| src/rivercrossing/ui/presenters/settings.py | 258 | dead-code | `SettingsPresenter` never instantiated in production |
| README.md | 20 | stale-doc | Status says "EPIC 1 of 9"; repo is EPIC 9 / v1.0 |
| README.md | 62 | stale-doc | Windows testing step cites retired demo shell |
| AGENTS.md | 120 | contradicts-code | demo seam rule contradicts enforced import-linter contract |
| src/rivercrossing/ui/presenters/detail.py | 26 | contradicts-code | audit-button docstrings stale (E7.3.1 landed) |

### Low

| File | Line | Category | Description |
|---|---|---|---|
| cards.py / hands.py / csvio.py / roster.py / demo.py / htmlexport/__init__.py / data_source.py / library.py / riders.py / selftest.py | — | missing-all | public module lacks `__all__` (standard §"Public vs Internal Interfaces" REQUIRES it) |
| tools/timer_repro.py | 51 | comment-capitalization | comment first word lowercase |
| src/rivercrossing/ui/app.py | 2378 | comment-capitalization | comment first word lowercase |
| src/rivercrossing/ui/presenters/console.py | 45 | comment-capitalization | comment first word lowercase |
| tools/gen_app_icons.py | 181,194,218 | missing-timeout | three `subprocess.run` (rsvg/iconutil/tiffutil) no `timeout=` |
| src/rivercrossing/pdfexport.py | 1162 | non-atomic-export-write | PDF/CSV written directly, not temp+replace |
| src/rivercrossing/ui/presenters/library.py | 26 | dead-code | `LibraryPresenter` empty no-op shell |
| AGENTS.md | 139 | inaccurate-doc | bare `pip` vs `.venv/bin/pip` |
| src/rivercrossing/ui/views/dialogs.py | 4 | stale-doc | dialog counts 21/13 stale vs XRC |

## Patterns Observed

- The project already enforces almost everything the review checks via ruff/mypy/import-linter; the
  findings are the residue those gates can't see (dead code, doc drift, subprocess timeouts).
- Dead presenters (`SettingsPresenter`, `LibraryPresenter`) are vestigial MVP shells — their wiring
  was completed through a different seam (direct `load_settings`/`SettingsDialog`, injected library
  callbacks) and the shells were never removed.
- Docs drifted across the EPIC 8/9 boundary (README Status, demo-seam rule, dialog counts, audit
  wiring) because doc edits were not part of those task briefs.

## Statistics

- **Files scanned**: 75 (58 src incl. excluded generated `ui/ids.py`, 15 tools, 1 installer, noxfile)
- **Total violations**: 26
- **Out-of-scope**: 1 (quadratic-crossing-replay — premature optimization)

## Disposition

- **Actionable**: 25 (all severities, per "fix 100% of any found issue")
- **Out-of-scope (documented, not fixed)**: 1 — `ride.py` quadratic replay; the measured cost is
  sub-second at far-beyond-realistic scale, so the proposed per-entry index is premature
  optimization (SIMPLECODE Rule 11) with risk to the crash-recovery invariant. Flagged for the user;
  can be re-opened if profiling ever shows a real regression.
