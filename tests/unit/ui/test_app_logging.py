# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the app's structured-log wiring (F3/F4/D1).

``ui.logging.Logging`` owns the NDJSON file; this module pins the
four seams ``app.py`` owns that feed it, all headless (no window is
constructed):

- **F3 menu logging.** :func:`~rivercrossing.ui.app._bind_routes`
  wraps every bound ``EVT_MENU`` handler so the selection is recorded
  with its id, menu and label before the route dispatches -- which
  also covers accelerator-triggered menu events.
- **F3 settings toggle.**
  :func:`~rivercrossing.ui.app._apply_settings_live` applies the
  dialog's ``verbose_logging`` checkbox to the live log.
- **F4 control logging.**
  :func:`~rivercrossing.ui.app._make_event_filter` records the
  whitelisted control events and skips everything else, and never
  swallows an event.
- **D1 unauthored-window marker.**
  :func:`~rivercrossing.ui.app._open_target` records a marker when a
  route's XRC target loads no window -- a menu row clicking through
  to nothing, the silent-death class -- and still posts the
  status-bar notice.

A real :class:`~rivercrossing.ui.logging.Logging` over ``tmp_path``
is the assertion surface, so each test checks the record that would
reach a support session rather than that a mock was called.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import wx
import wx.xrc

from rivercrossing.roster import Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import commands
from rivercrossing.ui.logging import Logging, build_log_path
from rivercrossing.ui.presenters.settings import AppSettings

if TYPE_CHECKING:
    from pathlib import Path

# A fixed launch instant names every test's invocation log.
_LAUNCH = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)

# The per-run transport keys the formatter adds to every record; the
# assertions compare each record's own event and fields only.
_VOLATILE = frozenset({"ts", "file", "line", "func"})


def _log_path(directory: Path) -> Path:
    """Return this test's invocation log path inside *directory*."""
    return build_log_path(directory, _LAUNCH)


class _NoticeFrame:
    """A frame double recording status notices and menu bindings."""

    def __init__(self) -> None:
        """Start with empty notice and binding logs."""
        self.notices: list[str] = []
        self.binds: list[tuple[object, object]] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record one status-bar notice."""
        self.notices.append(text)

    def Bind(self, _event: object, handler: object, id: object = None) -> None:  # noqa: N802, A002 -- wx API names
        """Record one bound handler under its resolved id."""
        self.binds.append((id, handler))


class _AppWithLog:
    """A minimal live-app double carrying the structured log."""

    def __init__(self, log: Logging | None) -> None:
        """Store the app's log (``None`` when un-wired)."""
        self.log = log


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


def _context(
    *,
    frame: object,
    log: Logging | None,
    resource: object = None,
) -> app_module._RouteContext:
    """Build a route context over *frame* carrying *log* (or none).

    *resource* is the XRC resource a target opens through; the
    ``None`` default matches the route-level contexts that never load
    a dialog.
    """
    return app_module._RouteContext(
        frame=frame,
        resource=resource,
        roster=Roster(),
        app=_AppWithLog(log),
        theme_controller=None,
    )


class _AppWithoutLog:
    """An app double carrying no ``log`` attribute at all."""


def test_log_given_an_app_without_the_attribute_returns_none() -> None:
    """F1: an app built without main() has no log to write to."""
    context = app_module._RouteContext(
        frame=_NoticeFrame(),
        resource=None,
        roster=Roster(),
        app=_AppWithoutLog(),
        theme_controller=None,
    )

    assert app_module._log(context) is None


# ------------------------------------------------- F3: menu logging


def _bound_handler(frame: _NoticeFrame, item_id: str) -> object:
    """Return the handler bound for *item_id*'s resolved wx id."""
    return dict(frame.binds)[wx.xrc.XRCID(item_id)]


def test_bind_routes_given_a_log_records_the_menu_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3: the wrapper records id/menu/label before the dispatch."""
    log = Logging(_log_path(tmp_path))
    frame = _NoticeFrame()
    context = _context(frame=frame, log=log)
    opened: list[object] = []
    monkeypatch.setattr(app_module, "_open_target", lambda _ctx, route: opened.append(route))

    app_module._bind_routes(context)
    route = commands.route_for_id("mi_open_library")
    _bound_handler(frame, "mi_open_library")(object())

    assert opened == [route]
    assert _entries(_log_path(tmp_path)) == [
        {
            "level": "DEBUG",
            "event": "menu",
            "item_id": wx.xrc.XRCID("mi_open_library"),
            "menu": route.menu,
            "label": route.label,
        }
    ]


def test_bind_routes_without_a_log_still_dispatches_the_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F3: an app with no log bound still fires its routes unchanged."""
    frame = _NoticeFrame()
    context = _context(frame=frame, log=None)
    opened: list[object] = []
    monkeypatch.setattr(app_module, "_open_target", lambda _ctx, route: opened.append(route))

    app_module._bind_routes(context)
    _bound_handler(frame, "mi_open_library")(object())

    assert opened == [commands.route_for_id("mi_open_library")]


