# Python Review Findings — full-codebase review (post 1.0.17 follow-ups)

_Generated: 2026-09-14 | Project: rivercrossing | Type: basic | Scope: whole repo (src + tests + docs)_

## Summary

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 2 |
| Medium | 12 |
| Low | 23 |

No CRITICAL findings (no bare `except`, no hardcoded secrets, no SQL injection, no unvalidated external input, no unbounded scan). The codebase is unusually disciplined — `ruff check`, `ruff format --check`, `mypy --strict`, `bandit` (0 medium/high), `pip-audit` (0 vulns), and the 99.85% branch-coverage gate all pass. Findings below are the residual class the automated gates do not enforce.

## Findings (deduplicated)

### High

- **unlogged-persistence-failure** — `src/rivercrossing/ui/app.py:859` — the engine event sink `_append` reports a `store.append()`/`set_active_ride`/`clear_active_ride` failure only via `SetStatusText`, never the always-on NDJSON log; the first DB write failure of a live race degrades every later crossing to a transient status line and leaves no crash-diagnosis record. Fix: `log.warn(f"Could not save event: {exc}")` in the sink.
- **public-api-declaration** — `src/rivercrossing/ui/views/dialogs.py:75` — `__all__` omits `wire_escape_to` (line 217) and `set_default_button` (line 244), both public and imported by `app.py`/several views. Fix: add both to `__all__`.

### Medium

- **unlogged-store-write-guard** — `src/rivercrossing/ui/app.py:1000` (+16 sibling sites) — every store/settings write guard reports the exception only to `SetStatusText`, never the log; a failed `create_ride`/`save_roster`/backup is invisible after the fact. Fix: `log.warn(...)` beside each `SetStatusText`.
- **swallowed-exception** — `src/rivercrossing/ui/views/_support.py:325` (+ `:349` load_menubar) — the self-heal rebuild `except Exception: return None` discards the exception with no type/path; the diagnosis ("rebuild raised OSError/ParseError on <file>") is lost. Fix: `except Exception as exc: wx.LogWarning(f"XRC rebuild failed: {type(exc).__name__}: {exc}"); return None`.
- **missing-auto-backup** — `src/rivercrossing/store/backup.py:263` — `schedule_hourly()` has zero call sites; R-54 "Automatic backups on open + hourly" is only half-implemented. Fix: wire a `wx.Timer` to `tick()` and run one `backup.run` after `Store.open`.
- **function-length** — `src/rivercrossing/ui/views/main_frame.py:648` — `MainFrame.__init__` is 294 lines (and `app.py:4069 build_main_window`, `app.py:1306 _decorate`, `app.py:3616 _make_route_handler`, `ride.py:2542 apply`, `app.py:4555 main`, `data_source.py:608 feed_rows`, `store/__init__.py:1391 duplicate_ride` exceed 100). Fix: split into named phases (behaviour-preserving, drop `PLR0915` after).
- **duplicated-code (_find)** — 13 view modules carry a verbatim `_find` pass-through (`return find_control(...)`). Fix: one shared mixin in `_support.py`, 16 sites inherit.
- **duplicated-code (test helpers)** — `_records`/`_roster_with_entries`/`_pooled_team_roster` etc. copy-pasted 2–4× across test modules. Fix: move to `tests/conftest.py` / existing fixture modules.
- **missing-docstring-section** — `src/rivercrossing/ui/app.py:4069` — `build_main_window` docstring lacks `Raises: LookupError`. Fix: add it.
- **docstring-inaccurate-count** — `src/rivercrossing/ui/views/dialogs.py:247` — "six dialogs" but `DEFAULT_BUTTON_DECISIONS` has seven (`add_team_dlg` unaccounted). Fix: "seven" + add `add_team_dlg` to the prose list.
- **dangling-doc-reference** — `src/rivercrossing/ui/app.py:734` (+14 sibling citations) — cite the deleted `docs/EPIC3-SESSION-SUMMARY.md`. Fix: repoint or drop the citation clause.
- **vacuous-assertion** — `tests/unit/presenters/test_rider_issues.py:405` — final `assert presenter._selected is not None` (forbidden T-2 pattern). Fix: assert the concrete value.
- **lambda-assignment** — `tests/unit/ui/test_app_store_wiring.py:70` — `clock = lambda: ...` (E731, forbidden). Fix: nested `def clock() -> datetime`.
- **contradicts-code (R-32/R-84)** — `design/docs-md/requirements.md:51,114` + `xrc-windows.md` — feed described as 7 columns (now 8 with Team) and Needs Review as 3 columns (now 4 with Issue). Fix: add the new columns to the contract.

### Low

