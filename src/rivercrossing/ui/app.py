# SPDX-License-Identifier: GPL-3.0-only
"""Application bootstrap: the ``rivercrossing`` GUI entry point.

Phase-1 built a ``wx.App()`` and returned -- no frame, no menubar, no
``MainLoop`` -- so the packaged bundle launched and exited in ~0.14s
with nothing on screen (E1.6.1's own report). This module assembles
every already-tested piece (XRC, ``MainFrame``, the §15 route table,
the accelerator table) into a window that actually stays up. E5.4.2
retired the ``DemoDataSource`` wiring: no production module imports
``rivercrossing.demo`` (import-linter contract), the bootstrap's
windows read either a real store/engine-backed source or the
``EmptyDataSource`` empty state, and demo.py remains as test-only
fixture data.

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
``quit_flow``, ``theme``, ``rivercrossing.roster`` -- E3.2's seeded
rider roster, :func:`~rivercrossing.ui.require_wx`) are imported at
module scope, so this module itself stays importable even when wx
cannot be (mirrors the guard the original stub's own docstring already
promised). Every wx-touching name -- ``wx`` itself, its ``xrc``
submodule, the view classes, and the ``RiverCrossingApp`` subclass
:func:`build_app` builds -- is imported/defined inside the function
that first needs it, each behind its own :func:`require_wx` call.
E3.4's Import/Export Riders CSV… routes (``_handle_import_csv``/
``_handle_export_csv``) are two more such deferred names: both
delegate straight to ``rivercrossing.ui.views.rider_editor``'s own
shared flow functions, the one place that route and
``rider_editor_dlg``'s own import_btn/export_btn both run the picker
-> preview/write flow through (that module's own banner comment
explains why it is hosted there, not here).
"""

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rivercrossing.cards import Shoe
from rivercrossing.ride import RideConfig, RideEngine, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import accelerators, commands, ids, quit_flow, require_wx, resume_flow, theme
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EmptyDataSource, EngineDataSource, RideSummary

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.store import Store

__all__ = ["build_app", "build_main_window", "main"]

# E3.2's seeded roster default (R-20): every mixed ride this app opens
# is rider_pooled with room for teams up to this size. The bootstrap
# roster is EMPTY (no store-backed ride is open yet -- E5.4.2); the
# mode still reads mixed/pooled so a ride the library later opens keeps
# the same shape.
_SEEDED_MAX_TEAM_SIZE = 4

# The empty-state DataSource the windows E6/E7 have not wired to real
# data yet read (E5.4.2): with no store-backed ride open, entry detail,
# results and the no-store library render zero rows rather than demo
# ones. Stateless, so one shared instance serves every route.
_EMPTY_SOURCE = EmptyDataSource()

# Riders > Entry Detail... has no plate to open with until a real ride
# exists (EPIC 4+); with demo retired the dialog opens the empty state
# (``EmptyDataSource.entry_detail`` ignores the key and returns an
# empty view-model), so the lookup key itself no longer matters.
_ENTRY_DETAIL_DEFAULT_PLATE = ""

# The View row's own commands.py target (P8-D8): its 11 ids share one
# route, dispatched further by event id below -- the theme trio to
# theme.ThemeController, the other 8 to the generic COMMAND stub.
_VIEW_ROUTE_TARGET = "view_setting"


@dataclass
class _RouteContext:
    """The pieces every bound §15 route handler needs to act.

    Threading these together keeps every route-handling helper below to
    at most one extra parameter. E5.4.2 removed the ``data_source``
    seam field: no window a route opens reads the demo source any more
    (the empty-state windows read the module-level
    :data:`_EMPTY_SOURCE`, the live console reads its own
    ``EngineDataSource``, and the quit flow reads the live presenter's
    engine), so the context no longer needs to carry a display-data
    source at all.

    Not frozen (unlike a plain value record) because E5.4.1's library
    Open swaps the console in place: the route handlers bound in
    :func:`_bind_routes` close over this one object, so mutating
    :attr:`presenter`, :attr:`roster` and :attr:`active_ride_id` on it
    is what lets a later ``EVT_MENU`` see the opened ride.

    Attributes:
        frame: ``main_frame``.
        resource: The loaded ``wx.xrc.XmlResource``.
        roster: The in-memory :class:`~rivercrossing.roster.Roster`
            ``rider_editor_dlg`` reads and writes directly (E3.2) --
            unlike every other window here, it is not a
            ``data_source`` projection, so it is threaded separately.
            E5.4.1's library Open replaces it with the opened ride's
            store-reconstructed roster; at bootstrap it is empty (no
            store-backed ride is open yet, E5.4.2).
        app: The live ``wx.App`` -- carries ``really_quitting`` (the
            flag :func:`_on_query_end_session`/the exit route set so
            :func:`_on_main_frame_close` never re-opens a confirm
            dialog for a quit already confirmed, P8-D1's risk 1) and
            ``main_frame`` (for ``RiverCrossingApp.MacReopenApp``).
        theme_controller: The one live :class:`theme.ThemeController`
            the View row's theme ids apply modes through (P8-D4).
        store: The live :class:`~rivercrossing.store.Store`, when the
            app opened one (E5.2.1). ``None`` until a store-backed
            bootstrap (E5.4.1); a confirmed quit closes the open
            session through it (R-52's clean-quit signal).
        presenter: The live console's presenter, threaded so the
            Cards ▸ Undo Last Crossing route (and its Ctrl+Z
            accelerator) can fire ``presenter.on_undo``, the Finish
            flow can fire ``on_finish`` (E4.4.4), the quit flow can
            read the live ride status/name (:func:`_confirm_quit`,
            E5.4.2), and E5.4.1's Reopen route can fire ``on_reopen``.
            Optional with a stub fallback so route-level tests that
            construct ``_RouteContext`` without a live console keep
            working unchanged (test_app_open_target.py's
            ``_make_route_context``).
        console_view: The live :class:`~rivercrossing.ui.views.
            MainFrame` console, set by :func:`build_main_window`
            after construction; E5.4.1's library Open swaps its
            presenter through :meth:`MainFrame.set_presenter`.
        active_ride_id: The id of the store ride currently open in
            the console (E5.4.1). ``None`` until a store-backed ride
            is opened -- by the resume flow (E5.2.2's Continue) or the
            library's Open -- and what File ▸ Duplicate Ride… reads.
    """

    frame: Any
    resource: Any
    roster: Roster
    app: Any
    theme_controller: theme.ThemeController
    # E4.4.1: the live console's presenter, threaded so the Cards ▸
    # Undo Last Crossing route (and its Ctrl+Z accelerator) can fire
    # presenter.on_undo. Optional with a stub fallback so route-level
    # tests that construct _RouteContext without a live console keep
    # working unchanged (test_app_open_target.py's _make_route_context).
    presenter: ConsolePresenter | None = None
    # E5.2.1: the optional live Store the quit flow stamps closed_at on.
    store: Store | None = None
    # E5.4.1: the live console view (set by build_main_window after
    # MainFrame construction) and the currently open store ride's id.
    console_view: Any = None
    active_ride_id: int | None = None


