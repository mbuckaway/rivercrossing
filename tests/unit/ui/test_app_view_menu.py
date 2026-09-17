# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the View menu's setting rows (R-37, E8.1.4).

The ``view_setting`` row carries nine ids, dispatched by the fired id
alone: the two time-column check items
(``mi_show_total_times``/``mi_show_lap_time``) flip their own
``AppSettings`` field, persist it, set their own check item explicitly
(a synthetic ``EVT_MENU`` never ticks an item on this pin) and apply
both flags through the console presenter's ``on_time_columns``; the
seven ``mi_zoom_*`` radios set the zoom, tick the fired radio and
persist the percent; any other id falls back to the generic "not yet
implemented" notice.

A refused settings write is a notice, not a crash -- and never an
aborted toggle: these handlers run inside a wx menu event, where an
unguarded raise is swallowed with zero signal (measured), so the flip
is still applied and reported (``_apply_settings_live``'s idiom:
``_log_warn`` plus ``SetStatusText``).

No wx window is constructed. The menubar double is keyed by the real
``wx.xrc.XRCID`` id space (the ``test_app_publish_menu`` pattern), the
console double records the R-37 column push exactly as
``test_app_ride_switch``'s does, and the zoom controller's own
``set_percent`` is the GUI I/O boundary stubbed here (T-10).
"""

from typing import TYPE_CHECKING

import pytest

from rivercrossing.roster import Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import commands, ids, zoom
from rivercrossing.ui.presenters.settings import default_settings

if TYPE_CHECKING:
    from pathlib import Path

# R-37: each AppSettings field and the View-menu check item mirroring
# it, transcribed independently of app.py's own map.
TIME_COLUMN_ITEMS = {
    "show_total_times": ids.MI_SHOW_TOTAL_TIMES,
    "show_lap_time": ids.MI_SHOW_LAP_TIME,
}
TIME_COLUMN_KEYS = list(TIME_COLUMN_ITEMS)

# What one toggle of each key publishes to the presenter, from
# ``default_settings()`` (total hidden, lap time shown): the flip of
# that one key, both flags carried.
APPLIED_BY_KEY = {
    "show_total_times": {"show_total": True, "show_lap": True},
    "show_lap_time": {"show_total": False, "show_lap": False},
}

# The two time items plus the seven zoom radios: every id this row
# resolves through the real ``wx.xrc.XRCID`` space.
_MENU_ITEM_NAMES = (*TIME_COLUMN_ITEMS.values(), *zoom.ZOOM_MENU_ITEM_IDS)

# T-4: the ladder's ends and its default, each radio's own percent.
# The ladder's mapping is test_zoom.py's concern; what is driven here
# is this row's dispatch.
ZOOM_ROWS = {ids.MI_ZOOM_90: 90, ids.MI_ZOOM_120: 120, ids.MI_ZOOM_150: 150}


class _FakeMenuItem:
    """A menu item double carrying its check state."""

    def __init__(self) -> None:
        """Start unchecked, like a fresh XRC item."""
        self.checked = False


class _FakeMenuBar:
    """A menubar double over the real ``wx.xrc.XRCID`` id space."""

    def __init__(self) -> None:
        """Build one item double per id this row dispatches."""
        import wx.xrc  # noqa: PLC0415 -- the real id space the SUT uses

        self._names = {wx.xrc.XRCID(name): name for name in _MENU_ITEM_NAMES}
        self.items = {name: _FakeMenuItem() for name in _MENU_ITEM_NAMES}
        self.checks: list[tuple[str, bool]] = []

    def Check(self, real_id: int, checked: bool) -> None:  # noqa: N802, FBT001 -- wx API name
        """Record the check state on the item *real_id* names."""
        name = self._names.get(real_id)
        if name is None:
            return
        self.items[name].checked = checked
        self.checks.append((name, checked))


class _MenuFrame:
    """A frame double: status notices and the staged menubar."""

    def __init__(self, menubar: _FakeMenuBar) -> None:
        """Store the menubar the SUT checks items through."""
        self._menubar = menubar
        self.notices: list[str] = []

    def GetMenuBar(self) -> _FakeMenuBar:  # noqa: N802 -- wx API name
        """Return the staged menubar."""
        return self._menubar

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record one status notice."""
        self.notices.append(text)


