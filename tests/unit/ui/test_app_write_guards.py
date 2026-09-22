# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for app.py's store- and settings-write guards.

Store writes (``create_ride``/``save_roster``/``delete_ride``/
``duplicate_ride``/``close_session`` and the ``on_event`` append
sink) and settings writes run inside wx menu/button/sash handlers,
and wx swallows a Python exception raised inside an event handler
(measured) -- so an unguarded
write failure vanishes with zero signal, and worst case the operator
is told an import succeeded while the roster silently stayed
unpersisted. Each write route below wraps its store call in
``try/except (OSError, sqlite3.Error)`` and posts a status notice,
the app's own notice idiom (``_handle_backup_database``). The W10
delete refusal is the exception: the library is a modal, so its
status bar is hidden behind it and a refused delete shows an error
dialog above the library window instead
(``std_dialogs.show_error``, stubbed here) -- plus ``StoreError`` is
caught too, so a ``RideRunningError``/``RideNotFoundError`` refusal
never escapes silently. This module drives every guard headless
with a notice-capturing frame stub and failing-store fakes -- no wx
window is constructed.

The E5.4.1 library Open joins them: it is the deferred
``wx.CallAfter`` the library schedules inside its own modal unwind,
so a refused console switch (``RideEngineError``/``StoreError``)
would reach the crash hook as a false crash. Its guard wraps the
switch in the ride-named message the resume flow's Continue uses,
posts it, alerts through ``std_dialogs.show_error`` and clears the
session marker.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple

import pytest
import wx.xrc

from rivercrossing.ride import Event, RideEngineError, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.store import (
    RideNameMismatchError,
    RideNotFoundError,
    RideRunningError,
    Store,
    StoreError,
)
from rivercrossing.ui import app as app_module
from rivercrossing.ui import commands, ids, std_dialogs
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EngineDataSource, RideSummary
from rivercrossing.ui.presenters.settings import AppSettings, default_settings
from rivercrossing.ui.presenters.simulator import SimOutcome, SimulatorPresenter

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class _NoticeFrame:
    """A minimal frame: status notices are captured, nothing else."""

    def __init__(self) -> None:
        """Start with no notices."""
        self.notices: list[str] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record *text* as the latest notice."""
        self.notices.append(text)


class _RideRow:
    """A minimal store ``rides()`` row: the fields these routes read."""

    def __init__(self, *, ride_id: int, name: str) -> None:
        """Store the row's id and name."""
        self.id = ride_id
        self.name = name


def _context(*, store: object, frame: object | None = None) -> app_module._RouteContext:
    """Build a route context carrying *store* and a notice frame."""
    return app_module._RouteContext(
        frame=frame if frame is not None else _NoticeFrame(),
        resource=None,
        roster=Roster(),
        app=None,
        theme_controller=None,
        store=store,
    )


class _RecordsDeleteStore:
    """A store that records every ``delete_ride`` call (W10 by-id seam).

    ``rides()`` raises: the by-id delete callback must never scan rows
    to resolve the ride -- the selected row already carries the id.
    """

    def __init__(self) -> None:
        """Start with an empty delete log."""
        self.calls: list[tuple[int, str]] = []

    def rides(self) -> list[_RideRow]:
        """Refuse the scan a by-id delete does not need."""
        raise AssertionError("a by-id delete must not read rides()")

    def delete_ride(self, ride_id: int, typed_name: str) -> None:
        """Record the exact delete the callback requested."""
        self.calls.append((ride_id, typed_name))


class _DuplicateFailsStore:
    """A store whose ride duplicate fails like a full disk."""

    def duplicate_ride(self, _ride_id: int) -> int:
        """Refuse the write."""
        raise OSError("disk full")


class _MenuDuplicateFailsStore(_DuplicateFailsStore):
    """Adds the ``rides()`` lookup the File ▸ Duplicate route needs."""

    def rides(self) -> list[_RideRow]:
        """Return the one ride whose name the confirm dialog shows."""
        return [_RideRow(ride_id=3, name="Ride A")]


class _SaveRosterFailsStore:
    """A store whose roster save fails like a full disk."""

    def save_roster(self, _ride_id: int, _roster: object) -> None:
        """Refuse the write."""
        raise OSError("disk full")


class _CloseSessionFailsStore:
    """A store whose session stamp fails like a locked database."""

    def close_session(self) -> None:
        """Refuse the write."""
        raise sqlite3.OperationalError("database is locked")


class _CreateFailsStore:
    """A store whose ride create is refused like a locked database."""

    def create_ride(self, _config: object) -> int:
        """Refuse the write."""
        raise sqlite3.OperationalError("database is locked")

    def save_roster(self, _ride_id: int, _roster: object) -> None:
        """Raise if the guard lets the flow past the failed create."""
        raise AssertionError("save_roster must not run after a failed create")

    def set_active_ride(self, _ride_id: int) -> None:
        """Raise if the guard lets the flow past the failed create."""
        raise AssertionError("set_active_ride must not run after a failed create")


class _CreateOkRosterSaveFailsStore:
    """A store whose ride create succeeds but roster save fails."""

    def create_ride(self, _config: object) -> int:
        """Succeed at creating the ride row."""
        return 41

    def save_roster(self, _ride_id: int, _roster: object) -> None:
        """Refuse the write."""
        raise OSError("disk full")

    def set_active_ride(self, _ride_id: int) -> None:
        """Raise if the guard lets the flow past the failed save."""
        raise AssertionError("set_active_ride must not run after a failed save")


class _CallAfterRecorder:
    """Record every deferred call without constructing any GUI."""

    def __init__(self) -> None:
        """Start with an empty schedule log."""
        self.calls: list[tuple[object, tuple[object, ...]]] = []

    def CallAfter(self, callable_: object, *args: object) -> None:  # noqa: N802 -- wx API name
        """Record one deferred call."""
        self.calls.append((callable_, args))


# --------------------------------------------- store-write route guards


def test_persist_created_ride_given_a_failed_create_posts_a_notice_and_schedules_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused ride create surfaces; no ride is switched onto."""
    from conftest import gorba_config  # noqa: PLC0415 -- the shared live-config fixture

    context = _context(store=_CreateFailsStore())
    recorder = _CallAfterRecorder()
    monkeypatch.setattr(app_module, "require_wx", lambda: recorder)

    app_module._persist_created_ride(context, gorba_config())

    assert context.frame.notices == ["Could not create ride: database is locked"]
    assert recorder.calls == []


def test_persist_created_ride_given_a_failed_roster_save_posts_a_notice_and_schedules_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused roster save leaves the new ride unswitched."""
    from conftest import gorba_config  # noqa: PLC0415 -- the shared live-config fixture

    context = _context(store=_CreateOkRosterSaveFailsStore())
    recorder = _CallAfterRecorder()
    monkeypatch.setattr(app_module, "require_wx", lambda: recorder)

    app_module._persist_created_ride(context, gorba_config())

    assert context.frame.notices == ["Could not save riders: disk full"]
    assert recorder.calls == []


def _selected_ride(*, ride_id: int = 3, name: str = "Ride A") -> RideSummary:
    """One library row as the delete seam receives it (W10)."""
    return RideSummary(
        name=name, date="2026-09-20", status=RideStatus.DRAFT, entries=0, ride_id=ride_id
    )


def test_library_delete_callback_given_a_selected_ride_deletes_that_exact_ride() -> None:
    """The seam hands the selected row's id to ``Store.delete_ride``.

    No name-resolution scan over ``rides()``: the row the operator
    confirmed carries the ``ride_id``, so ``delete_ride`` addresses
    the exact row -- duplicate ride names can no longer delete the
    wrong (first) match.
    """
    store = _RecordsDeleteStore()
    context = _context(store=store)

    callback = app_module._library_delete_callback(context, window=_NoticeFrame())
    assert callback is not None
    callback(_selected_ride())

    assert store.calls == [(3, "Ride A")]


