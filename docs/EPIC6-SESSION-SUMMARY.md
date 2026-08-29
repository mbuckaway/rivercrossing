# EPIC 6 — Session Summary and Hand-off

**Written:** 2026-08-28 · **Branch:** `topic/epic-6-results-publishing` · **Version:** `0.6.0`
**Purpose:** EPIC 6 (Results & publishing) is implemented; this file records what shipped,
the decisions taken, and what the next machine needs to resume into EPIC 7.

---

## Status at a glance

| Item | State |
|---|---|
| E6.1.1 hand-name prose + tie-break conversion | **DONE** — pushed |
| E6.1.2 leaderboard negative guard | **DONE** — pushed (tests-only pin) |
| §7 roster-CSV finished-ride columns (approved addition) | **DONE** — pushed |
| E6.2.1 vendored CSS build step (+ templates + fonts + gate) | **DONE** — pushed |
| E6.2.2 htmlexport.render + frozen goldens | **DONE** — pushed |
| E6.2.3 no-times variant | **DONE** — covered within E6.2.2 (no separate commit) |
| E6.3.1 PDF report | **DONE** — pushed |
| E6.3.2 podium poster | **DONE** — pushed |
| E6.4.1 results window live | **DONE** — pushed |
| E6.4.2 Results menu + standings CSV | **DONE** — pushed |
| E6.4.3 finish gate | **DONE** — pushed |
| Version 0.6.0 + CHANGELOG + design write-backs | **DONE** |
| Headless gates (unit+property+simulations, lint, mypy, import-linter, ids drift, css drift) | **GREEN** — 2145 tests, 98.49% coverage (line+branch ≥ 90) |
| Functional suite in the Tart VM | **PENDING** — new results-window/export-walk tests written and collected (908 collected) but the VM run happens on a machine with the Tart VM (see §5) |
| PR | **#12 — EPIC 6 — Results & publishing** (single PR for the whole epic, per product decision) |

---

## 1 · What shipped

- **`rivercrossing.standings`** — `hand_name(hand) -> str` (title-case em-dash prose, one style
  for window + exports — D1; raises `ValueError` on an empty hand), `tiebreak_order_from_spellings`
  (ride spellings `"laps"/"total_time"/"high_card"` ⇄ `TieBreak` members; unknown spelling raises),
  E6.1.2's negative guard pinned (`(-laps, total_time)` order, never time alone).
- **`rivercrossing.csvio`** — `export(ride, path, *, placed=None)` now appends spec §7's
  `laps, cards, best_hand, total_time` columns for a FINISHED ride (approved addition; pooled
  rides repeat the entry's stats on every rider row; raw numeric seconds — machine-readable);
  `export_standings(placed, path, *, show_times=False)` ships the §15 standings CSV (D3).
- **`rivercrossing.htmlexport`** — the frozen templates ship verbatim
  (`src/rivercrossing/htmlexport/templates/`); `tools/gen_css.py` vendors Tailwind v4 output
  (`compiled_css`, `--minify`, pinned `@tailwindcss/cli@4.3.3` via committed package.json/lock)
  and base64 Barlow/Barlow Condensed latin subsets (`fonts_css`) with a provenance-checksum
  header; `css_drift` (`gen_css.py --check` = regenerate-to-temp + byte-compare) gates CI and
  is the TB-7 staleness gate; `render(ride, placed, opts, *, logo_src, generated, logo_path)`
  (PackageLoader, autoescape, StrictUndefined, `racejson` escaping every `</` → `<\/`) derives
  the laps/time boards from placed when the options ask and falls back to a transparent 1×1
  data URI for a logo-less ride; `tools/gen_htmlexport_goldens.py` froze the two golden pages
  (times + no-times) after value-parity vs `design/exports/*` (TB-5); the no-times variant
  omits time markup AND JSON (R-63, title suffix "(no times)").
- **`rivercrossing.pdfexport`** — `render(ride, placed, opts, path, *, letter, created_at,
  logo_path)` multi-section report (cover/podium/top-ten/boards/full-field, DNF marked,
  all-cards sub-rows) and `podium_poster(...)` one-page [5d] poster; byte-deterministic across
  OSes via a timezone-aware-UTC creation date (D14 — a naive stamp bakes the local offset into
  `/CreationDate`), embedded OFL fonts (Barlow Regular, Barlow Condensed SemiBold, DejaVu Sans
  for `♠♥♦♣★`), organizer logo drawn top-right when given; both goldens frozen
  (`tests/unit/fixtures/pdfexport/`).
- **Results window live (E6.4.1)** — `ResultsWindow` implements the full `ResultsView`:
  real placed rows from the console's live `EngineDataSource`, publish checkboxes ⇄
  `ExportOptions` (show-times also hides the Total column — `SetHidden`), tie-break re-rank
  through the control's **native** ▲▼ arrows (seeded from the ride's stored order as plain
  labels; invalid row sets restore the known-good order + notice — D13), ⚠ badge for
  draw-required rows inside the Place cell (the canvas pins seven columns), and the hidden
  `stale_infobar` (code-side `wx.InfoBar`, `SetName`, `Insert` at sizer slot 0) waiting for
  E7.3.2. `DataSource.standings(order=DEFAULT_TIEBREAK_ORDER)` on the Protocol (Engine
  forwards, Empty/Demo accept the defaulted param).
- **Results menu (E6.4.2)** — `export_html`/`export_pdf`/`export_poster`/`export_results_csv`
  write real files off-loop (background thread + `wx.CallAfter`, R-02) with an injectable
  save-path seam; `preview_in_browser` opens the last export (injectable opener);
  `focus_tiebreak_control` opens Results and focuses `tiebreak_list`; the four window export
  buttons post the same `mi_export_*` events; `export_exists` derives from
  `context.last_export_path`. FINISHED gating stays in `commands.py`'s `is_route_enabled`.