def _build_console_engine(roster: Roster) -> tuple[RideEngine, EngineDataSource]:
    """Build the started ride the console runs at bootstrap (E4.4.1).

    With no store-backed ride open yet (E5.4.2), the console still
    opens on a real engine: a valid :class:`~rivercrossing.ride.
    RideConfig` matching *roster*'s own settings, an 8-deck seeded
    shoe, and the real wall clock. The engine is started so the
    console opens live (RUNNING), and a typed plate records on the
    very first Enter once the roster holds that entry. The bootstrap
    roster is empty until the library Open / resume flow loads a
    store ride, so the fresh console is the correct empty state: zero
    crossings, zero counters, full shoe, every plate refused as
    unknown (R-31).

    This is the one place a ride is created at bootstrap; E5's
    store-backed create/reopen flow replaces it (``_switch_console_
    to_ride``/``_resume_console_engine``).

    Returns:
        ``(engine, engine_source)`` -- the write side and the read-only
        source the console presenter and view are wired to.
    """
    config = RideConfig(
        name="GORBA EPIC 2026",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- pre-persistence local, RideConfig's own contract
        planned_duration_s=21600,
        min_lap_s=1080,
        entry_mode=roster.entry_mode,
        plate_model=roster.plate_model,
        max_team_size=roster.max_team_size,
    )
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    engine = RideEngine(
        config=config,
        shoe=shoe,
        clock=lambda: datetime.now(UTC),
        roster=roster,
    )
    engine.start()
    return engine, EngineDataSource(engine, roster)


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


