# SPDX-License-Identifier: GPL-3.0-only
"""Appearance modes: the Settings radios drive ``wx.App.SetAppearance``.

R-03: the Settings window's System/Light/Dark appearance radios
(``appearance_system_radio``, ``appearance_light_radio``,
``appearance_dark_radio``) apply the OS appearance at runtime via
``wx.App.SetAppearance`` and persist it through the settings file.
W13 (testing notes #14) removed the View > Theme radio trio -- the
Settings radios are the single theme surface, so this module no
longer maps menu-item ids to modes; :class:`ThemeController` applies
a :class:`ThemeMode` directly (:meth:`ThemeController.apply_mode`),
the call the settings OK path makes. Per A8, this module owns
appearance-mode logic only. module-skeletons.md:56 also plans a
light/dark token table for this module; that table stays deferred,
since it has no custom-drawn consumer yet (EPIC 1 open item O2).

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

Everything here reasons about a real ``wx.PyApp.AppearanceResult`` /
``Appearance`` / ``SystemAppearance`` only at the point wx is first
needed: this module's own import line touches no wx name at all,
mirroring ``app.py``'s convention. Everything past that point --
:func:`notice_for_result`, :func:`apply`,
:func:`apply_light_mode_panel_bg`, and :class:`ThemeController` --
calls :func:`~rivercrossing.ui.require_wx` at the point wx is first
needed, never at import time.
"""

from enum import Enum
from typing import Any

from rivercrossing.ui import require_wx

__all__ = [
    "ThemeController",
    "ThemeMode",
    "apply",
    "apply_light_mode_panel_bg",
    "notice_for_result",
]


class ThemeMode(Enum):
    """The three appearance choices (Settings radios), wx-free."""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


_NEXT_LAUNCH_NOTICE = "Theme change takes effect at next launch"

# ux-polish: the light-mode panel background for dialogs -- a subtle,
# single-tone neutral light grey, applied only in a Light appearance so
# the native white text-entry boxes stay visually distinct. Kept as a
# wx-free RGB tuple (this module never touches a wx name at import
# time; see the module docstring); the wx.Colour is built at call time
# in apply_light_mode_panel_bg.
_LIGHT_PANEL_BG: tuple[int, int, int] = (230, 230, 230)


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


def apply_light_mode_panel_bg(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Give *dialog* the light-mode panel background; no-op otherwise.

    ux-polish: in a Light appearance, dialogs get a subtle single-tone
    neutral grey (:data:`_LIGHT_PANEL_BG`) so the native white text
    entry boxes read as distinct fields on a slightly darker panel.
    Only the window background is touched -- every standard control
    keeps its own native colours (UX-DESKTOP §1), and measured against
    the actual XRC, no dialog in this codebase carries a direct-child
    content panel: dialogs.xrc/setup.xrc/riders.xrc/settings.xrc/
    teams.xrc/audit.xrc/results.xrc declare zero ``wxPanel`` objects,
    so each top sizer sits directly on its dialog/frame and that
    window background is the only surface needing the colour. A future
    dialog that wraps its content in a panel would extend this helper,
    not each call site.

    Light is detected as ``not wx.SystemSettings.GetAppearance().
    IsDark()`` -- measured, not guessed: at the pinned wxPython 4.3.1 /
    wxWidgets 3.3.3, ``GetAppearance()`` returns a
    ``wx.SystemAppearance`` exposing ``IsDark()``/``IsSystemDark()``/
    ``IsUsingDarkBackground()``/``GetName()``/``AreAppsDark`` and no
    ``IsLight()`` at all, and the functional theme scenarios already
    read live theme results with this identical probe
    (``GetAppearance().IsDark()``). ``ThemeController.mode`` was
    deliberately not used: it records the *selected* radio, while the
    *rendered* appearance is what decides whether native entry boxes
    are white -- System mode on a light OS must tint, and on MSW a
    Light selection the OS cannot apply at runtime (``CannotChange``)
    must not. ``IsDark()`` reports exactly that rendered state.

    Apply-at-open is sufficient for the modal dialogs that call this:
    a theme change cannot reach them while they are shown. A live
    re-apply across modeless windows when the theme changes mid-show
    is out of scope (no event plumbing is added for it); the results
    frame's own open path notes that trade-off at its call site.

    Args:
        dialog: The ``wx.Dialog`` (or results ``wx.Frame``) to tint.

    Returns:
        ``None`` -- no-op unless the active appearance is Light.
    """
    wx = require_wx()
    if wx.SystemSettings.GetAppearance().IsDark():
        return
    dialog.SetBackgroundColour(wx.Colour(*_LIGHT_PANEL_BG))


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
    """Owns the selected :class:`ThemeMode` and applies it (R-03).

    Constructed once per app bootstrap and kept alive by the binding
    its own :meth:`on_sys_colour_changed` becomes (mirrors ``app.py``'s
    own note about ``_console``/``_presenter``). Starts at
    ``ThemeMode.SYSTEM`` -- the default appearance, spec.md footnote
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

    def apply_mode(self, mode: ThemeMode) -> str | None:
        """Apply *mode* live; return an optional notice (W13).

        The single theme surface is the Settings appearance radios, so
        the settings OK path calls this with the collected mode --
        there is no View-menu radio any more (the W13 removal of the
        theme trio, notes #14).

        Args:
            mode: The appearance to switch to.

        Returns:
            :func:`notice_for_result`'s text, or ``None``.
        """
        self._mode = mode
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
