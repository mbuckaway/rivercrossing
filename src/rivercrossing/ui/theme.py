# SPDX-License-Identifier: GPL-3.0-only
"""Appearance modes: View > Theme wiring for ``wx.App.SetAppearance``.

R-03/P8-D4: the three View > Theme radios (``mi_theme_system``,
``mi_theme_light``, ``mi_theme_dark``) apply the OS appearance at
runtime via ``wx.App.SetAppearance`` -- not merely a Settings-dialog
mirror, which stays EPIC 8 along with persistence. Per A8, this
module owns appearance-mode logic only. module-skeletons.md:56
also plans a light/dark token table for this module; that table
stays deferred, since it has no custom-drawn consumer yet (EPIC 1
open item O2).

Per-OS truth, measured against the wxPython 4.3.1 / wxWidgets 3.3.3
pin (spec.md / xrc-windows.md footnote (6)):

* **macOS** applies a change at runtime, live, to every already-open
  window -- no restart, no capability check.
* ``Appearance.System`` does **not** durably follow the OS appearance
  on macOS: it pins the *current* ``NSAppearance`` at the moment of
  the call rather than resuming automatic tracking (measured).
  :class:`ThemeController` mitigates this by re-applying ``System``
  on every ``wx.EVT_SYS_COLOUR_CHANGED`` while that mode is still
  selected -- documented best-effort, since the underlying wx/OS
  behaviour is observed here, not specified anywhere upstream.
* **MSW** returns ``AppearanceResult.CannotChange`` once any
  top-level window already exists, so a Windows theme change only
  takes effect at the next launch; :func:`notice_for_result` is what
  turns that into the honest status-bar text.

:func:`mode_for_menu_id` is fully wx-free, so it stays importable
even when wx is broken -- this module's own import line touches no
wx name at all, mirroring ``app.py``'s convention. Everything past
that point -- :func:`notice_for_result`, :func:`apply`, and
:class:`ThemeController` -- reasons about a real
``wx.PyApp.AppearanceResult`` / ``Appearance`` and calls
:func:`~rivercrossing.ui.require_wx` at the point wx is first needed,
never at import time.
"""

from enum import Enum
from typing import Any

from rivercrossing.ui import ids, require_wx

__all__ = [
    "THEME_MENU_ITEM_IDS",
    "ThemeController",
    "ThemeMode",
    "UnknownThemeMenuItemError",
    "apply",
    "menu_item_id_for",
    "mode_for_menu_id",
    "notice_for_result",
]


class ThemeMode(Enum):
    """The three View > Theme radio choices, wx-free."""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class UnknownThemeMenuItemError(LookupError):
    """Raised when a menu item id maps to no :class:`ThemeMode`."""


# main.xrc's own declared order for the theme trio (also §15b's).
_MODE_BY_MENU_ID: dict[str, ThemeMode] = {
    ids.MI_THEME_SYSTEM: ThemeMode.SYSTEM,
    ids.MI_THEME_LIGHT: ThemeMode.LIGHT,
    ids.MI_THEME_DARK: ThemeMode.DARK,
}
# The reverse mapping, for the bootstrap's restored-appearance radio
# check (E8.1.1). ThemeMode is a closed enum and the dict above maps
# all three members, so a lookup can never miss.
_MENU_ID_BY_MODE: dict[ThemeMode, str] = {
    mode: item_id for item_id, mode in _MODE_BY_MENU_ID.items()
}
THEME_MENU_ITEM_IDS: tuple[str, ...] = tuple(_MODE_BY_MENU_ID)

_NEXT_LAUNCH_NOTICE = "Theme change takes effect at next launch"


def mode_for_menu_id(item_id: str) -> ThemeMode:
    """Return the :class:`ThemeMode` the theme radio *item_id* selects.

    Args:
        item_id: One of :data:`THEME_MENU_ITEM_IDS`.

    Returns:
        The matching :class:`ThemeMode`.

    Raises:
        UnknownThemeMenuItemError: If *item_id* names no theme radio.
    """
    try:
        return _MODE_BY_MENU_ID[item_id]
    except KeyError as exc:
        raise UnknownThemeMenuItemError(f"no theme mapping for menu item id {item_id!r}") from exc


def menu_item_id_for(mode: ThemeMode) -> str:
    """Return the theme radio's XRC name for *mode*.

    The reverse of :func:`mode_for_menu_id`: the bootstrap's restored-
    appearance radio check (E8.1.1) needs the id for a mode.

    Args:
        mode: The :class:`ThemeMode` to look up.

    Returns:
        The matching radio's XRC name (one of
        :data:`THEME_MENU_ITEM_IDS`).
    """
    return _MENU_ID_BY_MODE[mode]


