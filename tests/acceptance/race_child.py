# SPDX-License-Identifier: GPL-3.0-only
"""Child program for the E9.2.1 full-race acceptance test (R-74).

The parent test (``tests/acceptance/test_full_race.py``) spawns this
program in a fresh interpreter per phase of the multi-process seam:
child A records crossings and is then SIGKILLed (the crash leg); child
B resumes, records, reads the resumed clock, and quits through the
real File ▸ Exit flow; child C resumes and verifies everything
survived. Each child opens the *shared* rides database named by the
``RIVERCROSSING_RACE_DB`` environment variable -- never argv, so
``scenario_runner``'s fixed-argv contract (``<script> <scenario>``)
holds -- and prints exactly one JSON line to stdout::

    {"ok": bool, "error": str | None, "data": {...} | None}

``RIVERCROSSING_RACE_CROSSINGS`` (default 3) sets how many crossings
the child records through the real ``plate_input`` (typed event
injection per ``harness.py``). The two scenarios:

- ``record_crash``: resume Continue, record N crossings, print the
  JSON line, then run the app's ``MainLoop`` -- the parent's SIGKILL
  is the hard exit. The quit flow never runs, so ``closed_at`` is
  never stamped and the next launcher reads CRASHED-with-ride; a hard
  kill is byte-equivalent to ``os._exit`` for that bookkeeping.
- ``record_quit``: resume Continue, record N crossings, read the
  resumed clock after one tick, then drive the real File ▸ Exit route
  and confirm Quit on ``exit_running_dlg`` -- ``Store.close_session``
  stamps ``closed_at`` (the next launcher reads RUNNING_AT_EXIT) --
  and exit normally.

The module's non-wx logic (:func:`parse_race_env`, :func:`envelope`,
:func:`race_data`) is unit-tested headless in
``tests/unit/test_race_child.py``. ``faulthandler`` runs first and a
daemon timer hard-bounds the process, the same crash-safety pair
``console_subprocess_scenarios.py``'s ``main`` uses.
"""

from __future__ import annotations

import faulthandler
import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import wx
import wx.xrc

from rivercrossing.standings import hand_name, rank, tiebreak_order_from_spellings
from rivercrossing.ui import app as app_module
from rivercrossing.ui import feed_model, ids
from rivercrossing.ui.views import corrections, dialogs, rider_editor

if TYPE_CHECKING:
    from rivercrossing.ride import RideEngine
    from rivercrossing.store import Store

_FUNCTIONAL_DIR = Path(__file__).resolve().parent.parent / "functional"
if str(_FUNCTIONAL_DIR) not in sys.path:
    sys.path.insert(0, str(_FUNCTIONAL_DIR))

import harness  # noqa: E402 -- needs tests/functional on sys.path first (module docstring)
import pages  # noqa: E402 -- needs tests/functional on sys.path first (module docstring)
import scenario_runner  # noqa: E402 -- its SCENARIO_CHILD_BOUND_SECONDS hard-bounds this child

__all__ = ["envelope", "main", "parse_race_env", "race_data"]

RACE_DB_ENV = "RIVERCROSSING_RACE_DB"
RACE_CROSSINGS_ENV = "RIVERCROSSING_RACE_CROSSINGS"
RACE_CSV_ENV = "RIVERCROSSING_RACE_CSV"
RACE_PLATES_ENV = "RIVERCROSSING_RACE_PLATES"
RACE_EXPORTS_DIR_ENV = "RIVERCROSSING_RACE_EXPORTS_DIR"
RACE_BOUND_ENV = "RIVERCROSSING_RACE_BOUND_SECONDS"
DEFAULT_CROSSINGS = 3
# The wave pattern's default plate count: R-74 imports a 20-rider CSV
# and cycles plates 1..20 in wave order (15 waves = 300 crossings).
DEFAULT_PLATES = 20
# The staged roster's solo entry plate (store_staging.library_roster);
# every race child records this one plate.
_RECORD_PLATE = "12"

# The R-74 finish_and_exports scenario's correction targets: a missed
# crossing added for plate 5 at a pinned early time (before every
# replayed lap, so the picker's event-date time-of-day can express
# it), and the current leader's latest credited card voided through
# the Void Card route.
_ADD_CROSSING_PLATE = "5"
_ADD_CROSSING_TIME = "10:15:00"
_ADD_CROSSING_REASON = "missed crossing"
_VOID_CARD_REASON = "mis-called card"

# The resume-clock read waits one tick past the presenter's 1 s timer
# (the same window _resume_continue_loads_ride_with_elapsed proves);
# 2 s leaves slack under VM load.
_CLOCK_READ_DELAY_MS = 2000


