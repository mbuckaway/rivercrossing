# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the W3 launch flow.

Post-Show launch prompts (R-52 / ux-polish),

The launch-flow workstream moves every launch modal out of
``build_main_window`` (a modal that cannot present before
``frame.Show()`` blocks the app invisibly -- the "app never starts
again" regression): the frame is shown first, then
:func:`rivercrossing.ui.app._run_launch_flow` decides from the
store's previous-session record whether to show ``resume_dlg``, the
No Ride Open info alert, or nothing. This module proves that flow
headless:

- the pure decision (:func:`_launch_choice`) over every session
  state / ride-open combination;
- Continue reuses the library-Open console switch and keeps
  ``Store.set_active_ride`` (the R-52 resume marker);
- a replay failure (``RideEngineError`` from ``Store.load_engine``
  against a drifted roster) clears the marker, shows an error naming
  the ride and the offending event, and leaves the placeholder
  console up;
- Open library defers ``ride_library_dlg`` through ``wx.CallAfter``
  and never double-prompts;
- a no-candidate launch with no ride open shows the No Ride Open
  info alert with the exact copy.

The wx boundary is the one mocked thing: ``require_wx`` is replaced
with a recorder, and ``std_dialogs.show_info``/``show_error`` are
replaced with spies (T-10: wx and the native-message-dialog seam are
the GUI I/O boundary). Session and ride data run against a real
``Store`` over a ``tmp_path`` file, the established store-test style.
"""

import re
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from conftest import gorba_config
from rivercrossing.ride import Event, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.store import PreviousSession, SessionState, Store
from rivercrossing.ui import app as app_module
from rivercrossing.ui import commands, ids, std_dialogs
from rivercrossing.ui.presenters.console import ConsolePresenter

if TYPE_CHECKING:
    from pathlib import Path

# The staged ride's actual_start, as the store's audit payload (naive
# local, the engine's own contract -- a literal, never a tz call).
_START_ISO = "2026-09-20T10:00:00"

_NO_RIDE_TITLE = "No Ride Open"
_NO_RIDE_COPY = "No ride is loaded. Create a new one or load an existing one."


class _FakeFrame:
    """Record status-bar notices; no wx window ever exists."""

    def __init__(self) -> None:
        """Start with an empty notice log."""
        self.notices: list[str] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record one status-bar notice."""
        self.notices.append(text)


class _FakeConsoleView:
    """Record every render call a console switch makes."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, object]] = []

    def set_presenter(self, presenter: object) -> None:
        """Record the swapped presenter."""
        self.calls.append(("set_presenter", presenter))

    def show_ride_name(self, name: str) -> None:
        """Record the rendered ride name."""
        self.calls.append(("show_ride_name", name))

    def set_state(self, status: RideStatus) -> None:
        """Record the rendered lifecycle state."""
        self.calls.append(("set_state", status))

    def show_feed(self, rows: list[object]) -> None:
        """Record the number of rendered feed rows."""
        self.calls.append(("show_feed", len(rows)))

    def show_counters(self, counters: object) -> None:
        """Record the rendered counters."""
        self.calls.append(("show_counters", counters))

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Record the R-11 teams-chip visibility push (W12 protocol)."""
        self.calls.append(("set_team_ui_visible", visible))

    def focus_entry(self) -> None:
        """Record the focus request."""
        self.calls.append(("focus_entry", None))


class _FakeWx:
    """Record every ``CallAfter`` schedule without constructing GUI."""

    def __init__(self) -> None:
        """Start with an empty schedule log."""
        self.calls: list[tuple[object, tuple[object, ...]]] = []

    def CallAfter(self, callable_: object, *args: object) -> None:  # noqa: N802 -- wx API name
        """Record one deferred call."""
        self.calls.append((callable_, args))


class _FixedPreviousStore:
    """A store stand-in returning one canned previous-session record."""

    def __init__(self, previous: PreviousSession) -> None:
        """Hold the record every ``previous_session`` call returns."""
        self.previous = previous

    def previous_session(self) -> PreviousSession:
        """Return the canned previous-session record."""
        return self.previous


def _context(
    store: Store | None,
    view: _FakeConsoleView | None = None,
    frame: _FakeFrame | None = None,
) -> app_module._RouteContext:
    """Build a route context over *store* with fake frame/view."""
    return app_module._RouteContext(
        frame=frame if frame is not None else _FakeFrame(),
        resource=None,
        roster=Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED),
        app=None,
        theme_controller=None,
        store=store,
        console_view=view,
    )


