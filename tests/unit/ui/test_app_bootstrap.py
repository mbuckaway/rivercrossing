# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for ``app.main``'s bootstrap order (G, F3/F4).

``main`` owns the one order the launch has to hold: build the app,
open the verbose log next to ``settings.json``, install the
whitelisted control-event filter and the crash hook, open the store,
build and show ``main_window``, and only then hand the event loop the
deferred work. Two of those steps are pinned here without ever
entering ``MainLoop`` (which blocks) or constructing a real window:

- **G**: the launch flow (``resume_dlg`` / the No Ride Open alert) is
  deferred through ``wx.CallAfter`` -- it must run on the running
  event loop, after the frame is shown and the menubar is live, not
  synchronously while the frame is still being set up.
- **F3/F4**: the verbose log is constructed from the loaded
  settings' ``verbose_logging``, records its launch marker, and the
  control-event filter is installed on the app.

Every wx touch is faked (T-10: the GUI toolkit is the I/O boundary)
and every heavy collaborator -- ``build_app``, ``Store``,
``_bootstrap_window`` -- is a recording double, so the test drives
``main``'s own ordering and nothing else.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rivercrossing.ui import app as app_module
from rivercrossing.ui.logging import VERBOSE_LOG_NAME, VerboseLog
from rivercrossing.ui.presenters import settings as settings_store
from rivercrossing.ui.presenters.settings import AppSettings

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
        """Start with a launch context and no loop calls."""
        self.launch_context = object()
        self.main_loop_calls = 0
        self.verbose_log: object = None

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
    verbose filter's whitelist compares against.
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


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    verbose_logging: bool,
) -> _Bootstrap:
    """Swap ``main``'s collaborators for recording doubles."""
    store = _FakeStore("staged")
    bootstrap = _Bootstrap(store)

    def _open(_cls: object, path: object) -> _FakeStore:
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


def test_main_constructs_the_verbose_log_next_to_the_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3: main builds the log by the settings path and marks launch."""
    bootstrap = _install_fakes(monkeypatch, tmp_path, verbose_logging=True)

    app_module.main()

    log = bootstrap.app.verbose_log
    assert isinstance(log, VerboseLog)
    assert log.enabled is True
    assert [record["msg"] for record in _records(tmp_path / VERBOSE_LOG_NAME)] == [
        "RiverCrossing launching"
    ]


def test_main_given_verbose_logging_off_starts_the_log_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3: the loaded settings' flag seeds the log's enabled state."""
    bootstrap = _install_fakes(monkeypatch, tmp_path, verbose_logging=False)

    app_module.main()

    log = bootstrap.app.verbose_log
    assert isinstance(log, VerboseLog)
    assert log.enabled is False
    assert _records(tmp_path / VERBOSE_LOG_NAME) == []


def test_main_installs_the_control_event_filter_after_building_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F4: one whitelisted-control filter is added to the live app."""
    bootstrap = _install_fakes(monkeypatch, tmp_path, verbose_logging=True)

    app_module.main()

    assert len(bootstrap.wx.filters) == 1
    assert isinstance(bootstrap.wx.filters[0], _FakeEventFilter)
