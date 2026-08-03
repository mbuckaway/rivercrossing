# SPDX-License-Identifier: GPL-3.0-only
"""Application bootstrap: the ``rivercrossing`` GUI entry point.

Phase-1 built a ``wx.App()`` and returned -- no frame, no menubar, no
``MainLoop`` -- so the packaged bundle launched and exited in ~0.14s
with nothing on screen (E1.6.1's own report). This module assembles
every already-tested piece (XRC, ``MainFrame``, the §15 route table,
the accelerator table, ``DemoDataSource``) into a window that actually
stays up.

Two measured wx failure modes this module exists to avoid (AGENTS.md):

* An **unbound** ``wx.App()`` is garbage-collected the moment the
  function that built it returns, and the interpreter then hangs at
  exit with no application object left alive. :func:`main` keeps its
  ``app`` bound to a local name for its whole body, spanning the real
  ``MainLoop`` call.
* wx's default GUI log target *queues* errors rather than printing
  them; unless something shows or clears the queue,
  ``wxApp::CleanUp()`` tries to pop a "Several errors occurred" modal
  at interpreter exit with no user present to dismiss it, and hangs
  forever. ``tests/functional/conftest.py`` hits this from
  ``LoadFrame``/``LoadDialog`` failures and redirects the log target
  for the same reason this module does: to stderr, not disabled,
  since a failed XRC load still names the resource it could not find.

Only wx-free names (``ids``, ``commands``, ``accelerators``,
``rivercrossing.demo``, :func:`~rivercrossing.ui.require_wx`) are
imported at module scope, so this module itself stays importable even
when wx cannot be (mirrors the guard the original stub's own
docstring already promised). Every wx-touching name -- ``wx`` itself,
its ``xrc`` submodule, and the view classes -- is imported inside the
function that first needs it, each behind its own :func:`require_wx`
call.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rivercrossing.demo import DemoDataSource  # the one demo seam import (E1.2.4)
from rivercrossing.ui import accelerators, commands, ids, require_wx

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.ui.presenters.data_source import DataSource

__all__ = ["build_main_window", "main"]

# Riders > Entry Detail... has no plate to open with until a real ride
# exists (EPIC 4+); "77" is the only plate rivercrossing.demo carries
# entry_detail fixture data for, so it is what D1's menu route opens.
_ENTRY_DETAIL_DEMO_PLATE = "77"


@dataclass(frozen=True)
class _RouteContext:
    """The pieces every bound §15 route handler needs to act.

    Threading these three together keeps every route-handling helper
    below to at most one extra parameter, and keeps this module's one
    :class:`DemoDataSource` construction (E1.2.4) to the single call
    in :func:`build_main_window` -- every window a route later opens
    reuses that same instance rather than constructing its own.
    """

    frame: Any
    resource: Any
    data_source: DataSource


def _load_xrc_resources() -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Load every packaged ``.xrc`` file into the global resource.

    Mirrors ``tests/functional/harness.load_xrc_resources`` exactly,
    but is not imported from there: that module is test-only
    infrastructure, absent from a frozen bundle's own package path.
    ``wx.xrc.XmlResource.Get()`` is a process-wide singleton and
    ``Load`` is idempotent, so this is harmless to call more than
    once in a session (harness.py's own measured note).
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    xrc_dir = Path(__file__).resolve().parent / "xrc"
    resource = wx.xrc.XmlResource.Get()
    for path in sorted(xrc_dir.glob("*.xrc")):
        resource.Load(str(path))
    return resource


def _accelerator_entries(menubar: Any) -> list[Any]:  # noqa: ANN401 -- wx ships no stubs
    """Return each menu-bound row's own ``wx.AcceleratorEntry``.

    Harvests each row's own ``wx.MenuItem.GetAccel()`` from *menubar*
    rather than re-encoding the key spec here, so this can never
    drift from what ``main.xrc``'s own ``<accel>`` elements declare
    (accelerators.py's own concern about drift). Measured: a menu
    item's own ``GetAccel()`` carries the right key and modifiers but
    not the item's command id (it comes back as ``0``), so each
    entry is rebuilt with the real id explicit -- otherwise every
    harvested entry would collapse onto the same (wrong) command.

    Enter, ``accelerators.ACCELERATOR_TABLE``'s 4th row, is not a
    menu accelerator at all (that table's own docstring) and carries
    no ``menu_item_id`` to harvest, so it contributes no entry here:
    ``plate_input`` already carries ``wxTE_PROCESS_ENTER`` (measured,
    ``main.xrc``) for a future console handler, and a frame-level
    accelerator on bare Enter would risk shadowing it.
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    entries = []
    for accelerator in accelerators.ACCELERATOR_TABLE:
        if accelerator.menu_item_id is None:
            continue
        real_id = wx.xrc.XRCID(accelerator.menu_item_id)
        item, _menu = menubar.FindItem(real_id)
        xrc_accel = item.GetAccel()
        entries.append(wx.AcceleratorEntry(xrc_accel.GetFlags(), xrc_accel.GetKeyCode(), real_id))
    return entries


def _apply_accelerators(frame: Any, menubar: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Apply the frozen accelerator table to *frame* (E1.4.1)."""
    wx = require_wx()
    frame.SetAcceleratorTable(wx.AcceleratorTable(_accelerator_entries(menubar)))


def _decorate(context: _RouteContext, window: Any, route: commands.MenuRoute) -> None:  # noqa: ANN401
    """Bind *window*'s code-side view class, if *route.target* has one.

    Plain XRC dialogs with no code-side view class (D1's remaining
    forms -- ride setup, settings, audit trail and the correction
    dialogs) need nothing further here; they already carry their own
    canvas defaults from their own ``.xrc`` authoring.
    """
    from rivercrossing.ui.views.entry_detail import EntryDetailDialog  # noqa: PLC0415
    from rivercrossing.ui.views.results_win import ResultsWindow  # noqa: PLC0415
    from rivercrossing.ui.views.ride_library import RideLibrary  # noqa: PLC0415
    from rivercrossing.ui.views.rider_editor import RiderEditor  # noqa: PLC0415

    if route.target == ids.RIDE_LIBRARY_DLG:
        RideLibrary(window, data_source=context.data_source)
    elif route.target == ids.RIDER_EDITOR_DLG:
        RiderEditor(window, data_source=context.data_source)
    elif route.target == ids.ENTRY_DETAIL_DLG:
        EntryDetailDialog(window, _ENTRY_DETAIL_DEMO_PLATE, data_source=context.data_source)
    elif route.target == ids.RESULTS_FRAME:
        ResultsWindow(window, data_source=context.data_source)


def _open_target(context: _RouteContext, route: commands.MenuRoute) -> None:
    """Open *route*'s target window, or notice its absence (D1).

    ``LoadFrame``/``LoadDialog`` return ``None`` rather than raise
    when *route.target* names no XRC resource at all (harness.py's
    own measured note) -- true today only for the three §15 rows with
    no frozen window yet (Duplicate Ride, Reopen Ride, Void Card), and
    automatically true again for any future route whose target is not
    yet authored, with no change needed here: a route never silently
    does nothing, it always says so on the status bar instead.
    """
    is_frame = route.target == ids.RESULTS_FRAME
    window = (
        context.resource.LoadFrame(None, route.target)
        if is_frame
        else context.resource.LoadDialog(None, route.target)
    )
    if window is None:
        context.frame.SetStatusText(f"{route.label} — no window authored yet")
        return

    _decorate(context, window, route)
    if is_frame:
        window.Show()
        window.Raise()
        return

    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see module docstring

    try:
        dialogs.run_dialog(window, opener=context.frame)
    finally:
        if not window.IsBeingDeleted():
            window.Destroy()


def _make_route_handler(
    context: _RouteContext, route: commands.MenuRoute
) -> Callable[[Any], None]:
    """Return the ``EVT_MENU`` handler *route* fires.

    A ``COMMAND`` row has no window to open, and no ride engine yet
    exists to run its real action (EPIC 4+); it posts a status-bar
    notice instead of silently doing nothing. ``WINDOW``/``DIALOG``
    rows always attempt to open their target through
    :func:`_open_target`, which posts the same kind of notice if that
    target has no frozen window yet.
    """
    if route.kind is commands.TargetKind.COMMAND:
        return lambda _event: context.frame.SetStatusText(f"{route.label} — not yet implemented")
    return lambda _event: _open_target(context, route)


def _bind_routes(context: _RouteContext) -> None:
    """Bind every ``commands.ROUTE_TABLE`` id to a live handler.

    Iterates the table itself, never a hand-copied id list, so a
    route added later is bound automatically and cannot be missed
    (R-73).
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    for route in commands.ROUTE_TABLE:
        handler = _make_route_handler(context, route)
        for item_id in route.ids:
            context.frame.Bind(wx.EVT_MENU, handler, id=wx.xrc.XRCID(item_id))


def build_main_window(app: Any) -> Any:  # noqa: ANN401, ARG001 -- see conftest.xrc_resource's own pattern
    """Build and wire ``main_frame``, complete but not yet shown.

    Loads every packaged XRC resource, builds the console
    (:class:`~rivercrossing.ui.views.MainFrame`) and attaches its
    menubar via ``LoadMenuBar`` (never ``FindWindowByName`` -- the XRC
    menubar handler drops the name, spec.md §15b), applies the
    accelerator table, and binds every §15 route -- threading the one
    :class:`DemoDataSource` this function constructs through every
    window the bootstrap can reach. Deleting ``rivercrossing.demo``
    breaks exactly this module's import line and this construction
    line, and nothing else (E1.2.4's removable seam).

    Split out of :func:`main` so a test can drive the whole
    construction path without ever entering ``MainLoop``, which
    blocks.

    Args:
        app: The live ``wx.App`` :func:`main` already constructed;
            taken for ordering only -- an App must exist before any
            wx object is built -- and otherwise unused here.

    Returns:
        The loaded, fully wired ``main_frame``, not yet shown.
    """
    from rivercrossing.ui.views import MainFrame  # noqa: PLC0415 -- deferred, see module docstring

    resource = _load_xrc_resources()

    frame = resource.LoadFrame(None, ids.MAIN_FRAME)
    menubar = resource.LoadMenuBar(None, ids.MAIN_MENUBAR)
    frame.SetMenuBar(menubar)

    data_source = DemoDataSource()  # the one demo seam construction (E1.2.4)
    _console = MainFrame(frame, data_source=data_source)  # kept alive by its own event binding

    _apply_accelerators(frame, menubar)
    _bind_routes(_RouteContext(frame=frame, resource=resource, data_source=data_source))

    return frame


def main() -> int:
    """Run the RiverCrossing GUI application.

    Builds and shows ``main_frame`` with its menubar, accelerators and
    every §15 route bound (:func:`build_main_window`), then runs the
    event loop until the last top-level window closes.

    Returns:
        The process exit code; ``0`` on a clean shutdown.

    Raises:
        WxUnavailableError: If ``wx`` cannot be imported.
    """
    wx = require_wx()
    app = wx.App()  # bound for this whole call -- an unbound App is collected immediately
    wx.Log.SetActiveTarget(wx.LogStderr())  # see module docstring: the exit-time modal hang

    frame = build_main_window(app)
    frame.Show()
    app.MainLoop()

    return 0