def test_library_delete_callback_without_a_store_returns_none() -> None:
    """No store open: there is nothing to delete -- no callback."""
    context = _context(store=None)

    callback = app_module._library_delete_callback(context, window=_NoticeFrame())

    assert callback is None


def test_library_delete_callback_given_a_row_without_a_ride_id_is_a_noop() -> None:
    """E5.4.2 demo-era rows carry no store id -- nothing to delete."""
    store = _RecordsDeleteStore()
    context = _context(store=store)

    callback = app_module._library_delete_callback(context, window=_NoticeFrame())
    assert callback is not None
    callback(_selected_ride(ride_id=None))

    assert store.calls == []


class _RaisesDeleteStore:
    """A store whose delete refuses with one pinned exception."""

    def __init__(self, failure: Exception) -> None:
        """Store the refusal *failure* to raise on delete."""
        self._failure = failure

    def delete_ride(self, _ride_id: int, _typed_name: str) -> None:
        """Refuse the write with the pinned failure."""
        raise self._failure


_DELETE_REFUSAL_CASES = (
    (
        sqlite3.OperationalError("database is locked"),
        "Could not delete ride: database is locked",
    ),
    (OSError("disk full"), "Could not delete ride: disk full"),
    (
        RideRunningError("ride 3 is RUNNING and cannot be deleted"),
        "The ride is running, so it cannot be deleted. Finish the ride first.",
    ),
    (
        RideNotFoundError("no ride with id 3"),
        "The ride is no longer in the library.",
    ),
    (
        RideNameMismatchError("typed name 'Ride A' does not match ride 3 name 'Ride B'"),
        (
            "The ride's name changed since the library opened."
            " Close and reopen the library, then try again."
        ),
    ),
)


@pytest.mark.parametrize(("failure", "expected_text"), _DELETE_REFUSAL_CASES)
def test_delete_refusal_text_given_each_refusal_returns_plain_copy(
    failure: Exception, expected_text: str
) -> None:
    """UX-DESKTOP §9: no row ids or stored-status words reach the UI."""
    assert app_module._delete_refusal_text(failure) == expected_text