# ------------------------------------------- F3: the settings toggle


class _SettingsFrame(_NoticeFrame):
    """A frame double answering the settings apply's menubar sync."""

    def __init__(self) -> None:
        """Start with an empty check log."""
        super().__init__()
        self.checks: list[tuple[int, bool]] = []

    def GetMenuBar(self) -> _SettingsFrame:  # noqa: N802 -- wx API name
        """Return this frame as the menubar double."""
        return self

    def Check(self, item_id: int, checked: bool) -> None:  # noqa: N802, FBT001 -- wx API name, positional bool
        """Record one menu check sync."""
        self.checks.append((item_id, checked))


class _FakeThemeController:
    """A theme controller reporting no notice."""

    def apply_mode(self, _mode: object) -> None:
        """Apply nothing and report no notice."""


def _settings(*, verbose_logging: bool) -> AppSettings:
    """Build settings with only the verbose flag under test varied."""
    return AppSettings(
        appearance="system",
        sound_on=True,
        hide_times=False,
        zoom_percent=100,
        verbose_logging=verbose_logging,
    )


@pytest.mark.parametrize("verbose_logging", [True, False])
def test_apply_settings_live_given_the_verbose_flag_applies_it_to_the_log(
    tmp_path: Path, *, verbose_logging: bool
) -> None:
    """F3: the settings dialog's checkbox drives the live log."""
    log = Logging(_log_path(tmp_path), verbose=not verbose_logging)
    context = app_module._RouteContext(
        frame=_SettingsFrame(),
        resource=None,
        roster=Roster(),
        app=_AppWithLog(log),
        theme_controller=_FakeThemeController(),
    )
    context.settings_path = tmp_path / "settings.json"

    app_module._apply_settings_live(context, _settings(verbose_logging=verbose_logging))

    assert log.verbose is verbose_logging


def test_apply_settings_live_without_a_log_applies_the_rest(
    tmp_path: Path,
) -> None:
    """F3: an app carrying no log still applies every other setting."""
    frame = _SettingsFrame()
    context = app_module._RouteContext(
        frame=frame,
        resource=None,
        roster=Roster(),
        app=_AppWithLog(None),
        theme_controller=_FakeThemeController(),
    )
    context.settings_path = tmp_path / "settings.json"

    app_module._apply_settings_live(context, _settings(verbose_logging=False))

    assert context.settings.verbose_logging is False


# ---------------------------------------------- F4: the event filter


class _FakeControl:
    """A control double exposing the two names the filter reads."""

    def __init__(self, name: str, label: str = "") -> None:
        """Store the frozen name and visible label."""
        self._name = name
        self._label = label

    def GetName(self) -> str:  # noqa: N802 -- wx API name
        """Return the control's frozen name."""
        return self._name

    def GetLabel(self) -> str:  # noqa: N802 -- wx API name
        """Return the control's visible label."""
        return self._label


class _FakeCommandEvent:
    """A command-event double exposing type and event object."""

    def __init__(self, event_type: int, control: object = None) -> None:
        """Store the event type id and its originating control."""
        self._event_type = event_type
        self._control = control

    def GetEventType(self) -> int:  # noqa: N802 -- wx API name
        """Return the numeric event type."""
        return self._event_type

    def GetEventObject(self) -> object:  # noqa: N802 -- wx API name
        """Return the control the event names, if any."""
        return self._control


def test_event_filter_given_a_button_event_logs_the_button(tmp_path: Path) -> None:
    """F4: a button activation records its frozen name and label."""
    log = Logging(_log_path(tmp_path))
    event_filter = app_module._make_event_filter(log)
    event = _FakeCommandEvent(wx.EVT_BUTTON.typeId, _FakeControl("backup_now_btn", "Back up now"))

    result = event_filter.FilterEvent(event)

    assert result == wx.EventFilter.Event_Skip
    assert _entries(_log_path(tmp_path)) == [
        {
            "level": "DEBUG",
            "event": "button",
            "name": "backup_now_btn",
            "label": "Back up now",
        }
    ]