def _check_default_menu_radios(menubar: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Tick the two documented radio defaults after ``LoadMenuBar``.

    ``<checked>`` is a silent no-op on ``wxITEM_RADIO`` (main.xrc's
    own comment, measured against ``src/xrc/xh_menu.cpp``) -- both
    documented defaults are ticked here in code instead (P8-D4).
    ``mi_theme_system`` already reads checked before this call in
    practice (it is the first item of its own radio group, and wx
    checks a fresh group's first member by default, measured); this
    still ticks it explicitly rather than relying on group order,
    which XRC authoring could change without this line noticing.
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    menubar.Check(wx.xrc.XRCID(ids.MI_THEME_SYSTEM), True)  # noqa: FBT003 -- wx API takes a positional bool
    menubar.Check(wx.xrc.XRCID(ids.MI_ZOOM_100), True)  # noqa: FBT003 -- wx API takes a positional bool


def _theme_item_id_for(real_id: int) -> str | None:
    """Return the theme radio's own XRC name for *real_id*, if any.

    The reverse of ``wx.xrc.XRCID``: an ``EVT_MENU`` only ever carries
    the resolved runtime int, never the name that produced it, so the
    three theme ids are walked back explicitly rather than kept in
    some other, larger lookup this row's other eight ids would also
    need to share.
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    return next(
        (item_id for item_id in theme.THEME_MENU_ITEM_IDS if wx.xrc.XRCID(item_id) == real_id),
        None,
    )


def _handle_view_row(context: _RouteContext, route: commands.MenuRoute, event: Any) -> None:  # noqa: ANN401
    """Dispatch the View row: theme ids to the controller, else stub.

    P8-D4. The other eight ids in this row (``mi_hide_times``, the seven
    ``mi_zoom_*``) keep the pre-Phase-8 generic ``COMMAND`` stub --
    they carry no engine yet either. A synthetic ``EVT_MENU`` never
    flips a radio's own checked state the way a genuine native click
    does (measured: this harness's functional suite has no delivery
    mechanism but direct event injection, harness.py's own module
    docstring), so this ticks the fired radio explicitly;
    ``wxMenuBar.Check`` also unchecks the theme trio's other two
    members (measured), matching what a real click's own native
    handling would already have done.
    """
    item_id = _theme_item_id_for(event.GetId())
    if item_id is None:
        context.frame.SetStatusText(f"{route.label} — not yet implemented")
        return
    context.frame.GetMenuBar().Check(event.GetId(), True)  # noqa: FBT003 -- wx API takes a positional bool
    notice = context.theme_controller.on_menu(item_id)
    if notice is not None:
        context.frame.SetStatusText(notice)


def _library_delete_callback(context: _RouteContext) -> Callable[[str], None] | None:
    """Return the library's store-backed delete callback, if any.

    E5.3.2's R-18 seam: a confirmed Delete on ``delete_ride_dlg``
    calls this with the ride's name, and the store deletes it (writing
    its backup first). With no store open there is nothing to delete,
    and the callback resolves no match and is a silent no-op -- the
    store module docstring's E5.3.2/E5.4.1 boundary resolution. The
    no-store library's rows are the E5.4.2 empty state (zero rows), so
    there is no ride name to match either way.
    """
    store = context.store
    if store is None:
        return None

    def _delete(ride_name: str) -> None:
        for ride in store.rides():
            if ride.name == ride_name:
                store.delete_ride(ride.id, ride_name)
                return

    return _delete


class _StoreLibrarySource:
    """DataSource-shaped ``rides()`` source over a real Store (E5.4.1).

    The ride library's live source: every ``rides()`` call re-queries
    the store, so a duplicate (or delete) followed by the view's own
    :meth:`~rivercrossing.ui.views.ride_library.RideLibrary.refresh`
    shows the change immediately. Rows carry the real ``ride_id`` so
    Open/Duplicate/Delete address the actual row. Only the library
    needs this seam; with no store open the library reads the
    E5.4.2 empty state (``_decorate``, zero rows) instead.
    """

    def __init__(self, store: Store) -> None:
        """Wrap *store* as the library's live row source."""
        self._store = store

    def rides(self) -> list[RideSummary]:
        """Return store rows as library ``RideSummary`` rows with ids.

        Each call re-queries the store, so the view's own refresh after
        a duplicate (or delete) shows the change immediately.
        """
        return [
            RideSummary(
                name=row.name,
                date=row.event_date.isoformat(),
                status=row.status,
                entries=row.entries,
                ride_id=row.id,
            )
            for row in self._store.rides()
        ]


def _switch_console_to_ride(
    context: _RouteContext, ride_id: int, clock: Callable[[], datetime] | None = None
) -> None:
    """Load *ride_id* from the store and swap the live console onto it.

    E5.4.1's library Open: the one place the console changes ride
    after bootstrap. Rebuilds the ride's roster and engine from the
    DB (:meth:`Store.roster_for`/:meth:`Store.load_engine`), builds a
    fresh ``EngineDataSource``, and swaps the presenter through
    :meth:`MainFrame.set_presenter` -- which rewires the plate entry,
    lifecycle controls and tick timer without rebinding (E5.2.2's
    resume wiring is the same store-load, applied at launch instead
    of mid-session). The route context's presenter/roster/
    ``active_ride_id`` are mutated in place because every bound route
    handler closes over this one object (``_RouteContext`` docstring).
    """
    store = context.store
    if store is None or context.console_view is None:
        return
    roster = store.roster_for(ride_id)
    engine = store.load_engine(ride_id, roster, clock=clock)
    source = EngineDataSource(engine, roster)
    presenter = ConsolePresenter(context.console_view, engine=engine, source=source)
    context.console_view.set_presenter(presenter)
    context.console_view.set_state(source.ride_status())
    context.console_view.show_feed(source.feed_rows())
    context.console_view.show_counters(source.counters())
    context.console_view.focus_entry()
    context.presenter = presenter
    context.roster = roster
    context.active_ride_id = ride_id


def _live_library_callbacks(
    context: _RouteContext,
    window: Any,  # noqa: ANN401 -- wx ships no stubs; a loaded wx.Dialog
    store: Store,
) -> tuple[Callable[[RideSummary], None], Callable[[], None], Callable[[RideSummary], None]]:
    """Return the store-backed library's Open/New/Duplicate callbacks.

    E5.4.1 wires the live library to the real DB through these three:

    - **Open** loads the selected ride and swaps the console onto it
      (:func:`_switch_console_to_ride`), then ends the library modal
      -- deferred through ``wx.CallAfter``, the same modal-chaining
      avoidance the resume flow's ``library_btn`` uses (measured
      there: a modal opened synchronously inside this one's unwind is
      not dismissible by the harness).
    - **New** ends the library modal and opens File ▸ New Ride…'s
      target (the ride setup flow), also deferred.
    - **Duplicate** shows the ride's name in the E5.4.1 mock-first
      confirm and, on OK, calls ``Store.duplicate_ride`` -- the view
      refreshes its own rows afterwards, so the new DRAFT ride
      appears immediately (R-15).

    ``window`` is the live ``ride_library_dlg``, used to end the
    modal for Open/New. ``store`` is the live Store the callbacks
    act on (:func:`_decorate` only calls this with one open).
    """
    wx = require_wx()

    def _open(selected: RideSummary) -> None:
        if selected.ride_id is None:
            return
        if not window.IsBeingDeleted():
            window.EndModal(wx.ID_CLOSE)
        wx.CallAfter(_switch_console_to_ride, context, selected.ride_id)

    def _new() -> None:
        if not window.IsBeingDeleted():
            window.EndModal(wx.ID_CLOSE)
        wx.CallAfter(_open_target, context, commands.route_for_id("mi_new_ride"))

    def _duplicate(selected: RideSummary) -> None:
        if selected.ride_id is None:
            return
        store.duplicate_ride(selected.ride_id)

    return _open, _new, _duplicate


def _decorate(context: _RouteContext, window: Any, route: commands.MenuRoute) -> None:  # noqa: ANN401
    """Bind *window*'s code-side view class, if *route.target* has one.

    Plain XRC dialogs with no code-side view class (D1's remaining
    forms -- settings, audit trail and the correction dialogs) need
    nothing further here; they already carry their own canvas
    defaults from their own ``.xrc`` authoring.
    """
    from rivercrossing.ui.views.entry_detail import EntryDetailDialog  # noqa: PLC0415
    from rivercrossing.ui.views.results_win import ResultsWindow  # noqa: PLC0415
    from rivercrossing.ui.views.ride_library import RideLibrary  # noqa: PLC0415
    from rivercrossing.ui.views.ride_setup import RideSetup  # noqa: PLC0415
    from rivercrossing.ui.views.rider_editor import RiderEditor  # noqa: PLC0415
    from rivercrossing.ui.views.selftest import SelfTestDialog  # noqa: PLC0415

    if route.target == ids.RIDE_LIBRARY_DLG:
        if context.store is not None:
            on_open, on_new, on_duplicate = _live_library_callbacks(context, window, context.store)
            RideLibrary(
                window,
                data_source=_StoreLibrarySource(context.store),
                on_delete=_library_delete_callback(context),
                on_open=on_open,
                on_new=on_new,
                on_duplicate=on_duplicate,
            )
        else:
            # No store open: the library is the E5.4.2 empty state --
            # zero rides until a store-backed bootstrap or New Ride
            # creates one; the Delete seam stays a no-op.
            RideLibrary(
                window,
                data_source=_EMPTY_SOURCE,
                on_delete=_library_delete_callback(context),
            )
    elif route.target == ids.RIDER_EDITOR_DLG:
        # E5.4.2: the roster is the store's when a store-backed ride is
        # open (E5.4.1's library Open replaced context.roster), and the
        # empty bootstrap roster otherwise -- the rider editor shows a
        # correct empty state until a real ride is opened.
        RiderEditor(window, roster=context.roster)
    elif route.target == ids.RIDE_SETUP_DLG:
        RideSetup(window, roster=context.roster)
    elif route.target == ids.ENTRY_DETAIL_DLG:
        # E5.4.2: no store-backed ride is selected, so entry detail is
        # the empty state (E7 wires the real per-entry lookup, R-38's
        # deep-link); the lookup key is irrelevant to the empty source.
        EntryDetailDialog(window, _ENTRY_DETAIL_DEFAULT_PLATE, data_source=_EMPTY_SOURCE)
    elif route.target == ids.RESULTS_FRAME:
        # E6.4.1 (D10): with a live console threaded, results render
        # the real placed rows from the console's EngineDataSource
        # (the same live source build_main_window wired, so the
        # roster always matches the engine -- the resume path never
        # updates context.roster) and seed the tie-break list from
        # the ride's stored order. The E5.4.2 empty state stays for
        # the no-presenter path (route-level tests).
        presenter = context.presenter
        if presenter is not None:
            ResultsWindow(
                window,
                data_source=presenter.source,
                tiebreak_order=presenter.engine.config.tiebreak_order,
            )
        else:
            ResultsWindow(window, data_source=_EMPTY_SOURCE)
    elif route.target == ids.SELFTEST_DLG:
        SelfTestDialog(window)


def _apply_dialog_defaults(window: Any, route: commands.MenuRoute) -> None:  # noqa: ANN401
    """Apply *route.target*'s recorded default-button/first-field.

    ``views.dialogs.DEFAULT_BUTTON_DECISIONS``/``FORM_FIRST_FIELDS``
    are the one place these E1.5.3/spec.md §13 per-dialog decisions
    are recorded; a no-op for any target with no entry (most dialogs
    already declare their own ``<default>`` in XRC and need no
    first-field override). This is what actually applies them when a
    real menu route opens the dialog -- the E1.5.3 gap this closes
    left ``dialogs.set_default_button``/``set_initial_focus`` proven
    only against a raw, directly-loaded XRC dialog, never through the
    app's own route path.
    """
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see module docstring

    default_button = dialogs.default_button_for(route.target)
    if default_button is not None:
        dialogs.set_default_button(window, default_button)
    first_field = dialogs.first_field_for(route.target)
    if first_field is not None:
        dialogs.set_initial_focus(window, first_field)


def _handle_import_csv(context: _RouteContext) -> None:
    """File ▸ Import Riders CSV…: run the shared flow (E3.4).

    The route's own target stays ``csv_preview_dlg`` (commands.py
    unchanged) -- :func:`~rivercrossing.ui.views.rider_editor.
    run_csv_import_flow` is the one place this route handler and
    ``rider_editor_dlg``'s own ``import_btn`` both run the picker ->
    preview -> commit flow through (that module's own banner comment
    explains why it is hosted there, not here).
    """
    from rivercrossing.ui.views import rider_editor  # noqa: PLC0415

    rider_editor.run_csv_import_flow(context.frame, context.roster)


def _handle_export_csv(context: _RouteContext) -> None:
    """File ▸ Export Riders CSV…: run the shared flow (E3.4).

    No window opens for this ``COMMAND`` row (spec.md §15's own
    "OS-native ... dialog, no app window") -- a cancelled picker is a
    silent no-op, the same shape :func:`_handle_import_csv` uses.
    """
    from rivercrossing.ui.views import rider_editor  # noqa: PLC0415

    path = rider_editor.run_csv_export_flow(context.frame, context.roster)
    if path is not None:
        context.frame.SetStatusText(f"Exported {path.name}")


def _handle_finish_route(context: _RouteContext) -> None:
    """Ride ▸ Finish Ride…: confirm, then run the finish flow (E4.4.4).

    Loads ``finish_confirm_dlg`` and shows it through
    :func:`~rivercrossing.ui.views.dialogs.run_dialog` -- the one seam
    every dialog in this codebase shows through -- and only a confirmed
    ``wx.ID_OK`` fires the live console presenter's ``on_finish``,
    which consults ``FINISH_GATE`` and calls ``engine.finish()`` (the
    E6.4.3 gate hook stays the stub until then). Mirrors the
    ``undo_last_crossing`` route's presenter-first shape: with no live
    presenter threaded (route-level tests), a notice stands in for the
    action after a confirmed dialog.
    """
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see app.py

    wx = require_wx()
    dialog = context.resource.LoadDialog(None, ids.FINISH_CONFIRM_DLG)
    # logic-coverage-exempt: T-3 -- the two defensive arms (a resource
    # without the dialog; a route context without a live presenter) are
    # unreachable in every live construction, mirroring the stop-confirm
    # guards in main_frame.py. The cancel arm below IS driven
    # functionally (test_mini_acceptance's finish-cancel case).
    if dialog is None:
        context.frame.SetStatusText("Finish Ride… — no finish dialog authored yet")
        return
    try:
        result = dialogs.run_dialog(dialog, opener=context.frame)
    finally:
        if not dialog.IsBeingDeleted():
            dialog.Destroy()
    if result != wx.ID_OK:
        return
    presenter = context.presenter
    if presenter is None:
        label = commands.route_for_id("mi_finish_ride").label
        context.frame.SetStatusText(f"{label} — not yet implemented")
        return
    presenter.on_finish()


def _open_ride_confirm(context: _RouteContext, dialog_name: str, message_lbl_text: str) -> bool:
    """Show one E5.4.1 confirm dialog; return whether it was confirmed.

    The shared shape of the two mock-first confirms
    (``duplicate_ride_dlg``, ``reopen_ride_dlg``): loads the dialog
    from the context resource, writes the ride-naming copy into
    ``message_lbl`` (a blank label is a failed assertion, never a
    cosmetic one -- UX-DESKTOP §4), shows it through
    :func:`~rivercrossing.ui.views.dialogs.run_dialog`, and reports
    whether ``wxID_OK`` (the marked default) was chosen. Both are
    non-destructive confirms, so Enter-ok is safe and there is no
    Cancel-focus wiring.

    Returns:
        ``True`` when the operator confirmed; ``False`` on Cancel or
        when the dialog resource is missing (which posts a notice).
    """
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see app.py

    wx = require_wx()
    dialog = context.resource.LoadDialog(None, dialog_name)
    if dialog is None:
        # logic-coverage-exempt: T-3 -- both dialogs are authored in
        # dialogs.xrc and loaded before any route opens them; a None
        # here means the resource is missing, which the functional
        # load-time verification already fails on.
        route = next(
            (row for row in commands.ROUTE_TABLE if row.target == dialog_name),
            None,
        )
        label = route.label if route is not None else dialog_name
        context.frame.SetStatusText(f"{label} — no dialog authored yet")
        return False
    try:
        message_lbl = wx.Window.FindWindowByName(ids.MESSAGE_LBL, dialog)
        if message_lbl is not None:
            message_lbl.SetLabel(message_lbl_text)
        result = dialogs.run_dialog(dialog, opener=context.frame)
    finally:
        if not dialog.IsBeingDeleted():
            dialog.Destroy()
    return bool(result == wx.ID_OK)


def _handle_duplicate_ride_route(context: _RouteContext) -> None:
    """File ▸ Duplicate Ride…: confirm, then duplicate the open ride.

    E5.4.1 replaces the E1.4.1 sentinel for this row: the route opens
    the mock-first ``duplicate_ride_dlg`` naming the ride currently
    open in the console, and on a confirmed Duplicate calls
    ``Store.duplicate_ride`` on the context's ``active_ride_id`` (the
    ride the library Open or the resume flow loaded). R-15: the copy
    is setup + roster, no timing data. Without a store-backed open
    ride there is nothing to duplicate, and the confirm's OK posts a
    notice instead of inventing a ride.
    """
    ride_id = context.active_ride_id
    if ride_id is None:
        context.frame.SetStatusText("Duplicate Ride… — no store ride is open")
        return
    store = context.store
    if store is None:
        context.frame.SetStatusText("Duplicate Ride… — no store is open")
        return
    ride_name = next(
        (ride.name for ride in store.rides() if ride.id == ride_id),
        "The ride",
    )
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see app.py

    confirmed = _open_ride_confirm(
        context,
        ids.DUPLICATE_RIDE_DLG,
        dialogs.duplicate_ride_message(ride_name),
    )
    if not confirmed:
        return
    copy_id = store.duplicate_ride(ride_id)
    copy_name = next(
        (ride.name for ride in store.rides() if ride.id == copy_id),
        "a new ride",
    )
    context.frame.SetStatusText(f"Duplicated as {copy_name}")


def _handle_reopen_ride_route(context: _RouteContext) -> None:
    """Ride ▸ Reopen Ride: confirm, then reopen the finished ride.

    E5.4.1 replaces the E1.4.1 sentinel for this row: the route opens
    the mock-first ``reopen_ride_dlg`` naming the ride (a FINISHED
    ride is the only one the row enables, commands.py), and on a
    confirmed Reopen fires the live console presenter's ``on_reopen``
    -- ``engine.reopen()`` moves the console to REOPENED, the
    corrections-only state (spec §3, R-36). With no live presenter
    (route-level tests) a notice stands in, mirroring the finish
    route's presenter-first shape.
    """
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see app.py

    presenter = context.presenter
    ride_name = (
        presenter.engine.config.name
        if presenter is not None
        else commands.route_for_id("mi_reopen_ride").label
    )
    confirmed = _open_ride_confirm(
        context,
        ids.REOPEN_RIDE_DLG,
        dialogs.reopen_ride_message(ride_name),
    )
    if not confirmed:
        return
    if presenter is None:
        label = commands.route_for_id("mi_reopen_ride").label
        context.frame.SetStatusText(f"{label} — not yet implemented")
        return
    presenter.on_reopen()


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

    try:
        _decorate(context, window, route)
        _apply_dialog_defaults(window, route)
    except Exception:
        # Fault A: any post-load failure must close the just-loaded
        # window before re-raising. _decorate's view construction runs
        # before the dialog path's own try/finally below, and a raise
        # there (find_control's 25-retry LookupError under load) used
        # to leak the dialog fully alive, rerun-masked, until the reap
        # pin caught it. A successfully shown frame stays open by
        # design, so this guard only ever runs on the exception path.
        if not window.IsBeingDeleted():
            window.Destroy()
        raise
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
    *context*'s already-loaded resource -- ``exit_running_dlg`` for a
    RUNNING ride, ``exit_confirm_dlg`` otherwise (R-51) -- writes the
    running variant's ride-naming copy into its ``message_lbl``
    (E5.2.3), binds ``finish_first_btn`` to ``EndModal`` (A1), shows
    it through :func:`~rivercrossing.ui.views.dialogs.run_dialog` --
    the one seam every dialog in this codebase shows through -- and
    maps the result.

    The live ride status and name come from the console's own
    presenter engine (E5.4.2: the ``data_source`` seam is gone; the
    quit flow asks the live console, never a display-data source).
    Route-level tests construct ``_RouteContext`` without a live
    presenter and never reach this path; the DRAFT/"The ride"
    fallbacks mirror the finish route's own presenter-less stub.

    A confirmed ``QuitOutcome.QUIT`` closes the live session through
    *context*.store (E5.2.1: stamp ``closed_at`` so the next launch
    reads a clean quit, not a crash); a ``QuitOutcome.FINISH_FIRST``
    hands off to the E4.4.4 finish flow -- :func:`_handle_finish_route`
    -- which shows ``finish_confirm_dlg`` and, on OK, runs the live
    console presenter's ``on_finish`` (E5.2.3 replaces the old stub
    notice).
    """
    wx = require_wx()

    presenter = context.presenter
    # logic-coverage-exempt: T-3 -- the DRAFT/"The ride" fallback arms
    # are unreachable in every live construction: _confirm_quit runs
    # only from post-bootstrap route handlers, which always have a
    # live presenter threaded (build_main_window's replace), mirroring
    # the finish route's own presenter-less stub exemption.
    status = presenter.engine.state if presenter is not None else RideStatus.DRAFT
    dialog_name = quit_flow.dialog_for_status(status)
    dialog = context.resource.LoadDialog(None, dialog_name)
    if dialog_name == ids.EXIT_RUNNING_DLG:
        ride_name = presenter.engine.config.name if presenter is not None else "The ride"
        message_lbl = wx.Window.FindWindowByName(ids.MESSAGE_LBL, dialog)
        if message_lbl is not None:
            message_lbl.SetLabel(quit_flow.running_exit_message(ride_name))

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
    if outcome is quit_flow.QuitOutcome.QUIT:
        store = context.store
        if store is not None:
            store.close_session()
    elif outcome is quit_flow.QuitOutcome.FINISH_FIRST:
        _handle_finish_route(context)
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
    convention, so it runs the same confirm flow the menu does; on a
    confirmed ``QUIT`` the destroy is deferred through
    ``wx.CallAfter`` -- a synchronous ``Destroy()`` here, inside
    ``EVT_CLOSE`` right after the confirm modal unwinds, deadlocks
    wxMSW (measured on windows-latest CI).
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
        # wxMSW deadlock (measured on windows-latest CI): Destroy()
        # called here -- synchronously inside EVT_CLOSE, right after
        # the confirm modal unwinds -- hangs the app with the GIL
        # held. Defer the destroy to the event loop, the same
        # wx.CallAfter idiom run_modal uses.
        wx.CallAfter(context.frame.Destroy)
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


def _bind_theme(context: _RouteContext) -> None:
    """Best-effort re-apply of System mode (P8-D4).

    Binds ``EVT_SYS_COLOUR_CHANGED`` on *context.frame*, not
    *context.app*: measured (a throwaway probe, per this repo's own
    convention), a ``wx.SysColourChangedEvent`` delivered through a
    frame's own event handler reaches only a frame-level ``Bind``,
    never an app-level one -- real OS appearance-change notifications
    target windows, and ``main_frame`` is the one window this app
    keeps alive for its whole run.
    """
    wx = require_wx()
    context.frame.Bind(wx.EVT_SYS_COLOUR_CHANGED, context.theme_controller.on_sys_colour_changed)


# E5.4.1's two mock-first confirm routes: Duplicate Ride… and Reopen
# Ride both open a confirm dialog then act on OK (like the finish
# route), so they dispatch through one table in _make_route_handler
# rather than two near-identical branches.
_RIDE_CONFIRM_HANDLERS: dict[str, Callable[[_RouteContext], None]] = {
    ids.DUPLICATE_RIDE_DLG: _handle_duplicate_ride_route,
    ids.REOPEN_RIDE_DLG: _handle_reopen_ride_route,
}


def _make_route_handler(  # noqa: PLR0911 -- one early-return per route special case; each is a real action
    context: _RouteContext, route: commands.MenuRoute
) -> Callable[[Any], None]:
    """Return the ``EVT_MENU`` handler *route* fires.

    ``route.target == "exit_or_quit"`` (the Exit row, P8-D8) always
    runs the quit-confirm flow instead of the generic ``COMMAND``
    stub below it. ``route.target == _VIEW_ROUTE_TARGET`` (the View
    row, P8-D4) dispatches further by *event*'s own id, inside
    :func:`_handle_view_row`, rather than by anything ``route`` alone
    carries -- its 11 ids all share this one row. ``export_riders_csv``
    (E3.4) is the one ``COMMAND`` row with a real action of its own,
    ahead of the generic stub. ``undo_last_crossing`` (E4.4.2) fires
    the live console presenter's ``on_undo`` (covering both the Cards
    ▸ Undo menu item and its Ctrl+Z accelerator); when no live
    presenter is threaded (route-level tests), it falls back to the
    generic stub. ``finish_confirm_dlg`` (E4.4.4) opens its confirm
    through :func:`_handle_finish_route`, which runs
    ``presenter.on_finish`` on a confirmed OK -- the same
    presenter-first shape ``undo_last_crossing`` uses -- instead of
    :func:`_open_target`'s generic open-and-return. ``csv_preview_dlg``
    (E3.4) is the one
    ``DIALOG`` target that needs a picker run before it opens, ahead
    of :func:`_open_target`'s generic path. Every other ``COMMAND``
    row has no window to open and no ride engine yet to run its real
    action (EPIC 4+); it posts a status-bar notice instead of
    silently doing nothing. Every other ``WINDOW``/``DIALOG`` row
    always attempts to open its target through :func:`_open_target`,
    which posts the same kind of notice if that target has no frozen
    window yet.
    """
    if route.target == "exit_or_quit":
        return lambda _event: _handle_exit_route(context)
    if route.target == _VIEW_ROUTE_TARGET:
        return lambda event: _handle_view_row(context, route, event)
    if route.target == "export_riders_csv":
        return lambda _event: _handle_export_csv(context)
    if route.target == "undo_last_crossing":
        presenter = context.presenter
        if presenter is not None:
            return lambda _event: presenter.on_undo()
        return lambda _event: context.frame.SetStatusText(f"{route.label} — not yet implemented")
    if route.target == ids.FINISH_CONFIRM_DLG:
        return lambda _event: _handle_finish_route(context)
    # E5.4.1's two mock-first confirms both need a real handler ahead
    # of _open_target (their confirm -> action shape, like the finish
    # route), so they share one dispatch table instead of two branches.
    ride_confirm_handler = _RIDE_CONFIRM_HANDLERS.get(route.target)
    if ride_confirm_handler is not None:
        return lambda _event: ride_confirm_handler(context)
    if route.target == ids.CSV_PREVIEW_DLG:
        return lambda _event: _handle_import_csv(context)
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


def _run_launch_self_test(context: _RouteContext) -> None:
    """Run the R-44 evaluator self-test at launch (spec section 12).

    ``SelfTestDialog`` already runs the real suite once as part of
    its own construction (its presenter's ``__init__``), so this
    reuses that one run rather than calling ``self_test()`` again
    separately: a green report never shows the dialog at all -- the
    launch hook stays silent -- and only a red one pops the modal a
    scorer must dismiss before continuing. The BLOCKING half of R-44
    ("failure blocks finishing a ride") is EPIC 6's; this only makes
    the hook itself exist and run (E2.4.1's own scope note).
    """
    from rivercrossing.ui.views.selftest import SelfTestDialog  # noqa: PLC0415

    window = context.resource.LoadDialog(None, ids.SELFTEST_DLG)
    try:
        view = SelfTestDialog(window)
    except Exception:
        # Fault A: construction runs before the run_dialog try/finally
        # below; a raise here must not leave the loaded dialog alive.
        if not window.IsBeingDeleted():
            window.Destroy()
        raise
    if view.presenter.report.passed:
        window.Destroy()
        return

    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see module docstring

    try:
        dialogs.run_dialog(window, opener=context.frame)
    finally:
        if not window.IsBeingDeleted():
            window.Destroy()


def _resume_console_engine(
    context: _RouteContext, clock: Callable[[], datetime] | None
) -> tuple[RideEngine, EngineDataSource] | None:
    """Show ``resume_dlg`` when the previous session warrants it.

    R-52: on launch with a running ride, ``resume_dlg`` always
    appears. Reads the store's previous-session record; when the
    resume decision (:func:`~rivercrossing.ui.resume_flow.
    resume_dialog_for`) says a ride was running at the previous exit,
    loads ``resume_dlg``, writes the quit-vs-crash copy
    (:func:`~rivercrossing.ui.resume_flow.resume_message`) into its
    ``message_lbl`` -- a blank label is a failed assertion, never a
    cosmetic one -- binds ``continue_btn``/``library_btn`` to end the
    modal (spec §15b's code-side ``SetAffirmativeId`` contract, and
    E1.5.3's Escape->library decision), and maps the outcome:

    - **Continue** marks the open session's running ride
      (:meth:`~rivercrossing.store.Store.set_active_ride`), rebuilds
      the engine from the store (:meth:`~rivercrossing.store.
      Store.load_engine` with the ride's roster shell), and hands the
      console that engine/source -- elapsed derives from the engine's
      replayed ``actual_start`` and the wall clock (R-30).
    - **Open library** opens ``ride_library_dlg`` (the store-backed
      library, E5.4.1) and falls through to the bootstrap console.
    - **No store, or nothing to resume** returns ``None`` -- the
      bootstrap console path stays (E5.4.2: an empty real engine).

    Returns:
        ``(engine, source)`` for the console when the ride was
        resumed; ``None`` when no store-backed resume happened.
    """
    store = context.store
    if store is None:
        return None
    previous = store.previous_session()
    if resume_flow.resume_dialog_for(previous) is None:
        return None
    ride_id = previous.ride_id
    ended_at = previous.ended_at
    if ride_id is None or ended_at is None:
        # logic-coverage-exempt: T-3 -- resume_flow.resume_dialog_for
        # only warrants a dialog for a session that carried a running
        # ride, and such a session always has an end instant (closed_at
        # for a quit, heartbeat/opened_at for a crash); this guard
        # only narrows types for mypy.
        raise RuntimeError("resume dialog warranted without a ride or end time")

    wx = require_wx()
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see module docstring

    dialog = context.resource.LoadDialog(None, ids.RESUME_DLG)
    if dialog is None:
        context.frame.SetStatusText("Resume Ride — no resume dialog authored yet")
        return None
    try:
        ride_name = next(
            (ride.name for ride in store.rides() if ride.id == ride_id),
            "The ride",  # FK-guaranteed present; same fallback _confirm_quit uses
        )
        message_lbl = wx.Window.FindWindowByName(ids.MESSAGE_LBL, dialog)
        if message_lbl is not None:
            message_lbl.SetLabel(resume_flow.resume_message(ride_name, previous.state, ended_at))

        continue_id: int | None = None
        continue_btn = wx.Window.FindWindowByName(ids.CONTINUE_BTN, dialog)
        if continue_btn is not None:
            continue_id = continue_btn.GetId()
            # dialogs.xrc's own documented contract (spec §15b): the
            # custom-named continue_btn keeps its name, and the
            # affirmative behavior is wired in code with
            # SetAffirmativeId so Enter returns its own id.
            dialog.SetAffirmativeId(continue_id)
            dialog.Bind(
                wx.EVT_BUTTON,
                lambda event: dialog.EndModal(event.GetId()),
                continue_btn,
            )
        library_btn = wx.Window.FindWindowByName(ids.LIBRARY_BTN, dialog)
        if library_btn is not None:
            # E1.5.3's product decision: resume_dlg's Escape routes to
            # library_btn (the non-committal path; nothing to cancel on
            # launch). wire_escape_to also binds the click-to-EndModal,
            # so this one call covers both.
            dialogs.wire_escape_to(dialog, ids.LIBRARY_BTN)

        result = dialogs.run_dialog(dialog, opener=context.frame)

        if continue_id is not None and result == continue_id:
            store.set_active_ride(ride_id)
            # E5.4.1: record the continued ride on the shared context
            # so File ▸ Duplicate Ride… knows what is open (the route
            # handlers close over this same object).
            context.active_ride_id = ride_id
            roster = store.roster_for(ride_id)
            engine = store.load_engine(ride_id, roster, clock=clock)
            return engine, EngineDataSource(engine, roster)
    finally:
        if not dialog.IsBeingDeleted():
            dialog.Destroy()

    # Open library instead of resuming (also Escape's target). Deferred
    # through wx.CallAfter rather than opened here: a modal chained
    # synchronously inside this bootstrap call -- while the resume
    # modal's own unwind is still on the stack -- is not dismissible by
    # the functional harness's CallAfter pattern (measured; the child
    # hit its bound in a hung ride_library_dlg). The CallAfter fires on
    # the running event loop (main()'s MainLoop), right at startup.
    wx.CallAfter(lambda: _open_target(context, commands.route_for_id("mi_open_library")))
    return None


def build_main_window(
    app: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    store: Store | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Build and wire ``main_frame``, complete but not yet shown.

    Loads every packaged XRC resource, builds the console
    (:class:`~rivercrossing.ui.views.MainFrame`) and attaches its
    menubar via ``LoadMenuBar`` (never ``FindWindowByName`` -- the XRC
    menubar handler drops the name, spec.md §15b), ticks the two
    documented radio defaults, applies the accelerator table, binds
    every §15 route and the theme controller's own
    ``EVT_SYS_COLOUR_CHANGED`` re-apply, and wires the two
    process-quit paths ``EVT_CLOSE``/``wxEVT_QUERY_END_SESSION``
    (Phase 8, P8-D1/P8-D2/P8-D4). E5.4.2 retired the
    :class:`DemoDataSource` construction: the bootstrap roster is
    empty (no store-backed ride is open), the console reads its own
    live ``EngineDataSource``, and the E6/E7 windows read the
    :data:`_EMPTY_SOURCE` empty state -- no production module imports
    ``rivercrossing.demo`` any more (import-linter contract).

    Split out of :func:`main` so a test can drive the whole
    construction path without ever entering ``MainLoop``, which
    blocks.

    Args:
        app: The live app :func:`main`/:func:`build_app` already
            constructed. An App must exist before any wx object is
            built; this function also hands it *frame*, for
            ``RiverCrossingApp.MacReopenApp`` to restore later, and
            binds its ``wxEVT_QUERY_END_SESSION``.
        store: The live :class:`~rivercrossing.store.Store`, when the
            caller opened one (E5.2.1). Threaded through
            :class:`_RouteContext` so a confirmed quit stamps the
            open session's ``closed_at`` (:func:`_confirm_quit`), and
            read by :func:`_resume_console_engine` so a running ride
            at the previous exit opens ``resume_dlg`` (E5.2.2, R-52);
            ``None`` until the store-backed bootstrap (E5.4.1).
        clock: Wall-clock source for a store-loaded resume engine
            (:meth:`~rivercrossing.store.Store.load_engine`), injected
            by the functional suite to pin the elapsed reading at a
            fixed instant; ``None`` uses the engine's own default
            (``datetime.now``).

    Returns:
        The loaded, fully wired ``main_frame``, not yet shown.
    """
    from rivercrossing.ui.views import MainFrame  # noqa: PLC0415 -- deferred, see module docstring

    resource = _load_xrc_resources()

    frame = resource.LoadFrame(None, ids.MAIN_FRAME)
    menubar = resource.LoadMenuBar(None, ids.MAIN_MENUBAR)
    frame.SetMenuBar(menubar)
    _check_default_menu_radios(menubar)

    # E5.4.2: no store-backed ride is open at bootstrap, so the roster
    # is empty (rider_editor_dlg shows the empty state; the library
    # Open / resume flow replaces it with the store's roster). The
    # mixed/pooled mode keeps the E3.2 default shape.
    roster = Roster(
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        max_team_size=_SEEDED_MAX_TEAM_SIZE,
    )
    theme_controller = theme.ThemeController(app)
    context = _RouteContext(
        frame=frame,
        resource=resource,
        roster=roster,
        app=app,
        theme_controller=theme_controller,
        store=store,
        # presenter is threaded below with dataclasses.replace, once the
        # live console exists; the resume flow and route binding only
        # need the pieces already set here.
    )

    # E5.2.2: a store-backed launch with a running ride at the
    # previous exit shows resume_dlg (R-52) and hands the console the
    # store-loaded engine; anything else keeps the bootstrap console
    # path (an empty real engine, E5.4.2).
    resumed = _resume_console_engine(context, clock)
    if resumed is None:
        engine, engine_source = _build_console_engine(roster)
    else:
        engine, engine_source = resumed

    _console = MainFrame(frame, data_source=engine_source, resource=resource)

    # _presenter is kept alive the same way: wire_entry/wire_console's
    # closures hold its bound handlers, which wx's own event table and
    # the tick timer then hold.
    _presenter = ConsolePresenter(_console, engine=engine, source=engine_source)
    _console.wire_entry(_presenter.on_plate_entered)
    _console.wire_console(_presenter)
    _console.set_state(engine_source.ride_status())
    _console.focus_entry()

    _apply_accelerators(frame, menubar)
    # theme_controller is kept alive by _RouteContext, threaded through
    # every route handler. console_view is threaded the same way so
    # E5.4.1's library Open can swap the console's presenter;
    # active_ride_id records the store ride the resume flow continued,
    # if any (File ▸ Duplicate Ride… reads it).
    context = replace(
        context,
        presenter=_presenter,
        console_view=_console,
    )
    _bind_routes(context)
    _bind_process_quit_paths(context)
    _bind_theme(context)
    _run_launch_self_test(context)

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
