# SPDX-License-Identifier: GPL-3.0-only
"""R-54's automatic backup wiring (E5.3.1): on open, then hourly.

``store/backup.py`` owns the mechanism -- ``run``, ``schedule_hourly``
and :class:`HourlyBackup` -- and stays wx-free (R-71, enforced by the
import-linter contract). This module pins the *app* half of R-54 that
the module's own docstring names as still missing:

- ``_run_automatic_backup``: one backup through the store, with a
  failure recorded through ``Logging.warn`` instead of raised. A
  failed automatic backup is never silent, and never a false crash.
- ``_build_backup_scheduler``: the store-backed ``HourlyBackup`` the
  console's timer ticks (``None`` with no store-backed bootstrap).
- ``_bootstrap_window``: R-54's "on open" backup, run right after the
  store opens -- on both the ``db_path`` and the ``store=`` paths.
- ``MainFrame.wire_console``: the frame-owned hourly ``wx.Timer``
  that fires the scheduler's ``tick()``, stopped with the frame for
  the measured segfault reason the tick timer's own comment records.

Only what needs no window is pinned here: ``_bootstrap_window`` runs
with ``build_main_window`` (the XRC load and every wx construction)
replaced by a double, and ``wire_console`` runs against a shell plus a
stand-in for the slice of ``wx`` it binds with -- the same headless
pattern ``test_main_frame_presenter_swap.py`` uses.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rivercrossing.store import Store, StoreError
from rivercrossing.store.backup import backup_dir_for
from rivercrossing.ui import app as app_module
from rivercrossing.ui.logging import Logging
from rivercrossing.ui.views import main_frame

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest

# R-54's hourly cadence, as wx.Timer.Start takes it (milliseconds).
_HOURLY_MS = 3_600_000


class _BackupFailsStore:
    """A store double whose ``backup_now`` always refuses.

    The unwritable-backup-target class ``_handle_backup_database``
    already guards: an automatic backup must record the failure, not
    take the launch down.
    """

    def __init__(self) -> None:
        """Start with no recorded attempt and no close."""
        self.backup_calls = 0
        self.closed = False

    def backup_now(self) -> Path:
        """Fail the backup, recording the attempt."""
        self.backup_calls += 1
        raise StoreError("no space left on device")

    def close(self) -> None:
        """Record the close ``main``'s ``finally`` would make."""
        self.closed = True


class _Clock:
    """A settable wall clock for the scheduler's hour boundary."""

    def __init__(self, now: datetime) -> None:
        """Start *now*."""
        self.now = now

    def __call__(self) -> datetime:
        """Return the current instant."""
        return self.now


class _AppDouble:
    """Carry only the F1 log ``_bootstrap_window`` reads."""

    def __init__(self, log: Logging | None) -> None:
        """Carry *log* as the launch's own."""
        self.log = log


def _store_opening(store: _BackupFailsStore) -> type:
    """Return a ``Store`` double whose ``open`` answers *store*."""
    return type("_StoreDouble", (), {"open": classmethod(lambda _cls, _path: store)})


def _warn_messages(path: Path) -> list[object]:
    """Return every ``warn`` message at *path*, in write order."""
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [record["msg"] for record in records if record["event"] == "warn"]


def _backups(db_path: Path) -> list[Path]:
    """Return the backup files written for *db_path*, oldest first."""
    return sorted(backup_dir_for(db_path).glob("*.db"))


# ------------------------------------------- _run_automatic_backup


def test_run_automatic_backup_given_a_store_writes_one_backup(tmp_path: Path) -> None:
    """The runner is a real backup of the store's own file."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        app_module._run_automatic_backup(store, None)
    finally:
        store.close()

    backups = _backups(db_path)

    assert len(backups) == 1
    assert backups[0].name.startswith("rides.")


def test_run_automatic_backup_given_a_failed_backup_records_the_reason(
    tmp_path: Path,
) -> None:
    """R-54: a failed automatic backup is recorded, never silent."""
    log = Logging(tmp_path / "app.ndjson")
    store = _BackupFailsStore()
    try:
        app_module._run_automatic_backup(store, log)
    finally:
        log.close()

    assert _warn_messages(tmp_path / "app.ndjson") == [
        "automatic backup failed: StoreError: no space left on device"
    ]


def test_run_automatic_backup_given_no_log_still_asks_the_store() -> None:
    """Back up with no log: the failure is swallowed, not raised."""
    store = _BackupFailsStore()

    app_module._run_automatic_backup(store, None)

    assert store.backup_calls == 1


# ----------------------------------------- _build_backup_scheduler


def test_build_backup_scheduler_given_no_store_returns_none() -> None:
    """W1's no-store bootstrap gives no scheduler to tick."""
    assert app_module._build_backup_scheduler(None, None) is None


