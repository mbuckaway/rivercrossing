# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the Results publish menu wiring (G6).

G6 moved the results dialog's five publish checkboxes onto the Results
menu as one checkable row (``commands.ROUTE_TABLE``'s
``results_publish`` target), with their flags persisted as
``AppSettings.publish_*``. This module drives the app-side seams
headlessly, over a menubar double keyed by the real ``wx.xrc.XRCID``
id space (the ``test_app_exports`` / ``test_menu_state`` pattern -- no
window is constructed):

- :func:`~rivercrossing.ui.app._check_loaded_publish_options` -- the
  startup / Settings-OK mirror, and R-63's gate on the Fastest-time
  board item;
- :func:`~rivercrossing.ui.app._toggle_publish_option` -- one check
  item's live flip: setting, persisted file, menu check state, and the
  gate;
- :func:`~rivercrossing.ui.app._handle_results_publish_row` -- the
  five ids dispatched to their own setting;
- :func:`~rivercrossing.ui.app._make_route_handler` -- the
  ``results_publish`` route reaching that dispatcher;
- :func:`~rivercrossing.ui.app._apply_menu_state` -- re-applying the
  gate after ``menu_state.apply_to_menubar``, which re-enables every
  routed item on each ride-state change and would otherwise clobber
  R-63's disable.
"""

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from rivercrossing.ride import RideStatus
from rivercrossing.ui import app as app_module
from rivercrossing.ui import commands, ids
from rivercrossing.ui.presenters.settings import AppSettings, default_settings

if TYPE_CHECKING:
    from pathlib import Path

# G6: each setting and the Results-menu item that mirrors it, in the
# menu's own order -- transcribed independently of app.py's map.
PUBLISH_ITEMS = (
    ("publish_show_times", ids.MI_SHOW_TIMES),
    ("publish_laps_board", ids.MI_LAPS_BOARD),
    ("publish_time_board", ids.MI_TIME_BOARD),
    ("publish_full_field", ids.MI_FULL_FIELD),
    ("publish_all_cards", ids.MI_ALL_CARDS),
)
PUBLISH_ITEM_IDS = [item_id for _key, item_id in PUBLISH_ITEMS]
PUBLISH_KEYS = [key for key, _item_id in PUBLISH_ITEMS]

# The five settings-surface names, plus the Fastest-time board item the
# R-63 gate acts on (one of the five) and nothing else.
MENU_ITEM_NAMES = (*PUBLISH_ITEM_IDS, ids.MI_TIME_BOARD)


class _FakeMenuItem:
    """A menu item double recording its enablement and check state."""

    def __init__(self) -> None:
        """Start unchecked and enabled, like a fresh check item."""
        self.enabled = True
        self.checked = False

    def Enable(self, enabled: bool) -> None:  # noqa: N802, FBT001 -- wx API name
        """Record the enablement verdict."""
        self.enabled = enabled


class _FakeMenuBar:
    """A menubar double over the real ``wx.xrc.XRCID`` id space."""

    def __init__(self, names: tuple[str, ...] = tuple(MENU_ITEM_NAMES)) -> None:
        """Build one item double per frozen *names* entry."""
        import wx.xrc  # noqa: PLC0415 -- the real id space the SUT resolves through

        self._names = {wx.xrc.XRCID(name): name for name in names}
        self.items = {name: _FakeMenuItem() for name in names}

    def FindItem(  # noqa: N802 -- wx API name
        self, real_id: int
    ) -> tuple[_FakeMenuItem | None, None]:
        """Return the item for *real_id*, or a ``(None, None)`` miss."""
        name = self._names.get(real_id)
        return (None, None) if name is None else (self.items[name], None)

    def Check(self, real_id: int, checked: bool) -> None:  # noqa: N802, FBT001 -- wx API name
        """Record a check state on the item *real_id* names."""
        name = self._names.get(real_id)
        if name is not None:
            self.items[name].checked = checked


class _FakeFrame:
    """A frame double answering the menubar the SUT applies through."""

    def __init__(self, menubar: _FakeMenuBar | None) -> None:
        """Store the menubar (None = no menu bar)."""
        self._menubar = menubar
        self.notices: list[str] = []

    def GetMenuBar(self) -> _FakeMenuBar | None:  # noqa: N802 -- wx API name
        """Return the staged menubar."""
        return self._menubar

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record one status notice."""
        self.notices.append(text)


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


class _FakeThemeController:
    """A theme controller double reporting no notice (settings OK)."""

    def apply_mode(self, _mode: object) -> None:
        """Apply nothing and report no notice."""


def _context(
    menubar: _FakeMenuBar | None,
    settings: AppSettings,
    settings_path: Path,
) -> app_module._RouteContext:
    """Build a route context over *menubar*, *settings* and the file."""
    context = app_module._RouteContext(
        frame=_FakeFrame(menubar),
        resource=None,
        roster=None,  # type: ignore[arg-type]
        app=None,
        theme_controller=_FakeThemeController(),  # type: ignore[arg-type]
    )
    context.settings = settings
    context.settings_path = settings_path
    return context


