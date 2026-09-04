# SPDX-License-Identifier: GPL-3.0-only
"""Live menu-enablement binder (E1.4.2's second half, E7.2.1).

``commands.py`` owns the §15 "Enabled when" rules as pure logic over
``commands.RideState`` -- headless-testable, and pinned by
``tests/unit/ui/test_commands.py``. This module is the missing half
that *applies* those rules to a real ``wx.MenuBar``: on every
ride-state (or ride-data) change, the app bootstrap computes one
``commands.RideState`` from the live engine and calls
:func:`apply_to_menubar`, which walks ``commands.ROUTE_TABLE`` and
``Enable()``s each menu item through its own ``wx.xrc.XRCID`` -- the
same table the unit suite proves, now holding in the app.

No module-scope ``wx`` import: the wx touch happens inside
:func:`apply_to_menubar`, and the ``xrcid`` seam lets a headless test
drive the identical code with a fake id resolver
(``tests/unit/ui/test_menu_state.py``).
"""

from typing import TYPE_CHECKING, Any

from rivercrossing.ui import commands, require_wx

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["apply_to_menubar", "enablement_table"]


def enablement_table(state: commands.RideState) -> dict[str, bool]:
    """Return ``{item_id: enabled}`` for every routed menu item.

    One verdict per ``commands.ROUTE_TABLE`` item id (49 ids), each
    exactly ``commands.is_route_enabled(route, state)`` -- the pure,
    headless-testable half of the binder.
    """
    return {
        item_id: commands.is_route_enabled(route, state)
        for route in commands.ROUTE_TABLE
        for item_id in route.ids
    }


def apply_to_menubar(
    menubar: Any,  # noqa: ANN401 -- wx ships no stubs; a wx.MenuBar
    state: commands.RideState,
    *,
    xrcid: Callable[[str], int] | None = None,
) -> None:
    """Enable or disable every routed menu item for *state*.

    Resolves each item id through ``wx.xrc.XRCID`` (or the injected
    ``xrcid`` seam in headless tests), looks the live ``wx.MenuItem``
    up via ``menubar.FindItem``, and ``Enable()``s it to
    :func:`enablement_table`'s verdict. A ``FindItem`` miss -- an id
    with no live menu item, e.g. a stock item wx relocated -- is a
    silent skip, never a crash.

    Args:
        menubar: The live ``wx.MenuBar`` attached to ``main_frame``.
        state: The current ``commands.RideState`` the rules read.
        xrcid: The name -> runtime-id resolver; defaults to
            ``wx.xrc.XRCID``.
    """
    if xrcid is None:
        require_wx()
        import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

        xrcid = wx.xrc.XRCID
    for item_id, enabled in enablement_table(state).items():
        item, _menu = menubar.FindItem(xrcid(item_id))
        if item is not None:
            item.Enable(enabled)