@dataclass(frozen=True, slots=True)
class RaceEnv:
    """The parsed env contract for one full-race child launch.

    Attributes:
        db_path: The shared rides database (RIVERCROSSING_RACE_DB).
        crossings: How many crossings the scenario records
            (RIVERCROSSING_RACE_CROSSINGS; default 3).
        csv_path: The riders CSV to import for ``setup_import_and_run``
            (RIVERCROSSING_RACE_CSV), or None.
        plates: The wave pattern's plate count
            (RIVERCROSSING_RACE_PLATES; default 20).
        exports_dir: Where ``finish_and_exports`` writes the four
            exports (RIVERCROSSING_RACE_EXPORTS_DIR), or None.
        bound_seconds: The child's self-terminate bound
            (RIVERCROSSING_RACE_BOUND_SECONDS; default
            scenario_runner's own bound).
    """

    db_path: Path
    crossings: int
    csv_path: Path | None
    plates: int
    exports_dir: Path | None
    bound_seconds: int


def _optional_path(env_value: str | None) -> Path | None:
    """Return *env_value* as a Path, or None when unset/empty."""
    return Path(env_value) if env_value else None


def _optional_positive_int(env_value: str | None, default: int, env_name: str) -> int:
    """Return *env_value* as a positive int, or *default* when unset.

    Raises:
        ValueError: *env_value* is not an integer or is below 1.
    """
    if not env_value:
        return default
    try:
        value = int(env_value)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{env_name} must be a positive integer")
    return value


def parse_race_env(  # noqa: PLR0913 -- the env contract's six vars plus the default bound
    db_env: str | None,
    crossings_env: str | None,
    *,
    csv_env: str | None = None,
    plates_env: str | None = None,
    exports_dir_env: str | None = None,
    bound_env: str | None = None,
    default_bound: int = scenario_runner.SCENARIO_CHILD_BOUND_SECONDS,
) -> RaceEnv:
    """Return the parsed race env contract for one child launch.

    Args:
        db_env: ``RIVERCROSSING_RACE_DB``'s value.
        crossings_env: ``RIVERCROSSING_RACE_CROSSINGS``' value.
        csv_env: ``RIVERCROSSING_RACE_CSV``'s value.
        plates_env: ``RIVERCROSSING_RACE_PLATES``'s value.
        exports_dir_env: ``RIVERCROSSING_RACE_EXPORTS_DIR``'s value.
        bound_env: ``RIVERCROSSING_RACE_BOUND_SECONDS``'s value.
        default_bound: The bound when *bound_env* is unset/empty.

    Returns:
        The parsed :class:`RaceEnv`.

    Raises:
        ValueError: *db_env* is missing or empty; *crossings_env* is
            not an integer or is negative; *plates_env*/*bound_env* is
            not a positive integer.
    """
    if not db_env:
        raise ValueError(f"{RACE_DB_ENV} must name a rides database")
    try:
        crossings = int(crossings_env) if crossings_env else DEFAULT_CROSSINGS
    except ValueError as exc:
        raise ValueError(f"{RACE_CROSSINGS_ENV} must be an integer") from exc
    if crossings < 0:
        raise ValueError(f"{RACE_CROSSINGS_ENV} must not be negative")
    return RaceEnv(
        db_path=Path(db_env),
        crossings=crossings,
        csv_path=_optional_path(csv_env),
        plates=_optional_positive_int(plates_env, DEFAULT_PLATES, RACE_PLATES_ENV),
        exports_dir=_optional_path(exports_dir_env),
        bound_seconds=_optional_positive_int(bound_env, default_bound, RACE_BOUND_ENV),
    )


def _wave_plates(count: int, plates_count: int) -> list[str]:
    """Return *count* plates cycling ``1..plates_count`` in order."""
    return [str((index % plates_count) + 1) for index in range(count)]


def envelope(*, ok: bool, error: str | None, data: dict[str, Any] | None) -> dict[str, Any]:
    """Return the child's one-line JSON contract with its parent."""
    return {"ok": ok, "error": error, "data": data}


def race_data(  # noqa: PLR0913 -- the report's verbatim facts; a parameter object would hide them
    *,
    scenario: str,
    exit_mode: str,
    ride_id: int,
    resume_dlg_shown: bool,
    resume_state: str | None,
    resume_message: str,
    crossings_recorded: int,
    feed_rows: int,
    feed_plates: list[str],
    audit_actions: list[str],
    recorded_crossings: int,
    status_label: str,
    clock_elapsed: str = "",
) -> dict[str, Any]:
    """Return the child's JSON data block: verbatim facts only."""
    return {
        "scenario": scenario,
        "exit_mode": exit_mode,
        "ride_id": ride_id,
        "resume_dlg_shown": resume_dlg_shown,
        "resume_state": resume_state,
        "resume_message": resume_message,
        "crossings_recorded": crossings_recorded,
        "feed_rows": feed_rows,
        "feed_plates": feed_plates,
        "audit_actions": audit_actions,
        "recorded_crossings": recorded_crossings,
        "status_label": status_label,
        "clock_elapsed": clock_elapsed,
    }


def _post_text_enter(control: Any) -> None:  # noqa: ANN401
    """Post the event a real Enter keypress fires in *control*."""
    event = wx.CommandEvent(wx.EVT_TEXT_ENTER.typeId, control.GetId())
    event.SetEventObject(control)
    control.GetEventHandler().ProcessEvent(event)
    harness.pump()