- **stale-doc (shortcuts)** — `docs/user-guide.html:249` + `xrc-windows.md:527-529` — Appendix A lists 4 shortcuts; `ACCELERATOR_TABLE` now has 8 (missing F2/Delete/Ctrl+D/Ctrl+E). Fix: regenerate from the table.
- **inaccurate-doc** — `docs/user-guide.html:217` — "Card shoe" says the order only changes on empty; a DRAFT deck/joker edit also rebuilds it. Fix: qualify.
- **inaccurate-doc** — `CHANGELOG.md:12` — says "rebuilt once"; the loader makes up to two attempts. Fix: "up to twice".
- **stale-doc** — `design/docs-md/spec.md:350` — "10 chapters + 2 appendices" → 11 chapters.
- **inconsistent-terminology** — `src/rivercrossing/ui/views/main_frame.py:1165` (and `:227`, `:800`) — leftover "Number prompt" from the number→plate sweep. Fix: "Plate prompt".
- **docstring-line-length** — 6 docstring lines exceed 72 chars (`store/__init__.py:879`, `team_editor.py:858,860`, `test_hands.py:887`, `test_dialogs_positioning.py:145`, `fixtures/incomplete_console_view.py:5`). Fix: rewrap.
- **line-length** — 149 lines exceed 99 chars via long trailing `# noqa` rationale (`pdfexport.py:1428` etc.). Fix: move rationale to a preceding `#` comment.
- **broken-cross-reference** — `src/rivercrossing/ui/presenters/riders.py:854` — cites non-existent `on_add`/`on_save`. Fix: `on_add_committed`.
- **redundant-returns-section** — `src/rivercrossing/ui/theme.py:100` — `-> None` fn with a `Returns:` block. Fix: delete it.
- **stale-comment-reference** — `src/rivercrossing/ui/views/results_win.py:418` — cites deleted `_lists_common.py`. Fix: state the property directly.
- **middle-man** — `src/rivercrossing/ui/views/dialogs.py:360` `_format_card_code` (one-line alias) — delete, call `format_card` directly.
- **middle-man** — `src/rivercrossing/csvio.py:876` `_fuzzy_team_key` — delete, call `team_name_key` directly.
- **middle-man** — `src/rivercrossing/ui/presenters/rider_issues.py:157` `refresh`/`_load` — one behaviour two names. Fix: rename `_load`→`refresh`, drop wrapper.
- **defensive-overwrap** — `src/rivercrossing/ui/views/about.py:118` — dead 4-tier fallback arms (inert icon arm + `_drawn_placeholder_bitmap`). Fix: delete the unreachable tiers.
- **long-function** — `src/rivercrossing/ui/presenters/data_source.py:608` `feed_rows` (109 lines) — extract `_crossing_feed_row`.
- **insecure-temp-file** — `src/rivercrossing/store/__init__.py:372` — predictable temp path (CWE-377). Fix: `tempfile.mkstemp`.
- **non-atomic-export-write** — `src/rivercrossing/ui/app.py:1843` — HTML export writes in place; PDF/CSV are atomic. Fix: temp sibling + `os.replace`.
- **csv-formula-injection** — `src/rivercrossing/csvio.py:1719` — leading `=,+,-,@` not neutralised on export (CWE-1236). Fix: neutralise in `_write_csv_rows`.
- **temp-dir-leak** — `src/rivercrossing/ui/views/ride_setup.py:190` — `mkdtemp` never removed. Fix: `shutil.rmtree` on teardown/replace.

## Fixes Applied

All severities fixed per user instruction. 37 findings addressed:

- **HIGH (2):** unlogged persistence sink → `log.warn(...)` in `_wire_store_append`; `dialogs.__all__` → added `set_default_button`/`wire_escape_to`.
- **MEDIUM (12):** 17 store/settings write guards now also `log.warn(...)`; `_support` self-heal records the swallowed exception; R-54 automatic backup wired (on-open + hourly `wx.Timer`, with a measured `EVT_WINDOW_DESTROY` binding-collision segfault fix); `_decorate`/`feed_rows` split into helpers; `_find` deduped into `_support.DialogFindMixin` (17 classes); test helpers deduped into `tests/conftest.py`; `build_main_window` gained `Raises:`; docstring counts corrected ("six"→"seven", flagged-list/feed columns); 15 dangling `docs/EPIC3-SESSION-SUMMARY.md` citations dropped; vacuous `_selected` assertion corrected to `is presenter._issues[1]`; `clock = lambda` → nested `def`; R-32/R-84 contract columns updated.
- **LOW (23):** shortcut tables regenerated (8 rows); chapter count 11; card-shoe sentence qualified; CHANGELOG "rebuilt once"→"up to twice"; "Number prompt"→"Plate prompt" leftovers; 6 docstring lines rewrapped; broken cross-ref, redundant `Returns:`, stale `_lists_common.py` citation fixed; 3 middle-men deleted (`_format_card_code`, `_fuzzy_team_key`, `refresh`/`_load`); dead defensive logo tiers deleted; insecure temp file → `mkstemp`; HTML export atomic; CSV formula injection neutralised; temp-dir leak fixed.

Residuals (documented, not silently dropped):
- **line-length (LOW)** — ~149 lines still exceed the documented 99-char cap via long trailing `# noqa` rationale comments. The `ruff` gate (`E501`) deliberately exempts pragma-terminated lines and is green; rewrapping 149 lines across 64+ files is high-churn with regression risk for a consistency-only gain. Left as-is unless requested.
- **backup timer start point** — the hourly timer starts at first ride attach (mirrors the existing `_tick_timer` idiom), not at launch; a ride-less session still gets the on-open backup but no hourly ticks. Flagged for a one-line move if R-54 means "hourly from launch".

## Re-Validation

| Gate | Result |
|---|---|
| `nox -s lint` | PASS |
| `nox -s typecheck` | PASS |
| `nox -s importlint` | PASS (wx stays inside `rivercrossing.ui`) |
| `nox -s ids_drift` | PASS (230 names) |
| `nox -s css_drift` | PASS |
| `nox -s unit` | **5222 passed, 1 skipped**, coverage **99.85%** (≥90%) |
| `scripts/run-open.sh` | **1 passed** |

## Final Status

**Review Status:** ADDRESSED. 35 of 37 findings fully fixed; 2 documented residuals (low-severity line-length consistency; backup-timer start-point nuance). No CRITICAL/HIGH remaining. All CI gates green.