class _FakePresenter:
    """A console presenter double recording the R-37 column push."""

    def __init__(self) -> None:
        """Start with an empty push log."""
        self.calls: list[dict[str, bool]] = []

    def on_time_columns(self, *, show_total: bool, show_lap: bool) -> None:
        """Record the two flags the toggle published."""
        self.calls.append({"show_total": show_total, "show_lap": show_lap})


class _MenuEvent:
    """A ``wx.CommandEvent`` double carrying only the fired id."""

    def __init__(self, real_id: int) -> None:
        """Store the runtime id the handler dispatches on."""
        self._real_id = real_id

    def GetId(self) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Return the fired id."""
        return self._real_id


def _real_id(item_id: str) -> int:
    """Return the runtime id of the frozen menu-item *item_id*."""
    import wx.xrc  # noqa: PLC0415 -- the real id space the SUT resolves through

    return wx.xrc.XRCID(item_id)


def _context(
    settings_path: Path,
    *,
    menubar: _FakeMenuBar | None = None,
    presenter: _FakePresenter | None = None,
) -> app_module._RouteContext:
    """Build a route context over the menu doubles and the file."""
    context = app_module._RouteContext(
        frame=_MenuFrame(_FakeMenuBar() if menubar is None else menubar),
        resource=None,
        roster=Roster(),
        app=None,
        theme_controller=None,
    )
    context.presenter = presenter
    context.settings_path = settings_path
    return context


def _refuse_save(_settings: object, _path: object) -> None:
    """Stand in for an unwritable settings file (a full disk)."""
    raise OSError("disk full")


# ------------------------------------------- one time column's toggle


@pytest.mark.parametrize("key", TIME_COLUMN_KEYS)
def test_toggle_time_column_given_each_key_flips_only_that_setting(
    tmp_path: Path, key: str
) -> None:
    """R-37: the two column flags are independent."""
    context = _context(tmp_path / "settings.json")
    before = context.settings

    app_module._toggle_time_column(context, key=key)

    assert getattr(context.settings, key) is (not getattr(before, key))


@pytest.mark.parametrize("key", TIME_COLUMN_KEYS)
def test_toggle_time_column_given_each_key_persists_the_flipped_setting(
    tmp_path: Path, key: str
) -> None:
    """The flip reaches the settings file, not only the context."""
    path = tmp_path / "settings.json"
    context = _context(path)
    before = context.settings

    app_module._toggle_time_column(context, key=key)

    assert getattr(app_module.settings_store.load_settings(path), key) is (
        not getattr(before, key)
    )


@pytest.mark.parametrize("key", TIME_COLUMN_KEYS)
def test_toggle_time_column_given_each_key_checks_its_own_item(tmp_path: Path, key: str) -> None:
    """A synthetic event never ticks the item, so it is set here."""
    menubar = _FakeMenuBar()
    context = _context(tmp_path / "settings.json", menubar=menubar)

    app_module._toggle_time_column(context, key=key)

    assert menubar.checks == [(TIME_COLUMN_ITEMS[key], getattr(context.settings, key))]


@pytest.mark.parametrize("key", TIME_COLUMN_KEYS)
def test_toggle_time_column_given_a_presenter_applies_both_flags(tmp_path: Path, key: str) -> None:
    """Both feed columns re-render, not only the flipped one."""
    presenter = _FakePresenter()
    context = _context(tmp_path / "settings.json", presenter=presenter)

    app_module._toggle_time_column(context, key=key)

    assert presenter.calls == [APPLIED_BY_KEY[key]]


def test_toggle_time_column_given_no_presenter_still_flips_and_checks(
    tmp_path: Path,
) -> None:
    """T-3: a context with no live console toggles without a crash."""
    menubar = _FakeMenuBar()
    context = _context(tmp_path / "settings.json", menubar=menubar)

    app_module._toggle_time_column(context, key="show_total_times")

    assert context.settings.show_total_times is True
    assert menubar.checks == [(ids.MI_SHOW_TOTAL_TIMES, True)]


def test_toggle_time_column_given_a_refused_save_still_toggles_and_posts_a_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unwritable file is a notice, never an aborted toggle."""
    menubar = _FakeMenuBar()
    presenter = _FakePresenter()
    context = _context(
        tmp_path / "settings.json",
        menubar=menubar,
        presenter=presenter,
    )
    monkeypatch.setattr(app_module.settings_store, "save_settings", _refuse_save)

    app_module._toggle_time_column(context, key="show_total_times")

    assert context.frame.notices == ["Could not save settings: disk full"]
    assert context.settings.show_total_times is True
    assert menubar.checks == [(ids.MI_SHOW_TOTAL_TIMES, True)]
    assert presenter.calls == [APPLIED_BY_KEY["show_total_times"]]