def _click_continue_resume(found: dict[str, Any]) -> None:
    """Click Continue on resume_dlg, capturing the copy if shown."""
    dialog = wx.Window.FindWindowByName(ids.RESUME_DLG)
    found["resume_dlg_shown"] = dialog is not None and dialog.IsShown()
    if dialog is None:
        return
    found["resume_message"] = harness.find_control(dialog, ids.MESSAGE_LBL).GetLabelText()
    harness.click(dialog, ids.CONTINUE_BTN)


def _click_quit_on_exit_dialog() -> None:
    """Click Quit (wxID_OK) on the running-ride exit dialog."""
    dialog = wx.Window.FindWindowByName(ids.EXIT_RUNNING_DLG)
    if dialog is not None:
        harness.click(dialog, pages.WX_ID_OK)


def _fire_exit_route(frame: Any) -> None:  # noqa: ANN401
    """Post a real ``EVT_MENU`` for ``wxID_EXIT`` at *frame*.

    Never pumped afterwards: the handler this fires calls
    ``ShowModal()`` synchronously, which is itself the native modal
    loop that runs any ``wx.CallAfter`` a caller already scheduled
    (the same ``_fire_exit_route`` technique
    ``console_subprocess_scenarios.py`` uses).
    """
    real_id = wx.xrc.XRCID("wxID_EXIT")
    event = wx.CommandEvent(wx.EVT_MENU.typeId, real_id)
    event.SetEventObject(frame)
    frame.GetEventHandler().ProcessEvent(event)


def _record_crossings(frame: Any, plates: list[str]) -> dict[str, Any]:  # noqa: ANN401
    """Record *plates* through the real plate_input; feed facts back."""
    plate_input = harness.find_control(frame, ids.PLATE_INPUT)
    for plate in plates:
        plate_input.SetValue(plate)
        _post_text_enter(plate_input)
    model = harness.find_control(frame, ids.CROSSINGS_LIST).GetModel()
    return {
        "feed_rows": model.GetCount(),
        "feed_plates": [
            model.GetValueByRow(row, feed_model.COL_PLATE) for row in range(model.GetCount())
        ],
    }


def _ride_facts(store: Store, ride_id: int) -> dict[str, Any]:
    """Return the ride's audit actions and recorded-crossing count.

    Reads through *store*'s own open connection, so the child never
    reopens the db (a reopen would insert another session row). The
    count is of ``record_crossing`` ``audit`` rows -- the one channel
    ``Store.append`` persists; the spec §2 ``crossing`` table is only
    populated by EPIC 5's writer, so it is not the proof.
    """
    rows = store._conn.execute(
        "SELECT action FROM audit WHERE ride_id = ? ORDER BY id", (ride_id,)
    ).fetchall()
    recorded_crossings = store._conn.execute(
        "SELECT COUNT(*) FROM audit WHERE ride_id = ? AND action = 'record_crossing'",
        (ride_id,),
    ).fetchone()[0]
    return {
        "audit_actions": [row["action"] for row in rows],
        "recorded_crossings": recorded_crossings,
    }


def _race_record_crash(env: RaceEnv) -> dict[str, Any]:
    """Resume, record *env.crossings*, report, then wait for the kill.

    The store is closed before the JSON line prints and the child
    enters ``MainLoop``: the parent reads the line and kills the
    process, leaving the session row with ``closed_at`` NULL -- the
    crash the next launcher words.
    """
    found: dict[str, Any] = {"resume_message": ""}
    wx.CallAfter(lambda: _click_continue_resume(found))
    frame, store = app_module._bootstrap_window(wx.GetApp(), db_path=env.db_path)
    frame.Show()
    frame.Layout()
    harness.pump()
    previous = store.previous_session()
    ride_id = previous.ride_id
    if ride_id is None:
        store.close()
        raise RuntimeError("no ride to resume on the shared db")
    plates = [_RECORD_PLATE] * env.crossings
    feed = _record_crossings(frame, plates)
    status_label = harness.find_control(frame, ids.RIDE_STATUS_LBL).GetLabelText()
    try:
        facts = _ride_facts(store, ride_id)
    finally:
        store.close()
    return race_data(
        scenario="record_crash",
        exit_mode="crash",
        ride_id=ride_id,
        resume_dlg_shown=found["resume_dlg_shown"],
        resume_state=previous.state.value,
        resume_message=found["resume_message"],
        crossings_recorded=len(plates),
        feed_rows=feed["feed_rows"],
        feed_plates=feed["feed_plates"],
        audit_actions=facts["audit_actions"],
        recorded_crossings=facts["recorded_crossings"],
        status_label=status_label,
    )


