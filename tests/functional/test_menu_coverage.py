# SPDX-License-Identifier: GPL-3.0-only
"""Functional coverage walk over every §15 route (E1.4.1, R-73).

R-73: "the menu-coverage test walks all §15 routes in all ride
states." Only the parts of the walk that genuinely need a real,
loaded ``wx.MenuBar`` live here -- the route table's own shape
(row/menu counts, kind/target transcription, ``route_for_id``'s
dispatch and its negative path) needs no display at all and lives in
``tests/unit/ui/test_commands.py`` instead (mirroring the split
``cards_imagelist`` already uses).

What only this module can prove:

1. A real ``wx.CommandEvent(wx.EVT_MENU, ...)`` posted at a real,
   loaded menubar's real id actually reaches ``commands.ROUTE_TABLE``'s
   matching row -- what R-73 means by "reachable and drivable".
   ``main.xrc``'s own declared shape (45 ``mi_*`` names + 3 stock ids,
   per-menu item counts, accelerators) is already locked down by
   ``test_xrc_structure.py``; this module drives real events at it.
2. What wx actually does with the macOS stock-item relocation in this
   harness session (measured, not assumed).
3. That the accelerator table agrees with ``main.xrc``'s live
   ``<accel>`` declarations, via a real ``wx.MenuItem.GetAccel()``.

``wx.xrc.XRCID(name)`` is measured (a throwaway probe, per
harness.py's own convention) to return the *same* runtime int id a
loaded resource assigned to *name*, for both ``mi_*`` names and the
``wxID_*`` stock names -- but only once that name has been loaded at
least once in this process. The session-scoped ``xrc_resource``
fixture guarantees that before any test here runs.
"""

import harness
import pytest
import wx
import wx.xrc

from rivercrossing.ui import accelerators, commands, ids

pytestmark = pytest.mark.functional

COVERAGE_CASES = tuple((route, item_id) for route in commands.ROUTE_TABLE for item_id in route.ids)
COVERAGE_CASE_IDS = [f"{route.menu}:{item_id}" for route, item_id in COVERAGE_CASES]

STOCK_RELOCATION_CASES = (
    (wx.ID_ABOUT, "&Help"),
    (wx.ID_PREFERENCES, "&File"),
    (wx.ID_EXIT, "&File"),
)

XRC_ACCELERATOR_CASES = tuple(
    (accelerator.menu_item_id, accelerator.key)
    for accelerator in accelerators.ACCELERATOR_TABLE
    if accelerator.menu_item_id is not None
)


@pytest.fixture
def frame_with_menubar(xrc_resource: object):
    """Load main_frame with its real menubar attached, then close it."""
    frame = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    menubar = harness.load_menubar(xrc_resource, ids.MAIN_MENUBAR)
    frame.SetMenuBar(menubar)
    harness.pump()
    try:
        yield frame, menubar
    finally:
        harness.close_window(frame)


def _real_id(name: str) -> int:
    """Return the runtime wx id XRC assigned to *name*.

    Measured idempotent once *name* has been loaded (see the module
    docstring): a safe way back from a frozen XRC/stock name to the
    int id a real ``wx.MenuItem`` carries.
    """
    return int(wx.xrc.XRCID(name))


@pytest.mark.parametrize(("route", "item_id"), COVERAGE_CASES, ids=COVERAGE_CASE_IDS)
def test_menu_route_is_reachable_and_resolves_its_declared_kind(
    frame_with_menubar: object, route: commands.MenuRoute, item_id: str
) -> None:
    """Every §15 route's real menu id delivers EVT_MENU and resolves."""
    frame, _menubar = frame_with_menubar
    real_id = _real_id(item_id)
    delivered_ids: list[int] = []
    frame.Bind(wx.EVT_MENU, lambda evt: delivered_ids.append(evt.GetId()), id=real_id)
    event = wx.CommandEvent(wx.EVT_MENU.typeId, real_id)
    event.SetEventObject(frame)

    frame.GetEventHandler().ProcessEvent(event)
    harness.pump()

    resolved = commands.route_for_id(item_id)
    assert delivered_ids == [real_id]
    assert resolved is route


@pytest.mark.parametrize(("stock_id", "authored_menu_title"), STOCK_RELOCATION_CASES)
def test_stock_menu_item_stays_in_its_authored_menu_in_this_harness(
    frame_with_menubar: object, stock_id: int, authored_menu_title: str
) -> None:
    """Measured: no macOS app-menu relocation in this pytest session.

    wx documents that About / Preferences / Exit move into the native
    application menu on macOS, but that needs a foregrounded
    NSApplication. This harness's ``wx.App`` is never the active,
    focused app (harness.py's own module docstring measures the same
    fact about ``wx.UIActionSimulator``), so at the ``wx.MenuBar``
    object level -- the only level a headless functional test can
    observe -- ``wxID_ABOUT``/``wxID_PREFERENCES``/``wxID_EXIT`` are
    found still attached to the menu ``main.xrc`` declared them in,
    not moved to a distinct application menu (measured on wxPython
    4.3.1 / wxWidgets 3.3.3, macOS-cocoa). This reports what wx
    actually does here, not the documented behaviour of a real,
    foregrounded app bundle, which this suite cannot observe.
    """
    _frame, menubar = frame_with_menubar

    item, containing_menu = menubar.FindItem(stock_id)

    assert item.GetId() == stock_id
    assert containing_menu.GetTitle() == authored_menu_title


@pytest.mark.parametrize(("menu_item_id", "key"), XRC_ACCELERATOR_CASES)
def test_accelerator_table_agrees_with_the_xrc_declared_accel(
    frame_with_menubar: object, menu_item_id: str, key: str
) -> None:
    """Ctrl+Z, F5, F1: the table agrees with main.xrc's <accel>."""
    _frame, menubar = frame_with_menubar
    real_id = _real_id(menu_item_id)

    item, _menu = menubar.FindItem(real_id)

    assert item.GetAccel().ToString() == key