def notice_for_result(result: Any) -> str | None:  # noqa: ANN401 -- wx ships no stubs
    """Return the status-bar text a ``SetAppearance`` result calls for.

    Args:
        result: The ``wx.PyApp.AppearanceResult`` :func:`apply` (or a
            direct ``SetAppearance`` call) returned. Never
            truth-tested -- ``AppearanceResult.Failure`` is ``0`` and
            would read as falsy, which is exactly the trap this
            compares around explicitly instead.

    Returns:
        The next-launch notice for ``CannotChange`` (MSW once a
        top-level window exists, spec.md footnote (6)); ``None`` for
        every other result -- a successful, silent runtime switch
        (``Ok``) needs no notice, and ``Failure`` has nothing more
        actionable to tell the operator than that.
    """
    wx = require_wx()
    if result == wx.PyApp.AppearanceResult.CannotChange:
        return _NEXT_LAUNCH_NOTICE
    return None


def apply(app: Any, mode: ThemeMode) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Apply *mode* to *app* via ``wx.App.SetAppearance`` (R-03).

    Args:
        app: The live ``wx.App``.
        mode: The mode to switch to.

    Returns:
        The raw ``wx.PyApp.AppearanceResult`` -- never truth-test it;
        compare it to a named member instead (see
        :func:`notice_for_result`). ``None`` when *app* has no
        ``SetAppearance`` or this wx build exposes no
        ``wx.PyApp.Appearance`` (the E8.1.2 capability guard): a build
        regressing the API away must fall back silently, never raise
        ``AttributeError``. There is deliberately no UI for the absent
        arm -- the guard only stops a regression from crashing.
    """
    wx = require_wx()
    set_appearance = getattr(app, "SetAppearance", None)
    if set_appearance is None or not hasattr(wx.PyApp, "Appearance"):
        return None
    appearance_by_mode = {
        ThemeMode.SYSTEM: wx.PyApp.Appearance.System,
        ThemeMode.LIGHT: wx.PyApp.Appearance.Light,
        ThemeMode.DARK: wx.PyApp.Appearance.Dark,
    }
    return set_appearance(appearance_by_mode[mode])


class ThemeController:
    """Owns the selected :class:`ThemeMode` and applies it (P8-D4).

    Constructed once per app bootstrap and kept alive by the binding
    its own :meth:`on_sys_colour_changed` becomes (mirrors ``app.py``'s
    own note about ``_console``/``_presenter``). Starts at
    ``ThemeMode.SYSTEM`` -- the checked menu default, spec.md footnote
    (8) -- without calling :func:`apply` at construction for System
    (the OS appearance is already System until something asks
    otherwise). A non-System mode passed in (E8.1.1's persisted
    appearance) IS applied at construction, so a relaunch opens in the
    saved appearance.
    """

    def __init__(self, app: Any, *, mode: ThemeMode = ThemeMode.SYSTEM) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Store the live *app* and apply *mode*.

        Args:
            app: The live ``wx.App`` this controller drives.
            mode: The appearance to start in; defaults to System.
        """
        self._app = app
        self._mode = mode
        self._reapplying = False
        if mode is not ThemeMode.SYSTEM:
            apply(app, mode)

    @property
    def mode(self) -> ThemeMode:
        """Return the currently selected mode."""
        return self._mode

    def on_menu(self, item_id: str) -> str | None:
        """Handle a View > Theme radio click; return an optional notice.

        Args:
            item_id: The radio's own XRC name (one of
                :data:`THEME_MENU_ITEM_IDS`).

        Returns:
            :func:`notice_for_result`'s text, or ``None``.

        Raises:
            UnknownThemeMenuItemError: If *item_id* names no theme
                radio -- propagated from :func:`mode_for_menu_id`.
        """
        self._mode = mode_for_menu_id(item_id)
        result = apply(self._app, self._mode)
        return notice_for_result(result)

    def on_sys_colour_changed(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Re-apply System on an OS appearance change, best-effort.

        macOS's ``Appearance::System`` pins the current appearance at
        the call rather than resuming automatic tracking (module
        docstring), so this re-applies it on every
        ``wx.EVT_SYS_COLOUR_CHANGED`` while System is still selected.
        Guarded by *self._reapplying*: :func:`apply` itself can
        trigger this very event synchronously (measured on this pin
        -- ``SetAppearance`` fires ``EVT_SYS_COLOUR_CHANGED`` on the
        frame before it returns), so without the guard a re-apply
        could recurse into itself. Always calls ``event.Skip()``: the
        default handler still needs to run so child controls redraw
        with the new system colours.
        """
        if self._mode is ThemeMode.SYSTEM and not self._reapplying:
            self._reapplying = True
            try:
                apply(self._app, ThemeMode.SYSTEM)
            finally:
                self._reapplying = False
        event.Skip()
