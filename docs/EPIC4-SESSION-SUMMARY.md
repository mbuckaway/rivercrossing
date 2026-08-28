# EPIC 4 — Session Summary and Hand-off

**Written:** 2026-08-28 · **Branch:** `topic/epic-4-live-ride` · **Head:** `7b46000` · **Version:** `0.4.0`
**Purpose:** EPIC 4 (Live ride, in-memory) is implemented and merged to the E4 branch; this file
records what shipped, the decisions taken, and what the next machine needs to resume into EPIC 5.

---

## Status at a glance

| Item | State |
|---|---|
| E4.0 `rivercrossing.standings` (pulled forward from E6) | **DONE** — pushed (`d501b5e`) |
| E4.1 `RideEngine` state machine + clock + set-start + stop/continue | **DONE** — pushed (`3cdaf5e`) |
| E4.2 crossings, unknown-plate, min-lap held cards, undo | **DONE** — pushed (`808dead`) |
| E4.3 cap X, `deal_manual`, shoe close on finish, seeded sim replay | **DONE** — pushed (`7922520`) |
| E4.4.1-3 engine console live + `ui/sound.py` + arm/stop/finish | **DONE** — pushed (`09f0a79`, `4668ccc`) |
| E4.4.4 mini acceptance (20-rider race through the real UI) | **DONE** — pushed (`7b46000`) |
| Version bump to 0.4.0 + design write-backs + this handoff | **DONE** |
| Local gates (unit+property+simulation, lint, mypy, import-linter) | **GREEN** — 1668 tests, 98.18% coverage |
| macOS functional (incl. mini acceptance) | **GREEN** on the E4 head |
| Windows functional (stage 3) | **RED — pre-existing, deprioritized** (see §3) |
| PR for the E4 branch | **OPEN** (not yet opened — head `7b46000` against `master`) |

---

## 1 · What shipped

- **`rivercrossing.standings`** (E4.0): `TieBreak` (values = ride.py's `"laps"`/`"total_time"`/
  `"high_card"` spellings, so a stored `tiebreak_order` maps on without importing ride), frozen
  `EntryResult` / `Placed`, `rank(results, order)`, `laps_leaderboard`, `time_leaderboard`.
  Ranking by precomputed `hands.EvaluatedHand`; R-14 tie-break order; R-43 draw-required never
  silently ordered (shared place + `tie_note` "draw required"); DNFs listed last, never displacing
  ACTIVE placings. Import direction: `standings` imports `hands`/`cards` only (no ride/roster/wx).

- **`RideEngine`** in `ride.py` (E4.1–E4.3): DRAFT→RUNNING→FINISHED→REOPENED with guards
  (illegal transitions raise `IllegalStateError`); injected wall clock (`clock: Callable`),
  `elapsed()`/`remaining()` derive from `now − actual_start`; `start(at)` / `set_start_time(at)`
  (lap-1 retro-recompute, audit-logged); stop-as-a-guard (locks entry, ride stays RUNNING) with
  `start()` as continue (`actual_start` unchanged); `record_crossing` (plate→entry via
  `Roster.resolve_plate`, lap + timestamp, card-per-lap from the seeded `Shoe`, reshuffle + audit
  on exhaustion, min-lap flags hold the card, unknown plate → rejected result with
  `reason="unknown_plate"`); `confirm_held`/`void_held`; `undo_last` (lap removed, card
  restituted, audit row); card cap X (laps past cap count, cards past cap dealt but non-scoring);
  `deal_manual(plate, reason)` (audited, E7's dialog seam); `finish()` closes the shoe;
  `snapshot() -> list[EntryResult]`; `events` audit log.

- **Live console** (E4.4.1-3): `EngineDataSource` (implements the `DataSource` Protocol over
  engine+roster; feed cap 30 newest-first, counters, status, standings via
  `standings.rank(snapshot())`); `ConsolePresenter(view, engine, source, *, now=...)` fully wired
  (accepted → feed/flash/RECORDED + clear+refocus; flagged → FLAGGED; rejected → ERROR + keep
  text/focus; `on_undo`, `on_arm_stop` with 10 s auto-clear via tick, `on_stop_confirmed`,
  `on_start`, `on_finish` consulting `FINISH_GATE`, `on_hide_times`, `tick`); `ui/sound.py`
  (`Cue` enum + `SoundPlayer` with a fake-able backend, muted default-on, missing WAV → silent);
  `MainFrame` bindings for start/stop/arm/undo + 1 s tick; `app.py` wires the console to the
  engine source (`_build_console_engine`) while other windows keep `DemoDataSource` until
  E5.4.2.

- **Mini acceptance** (E4.4.4): `tests/functional/test_mini_acceptance.py` — the scripted race
  (20 riders incl. a pooled team, `min_lap_s` lowered, seeded shoe): start, 60 crossings with two
  short-lap flags (held cards confirmed/voided), an undo, stop/continue, finish through the real
  UI; standings asserted headlessly via `standings.rank(engine.snapshot())` against a
  hand-verified fixture. Finish menu route wired (`mi_finish_ride` → `finish_confirm_dlg` →
  `presenter.on_finish`); cancel arm covered.

## 2 · Decisions recorded (write-backs landed)

- **Standings pulled forward from E6** (resolved with the user 2026-08-28): the core ranking module
  ships in E4; the prose hand-name renderer, results-window live wiring, and draggable tie-break UI
  stay E6. Recorded in `project-plan.md` (E4 shipped note + E6.1 note) and `CHANGELOG.md`.
- **Cue names**: `recorded/flagged/error` (matches module-skeletons S4, the WAV filenames, and the
  `Cue` enum). spec §10's `rejected/held` mapping written back (flagged↔held two-tone, error↔
  rejected buzz) — spec.md §10 edited 2026-08-28.
