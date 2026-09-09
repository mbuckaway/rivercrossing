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
from typing import TYPE_CHECKING

import pytest

from rivercrossing.ride import RideStatus
from rivercrossing.roster import Roster
from rivercrossing.store import RideNameMismatchError, RideNotFoundError, RideRunningError
from rivercrossing.ui import app as app_module
from rivercrossing.ui import std_dialogs
from rivercrossing.ui.presenters.data_source import RideSummary
from rivercrossing.ui.presenters.settings import AppSettings

if TYPE_CHECKING:
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


def _context(*, store: object) -> app_module._RouteContext:
    """Build a route context carrying *store* and a notice frame."""
    return app_module._RouteContext(
        frame=_NoticeFrame(),
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
    _open, _new, duplicate = app_module._live_library_callbacks(
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