@pytest.mark.parametrize(
    ("event_type", "kind"),
    [
        (wx.EVT_CHECKBOX.typeId, "CheckBox"),
        (wx.EVT_RADIOBUTTON.typeId, "RadioButton"),
        (wx.EVT_CHOICE.typeId, "Choice"),
        (wx.EVT_TEXT.typeId, "TextCtrl"),
    ],
    ids=["checkbox", "radiobutton", "choice", "text"],
)
def test_event_filter_given_a_whitelisted_control_logs_its_kind(
    tmp_path: Path,
    event_type: int,
    kind: str,
) -> None:
    """F4: each other whitelisted control logs its frozen name/kind."""
    log = Logging(_log_path(tmp_path))
    event_filter = app_module._make_event_filter(log)

    result = event_filter.FilterEvent(_FakeCommandEvent(event_type, _FakeControl("sound_chk")))

    assert result == wx.EventFilter.Event_Skip
    assert _entries(_log_path(tmp_path)) == [
        {"level": "DEBUG", "event": "control", "name": "sound_chk", "kind": kind}
    ]


@pytest.mark.parametrize(
    "event_type",
    [wx.EVT_PAINT.typeId, wx.EVT_MOTION.typeId, wx.EVT_TIMER.typeId, wx.EVT_IDLE.typeId],
    ids=["paint", "motion", "timer", "idle"],
)
def test_event_filter_given_a_noise_event_writes_nothing(
    tmp_path: Path,
    event_type: int,
) -> None:
    """F4: paint/mouse/timer/idle traffic never reaches the log."""
    log = Logging(_log_path(tmp_path))
    event_filter = app_module._make_event_filter(log)
    event = _FakeCommandEvent(event_type, _FakeControl("plate_input"))

    result = event_filter.FilterEvent(event)

    assert result == wx.EventFilter.Event_Skip
    assert _entries(_log_path(tmp_path)) == []


def test_event_filter_given_a_non_verbose_log_writes_nothing(tmp_path: Path) -> None:
    """F4: an opted-out log skips before touching the control."""
    log = Logging(_log_path(tmp_path), verbose=False)
    event_filter = app_module._make_event_filter(log)
    event = _FakeCommandEvent(wx.EVT_BUTTON.typeId, _FakeControl("backup_now_btn", "Back up now"))

    result = event_filter.FilterEvent(event)

    assert result == wx.EventFilter.Event_Skip
    assert _entries(_log_path(tmp_path)) == []


def test_event_filter_given_a_whitelisted_type_with_no_object_writes_nothing(
    tmp_path: Path,
) -> None:
    """F4: an event naming no control is skipped, never raised on."""
    log = Logging(_log_path(tmp_path))
    event_filter = app_module._make_event_filter(log)

    result = event_filter.FilterEvent(_FakeCommandEvent(wx.EVT_BUTTON.typeId))

    assert result == wx.EventFilter.Event_Skip
    assert _entries(_log_path(tmp_path)) == []


# --------------------------------- D1: the unauthored-window marker


class _MissingWindowResource:
    """An ``XmlResource`` double whose targets load no window (D1)."""

    def LoadDialog(self, _parent: object, _name: object) -> None:  # noqa: N802 -- wx API name
        """Report the target has no authored window."""


def test_open_target_given_no_authored_window_records_the_marker_and_posts_the_notice(
    tmp_path: Path,
) -> None:
    """D1: a click-through to nothing is in log and notice."""
    log = Logging(_log_path(tmp_path))
    frame = _NoticeFrame()
    context = _context(frame=frame, log=log, resource=_MissingWindowResource())
    route = commands.route_for_id("mi_standings")

    app_module._open_target(context, route)

    assert _entries(_log_path(tmp_path)) == [
        {
            "level": "DEBUG",
            "event": "marker",
            "msg": "Standings: no window authored for target 'results_dlg'",
        }
    ]
    assert frame.notices == ["Standings — no window authored yet"]


def test_open_target_given_no_authored_window_and_no_log_still_posts_the_notice() -> None:
    """D1: an app with no log still posts the notice."""
    frame = _NoticeFrame()
    context = _context(frame=frame, log=None, resource=_MissingWindowResource())

    app_module._open_target(context, commands.route_for_id("mi_standings"))

    assert frame.notices == ["Standings — no window authored yet"]