def _latest_session_active_ride(db_path: Path) -> int | None:
    """Read the newest app_session row's ``active_ride_id`` off disk."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT active_ride_id FROM app_session ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise AssertionError("no app_session row")
    return row["active_ride_id"]


def _stage_resumed_ride(db_path: Path, *, corrupt_replay: bool = False) -> int:
    """Stage a quit-keep-running ride the next launch can resume.

    Creates the ride, records a ``start`` event (so a replay reaches
    RUNNING), marks it active, and closes the session cleanly -- the
    previous-session record the launch flow reads is RUNNING_AT_EXIT
    with *ride_id*. ``corrupt_replay`` appends a ``confirm_held`` row
    no crossing can satisfy, the drifted-roster replay shape
    (``ride.py``'s ``_crossing_from`` raise).

    Returns:
        The staged ride's id.
    """
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(gorba_config())
        store.append(
            ride_id,
            Event(action="start", payload={"actual_start": _START_ISO}),
        )
        store.set_active_ride(ride_id)
        if corrupt_replay:
            with store._conn:
                store._conn.execute(
                    "INSERT INTO audit (ride_id, at, action, payload_json) VALUES (?, ?, ?, ?)",
                    (ride_id, 0, "confirm_held", '{"entry_id": "12", "seq": 1}'),
                )
        store.close_session()  # quit-keep-running
    finally:
        store.close()
    return ride_id


# ------------------------------------------------- _launch_choice


@pytest.mark.parametrize(
    ("state", "ride_id", "ride_open", "expected"),
    [
        (SessionState.RUNNING_AT_EXIT, 7, False, "resume"),
        (SessionState.RUNNING_AT_EXIT, 7, True, "resume"),
        (SessionState.CRASHED, 7, False, "resume"),
        (SessionState.CRASHED, 7, True, "resume"),
        (SessionState.CRASHED, None, False, "no_ride"),
        (SessionState.CRASHED, None, True, "none"),
        (SessionState.CLEAN_QUIT, None, False, "no_ride"),
        (SessionState.CLEAN_QUIT, None, True, "none"),
    ],
    ids=(
        "running_at_exit_resumes",
        "running_at_exit_resumes_even_with_ride_open",
        "crashed_with_ride_resumes",
        "crashed_with_ride_resumes_even_with_ride_open",
        "crashed_without_ride_prompts_no_ride",
        "crashed_without_ride_with_ride_open_does_nothing",
        "clean_quit_prompts_no_ride",
        "clean_quit_with_ride_open_does_nothing",
    ),
)
def test_launch_choice_returns_the_dialog_for_the_session_state(  # noqa: PLR0913, PLR0917 -- (state, ride_id, ride_open, expected): the T-13 decision table's four columns; FBT001: ride_open is a table column, never a call-site flag
    state: SessionState,
    ride_id: int | None,
    ride_open: bool,  # noqa: FBT001 -- decision-table column, not a call-site flag
    expected: str,
) -> None:
    """T-13: the resume/no-ride/none decision over every state pair."""
    session = PreviousSession(state=state, ride_id=ride_id, ended_at=None)

    choice = app_module._launch_choice(session, ride_open=ride_open)

    assert choice == expected


# ------------------------------------------- store-less builds


def test_run_launch_flow_given_no_store_returns_without_prompting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store-less build has no session row and no launch prompt."""
    shown: list[tuple[object, ...]] = []
    monkeypatch.setattr(std_dialogs, "show_info", lambda *args: shown.append(args))

    app_module._run_launch_flow(_context(store=None), None)

    assert shown == []


# ----------------------------------------- no-candidate launches


def test_run_launch_flow_given_no_candidate_and_no_ride_shows_no_ride_info_with_exact_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh store: the No Ride Open alert appears with the copy."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    shown: list[tuple[object, ...]] = []
    monkeypatch.setattr(std_dialogs, "show_info", lambda *args: shown.append(args))
    try:
        frame = _FakeFrame()
        context = _context(store=store, frame=frame)

        app_module._run_launch_flow(context, store)
    finally:
        store.close()

    assert shown == [(frame, _NO_RIDE_TITLE, _NO_RIDE_COPY)]


def test_run_launch_flow_given_no_candidate_with_a_ride_open_shows_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ride already open answers the launch; no prompt appears."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    shown: list[tuple[object, ...]] = []
    monkeypatch.setattr(std_dialogs, "show_info", lambda *args: shown.append(args))
    try:
        context = _context(store=store)
        context.active_ride_id = 7

        app_module._run_launch_flow(context, store)
    finally:
        store.close()

    assert shown == []


def test_run_launch_flow_given_a_resume_warranted_record_without_a_ride_raises() -> None:
    """A resume warrant with no ride is a caller bug, never a prompt."""
    session = PreviousSession(
        state=SessionState.RUNNING_AT_EXIT,
        ride_id=None,
        ended_at=datetime(2026, 9, 20, 12, 0),  # noqa: DTZ001 -- naive local, the store's contract
    )
    context = _context(store=None)

    with pytest.raises(
        RuntimeError, match=re.escape("resume dialog warranted without a ride or end time")
    ):
        app_module._run_launch_flow(context, _FixedPreviousStore(session))


def test_run_resume_dialog_given_no_end_time_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dialog shell's own guard names the missing record field."""
    session = PreviousSession(state=SessionState.RUNNING_AT_EXIT, ride_id=7, ended_at=None)
    context = _context(store=None)

    def _fake_wx() -> _FakeWx:
        """Return a recorder wx stand-in."""
        return _FakeWx()

    monkeypatch.setattr(app_module, "require_wx", _fake_wx)

    with pytest.raises(
        RuntimeError, match=re.escape("resume dialog warranted without a ride or end time")
    ):
        app_module._run_resume_dialog(context, _FixedPreviousStore(session), session)


# ------------------------------------------------- Continue path


def test_run_launch_flow_continue_resumes_the_ride_and_keeps_the_active_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Continue reloads the ride; the session marker stays set.

    The switch runs; the R-52 marker on the open session is kept.
    """
    db_path = tmp_path / "rides.db"
    ride_id = _stage_resumed_ride(db_path)
    store = Store.open(db_path)
    view = _FakeConsoleView()
    context = _context(store=store, view=view)
    monkeypatch.setattr(app_module, "_run_resume_dialog", lambda _c, _s, _p: "continue")
    try:
        app_module._run_launch_flow(context, store)
    finally:
        store.close()

    assert context.active_ride_id == ride_id
    assert _latest_session_active_ride(db_path) == ride_id
    assert ("show_ride_name", "GORBA EPIC 2026") in view.calls
    assert ("set_state", RideStatus.RUNNING) in view.calls
    swapped = next(arg for name, arg in view.calls if name == "set_presenter")
    assert isinstance(swapped, ConsolePresenter)
    assert swapped.engine.state is RideStatus.RUNNING


def test_run_launch_flow_replay_failure_clears_marker_shows_error_and_keeps_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A drifted-roster replay names the ride and keeps the app up.

    The regression behind the workstream: replay of an audit stream
    that no longer matches the ride raises ``RideEngineError`` out of
    ``Store.load_engine``; before the fix the raise escaped the
    bootstrap and every relaunch re-triggered it invisibly.
    """
    db_path = tmp_path / "rides.db"
    _stage_resumed_ride(db_path, corrupt_replay=True)
    store = Store.open(db_path)
    view = _FakeConsoleView()
    frame = _FakeFrame()
    context = _context(store=store, view=view, frame=frame)
    monkeypatch.setattr(app_module, "_run_resume_dialog", lambda _c, _s, _p: "continue")
    shown: list[tuple[object, ...]] = []
    monkeypatch.setattr(std_dialogs, "show_error", lambda *args: shown.append(args))
    expected = (
        "cannot resume ride GORBA EPIC 2026: no crossing with entry_id 12 seq 1 for confirm_held"
    )
    try:
        app_module._run_launch_flow(context, store)
    finally:
        store.close()

    assert context.active_ride_id is None
    assert _latest_session_active_ride(db_path) is None
    assert view.calls == []  # the placeholder console never swapped
    assert frame.notices == [f"Could not resume ride: {expected}"]
    assert shown == [(frame, "Cannot Resume Ride", expected)]


# -------------------------------------------------- Library path


def test_run_launch_flow_library_defers_open_library_and_never_prompts_no_ride(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Open library defers ride_library_dlg; no no-ride alert.

    The launch already answered its question.
    """
    db_path = tmp_path / "rides.db"
    _stage_resumed_ride(db_path)
    store = Store.open(db_path)
    context = _context(store=store)
    fake_wx = _FakeWx()
    monkeypatch.setattr(app_module, "require_wx", lambda: fake_wx)
    monkeypatch.setattr(app_module, "_run_resume_dialog", lambda _c, _s, _p: "library")
    shown: list[tuple[object, ...]] = []
    monkeypatch.setattr(std_dialogs, "show_info", lambda *args: shown.append(args))
    opened: list[tuple[object, object]] = []
    monkeypatch.setattr(app_module, "_open_target", lambda ctx, route: opened.append((ctx, route)))
    try:
        app_module._run_launch_flow(context, store)

        deferred = fake_wx.calls[0][0]
        deferred()  # the CallAfter fires once the loop runs
    finally:
        store.close()

    assert shown == []  # the launch already answered its question
    assert opened == [(context, commands.route_for_id("mi_open_library"))]
    assert opened[0][1].target == ids.RIDE_LIBRARY_DLG


def test_run_launch_flow_given_a_resume_warrant_and_no_loadable_dialog_does_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing resume dialog posts nothing further (XRC authored)."""
    db_path = tmp_path / "rides.db"
    _stage_resumed_ride(db_path)
    store = Store.open(db_path)
    context = _context(store=store)
    monkeypatch.setattr(app_module, "_run_resume_dialog", lambda _c, _s, _p: None)
    shown: list[tuple[object, ...]] = []
    monkeypatch.setattr(std_dialogs, "show_info", lambda *args: shown.append(args))
    try:
        app_module._run_launch_flow(context, store)
    finally:
        store.close()

    assert shown == []
    assert context.active_ride_id is None
