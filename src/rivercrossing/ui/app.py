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
``quit_flow``, ``rivercrossing.demo``,
:func:`~rivercrossing.ui.require_wx`) are imported at module scope, so
this module itself stays importable even when wx cannot be (mirrors
the guard the original stub's own docstring already promised). Every
wx-touching name -- ``wx`` itself, its ``xrc`` submodule, the view
classes, and the ``RiverCrossingApp`` subclass :func:`build_app`
builds -- is imported/defined inside the function that first needs
it, each behind its own :func:`require_wx` call.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rivercrossing.demo import DemoDataSource  # the one demo seam import (E1.2.4)
from rivercrossing.ui import accelerators, commands, ids, quit_flow, require_wx
from rivercrossing.ui.presenters.console import ConsolePresenter

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.ui.presenters.data_source import DataSource

__all__ = ["build_app", "build_main_window", "main"]

# Riders > Entry Detail... has no plate to open with until a real ride
# exists (EPIC 4+); "77" is the only plate rivercrossing.demo carries
# entry_detail fixture data for, so it is what D1's menu route opens.
_ENTRY_DETAIL_DEMO_PLATE = "77"


@dataclass(frozen=True)
class _RouteContext:
    """The pieces every bound §15 route handler needs to act.

    Threading these four together keeps every route-handling helper
    below to at most one extra parameter, and keeps this module's one
    :class:`DemoDataSource` construction (E1.2.4) to the single call
    in :func:`build_main_window` -- every window a route later opens
    reuses that same instance rather than constructing its own.

    Attributes:
        frame: ``main_frame``.
        resource: The loaded ``wx.xrc.XmlResource``.
        data_source: The demo/live display-data seam.
        app: The live ``wx.App`` -- carries ``really_quitting`` (the
            flag :func:`_on_query_end_session`/the exit route set so
            :func:`_on_main_frame_close` never re-opens a confirm
            dialog for a quit already confirmed, P8-D1's risk 1) and
            ``main_frame`` (for ``RiverCrossingApp.MacReopenApp``).
    """

    frame: Any
    resource: Any
    data_source: DataSource
    app: Any


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
    ``plate_input`` carries ``wxTE_PROCESS_ENTER`` (measured,
    ``main.xrc``) for :meth:`MainFrame.wire_entry`'s own console
    handler, and a frame-level accelerator on bare Enter would risk
    shadowing it.
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


def _confirm_quit(context: _RouteContext) -> quit_flow.QuitOutcome:
    """Run the quit-confirm dialog for the ride's current status.

    Loads :func:`quit_flow.dialog_for_status`'s target from
    *context*'s already-loaded resource, binds ``finish_first_btn``
    to ``EndModal`` first when the dialog carries one (A1 -- today it
    ends nothing), shows it through
    :func:`~rivercrossing.ui.views.dialogs.run_dialog` -- the one seam
    every dialog in this codebase shows through -- and posts the
    Finish-Ride stub notice on ``QuitOutcome.FINISH_FIRST`` before
    returning.
    """
    wx = require_wx()

    dialog_name = quit_flow.dialog_for_status(context.data_source.ride_status())
    dialog = context.resource.LoadDialog(None, dialog_name)

    finish_first_id: int | None = None
    finish_first_button = wx.Window.FindWindowByName(ids.FINISH_FIRST_BTN, dialog)
    if finish_first_button is not None:
        finish_first_id = finish_first_button.GetId()
        dialog.Bind(
            wx.EVT_BUTTON,
            lambda event: dialog.EndModal(event.GetId()),
            finish_first_button,
        )

    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see module docstring

    try:
        result = dialogs.run_dialog(dialog, opener=context.frame)
    finally:
        if not dialog.IsBeingDeleted():
            dialog.Destroy()

    outcome = quit_flow.outcome_for(result, ok_id=wx.ID_OK, finish_first_id=finish_first_id)
    if outcome is quit_flow.QuitOutcome.FINISH_FIRST:
        label = commands.route_for_id("mi_finish_ride").label
        context.frame.SetStatusText(f"{label} — not yet implemented")
    return outcome


def _handle_exit_route(context: _RouteContext) -> None:
    """File ▸ Exit / app-menu Quit / ⌘Q (all ``wxID_EXIT``): confirm.

    On a ``QUIT`` outcome, marks *context.app* as really quitting and
    force-closes -- ``Close(force=True)`` builds a non-vetoable
    ``EVT_CLOSE`` (P8-D1), so :func:`_on_main_frame_close` destroys
    the frame with no second dialog.
    """
    if _confirm_quit(context) is not quit_flow.QuitOutcome.QUIT:
        return
    context.app.really_quitting = True
    context.frame.Close(force=True)


def _on_main_frame_close(context: _RouteContext, event: Any) -> None:  # noqa: ANN401
    """Handle ``main_frame``'s own ``EVT_CLOSE``: the red X / close box.

    Checked first, together: a forced close (``not event.CanVeto()``,
    always true for :func:`_handle_exit_route`'s own
    ``Close(force=True)``) and *context.app.really_quitting* (set by
    a confirmed ``wxEVT_QUERY_END_SESSION``,
    :func:`_on_query_end_session`) both destroy with no dialog -- the
    second case is what keeps Dock ▸ Quit quittable (P8-D1's risk 1):
    its own default handler calls ``TopWindow->Close()`` next, and
    that call must not show a second confirm or hide instead of
    quitting.

    macOS never quits on the red X (P8-D2): it hides *context.frame*
    instead, leaving ``RiverCrossingApp.MacReopenApp`` a window to
    restore on a Dock-icon click. Windows has no equivalent hide
    convention, so it runs the same confirm flow the menu does.
    """
    wx = require_wx()
    if not event.CanVeto() or context.app.really_quitting:
        context.frame.Destroy()
        return
    if wx.Platform == "__WXMAC__":
        event.Veto()
        context.frame.Hide()
        return
    if _confirm_quit(context) is quit_flow.QuitOutcome.QUIT:
        context.app.really_quitting = True
        context.frame.Destroy()
    else:
        event.Veto()


def _on_query_end_session(context: _RouteContext, event: Any) -> None:  # noqa: ANN401
    """Handle Dock ▸ Quit / logout (``wxEVT_QUERY_END_SESSION``).

    Vetoing on anything but ``QUIT`` is what stops a cancelled Dock ▸
    Quit from tearing the app down anyway; ``event.Skip()`` on
    ``QUIT`` lets wx's own default handler proceed to
    ``TopWindow->Close()``, which :func:`_on_main_frame_close` then
    finds *really_quitting* already set and destroys with no second
    dialog (P8-D1's risk 1).
    """
    if _confirm_quit(context) is quit_flow.QuitOutcome.QUIT:
        context.app.really_quitting = True
        event.Skip()
        return
    event.Veto()


def _bind_process_quit_paths(context: _RouteContext) -> None:
    """Wire every way the process can end (P8-D1/P8-D2/P8-D8).

    Binds *context.frame*'s own ``EVT_CLOSE`` and *context.app*'s
    ``wxEVT_QUERY_END_SESSION``, and hands *context.app* the frame
    reference ``RiverCrossingApp.MacReopenApp`` restores later.
    """
    wx = require_wx()
    context.app.main_frame = context.frame
    context.frame.Bind(wx.EVT_CLOSE, lambda event: _on_main_frame_close(context, event))
    context.app.Bind(wx.EVT_QUERY_END_SESSION, lambda event: _on_query_end_session(context, event))


def _make_route_handler(
    context: _RouteContext, route: commands.MenuRoute
) -> Callable[[Any], None]:
    """Return the ``EVT_MENU`` handler *route* fires.

    ``route.target == "exit_or_quit"`` (the Exit row, P8-D8) always
    runs the quit-confirm flow instead of the generic ``COMMAND``
    stub below it. Every other ``COMMAND`` row has no window to open
    and no ride engine yet to run its real action (EPIC 4+); it posts
    a status-bar notice instead of silently doing nothing.
    ``WINDOW``/``DIALOG`` rows always attempt to open their target
    through :func:`_open_target`, which posts the same kind of notice
    if that target has no frozen window yet.
    """
    if route.target == "exit_or_quit":
        return lambda _event: _handle_exit_route(context)
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


def build_main_window(app: Any) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Build and wire ``main_frame``, complete but not yet shown.

    Loads every packaged XRC resource, builds the console
    (:class:`~rivercrossing.ui.views.MainFrame`) and attaches its
    menubar via ``LoadMenuBar`` (never ``FindWindowByName`` -- the XRC
    menubar handler drops the name, spec.md §15b), applies the
    accelerator table, binds every §15 route, and wires the two
    process-quit paths ``EVT_CLOSE``/``wxEVT_QUERY_END_SESSION``
    (Phase 8, P8-D1/P8-D2) -- threading the one
    :class:`DemoDataSource` this function constructs through every
    window the bootstrap can reach. Deleting ``rivercrossing.demo``
    breaks exactly this module's import line and this construction
    line, and nothing else (E1.2.4's removable seam).

    Split out of :func:`main` so a test can drive the whole
    construction path without ever entering ``MainLoop``, which
    blocks.

    Args:
        app: The live app :func:`main`/:func:`build_app` already
            constructed. An App must exist before any wx object is
            built; this function also hands it *frame*, for
            ``RiverCrossingApp.MacReopenApp`` to restore later, and
            binds its ``wxEVT_QUERY_END_SESSION``.

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

    # _presenter is kept alive the same way: wire_entry's closure holds
    # its bound on_plate_entered, which wx's own event table then holds.
    _presenter = ConsolePresenter(_console, data_source=data_source)
    _console.wire_entry(_presenter.on_plate_entered)
    _console.set_state(data_source.ride_status())
    _console.focus_entry()

    _apply_accelerators(frame, menubar)
    context = _RouteContext(frame=frame, resource=resource, data_source=data_source, app=app)
    _bind_routes(context)
    _bind_process_quit_paths(context)

    return frame


def _build_app_class() -> type[Any]:
    """Build the ``wx.App`` subclass Dock-reopen and quit need.

    A function, not a module-level ``class`` statement: the class
    body needs a live ``wx.App`` to subclass at all, and a bare
    ``class RiverCrossingApp(wx.App):`` at import time would break
    this module's "importable even when wx cannot be" guarantee
    (module docstring).
    """
    require_wx()
    import wx  # noqa: PLC0415 -- deferred, see module docstring

    class RiverCrossingApp(wx.App):  # type: ignore[misc]
        """The one live app object: owns Dock-reopen and the quit flag.

        ``# type: ignore[misc]``: wx ships no stubs (pyproject.toml's
        ``ignore_missing_imports`` for ``wx.*``), so ``wx.App``
        resolves to ``Any`` and mypy refuses to subclass ``Any`` --
        the same reasoning ``main_frame.CrossingsFeedModel`` already
        documents for the first wx base class this codebase
        subclasses.

        ``main_frame``/``really_quitting`` are set by
        :func:`build_main_window`/:func:`_handle_exit_route`/
        :func:`_on_main_frame_close`/:func:`_on_query_end_session`
        once they exist; both default here so every attribute access
        is safe even before then.
        """

        main_frame: Any = None
        really_quitting: bool = False

        def MacReopenApp(self) -> None:  # noqa: N802 -- wx's own override name
            """Show and raise the hidden main frame (P8-D2).

            wx's own default ``MacReopenApp`` only restores an
            *iconized* window (source-verified at the 4.3.1 pin) --
            this app hides ``main_frame`` on the red X instead of
            iconizing it (:func:`_on_main_frame_close`), so the
            default alone would leave a Dock-icon click doing
            nothing.
            """
            if self.main_frame is not None:
                self.main_frame.Show()
                self.main_frame.Raise()

    return RiverCrossingApp


def build_app() -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Construct the one live ``RiverCrossingApp`` instance."""
    return _build_app_class()()


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
    app = build_app()  # bound for this whole call -- an unbound App is collected immediately
    wx.Log.SetActiveTarget(wx.LogStderr())  # see module docstring: the exit-time modal hang

    frame = build_main_window(app)
    frame.Show()
    app.MainLoop()

    return 0