def test_build_backup_scheduler_given_a_new_hour_writes_one_backup(tmp_path: Path) -> None:
    """R-54's hourly half: one tick per hour boundary writes one backup.

    The first tick only seeds the hour (the on-open backup is
    ``_bootstrap_window``'s separate call); the tick whose hour has
    advanced runs the store-backed runner exactly once.
    """
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    clock = _Clock(datetime(2026, 9, 14, 12, 0, tzinfo=UTC))
    try:
        scheduler = app_module._build_backup_scheduler(store, None, clock=clock)
        seeded = scheduler.tick()
        clock.now = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
        ran = scheduler.tick()
    finally:
        store.close()

    assert (seeded, ran) == (False, True)
    assert len(_backups(db_path)) == 1


# ------------------------------------------------- _bootstrap_window


def test_bootstrap_window_given_a_db_path_writes_the_on_open_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R-54's "on open": one backup right after the store opens."""
    db_path = tmp_path / "rides.db"
    built = object()
    monkeypatch.setattr(app_module, "build_main_window", lambda _app, **_kwargs: built)

    frame, store = app_module._bootstrap_window(None, db_path=db_path)
    try:
        assert frame is built
        assert len(_backups(db_path)) == 1
    finally:
        store.close()


def test_bootstrap_window_given_a_failed_backup_records_it_and_still_builds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed on-open backup warns and the launch continues."""
    log = Logging(tmp_path / "app.ndjson")
    store = _BackupFailsStore()
    built = object()
    monkeypatch.setattr(app_module, "Store", _store_opening(store))
    monkeypatch.setattr(app_module, "build_main_window", lambda _app, **_kwargs: built)

    frame, opened = app_module._bootstrap_window(_AppDouble(log), db_path=tmp_path / "rides.db")
    log.close()

    assert frame is built
    assert opened is store
    assert _warn_messages(tmp_path / "app.ndjson") == [
        "automatic backup failed: StoreError: no space left on device"
    ]


# --------------------------------------------- MainFrame.wire_console


class _TimerDouble:
    """A ``wx.Timer`` double recording its owner, starts and stops."""

    def __init__(self, owner: object) -> None:
        """Record the owning window; start unstarted."""
        self.owner = owner
        self.starts: list[int] = []
        self.stops = 0

    def Start(self, milliseconds: int) -> None:  # noqa: N802 -- wx API name
        """Record one start, with its interval."""
        self.starts.append(milliseconds)

    def Stop(self) -> None:  # noqa: N802 -- wx API name
        """Record one stop."""
        self.stops += 1


class _FakeWx:
    """The slice of ``wx`` ``wire_console`` binds its timers with."""

    EVT_BUTTON = "evt:button"
    EVT_TIMER = "evt:timer"
    EVT_WINDOW_DESTROY = "evt:window-destroy"
    Window = object

    def __init__(self) -> None:
        """Start with no timer built."""
        self.timers: list[_TimerDouble] = []

    def Timer(self, owner: object) -> _TimerDouble:  # noqa: N802 -- wx API name
        """Build and record a timer owned by *owner*."""
        timer = _TimerDouble(owner)
        self.timers.append(timer)
        return timer


class _BindRecorder:
    """A wx window double recording every ``Bind``."""

    def __init__(self) -> None:
        """Start with no bindings."""
        self.binds: list[tuple[object, Callable[[object], None], object]] = []

    def Bind(  # noqa: N802 -- wx API name
        self,
        event: object,
        handler: Callable[[object], None],
        source: object = None,
    ) -> None:
        """Record one binding."""
        self.binds.append((event, handler, source))

    def handlers_for(self, event: object) -> list[Callable[[object], None]]:
        """Return every handler bound to *event*, in binding order."""
        return [handler for bound, handler, _source in self.binds if bound is event]

    def handler_for_timer(self, event: object, timer: _TimerDouble) -> Callable[[object], None]:
        """Return the handler bound to *event* for *timer*."""
        return next(
            handler for bound, handler, source in self.binds if bound is event and source is timer
        )


class _PresenterDouble:
    """The presenter members ``wire_console`` binds."""

    def on_start(self) -> None:
        """No-op: the shell never fires the control handlers."""

    def on_stop_requested(self) -> None:
        """No-op: the shell never fires the control handlers."""

    def on_undo(self) -> None:
        """No-op: the shell never fires the control handlers."""

    def tick(self) -> None:
        """No-op: the shell only counts scheduler ticks."""


class _SchedulerDouble:
    """An ``HourlyBackup`` double counting its ticks."""

    def __init__(self) -> None:
        """Start with no ticks."""
        self.ticks = 0

    def tick(self) -> bool:
        """Record one hour check."""
        self.ticks += 1
        return True


class _ConsoleShell:
    """A ``MainFrame`` double owning what ``wire_console`` binds."""

    # The real destroy handler, so the frame's own binding is exercised
    # against the two timer attributes this shell owns.
    _on_frame_destroy = main_frame.MainFrame._on_frame_destroy

    def __init__(self, scheduler: _SchedulerDouble | None) -> None:
        """Seed the constructor's own timer/scheduler state."""
        self.frame = _BindRecorder()
        self.start_btn = _BindRecorder()
        self.stop_btn = _BindRecorder()
        self.undo_btn = _BindRecorder()
        self._presenter = None
        self._tick_timer = None
        self._backup_timer: _TimerDouble | None = None
        self._backup_scheduler = scheduler