@pytest.mark.parametrize(("failure", "expected_message"), _DELETE_REFUSAL_CASES)
def test_library_delete_callback_given_a_refused_delete_shows_an_error_dialog(
    failure: Exception,
    expected_message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused delete surfaces in an error dialog, never silently.

    The callback runs from the library's delete-confirm handler; the
    refusal shows an error dialog parented to the library window,
    whose status bar the modal hides -- not an invisible notice.
    """
    shown: list[tuple[object, str, str]] = []
    monkeypatch.setattr(
        std_dialogs,
        "show_error",
        lambda parent, title, message: shown.append((parent, title, message)),
    )
    window = _NoticeFrame()
    context = _context(store=_RaisesDeleteStore(failure))

    callback = app_module._library_delete_callback(context, window=window)
    assert callback is not None
    callback(_selected_ride())

    assert shown == [(window, "Could Not Delete Ride", expected_message)]


def test_live_library_duplicate_given_a_failed_duplicate_posts_a_notice() -> None:
    """A refused duplicate surfaces on the status bar."""
    context = _context(store=_DuplicateFailsStore())
    _open, duplicate = app_module._live_library_callbacks(
        context, window=object(), store=_DuplicateFailsStore()
    )
    selected = RideSummary(
        name="Ride A", date="2026-09-20", status=RideStatus.DRAFT, entries=0, ride_id=3
    )

    duplicate(selected)

    assert context.frame.notices == ["Could not duplicate ride: disk full"]


def test_handle_duplicate_ride_route_given_a_failed_duplicate_posts_a_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """File ▸ Duplicate Ride…'s refused write surfaces, never raises."""
    context = _context(store=_MenuDuplicateFailsStore())
    context.active_ride_id = 3
    monkeypatch.setattr(app_module, "_open_ride_confirm", lambda _ctx, _dlg, _msg: True)

    app_module._handle_duplicate_ride_route(context)

    assert context.frame.notices == ["Could not duplicate ride: disk full"]


def test_handle_import_csv_given_a_failed_roster_save_posts_a_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A committed import whose save fails never reports success."""
    from rivercrossing.ui.views import rider_editor  # noqa: PLC0415 -- the flow seam

    context = _context(store=_SaveRosterFailsStore())
    context.active_ride_id = 5
    monkeypatch.setattr(rider_editor, "run_csv_import_flow", lambda _f, _r: True)

    app_module._handle_import_csv(context)

    assert context.frame.notices == ["Could not save riders: disk full"]


def test_handle_check_rider_issues_given_a_failed_roster_save_posts_a_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A converted issue whose save fails surfaces, never raises."""
    from rivercrossing.ui.views import rider_issues  # noqa: PLC0415 -- the flow seam

    context = _context(store=_SaveRosterFailsStore())
    context.active_ride_id = 5
    monkeypatch.setattr(rider_issues, "run_rider_issues_flow", lambda _f, _r, **_kwargs: True)

    app_module._handle_check_rider_issues(context)

    assert context.frame.notices == ["Could not save riders: disk full"]


def test_stamp_closed_session_given_a_failed_session_close_posts_a_notice() -> None:
    """A refused ``closed_at`` stamp surfaces; the quit proceeds."""
    context = _context(store=_CloseSessionFailsStore())

    app_module._stamp_closed_session(context)

    assert context.frame.notices == ["Could not close session: database is locked"]


# --------------------- E5.4.1: the library Open's replay-failure guard
#
# ``_open_ride_from_library`` is the deferred ``wx.CallAfter`` the ride
# library's Open schedules from inside the modal's own unwind, so an
# unguarded raise from the console switch reaches the crash hook as a
# false crash. The guard is the resume flow's Continue guard
# (``_resume_continue``) on the library's own message: the ride-named
# wrap, the status notice, the error alert and the cleared session
# marker. These tests drive it with fake stores, a recording
# ``_switch_console_to_ride`` double and a spy on
# ``std_dialogs.show_error`` (T-10: wx and the alert seam are the GUI
# I/O boundary) -- no window is constructed.


class _SwitchConsoleToRide:
    """A ``_switch_console_to_ride`` stand-in: records, may refuse."""

    def __init__(self, failure: Exception | None = None) -> None:
        """Start with an empty call log, refusing with *failure*."""
        self.calls: list[tuple[object, int]] = []
        self._failure = failure

    def __call__(self, context: object, ride_id: int) -> None:
        """Record the switch, then raise the pinned refusal (if any)."""
        self.calls.append((context, ride_id))
        if self._failure is not None:
            raise self._failure


class _OpenRideStore:
    """A store double for the Open guard: the rows, the marker clear."""

    def __init__(self, *rows: _RideRow) -> None:
        """Hold the library rows the failure's name lookup scans."""
        self._rows = list(rows)
        self.clears = 0

    def rides(self) -> list[_RideRow]:
        """Return the library rows."""
        return self._rows

    def clear_active_ride(self) -> None:
        """Record one session-marker clear."""
        self.clears += 1


def _open_ride_doubles(
    monkeypatch: pytest.MonkeyPatch,
    store: _OpenRideStore,
    failure: Exception | None = None,
) -> tuple[app_module._RouteContext, _SwitchConsoleToRide, list[tuple[object, str, str]]]:
    """Patch the switch and alert seams; return the three doubles."""
    switch = _SwitchConsoleToRide(failure)
    shown: list[tuple[object, str, str]] = []
    monkeypatch.setattr(app_module, "_switch_console_to_ride", switch)
    monkeypatch.setattr(
        std_dialogs,
        "show_error",
        lambda parent, title, message: shown.append((parent, title, message)),
    )
    return _context(store=store), switch, shown


# Both arms of the guard's ``except (RideEngineError, StoreError)``.
_OPEN_REFUSAL_FAILURES = (
    RideEngineError("no crossing with entry_id 12 seq 1 for confirm_held"),
    StoreError("ride 7 has an invalid tiebreak_order: 'nope'"),
)


@pytest.mark.parametrize(
    "failure",
    _OPEN_REFUSAL_FAILURES,
    ids=["ride-engine-error", "store-error"],
)
def test_open_ride_from_library_given_a_refused_load_surfaces_it_and_clears_the_marker(
    failure: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused Open names the ride, alerts, and clears the marker.

    The message is wrapped at the catch site, so it names the ride as
    well as the offending event the replay error already names; the
    console stays where it was (the switch raised).
    """
    store = _OpenRideStore(_RideRow(ride_id=7, name="GORBA EPIC 2026"))
    context, switch, shown = _open_ride_doubles(monkeypatch, store, failure)
    expected = f"cannot open ride GORBA EPIC 2026: {failure}"

    app_module._open_ride_from_library(context, store, 7)

    assert switch.calls == [(context, 7)]
    assert context.frame.notices == [f"Could not open ride: {expected}"]
    assert shown == [(context.frame, "Cannot Open Ride", expected)]
    assert store.clears == 1


@pytest.mark.parametrize(
    ("rows", "expected_name"),
    [
        ((), "The ride"),
        ((_RideRow(ride_id=3, name="Another Ride"),), "The ride"),
        (
            (
                _RideRow(ride_id=1, name="First Ride"),
                _RideRow(ride_id=7, name="GORBA EPIC 2026"),
                _RideRow(ride_id=9, name="Third Ride"),
            ),
            "GORBA EPIC 2026",
        ),
    ],
    ids=["no-rows", "other-row-only", "match-among-many"],
)
def test_open_ride_from_library_given_a_refused_load_names_the_ride_from_the_library(
    rows: tuple[_RideRow, ...], expected_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wrap takes the ride's name from the library, else a stand-in.

    The row can be gone from the library (deleted between the modal's
    open and the deferred Open), so the name lookup has a fallback: the
    message is never left naming an empty ride.
    """
    store = _OpenRideStore(*rows)
    failure = RideEngineError("no crossing with entry_id 12 seq 1 for confirm_held")
    context, _switch, shown = _open_ride_doubles(monkeypatch, store, failure)
    expected = f"cannot open ride {expected_name}: {failure}"

    app_module._open_ride_from_library(context, store, 7)

    assert shown == [(context.frame, "Cannot Open Ride", expected)]
    assert context.frame.notices == [f"Could not open ride: {expected}"]


def test_open_ride_from_library_given_a_loadable_ride_switches_the_console_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path swaps the console: nothing is surfaced."""
    store = _OpenRideStore(_RideRow(ride_id=7, name="GORBA EPIC 2026"))
    context, switch, shown = _open_ride_doubles(monkeypatch, store)

    app_module._open_ride_from_library(context, store, 7)

    assert switch.calls == [(context, 7)]
    assert context.frame.notices == []
    assert shown == []
    assert store.clears == 0


# -------------------------------------- settings-write route guards


def test_save_layout_settings_given_an_unwritable_settings_file_posts_a_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused layout save surfaces; the in-memory settings stay put.

    ``persist_layout`` fires from the sash/move/size/close handlers;
    an unguarded raise there is swallowed by wx and, on close, stalls
    ``event.Skip()`` -- the window never quits and nothing is said.
    """
    context = _context(store=None)
    context.settings_path = tmp_path / "settings.json"
    settings_before = context.settings

    def _save_that_fails(_settings: object, _path: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(app_module.settings_store, "save_settings", _save_that_fails)

    app_module._save_layout_settings(context, sash=111, geometry=(1, 2, 3, 4))

    assert context.frame.notices == ["Could not save settings: disk full"]
    assert context.settings is settings_before


def test_save_layout_settings_given_a_writable_file_persists_and_updates_context(
    tmp_path: Path,
) -> None:
    """A successful layout save writes the file and updates settings."""
    context = _context(store=None)
    context.settings_path = tmp_path / "settings.json"

    app_module._save_layout_settings(context, sash=222, geometry=(5, 6, 7, 8))

    assert context.frame.notices == []
    assert context.settings.splitter_sash == 222
    assert context.settings.window_geometry == (5, 6, 7, 8)
    assert (tmp_path / "settings.json").exists()


def test_apply_settings_live_given_an_unwritable_settings_file_posts_a_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused settings-dialog save posts a notice and still applies.

    The dialog's OK handler runs this; an unguarded raise would be
    swallowed by wx with the dialog already closed and nothing said.
    """

    class _StubMenuItem:
        """Record the enable verdict the R-63 publish gate applies."""

        def __init__(self) -> None:
            """Start with no recorded verdict."""
            self.enabled: bool | None = None

        def Enable(self, enabled: bool) -> None:  # noqa: N802, FBT001 -- wx API name
            """Record the verdict."""
            self.enabled = enabled

    class _StubMenubar:
        """Record every radio-check call the apply path makes."""

        def __init__(self) -> None:
            """Start with an empty check log and one item double."""
            self.checks: list[tuple[int, bool]] = []
            self.item = _StubMenuItem()

        # mirrors wx MenuBar.Check's positional bool
        def Check(self, item_id: int, checked: bool) -> None:  # noqa: N802, FBT001
            """Record one check call."""
            self.checks.append((item_id, checked))

        def FindItem(  # noqa: N802 -- wx API name
            self, _real_id: int
        ) -> tuple[_StubMenuItem, None]:
            """Answer the one item the publish gate looks up (G6)."""
            return self.item, None

    class _SettingsFrame(_NoticeFrame):
        """A notice frame that also answers GetMenuBar."""

        def __init__(self) -> None:
            """Start with a recording menubar."""
            super().__init__()
            self.menubar = _StubMenubar()

        def GetMenuBar(self) -> _StubMenubar:  # noqa: N802 -- wx API name
            """Return the recording menubar."""
            return self.menubar

    class _FakeThemeController:
        """A theme controller reporting no notice."""

        def apply_mode(self, _mode: object) -> None:
            """Report no notice."""

    context = app_module._RouteContext(
        frame=_SettingsFrame(),
        resource=None,
        roster=Roster(),
        app=None,
        theme_controller=_FakeThemeController(),
        store=None,
    )
    context.settings_path = tmp_path / "settings.json"
    new_settings = AppSettings(
        appearance="system",
        sound_on=True,
        show_total_times=False,
        show_lap_time=True,
        zoom_percent=context.settings.zoom_percent,
    )

    def _save_that_fails(_settings: object, _path: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(app_module.settings_store, "save_settings", _save_that_fails)

    app_module._apply_settings_live(context, new_settings)

    assert context.frame.notices == ["Could not save settings: disk full"]
    assert context.settings is new_settings


class _ColumnMenuBar:
    """A menubar double answering the R-37 / G6 check calls."""

    def __init__(self) -> None:
        """Start with no checked item."""
        self.checked: dict[int, bool] = {}

    def Check(self, real_id: int, checked: bool) -> None:  # noqa: N802, FBT001 -- wx API name
        """Record one check call by the item's runtime id."""
        self.checked[real_id] = checked

    def FindItem(  # noqa: N802 -- wx API name
        self, _real_id: int
    ) -> tuple[None, None]:
        """Answer the miss R-63's gate skips on."""
        return (None, None)


class _MenuNoticeFrame(_NoticeFrame):
    """A notice frame answering ``GetMenuBar`` with a column bar."""

    def __init__(self) -> None:
        """Start with a recording bar."""
        super().__init__()
        self.menubar = _ColumnMenuBar()

    def GetMenuBar(self) -> _ColumnMenuBar:  # noqa: N802 -- wx API name
        """Return the recording bar."""
        return self.menubar


class _ColumnViewStub:
    """A console-view double recording the R-37 column push."""

    def __init__(self) -> None:
        """Start with an empty push log."""
        self.calls: list[tuple[bool, bool]] = []

    def set_time_columns(self, *, show_total: bool, show_lap: bool) -> None:
        """Record the two show flags."""
        self.calls.append((show_total, show_lap))


class _ViewEvent:
    """A ``wx.CommandEvent`` double carrying only the fired id."""

    def __init__(self, real_id: int) -> None:
        """Store the runtime id the handler dispatches on."""
        self._real_id = real_id

    def GetId(self) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Return the fired id."""
        return self._real_id


def _refuse_save(_settings: object, _path: object) -> None:
    """Stand in for an unwritable settings file (a full disk)."""
    raise OSError("disk full")


# T-13: the two time columns are independent booleans, so the apply's
# decision table is all four combinations.
COLUMN_FLAG_ROWS = ((False, False), (False, True), (True, False), (True, True))


def test_toggle_time_column_given_a_refused_save_posts_a_notice_and_still_flips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-37: a refused settings write never aborts the menu toggle.

    ``_toggle_time_column`` runs inside a wx menu event, where an
    unguarded raise is swallowed with zero signal (measured) -- so the
    column would silently not toggle and nothing would be said.
    """
    frame = _MenuNoticeFrame()
    context = _context(store=None, frame=frame)
    context.settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(app_module.settings_store, "save_settings", _refuse_save)

    app_module._toggle_time_column(context, key="show_total_times")

    assert context.frame.notices == ["Could not save settings: disk full"]
    assert context.settings.show_total_times is True
    assert frame.menubar.checked == {wx.xrc.XRCID(ids.MI_SHOW_TOTAL_TIMES): True}


def test_toggle_publish_option_given_a_refused_save_posts_a_notice_and_still_flips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G6: a refused publish write never aborts the menu toggle."""
    frame = _MenuNoticeFrame()
    context = _context(store=None, frame=frame)
    context.settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(app_module.settings_store, "save_settings", _refuse_save)

    app_module._toggle_publish_option(context, key="publish_laps_board")

    assert context.frame.notices == ["Could not save settings: disk full"]
    assert context.settings.publish_laps_board is False
    assert frame.menubar.checked == {wx.xrc.XRCID(ids.MI_LAPS_BOARD): False}


def test_handle_view_row_given_a_refused_zoom_save_posts_a_notice_and_still_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E8.1.4: a refused zoom write never aborts the radio's apply."""
    frame = _MenuNoticeFrame()
    context = _context(store=None, frame=frame)
    context.settings_path = tmp_path / "settings.json"
    route = commands.route_for_id(ids.MI_ZOOM_120)
    applied: list[int] = []
    monkeypatch.setattr(app_module.zoom, "set_percent", applied.append)
    monkeypatch.setattr(app_module.settings_store, "save_settings", _refuse_save)

    app_module._handle_view_row(context, route, _ViewEvent(wx.xrc.XRCID(ids.MI_ZOOM_120)))

    assert context.frame.notices == ["Could not save settings: disk full"]
    assert (applied, context.settings.zoom_percent) == ([120], 120)
    assert frame.menubar.checked == {wx.xrc.XRCID(ids.MI_ZOOM_120): True}


@pytest.mark.parametrize("flags", COLUMN_FLAG_ROWS)
def test_apply_time_columns_given_a_console_view_pushes_the_live_settings(
    flags: tuple[bool, bool],
) -> None:
    """R-37: the no-presenter paths push the columns onto the view.

    The no-ride bootstrap and Ride ▸ Clear Ride… have no presenter to
    call ``on_time_columns`` on, so the persisted choice reaches the
    console through its own ``set_time_columns``. T-13: both flags are
    independent, so all four combinations are driven.
    """
    show_total, show_lap = flags
    view = _ColumnViewStub()
    context = _context(store=None)
    context.console_view = view
    context.settings = replace(
        default_settings(), show_total_times=show_total, show_lap_time=show_lap
    )

    app_module._apply_time_columns(context)

    assert view.calls == [(show_total, show_lap)]


def test_apply_time_columns_given_no_console_view_applies_nothing() -> None:
    """T-3: a console-less context (route-level tests) applies none."""
    context = _context(store=None)

    app_module._apply_time_columns(context)

    assert context.settings == default_settings()


# ------------------------ W7: rider editor close persists its changes


class _EditorPresenterStub:
    """A presenter-shaped stub: only the change flag the save reads."""

    def __init__(self, *, roster_changed: bool) -> None:
        """Store the flag the editor's close-save consults."""
        self.roster_changed = roster_changed


class _EditorViewStub:
    """A RiderEditor-shaped stub answering the close-save's queries."""

    def __init__(self, *, roster_changed: bool) -> None:
        """Hold a presenter stub carrying *roster_changed*."""
        self.presenter = _EditorPresenterStub(roster_changed=roster_changed)

    def select_rider_by_plate(self, _plate: str) -> None:
        """No-op: the route-level test never drives a real list."""


class _SimulatorViewStub:
    """A SimulatorDialog-shaped stub: the flag, spins and behaviours."""

    def __init__(
        self,
        *,
        roster_changed: bool,
        sim_values: tuple[int, int, int, int, int] = (10, 2, 2, 1, 1),
        sim_behaviors: tuple[int, int, int] = (1, 0, 0),
    ) -> None:
        """Hold a presenter stub and the values the close persists."""
        self.presenter = _EditorPresenterStub(roster_changed=roster_changed)
        self.sim_values = sim_values
        self.sim_behaviors = sim_behaviors


class _FakeWindow:
    """A wx-window-shaped stub: loadable, closable, nothing else."""

    def IsBeingDeleted(self) -> bool:  # noqa: N802 -- wx API name
        """Report this stub is never mid-delete."""
        return False

    def Destroy(self) -> None:  # noqa: N802 -- wx API name
        """No-op: there is no real window to destroy."""


class _FakeResource:
    """A resource-shaped stub returning the one fake window."""

    def __init__(self, window: _FakeWindow) -> None:
        """Store the window every LoadDialog call returns."""
        self.window = window

    # wx API name
    def LoadDialog(self, _parent: object, _name: object) -> _FakeWindow:  # noqa: N802
        """Return the one fake window."""
        return self.window


class _SaveRecorderStore:
    """A store recording roster saves instead of writing them."""

    def __init__(self) -> None:
        """Start with no saved rides."""
        self.saved: list[tuple[int, object]] = []

    def save_roster(self, ride_id: int, roster: object) -> None:
        """Record one save call."""
        self.saved.append((ride_id, roster))


class _SaveMustNotRunStore:
    """A store that fails loudly if save_roster is ever called."""

    def save_roster(self, _ride_id: int, _roster: object) -> None:
        """Raise: an unchanged editor must never persist."""
        raise AssertionError("save_roster must not run without a change")


def test_persist_rider_editor_changes_given_a_failed_save_posts_a_notice() -> None:
    """The editor close-save refuses like every other roster save."""
    context = _context(store=_SaveRosterFailsStore())
    context.active_ride_id = 5
    roster = Roster()
    context.roster = roster

    app_module._persist_rider_editor_changes(context, _EditorViewStub(roster_changed=True))

    assert context.frame.notices == ["Could not save riders: disk full"]


def test_persist_rider_editor_changes_given_no_change_is_a_silent_no_op() -> None:
    """A clean editor session never touches the store (W7)."""
    context = _context(store=_SaveMustNotRunStore())
    context.active_ride_id = 5

    app_module._persist_rider_editor_changes(context, _EditorViewStub(roster_changed=False))

    assert context.frame.notices == []


def test_persist_rider_editor_changes_given_no_store_is_a_silent_no_op() -> None:
    """A bootstrap (store-less) editor session never touches a store."""
    context = _context(store=None)
    context.active_ride_id = None

    app_module._persist_rider_editor_changes(context, _EditorViewStub(roster_changed=True))

    assert context.frame.notices == []


def test_open_target_given_rider_editor_close_with_changes_saves_the_roster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The menu route persists a changed editor when its modal ends."""
    store = _SaveRecorderStore()
    context = _context(store=store)
    context.active_ride_id = 5
    roster = Roster()
    context.roster = roster
    window = _FakeWindow()
    context.resource = _FakeResource(window)
    changed_view = _EditorViewStub(roster_changed=True)
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(app_module, "_decorate", lambda _ctx, _w, _route: changed_view)
    monkeypatch.setattr(app_module, "_apply_dialog_defaults", lambda _w, _route: None)
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- the patched modal seam

    # the SUT calls opener=; the stub ignores it
    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005

    app_module._open_target(context, app_module.commands.route_for_id("mi_rider_editor"))

    assert store.saved == [(5, roster)]


def test_open_target_given_rider_editor_close_without_changes_skips_the_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unchanged menu-route editor session saves nothing."""
    context = _context(store=_SaveMustNotRunStore())
    context.active_ride_id = 5
    context.roster = Roster()
    context.resource = _FakeResource(_FakeWindow())
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(
        app_module, "_decorate", lambda _ctx, _w, _route: _EditorViewStub(roster_changed=False)
    )
    monkeypatch.setattr(app_module, "_apply_dialog_defaults", lambda _w, _route: None)
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- the patched modal seam

    # the SUT calls opener=; the stub ignores it
    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005

    app_module._open_target(context, app_module.commands.route_for_id("mi_rider_editor"))

    assert context.frame.notices == []


def test_open_target_given_rider_editor_close_and_a_failed_save_posts_a_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused close-save surfaces on the status bar, never raises."""
    context = _context(store=_SaveRosterFailsStore())
    context.active_ride_id = 5
    context.roster = Roster()
    context.resource = _FakeResource(_FakeWindow())
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(
        app_module, "_decorate", lambda _ctx, _w, _route: _EditorViewStub(roster_changed=True)
    )
    monkeypatch.setattr(app_module, "_apply_dialog_defaults", lambda _w, _route: None)
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- the patched modal seam

    # the SUT calls opener=; the stub ignores it
    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005

    app_module._open_target(context, app_module.commands.route_for_id("mi_rider_editor"))

    assert context.frame.notices == ["Could not save riders: disk full"]


def test_open_rider_editor_for_close_with_changes_saves_the_roster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console Riders-tab path persists a changed editor too."""
    from rivercrossing.ui.views import rider_editor  # noqa: PLC0415 -- the patched view class

    store = _SaveRecorderStore()
    context = _context(store=store)
    context.active_ride_id = 5
    roster = Roster()
    context.roster = roster
    context.resource = _FakeResource(_FakeWindow())
    changed_view = _EditorViewStub(roster_changed=True)
    monkeypatch.setattr(
        rider_editor,
        "RiderEditor",
        # the SUT calls roster= and engine=; the stub ignores both
        lambda _window, *, roster, engine: changed_view,  # noqa: ARG005
    )
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(app_module, "_apply_dialog_defaults", lambda _w, _route: None)
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- the patched modal seam

    # the SUT calls opener=; the stub ignores it
    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005

    app_module._open_rider_editor_for(context, "77")

    assert store.saved == [(5, roster)]


def test_open_rider_editor_for_close_without_changes_skips_the_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unchanged console-path editor session saves nothing (W7)."""
    from rivercrossing.ui.views import rider_editor  # noqa: PLC0415 -- the patched view class

    context = _context(store=_SaveMustNotRunStore())
    context.active_ride_id = 5
    context.roster = Roster()
    context.resource = _FakeResource(_FakeWindow())
    monkeypatch.setattr(
        rider_editor,
        "RiderEditor",
        # the SUT calls roster= and engine=; the stub ignores both
        lambda _window, *, roster, engine: _EditorViewStub(  # noqa: ARG005
            roster_changed=False
        ),
    )
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(app_module, "_apply_dialog_defaults", lambda _w, _route: None)
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- the patched modal seam

    # the SUT calls opener=; the stub ignores it
    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005

    app_module._open_rider_editor_for(context, "77")

    assert context.frame.notices == []


# ------------------------ W8: team editor close persists its changes


def test_persist_team_editor_changes_given_a_failed_save_posts_a_notice() -> None:
    """The team editor's close-save refuses like the rider editor's."""
    context = _context(store=_SaveRosterFailsStore())
    context.active_ride_id = 5
    context.roster = Roster()

    app_module._persist_team_editor_changes(context, _EditorViewStub(roster_changed=True))

    assert context.frame.notices == ["Could not save teams: disk full"]


def test_persist_team_editor_changes_given_no_change_is_a_silent_no_op() -> None:
    """A clean team-editor session never touches the store (W8)."""
    context = _context(store=_SaveMustNotRunStore())
    context.active_ride_id = 5

    app_module._persist_team_editor_changes(context, _EditorViewStub(roster_changed=False))

    assert context.frame.notices == []


def test_persist_team_editor_changes_given_no_store_is_a_silent_no_op() -> None:
    """A store-less bootstrap session never touches a store."""
    context = _context(store=None)
    context.active_ride_id = None

    app_module._persist_team_editor_changes(context, _EditorViewStub(roster_changed=True))

    assert context.frame.notices == []


def test_open_target_given_team_editor_close_with_changes_saves_the_roster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The menu route persists a changed editor once its modal ends."""
    store = _SaveRecorderStore()
    context = _context(store=store)
    context.active_ride_id = 5
    roster = Roster()
    context.roster = roster
    context.resource = _FakeResource(_FakeWindow())
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(
        app_module, "_decorate", lambda _ctx, _w, _route: _EditorViewStub(roster_changed=True)
    )
    monkeypatch.setattr(app_module, "_apply_dialog_defaults", lambda _w, _route: None)
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- the patched modal seam

    # the SUT calls opener=; the stub ignores it
    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005

    app_module._open_target(context, app_module.commands.route_for_id("mi_team_editor"))

    assert store.saved == [(5, roster)]


def test_open_target_given_team_editor_close_without_changes_skips_the_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unchanged team-editor session saves nothing (W8)."""
    context = _context(store=_SaveMustNotRunStore())
    context.active_ride_id = 5
    context.roster = Roster()
    context.resource = _FakeResource(_FakeWindow())
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(
        app_module, "_decorate", lambda _ctx, _w, _route: _EditorViewStub(roster_changed=False)
    )
    monkeypatch.setattr(app_module, "_apply_dialog_defaults", lambda _w, _route: None)
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- the patched modal seam

    # the SUT calls opener=; the stub ignores it
    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005

    app_module._open_target(context, app_module.commands.route_for_id("mi_team_editor"))

    assert context.frame.notices == []


# ---------------- Simulation: simulator close persists its roster


def test_persist_simulator_changes_given_a_failed_save_posts_a_notice(
    tmp_path: Path,
) -> None:
    """The simulator's close-save refuses like the editor's."""
    context = _context(store=_SaveRosterFailsStore())
    context.active_ride_id = 5
    context.roster = Roster()
    context.settings_path = tmp_path / "settings.json"

    app_module._persist_simulator_changes(context, _SimulatorViewStub(roster_changed=True))

    assert context.frame.notices == ["Could not save riders: disk full"]


def test_persist_simulator_changes_given_no_change_is_a_silent_no_op(
    tmp_path: Path,
) -> None:
    """A session that generated nothing never touches the store."""
    context = _context(store=_SaveMustNotRunStore())
    context.active_ride_id = 5
    context.settings_path = tmp_path / "settings.json"

    app_module._persist_simulator_changes(context, _SimulatorViewStub(roster_changed=False))

    assert context.frame.notices == []


def test_persist_simulator_changes_given_no_store_is_a_silent_no_op(
    tmp_path: Path,
) -> None:
    """A store-less bootstrap session never touches a store."""
    context = _context(store=None)
    context.active_ride_id = None
    context.settings_path = tmp_path / "settings.json"

    app_module._persist_simulator_changes(context, _SimulatorViewStub(roster_changed=True))

    assert context.frame.notices == []


def test_persist_simulator_changes_given_a_store_but_no_ride_skips_the_roster_save(
    tmp_path: Path,
) -> None:
    """A generated roster with no open ride is not persisted."""
    context = _context(store=_SaveMustNotRunStore())
    context.active_ride_id = None
    context.settings_path = tmp_path / "settings.json"

    app_module._persist_simulator_changes(context, _SimulatorViewStub(roster_changed=True))

    assert context.frame.notices == []


def test_persist_simulator_changes_carries_the_spin_values_into_settings(
    tmp_path: Path,
) -> None:
    """Plan §1: the closed dialog's spins persist to settings.json."""
    context = _context(store=None)
    context.settings_path = tmp_path / "settings.json"
    view = _SimulatorViewStub(roster_changed=False, sim_values=(37, 6, 5, 4, 9))

    app_module._persist_simulator_changes(context, view)

    assert (
        context.settings.sim_riders,
        context.settings.sim_teams,
        context.settings.sim_solo,
        context.settings.sim_laps,
        context.settings.sim_interval,
    ) == (37, 6, 5, 4, 9)
    assert app_module.settings_store.load_settings(context.settings_path) == context.settings


def test_persist_simulator_changes_carries_the_three_behaviours_into_settings(
    tmp_path: Path,
) -> None:
    """G9: the closed dialog's three behaviour counts persist too."""
    context = _context(store=None)
    context.settings_path = tmp_path / "settings.json"
    view = _SimulatorViewStub(roster_changed=False, sim_behaviors=(3, 7, 10))

    app_module._persist_simulator_changes(context, view)

    assert (
        context.settings.sim_short_laps,
        context.settings.sim_lapped,
        context.settings.sim_team_stop,
    ) == (3, 7, 10)
    assert app_module.settings_store.load_settings(context.settings_path) == context.settings


@pytest.mark.parametrize(
    "error",
    [OSError("disk full"), sqlite3.OperationalError("database is locked")],
)
def test_persist_simulator_changes_given_a_failed_settings_save_posts_a_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """A refused settings write is a notice, keeping the live values."""

    def _fail(_settings: object, _path: object) -> None:
        raise error

    context = _context(store=None)
    context.settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(app_module.settings_store, "save_settings", _fail)

    app_module._persist_simulator_changes(
        context, _SimulatorViewStub(roster_changed=False, sim_values=(1, 2, 3, 4, 5))
    )

    assert context.frame.notices == [f"Could not save settings: {error}"]
    assert context.settings.sim_riders == 1


def test_open_target_given_simulation_close_with_changes_saves_the_roster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The File route persists a changed simulator on close."""
    store = _SaveRecorderStore()
    context = _context(store=store)
    context.active_ride_id = 5
    roster = Roster()
    context.roster = roster
    context.settings_path = tmp_path / "settings.json"
    context.resource = _FakeResource(_FakeWindow())
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(
        app_module,
        "_decorate",
        lambda _ctx, _w, _route: _SimulatorViewStub(roster_changed=True),
    )
    monkeypatch.setattr(app_module, "_apply_dialog_defaults", lambda _w, _route: None)
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- the patched modal seam

    # the SUT calls opener=; the stub ignores it
    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005

    app_module._open_target(context, app_module.commands.route_for_id("mi_simulation"))

    assert store.saved == [(5, roster)]


# ------- Simulation: the post-GO close re-applies the live menu state
#
# Plan §2: the simulator's GO leaves the ride RUNNING (stopped), but
# its §15 menu enablement is computed from the live engine and only
# refreshed on a console ride-state change -- which a simulated GO
# never raises (it drives the engine directly, not through the
# presenter). So Finish Ride / Stop Ride / Undo Last Crossing stay
# disabled after the modal closes. The tests below stage the app's own
# flow headless (real store, store-replayed engine with the store's
# append as its event sink, a real console presenter) and drive the
# close-persist the route runs once the modal has ended. The console
# half is the same defect: no ride-state change fired either, so the
# console kept its pre-GO render (an unlocked entry row on a stopped
# ride) until a tick; the close refreshes it beside the menu.


class _FakeMenuItem:
    """A recording menu item: ``Enable(bool)`` records the verdict."""

    def __init__(self) -> None:
        """Start with no recorded verdict."""
        self.enabled: bool | None = None

    def Enable(self, enabled: bool) -> None:  # noqa: N802, FBT001 -- wx API name; positional bool
        """Record the enablement verdict."""
        self.enabled = enabled


class _FakeMenuBar:
    """A recording menubar holding plan §2's three watched menu rows."""

    _WATCHED = (ids.MI_FINISH_RIDE, ids.MI_STOP_RIDE, ids.MI_UNDO_CROSSING)

    def __init__(self) -> None:
        """Build one recording item per watched route id."""
        self.items = {wx.xrc.XRCID(item_id): _FakeMenuItem() for item_id in self._WATCHED}

    def FindItem(  # noqa: N802 -- wx API name
        self, real_id: int
    ) -> tuple[_FakeMenuItem | None, None]:
        """Return the item for *real_id*, or a ``(None, None)`` miss."""
        item = self.items.get(real_id)
        return (item, None) if item is not None else (None, None)

    def enabled(self, item_id: str) -> bool | None:
        """Return the verdict recorded for one watched route id."""
        return self.items[wx.xrc.XRCID(item_id)].enabled


class _MenuFrame(_NoticeFrame):
    """A notice frame answering ``GetMenuBar`` with a fake bar."""

    def __init__(self, menubar: _FakeMenuBar) -> None:
        """Hold the bar the menu binder flips."""
        super().__init__()
        self.menubar = menubar

    def GetMenuBar(self) -> _FakeMenuBar:  # noqa: N802 -- wx API name
        """Return the recording menubar."""
        return self.menubar


class _ConsoleViewStub:
    """A console-view stub: the renders the presenter drives.

    The constructor's own R-11 teams-chip push, and -- since the
    simulator's close-persist now re-renders the console beside the
    menu re-apply (plan §2) -- every channel
    :meth:`ConsolePresenter.refresh_state` pushes. The ride state and
    the entry lock are recorded, because they are what this module's
    post-GO test reads; the rest are inert.
    """

    def __init__(self) -> None:
        """Start with no chip render and no state render recorded."""
        self.team_ui_visible: bool | None = None
        self.last_state: RideStatus | None = None
        self.last_stopped: bool | None = None
        self.entry_locked: bool | None = None

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Record the R-11 teams-chip visibility push."""
        self.team_ui_visible = visible

    def set_state(self, status: RideStatus, *, stopped: bool = False) -> None:
        """Record the ride state and the stop guard."""
        self.last_state = status
        self.last_stopped = stopped

    def set_entry_locked(self, *, locked: bool) -> None:
        """Record the entry-row lock verdict."""
        self.entry_locked = locked

    def show_feed(self, _rows: list[object]) -> None:
        """No-op: the post-GO test reads the state render alone."""

    def show_flagged(self, _rows: list[object]) -> None:
        """No-op: the review tab is not this module's concern."""

    def show_current_lap(self, _lap: int) -> None:
        """No-op: the header reading is not this module's concern."""

    def show_counters(self, _counters: object) -> None:
        """No-op: the counter chips are not this module's concern."""

    def show_clock(self, _elapsed: str, _remaining: str) -> None:
        """No-op: the clock labels are not this module's concern."""

    def set_clock_fractions(self, *, elapsed_frac: float, remaining_frac: float) -> None:
        """No-op: the gauge dials are not this module's concern."""


# The staged GO: four generated riders on two teams, replayed for two
# laps. Each entry crosses once a lap (Phase 2's team-lap fix), so two
# team entries over two laps record exactly four crossings.
_SIM_RIDERS = 4
_SIM_TEAMS = 2
_SIM_ENTRIES = _SIM_TEAMS
_SIM_LAPS = 2
_SIM_INTERVAL_MINUTES = 1
_SIM_CROSSINGS = _SIM_ENTRIES * _SIM_LAPS


class _SimulatedGo(NamedTuple):
    """One staged GO: the context, its store, ride id and bar."""

    context: app_module._RouteContext
    store: Store
    ride_id: int
    menubar: _FakeMenuBar


@pytest.fixture
def simulated_go(tmp_path: Path) -> Iterator[_SimulatedGo]:
    """Stage the simulator's own flow: open a DRAFT ride, then GO.

    The app's path, headless: the ride row is created over a real store,
    its console presenter is threaded over the store-replayed engine
    with the store's append wired as the engine's event sink (the sink
    :func:`app._wire_store_append` attaches), the dialog's own
    presenter generates the placeholder field and replays the race (the
    dialog's GO), and the ride is left RUNNING+stopped with every event
    persisted. Yields the pieces the three post-GO tests act on.
    """
    from conftest import gorba_config  # noqa: PLC0415 -- the shared live-config fixture

    store = Store.open(tmp_path / "rides.db")
    try:
        ride_id = store.create_ride(gorba_config())
        roster = store.roster_for(ride_id)
        engine = store.load_engine(ride_id, roster)
        menubar = _FakeMenuBar()
        context = _context(store=store, frame=_MenuFrame(menubar))
        context.roster = roster
        context.active_ride_id = ride_id
        context.settings_path = tmp_path / "settings.json"
        app_module._wire_store_append(
            engine, store, ride_id, notify=app_module._status_notice(context)
        )
        context.presenter = ConsolePresenter(
            _ConsoleViewStub(), engine=engine, source=EngineDataSource(engine, roster)
        )
        simulator = SimulatorPresenter(engine, roster)
        simulator.generate_riders(_SIM_RIDERS, _SIM_TEAMS, 0, seed=1)
        outcome = simulator.run_simulation(_SIM_LAPS, _SIM_INTERVAL_MINUTES)
        assert outcome == SimOutcome(cancelled=False, recorded=_SIM_CROSSINGS, blocked=None)
        yield _SimulatedGo(context=context, store=store, ride_id=ride_id, menubar=menubar)
    finally:
        store.close()


def test_persist_simulator_changes_after_a_simulated_go_leaves_the_ride_running(
    simulated_go: _SimulatedGo,
) -> None:
    """Plan §2: the close leaves the ride RUNNING and stopped."""
    context, _store, _ride_id, _menubar = simulated_go

    app_module._persist_simulator_changes(context, _SimulatorViewStub(roster_changed=True))

    engine = context.presenter.engine  # type: ignore[union-attr] -- the fixture threads one
    assert (engine.state, engine.stopped) == (RideStatus.RUNNING, True)


def test_persist_simulator_changes_after_a_simulated_go_enables_the_running_rows(
    simulated_go: _SimulatedGo,
) -> None:
    """Plan §2: Finish Ride / Stop / Undo enable on the ride."""
    context, _store, _ride_id, menubar = simulated_go

    app_module._persist_simulator_changes(context, _SimulatorViewStub(roster_changed=True))

    assert menubar.enabled(ids.MI_FINISH_RIDE) is True
    assert menubar.enabled(ids.MI_STOP_RIDE) is True
    assert menubar.enabled(ids.MI_UNDO_CROSSING) is True


def test_persist_simulator_changes_after_a_simulated_go_keeps_the_audit_trail(
    simulated_go: _SimulatedGo,
) -> None:
    """Plan §2: the ride's start, crossings and stop all persist."""
    context, store, ride_id, _menubar = simulated_go

    app_module._persist_simulator_changes(context, _SimulatorViewStub(roster_changed=True))

    assert [row.action for row in store.audit_rows(ride_id)] == [
        "stop",
        *["record_crossing"] * _SIM_CROSSINGS,
        "start",
    ]


def test_persist_simulator_changes_after_a_simulated_go_renders_the_console(
    simulated_go: _SimulatedGo,
) -> None:
    """Plan §2: the close re-renders the console the GO left behind.

    GO drives the engine directly, so no console ride-state change
    fires: the main screen would keep its pre-GO render -- an unlocked
    entry row on a stopped ride, a stale clock -- until the next tick.
    The close-persist refreshes it beside the menu re-apply, so the
    engine's own verdicts (RUNNING, stopped, entry locked) are what
    the console shows the moment the modal is gone.
    """
    context, _store, _ride_id, _menubar = simulated_go
    view = context.presenter.view  # type: ignore[union-attr] -- the fixture threads one

    app_module._persist_simulator_changes(context, _SimulatorViewStub(roster_changed=True))

    assert (view.last_state, view.last_stopped, view.entry_locked) == (
        RideStatus.RUNNING,
        True,
        True,
    )


def test_persist_simulator_changes_without_a_presenter_leaves_the_menu_untouched(
    tmp_path: Path,
) -> None:
    """Plan §2: no live engine means no menu re-apply at all."""
    menubar = _FakeMenuBar()
    context = _context(store=None, frame=_MenuFrame(menubar))
    context.settings_path = tmp_path / "settings.json"

    app_module._persist_simulator_changes(context, _SimulatorViewStub(roster_changed=False))

    assert menubar.enabled(ids.MI_FINISH_RIDE) is None
    assert menubar.enabled(ids.MI_STOP_RIDE) is None
    assert menubar.enabled(ids.MI_UNDO_CROSSING) is None


# ------- the rider editor's close re-applies the live menu + console
#
# E3.1.2: a committed team change on a live ride goes through the
# engine's own pooled move, which re-attributes the rider's laps and
# cards -- a change no roster edit alone signals to the console. Rather
# than a ride-state change, the editor's close-persist mirrors the
# simulator's: it re-applies the menubar from the live engine and
# refreshes the console, so the re-credited field is what the operator
# sees the moment the modal is gone. The tests below stage the app's own
# shape headless (real store, store-replayed engine, a real console
# presenter) and drive the close-persist the route runs.


class _LiveRide(NamedTuple):
    """One staged open ride: the context, its store, ride id and bar."""

    context: app_module._RouteContext
    store: Store
    ride_id: int
    menubar: _FakeMenuBar


@pytest.fixture
def live_ride(tmp_path: Path) -> Iterator[_LiveRide]:
    """Stage a store-backed DRAFT ride with a live console presenter.

    The library Open's own wiring, headless: a ride row is created over
    a real store and its console presenter is threaded over the
    store-replayed engine, so the editor's close-persist has the live
    engine the app's own console holds.
    """
    from conftest import gorba_config  # noqa: PLC0415 -- the shared live-config fixture

    store = Store.open(tmp_path / "rides.db")
    try:
        ride_id = store.create_ride(gorba_config())
        roster = store.roster_for(ride_id)
        engine = store.load_engine(ride_id, roster)
        menubar = _FakeMenuBar()
        context = _context(store=store, frame=_MenuFrame(menubar))
        context.roster = roster
        context.active_ride_id = ride_id
        context.presenter = ConsolePresenter(
            _ConsoleViewStub(), engine=engine, source=EngineDataSource(engine, roster)
        )
        yield _LiveRide(context=context, store=store, ride_id=ride_id, menubar=menubar)
    finally:
        store.close()


def test_persist_rider_editor_changes_given_a_live_engine_re_applies_the_menu(
    live_ride: _LiveRide,
) -> None:
    """The editor's close re-applies §15 enablement from the engine."""
    context, _store, _ride_id, menubar = live_ride

    app_module._persist_rider_editor_changes(context, _EditorViewStub(roster_changed=True))

    assert menubar.enabled(ids.MI_FINISH_RIDE) is False


def test_persist_rider_editor_changes_given_a_live_engine_renders_the_console(
    live_ride: _LiveRide,
) -> None:
    """The editor's close refreshes the console's own render too."""
    context, _store, _ride_id, _menubar = live_ride
    view = context.presenter.view  # type: ignore[union-attr] -- the fixture threads one

    app_module._persist_rider_editor_changes(context, _EditorViewStub(roster_changed=True))

    assert (view.last_state, view.entry_locked) == (RideStatus.DRAFT, True)


def test_persist_rider_editor_changes_given_no_presenter_leaves_the_menu_untouched(
    live_ride: _LiveRide,
) -> None:
    """T-3: no live engine means no menu re-apply, store save or not."""
    context, _store, _ride_id, menubar = live_ride
    context.presenter = None

    app_module._persist_rider_editor_changes(context, _EditorViewStub(roster_changed=True))

    assert menubar.enabled(ids.MI_FINISH_RIDE) is None


# ------------- plan §8: roster plate changes reach the audit table


class _RosterAuditRecorderStore(_SaveRecorderStore):
    """A store recording roster saves and the audit rows they drain."""

    def __init__(self) -> None:
        """Start with no saved rides and no audit rows."""
        super().__init__()
        self.audit: list[tuple[int, str, str]] = []

    def append_roster_event(self, ride_id: int, action: str, payload_json: str) -> None:
        """Record one drained roster event."""
        self.audit.append((ride_id, action, payload_json))


class _RosterAuditFailsStore(_RosterAuditRecorderStore):
    """A store whose audit-row insert fails like a full disk."""

    def append_roster_event(self, _ride_id: int, _action: str, _payload_json: str) -> None:
        """Refuse the audit insert."""
        raise OSError("disk full")


class _SourcePresenterStub:
    """A stub carrying the one attribute the audit route reads."""

    def __init__(self, source: object) -> None:
        """Hold the display source the console would expose."""
        self.source = source


def _roster_with_solo_plate_change() -> tuple[Roster, object]:
    """Build a DRAFT roster whose solo entry's plate moved 12 -> 13."""
    roster = Roster()
    entry = roster.create_solo_entry(first_name="Alice", plate="12")
    roster.change_solo_plate(entry, plate="13")
    return roster, entry


def test_persist_rider_editor_changes_given_a_plate_change_persists_its_audit_row() -> None:
    """A rider-editor plate edit persists to the audit table."""
    store = _RosterAuditRecorderStore()
    context = _context(store=store)
    context.active_ride_id = 5
    roster, _entry = _roster_with_solo_plate_change()
    context.roster = roster

    app_module._persist_rider_editor_changes(context, _EditorViewStub(roster_changed=True))

    assert store.saved == [(5, roster)]
    assert store.audit == [
        (
            5,
            "change_solo_plate",
            json.dumps(
                {
                    "display_name": "Alice",
                    "old_plate": "12",
                    "new_plate": "13",
                    "reason": "12 → 13",
                }
            ),
        )
    ]
    assert roster.audit_log == ()


def test_persist_team_editor_changes_given_a_rider_plate_change_persists_its_audit_row() -> None:
    """A team-editor rider plate edit persists to the audit table."""
    store = _RosterAuditRecorderStore()
    context = _context(store=store)
    context.active_ride_id = 5
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    entry = roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    roster.change_pooled_rider_plate(entry.riders[0], plate="79")
    context.roster = roster

    app_module._persist_team_editor_changes(context, _EditorViewStub(roster_changed=True))

    assert store.saved == [(5, roster)]
    assert store.audit == [
        (
            5,
            "change_pooled_rider_plate",
            json.dumps(
                {
                    "rider_name": "A. Roy",
                    "old_plate": "77",
                    "new_plate": "79",
                    "reason": "77 → 79",
                }
            ),
        )
    ]
    assert roster.audit_log == ()


def test_handle_check_rider_issues_given_a_plate_change_persists_its_audit_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An issues-dialog plate fix persists to the audit table."""
    from rivercrossing.ui.views import rider_issues  # noqa: PLC0415 -- the flow seam

    store = _RosterAuditRecorderStore()
    context = _context(store=store)
    context.active_ride_id = 5
    roster = Roster()
    entry = roster.create_solo_entry(first_name="Alice", plate="12")
    context.roster = roster

    def _fix_plate(_frame: object, live_roster: Roster, **_kwargs: object) -> bool:
        live_roster.change_solo_plate(entry, plate="13")
        return True

    monkeypatch.setattr(rider_issues, "run_rider_issues_flow", _fix_plate)

    app_module._handle_check_rider_issues(context)

    assert store.saved == [(5, roster)]
    assert store.audit == [
        (
            5,
            "change_solo_plate",
            json.dumps(
                {
                    "display_name": "Alice",
                    "old_plate": "12",
                    "new_plate": "13",
                    "reason": "12 → 13",
                }
            ),
        )
    ]


def test_persist_rider_editor_changes_given_a_non_plate_change_persists_no_audit_row() -> None:
    """A non-plate roster event writes no audit row."""
    store = _RosterAuditRecorderStore()
    context = _context(store=store)
    context.active_ride_id = 5
    roster = Roster()
    roster.create_solo_entry(first_name="Alice", plate="12")
    context.roster = roster

    app_module._persist_rider_editor_changes(context, _EditorViewStub(roster_changed=True))

    assert store.saved == [(5, roster)]
    assert store.audit == []
    assert roster.audit_log == ()


def test_persist_rider_editor_changes_given_a_failed_audit_append_posts_a_notice() -> None:
    """A refused audit insert surfaces as a status notice."""
    store = _RosterAuditFailsStore()
    context = _context(store=store)
    context.active_ride_id = 5
    roster, _entry = _roster_with_solo_plate_change()
    context.roster = roster

    app_module._persist_rider_editor_changes(context, _EditorViewStub(roster_changed=True))

    assert store.saved == [(5, roster)]
    assert context.frame.notices == ["Could not save audit trail: disk full"]


def test_audit_source_given_a_store_backed_ride_returns_engine_and_plate_rows(
    tmp_path: Path,
) -> None:
    """The audit source reads engine and plate-change rows."""
    from conftest import gorba_config  # noqa: PLC0415 -- the shared live-config fixture

    store = Store.open(tmp_path / "rides.db")
    try:
        ride_id = store.create_ride(gorba_config())
        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
        store.append_roster_event(
            ride_id,
            "change_team_plate",
            json.dumps({"display_name": "A", "old_plate": "1", "new_plate": "2"}),
        )
        context = _context(store=store)
        context.active_ride_id = ride_id

        rows = app_module._audit_source(context).audit_rows()
    finally:
        store.close()

    assert [(row.action, row.entry) for row in rows] == [
        ("change_team_plate", "1"),
        ("start", ""),
    ]


def test_audit_source_given_a_store_without_an_open_ride_returns_the_empty_source() -> None:
    """A store with no ride open keeps the dialog's empty state."""
    context = _context(store=_SaveRecorderStore())
    context.active_ride_id = None

    assert app_module._audit_source(context) is app_module._EMPTY_SOURCE


def test_audit_source_given_no_store_returns_the_live_console_source() -> None:
    """Without a store the dialog reads the live console."""
    source = object()
    context = _context(store=None)
    context.presenter = _SourcePresenterStub(source)

    assert app_module._audit_source(context) is source


def test_audit_source_given_no_store_and_no_presenter_returns_the_empty_source() -> None:
    """No store and no console: the E5.4.2 empty state stands."""
    context = _context(store=None)

    assert app_module._audit_source(context) is app_module._EMPTY_SOURCE