- **Sound module**: `rivercrossing.ui.sound` (not spec §10's "audio") — matches module-skeletons
  and the console's pre-existing comment.
- **`RideEngine.__init__` gains `roster`** (typed under `TYPE_CHECKING`, duck-typed at runtime) to
  avoid the roster⇄ride runtime import cycle; recorded in module-skeletons S4.
- **Mini acceptance placement**: runs as a functional test (stage 3) until CI stage 4 arrives in
  E9; "green on both OSes" verified on macOS + the headless engine/standings assertions, since
  Windows functional is deprioritized (below).
- **`FINISH_GATE`** is a module-level stub returning True; E6.4.3 wires the real evaluator
  self-test.

## 3 · Known reds carried forward (do not re-litigate without the user)

- **Windows stage-3 functional** is RED with a fully root-caused, upstream-unsolved cause: the
  wx/SIP wrapper-cache corruption plus the XRC silent subtree-skip (Class 2 — `ride_setup_dlg`'s
  whole entry-group missing), documented in `EPIC3-SESSION-SUMMARY.md` Addendum 2 and confirmed
  live (`LookupError: ride_setup_dlg has no control named 'solo_radio'`). No in-process repair
  exists. The user deprioritized Windows 2026-08-28 ("we can always run the Mac version"); the
  close-out branch `topic/epic-3-closeout-fixes` (PR #9, merged to master `b4fd1f9`) carries the
  mitigation work (deadlock fix, harness hardening, `functional_rerun.py` fresh-process file
  reruns incl. the timeout path).
- **macOS functional** is green on the E4 head; it sits in a documented noisy 1-16 failure band
  under sustained load (absorbed by `functional_rerun.py`).

## 4 · Resuming — EPIC 5 (Persistence & crash recovery)

Entry gate: E4 exit green. macOS is green; the E4 branch needs a PR opened against `master`
(title e.g. `EPIC 4 — Live ride (in-memory)`, body citing R-30…36/40/13/16 and the window ids),
then merge, then branch `topic/epic-5-persistence` from `master`.

- **Brief**: `design/epic-prompts/EPIC-5-persistence-crash-recovery.md`; task list under
  `design/docs-md/task-briefs.md` E5 block. **Entry:** E4 exit criteria green.
- **E5 highlights**: SQLite event store with replay (RideEngine already returns `Event` objects —
  every mutation appends one, designed for this); the **wx⇄asyncio integration** choice (wxasync
  ruled out, spec §10 — E5 chooses the mechanism where the async writer first appears); mock-first
  per plan §2 for **Duplicate Ride…** and **Reopen Ride** (the two §15 routes with no frozen
  window — register names in §15b before any UI code); `ride_library_dlg` live; E5.4.2 removes
  `DemoDataSource` from the app path (lint proves demo imports = tests only).
- **Test seams E4 left for E5**: `EngineDataSource` is currently the console's source — E5's
  store-backed source replaces it; `Event` payloads are JSON-ready (datetimes ISO strings);
  `Shoe.replay(config, seed, deals, cycles)` reconstructs shoe state; the roster `AuditEvent`s and
  engine `Event`s are what the store persists.
- **Known gotchas for the next machine**: the wx/SIP wrapper-cache hazard (keep functional tests
  to fresh processes; harness `find_control` now retries 25× with isinstance); macOS functional
  runs via the Tart VM script; the `-n auto --dist loadfile` functional command goes through
  `tools/functional_rerun.py`; a timed-out pass (124) now re-runs failed + stalled files fresh
  rather than the whole suite.

## 5 · Numbers

- Unit + property + simulations: **1668 passed**, coverage **98.18%** (≥ 90% line + branch gate).
- Functional (macOS, this head): full suite green via `functional_rerun.py` (one absorbed
  harness-self-test flake); mini acceptance 9 tests green; menu-coverage walk 54 green.
- Core modules at 100% branch coverage in their changed scope: `ride.py`, `roster.py`,
  `standings.py`, `data_source.py`, `console.py`, `sound.py`.