# ------------------------------------------------ the row's dispatch


@pytest.mark.parametrize("key", TIME_COLUMN_KEYS)
def test_handle_view_row_given_a_time_column_id_toggles_that_column(
    tmp_path: Path, key: str
) -> None:
    """R-37: each time-column id reaches its own toggle."""
    item_id = TIME_COLUMN_ITEMS[key]
    context = _context(tmp_path / "settings.json")
    route = commands.route_for_id(item_id)

    app_module._handle_view_row(context, route, _MenuEvent(_real_id(item_id)))

    assert getattr(context.settings, key) is (not getattr(default_settings(), key))


@pytest.mark.parametrize("item_id", list(ZOOM_ROWS), ids=list(ZOOM_ROWS))
def test_handle_view_row_given_a_zoom_radio_applies_ticks_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, item_id: str
) -> None:
    """E8.1.4: the radio's percent is applied, ticked and saved."""
    percent = ZOOM_ROWS[item_id]
    path = tmp_path / "settings.json"
    menubar = _FakeMenuBar()
    context = _context(path, menubar=menubar)
    route = commands.route_for_id(item_id)
    applied: list[int] = []
    monkeypatch.setattr(app_module.zoom, "set_percent", applied.append)

    app_module._handle_view_row(context, route, _MenuEvent(_real_id(item_id)))

    assert applied == [percent]
    assert context.settings.zoom_percent == percent
    assert menubar.checks == [(item_id, True)]
    assert app_module.settings_store.load_settings(path).zoom_percent == percent


def test_handle_view_row_given_a_foreign_id_posts_the_not_implemented_notice(
    tmp_path: Path,
) -> None:
    """T-3: an id this row does not own falls back to the notice."""
    context = _context(tmp_path / "settings.json")
    route = commands.route_for_id(ids.MI_STANDINGS)

    app_module._handle_view_row(context, route, _MenuEvent(_real_id(ids.MI_STANDINGS)))

    assert context.frame.notices == [f"{route.label} — not yet implemented"]


def test_handle_view_row_given_a_zoom_radio_and_a_refused_save_still_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-3: the zoom still applies when the percent cannot be stored."""
    menubar = _FakeMenuBar()
    context = _context(tmp_path / "settings.json", menubar=menubar)
    route = commands.route_for_id(ids.MI_ZOOM_120)
    applied: list[int] = []
    monkeypatch.setattr(app_module.zoom, "set_percent", applied.append)
    monkeypatch.setattr(app_module.settings_store, "save_settings", _refuse_save)

    app_module._handle_view_row(context, route, _MenuEvent(_real_id(ids.MI_ZOOM_120)))

    assert context.frame.notices == ["Could not save settings: disk full"]
    assert (applied, context.settings.zoom_percent) == ([120], 120)
    assert menubar.checks == [(ids.MI_ZOOM_120, True)]