def _race_record_quit(env: RaceEnv) -> dict[str, Any]:
    """Resume, record, read the resumed clock, quit via File ▸ Exit.

    The clock label refreshes on the presenter's 1 s tick timer, which
    only fires under a real ``MainLoop`` (measured; the same wall
    ``_resume_continue_loads_ride_with_elapsed`` documents), so the
    Continue click, the 2 s read+record, and the quit all run on one
    ``MainLoop``: the 2 s ``CallLater`` reads the resumed clock,
    records *env.crossings*, then fires the real exit route (the pre-
    scheduled ``CallAfter`` clicks Quit inside its modal), which stamps
    ``closed_at`` and destroys the frame -- ending the loop.
    """
    found: dict[str, Any] = {"resume_message": ""}
    wx.CallAfter(lambda: _click_continue_resume(found))
    frame, store = app_module._bootstrap_window(wx.GetApp(), db_path=env.db_path)
    frame.Show()
    frame.Layout()
    harness.pump()
    previous = store.previous_session()
    ride_id = previous.ride_id
    if ride_id is None:
        store.close()
        raise RuntimeError("no ride to resume on the shared db")
    plates = [_RECORD_PLATE] * env.crossings

    def _read_clock_record_and_quit() -> None:
        found["clock_elapsed"] = harness.find_control(
            frame, ids.CLOCK_ELAPSED_LBL
        ).GetLabelText()
        found["status_label"] = harness.find_control(frame, ids.RIDE_STATUS_LBL).GetLabelText()
        feed = _record_crossings(frame, plates)
        found["feed_rows"] = feed["feed_rows"]
        found["feed_plates"] = feed["feed_plates"]
        wx.CallAfter(_click_quit_on_exit_dialog)
        _fire_exit_route(frame)

    wx.CallLater(_CLOCK_READ_DELAY_MS, _read_clock_record_and_quit)
    wx.GetApp().MainLoop()
    try:
        facts = _ride_facts(store, ride_id)
    finally:
        store.close()
    return race_data(
        scenario="record_quit",
        exit_mode="quit",
        ride_id=ride_id,
        resume_dlg_shown=found["resume_dlg_shown"],
        resume_state=previous.state.value,
        resume_message=found["resume_message"],
        crossings_recorded=len(plates),
        feed_rows=found["feed_rows"],
        feed_plates=found["feed_plates"],
        audit_actions=facts["audit_actions"],
        recorded_crossings=facts["recorded_crossings"],
        status_label=found["status_label"],
        clock_elapsed=found["clock_elapsed"],
    )


class _ScenarioClock:
    """An advanceable naive datetime clock for the scripted race."""

    def __init__(self, start: datetime) -> None:
        """Start at *start* (the pinned base instant)."""
        self._now = start

    def __call__(self) -> datetime:
        """Return the current scenario time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock forward by *seconds*."""
        self._now = self._now + timedelta(seconds=seconds)


