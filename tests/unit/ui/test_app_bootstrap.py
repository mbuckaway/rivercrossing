# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for ``app.main``'s bootstrap order (G, F1/F3/F4).

``main`` owns the one order the launch has to hold: load the settings,
prune the old invocation logs, open this launch's NDJSON log next to
``settings.json``, record the launch context, build the app, install
the whitelisted control-event filter and the crash hook, open the
store, build and show ``main_window``, and only then hand the event
loop the deferred work. Three of those steps are pinned here without
ever entering ``MainLoop`` (which blocks) or constructing a real
window:

- **G**: the launch flow (``resume_dlg`` / the No Ride Open alert) is
  deferred through ``wx.CallAfter`` -- it must run on the running
  event loop, after the frame is shown and the menubar is live, not
  synchronously while the frame is still being set up.
- **F1/F3/F4**: the log is constructed from the loaded settings'
  ``verbose_logging``, records the ``app_start`` launch context, and
  the control-event filter is installed on the app.
- **F1 facts**: the launch choice and a loaded ride are recorded by
  ``_run_launch_flow`` and ``_switch_console_to_ride``.

Every wx touch is faked (T-10: the GUI toolkit is the I/O boundary)
and every heavy collaborator -- ``build_app``, ``Store``,
``_bootstrap_window`` -- is a recording double, so the test drives
``main``'s own ordering and nothing else.
"""

from __future__ import annotations

import gc
import json
import os
import platform
import re
import sys
import weakref
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from rivercrossing import __version__
from rivercrossing.roster import Roster
from rivercrossing.store import PreviousSession, SchemaVersionMismatchError, SessionState
from rivercrossing.ui import app as app_module
from rivercrossing.ui import std_dialogs
from rivercrossing.ui.logging import Logging, build_log_path
from rivercrossing.ui.presenters import settings as settings_store
from rivercrossing.ui.presenters.settings import AppSettings

if TYPE_CHECKING:
    from pathlib import Path

# A fixed launch instant names every test-constructed log.
_LAUNCH = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)

# An old instant for the prune test's pre-existing logs.
_STALE_BASE = datetime(2020, 1, 1, tzinfo=UTC)

# The per-run transport keys the formatter adds to every record.
_VOLATILE = frozenset({"ts", "file", "line", "func"})


class _FakeFrame:
    """A frame double recording its Show."""

    def __init__(self) -> None:
        """Start unshown."""
        self.shown = False

    def Show(self) -> None:  # noqa: N802 -- wx API name main calls
        """Record the show."""
        self.shown = True


class _FakeStore:
    """A store double recording its close."""

    def __init__(self, path: object) -> None:
        """Record the path ``Store.open`` was asked for."""
        self.path = path
        self.closed = False

    def close(self) -> None:
        """Record the close."""
        self.closed = True


class _FakeApp:
    """An app double carrying the launch context and its MainLoop."""

    def __init__(self) -> None:
        """Start with a launch context, no loop calls and no log."""
        self.launch_context = object()
        self.main_loop_calls = 0
        self.log: Logging | None = None
        self.event_filter: object = None

    def MainLoop(self) -> None:  # noqa: N802 -- wx API name main calls
        """Record the loop entry (never blocks)."""
        self.main_loop_calls += 1


class _FakeEventType:
    """A ``PyEventBinder``-like double exposing ``typeId``."""

    def __init__(self, type_id: int) -> None:
        """Store the event type id."""
        self.typeId = type_id


class _FakeEventFilter:
    """A minimal ``wx.EventFilter`` stand-in (base class + Skip)."""

    Event_Skip = -1

    def FilterEvent(self, _event: object) -> int:  # noqa: N802 -- wx's override
        """Pass every event through (the real filter's own contract)."""
        return self.Event_Skip


class _FakeWx:
    """The wx surface ``main`` touches, recording everything.

    Only the handful of names ``main`` reads is present: the log
    target, ``CallAfter``, ``App.AddFilter`` and the event types the
    control-event filter's whitelist compares against.
    """

    def __init__(self) -> None:
        """Start with empty recording logs."""
        self.deferred: list[tuple[object, tuple[object, ...]]] = []
        self.filters: list[object] = []
        self.log_targets: list[object] = []
        self.Log = self
        self.App = self
        self.EventFilter = _FakeEventFilter
        self.EVT_BUTTON = _FakeEventType(10007)
        self.EVT_CHECKBOX = _FakeEventType(10008)
        self.EVT_RADIOBUTTON = _FakeEventType(10016)
        self.EVT_CHOICE = _FakeEventType(10009)
        self.EVT_TEXT = _FakeEventType(10208)

    def SetActiveTarget(self, target: object) -> None:  # noqa: N802 -- wx API name
        """Record one log-target swap."""
        self.log_targets.append(target)

    def LogStderr(self) -> str:  # noqa: N802 -- wx API name
        """Return this fake's sentinel log target."""
        return "stderr-target"

    def CallAfter(self, callable_: object, *args: object) -> None:  # noqa: N802 -- wx API name
        """Record one deferred call instead of scheduling it."""
        self.deferred.append((callable_, args))

    def AddFilter(self, event_filter: object) -> None:  # noqa: N802 -- wx API name
        """Record one installed event filter."""
        self.filters.append(event_filter)


class _Bootstrap:
    """The fakes and recorded calls one ``main`` run produced."""

    def __init__(self, store: _FakeStore) -> None:
        """Start with empty call logs over *store*."""
        self.wx = _FakeWx()
        self.app = _FakeApp()
        self.frame = _FakeFrame()
        self.store = store
        self.launch_calls: list[tuple[object, object]] = []
        self.self_test_calls: list[object] = []
        self.crash_hook_calls: list[object] = []


def _install_fakes(  # noqa: PLR0913 -- (monkeypatch, tmp_path), verbose_logging, open_error
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    verbose_logging: bool,
    open_error: Exception | None = None,
) -> _Bootstrap:
    """Swap ``main``'s collaborators for recording doubles.

    *open_error* makes the ``Store.open`` double raise instead of
    returning -- the start-failure seam the exit-path tests drive.
    """
    store = _FakeStore("staged")
    bootstrap = _Bootstrap(store)

    def _open(_cls: object, path: object) -> _FakeStore:
        if open_error is not None:
            raise open_error
        store.path = path
        return store

    monkeypatch.setattr(app_module, "require_wx", lambda: bootstrap.wx)
    monkeypatch.setattr(app_module, "build_app", lambda: bootstrap.app)
    monkeypatch.setattr(
        app_module, "Store", type("_StoreDouble", (), {"open": classmethod(_open)})
    )
    monkeypatch.setattr(
        app_module,
        "_bootstrap_window",
        lambda _app, *, store=None: (bootstrap.frame, store),
    )
    monkeypatch.setattr(app_module, "_install_crash_excepthook", bootstrap.crash_hook_calls.append)
    monkeypatch.setattr(
        app_module,
        "_run_launch_flow",
        lambda context, store: bootstrap.launch_calls.append((context, store)),
    )
    monkeypatch.setattr(app_module, "_run_launch_self_test", bootstrap.self_test_calls.append)
    monkeypatch.setattr(settings_store, "default_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(
        settings_store,
        "load_settings",
        lambda _path=None: AppSettings(
            appearance="system",
            sound_on=True,
            hide_times=False,
            zoom_percent=100,
            verbose_logging=verbose_logging,
        ),
    )
    return bootstrap


def _records(path: Path) -> list[dict[str, object]]:
    """Return the NDJSON records at *path* (none when it is absent)."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def _entries(path: Path) -> list[dict[str, object]]:
    """Return each record's event and fields, minus transport keys."""
    return [
        {key: value for key, value in record.items() if key not in _VOLATILE}
        for record in _records(path)
    ]


def _written_log(directory: Path) -> Path:
    """Return the one invocation log ``main`` wrote into *directory*."""
    return next(directory.glob("rivercrossing-*.log"))


def _log_names(directory: Path) -> list[str]:
    """Return the sorted invocation-log file names in *directory*."""
    return sorted(path.name for path in directory.glob("rivercrossing-*.log"))


def _stage_stale_logs(directory: Path, count: int) -> None:
    """Write *count* old invocation logs with ascending mtimes."""
    for index in range(count):
        stale = build_log_path(directory, _STALE_BASE + timedelta(seconds=index))
        stale.write_text("{}\n", encoding="utf-8")
        stamp = _STALE_BASE.timestamp() + index
        os.utime(stale, (stamp, stamp))


def _context(*, log: Logging | None) -> app_module._RouteContext:
    """Build a route context over a frame double carrying *log*."""
    app = _FakeApp()
    app.log = log
    return app_module._RouteContext(
        frame=_FakeFrame(),
        resource=None,
        roster=Roster(),
        app=app,
        theme_controller=None,
    )


def test_main_defers_the_launch_flow_to_the_running_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G: the resume/No-Ride flow is scheduled, never run synchronously.

    Running it while the frame is still being set up is the "app never
    starts again" shape; the deferred call keeps it on the running
    event loop, after ``Show`` and after the menubar/routes are live.
    """
    bootstrap = _install_fakes(monkeypatch, tmp_path, verbose_logging=True)

    exit_code = app_module.main()

    assert bootstrap.launch_calls == []
    assert (
        app_module._run_launch_flow,
        (bootstrap.app.launch_context, bootstrap.store),
    ) in bootstrap.wx.deferred
    assert exit_code == 0


def test_main_shows_the_frame_before_entering_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G: Show() runs before MainLoop, which really is entered."""
    bootstrap = _install_fakes(monkeypatch, tmp_path, verbose_logging=True)

    app_module.main()

    assert (bootstrap.frame.shown, bootstrap.app.main_loop_calls) == (True, 1)


def test_main_defers_the_self_test_after_the_frame_is_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The R-44 self-test stays deferred, behind the launch flow."""
    bootstrap = _install_fakes(monkeypatch, tmp_path, verbose_logging=True)

    app_module.main()

    assert (
        app_module._run_launch_self_test,
        (bootstrap.app.launch_context,),
    ) in bootstrap.wx.deferred


def test_main_constructs_the_log_next_to_the_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: main builds the log by the settings path and records it."""
    bootstrap = _install_fakes(monkeypatch, tmp_path, verbose_logging=True)

    app_module.main()

    log = bootstrap.app.log
    assert isinstance(log, Logging)
    assert log.verbose is True
    records = _records(_written_log(tmp_path))
    assert [record["event"] for record in records] == ["app_start"]
    record = records[0]
    assert record["level"] == "INFO"
    assert record["verbose"] is True
    assert record["version"] == __version__
    assert record["platform"] == sys.platform
    assert record["python"] == platform.python_version()
    assert record["pid"] == os.getpid()
    assert datetime.fromisoformat(str(record["started_at"])).utcoffset() == timedelta(0)


def test_main_given_verbose_logging_off_still_writes_the_startup_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: verbose off still creates the file and the always-on dump."""
    bootstrap = _install_fakes(monkeypatch, tmp_path, verbose_logging=False)

    app_module.main()

    log = bootstrap.app.log
    assert isinstance(log, Logging)
    assert log.verbose is False
    records = _records(_written_log(tmp_path))
    assert [record["event"] for record in records] == ["app_start"]
    assert records[0]["verbose"] is False


def test_main_prunes_the_invocation_logs_to_the_last_twenty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: main keeps the twenty newest logs before opening its own."""
    _stage_stale_logs(tmp_path, 30)
    _install_fakes(monkeypatch, tmp_path, verbose_logging=False)

    app_module.main()

    assert len(_log_names(tmp_path)) == 21
    assert build_log_path(tmp_path, _STALE_BASE).name not in _log_names(tmp_path)


def test_main_installs_the_control_event_filter_after_building_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F4: one whitelisted-control filter is added to the live app."""
    bootstrap = _install_fakes(monkeypatch, tmp_path, verbose_logging=True)

    app_module.main()

    assert len(bootstrap.wx.filters) == 1
    assert isinstance(bootstrap.wx.filters[0], _FakeEventFilter)


def test_main_retains_the_installed_event_filter_on_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F4: the app keeps the filter wx only holds a raw pointer to.

    ``wx.App.AddFilter`` (``wxEvtHandler.AddFilter``) stores a raw C++
    pointer, not a Python reference, so a filter the app never names is
    collected and wx then dispatches the first event into freed memory
    -- the launch SIGSEGV the frozen bundle hit. The exact object handed
    to ``AddFilter`` must therefore stay strongly referenced by the app
    for its whole lifetime, surviving a collection.
    """
    bootstrap = _install_fakes(monkeypatch, tmp_path, verbose_logging=True)

    app_module.main()
    # Drop the recording double's own Python reference: from here the
    # app's own attribute is the only thing that can keep the filter
    # alive, exactly as ``wx.App.AddFilter``'s raw pointer leaves it.
    installed = bootstrap.wx.filters[0]
    bootstrap.wx.filters.clear()
    reference = weakref.ref(installed)
    del installed
    gc.collect()

    assert reference() is bootstrap.app.event_filter


# --- F1 facts: the launch and ride-loaded records ------------------


def test_run_launch_flow_with_no_ride_records_the_launch_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: the launch record names the previous state and the choice."""
    log = Logging(build_log_path(tmp_path, _LAUNCH))
    context = _context(log=log)
    store = _PreviousSessionStore(
        PreviousSession(state=SessionState.CLEAN_QUIT, ride_id=None, ended_at=None)
    )
    monkeypatch.setattr(app_module, "_show_no_ride_info", lambda _parent: None)

    app_module._run_launch_flow(context, store)

    assert _entries(build_log_path(tmp_path, _LAUNCH)) == [
        {
            "level": "INFO",
            "event": "launch",
            "previous_state": "clean_quit",
            "previous_ride_id": None,
            "choice": "no_ride",
        }
    ]


class _PreviousSessionStore:
    """A store double answering only ``previous_session``."""

    def __init__(self, session: PreviousSession) -> None:
        """Store the record ``previous_session`` returns."""
        self._session = session

    def previous_session(self) -> PreviousSession:
        """Return the staged previous-session record."""
        return self._session


def test_run_launch_flow_without_a_log_still_runs_the_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1: a log-less app still shows the No Ride Open alert."""
    context = _context(log=None)
    store = _PreviousSessionStore(
        PreviousSession(state=SessionState.CLEAN_QUIT, ride_id=None, ended_at=None)
    )
    shown: list[object] = []
    monkeypatch.setattr(app_module, "_show_no_ride_info", shown.append)

    app_module._run_launch_flow(context, store)

    assert shown == [context.frame]


class _FakeEngine:
    """An engine double carrying the store-append sink."""

    def __init__(self) -> None:
        """Start with no event sink."""
        self.on_event: object = None


class _RideStore:
    """A store double answering the ride-loading reads."""

    def __init__(self) -> None:
        """Start over an empty roster and one engine."""
        self.engine = _FakeEngine()

    def roster_for(self, _ride_id: int) -> Roster:
        """Return the ride's (empty) roster."""
        return Roster()

    def load_engine(self, _ride_id: int, _roster: Roster, **_kwargs: object) -> _FakeEngine:
        """Return the staged engine, ignoring the clock seam."""
        return self.engine


def test_switch_console_to_ride_given_a_log_records_the_loaded_ride(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: the ride-loaded record names the ride the store loaded."""
    log = Logging(build_log_path(tmp_path, _LAUNCH))
    context = _context(log=log)
    context.store = _RideStore()
    context.console_view = object()
    monkeypatch.setattr(app_module, "_swap_console_onto", lambda *_args, **_kwargs: None)

    app_module._switch_console_to_ride(context, 7)

    assert _entries(build_log_path(tmp_path, _LAUNCH)) == [
        {"level": "INFO", "event": "ride_loaded", "ride_id": 7}
    ]


def test_switch_console_to_ride_without_a_log_still_swaps_the_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1: a log-less app still switches the console onto the ride."""
    context = _context(log=None)
    context.store = _RideStore()
    context.console_view = object()
    monkeypatch.setattr(app_module, "_swap_console_onto", lambda *_args, **_kwargs: None)

    app_module._switch_console_to_ride(context, 9)

    assert context.active_ride_id == 9


# --- W4/Phase 2: a schema-version mismatch is an expected exit -------


def test_main_given_a_schema_version_mismatch_shows_a_danger_box_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mismatched database is diagnosed, never crashed on.

    A file written by a different build cannot be read; the operator
    is told exactly what to do (rename or delete it) and the process
    exits non-zero without re-raising, because the generic crash path
    would file an exception record for an expected condition.
    """
    message = (
        "Database schema version 2 does not match this build's schema version 1. "
        "Rename or delete the database file to continue."
    )
    _install_fakes(
        monkeypatch,
        tmp_path,
        verbose_logging=False,
        open_error=SchemaVersionMismatchError(message),
    )
    shown: list[tuple[object, str, str]] = []
    monkeypatch.setattr(
        std_dialogs,
        "show_error",
        lambda parent, title, text: shown.append((parent, title, text)),
    )

    exit_code = app_module.main()

    assert exit_code == 1
    assert shown == [(None, "Database Mismatch", message)]


def test_main_given_a_generic_start_failure_still_shows_the_box_and_reraises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every other start failure keeps the W3 crash path unchanged."""
    _install_fakes(monkeypatch, tmp_path, verbose_logging=False, open_error=RuntimeError("boom"))
    shown: list[tuple[object, str, str]] = []
    monkeypatch.setattr(
        std_dialogs,
        "show_error",
        lambda parent, title, text: shown.append((parent, title, text)),
    )

    with pytest.raises(RuntimeError, match=re.escape("boom")):
        app_module.main()

    assert shown == [
        (None, "RiverCrossing Could Not Start", "RiverCrossing could not start:\nboom")
    ]


def test_main_given_a_start_failure_closes_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The finally closes the log even when the store open fails."""
    _install_fakes(monkeypatch, tmp_path, verbose_logging=False, open_error=RuntimeError("boom"))
    monkeypatch.setattr(std_dialogs, "show_error", lambda *_args: None)

    with pytest.raises(RuntimeError, match=re.escape("boom")):
        app_module.main()

    assert [record["event"] for record in _records(_written_log(tmp_path))] == ["app_start"]