- **Finish gate (E6.4.3)** — `FINISH_GATE` runs `hands.self_test()` fresh and returns
  `report.passed`; a red suite blocks finishing with the existing notice (R-44's blocking half).
- **Tooling/wiring** — `nox -s gen_css`/`css_drift`; `scripts/run_lint.sh` runs `css_drift`;
  CI static job gained `python tools/gen_css.py --check` (+ `npm ci` for the pinned CLI);
  package-data ships the 5 template files + 3 PDF TTFs; `check_asset_manifest.py` gained the
  templates + pdf-fonts blocks wired into the PyInstaller spec; import-linter's wx-forbidden
  list now names `rivercrossing.pdfexport`.

## 2 · Decisions recorded (write-backs landed)

- **D1** hand-name prose = title-case em-dash everywhere ("Four of a Kind — Nines");
  xrc-windows.md §D sample rows updated to the frozen form.
- **D2/D3** spec §7 columns in `csvio.export` (approved addition) and the §15 standings CSV in
  `csvio.export_standings` — all CSV I/O in csvio (module-skeletons dep line 6 now
  `csvio ─→ roster, standings`).
- **D13** tie-break reorder uses the `wx.adv.EditableListBox` native arrows (no custom buttons —
  `MoveCurrentUp/Down` do not exist on this baseline); New/Edit/Delete buttons stay (nothing
  suppresses them, ride_setup precedent) and the presenter validates + restores.
- **D14** PDF determinism: aware-UTC `set_creation_date` (naive stamps rejected with
  `ValueError`); embedded OFL TTFs; `add_font` without the deprecated `uni=` kwarg and with
  absolute package paths.
- **D15/D16/D19** `render(ride, placed, opts)` per skeleton + `_render_payload` seam for the
  golden tests; `standings(order=)` on the DataSource Protocol (three implementations);
  Tailwind pinned via committed manifest only (`npx` one-shots cannot resolve `tailwindcss`).
- **One PR per EPIC** — product decision (2026-08-28): the task-briefs "one task per PR" rule
  is overridden for EPIC 6; all twelve task commits land in PR #12 in test-first order.
  Recorded in project-plan.md's E6 write-back + task-briefs TB-9 + this file.
- **Tailwind scan isolation** — `gen_css` builds in a scratch dir with `--cwd`, so the
  committed artifacts never feed back into the content scan (measured: without it,
  regeneration can never byte-match).

## 3 · Known reds / flakes carried forward (do not re-litigate without the user)

- **Windows stage-3 functional**: red from the confirmed upstream wx/SIP + XRC degradation
  (EPIC3-SESSION-SUMMARY Addendum 2); deprioritized by product decision 2026-08-28 — macOS is
  the working gate.
- **`test_resume_dlg.py::test_resume_open_library_opens_ride_library_dlg`**: pre-existing
  subprocess-scenario modal-dismissal hang (its scenario docstring names the hang). Bounded
  root-cause attempt: the scenario drives `library_btn` on the modal `resume_dlg` through the
  scenario runner while the parent modal is dismissing — the same class of modal churn that
  flakes other modal-walk tests. **Quarantine decision:** keep it carried (rerun wrapper +
  screenshot artifacts already in place); root-cause it properly in E7 if it keeps tripping.
- **`test_mini_acceptance.py::test_mini_acceptance_finish_confirm_cancel_leaves_ride_running`**:
  intermittent native segfault under wx churn (passes on some workers).
- **Testing discipline (HARD)**: on macOS, functional tests run ONLY in the Tart VM via
  `scripts/run_functional_tests_vm.sh` — never `pytest tests/functional/...` on the host.

## 4 · Resuming — EPIC 7 (Corrections & audit)

Entry gate: E5 exit (stale-flag needs E6.4.1 — now shipped). Branch off master after EPIC 6
merges; `docs/EPIC7-SESSION-SUMMARY` does not exist yet.

- **Brief**: `design/epic-prompts/EPIC-7-corrections-audit.md`; task list under
  `design/docs-md/task-briefs.md` E7 block.
- **E7 highlights**: `stale_infobar` is ready (E6.4.1 — `ResultsWindow.set_stale` is
  implemented and hidden; E7.3.2 triggers it); the held-card/manual-deal engine paths from
  E4.3.2 feed E7.2.1's dialogs; the Void Card… confirm is the third mock-first window
  (registered in §15b before wiring); E7.2.2's "Finish again" re-rank reuses
  `standings.rank` + the E6.4.1 reorder machinery; audit-dialog filters (R-38) are new UI.
- **Gotchas**: `hand_name` raises `ValueError` on a 0-card hand — any 0-lap entry rendered
  anywhere must use the blank-hand guard the exporters already apply; the tie-break
  label map lives in the results presenter (duplicated from ride_setup — refactor when a
  third user appears); the runtime menu-enablement binder (E1.4.2) currently applies only via
  `is_route_enabled` unit tests — the live binder was NOT built in E6 (out of scope) and
  `RideState.export_exists` is set on the route context, ready for it.

## 5 · Numbers

- Unit + property + simulations: **2145 passed**, coverage **98.49%** (line+branch ≥ 90 gate).
- Functional (Tart VM): suite collects **908 tests** (was 903; +5 new export-walk/results
  tests). The VM run itself is PENDING — the authoring machine has no Tart VM; run
  `scripts/run_functional_tests_vm.sh` on a VM-capable host before merging PR #12, or rely on
  CI's functional stage.
- Goldens: HTML times 210,206 B / no-times 204,891 B; PDF report 48,572 B / poster 29,088 B —
  all byte-frozen with regenerate-matches tests and generator `--check` gates.
