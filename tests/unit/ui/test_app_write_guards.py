# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for app.py's store- and settings-write guards.

Store writes (``create_ride``/``save_roster``/``delete_ride``/
``duplicate_ride``/``close_session`` and the ``on_event`` append
sink) and settings writes run inside wx menu/button/sash handlers,
and wx swallows a Python exception raised inside an event handler
(measured: ``docs/EPIC3-SESSION-SUMMARY.md``) -- so an unguarded
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
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, NamedTuple

import pytest
import wx.xrc

from rivercrossing.ride import RideStatus
from rivercrossing.roster import Roster
from rivercrossing.store import (
    RideNameMismatchError,
    RideNotFoundError,
    RideRunningError,
    Store,
)
from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids, std_dialogs
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EngineDataSource, RideSummary
from rivercrossing.ui.presenters.settings import AppSettings
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
    monkeypatch.setattr(rider_issues, "run_rider_issues_flow", lambda _f, _r: True)

    app_module._handle_check_rider_issues(context)

    assert context.frame.notices == ["Could not save riders: disk full"]


def test_stamp_closed_session_given_a_failed_session_close_posts_a_notice() -> None:
    """A refused ``closed_at`` stamp surfaces; the quit proceeds."""
    context = _context(store=_CloseSessionFailsStore())

    app_module._stamp_closed_session(context)

    assert context.frame.notices == ["Could not close session: database is locked"]


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

    class _StubMenubar:
        """Record every radio-check call the apply path makes."""

        def __init__(self) -> None:
            """Start with an empty check log."""
            self.checks: list[tuple[int, bool]] = []

        def Check(self, item_id: int, checked: bool) -> None:  # noqa: N802, FBT001 -- mirrors wx MenuBar.Check's positional bool
            """Record one check call."""
            self.checks.append((item_id, checked))

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
        hide_times=False,
        zoom_percent=context.settings.zoom_percent,
    )

    def _save_that_fails(_settings: object, _path: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(app_module.settings_store, "save_settings", _save_that_fails)

    app_module._apply_settings_live(context, new_settings)

    assert context.frame.notices == ["Could not save settings: disk full"]
    assert context.settings is new_settings


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
    """A SimulatorDialog-shaped stub: the change flag plus the spins."""

    def __init__(
        self,
        *,
        roster_changed: bool,
        sim_values: tuple[int, int, int, int, int] = (10, 2, 2, 1, 1),
    ) -> None:
        """Hold a presenter stub and the five spin values to persist."""
        self.presenter = _EditorPresenterStub(roster_changed=roster_changed)
        self.sim_values = sim_values


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

    def LoadDialog(self, _parent: object, _name: object) -> _FakeWindow:  # noqa: N802 -- wx API name
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

    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005 -- the SUT calls opener=; the stub ignores it

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

    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005 -- the SUT calls opener=; the stub ignores it

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

    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005 -- the SUT calls opener=; the stub ignores it

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
        lambda _window, *, roster: changed_view,  # noqa: ARG005 -- the SUT calls roster=; the stub ignores it
    )
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(app_module, "_apply_dialog_defaults", lambda _w, _route: None)
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- the patched modal seam

    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005 -- the SUT calls opener=; the stub ignores it

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
        lambda _window, *, roster: _EditorViewStub(  # noqa: ARG005 -- the SUT calls roster=; the stub ignores it
            roster_changed=False
        ),
    )
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(app_module, "_apply_dialog_defaults", lambda _w, _route: None)
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- the patched modal seam

    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005 -- the SUT calls opener=; the stub ignores it

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

    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005 -- the SUT calls opener=; the stub ignores it

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

    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005 -- the SUT calls opener=; the stub ignores it

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

    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005 -- the SUT calls opener=; the stub ignores it

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
# close-persist the route runs once the modal has ended.


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
    """A console-view stub: only the presenter's constructor render."""

    def __init__(self) -> None:
        """Start with no chip render recorded."""
        self.team_ui_visible: bool | None = None

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Record the R-11 teams-chip visibility push."""
        self.team_ui_visible = visible


# The staged GO: four generated riders on two teams, replayed for two
# laps, so one simulated race records exactly eight crossings.
_SIM_RIDERS = 4
_SIM_TEAMS = 2
_SIM_LAPS = 2
_SIM_INTERVAL_MINUTES = 1
_SIM_CROSSINGS = _SIM_RIDERS * _SIM_LAPS


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