def _settings(**flags: bool) -> AppSettings:
    """Return the defaults with the given ``publish_*`` flags set."""
    return replace(default_settings(), **flags)


# ------------------------------- the loaded / applied check states


LOADED_CASES = (
    _settings(),
    _settings(
        publish_show_times=True,
        publish_laps_board=False,
        publish_time_board=True,
        publish_full_field=False,
        publish_all_cards=False,
    ),
)
LOADED_IDS = ("canvas_defaults", "all_flipped")


@pytest.mark.parametrize("settings", LOADED_CASES, ids=LOADED_IDS)
def test_check_loaded_publish_options_given_settings_checks_each_mirror_item(
    settings: AppSettings,
) -> None:
    """Each publish_* flag is applied to its own menu item."""
    menubar = _FakeMenuBar()

    app_module._check_loaded_publish_options(menubar, settings)

    assert {name: menubar.items[name].checked for name in PUBLISH_ITEM_IDS} == {
        item_id: getattr(settings, key) for key, item_id in PUBLISH_ITEMS
    }


def test_check_loaded_publish_options_given_times_off_disables_the_time_board() -> None:
    """R-63: with no times shown the board is off limits."""
    menubar = _FakeMenuBar()

    app_module._check_loaded_publish_options(menubar, _settings(publish_show_times=False))

    assert menubar.items[ids.MI_TIME_BOARD].enabled is False


def test_check_loaded_publish_options_given_times_off_unchecks_the_time_board() -> None:
    """R-63: and its stored tick is cleared with the disable."""
    menubar = _FakeMenuBar()

    app_module._check_loaded_publish_options(
        menubar, _settings(publish_show_times=False, publish_time_board=True)
    )

    assert menubar.items[ids.MI_TIME_BOARD].checked is False


def test_check_loaded_publish_options_given_times_on_leaves_the_time_board_enabled() -> None:
    """R-63's other arm: times shown, so the board is requestable."""
    menubar = _FakeMenuBar()
    menubar.items[ids.MI_TIME_BOARD].enabled = False  # the state while times were off

    app_module._check_loaded_publish_options(menubar, _settings(publish_show_times=True))

    assert menubar.items[ids.MI_TIME_BOARD].enabled is True


def test_check_loaded_publish_options_given_a_missing_board_item_is_a_silent_skip() -> None:
    """A menubar without the board item (a relocated id) is safe."""
    menubar = _FakeMenuBar(names=(ids.MI_SHOW_TIMES,))

    app_module._check_loaded_publish_options(menubar, _settings(publish_show_times=False))

    assert menubar.items[ids.MI_SHOW_TIMES].checked is False


# ------------------------------------------- one check item's toggle


@pytest.mark.parametrize("key", PUBLISH_KEYS)
def test_toggle_publish_option_given_a_key_flips_that_setting(tmp_path: Path, key: str) -> None:
    """The flipped flag lands on the context's own settings."""
    settings = _settings()
    context = _context(_FakeMenuBar(), settings, tmp_path / "settings.json")

    app_module._toggle_publish_option(context, key=key)

    assert getattr(context.settings, key) is not getattr(settings, key)


@pytest.mark.parametrize("key", PUBLISH_KEYS)
def test_toggle_publish_option_given_a_key_persists_the_flipped_setting(
    tmp_path: Path, key: str
) -> None:
    """The flip is written to the settings file, not only held."""
    path = tmp_path / "settings.json"
    settings = _settings()
    context = _context(_FakeMenuBar(), settings, path)

    app_module._toggle_publish_option(context, key=key)

    assert getattr(app_module.settings_store.load_settings(path), key) is (
        not getattr(settings, key)
    )


# The four keys whose item's check state tracks its own setting alone.
# publish_time_board is excluded: R-63's gate owns that item's state
# whenever show-times is off (the two tests below cover both arms).
SELF_CHECKED_KEYS = [key for key in PUBLISH_KEYS if key != "publish_time_board"]


@pytest.mark.parametrize("key", SELF_CHECKED_KEYS)
def test_toggle_publish_option_given_a_key_sets_its_own_check_state(
    tmp_path: Path, key: str
) -> None:
    """A synthetic EVT_MENU never toggles the item, so it is set."""
    menubar = _FakeMenuBar()
    context = _context(menubar, _settings(publish_show_times=True), tmp_path / "settings.json")

    app_module._toggle_publish_option(context, key=key)

    assert menubar.items[app_module._RESULTS_PUBLISH_MENU_IDS[key]].checked is getattr(
        context.settings, key
    )


def test_toggle_publish_option_given_the_board_and_times_off_leaves_it_unchecked(
    tmp_path: Path,
) -> None:
    """R-63's gate owns the board item's state while times are off."""
    menubar = _FakeMenuBar()
    context = _context(menubar, _settings(publish_show_times=False), tmp_path / "settings.json")

    app_module._toggle_publish_option(context, key="publish_time_board")

    assert menubar.items[ids.MI_TIME_BOARD].checked is False


