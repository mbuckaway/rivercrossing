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
from pathlib import Path
from typing import TYPE_CHECKING, Any

import wx
import wx.xrc

from rivercrossing.ui import app as app_module
from rivercrossing.ui import feed_model, ids

if TYPE_CHECKING:
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
DEFAULT_CROSSINGS = 3
# The staged roster's solo entry plate (store_staging.library_roster);
# every race child records this one plate.
_RECORD_PLATE = "12"

# The resume-clock read waits one tick past the presenter's 1 s timer
# (the same window _resume_continue_loads_ride_with_elapsed proves);
# 2 s leaves slack under VM load.
_CLOCK_READ_DELAY_MS = 2000


def parse_race_env(db_env: str | None, crossings_env: str | None) -> tuple[Path, int]:
    """Return (db_path, crossings) from the race env vars.

    Args:
        db_env: ``RIVERCROSSING_RACE_DB``'s value.
        crossings_env: ``RIVERCROSSING_RACE_CROSSINGS``' value.

    Returns:
        The shared db path, and how many crossings to record --
        :data:`DEFAULT_CROSSINGS` when *crossings_env* is unset/empty.

    Raises:
        ValueError: *db_env* is missing or empty; *crossings_env* is
            not an integer or is negative.
    """
    if not db_env:
        raise ValueError(f"{RACE_DB_ENV} must name a rides database")
    try:
        crossings = int(crossings_env) if crossings_env else DEFAULT_CROSSINGS
    except ValueError as exc:
        raise ValueError(f"{RACE_CROSSINGS_ENV} must be an integer") from exc
    if crossings < 0:
        raise ValueError(f"{RACE_CROSSINGS_ENV} must not be negative")
    return Path(db_env), crossings


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


def _race_record_crash(db_path: Path, crossings: int) -> dict[str, Any]:
    """Resume, record *crossings*, report, then wait for the SIGKILL.

    The store is closed before the JSON line prints and the child
    enters ``MainLoop``: the parent reads the line and kills the
    process, leaving the session row with ``closed_at`` NULL -- the
    crash the next launcher words.
    """
    found: dict[str, Any] = {"resume_message": ""}
    wx.CallAfter(lambda: _click_continue_resume(found))
    frame, store = app_module._bootstrap_window(wx.GetApp(), db_path=db_path)
    frame.Show()
    frame.Layout()
    harness.pump()
    previous = store.previous_session()
    ride_id = previous.ride_id
    if ride_id is None:
        store.close()
        raise RuntimeError("no ride to resume on the shared db")
    plates = [_RECORD_PLATE] * crossings
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


def _race_record_quit(db_path: Path, crossings: int) -> dict[str, Any]:
    """Resume, record, read the resumed clock, quit via File ▸ Exit.

    The clock label refreshes on the presenter's 1 s tick timer, which
    only fires under a real ``MainLoop`` (measured; the same wall
    ``_resume_continue_loads_ride_with_elapsed`` documents), so the
    Continue click, the 2 s read+record, and the quit all run on one
    ``MainLoop``: the 2 s ``CallLater`` reads the resumed clock,
    records *crossings*, then fires the real exit route (the pre-
    scheduled ``CallAfter`` clicks Quit inside its modal), which stamps
    ``closed_at`` and destroys the frame -- ending the loop.
    """
    found: dict[str, Any] = {"resume_message": ""}
    wx.CallAfter(lambda: _click_continue_resume(found))
    frame, store = app_module._bootstrap_window(wx.GetApp(), db_path=db_path)
    frame.Show()
    frame.Layout()
    harness.pump()
    previous = store.previous_session()
    ride_id = previous.ride_id
    if ride_id is None:
        store.close()
        raise RuntimeError("no ride to resume on the shared db")
    plates = [_RECORD_PLATE] * crossings

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


_SCENARIOS: dict[str, Any] = {
    "record_crash": _race_record_crash,
    "record_quit": _race_record_quit,
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
    faulthandler.dump_traceback_later(scenario_runner.SCENARIO_CHILD_BOUND_SECONDS, exit=False)
    bound = threading.Timer(
        scenario_runner.SCENARIO_CHILD_BOUND_SECONDS + 2, os._exit, args=(124,)
    )
    bound.daemon = True
    bound.start()
    if len(argv) != 2:
        print(  # noqa: T201 -- the child's entire contract with its parent
            json.dumps(envelope(ok=False, error="usage: <script> <scenario>", data=None)),
            flush=True,
        )
        return 2
    try:
        db_path, crossings = parse_race_env(
            os.environ.get(RACE_DB_ENV), os.environ.get(RACE_CROSSINGS_ENV)
        )
        scenario = _SCENARIOS.get(argv[1])
        if scenario is None:
            print(  # noqa: T201 -- the child's entire contract with its parent
                json.dumps(envelope(ok=False, error=f"unknown scenario {argv[1]!r}", data=None)),
                flush=True,
            )
            return 1
        # Built after the error paths, so refusals stay display-free
        # (testable headless); an unbound App is collected immediately.
        app = app_module.build_app()  # noqa: F841 -- kept alive by this binding, never read
        wx.Log.SetActiveTarget(wx.LogStderr())
        data = scenario(db_path, crossings)
    except Exception as exc:  # noqa: BLE001 -- reported in the envelope, not swallowed
        print(  # noqa: T201 -- the child's entire contract with its parent
            json.dumps(envelope(ok=False, error=f"{type(exc).__name__}: {exc}", data=None)),
            flush=True,
        )
        return 1
    print(json.dumps(envelope(ok=True, error=None, data=data)), flush=True)  # noqa: T201 -- the child's entire contract
    if argv[1] == "record_crash":
        # The parent SIGKILLs this process (the crash leg); only the
        # bound timer or a platform anomaly returns from here.
        wx.GetApp().MainLoop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