def _wire(
    console: _ConsoleShell, monkeypatch: pytest.MonkeyPatch
) -> tuple[_FakeWx, _PresenterDouble]:
    """Drive the real ``wire_console`` with a stand-in for ``wx``."""
    fake_wx = _FakeWx()
    monkeypatch.setattr(main_frame, "wx", fake_wx)
    presenter = _PresenterDouble()
    main_frame.MainFrame.wire_console(console, presenter)
    return fake_wx, presenter


def test_wire_console_given_a_backup_scheduler_starts_the_hourly_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-54: a frame-owned timer, ticking once an hour."""
    console = _ConsoleShell(_SchedulerDouble())

    fake_wx, _presenter = _wire(console, monkeypatch)

    assert console._backup_timer is fake_wx.timers[-1]
    assert console._backup_timer.owner is console.frame
    assert console._backup_timer.starts == [_HOURLY_MS]


def test_wire_console_given_the_hourly_timer_event_ticks_the_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The timer's own event is what advances the hour check."""
    scheduler = _SchedulerDouble()
    console = _ConsoleShell(scheduler)
    _fake_wx, _presenter = _wire(console, monkeypatch)
    assert console._backup_timer is not None

    console.frame.handler_for_timer(_FakeWx.EVT_TIMER, console._backup_timer)(None)

    assert scheduler.ticks == 1


def test_wire_console_given_the_frame_destroy_event_stops_both_timers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The measured segfault guard covers the backup timer too.

    A running ``wx.Timer`` whose owner was destroyed keeps its native
    timer registered, and the next ``wxSafeYield`` dispatches against
    the freed owner -- so the frame's own destroy event stops it.
    """
    console = _ConsoleShell(_SchedulerDouble())
    _fake_wx, _presenter = _wire(console, monkeypatch)

    for handler in console.frame.handlers_for(_FakeWx.EVT_WINDOW_DESTROY):
        handler(None)

    assert console._backup_timer is not None
    assert console._backup_timer.stops == 1
    assert console._tick_timer is not None
    assert console._tick_timer.stops == 1


def test_wire_console_binds_one_frame_destroy_handler_for_both_timers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One destroy binding, not two: the second REPLACES the first.

    Measured on wxPython 4.3.1 / wxWidgets 3.3.3 (macOS): binding
    ``wx.EVT_WINDOW_DESTROY`` twice on the same window with no source
    leaves only the last handler live, so a separate backup-timer
    stop-handler would leave the *tick* timer running against the
    destroyed frame -- the measured ``wxTimerImpl::SendEvent``
    segfault the functional open/quit smoke caught. Both timers are
    therefore stopped from the one binding.
    """
    console = _ConsoleShell(_SchedulerDouble())

    _fake_wx, _presenter = _wire(console, monkeypatch)

    assert len(console.frame.handlers_for(_FakeWx.EVT_WINDOW_DESTROY)) == 1


def test_wire_console_given_no_backup_scheduler_builds_only_the_tick_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-store bootstrap wires no backup timer at all."""
    console = _ConsoleShell(None)

    fake_wx, _presenter = _wire(console, monkeypatch)

    assert console._backup_timer is None
    assert fake_wx.timers == [console._tick_timer]