def _staged_timing(store: Store, ride_id: int) -> tuple[datetime, datetime | None]:
    """Return ``(actual_start, latest_crossed_at)`` from the audit log.

    The child's injected clock bases itself on these so new crossings
    always land strictly after every replayed one (module docstring's
    R-34 note). Reads the store's own connection like ``_ride_facts``,
    never a reopen (a reopen would insert another session row).

    Raises:
        RuntimeError: *ride_id* has no start event on the shared db.
    """
    start: datetime | None = None
    latest: datetime | None = None
    rows = store._conn.execute(
        "SELECT action, payload_json FROM audit WHERE ride_id = ? ORDER BY id", (ride_id,)
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"])
        if row["action"] == "start" and start is None:
            start = datetime.fromisoformat(payload["actual_start"])
        elif row["action"] == "record_crossing":
            crossed_at = datetime.fromisoformat(payload["crossed_at"])
            if latest is None or crossed_at > latest:
                latest = crossed_at
    if start is None:
        raise RuntimeError("no ride to resume on the shared db")
    return start, latest


def _ride_min_lap_s(store: Store, ride_id: int) -> int:
    """Return *ride_id*'s configured ``min_lap_s`` from the ride row."""
    row = store._conn.execute("SELECT min_lap_s FROM ride WHERE id = ?", (ride_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"no ride with id {ride_id}")
    return int(row["min_lap_s"])


def _race_clock(store: Store, ride_id: int) -> _ScenarioClock:
    """Build the advanceable clock for the scripted crossing typing.

    Starts one lap past the ride's latest recorded crossing (or five
    minutes after ``actual_start`` when nothing has crossed), and every
    typed lap advances one lap, so each new lap time stays above the
    ride's ``min_lap_s`` -- no card is held (R-34) and the standings
    stay deterministic.
    """
    start, latest = _staged_timing(store, ride_id)
    base = (
        latest + timedelta(seconds=_ride_min_lap_s(store, ride_id))
        if latest is not None
        else start + timedelta(minutes=5)
    )
    return _ScenarioClock(base)


def _feed_rows(frame: Any) -> int:  # noqa: ANN401 -- wx ships no stubs
    """Return how many rows the crossings feed currently renders."""
    model = harness.find_control(frame, ids.CROSSINGS_LIST).GetModel()
    return model.GetCount()


def _start_event_starts(store: Store, ride_id: int) -> list[str]:
    """Return every start/continue event's actual_start, in order."""
    rows = store._conn.execute(
        "SELECT payload_json FROM audit WHERE ride_id = ?"
        " AND action IN ('start', 'continue') ORDER BY id",
        (ride_id,),
    ).fetchall()
    return [json.loads(row["payload_json"])["actual_start"] for row in rows]


def _record_crossings_lapped(  # noqa: PLR0913, PLR0917 -- the typed-typing inputs
    frame: Any,  # noqa: ANN401 -- wx ships no stubs
    clock: _ScenarioClock,
    plates: list[str],
    lap_seconds: int,
) -> dict[str, Any]:
    """Record *plates* through plate_input, advancing *clock* per lap.

    Each crossing lands *lap_seconds* after the previous one (and the
    ride's replayed tail), so no lap is flagged short and every dealt
    card is credited -- the deterministic-standings contract the R-74
    parent verifies against the exports.
    """
    plate_input = harness.find_control(frame, ids.PLATE_INPUT)
    for plate in plates:
        clock.advance(lap_seconds)
        plate_input.SetValue(plate)
        _post_text_enter(plate_input)
    return {"feed_rows": _feed_rows(frame)}


def _import_csv_via_route(frame: Any, csv_path: Path) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Import *csv_path* through the real mi_import_csv route.

    The preview dialog's ``wxID_OK`` is clicked inside the modal loop
    through the ``dialogs.run_dialog`` seam (the
    ``console_subprocess_scenarios`` precedent): the real
    ``CsvPreviewDialog`` handler commits the CSV into the roster, and
    the route's R-74 store half saves that roster to the active ride.
    """
    original_pick = rider_editor._pick_import_path
    original_run_dialog = dialogs.run_dialog

    def _click_import(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001 -- wx ships no stubs
        harness.click(dialog, "wxID_OK")
        return wx.ID_OK

    rider_editor._pick_import_path = lambda _parent: csv_path
    dialogs.run_dialog = _click_import
    try:
        harness.fire_menu_event(frame, ids.MI_IMPORT_CSV)
    finally:
        rider_editor._pick_import_path = original_pick
        dialogs.run_dialog = original_run_dialog


def _stop_and_continue(frame: Any) -> dict[str, bool]:  # noqa: ANN401 -- wx ships no stubs
    """Arm, confirm the stop, check the lock, continue (R-35).

    Returns whether the stop locked plate entry and the continue
    re-enabled it with the same start (the caller compares the
    start/continue audit payloads).
    """
    arm_stop = harness.find_control(frame, ids.ARM_STOP_CHK)
    arm_stop.SetValue(True)  # noqa: FBT003 -- wx API takes a positional bool
    event = wx.CommandEvent(wx.EVT_CHECKBOX.typeId, arm_stop.GetId())
    event.SetEventObject(arm_stop)
    arm_stop.GetEventHandler().ProcessEvent(event)
    harness.pump()

    def _click_stop_ok() -> None:
        dialog = wx.Window.FindWindowByName(ids.STOP_CONFIRM_DLG)
        if dialog is not None:
            harness.click(dialog, "wxID_OK")

    wx.CallAfter(_click_stop_ok)
    harness.click(frame, ids.STOP_BTN)
    harness.pump()
    entry_locked = not harness.find_control(frame, ids.PLATE_INPUT).IsEnabled()

    harness.click(frame, ids.START_BTN)
    harness.pump()
    entry_reenabled = harness.find_control(frame, ids.PLATE_INPUT).IsEnabled()
    return {"entry_locked": entry_locked, "entry_reenabled": entry_reenabled}


def _standings_rows(engine: RideEngine) -> list[dict[str, object]]:
    """Return ranked standings as (place, plate, laps, hand) rows.

    The hand names use ``standings.hand_name``'s prose (the exact
    strings the HTML/PDF/CSV exports render), blank for a zero-card
    entry -- the same guard the export writers apply.
    """
    order = tiebreak_order_from_spellings(engine.config.tiebreak_order)
    rows: list[dict[str, object]] = []
    for placed in rank(engine.snapshot(), order):
        result = placed.result
        rows.append(
            {
                "place": placed.place,
                "plate": result.plate,
                "name": result.name,
                "laps": result.laps,
                "hand": hand_name(result.hand) if result.cards else "",
            }
        )
    return rows


def _voided_card_code(store: Store, ride_id: int) -> str:
    """Return the card code the latest void_card audit event carried."""
    row = store._conn.execute(
        "SELECT payload_json FROM audit WHERE ride_id = ? AND action = 'void_card'"
        " ORDER BY id DESC LIMIT 1",
        (ride_id,),
    ).fetchone()
    if row is None:
        return ""
    return str(json.loads(row["payload_json"]).get("card", ""))


def _drive_dialog_ok(dialog_name: str) -> None:
    """Schedule a wxID_OK click on the modal *dialog_name*."""

    def _drive() -> None:
        dialog = wx.Window.FindWindowByName(dialog_name)
        if dialog is not None:
            harness.click(dialog, "wxID_OK")

    wx.CallAfter(_drive)


def _drive_add_crossing() -> None:
    """Schedule the add-crossing form (plate, time, reason)."""

    def _drive() -> None:
        dialog = wx.Window.FindWindowByName(ids.EDIT_CROSSING_DLG)
        if dialog is None:
            return
        harness.type_text(dialog, ids.PLATE_INPUT, _ADD_CROSSING_PLATE)
        time_picker = corrections._find(dialog, ids.TIME_PICKER)
        corrections._set_time_picker(time_picker, _ADD_CROSSING_TIME)
        harness.type_text(dialog, ids.REASON_INPUT, _ADD_CROSSING_REASON)
        harness.click(dialog, "wxID_OK")

    wx.CallAfter(_drive)


def _click_ok_on_exit_confirm() -> None:
    """Click Quit (wxID_OK) on the non-running exit-confirm dialog."""
    dialog = wx.Window.FindWindowByName(ids.EXIT_CONFIRM_DLG)
    if dialog is not None:
        harness.click(dialog, pages.WX_ID_OK)


def _install_sync_exports(exports_dir: Path, paths: dict[str, Path]) -> None:
    """Point the export picker at *exports_dir* and write synchronously.

    The off-loop thread is replaced with a synchronous writer that
    records the path and watermark the moment the write lands -- the
    same seam ``test_results_exports._sync_offloop`` uses -- so the
    child can report ``export_paths``/``export_watermark`` without
    waiting on a background thread.
    """

    def _sync_offloop(  # noqa: PLR0913 -- mirrors _run_export_offloop's inputs
        ctx: Any,  # noqa: ANN401 -- wx ships no stubs; the live route context
        target: str,
        path: Path,
        *,
        config: object,
        placed: object,
        opts: object,
        watermark: int | None = None,
    ) -> None:
        app_module._write_export(config, placed, opts, target, path)
        ctx.last_export_path = path  # type: ignore[attr-defined]
        ctx.export_watermark = watermark  # type: ignore[attr-defined]
        paths[target] = path

    app_module._pick_export_path = lambda _name: exports_dir / _name
    app_module._run_export_offloop = _sync_offloop


def _race_setup_import_and_run(env: RaceEnv) -> dict[str, Any]:
    """Resume, import CSV, type *env.crossings*, stop/continue, report.

    R-74's opening leg: the child resumes the staged ride, imports the
    riders CSV through the real ``mi_import_csv`` route (the roster is
    saved to the store ride -- the gap the R-74 parent asserts via
    ``imported_plates``), confirms the console is RUNNING with entry
    enabled, types the scripted wave of crossings through the real
    ``plate_input`` on the injected clock, runs the stop/continue
    cycle, prints the JSON report, and waits in ``MainLoop`` for the
    parent's SIGKILL (the crash leg; ``closed_at`` is never stamped).
    """
    csv_path = env.csv_path
    if csv_path is None:
        raise RuntimeError(f"{RACE_CSV_ENV} must name a riders CSV")
    found: dict[str, Any] = {"resume_message": ""}
    wx.CallAfter(lambda: _click_continue_resume(found))
    frame, store = app_module._bootstrap_window(wx.GetApp(), db_path=env.db_path)
    frame.Show()
    frame.Layout()
    harness.pump()
    previous = store.previous_session()
    ride_id = previous.ride_id
    if ride_id is None:
        store.close()
        raise RuntimeError("no ride to resume on the shared db")
    clock = _race_clock(store, ride_id)
    lap_seconds = _ride_min_lap_s(store, ride_id)
    _import_csv_via_route(frame, csv_path)
    status_label = harness.find_control(frame, ids.RIDE_STATUS_LBL).GetLabelText()
    entry_enabled = harness.find_control(frame, ids.PLATE_INPUT).IsEnabled()
    imported_plates = sorted(entry.plate for entry in store.roster_for(ride_id).entries)
    plates = _wave_plates(env.crossings, env.plates)
    feed = _record_crossings_lapped(frame, clock, plates, lap_seconds)
    stop_continue = _stop_and_continue(frame)
    starts = _start_event_starts(store, ride_id)
    try:
        facts = _ride_facts(store, ride_id)
    finally:
        store.close()
    return {
        "scenario": "setup_import_and_run",
        "exit_mode": "crash",
        "ride_id": ride_id,
        "resume_dlg_shown": found["resume_dlg_shown"],
        "resume_state": previous.state.value,
        "resume_message": found["resume_message"],
        "status_label": status_label,
        "entry_enabled": entry_enabled,
        "imported_plates": imported_plates,
        "crossings_typed": len(plates),
        "feed_rows": feed["feed_rows"],
        "audit_actions": facts["audit_actions"],
        "recorded_crossings": facts["recorded_crossings"],
        "entry_locked_when_stopped": stop_continue["entry_locked"],
        "entry_reenabled_after_continue": stop_continue["entry_reenabled"],
        "continue_kept_start": len(starts) >= 2 and starts[-1] == starts[0],
    }


def _race_resume_verify_quit(env: RaceEnv) -> dict[str, Any]:
    """Resume, verify survivors, record *env.crossings*, quit cleanly.

    R-74's crash-wording leg: the child relaunches on the shared db,
    captures the resume dialog's crash wording, reads the surviving
    feed and audit counts, records a few more crossings on the injected
    clock (strictly past the replayed tail), and quits through the real
    File ▸ Exit flow so ``closed_at`` is stamped.
    """
    found: dict[str, Any] = {"resume_message": ""}
    wx.CallAfter(lambda: _click_continue_resume(found))
    frame, store = app_module._bootstrap_window(wx.GetApp(), db_path=env.db_path)
    frame.Show()
    frame.Layout()
    harness.pump()
    previous = store.previous_session()
    ride_id = previous.ride_id
    if ride_id is None:
        store.close()
        raise RuntimeError("no ride to resume on the shared db")
    feed_before = _feed_rows(frame)
    facts_before = _ride_facts(store, ride_id)
    clock = _race_clock(store, ride_id)
    lap_seconds = _ride_min_lap_s(store, ride_id)
    plates = _wave_plates(env.crossings, env.plates)
    feed = _record_crossings_lapped(frame, clock, plates, lap_seconds)
    status_label = harness.find_control(frame, ids.RIDE_STATUS_LBL).GetLabelText()
    wx.CallAfter(_click_quit_on_exit_dialog)
    _fire_exit_route(frame)
    try:
        facts_after = _ride_facts(store, ride_id)
    finally:
        store.close()
    return {
        "scenario": "resume_verify_quit",
        "exit_mode": "quit",
        "ride_id": ride_id,
        "resume_dlg_shown": found["resume_dlg_shown"],
        "resume_state": previous.state.value,
        "resume_message": found["resume_message"],
        "status_label": status_label,
        "feed_rows_before": feed_before,
        "recorded_crossings_before": facts_before["recorded_crossings"],
        "crossings_recorded": len(plates),
        "feed_rows_after": feed["feed_rows"],
        "recorded_crossings_after": facts_after["recorded_crossings"],
        "audit_actions": facts_after["audit_actions"],
    }


def _race_finish_and_exports(env: RaceEnv) -> dict[str, Any]:  # noqa: PLR0915 -- the R-74 finish leg's fixed script
    """Resume, finish, reopen, correct, finish again, export, quit.

    R-74's closing leg through the real menu routes: finish #1 (the
    ``mi_finish_ride`` confirm), a baseline HTML export that stamps the
    export watermark, ``mi_reopen_ride`` into REOPENED, a missed
    crossing added at an explicit time (``mi_add_crossing_at``), the
    current leader's latest card voided (``mi_void_card``, which reads
    ``context.detail_plate``), finish again (REOPENED's re-labelled
    route), all four Results exports written to ``env.exports_dir``,
    and a clean File ▸ Exit quit. The context is captured through the
    ``_bind_routes`` seam so the Void Card route's ``detail_plate`` and
    the export watermark are observable.
    """
    exports_dir = env.exports_dir
    if exports_dir is None:
        raise RuntimeError(f"{RACE_EXPORTS_DIR_ENV} must name an exports directory")
    found: dict[str, Any] = {"resume_message": ""}
    wx.CallAfter(lambda: _click_continue_resume(found))
    original_bind_routes = app_module._bind_routes
    captured: list[Any] = []

    def _capture_and_bind(context: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        captured.append(context)
        original_bind_routes(context)

    app_module._bind_routes = _capture_and_bind
    try:
        frame, store = app_module._bootstrap_window(wx.GetApp(), db_path=env.db_path)
    finally:
        app_module._bind_routes = original_bind_routes
    frame.Show()
    frame.Layout()
    harness.pump()
    previous = store.previous_session()
    ride_id = previous.ride_id
    if ride_id is None or not captured:
        store.close()
        raise RuntimeError("no ride to resume or no route context on the shared db")
    context = captured[0]
    engine = context.presenter.engine
    feed_rows = _feed_rows(frame)
    status_label = harness.find_control(frame, ids.RIDE_STATUS_LBL).GetLabelText()

    # Finish #1 through the real route; snapshot the pre-correction
    # standings and stamp the baseline export watermark.
    _drive_dialog_ok(ids.FINISH_CONFIRM_DLG)
    harness.fire_menu_event(frame, ids.MI_FINISH_RIDE)
    status_1 = harness.find_control(frame, ids.RIDE_STATUS_LBL).GetLabelText()
    standings_before = _standings_rows(engine)
    paths: dict[str, Path] = {}
    original_pick = app_module._pick_export_path
    original_offloop = app_module._run_export_offloop
    _install_sync_exports(exports_dir, paths)
    harness.fire_menu_event(frame, ids.MI_EXPORT_HTML)
    watermark_baseline = context.export_watermark

    # Reopen, then the two corrections past the baseline watermark.
    _drive_dialog_ok(ids.REOPEN_RIDE_DLG)
    harness.fire_menu_event(frame, ids.MI_REOPEN_RIDE)
    reopened = harness.find_control(frame, ids.RIDE_STATUS_LBL).GetLabelText() == "REOPENED"
    _drive_add_crossing()
    harness.fire_menu_event(frame, ids.MI_ADD_CROSSING_AT)
    context.detail_plate = cast("str", _standings_rows(engine)[0]["plate"])
    _drive_dialog_ok(ids.VOID_CARD_CONFIRM_DLG)
    harness.fire_menu_event(frame, ids.MI_VOID_CARD)
    voided_card = _voided_card_code(store, ride_id)

    # Finish again (REOPENED re-locks), then export all four.
    _drive_dialog_ok(ids.FINISH_CONFIRM_DLG)
    harness.fire_menu_event(frame, ids.MI_FINISH_RIDE)
    status_2 = harness.find_control(frame, ids.RIDE_STATUS_LBL).GetLabelText()
    standings_final = _standings_rows(engine)
    for item_id in (
        ids.MI_EXPORT_HTML,
        ids.MI_EXPORT_PDF,
        ids.MI_EXPORT_POSTER,
        ids.MI_EXPORT_RESULTS_CSV,
    ):
        harness.fire_menu_event(frame, item_id)
    watermark_final = context.export_watermark
    events_after_finish = len(engine.events)
    export_paths = {target: str(path) for target, path in paths.items()}

    app_module._pick_export_path = original_pick
    app_module._run_export_offloop = original_offloop
    # Quit cleanly through the real File ▸ Exit route: the session
    # row's closed_at is stamped (the parent's final session fact).
    wx.CallAfter(_click_ok_on_exit_confirm)
    _fire_exit_route(frame)
    try:
        facts = _ride_facts(store, ride_id)
    finally:
        store.close()
    return {
        "scenario": "finish_and_exports",
        "exit_mode": "quit",
        "ride_id": ride_id,
        "resume_dlg_shown": found["resume_dlg_shown"],
        "resume_state": previous.state.value,
        "resume_message": found["resume_message"],
        "status_label": status_label,
        "feed_rows": feed_rows,
        "status_after_finish_1": status_1,
        "standings_before_corrections": standings_before,
        "reopened": reopened,
        "add_crossing_plate": _ADD_CROSSING_PLATE,
        "voided_card": voided_card,
        "status_after_finish_2": status_2,
        "standings": standings_final,
        "watermark_baseline": watermark_baseline,
        "events_after_finish": events_after_finish,
        "watermark_final": watermark_final,
        "export_paths": export_paths,
        "audit_actions": facts["audit_actions"],
        "recorded_crossings": facts["recorded_crossings"],
    }


_SCENARIOS: dict[str, Any] = {
    "record_crash": _race_record_crash,
    "record_quit": _race_record_quit,
    "setup_import_and_run": _race_setup_import_and_run,
    "resume_verify_quit": _race_resume_verify_quit,
    "finish_and_exports": _race_finish_and_exports,
}


def main(argv: list[str]) -> int:
    """Run the race scenario named in *argv*; print one JSON line.

    The JSON line on stdout *is* this program's whole contract with
    its parent (module docstring) -- printing it is not debug output
    left behind, it is the point. ``faulthandler.enable()`` runs
    first, before anything else here; the daemon timer hard-bounds the
    process (exit 124) so a hung child cannot stall the parent's own
    bound. The app is built once and kept alive by this binding -- an
    unbound App is collected immediately (measured; the same note
    ``console_subprocess_scenarios.py``'s ``main`` carries).
    """
    faulthandler.enable()
    if len(argv) != 2:
        print(  # noqa: T201 -- the child's entire contract with its parent
            json.dumps(envelope(ok=False, error="usage: <script> <scenario>", data=None)),
            flush=True,
        )
        return 2
    try:
        env = parse_race_env(
            os.environ.get(RACE_DB_ENV),
            os.environ.get(RACE_CROSSINGS_ENV),
            csv_env=os.environ.get(RACE_CSV_ENV),
            plates_env=os.environ.get(RACE_PLATES_ENV),
            exports_dir_env=os.environ.get(RACE_EXPORTS_DIR_ENV),
            bound_env=os.environ.get(RACE_BOUND_ENV),
        )
        scenario = _SCENARIOS.get(argv[1])
        if scenario is None:
            print(  # noqa: T201 -- the child's entire contract with its parent
                json.dumps(envelope(ok=False, error=f"unknown scenario {argv[1]!r}", data=None)),
                flush=True,
            )
            return 1
        # The env-driven bound hard-limits this process (exit 124) so a
        # hung child cannot stall the parent's own bound; the parent
        # sets it well above the scenario's healthy runtime.
        faulthandler.dump_traceback_later(env.bound_seconds, exit=False)
        bound = threading.Timer(env.bound_seconds + 2, os._exit, args=(124,))
        bound.daemon = True
        bound.start()
        # Built after the error paths, so refusals stay display-free
        # (testable headless); an unbound App is collected immediately.
        app = app_module.build_app()  # noqa: F841 -- kept alive by this binding, never read
        wx.Log.SetActiveTarget(wx.LogStderr())
        data = scenario(env)
    except Exception as exc:  # noqa: BLE001 -- reported in the envelope, not swallowed
        print(  # noqa: T201 -- the child's entire contract with its parent
            json.dumps(envelope(ok=False, error=f"{type(exc).__name__}: {exc}", data=None)),
            flush=True,
        )
        return 1
    print(json.dumps(envelope(ok=True, error=None, data=data)), flush=True)  # noqa: T201 -- the child's entire contract
    if argv[1] in ("record_crash", "setup_import_and_run"):
        # The parent SIGKILLs this process (the crash leg); only the
        # bound timer or a platform anomaly returns from here.
        wx.GetApp().MainLoop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