def test_toggle_publish_option_given_the_board_and_times_on_checks_it(
    tmp_path: Path,
) -> None:
    """T-3: with times shown the board item carries its own tick."""
    menubar = _FakeMenuBar()
    context = _context(menubar, _settings(publish_show_times=True), tmp_path / "settings.json")

    app_module._toggle_publish_option(context, key="publish_time_board")

    assert menubar.items[ids.MI_TIME_BOARD].checked is True


def test_toggle_publish_option_given_times_switched_off_clears_the_time_board(
    tmp_path: Path,
) -> None:
    """R-63: times off cannot leave a time board requested."""
    context = _context(
        _FakeMenuBar(),
        _settings(publish_show_times=True, publish_time_board=True),
        tmp_path / "settings.json",
    )

    app_module._toggle_publish_option(context, key="publish_show_times")

    assert (context.settings.publish_time_board, context.settings.publish_show_times) == (
        False,
        False,
    )


def test_toggle_publish_option_given_times_on_keeps_the_time_board_flag(
    tmp_path: Path,
) -> None:
    """T-3: the co-flip's other arm leaves a stored tick alone."""
    context = _context(
        _FakeMenuBar(),
        _settings(publish_show_times=False, publish_time_board=True),
        tmp_path / "settings.json",
    )

    app_module._toggle_publish_option(context, key="publish_show_times")

    assert (context.settings.publish_show_times, context.settings.publish_time_board) == (
        True,
        True,
    )


# ------------------------------------------------- the row dispatcher


@pytest.mark.parametrize(("key", "item_id"), PUBLISH_ITEMS, ids=PUBLISH_ITEM_IDS)
def test_handle_results_publish_row_given_an_id_flips_only_its_setting(
    tmp_path: Path, key: str, item_id: str
) -> None:
    """Each check item dispatches to its own setting alone."""
    context = _context(_FakeMenuBar(), _settings(), tmp_path / "settings.json")
    route = commands.route_for_id(item_id)

    app_module._handle_results_publish_row(context, route, _MenuEvent(_real_id(item_id)))

    changed = {
        name
        for name in PUBLISH_KEYS
        if getattr(context.settings, name) != getattr(default_settings(), name)
    }
    assert changed == {key}


def test_handle_results_publish_row_given_a_foreign_id_posts_a_notice(
    tmp_path: Path,
) -> None:
    """T-3: an id this row does not own falls back to the notice."""
    context = _context(_FakeMenuBar(), _settings(), tmp_path / "settings.json")
    route = commands.route_for_id(ids.MI_SHOW_TIMES)

    app_module._handle_results_publish_row(context, route, _MenuEvent(_real_id(ids.MI_STANDINGS)))

    assert context.frame.notices == [f"{route.label} — not yet implemented"]


def test_make_route_handler_given_the_publish_route_toggles_its_item(
    tmp_path: Path,
) -> None:
    """The results_publish route reaches the id dispatcher."""
    context = _context(_FakeMenuBar(), _settings(), tmp_path / "settings.json")
    route = commands.route_for_id(ids.MI_SHOW_TIMES)

    handler = app_module._make_route_handler(context, route)
    handler(_MenuEvent(_real_id(ids.MI_SHOW_TIMES)))

    assert context.settings.publish_show_times is True


# ---------------------------------------------- the gate after menus


def test_apply_menu_state_given_times_off_re_disables_the_time_board(
    tmp_path: Path,
) -> None:
    """The binder re-enables items, so the gate re-applies."""
    menubar = _FakeMenuBar()
    context = _context(menubar, _settings(publish_show_times=False), tmp_path / "settings.json")

    app_module._apply_menu_state(context, RideStatus.DRAFT)

    assert menubar.items[ids.MI_TIME_BOARD].enabled is False


def test_apply_menu_state_given_times_on_leaves_the_time_board_enabled(
    tmp_path: Path,
) -> None:
    """T-3: the gate's other arm leaves the item enabled."""
    menubar = _FakeMenuBar()
    context = _context(menubar, _settings(publish_show_times=True), tmp_path / "settings.json")

    app_module._apply_menu_state(context, RideStatus.DRAFT)

    assert menubar.items[ids.MI_TIME_BOARD].enabled is True


def test_apply_menu_state_given_no_menubar_is_a_silent_no_op(tmp_path: Path) -> None:
    """A route-level context with no menubar applies nothing."""
    context = _context(None, _settings(), tmp_path / "settings.json")

    app_module._apply_menu_state(context, RideStatus.DRAFT)

    assert context.frame.notices == []


# ------------------------------------- the startup / settings mirror


def test_apply_settings_live_given_new_publish_flags_checks_the_items(
    tmp_path: Path,
) -> None:
    """Settings OK mirrors the five items (the startup path's twin)."""
    menubar = _FakeMenuBar()
    context = _context(menubar, _settings(), tmp_path / "settings.json")

    app_module._apply_settings_live(context, _settings(publish_full_field=False))

    assert (
        menubar.items[ids.MI_FULL_FIELD].checked,
        menubar.items[ids.MI_ALL_CARDS].checked,
    ) == (False, True)
