# SPDX-License-Identifier: GPL-3.0-only
"""Application bootstrap: the ``rivercrossing`` GUI entry point.

Phase-1 built a ``wx.App()`` and returned -- no frame, no menubar, no
``MainLoop`` -- so the packaged bundle launched and exited in ~0.14s
with nothing on screen (E1.6.1's own report). This module assembles
every already-tested piece (XRC, ``MainFrame``, the §15 route table,
the accelerator table) into a window that actually stays up. E5.4.2
retired the demo seam: the bootstrap's windows read either a real
store/engine-backed source or the ``EmptyDataSource`` empty state.

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
rider roster, ``rivercrossing.store`` -- E9.1.1's Store and its
:func:`~rivercrossing.store.default_db_path`, plus
:func:`~rivercrossing.ui.require_wx`) are imported at module scope,
so this module itself stays importable even when wx cannot be
(mirrors the guard the original stub's own docstring already
promised). Every wx-touching name -- ``wx`` itself, its ``xrc``
submodule, the view classes, and the ``RiverCrossingApp`` subclass
:func:`build_app` builds -- is imported/defined inside the function
that first needs it, each behind its own :func:`require_wx` call.
E3.4's Import/Export Riders CSV… routes (``_handle_import_csv``/
``_handle_export_csv``) are two more such deferred names: both
delegate straight to ``rivercrossing.ui.views.rider_editor``'s own
shared flow functions -- W7 removed ``rider_editor_dlg``'s own
import_btn/export_btn, so the File-menu routes are now their only
callers (that module's own banner comment explains why it is hosted
there, not here).
"""

import gc
import os
import platform
import re
import sqlite3
import sys
import threading
import webbrowser
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from platformdirs import user_data_dir

from rivercrossing import __version__, csvio, htmlexport, pdfexport
from rivercrossing.cards import Card, Shoe, ShoeClosedError
from rivercrossing.htmlexport import ExportOptions
from rivercrossing.ride import (
    IllegalStateError,
    PendingMiss,
    RideConfig,
    RideEngine,
    RideEngineError,
    RideStatus,
    UnknownPlateError,
)
from rivercrossing.roster import EntryMode, EntryType, PlateModel, Roster
from rivercrossing.standings import Placed, rank_by_kind, tiebreak_order_from_spellings
from rivercrossing.store import (
    PreviousSession,
    RideNameMismatchError,
    RideNotFoundError,
    RideRunningError,
    SchemaVersionMismatchError,
    Store,
    StoreError,
    default_db_path,
)
from rivercrossing.ui import (
    accelerators,
    commands,
    ids,
    quit_flow,
    require_wx,
    resume_flow,
    sound,
    theme,
    zoom,
)
from rivercrossing.ui import (
    help as help_module,
)
from rivercrossing.ui.logging import Logging, build_log_path, prune_logs
from rivercrossing.ui.presenters import settings as settings_store
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import (
    FEED_CAP,
    DataSource,
    EmptyDataSource,
    EngineDataSource,
    RideSummary,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

    from rivercrossing.ride import Crossing, Event
    from rivercrossing.ui.presenters.settings import AppSettings

__all__ = ["build_app", "build_main_window", "main"]

# E3.2's seeded roster default (R-20): every mixed ride this app opens
# is rider_pooled with room for teams up to this size. The bootstrap
# roster is EMPTY (no store-backed ride is open yet -- E5.4.2); the
# mode still reads mixed/pooled so a ride the library later opens keeps
# the same shape.
_SEEDED_MAX_TEAM_SIZE = 4

# W8's fixed team-logo seed for that same bootstrap roster: with no
# store-backed ride open the roster carries no ride-owned rng_seed at
# all, so Pick card used to refuse with the "every card logo is
# already in use" message against zero teams. A fixed deck here makes
# the empty state's card picks work; a ride the library opens swaps in
# the store roster seeded with the ride's own rng_seed.
_SEEDED_TEAM_LOGO_SEED = 20260906

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

# The View row's own commands.py target (P8-D8): its 8 ids share one
# route, dispatched further by event id below -- mi_hide_times and the
# seven zoom radios, with anything else falling to the generic COMMAND
# stub (W13: the theme trio left the View menu).
_VIEW_ROUTE_TARGET = "view_setting"

# E9.1.1's launch seam: the env var that points the bundled binary at
# a temp rides.db (the packaged-app smoke stages one through it), with
# an explicit ``main(db_path=...)`` argument taking precedence over it.
_DB_PATH_ENV = "RIVERCROSSING_DB_PATH"


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
            is opened -- by the launch flow's Continue (E5.2.2) or the
            library's Open -- and what File ▸ Duplicate Ride… reads.
        settings: The live :class:`AppSettings` this launch loaded
            (E8.1.1); the layout-save callback updates it as the
            sash/geometry persist.
        settings_path: The per-user settings file this launch loaded
            from, what the layout-save callback writes back to.
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
    # E6.4.2/Part D: the most recent per-format results exports,
    # backing Preview HTML in Browser / Preview PDF in Browser
    # (commands.RideState.html_exported / pdf_exported derive from
    # them). In-memory for the session: an app restart disables both
    # preview items until the next export.
    html_export_path: Path | None = None
    pdf_export_path: Path | None = None
    # E7.3.2: the engine event count captured at the last successful
    # results export (the export watermark). The results refresh
    # compares the live event log to it: any correction event at/after
    # this count landed after the export and shows the stale_export
    # banner. ``None`` until a fresh export succeeds -- nothing
    # published yet, so nothing is stale.
    export_watermark: int | None = None
    # E7.2.1: the entry detail currently open in the app (its plate),
    # recorded when the entry-detail route opens one. The correction
    # menu routes (Edit Crossing…, Reassign Plate…, Mark DNF…, Void
    # Card…) read it as the entry they target -- the dialogs carry no
    # plate field of their own (dialogs.xrc section C), so the "current
    # entry" context is what lets a menu item correct the entry the
    # scorer was just looking at. ``None`` until an entry detail is
    # opened; a correction route with none posts a notice instead.
    detail_plate: str | None = None
    # E8.1.1: the per-user settings file this launch loaded from and
    # the live AppSettings. The layout-save callback updates
    # :attr:`settings` as the sash/geometry persist, so a later
    # settings dialog (E8.2) opens onto the same current values.
    settings: AppSettings = field(default_factory=settings_store.default_settings)
    settings_path: Path = field(default_factory=settings_store.default_path)


def _build_console_engine(roster: Roster) -> tuple[RideEngine, EngineDataSource]:
    """Build the empty DRAFT ride the console runs at bootstrap (R-31).

    With no store-backed ride open yet (E5.4.2), the console still
    opens on a real engine: a valid :class:`~rivercrossing.ride.
    RideConfig` matching *roster*'s own settings, an 8-deck seeded
    shoe, and the real wall clock. The engine is left DRAFT -- no ride
    is running at a fresh launch -- so File ▸ Quit shows the plain
    ``exit_confirm_dlg``, not the "stop the running ride" dialog. The
    bootstrap roster is empty until the library Open / resume flow
    loads a store ride, so the fresh console is the correct empty
    state: zero crossings, zero counters, full shoe, plate entry
    disabled (R-31).

    This is the one place a ride is created at bootstrap; E5's
    store-backed create/reopen flow replaces it (``_switch_console_
    to_ride``, called by the launch flow's Continue and the library's
    Open).

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
    return engine, EngineDataSource(engine, roster)


_loaded_xrc_resource: Any | None = None
"""The process-global ``XmlResource``, loaded once and reused.

Re-parsing the .xrc files on every ``build_main_window`` call is
wasteful and re-rolls the Fault-B degraded-load die (a parse under
load can skip a subtree, and a later re-parse can overwrite an earlier
clean one). Load once per process and reuse, matching
``tests/functional/harness.load_xrc_resources``'s session-scoped
load-once pattern.
"""


def _load_xrc_resources() -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Load every packaged ``.xrc`` file into the global resource once.

    Mirrors ``tests/functional/harness.load_xrc_resources`` exactly,
    but is not imported from there: that module is test-only
    infrastructure, absent from a frozen bundle's own package path.
    ``wx.xrc.XmlResource.Get()`` is a process-wide singleton, so the
    loaded resource is memoized and returned on later calls.
    """
    global _loaded_xrc_resource  # noqa: PLW0603 -- module-level memoization cache
    if _loaded_xrc_resource is not None:
        return _loaded_xrc_resource
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    xrc_dir = Path(__file__).resolve().parent / "xrc"
    resource = wx.xrc.XmlResource.Get()
    for path in sorted(xrc_dir.glob("*.xrc")):
        resource.Load(str(path))
    _loaded_xrc_resource = resource
    return resource


def _fresh_xrc_resource() -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Return a private ``XmlResource`` loaded from every packaged .xrc.

    The Fault-B rebuild source: a *new* ``wx.xrc.XmlResource()`` (never
    the process-wide singleton :func:`_load_xrc_resources` loads, whose
    degraded builds under worker load are what this works around),
    loaded from the same ``ui/xrc/*.xrc`` files. Mirrors
    ``tests/functional/harness._fresh_resource``.
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    xrc_dir = Path(__file__).resolve().parent / "xrc"
    resource = wx.xrc.XmlResource()
    for path in sorted(xrc_dir.glob("*.xrc")):
        resource.Load(str(path))
    return resource


# 25, mirroring ui.views._support.FIND_SETTLE_ATTEMPTS (and the
# harness's own _FIND_SETTLE_ATTEMPTS): the same wx/SIP wrapper-cache
# stale-lookup hazard _support.find_control settles applies to the app
# gate's own name lookups too.
_FIND_SETTLE_ATTEMPTS = 25


def _missing_required_control(
    frame: Any,  # noqa: ANN401 -- wx ships no stubs; Any is honest
    required: tuple[str, ...],
    classes: dict[str, type],
) -> str | None:
    """Return *required*'s first name that does not resolve in *frame*.

    The verify step of :func:`_load_frame_verified`. A name resolves
    only when ``wx.Window.FindWindowByName(name, frame)`` answers an
    instance of ``classes[name]`` -- the concrete class
    ``MainFrame.__init__`` later ``_find``s. A ``None`` answer, or a
    NON-None stale wrapper of the WRONG Python type (the wx/SIP
    wrapper-cache corruption: an address reuse answers a stale wrapper
    whose Python class is wrong for the live control), means XRC
    silently skipped that control during the load (the Fault-B
    degraded-load class) -- a degraded load is rebuilt, never
    false-fasted. ``None`` (the return) means every required name
    resolved to its expected class: the build is complete.

    Each lookup settles the stale-lookup hazard with the bounded
    ``del control; gc.collect()`` re-query idiom the harness's own gate
    uses -- never a ``SafeYield``: yielding during verification is
    exactly the event processing the degradation hides in.
    """
    require_wx()
    import wx  # noqa: PLC0415 -- deferred, see module docstring

    for name in required:
        expected = classes[name]
        control = wx.Window.FindWindowByName(name, frame)
        attempts = 0
        while not isinstance(control, expected) and attempts < _FIND_SETTLE_ATTEMPTS:
            del control
            gc.collect()
            control = wx.Window.FindWindowByName(name, frame)
            attempts += 1
        if not isinstance(control, expected):
            return name
    return None


def _load_frame_verified(
    resource: Any,  # noqa: ANN401 -- wx ships no stubs; Any is honest
    required: tuple[str, ...],
    classes: dict[str, type],
) -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Load ``main_frame`` from *resource* and verify *required*.

    Fault-B (degraded-XRC-load) production mirror of
    ``tests/functional/harness.load_window_verified``: under worker load
    the process-global ``wx.xrc.XmlResource`` singleton can silently
    skip a subtree during a load, so the frame comes back missing a
    control ``MainFrame.__init__`` later needs -- which would otherwise
    surface as a bare ``LookupError`` from
    ``ui.views._support.find_control``. This verifies every name in
    *required* resolves to the concrete class ``classes[name]`` the ctor
    demands -- a name that cannot resolve to its expected control class
    (a ``None`` answer or a NON-None stale wrong-typed wrapper) counts
    as missing; a genuinely incomplete build is rebuilt ONCE from a
    fresh private ``wx.xrc.XmlResource()`` (never the degraded
    singleton) and re-verified. The rebuild happens while the degraded
    frame is still alive -- mirroring the harness's ordering, so the
    rebuilt controls cannot land on the degraded frame's just-freed
    addresses -- and the degraded frame is then destroyed (a plain
    ``frame.Destroy()``; its deferred deletion is processed by the
    app's later ``MainLoop``). A healthy build costs one extra
    name-walk and nothing else.

    Raises:
        LookupError: If the fresh rebuild is itself still missing a
            required control. The message carries the rebuilt frame's
            first-level-child inventory, in the same shape
            ``ui.views._support.find_control`` uses.
    """
    frame = resource.LoadFrame(None, ids.MAIN_FRAME)
    if _missing_required_control(frame, required, classes) is None:
        return frame

    fresh = _fresh_xrc_resource()
    rebuilt = fresh.LoadFrame(None, ids.MAIN_FRAME)
    frame.Destroy()
    if rebuilt is None:
        raise LookupError(f"fresh XmlResource found no window named {ids.MAIN_FRAME!r} to rebuild")
    missing = _missing_required_control(rebuilt, required, classes)
    if missing is not None:
        window_name = rebuilt.GetName()
        children = [child.GetName() for child in rebuilt.GetChildren()]
        rebuilt.Destroy()
        raise LookupError(
            f"{window_name} has no control named {missing!r} "
            f"(first-level children: {len(children)} -- {children!r})"
        )
    return rebuilt


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


def _check_loaded_hide_times(menubar: Any, *, hide: bool) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Set ``mi_hide_times``'s check item to *hide* (E8.1.3).

    Called at startup after ``LoadMenuBar`` (the fresh check item is
    unchecked, so ``False`` is a no-op) and whenever the settings
    dialog applies a new hide-times value (the mirror). ``wxMenuBar.
    Check`` sets the check state explicitly -- a synthetic ``EVT_MENU``
    does not auto-toggle check items on this pin (measured for the
    radio items; the functional suite verifies the check item the same
    way).
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    menubar.Check(wx.xrc.XRCID(ids.MI_HIDE_TIMES), hide)


def _check_loaded_zoom_radio(menubar: Any, percent: int) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Tick the zoom radio for *percent* (E8.1.4, startup).

    ``wxMenuBar.Check`` also unchecks the zoom group's other six
    members (measured, ``_handle_view_row``'s own note), so ticking the
    percent's radio alone restores the selection. Called at startup
    with the loaded percent (ticking 100 -- the default when no file
    exists -- is the documented ``mi_zoom_100`` default the XRC cannot
    declare: ``<checked>`` is a no-op on ``wxITEM_RADIO``). W13 removed
    the settings dialog's zoom choice (notes #12), so this is no longer
    a Settings mirror call: zoom changes only through the View-menu
    radios, which tick themselves.
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    menubar.Check(wx.xrc.XRCID(zoom.menu_item_id_for(percent)), True)  # noqa: FBT003 -- wx API takes a positional bool


def _toggle_hide_times(context: _RouteContext) -> None:
    """Flip the hide-times setting live and persist it (E8.1.3).

    Applies through the console presenter's ``on_hide_times`` (when a
    presenter is threaded) and sets the ``mi_hide_times`` check item
    explicitly -- a synthetic ``EVT_MENU`` does not auto-toggle check
    items on this pin (measured for the radio items; verified for the
    check item the same way in the functional suite).
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    hide = not context.settings.hide_times
    updated = replace(context.settings, hide_times=hide)
    settings_store.save_settings(updated, context.settings_path)
    context.settings = updated
    context.frame.GetMenuBar().Check(wx.xrc.XRCID(ids.MI_HIDE_TIMES), hide)
    presenter = context.presenter
    if presenter is not None:
        presenter.on_hide_times(hide=hide)


def _zoom_item_id_for(real_id: int) -> str | None:
    """Return the zoom radio's own XRC name for *real_id*, if any.

    An ``EVT_MENU`` carries only the resolved runtime int, never the
    XRC name, so the seven zoom ids are walked back explicitly rather
    than kept in some other, larger lookup this row's other id would
    also need to share.
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    return next(
        (item_id for item_id in zoom.ZOOM_MENU_ITEM_IDS if wx.xrc.XRCID(item_id) == real_id),
        None,
    )


def _handle_view_row(context: _RouteContext, route: commands.MenuRoute, event: Any) -> None:  # noqa: ANN401
    """Dispatch the View row: hide-times and zoom ids, else stub.

    E8.1.3 adds ``mi_hide_times``: a live toggle -- flip
    ``context.settings.hide_times``, apply through the console
    presenter, persist, and set the check item explicitly (a synthetic
    event does not auto-toggle check items either). E8.1.4 adds the
    seven ``mi_zoom_*`` radios: apply through the zoom controller,
    persist, and tick the fired radio explicitly.

    A synthetic ``EVT_MENU`` never flips a menu item's own checked
    state the way a genuine native click does (measured: this
    harness's functional suite has no delivery mechanism but direct
    event injection, harness.py's own module docstring), so each
    branch sets its item's checked state explicitly; ``wxMenuBar.
    Check`` also unchecks the other members of a radio group
    (measured), matching a real click's native handling.

    W13 (notes #14): the theme trio left this row -- the Settings
    appearance radios are the single theme surface, applied through
    ``_apply_settings_live``; only hide-times and zoom dispatch here.
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    if event.GetId() == wx.xrc.XRCID(ids.MI_HIDE_TIMES):
        _toggle_hide_times(context)
        return
    zoom_item_id = _zoom_item_id_for(event.GetId())
    if zoom_item_id is not None:
        percent = zoom.percent_for_menu_id(zoom_item_id)
        zoom.set_percent(percent)
        context.frame.GetMenuBar().Check(event.GetId(), True)  # noqa: FBT003 -- wx API takes a positional bool
        updated = replace(context.settings, zoom_percent=percent)
        settings_store.save_settings(updated, context.settings_path)
        context.settings = updated
        return
    context.frame.SetStatusText(f"{route.label} — not yet implemented")


def _delete_refusal_text(exc: Exception) -> str:
    """Return the operator-facing delete-refusal message for *exc*.

    ``StoreError`` subclasses carry developer-facing text (a row id, a
    stored-status word) that UX-DESKTOP §9 bars from the UI, so each
    maps to a plain what/why line; ``OSError`` and ``sqlite3.Error``
    refusals (a locked database, an unwritable backup target) pass
    their own text through, the idiom the resume-error alert uses.

    Args:
        exc: The refusal the store raised.

    Returns:
        The message ``std_dialogs.show_error`` shows.
    """
    if isinstance(exc, RideRunningError):
        return "The ride is running, so it cannot be deleted. Finish the ride first."
    if isinstance(exc, RideNotFoundError):
        return "The ride is no longer in the library."
    if isinstance(exc, RideNameMismatchError):
        return (
            "The ride's name changed since the library opened."
            " Close and reopen the library, then try again."
        )
    return f"Could not delete ride: {exc}"


def _library_delete_callback(
    context: _RouteContext,
    window: Any,  # noqa: ANN401 -- wx ships no stubs; the loaded ride_library_dlg
) -> Callable[[RideSummary], None] | None:
    """Return the library's store-backed delete callback, if any.

    E5.3.2's R-18 seam: a confirmed Delete on ``delete_ride_dlg``
    calls this with the selected ride row, and the store deletes it
    (writing its backup first). W10: the delete addresses the row by
    its ``ride_id`` -- the row the operator selected -- so duplicate
    ride names can never delete the wrong (first) match, and no
    ``rides()`` name-resolution scan runs inside the callback at all.
    With no store open there is nothing to delete, and the callback
    resolves to ``None`` -- the no-store library's rows are the
    E5.4.2 empty state (zero rows), so there is no ride to delete
    either way.

    A refused delete -- a locked database, an unwritable backup
    target, or the store's own ``RideRunningError`` refusal --
    surfaces as an error dialog above *window*, the library the
    delete was confirmed on (W10): the callback runs from the
    library's delete-confirm handler, an unguarded raise there is
    swallowed by wx with zero signal (the measured note
    ``docs/EPIC3-SESSION-SUMMARY.md`` records), and a status notice
    is invisible behind the library modal. ``StoreError`` joins
    ``OSError``/``sqlite3.Error`` in the guard so every refusal the
    store raises is caught.

    Args:
        context: The route context whose store deletes the ride.
        window: The live ``ride_library_dlg`` the delete was
            confirmed on; the refusal dialog parents to it.
    """
    store = context.store
    if store is None:
        return None

    def _delete(selected: RideSummary) -> None:
        if selected.ride_id is None:
            # logic-coverage-exempt: T-3 -- demo-era rows carry no
            # store id, and the library's own rows always do when a
            # store is open; the guard keeps the typed seam total.
            return
        try:
            store.delete_ride(selected.ride_id, selected.name)
        except (OSError, sqlite3.Error, StoreError) as exc:
            from rivercrossing.ui import (  # noqa: PLC0415 -- deferred, see module docstring
                std_dialogs,
            )

            std_dialogs.show_error(window, "Could Not Delete Ride", _delete_refusal_text(exc))

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


def _status_notice(context: _RouteContext) -> Callable[[str], None]:
    """Return a status-bar notice poster for *context*'s frame.

    The store-write guards' ``notify`` seam: a lazy closure so the
    frame is only touched when a failure actually posts (frame-less
    test constructions stay safe on the success path).
    """

    def _post(text: str) -> None:
        context.frame.SetStatusText(text)

    return _post


def _wire_store_append(  # noqa: PLR0913 -- (engine, store, ride_id) + the notice seam
    engine: RideEngine, store: Store, ride_id: int, *, notify: Callable[[str], None]
) -> None:
    """Persist every future engine event to *store* (E9.1.3).

    Attaches the store's append as the engine's event sink: from here
    on, every mutation the engine records -- crossings, undo,
    corrections, lifecycle -- writes one audit row per event. Called
    only after ``store.load_engine``'s replay completes, so the
    replayed tail is never re-persisted (the sink is off during
    replay; ``RideEngine.on_event`` docstring).

    A refused append (a locked or unwritable database) is caught
    inside the sink and surfaced through *notify* as
    ``Could not save event: {exc}``: the sink runs inside the engine
    mutation (``ride._append``), so an unguarded store error would
    abort the mutation mid-way and the wx handler calling it would
    swallow the raise with zero signal (the measured note
    ``docs/EPIC3-SESSION-SUMMARY.md`` records) -- the in-memory ride
    changed, the database did not, and nothing was said.

    W3 session lifecycle: the sink also owns the R-52 resume marker.
    A ``start`` event calls :meth:`Store.set_active_ride` (the ride
    creation path used to mark the session; only a ride that actually
    started may offer "continue" at the next launch) and a ``finish``
    event calls :meth:`Store.clear_active_ride` (a finished ride is
    never offered as still running). Both run under the same guard as
    the append: a refused marker write degrades to the *notify*
    notice, never a raise into the engine mutation.

    Args:
        engine: The engine to attach the sink to.
        store: The live Store the sink writes to.
        ride_id: The ride every future event belongs to.
        notify: Where a refused append's notice goes (the main
            frame's status bar).
    """

    def _append(event: Event) -> None:
        try:
            store.append(ride_id, event)
            if event.action == "start":
                store.set_active_ride(ride_id)
            elif event.action == "finish":
                store.clear_active_ride()
        except (OSError, sqlite3.Error) as exc:
            notify(f"Could not save event: {exc}")

    engine.on_event = _append


def _show_ride_header(context: _RouteContext, config: RideConfig) -> None:
    """Render *config*'s identity block onto the console header (C1).

    One seam for every console switch: the ride's name, its logo when
    it has one, and the date/start/type fallback line -- all read off
    the config the console is running, so a store reload, a New Ride
    and an Edit Ride cannot render different headers.
    """
    view = context.console_view
    if view is None:
        return
    view.show_ride_header(
        name=config.name,
        logo=config.logo_path,
        event_date=config.event_date,
        planned_start=config.planned_start,
        entry_mode=config.entry_mode,
    )


def _swap_console_onto(  # noqa: PLR0913, PLR0917 -- (context, engine, roster, source)
    context: _RouteContext,
    engine: RideEngine,
    roster: Roster,
    source: EngineDataSource,
) -> None:
    """Build a presenter over *engine* and render it onto the console.

    The one render sequence every console swap shares: the library
    Open's store load (:func:`_switch_console_to_ride`) and D3's Clear
    Ride reset. ``set_presenter`` rewires the plate entry, lifecycle
    controls and tick timer without rebinding (E5.2.2's resume wiring
    is the same shape, applied at launch). The context's presenter and
    roster are mutated in place because every bound route handler
    closes over this one object (``_RouteContext`` docstring).
    """
    view = context.console_view
    presenter = ConsolePresenter(view, engine=engine, source=source)
    view.set_presenter(presenter)
    _show_ride_header(context, engine.config)
    view.set_state(source.ride_status(), stopped=engine.stopped)
    view.show_feed(source.feed_rows())
    view.show_counters(source.counters())
    view.focus_entry()
    context.presenter = presenter
    context.roster = roster


def _switch_console_to_ride(
    context: _RouteContext, ride_id: int, clock: Callable[[], datetime] | None = None
) -> None:
    """Load *ride_id* from the store and swap the live console onto it.

    E5.4.1's library Open: the one place the console changes ride
    after bootstrap. Rebuilds the ride's roster and engine from the
    DB (:meth:`Store.roster_for`/:meth:`Store.load_engine`), wires the
    store's append as the engine's event sink, and renders it through
    :func:`_swap_console_onto`. The route context's presenter/roster/
    ``active_ride_id`` are mutated in place because every bound route
    handler closes over this one object (``_RouteContext`` docstring).
    """
    store = context.store
    if store is None or context.console_view is None:
        return
    roster = store.roster_for(ride_id)
    engine = store.load_engine(ride_id, roster, clock=clock)
    _wire_store_append(engine, store, ride_id, notify=_status_notice(context))
    _swap_console_onto(context, engine, roster, EngineDataSource(engine, roster))
    context.active_ride_id = ride_id
    log = _log(context)
    if log is not None:
        log.ride_loaded(ride_id=ride_id)


def _persist_created_ride(context: _RouteContext, config: RideConfig) -> None:
    """Persist a New Ride, then switch the console onto it (E9.1.4).

    The ride-setup dialog's submit callback (``_decorate``'s
    ``RIDE_SETUP_DLG`` branch): with a store open, creates the ride
    row, persists the roster the dialog was opened on, and switches
    the console onto the new ride. With no store the dialog keeps its
    E3.5 in-memory behavior.

    W3 session lifecycle: creating a ride no longer marks it active on
    the open session -- the ``start`` event's sink owns the R-52
    marker, so a ride that was never started is never offered as
    running at the next launch. The console switch is deferred through
    ``wx.CallAfter``, the same modal-chaining rule the library Open
    uses (``_live_library_callbacks``'s own docstring): this runs
    inside the setup dialog's submit, before ``EndModal``, and a
    post-modal action performed synchronously inside a modal's unwind
    is not dismissible by the functional harness (measured there).

    Args:
        context: The route context whose store/roster to act on.
        config: The committed ride configuration.

    A refused store write (a locked or unwritable database) surfaces
    as a status notice and leaves the console where it is: this runs
    inside the setup dialog's submit handler, and an unguarded raise
    there is swallowed by wx with zero signal (the measured note
    ``docs/EPIC3-SESSION-SUMMARY.md`` records) -- the operator would
    believe the ride was created.
    """
    store = context.store
    if store is None:
        return
    try:
        ride_id = store.create_ride(config)
    except (OSError, sqlite3.Error) as exc:
        context.frame.SetStatusText(f"Could not create ride: {exc}")
        return
    try:
        store.save_roster(ride_id, context.roster)
    except (OSError, sqlite3.Error) as exc:
        context.frame.SetStatusText(f"Could not save riders: {exc}")
        return
    require_wx().CallAfter(_switch_console_to_ride, context, ride_id)


# D2/D3: the two rows the Ride menu gains. Edit Ride reuses the New
# Ride window in its preload mode (the dialog's own title separates
# them); Clear Ride is destructive, so it confirms through the native
# error-icon danger dialog before touching anything.
EDIT_RIDE_TITLE = "Edit Ride"


def _empty_roster_for(config: RideConfig) -> Roster:
    """Build an empty roster shaped like *config* (D3's Clear Ride).

    The ride keeps its own setup (entry mode, plate model, team size);
    only its entries, crossings, cards and audit trail are cleared.
    """
    return Roster(
        entry_mode=config.entry_mode,
        max_team_size=config.max_team_size,
        plate_model=config.plate_model,
    )


def _decorate_edit_ride(context: _RouteContext, window: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Bind ``ride_setup_dlg`` in its Edit Ride mode (D2).

    The same XRC window New Ride loads, retitled and PRELOADED with
    the live ride's config; a committed submit writes the edited
    config back onto the live engine and the store row
    (:func:`_apply_edited_ride`). The view is imported as a module
    (``ride_setup.RideSetup``) so this decorator can be driven headless
    against a stub, the one thing the app's other decorators do not
    need.
    """
    from rivercrossing.ui.views import ride_setup  # noqa: PLC0415 -- deferred

    presenter = context.presenter
    if presenter is None:
        # The menu row only lights while a console ride is threaded,
        # so this guard is defensive; a console-less route posts the
        # same notice every other engine-less handler does.
        context.frame.SetStatusText(f"{EDIT_RIDE_TITLE} — no ride open")
        return
    window.SetTitle(EDIT_RIDE_TITLE)
    ride_setup.RideSetup(
        window,
        roster=context.roster,
        config=presenter.engine.config,
        on_submitted=lambda edited: _apply_edited_ride(context, edited),
    )


def _apply_edited_ride(context: _RouteContext, config: RideConfig) -> None:
    """Apply an Edit Ride submit to the live ride and persist it (D2).

    The live engine keeps its shoe, roster and event log -- only its
    config changes, so editing a started ride's name or venue never
    rewinds it. With a store-backed ride open the ride row is
    rewritten too, so the edit survives a relaunch.

    A refused store write (a locked or unwritable database) surfaces
    as a status notice and leaves both the row and the live engine
    untouched: this runs inside the setup dialog's submit, before
    ``EndModal``, and an unguarded raise there is swallowed by wx with
    zero signal (the measured note ``docs/EPIC3-SESSION-SUMMARY.md``
    records) -- the operator would believe the ride was renamed.
    """
    presenter = context.presenter
    if presenter is None:
        # The same defensive guard as _decorate_edit_ride's.
        context.frame.SetStatusText(f"{EDIT_RIDE_TITLE} — no ride open")
        return
    store = context.store
    ride_id = context.active_ride_id
    if store is not None and ride_id is not None:
        try:
            store.update_ride_config(ride_id, config)
        except (OSError, sqlite3.Error, StoreError) as exc:
            context.frame.SetStatusText(f"Could not save ride: {exc}")
            return
    presenter.engine.update_config(config)
    _show_ride_header(context, config)
    context.frame.SetStatusText("Ride settings saved")


def _handle_clear_ride_route(context: _RouteContext) -> None:
    """Ride ▸ Clear Ride…: confirm, then reset the ride (D3).

    The ride is reset IN PLACE -- its entries, crossings, cards and
    audit rows are removed and its state returns to DRAFT -- rather
    than deleted and re-created: the ride keeps its own setup and its
    library entry, and the console switches onto the emptied ride
    through the same store load the library Open uses. The console is
    left exactly as the bootstrap opens it (zero crossings, zero
    counters, empty feed).

    With no store (the store-less bootstrap console) the same reset
    runs in memory over :func:`_build_console_engine`. The confirm is
    the native danger dialog; anything but OK leaves every row alone.
    """
    from rivercrossing.ui import std_dialogs  # noqa: PLC0415 -- deferred, see module docstring

    presenter = context.presenter
    if presenter is None:
        context.frame.SetStatusText("Clear Ride — no ride open")
        return
    config = presenter.engine.config
    confirmed = std_dialogs.show_danger(
        context.frame,
        "Clear Ride",
        f'Clears "{config.name}": every rider, crossing and card is removed '
        "and the ride returns to DRAFT. This cannot be undone.",
        "Clear Ride",
        "Cancel",
    )
    if confirmed != int(require_wx().ID_OK):
        return
    store = context.store
    ride_id = context.active_ride_id
    if store is not None and ride_id is not None:
        try:
            store.clear_ride(ride_id)
        except (OSError, sqlite3.Error, StoreError) as exc:
            context.frame.SetStatusText(f"Could not clear ride: {exc}")
            return
        _switch_console_to_ride(context, ride_id)
        context.frame.SetStatusText("Ride cleared")
        return
    empty_roster = _empty_roster_for(config)
    engine, source = _build_console_engine(empty_roster)
    _swap_console_onto(context, engine, empty_roster, source)
    context.frame.SetStatusText("Ride cleared")


def _live_library_callbacks(
    context: _RouteContext,
    window: Any,  # noqa: ANN401 -- wx ships no stubs; a loaded wx.Dialog
    store: Store,
) -> tuple[Callable[[RideSummary], None], Callable[[RideSummary], None]]:
    """Return the store-backed library's Open/Duplicate callbacks.

    E5.4.1 wires the live library to the real DB through these two:

    - **Open** loads the selected ride and swaps the console onto it
      (:func:`_switch_console_to_ride`), then ends the library modal
      -- deferred through ``wx.CallAfter``, the same modal-chaining
      avoidance the resume flow's ``library_btn`` uses (measured
      there: a modal opened synchronously inside this one's unwind is
      not dismissible by the harness).
    - **Duplicate** shows the ride's name in the E5.4.1 mock-first
      confirm and, on OK, calls ``Store.duplicate_ride`` -- the view
      refreshes its own rows afterwards, so the new DRAFT ride
      appears immediately (R-15). A refused duplicate surfaces as a
      status notice: an unguarded raise from this button callback is
      swallowed by wx with zero signal (the measured note
      ``docs/EPIC3-SESSION-SUMMARY.md`` records).

    W10 dropped the third callback (New): the library no longer has a
    New button, so the ride-setup flow stays File ▸ New Ride…'s route
    alone.

    ``window`` is the live ``ride_library_dlg``, used to end the
    modal for Open. ``store`` is the live Store the callbacks act on
    (:func:`_decorate` only calls this with one open).
    """
    wx = require_wx()

    def _open(selected: RideSummary) -> None:
        if selected.ride_id is None:
            return
        if not window.IsBeingDeleted():
            window.EndModal(wx.ID_CLOSE)
        wx.CallAfter(_switch_console_to_ride, context, selected.ride_id)

    def _duplicate(selected: RideSummary) -> None:
        if selected.ride_id is None:
            return
        try:
            store.duplicate_ride(selected.ride_id)
        except (OSError, sqlite3.Error) as exc:
            context.frame.SetStatusText(f"Could not duplicate ride: {exc}")

    return _open, _duplicate


def _apply_settings_live(context: _RouteContext, settings: AppSettings) -> None:
    """Persist *settings* and apply its live paths (E8.1.2-E8.1.4).

    The settings dialog's OK callback: saves to the config file,
    updates the context's current settings, then applies what has live
    paths -- appearance through the live theme controller (W13: the
    Settings appearance radios are the single theme surface, so the
    mode is applied directly, with no View-menu radio to re-check),
    sound through :func:`~rivercrossing.ui.sound.set_muted`, and
    hide-times through the console presenter's ``on_hide_times`` (when
    a presenter is threaded) with the View-menu check item synced
    (``_check_loaded_hide_times``). ``zoom_percent`` is carried through
    untouched: W13 removed the dialog's zoom choice (notes #12), so
    zoom applies only at startup from the persisted file and through
    the View-menu radios (``_handle_view_row``).

    F1/F3: ``verbose_logging`` (``verbose_log_chk``) is applied to the
    live :class:`~rivercrossing.ui.logging.Logging` the launch
    constructed, so unticking it stops the trace records at once.
    """
    try:
        settings_store.save_settings(settings, context.settings_path)
    except OSError as exc:
        # A failed settings write is a notice, not a crash: this runs
        # inside the dialog's OK handler, and an unguarded raise there
        # is swallowed by wx with zero signal -- the operator would
        # believe the choice was saved.
        context.frame.SetStatusText(f"Could not save settings: {exc}")
    context.settings = settings
    mode = theme.ThemeMode(settings.appearance)
    notice = context.theme_controller.apply_mode(mode)
    if notice is not None:
        context.frame.SetStatusText(notice)
    _check_loaded_hide_times(context.frame.GetMenuBar(), hide=settings.hide_times)
    sound.set_muted(muted=not settings.sound_on)
    log = _log(context)
    if log is not None:
        log.set_verbose(settings.verbose_logging)
    presenter = context.presenter
    if presenter is not None:
        presenter.on_hide_times(hide=settings.hide_times)


def _save_layout_settings(
    context: _RouteContext,
    sash: int | None,
    geometry: tuple[int, int, int, int] | None,
) -> None:
    """Persist *sash*/*geometry*; keep the context current.

    The app bootstrap wires this as ``MainFrame``'s own
    ``on_layout_changed`` callback (E8.1.1): ``persist_layout`` fires
    it from the sash, move/size and close handlers. A refused write
    surfaces as a status notice instead of an unguarded raise out of
    those wx handlers, which wx swallows with zero signal -- and from
    the close handler would stall ``event.Skip()``, leaving the
    window unable to quit (the measured note
    ``docs/EPIC3-SESSION-SUMMARY.md`` records).

    Args:
        context: The route context whose settings to advance.
        sash: The splitter sash position to persist.
        geometry: The frame placement ``(x, y, width, height)`` to
            persist.
    """
    updated = replace(context.settings, splitter_sash=sash, window_geometry=geometry)
    try:
        settings_store.save_settings(updated, context.settings_path)
    except OSError as exc:
        context.frame.SetStatusText(f"Could not save settings: {exc}")
        return
    context.settings = updated


def _open_entry_detail_dialog(context: _RouteContext, window: Any, plate: str) -> None:  # noqa: ANN401
    """Decorate *window* as the entry detail for *plate* (E7.2.1).

    The entry-detail decoration both the ``mi_entry_detail`` menu route
    (via :func:`_decorate`) and the W11 F2a flagged-tab seam
    (:func:`_open_entry_detail_for`) perform: with a live console
    threaded AND a concrete entry (``context.detail_plate``, recorded
    when entry detail opened), entry detail opens that entry over the
    live engine/roster/resource, so the six action buttons act on real
    data; with no selection the E5.4.2 empty state stays (a live
    engine with an unset plate would raise LookupError from
    ``entry_detail("")`` -- R-38's loud failure is for a deep-linked
    plate, not the menu's no-selection default).

    Args:
        context: The route context whose live seams to thread.
        window: The already-loaded ``entry_detail_dlg`` to decorate.
        plate: The plate to open; ``""`` keeps the empty state.
    """
    from rivercrossing.ui.views.entry_detail import (  # noqa: PLC0415 -- deferred, see module docstring
        EntryDetailDialog,
    )

    presenter = context.presenter
    if presenter is not None and plate:
        context.detail_plate = plate
        engine = presenter.engine
        EntryDetailDialog(
            window,
            plate,
            data_source=presenter.source,
            engine=engine,
            roster=context.roster,
            resource=context.resource,
            notify=context.frame.SetStatusText,
            on_corrected=lambda: _apply_menu_state(context, engine.state),
            # W11 F2b: a plate_choice pick retargets the dialog AND
            # becomes the current entry the correction routes target.
            on_plate_picked=lambda plate: setattr(context, "detail_plate", plate),
        )
    else:
        EntryDetailDialog(window, _ENTRY_DETAIL_DEFAULT_PLATE, data_source=_EMPTY_SOURCE)


def _decorate(  # noqa: PLR0912, C901 -- one elif per decorated target; each binds a different view class
    context: _RouteContext,
    window: Any,  # noqa: ANN401 -- wx ships no stubs
    route: commands.MenuRoute,
) -> Any:  # noqa: ANN401 -- the caller reads the rider/team editor views back; other routes return None
    """Bind *window*'s code-side view class, if *route.target* has one.

    E7.3.1 added the audit trail to that set: ``audit_dlg`` now binds
    the real viewer (newest-first list + the two filters) over the
    live engine source. E8.1.2 adds the settings dialog:
    ``settings_dlg`` now binds the E8.1.2 viewer (renders the current
    AppSettings and, on OK, persists + applies it). E8.2.1 adds the
    shortcuts dialog: ``shortcuts_dlg`` now binds the E8.2.1 viewer
    (renders the accelerator table, Key | Action). E8.2.3 adds the
    About box: ``about_dlg`` now binds the E8.2.3 viewer (package
    version, ride-logo-or-app-icon). Phase 4 adds the teams editor:
    ``team_editor_dlg`` now binds the TeamEditor view over the live
    roster (team records -- name, relay plate, notes, logo). The
    rider simulator joins them: ``simulation_dlg`` binds SimulatorDialog
    over the same roster, driven by the live engine. The
    remaining plain XRC dialogs
    with no code-side view class (the correction dialogs) need
    nothing further here; they already carry their own canvas
    defaults from their own ``.xrc`` authoring.
    """
    from rivercrossing.ui.views.about import AboutDialog  # noqa: PLC0415
    from rivercrossing.ui.views.audit import AuditDialog  # noqa: PLC0415
    from rivercrossing.ui.views.results_win import ResultsWindow  # noqa: PLC0415
    from rivercrossing.ui.views.ride_library import RideLibrary  # noqa: PLC0415
    from rivercrossing.ui.views.ride_setup import RideSetup  # noqa: PLC0415
    from rivercrossing.ui.views.rider_editor import RiderEditor  # noqa: PLC0415
    from rivercrossing.ui.views.selftest import SelfTestDialog  # noqa: PLC0415
    from rivercrossing.ui.views.settings import SettingsDialog  # noqa: PLC0415
    from rivercrossing.ui.views.shortcuts import ShortcutsDialog  # noqa: PLC0415
    from rivercrossing.ui.views.simulator import SimulatorDialog  # noqa: PLC0415
    from rivercrossing.ui.views.team_editor import TeamEditor  # noqa: PLC0415

    if route.target == ids.RIDE_LIBRARY_DLG:
        if context.store is not None:
            on_open, on_duplicate = _live_library_callbacks(context, window, context.store)
            RideLibrary(
                window,
                data_source=_StoreLibrarySource(context.store),
                on_delete=_library_delete_callback(context, window),
                on_open=on_open,
                on_duplicate=on_duplicate,
            )
        else:
            # No store open: the library is the E5.4.2 empty state --
            # zero rides until a store-backed bootstrap or New Ride
            # creates one; the Delete seam stays a no-op.
            RideLibrary(
                window,
                data_source=_EMPTY_SOURCE,
                on_delete=_library_delete_callback(context, window),
            )
    elif route.target == ids.RIDER_EDITOR_DLG:
        # E5.4.2: the roster is the store's when a store-backed ride is
        # open (E5.4.1's library Open replaced context.roster), and the
        # empty bootstrap roster otherwise -- the rider editor shows a
        # correct empty state until a real ride is opened. W7 returns
        # the built view: _open_target persists this editor's changes
        # once its modal ends (the only route that needs the view
        # after decoration).
        return RiderEditor(window, roster=context.roster)
    elif route.target == ids.TEAM_EDITOR_DLG:
        # Phase 4: the same live-roster wiring as the rider editor --
        # team records (name, relay plate, notes, logo) over the
        # store's roster when one is open, the empty bootstrap roster
        # otherwise. The menu's own teams_allowed gate (entry_mode is
        # MIXED) is what lets this route fire at all. W8 returns the
        # built view: _open_target persists this editor's changes
        # once its modal ends, exactly like the rider editor.
        return TeamEditor(window, roster=context.roster)
    elif route.target == ids.RIDE_SETUP_DLG and ids.MI_EDIT_RIDE in route.ids:
        # D2: the same window in its Edit Ride mode -- the row's own id
        # decides (the mi_add_crossing_at/mi_edit_crossing precedent),
        # since both rows share one target and one XRC resource.
        _decorate_edit_ride(context, window)
    elif route.target == ids.RIDE_SETUP_DLG:
        # E9.1.2/E9.1.4: with a store open, a committed New Ride
        # persists the ride row and the roster the dialog was opened
        # on, then switches the console onto the new ride; with no
        # store the dialog keeps its E3.5 in-memory behavior.
        RideSetup(
            window,
            roster=context.roster,
            on_submitted=lambda config: _persist_created_ride(context, config),
        )
    elif route.target == ids.SIMULATION_DLG:
        # The simulator generates its placeholder field through the
        # live roster and replays the race through the console's own
        # engine, so it needs the threaded presenter. A route-level
        # context with none opens the plain XRC dialog (its Generate
        # and GO buttons inert); _open_target then has no view to
        # persist, and the roster is untouched anyway.
        if context.presenter is not None:
            return SimulatorDialog(window, engine=context.presenter.engine, roster=context.roster)
    elif route.target == ids.ENTRY_DETAIL_DLG:
        # E7.2.1 (shared with the W11 F2a flagged seam): the live
        # branch opens the selected entry over the live seams; the
        # empty branch keeps the E5.4.2 empty state. See
        # _open_entry_detail_dialog's own docstring.
        _open_entry_detail_dialog(context, window, context.detail_plate or "")
    elif route.target == ids.RESULTS_DLG:
        # E6.4.1 (D10): with a live console threaded, results render
        # the real placed rows from the console's EngineDataSource
        # (the same live source build_main_window wired, so the
        # roster always matches the engine -- the resume path never
        # updates context.roster) and rank them with the ride's
        # stored order. entry_mode decides the MIXED notebook or the
        # SOLO standalone list. The E5.4.2 empty state stays for the
        # no-presenter path (route-level tests), where the export
        # buttons stay disabled (DRAFT) and inert (no callback).
        presenter = context.presenter
        if presenter is not None:
            ResultsWindow(
                window,
                data_source=presenter.source,
                tiebreak_order=presenter.engine.config.tiebreak_order,
                export_watermark=context.export_watermark,
                entry_mode=presenter.engine.config.entry_mode,
                # W11: the four export buttons fire the same
                # _handle_export_command route the matching mi_export_*
                # menu row runs -- the dead synthetic-EVT_MENU
                # forwarding is gone (the parentless results window
                # never reached the main frame's handlers).
                on_export=lambda target: _handle_export_command(context, target),
            )
        else:
            ResultsWindow(window, data_source=_EMPTY_SOURCE)
    elif route.target == ids.SELFTEST_DLG:
        SelfTestDialog(window)
    elif route.target == ids.SETTINGS_DLG:
        # E8.1.2: Settings renders the context's current settings and,
        # on OK, persists + applies them through the live seams (the
        # appearance mirror to the View-menu radios). ux-polish wires
        # backup_now_btn: the dialog's on_backup_now seam runs the
        # same _handle_backup_database action File ▸ Back Up
        # Database… fires (R-54), surfacing the written path (or
        # failure) on the status bar.
        SettingsDialog(
            window,
            settings=context.settings,
            on_save=lambda new_settings: _apply_settings_live(context, new_settings),
            on_backup_now=lambda: _handle_backup_database(context),
        )
    elif route.target == ids.SHORTCUTS_DLG:
        # E8.2.1: Help ▸ Keyboard Shortcuts renders the accelerator
        # table -- one Key | Action row per Accelerator -- so the
        # dialog cannot drift from ui.accelerators (xrc-windows.md E).
        ShortcutsDialog(window)
    elif route.target == ids.ABOUT_DLG:
        # E8.2.3: the About box renders the package version and the
        # ride logo -- the live config's logo_path when a ride is
        # threaded, the app-icon fallback otherwise (about.py's own
        # contract).
        presenter = context.presenter
        logo_path = presenter.engine.config.logo_path if presenter is not None else None
        AboutDialog(window, logo_path=logo_path)
    elif route.target == ids.AUDIT_DLG:
        # E7.3.1: Audit Trail… opens the real viewer -- newest-first
        # audit_list plus the search/action filters -- over the live
        # console's engine source when one is threaded (the store-backed
        # ride's events), the E5.4.2 empty state otherwise. The roster
        # lets the search resolve a plate to its entry's display name.
        presenter = context.presenter
        if presenter is not None:
            AuditDialog(
                window,
                data_source=presenter.source,
                roster=context.roster,
            )
        else:
            AuditDialog(window, data_source=_EMPTY_SOURCE, roster=context.roster)
    return None


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


def _menu_ride_state(context: _RouteContext, status: RideStatus) -> commands.RideState:
    """Build the live §15 ``RideState`` the menu binder reads.

    Every field the enablement rules consult comes from the console's
    live engine (or the no-ride empty state when none is threaded).
    ``entry_has_cards`` reads "at least one entry holds a credited
    card" from the snapshot -- the global approximation of Void Card's
    per-entry "entry has cards" until the entry-detail flow supplies
    the concrete entry (E7.3.1's deep-link).
    """
    presenter = context.presenter
    engine = presenter.engine if presenter is not None else None
    if engine is None:
        return commands.RideState(status=status, ride_open=False)
    return commands.RideState(
        status=status,
        ride_open=True,
        ride_stopped=engine.stopped,
        crossings=len(engine.crossings),
        held_cards=len(engine.held_crossings()),
        audit_rows=len(engine.events),
        entry_has_cards=any(result.cards for result in engine.snapshot()),
        html_exported=context.html_export_path is not None,
        pdf_exported=context.pdf_export_path is not None,
        # Phase 4: teams only exist on a mixed ride, so the Teams
        # Editor menu row follows the config's entry_mode -- the
        # same fact the roster itself records (R-11).
        teams_allowed=engine.config.entry_mode is EntryMode.MIXED,
    )


def _apply_menu_state(context: _RouteContext, status: RideStatus) -> None:
    """Apply §15 enablement to the live menubar for *status* (E7.2.1).

    The missing E1.4.2 half: ``commands.is_route_enabled``'s rules
    were proven in unit tests but never applied to the real menubar.
    Wired to the console's ride-state-change seam
    (:meth:`MainFrame.set_on_ride_changed`) and to the correction
    handlers, so the §15 "Enabled when" cells hold in the app, not
    only in ``test_commands.py``. A frame with no menubar (route-level
    test constructions) is a silent no-op.
    """
    from rivercrossing.ui import menu_state  # noqa: PLC0415 -- deferred, see module docstring

    menubar = context.frame.GetMenuBar()
    if menubar is None:
        return
    menu_state.apply_to_menubar(menubar, _menu_ride_state(context, status))


def _handle_import_csv(context: _RouteContext) -> None:
    """File ▸ Import Riders CSV…: run the shared flow (E3.4).

    The route's own target stays ``csv_preview_dlg`` (commands.py
    unchanged) -- :func:`~rivercrossing.ui.views.rider_editor.
    run_csv_import_flow` is the one place this route handler runs the
    picker -> preview -> commit flow through (that module's own
    banner comment explains why it is hosted there, not here; W7
    removed the editor's own import_btn, so the File-menu route is
    the only caller left).

    R-74: a committed import into a store-backed ride persists the
    in-memory roster to the active ride (``Store.save_roster``), so
    the imported riders survive a relaunch -- and rebuilds the console
    onto that roster (:func:`_switch_console_to_ride`), because the
    live engine otherwise still resolves plates against its pre-import
    roster and typed plates would be rejected as unknown. With no
    store-backed ride open the import keeps its bootstrap in-memory
    behavior.

    A refused roster save (a locked or unwritable database) surfaces
    as a status notice and the console is NOT rebuilt: this runs
    inside a wx menu handler, and an unguarded raise there is
    swallowed with zero signal (the measured note
    ``docs/EPIC3-SESSION-SUMMARY.md`` records) -- worst case the
    operator is told the import succeeded while the roster silently
    stayed unpersisted.
    """
    from rivercrossing.ui.views import rider_editor  # noqa: PLC0415

    committed = rider_editor.run_csv_import_flow(context.frame, context.roster)
    store = context.store
    if committed and store is not None and context.active_ride_id is not None:
        try:
            store.save_roster(context.active_ride_id, context.roster)
        except (OSError, sqlite3.Error) as exc:
            context.frame.SetStatusText(f"Could not save riders: {exc}")
            return
        presenter = context.presenter
        # Carry the live engine's clock across the rebuild: a scripted
        # (injected) clock must survive, or every typed lap after the
        # import lands milliseconds apart and flags (R-34).
        clock = presenter.engine.clock if presenter is not None else None
        _switch_console_to_ride(context, context.active_ride_id, clock=clock)


def _handle_export_csv(context: _RouteContext) -> None:
    """File ▸ Export Riders CSV…: run the shared flow (E3.4).

    No window opens for this ``COMMAND`` row (spec.md §15's own
    "OS-native ... dialog, no app window") -- a cancelled picker is a
    silent no-op, the same shape :func:`_handle_import_csv` uses. A
    failed write surfaces on the status bar through the flow's
    ``on_error`` seam (``Export failed: {exc}``), the same notice
    idiom :func:`_handle_backup_database` uses -- never an unguarded
    raise into the wx menu handler, which swallows it.
    """
    from rivercrossing.ui.views import rider_editor  # noqa: PLC0415

    path = rider_editor.run_csv_export_flow(
        context.frame,
        context.roster,
        on_error=context.frame.SetStatusText,
    )
    if path is not None:
        context.frame.SetStatusText(f"Exported {path.name}")


def _handle_check_rider_issues(context: _RouteContext) -> None:
    """Riders ▸ Check for Rider Issues…: open the issues dialog (R-78).

    Any roster change made inside the dialog -- a one-click fix, or an
    edit made through the Open Editor button's nested rider/team
    editor -- mutates the shared in-memory roster, so this persists +
    rebuilds after the modal ends, exactly like a CSV import (persist
    first, then ``_switch_console_to_ride`` with the live clock), so
    the change survives a relaunch. ``run_rider_issues_flow`` reports
    an audit-log length delta rather than the presenter's own
    ``did_change``, so a nested editor's edit (which the presenter
    never performs) is caught too. A refused roster save surfaces as a
    status notice and skips the rebuild -- the same guard
    :func:`_handle_import_csv` uses, for the same wx-swallowed-raise
    reason (the measured note ``docs/EPIC3-SESSION-SUMMARY.md``
    records).
    """
    from rivercrossing.ui.views import rider_issues  # noqa: PLC0415

    changed = rider_issues.run_rider_issues_flow(context.frame, context.roster)
    store = context.store
    if changed and store is not None and context.active_ride_id is not None:
        try:
            store.save_roster(context.active_ride_id, context.roster)
        except (OSError, sqlite3.Error) as exc:
            context.frame.SetStatusText(f"Could not save riders: {exc}")
            return
        presenter = context.presenter
        clock = presenter.engine.clock if presenter is not None else None
        _switch_console_to_ride(context, context.active_ride_id, clock=clock)


_EXPORT_SUGGESTED_NAMES = {
    "export_html": "{slug}-results.html",
    "export_pdf": "{slug}-results.pdf",
    "export_poster": "{slug}-podium.pdf",
    "export_results_csv": "{slug}-standings.csv",
}

# E2: Ride ▸ Finish Ride… (and the quit flow's Finish First) publishes
# into this directory under the per-user data dir -- no save dialog,
# so a finished ride always leaves its results behind for the operator
# to find next to the database.
_EXPORTS_DIR_NAME = "exports"


def _pick_export_path(suggested_name: str) -> Path | None:
    """Open the OS save dialog for one export (E6.4.2 picker seam).

    Returns the chosen path, or None on cancel (a silent no-op, the
    same shape :func:`_handle_export_csv` uses). Tests monkeypatch
    this to write tmp files (the ``rider_editor._pick_export_path``
    precedent, test_csv_route_flows.py).

    H1: the save sheet is owned by the active window (the console or
    an open results frame) rather than ``None``, so it presents as a
    sheet over the app on macOS and cannot be hidden behind it on
    Windows. The seam keeps its single-argument shape -- the parent is
    resolved here, not threaded through every monkeypatched stub.
    """
    wx = require_wx()
    dialog = wx.FileDialog(
        wx.GetActiveWindow(),
        message="Export results",
        defaultFile=suggested_name,
        wildcard="",
        style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
    )
    try:
        if dialog.ShowModal() != wx.ID_OK:
            return None
        return Path(dialog.GetPath())
    finally:
        dialog.Destroy()


def _open_in_browser(path: Path) -> None:
    """Open *path* in the default browser (E6.4.2, injectable seam)."""
    webbrowser.open(path.as_uri())


def _ride_slug(name: str) -> str:
    """Slugify *name* for export filenames (``-`` for non-alnum)."""
    slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower())
    collapsed = re.sub(r"-+", "-", slug).strip("-")
    return collapsed or "results"


def _export_engine(context: _RouteContext) -> RideEngine | None:
    """Return the console's engine when a ride is threaded (E6.4.2)."""
    presenter = context.presenter
    return presenter.engine if presenter is not None else None


def _placed_for_export(
    context: _RouteContext,
) -> tuple[tuple[Placed, ...], tuple[Placed, ...]]:
    """Rank the snapshot's teams and solos with the stored tie-break.

    Phase 3 (team/solo results split): returns ``(teams, solo)`` --
    each kind ranked from 1 with its own DNF tail via
    ``standings.rank_by_kind``. The export writers receive both groups
    and merge them Teams-then-Solo for the shared single-``placed``
    surfaces (htmlexport/pdfexport render the two full-field sections
    by partitioning on ``result.kind``; the standings CSV carries the
    ``type`` column). With no ride threaded both groups are empty.
    """
    engine = _export_engine(context)
    if engine is None:
        return (), ()
    order = tiebreak_order_from_spellings(engine.config.tiebreak_order)
    teams, solo = rank_by_kind(engine.snapshot(), order)
    return tuple(teams), tuple(solo)


def _export_options() -> ExportOptions:
    """Return the results window's live publish options, else defaults.

    The ResultsPresenter built by the results window holds the live
    checkbox state (E6.4.1); with no window open the dataclass
    defaults (the canvas's own) apply.
    """
    wx = require_wx()
    if wx.GetApp() is None:
        return ExportOptions()
    dialog = wx.FindWindowByName(ids.RESULTS_DLG)
    presenter = getattr(dialog, "presenter", None)
    if presenter is not None:
        return cast("ExportOptions", presenter.export_options())
    return ExportOptions()


def _team_logo_srcs(roster: Roster | None) -> dict[str, str]:
    """Map every logo-carrying TEAM entry's plate to its data URI (W8).

    The HTML export's roster-entry lookup seam: htmlexport renders
    each placed row's small logo image from this map, keyed by the
    entry's plate. A card code resolves to its packaged 48x64 card
    bitmap (the same asset key the wx imagelist uses, read at the 2x
    scale so the ~48px-tall page image stays crisp). A team with no
    logo, or a code with no asset behind it, contributes no entry --
    ``None``/``{}`` both render nothing.

    Args:
        roster: The in-memory roster to read TEAM entries from
            (``None`` when no ride is threaded).

    Returns:
        A plate -> ``data:image/png;base64,...`` mapping, empty when
        no team carries a logo.
    """
    import base64  # noqa: PLC0415 -- the only consumer of base64 in this module

    from rivercrossing.ui.cards_imagelist import (  # noqa: PLC0415 -- deferred, wx-adjacent package
        SCALE_2X,
        UnknownCardCodeError,
        asset_filename,
        asset_key,
        cards_dir,
    )

    if roster is None:
        return {}
    srcs: dict[str, str] = {}
    for entry in roster.entries:
        if entry.type is not EntryType.TEAM:
            continue
        if entry.logo_card is None:
            continue
        try:
            key = asset_key(entry.logo_card)
        except UnknownCardCodeError:
            continue
        png = (cards_dir() / asset_filename(key, SCALE_2X)).read_bytes()
        encoded = base64.b64encode(png).decode("ascii")
        srcs[entry.plate] = f"data:image/png;base64,{encoded}"
    return srcs


def _write_export(  # noqa: PLR0913, PLR0917 -- (config, teams, solo, opts, target, path, team_logos): the pure writer's inputs
    config: RideConfig,
    teams: tuple[Placed, ...],
    solo: tuple[Placed, ...],
    opts: ExportOptions,
    target: str,
    path: Path,
    team_logos: dict[str, str] | None = None,
) -> None:
    """Render and write one results export to *path* (E6.4.2).

    Pure -- no wx, no context: it runs on the off-loop thread, so it
    must never touch wx (measured: a wx call from the worker thread
    bus-errors the process). The handler captures *config*/*teams*/
    *solo*/*opts*/*team_logos* on the main thread first.

    Phase 3 (team/solo results split): the two groups merge
    Teams-then-Solo into the single ``placed`` sequence each frozen
    writer takes -- the HTML/PDF full fields partition it back into the
    two sections on ``result.kind``, and the standings CSV emits the
    ``type`` column. W8: *team_logos* (the :func:`_team_logo_srcs`
    map) reaches the HTML renderer only -- the PDF report keeps no
    team logos (W8 scope note).
    """
    placed = (*teams, *solo)
    if target == "export_html":
        html = htmlexport.render(
            config, placed, opts, logo_path=config.logo_path, team_logos=team_logos
        )
        path.write_text(html, encoding="utf-8")
    elif target == "export_pdf":
        pdfexport.render(config, placed, opts, path, logo_path=config.logo_path)
    elif target == "export_poster":
        pdfexport.podium_poster(config, placed, path, logo_path=config.logo_path)
    elif target == "export_results_csv":
        csvio.export_standings(placed, path, show_times=opts.show_times)
    else:  # pragma: no cover -- the dispatch table owns the targets
        msg = f"unknown export target {target!r}"
        raise ValueError(msg)


def _run_export_offloop(  # noqa: PLR0913 -- context + the captured export inputs
    context: _RouteContext,
    target: str,
    path: Path,
    *,
    config: RideConfig,
    teams: tuple[Placed, ...],
    solo: tuple[Placed, ...],
    opts: ExportOptions,
    watermark: int,
    team_logos: dict[str, str] | None = None,
) -> None:
    """Write the export on a background thread; notice via CallAfter.

    R-02's off-loop rule: the UI never blocks on an export. A daemon
    thread renders and writes the already-captured inputs; completion
    posts the status notice and records the export's path/watermark
    through ``wx.CallAfter`` (the E5-recorded mechanism), keeping every
    wx touch on the main thread. E7.3.2: the successful export also
    advances the route context's export watermark and clears an open
    results window's stale banner -- only on success, so a failed write
    never masks a post-export correction. Failures surface on the
    status bar instead of the console.

    Part D: the path lands on the per-format field its target owns
    (:data:`_EXPORT_PATH_FIELDS`) and the same callback re-applies the
    menu state, so the matching Preview row enables the moment the
    worker lands. The finished-ride auto-export schedules both its
    HTML and PDF workers through here; each records into its own field,
    so neither can clobber the other.
    """

    def write() -> None:
        try:
            _write_export(config, teams, solo, opts, target, path, team_logos=team_logos)
        except Exception as exc:  # noqa: BLE001 -- a failed export is a notice, not a crash
            wx = require_wx()
            wx.CallAfter(context.frame.SetStatusText, f"Export failed: {exc}")
            return
        wx = require_wx()
        wx.CallAfter(context.frame.SetStatusText, f"Exported {path.name}")
        wx.CallAfter(_record_export_completion, context, target, path, watermark)
        wx.CallAfter(_clear_results_stale, watermark)

    threading.Thread(target=write, daemon=True).start()


# Part D: the per-format preview path field each export target records.
# The CSV export owns neither -- a standings .csv is not a preview.
_EXPORT_PATH_FIELDS = {
    "export_html": "html_export_path",
    "export_pdf": "pdf_export_path",
    "export_poster": "pdf_export_path",
}


def _record_export_completion(  # noqa: PLR0913, PLR0917 -- context + the completion's inputs
    context: _RouteContext, target: str, path: Path, watermark: int
) -> None:
    """Record a finished export and re-apply the menu state (Part D).

    Runs on the main thread via ``wx.CallAfter`` after a successful
    write: the path lands on the per-format field its target owns
    (nothing for the CSV target, which only advances the watermark),
    then the §15 enablement is re-applied so the matching Preview row
    enables immediately -- the lag the single-path writeback left.

    The refresh reads the LIVE engine status *here*, never a status
    captured when the export was dispatched: a ride can be reopened or
    replaced while a worker renders, and a stale FINISHED would wrongly
    re-enable the FINISHED-gated rows. With no presenter threaded the
    no-ride DRAFT state applies, matching :func:`_menu_ride_state`.
    """
    field_name = _EXPORT_PATH_FIELDS.get(target)
    if field_name is not None:
        setattr(context, field_name, path)
    context.export_watermark = watermark
    presenter = context.presenter
    status = RideStatus.DRAFT if presenter is None else presenter.engine.state
    _apply_menu_state(context, status)


def _clear_results_stale(watermark: int) -> None:
    """Clear an open results window's stale banner after a fresh export.

    E7.3.2's export-completion half: the export handler advances the
    route context's watermark above; this keeps an open results
    window's presenter in sync (its own watermark field plus
    ``set_stale(False)``) so the banner clears without waiting for the
    next refresh. No results window open: nothing to do -- the next
    Results open reads the advanced context watermark and evaluates
    clean.
    """
    wx = require_wx()
    dialog = wx.FindWindowByName(ids.RESULTS_DLG)
    presenter = getattr(dialog, "presenter", None)
    if presenter is not None:
        presenter.mark_exported(watermark)


def _handle_export_command(context: _RouteContext, target: str) -> None:
    """Run one Results ▸ export row (E6.4.2): pick, write off-loop.

    A cancelled picker is a silent no-op like the roster export. The
    wx-touching reads (engine/config/teams/solo/options) happen here
    on the main thread; the off-loop thread only renders and writes.
    E7.3.2: the export watermark -- the engine event count right now,
    the state the rendered file captures -- is read alongside the
    snapshot and stored on success, so a correction after this instant
    makes the results window render the stale banner.
    """
    engine = _export_engine(context)
    if engine is None:
        context.frame.SetStatusText("No ride to export")
        return
    name = _EXPORT_SUGGESTED_NAMES[target].format(slug=_ride_slug(engine.config.name))
    path = _pick_export_path(name)
    if path is None:
        return
    config = engine.config
    teams, solo = _placed_for_export(context)
    opts = _export_options()
    watermark = len(engine.events)
    # W8: the roster's team logos are captured on the main thread like
    # every other export input (the off-loop writer never touches the
    # live context).
    team_logos = _team_logo_srcs(context.roster)
    _run_export_offloop(
        context,
        target,
        path,
        config=config,
        teams=teams,
        solo=solo,
        opts=opts,
        watermark=watermark,
        team_logos=team_logos,
    )


def _finished_exports_dir() -> Path:
    """Return the per-user directory Finish publishes results into (E2).

    ``user_data_dir("RiverCrossing")/exports`` -- the same per-user
    data directory the rides database lives in, so the operator finds
    the finished ride's results without a save dialog.
    """
    return Path(user_data_dir("RiverCrossing")) / _EXPORTS_DIR_NAME


def _auto_export_finished_results(context: _RouteContext, engine: RideEngine) -> None:
    """Publish the finished ride's HTML and PDF results (E2, R-02).

    Ride ▸ Finish Ride… used to leave the operator with no results
    file until they ran the Results menu by hand. The two exports now
    run the same off-loop writer the menu rows do -- the wx-touching
    inputs (config, standings, publish options, watermark, team logos)
    are captured here on the main thread, the render/write happens on
    the worker's thread -- into the deterministic per-user directory
    instead of through the save dialog.

    Part D: each worker's completion records its own format's path and
    re-applies the menu state (:func:`_record_export_completion`), so
    both Preview rows enable off the finished ride's files; the export
    watermark is the event count the rendered files captured.
    """
    directory = _finished_exports_dir()
    directory.mkdir(parents=True, exist_ok=True)
    slug = _ride_slug(engine.config.name)
    config = engine.config
    teams, solo = _placed_for_export(context)
    opts = _export_options()
    watermark = len(engine.events)
    team_logos = _team_logo_srcs(context.roster)
    html_path = directory / _EXPORT_SUGGESTED_NAMES["export_html"].format(slug=slug)
    pdf_path = directory / _EXPORT_SUGGESTED_NAMES["export_pdf"].format(slug=slug)
    for target, path in (("export_html", html_path), ("export_pdf", pdf_path)):
        _run_export_offloop(
            context,
            target,
            path,
            config=config,
            teams=teams,
            solo=solo,
            opts=opts,
            watermark=watermark,
            team_logos=team_logos,
        )


def _handle_preview_browser(context: _RouteContext, path: Path | None) -> None:
    """Results ▸ Preview …: open *path* in the browser (E6.4.2).

    Shared by the two per-format preview rows (Part D): each hands its
    own recorded export path, and a missing one posts the same notice
    the single Preview row always did.
    """
    if path is None:
        context.frame.SetStatusText("No export yet — generate one first")
        return
    _open_in_browser(path)
    context.frame.SetStatusText(f"Opened {path.name}")


def _handle_preview_html_browser(context: _RouteContext) -> None:
    """Results ▸ Preview HTML in Browser: open the last HTML export."""
    _handle_preview_browser(context, context.html_export_path)


def _handle_preview_pdf_browser(context: _RouteContext) -> None:
    """Results ▸ Preview PDF in Browser: open the last PDF export."""
    _handle_preview_browser(context, context.pdf_export_path)


def _active_top_level_window(wx: Any) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return the app's currently active top-level window, if any.

    The keyboard-focus path first: the focused control's top-level
    parent is the dialog or frame the operator is actually in. When
    nothing has focus -- measured: ``FindFocus()`` reads ``None`` for
    a terminal-launched, unbundled Python that is never the frontmost
    macOS app (tests/functional/test_dialog_behavior.py), even with a
    modal dialog open -- the topmost modal dialog stands in (the
    modal IS the active window while it runs), then the app's top
    window.
    """
    focused = wx.Window.FindFocus()
    if focused is not None:
        # logic-coverage-exempt: T-3 -- the keyboard-focus path is
        # measured unobservable in the VM harness (FindFocus reads
        # None for a terminal-launched app that is never frontmost,
        # test_dialog_behavior.py), so its two arms are exercised only
        # on a real frontmost desktop; the modal/GetTopWindow paths
        # below carry the functional coverage.
        top = focused.GetTopLevelParent()
        if top is not None:
            return top
    for top in reversed(wx.GetTopLevelWindows()):
        # IsModal is a wx.Dialog method -- the top-level scan also
        # yields frames (measured: 'Frame' object has no attribute
        # 'IsModal' in the VM), so only dialogs are tested.
        if isinstance(top, wx.Dialog) and top.IsModal():
            return top
    app = wx.GetApp()
    if app is not None:
        top = app.GetTopWindow()
        if top is not None:
            return top
    # logic-coverage-exempt: T-3 -- no route handler runs without a
    # live app and its top window; None only narrows the type for the
    # caller's default anchor.
    return None


def _handle_open_user_guide(context: _RouteContext) -> None:
    """Help ▸ User Guide / F1: open the guide at the active anchor.

    E8.2.2: resolves the currently active top-level window
    (:func:`_active_top_level_window`), maps its XRC name to its
    user-guide chapter (:func:`~rivercrossing.ui.help.anchor_for`),
    opens the guide at that anchor in the OS-default browser, and
    posts the opened URL to the status bar. With no mapped window
    active the guide opens at its default opening chapter.
    """
    wx = require_wx()
    top = _active_top_level_window(wx)
    window_name = top.GetName() if top is not None else None
    anchor = help_module.anchor_for(window_name)
    url = help_module.open_guide(anchor)
    context.frame.SetStatusText(f"Opened user guide: {url}")


def _handle_backup_database(context: _RouteContext) -> None:
    """File ▸ Back Up Database… / Settings' ``backup_now_btn``: R-54.

    ux-polish's manual-backup command: with a live store open, writes
    one timestamped backup through :meth:`Store.backup_now` and posts
    the written path on the status bar; a failure posts the error
    instead of crashing. The settings dialog's own button runs this
    same handler through the ``on_backup_now`` seam ``_decorate``
    wires. With no store open there is no database to back up -- the
    E5.4.2 in-memory constructions get the same no-store guard notice
    the duplicate route uses.
    """
    store = context.store
    if store is None:
        label = commands.route_for_id("mi_backup_now").label
        context.frame.SetStatusText(f"{label} — no store is open")
        return
    try:
        path = store.backup_now()
    except Exception as exc:  # noqa: BLE001 -- a failed backup is a status notice, not a crash
        context.frame.SetStatusText(f"Backup failed: {exc}")
        return
    context.frame.SetStatusText(f"Backed up database to {path}")


def _handle_finish_route(context: _RouteContext) -> None:
    """Ride ▸ Finish Ride…: confirm, then run the finish flow (E4.4.4).

    H2: the confirm is the native
    :func:`~rivercrossing.ui.std_dialogs.show_danger` dialog (the
    retired ``finish_confirm_dlg``'s copy), naming the ride and
    defaulting to Cancel because finishing locks entry; only a
    confirmed ``wx.ID_OK`` fires the live console presenter's
    ``on_finish``, which consults ``FINISH_GATE`` -- the hook that runs
    the evaluator's real self-test suite -- and calls
    ``engine.finish()``. Mirrors the
    ``undo_last_crossing`` route's presenter-first shape: with no live
    presenter threaded (route-level tests), a notice stands in for the
    action after a confirmed dialog.

    E2: a finish that actually reached FINISHED auto-publishes the
    ride's HTML and PDF results (:func:`_auto_export_finished_results`)
    -- the quit flow's "Finish First" reaches the same handler, so
    both finish paths leave the results behind.
    """
    from rivercrossing.ui import std_dialogs  # noqa: PLC0415 -- deferred, see app.py
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see app.py

    wx = require_wx()
    # E7.2.2: REOPENED's single primary action is "Finish again" (spec
    # §3 design 8c) -- the same confirm, re-labelled. The engine is
    # read BEFORE the confirm so the label reflects the state the
    # dialog opens in, not the post-finish one.
    presenter_for_label = context.presenter
    title, ok_label = "Finish Ride?", "Finish ride"
    if presenter_for_label is not None and presenter_for_label.engine.state is RideStatus.REOPENED:
        title, ok_label = dialogs.finish_again_labels()
    result = std_dialogs.show_danger(
        context.frame,
        title,
        dialogs.finish_ride_message(),
        ok_label,
        "Cancel",
    )
    if result != int(wx.ID_OK):
        return
    presenter = context.presenter
    if presenter is None:
        label = commands.route_for_id("mi_finish_ride").label
        context.frame.SetStatusText(f"{label} — not yet implemented")
        return
    presenter.on_finish()
    # E2: only a finish that really locked the ride publishes results;
    # a gate refusal or a DRAFT refusal leaves the state unchanged.
    engine = presenter.engine
    if engine.state is RideStatus.FINISHED:
        _auto_export_finished_results(context, engine)


# H2: the title and affirmative button label for each native ride
# confirm, keyed by the COMMAND target _RIDE_CONFIRM_HANDLERS
# dispatches on (the two rows whose question names a ride).
_RIDE_CONFIRM_PROMPTS: dict[str, tuple[str, str]] = {
    "duplicate_ride": ("Duplicate Ride", "Duplicate"),
    "reopen_ride": ("Reopen Ride", "Reopen"),
}


def _open_ride_confirm(context: _RouteContext, target: str, message: str) -> bool:
    """Show one native ride confirm; return whether it was confirmed.

    The shared shape of the two mock-first confirms (File ▸ Duplicate
    Ride…, Ride ▸ Reopen Ride): both ask a non-destructive question
    through :func:`~rivercrossing.ui.std_dialogs.show_prompt` -- the
    information icon with OK as the default button, so Enter is safe
    (spec §13). *message* names the ride (a blank one is a failed
    assertion, never cosmetic -- UX-DESKTOP §4); the title and
    affirmative button label come from :data:`_RIDE_CONFIRM_PROMPTS`.

    Args:
        context: The route context whose frame owns the prompt.
        target: The row's own COMMAND target (the prompt-table key).
        message: The ride-naming question the prompt shows.

    Returns:
        ``True`` when the operator confirmed; ``False`` on Cancel.
    """
    from rivercrossing.ui import std_dialogs  # noqa: PLC0415 -- deferred, see app.py

    title, ok_label = _RIDE_CONFIRM_PROMPTS[target]
    result = std_dialogs.show_prompt(context.frame, title, message, ok_label, "Cancel")
    return result == int(require_wx().ID_OK)


def _handle_duplicate_ride_route(context: _RouteContext) -> None:
    """File ▸ Duplicate Ride…: confirm, then duplicate the open ride.

    E5.4.1's row, H2's native prompt: the route asks the
    non-destructive :func:`_open_ride_confirm` question naming the ride
    currently open in the console, and on a confirmed Duplicate calls
    ``Store.duplicate_ride`` on the context's ``active_ride_id`` (the
    ride the library Open or the resume flow loaded). R-15: the copy
    is setup + roster, no timing data. Without a store-backed open
    ride there is nothing to duplicate, and the confirm's OK posts a
    notice instead of inventing a ride. A refused duplicate (a locked
    or unwritable database) surfaces as a status notice: an unguarded
    raise from this wx menu handler is swallowed with zero signal
    (the measured note ``docs/EPIC3-SESSION-SUMMARY.md`` records).
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
        "duplicate_ride",
        dialogs.duplicate_ride_message(ride_name),
    )
    if not confirmed:
        return
    try:
        copy_id = store.duplicate_ride(ride_id)
    except (OSError, sqlite3.Error) as exc:
        context.frame.SetStatusText(f"Could not duplicate ride: {exc}")
        return
    copy_name = next(
        (ride.name for ride in store.rides() if ride.id == copy_id),
        "a new ride",
    )
    context.frame.SetStatusText(f"Duplicated as {copy_name}")


def _handle_reopen_ride_route(context: _RouteContext) -> None:
    """Ride ▸ Reopen Ride: confirm, then reopen the finished ride.

    E5.4.1's row, H2's native prompt: the route asks the
    non-destructive :func:`_open_ride_confirm` question naming the ride
    (a FINISHED ride is the only one the row enables, commands.py), and
    on a confirmed Reopen fires the live console presenter's
    ``on_reopen`` -- ``engine.reopen()`` moves the console to REOPENED,
    the corrections-only state (spec §3, R-36). With no live presenter
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
        "reopen_ride",
        dialogs.reopen_ride_message(ride_name),
    )
    if not confirmed:
        return
    if presenter is None:
        label = commands.route_for_id("mi_reopen_ride").label
        context.frame.SetStatusText(f"{label} — not yet implemented")
        return
    presenter.on_reopen()


# ============================ ux-polish: the last two dead Ride rows
#
# Ride ▸ Stop Ride… and Ride ▸ Set Start Time… were the final
# dead-control audit's two remaining dead menu items: their DIALOG
# targets opened through _open_target's generic path, so the dialog
# appeared and a confirmed OK did nothing. Stop Ride… now reaches the
# live presenter's native stop-confirm flow through the "stop_ride"
# COMMAND target (W5 -- see _make_route_handler), and set-start runs
# the set_start_dlg form through views.corrections and applies
# engine.set_start_time (spec §3 3d's gun-missed correction). The
# XRC stop_confirm_dlg retired with W5's native confirm; set-start
# keeps its XRC form and dispatches by target through
# _LIVE_FLOW_HANDLERS below.


def _handle_set_start_time_route(context: _RouteContext) -> None:
    """Ride ▸ Set Start Time…: back-date the start (spec §3, 3d).

    ux-polish wires the dead row: the route opens ``set_start_dlg``
    through :func:`~rivercrossing.ui.views.corrections.
    run_set_start_time` -- prefilled with the ride's planned start,
    the value the operator is correcting -- and on a confirmed OK
    applies ``engine.set_start_time(at)`` through :func:`_apply_
    correction`'s shape: ``actual_start`` moves and lap-1 times
    recompute; an engine refusal (any state but RUNNING -- a REOPENED
    ride the §15 enablement still lights, say) posts
    "Correction refused: …"; a success refreshes the console
    (``presenter.tick``) and posts a notice. With no live engine
    (route-level tests) the same "no ride open" notice the correction
    routes use stands in.
    """
    engine = _correction_engine(context)
    if engine is None:
        # logic-coverage-exempt: T-3 -- no live engine is unreachable
        # in every live construction (the menu enablement only lights
        # the row when a console ride is threaded), mirroring the
        # correction routes' own engine-less guards.
        context.frame.SetStatusText("Set Start Time… — no ride open")
        return
    from rivercrossing.ui.views import corrections  # noqa: PLC0415 -- deferred, see app.py

    at = corrections.run_set_start_time(
        context.resource,
        frame=context.frame,
        prefill=engine.config.planned_start,
    )
    if at is None:
        return
    _apply_correction(
        context,
        lambda: engine.set_start_time(at),
        "Start time set",
    )


# ========================================== E7.2.1 correction routes
#
# The six Cards/Riders correction rows (Add Crossing at Time, Edit
# Crossing, Reassign Plate, Deal Manual Card, Mark DNF, Void Card)
# previously opened their dialogs through _open_target's generic
# path -- plain XRC with no engine wiring. Each handler below mirrors
# the _open_ride_confirm shape: run the dialog through
# views.corrections' shared runner (which prefills, writes the named
# labels and enforces the non-empty reason), then apply the confirmed
# engine command, refresh the console (tick re-applies the menu
# binder through the feed seam) and post a status notice. The
# dialogs carry no plate field of their own (dialogs.xrc section C),
# so the entry-detail context (_RouteContext.detail_plate) is the
# entry they target; without one the handler posts a notice instead
# of inventing a target.

_CORRECTION_ERRORS = (IllegalStateError, UnknownPlateError, ValueError, ShoeClosedError)


def _correction_engine(context: _RouteContext) -> RideEngine | None:
    """Return the console's engine when a ride is threaded."""
    presenter = context.presenter
    return presenter.engine if presenter is not None else None


def _now_time_text() -> str:
    """Return local wall time as ``HH:MM:SS`` for a prefill."""
    return datetime.now(UTC).astimezone().strftime("%H:%M:%S")


def _crossing_time_text(crossed_at: datetime) -> str:
    """Render a crossing instant as local 24-hour ``HH:MM:SS``.

    spec §13: "Times: stored UTC, displayed local 24-hour." An aware
    UTC datetime converts to local; a naive one (tests) is shown as
    stored -- the same rule ``data_source._feed_time`` applies.
    """
    local = crossed_at.astimezone() if crossed_at.tzinfo is not None else crossed_at
    return local.strftime("%H:%M:%S")


def _entry_label(context: _RouteContext, plate: str) -> str:
    """Return a confirm label for *plate* (``plate · name``)."""
    roster = context.roster
    entry = roster.resolve_plate(plate) if roster is not None else None
    name = entry.display_name if entry is not None else plate
    return f"{plate} · {name}"


def _latest_seq_for_plate(engine: RideEngine, plate: str) -> int | None:
    """Return *plate*'s highest crossing seq, or None."""
    seqs = [crossing.seq for crossing in engine.crossings if crossing.entry_id == plate]
    return max(seqs) if seqs else None


def _latest_crossing_for_plate(engine: RideEngine, plate: str) -> Any:  # noqa: ANN401 -- a ride.Crossing, not imported at runtime
    """Return *plate*'s latest recorded crossing, or None."""
    for crossing in reversed(engine.crossings):
        if crossing.entry_id == plate:
            return crossing
    return None


def _latest_credited_card(engine: RideEngine, plate: str) -> Card | None:
    """Return *plate*'s latest credited card, or None.

    Reads the snapshot's credited sequence (never the held queue):
    Void Card targets a dealt, credited card only -- a held card stays
    the review surface's domain.
    """
    for result in engine.snapshot():
        if result.plate == plate and result.cards:
            return result.cards[-1]
    return None


def _apply_correction(
    context: _RouteContext,
    action: Callable[[], object],
    ok_notice: str,
) -> None:
    """Run one confirmed correction; surface refusals as notices.

    The engine already refuses empty reasons, wrong states, unknown
    plates and (for deals) a closed shoe; each refusal posts a status
    notice instead of crashing. On success the console presenter
    refreshes (tick re-renders the feed/counters, whose show_feed
    seam re-applies the menu binder) and the status bar confirms.
    """
    try:
        action()
    except _CORRECTION_ERRORS as exc:
        context.frame.SetStatusText(f"Correction refused: {exc}")
        return
    presenter = context.presenter
    if presenter is not None:
        presenter.tick()
    context.frame.SetStatusText(ok_notice)


def _handle_add_crossing_at_route(context: _RouteContext) -> None:
    """Cards ▸ Add Crossing at Time…: edit_crossing_dlg in add mode."""
    engine = _correction_engine(context)
    if engine is None:
        context.frame.SetStatusText("Add Crossing at Time… — no ride open")
        return
    from rivercrossing.ui.views import (  # noqa: PLC0415 -- deferred, see module docstring
        corrections,
    )

    edit = corrections.run_edit_crossing(
        context.resource,
        frame=context.frame,
        adding=True,
        plate=context.detail_plate or "",
        time=_now_time_text(),
        base_date=engine.config.event_date,
    )
    if edit is None or edit.crossed_at is None:
        return
    crossed_at = edit.crossed_at
    _apply_correction(
        context,
        lambda: engine.add_crossing_at(edit.entry_id, crossed_at, edit.reason),
        "Crossing added",
    )


def _handle_edit_crossing_route(context: _RouteContext) -> None:
    """Cards ▸ Edit Crossing…: edit_crossing_dlg in edit mode.

    The dialog carries no crossing selector (dialogs.xrc section C),
    so the seq the operator means is the entry's latest crossing when
    the dialog was not opened from a selected lap (the menu flow) --
    the "fix the most recent time error" reading of the row's
    "≥1 crossing" enablement.
    """
    engine = _correction_engine(context)
    if engine is None:
        context.frame.SetStatusText("Edit Crossing… — no ride open")
        return
    from rivercrossing.ui.views import (  # noqa: PLC0415 -- deferred, see module docstring
        corrections,
    )

    edit = corrections.run_edit_crossing(
        context.resource,
        frame=context.frame,
        adding=False,
        plate=context.detail_plate or "",
        time=_now_time_text(),
        base_date=engine.config.event_date,
    )
    if edit is None:
        return
    seq = edit.seq
    if seq is None:
        seq = _latest_seq_for_plate(engine, edit.entry_id)
        if seq is None:
            context.frame.SetStatusText(f"Edit Crossing… — no crossings for {edit.entry_id}")
            return
    if edit.void:
        _apply_correction(
            context,
            lambda: engine.void_crossing(edit.entry_id, seq, edit.reason),
            "Crossing voided",
        )
        return
    if edit.crossed_at is None:
        # logic-coverage-exempt: T-3 -- the runner always sets
        # crossed_at on a non-void commit (views/corrections.py); the
        # guard only narrows the optional type for mypy.
        return
    crossed_at = edit.crossed_at
    _apply_correction(
        context,
        lambda: engine.edit_crossing(edit.entry_id, seq, crossed_at, edit.reason),
        "Crossing edited",
    )


def _handle_reassign_route(context: _RouteContext) -> None:
    """Cards ▸ Reassign Plate…: move the current entry's latest lap."""
    engine = _correction_engine(context)
    if engine is None:
        context.frame.SetStatusText("Reassign Plate… — no ride open")
        return
    plate = context.detail_plate
    if plate is None:
        context.frame.SetStatusText("Reassign Plate… — open an entry first")
        return
    crossing = _latest_crossing_for_plate(engine, plate)
    if crossing is None:
        context.frame.SetStatusText(f"Reassign Plate… — no crossing for {plate}")
        return
    seq = next(index for index, item in enumerate(engine.crossings, start=1) if item is crossing)
    from rivercrossing.ui.views import (  # noqa: PLC0415 -- deferred, see module docstring
        corrections,
        dialogs,
    )

    request = corrections.run_reassign(
        context.resource,
        frame=context.frame,
        crossing_label=dialogs.reassign_message(_crossing_time_text(crossing.crossed_at), plate),
    )
    if request is None:
        return
    _apply_correction(
        context,
        lambda: engine.reassign_crossing(seq, request.new_plate, request.reason),
        "Crossing reassigned",
    )


def _handle_deal_manual_route(context: _RouteContext) -> None:
    """Cards ▸ Deal Manual Card…: manual_deal_dlg, then deal_manual."""
    engine = _correction_engine(context)
    if engine is None:
        context.frame.SetStatusText("Deal Manual Card… — no ride open")
        return
    from rivercrossing.ui.views import (  # noqa: PLC0415 -- deferred, see module docstring
        corrections,
    )

    deal = corrections.run_manual_deal(
        context.resource,
        frame=context.frame,
        plate=context.detail_plate or "",
    )
    if deal is None:
        return
    _apply_correction(
        context,
        lambda: engine.deal_manual(deal.plate, deal.reason),
        "Card dealt",
    )


def _handle_mark_dnf_route(context: _RouteContext) -> None:
    """Riders ▸ Mark DNF…: dnf_confirm_dlg naming the current entry."""
    engine = _correction_engine(context)
    if engine is None:
        context.frame.SetStatusText("Mark DNF… — no ride open")
        return
    plate = context.detail_plate
    if plate is None:
        context.frame.SetStatusText("Mark DNF… — open an entry first")
        return
    from rivercrossing.ui.views import (  # noqa: PLC0415 -- deferred, see module docstring
        corrections,
    )

    dnf = corrections.run_dnf(
        context.resource,
        frame=context.frame,
        entry_id=plate,
        entry=_entry_label(context, plate),
    )
    if dnf is None:
        return
    _apply_correction(
        context,
        lambda: engine.mark_dnf(dnf.entry_id, dnf.reason),
        "Entry marked DNF",
    )


def _handle_void_card_route(context: _RouteContext) -> None:
    """Cards ▸ Void Card…: void the current entry's latest card."""
    engine = _correction_engine(context)
    if engine is None:
        context.frame.SetStatusText("Void Card… — no ride open")
        return
    plate = context.detail_plate
    if plate is None:
        context.frame.SetStatusText("Void Card… — open an entry first")
        return
    card = _latest_credited_card(engine, plate)
    if card is None:
        context.frame.SetStatusText(f"Void Card… — no dealt card for {plate}")
        return
    from rivercrossing.ui.views import (  # noqa: PLC0415 -- deferred, see module docstring
        corrections,
    )

    void = corrections.run_void_card(
        context.resource,
        frame=context.frame,
        entry_id=plate,
        card=card.code(),
        entry=_entry_label(context, plate),
    )
    if void is None:
        return
    _apply_correction(
        context,
        lambda: engine.void_card(void.entry_id, Card.parse(void.card), void.reason),
        "Card voided",
    )


_CORRECTION_HANDLERS: dict[str, Callable[[_RouteContext], None]] = {
    ids.MI_ADD_CROSSING_AT: _handle_add_crossing_at_route,
    ids.MI_EDIT_CROSSING: _handle_edit_crossing_route,
    ids.MI_REASSIGN_PLATE: _handle_reassign_route,
    ids.MI_DEAL_MANUAL: _handle_deal_manual_route,
    ids.MI_MARK_DNF: _handle_mark_dnf_route,
    ids.MI_VOID_CARD: _handle_void_card_route,
}


def _open_target(context: _RouteContext, route: commands.MenuRoute) -> None:
    """Open *route*'s target window, or notice its absence (D1).

    ``LoadDialog`` returns ``None`` rather than raise when
    *route.target* names no XRC resource at all (harness.py's own
    measured note) -- no §15 route is un-authored anymore (E5.4.1 and
    E7 authored Duplicate Ride, Reopen Ride, Void Card), but the
    branch stays as the safety net for any future route whose target
    is not yet authored, with no change needed here: a route never
    silently does nothing, it always says so on the status bar instead
    -- and Part D also records the miss in the launch's NDJSON log, so
    this failure class (a menu row clicking through to nothing) is
    diagnosable from the log alone.
    """
    window = context.resource.LoadDialog(None, route.target)
    if window is None:
        log = _log(context)
        if log is not None:
            log.marker(f"{route.label}: no window authored for target {route.target!r}")
        context.frame.SetStatusText(f"{route.label} — no window authored yet")
        return

    try:
        # E8.1.4: every window opened later inherits the current zoom.
        # Applied BEFORE decoration so a view's value-setting runs last:
        # the recursive SetFont walk resets a wxChoice's selection to -1
        # on this pin (measured in the VM on settings_dlg's zoom_choice
        # before W13 removed that control), and the view's
        # show_settings re-sets every value afterwards. Base fonts come
        # from the fresh XRC load, scaled once (never compounded).
        zoom.apply_to(window)
        view = _decorate(context, window, route)
        _apply_dialog_defaults(window, route)
    except Exception:
        # Fault A: any post-load failure must close the just-loaded
        # window before re-raising. The construction calls above run
        # before the dialog path's own try/finally below, and a raise
        # there (find_control's 25-retry LookupError under load) used
        # to leak the dialog fully alive, rerun-masked, until the reap
        # pin caught it. A successfully shown frame stays open by
        # design, so this guard only ever runs on the exception path.
        if not window.IsBeingDeleted():
            window.Destroy()
        raise

    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see module docstring

    try:
        dialogs.run_dialog(window, opener=context.frame)
    finally:
        if not window.IsBeingDeleted():
            window.Destroy()

    # W7/W8: the Rider Editor and the Teams Editor are the two
    # dialogs whose roster edits must persist when they close -- the
    # console tab path (_open_rider_editor_for) and the menu routes
    # both persist through the same helpers once their modal ends.
    if route.target == ids.RIDER_EDITOR_DLG and view is not None:
        _persist_rider_editor_changes(context, view)
    elif route.target == ids.TEAM_EDITOR_DLG and view is not None:
        _persist_team_editor_changes(context, view)
    elif route.target == ids.SIMULATION_DLG and view is not None:
        _persist_simulator_changes(context, view)


def _persist_rider_editor_changes(context: _RouteContext, view: Any) -> None:  # noqa: ANN401
    """Persist the roster after a rider-editor modal ends (W7).

    Both editor-open paths (the Rider Editor menu route above and the
    console Riders-tab path :func:`_open_rider_editor_for`) call this
    after their modal has ended. With a store-backed ride open and
    any committed change this session (the presenter's own
    ``roster_changed``), the in-memory roster is written back so the
    edits survive a relaunch. A refused save (a locked or unwritable
    database) surfaces as a status notice -- the guard idiom
    :func:`_handle_import_csv` uses, for the same wx-swallowed-raise
    reason (the measured note ``docs/EPIC3-SESSION-SUMMARY.md``
    records): worst case the operator is told nothing happened while
    the edit silently stayed unpersisted.

    Args:
        context: The route context whose store/roster to act on.
        view: The closed ``RiderEditor`` (or a presenter-shaped
            stand-in) whose ``presenter.roster_changed`` says whether
            this session committed anything.
    """
    if not view.presenter.roster_changed:
        return
    store = context.store
    if store is None or context.active_ride_id is None:
        return
    try:
        store.save_roster(context.active_ride_id, context.roster)
    except (OSError, sqlite3.Error) as exc:
        context.frame.SetStatusText(f"Could not save riders: {exc}")


def _persist_team_editor_changes(context: _RouteContext, view: Any) -> None:  # noqa: ANN401
    """Persist the roster after a team-editor modal ends (W8).

    The Teams Editor menu route calls this after its modal has ended,
    the mirror of :func:`_persist_rider_editor_changes`: with a
    store-backed ride open and any committed change this session (the
    presenter's own ``roster_changed``), the in-memory roster is
    written back so team records -- including W8's empty teams and
    their logos -- survive a relaunch. A refused save (a locked or
    unwritable database) surfaces as a status notice -- the same
    guard idiom the rider editor uses, for the same
    wx-swallowed-raise reason (the measured note
    ``docs/EPIC3-SESSION-SUMMARY.md`` records).

    Args:
        context: The route context whose store/roster to act on.
        view: The closed ``TeamEditor`` (or a presenter-shaped
            stand-in) whose ``presenter.roster_changed`` says whether
            this session committed anything.
    """
    if not view.presenter.roster_changed:
        return
    store = context.store
    if store is None or context.active_ride_id is None:
        return
    try:
        store.save_roster(context.active_ride_id, context.roster)
    except (OSError, sqlite3.Error) as exc:
        context.frame.SetStatusText(f"Could not save teams: {exc}")


def _persist_simulator_changes(context: _RouteContext, view: Any) -> None:  # noqa: ANN401
    """Persist the roster after the simulator dialog closes.

    The mirror of :func:`_persist_rider_editor_changes`: the simulator
    generates placeholder riders and teams into the in-memory roster,
    so with a store-backed ride open and any generated change (the
    presenter's own ``roster_changed``), that roster is written back
    so a crashed or abandoned simulated field survives a relaunch. A
    refused save (a locked or unwritable database) surfaces as a status
    notice -- the same guard idiom the rider editor uses, for the same
    wx-swallowed-raise reason.

    Args:
        context: The route context whose store/roster to act on.
        view: The closed ``SimulatorDialog`` (or a presenter-shaped
            stand-in) whose ``presenter.roster_changed`` says whether
            this session generated anything.
    """
    if not view.presenter.roster_changed:
        return
    store = context.store
    if store is None or context.active_ride_id is None:
        return
    try:
        store.save_roster(context.active_ride_id, context.roster)
    except (OSError, sqlite3.Error) as exc:
        context.frame.SetStatusText(f"Could not save riders: {exc}")


def _open_rider_editor_for(context: _RouteContext, plate: str) -> None:
    """Open ``rider_editor_dlg`` over the shared roster at *plate*.

    ux-polish's console Riders-tab activation flow: wired as
    :meth:`MainFrame.set_on_open_rider`'s callback
    (:func:`_wire_rider_open_seam`), so a double-click (or Enter) on
    a rider row opens the editor on the live roster -- the same
    decoration :func:`_decorate` performs for the Rider Editor route
    -- and selects the rider's row, landing the operator on that
    rider's form instead of the add form
    (:meth:`RiderEditor.select_rider_by_plate`). W7 closes this
    path's two gaps against the menu route: it now applies the
    recorded dialog defaults (``_apply_dialog_defaults`` -- the menu
    route always had them) and persists the roster when the editor
    closes with changes (:func:`_persist_rider_editor_changes`). The
    dialog path mirrors :func:`_open_target`'s own: zoom applied
    before decoration, shown through ``dialogs.run_dialog``,
    destroyed in a ``finally`` (Fault A: a decoration raise must not
    leak it).
    """
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see module docstring
    from rivercrossing.ui.views.rider_editor import RiderEditor  # noqa: PLC0415 -- deferred

    window = context.resource.LoadDialog(None, ids.RIDER_EDITOR_DLG)
    if window is None:
        context.frame.SetStatusText("Rider Editor — no window authored yet")
        return
    view = None
    try:
        zoom.apply_to(window)
        view = RiderEditor(window, roster=context.roster)
        _apply_dialog_defaults(window, commands.route_for_id("mi_rider_editor"))
        view.select_rider_by_plate(plate)
        dialogs.run_dialog(window, opener=context.frame)
    finally:
        if not window.IsBeingDeleted():
            window.Destroy()

    if view is not None:
        _persist_rider_editor_changes(context, view)


def _wire_rider_open_seam(context: _RouteContext) -> None:
    """Wire the console Riders tab's activation to the rider editor.

    ux-polish: :meth:`MainFrame.set_on_open_rider` is the view's pure
    seam (it only fires ``callback(plate)``); this is the app's half
    that opens the editor preselected at the activated rider's plate.
    A console-less route-level context has nothing to wire.
    """
    console_view = context.console_view
    if console_view is None:
        return
    console_view.set_on_open_rider(lambda plate: _open_rider_editor_for(context, plate))


def _open_entry_detail_for(context: _RouteContext, plate: str) -> None:
    """Open ``entry_detail_dlg`` at *plate* (the W11 F2a flagged seam).

    The flagged-tab activation flow: wired as
    :meth:`MainFrame.set_on_open_flagged`'s callback
    (:func:`_wire_flagged_open_seam`), so a double-click (or Enter) on
    a flagged row opens the LIVE entry detail at the flagged entry's
    plate -- the same decoration :func:`_open_target` performs for the
    ``mi_entry_detail`` route (:func:`_open_entry_detail_dialog`,
    which also records ``context.detail_plate`` so the correction menu
    routes act on the flagged entry). The dialog path mirrors
    :func:`_open_rider_editor_for`'s own: zoom applied before
    decoration, shown through ``dialogs.run_dialog``, destroyed in a
    ``finally`` (Fault A: a decoration raise must not leak it).
    """
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see module docstring

    window = context.resource.LoadDialog(None, ids.ENTRY_DETAIL_DLG)
    if window is None:
        context.frame.SetStatusText("Entry Detail — no window authored yet")
        return
    try:
        zoom.apply_to(window)
        _open_entry_detail_dialog(context, window, plate)
        _apply_dialog_defaults(window, commands.route_for_id("mi_entry_detail"))
        dialogs.run_dialog(window, opener=context.frame)
    finally:
        if not window.IsBeingDeleted():
            window.Destroy()


def _wire_flagged_open_seam(context: _RouteContext) -> None:
    """Wire the console flagged tab's activation to entry detail.

    W11 F2a: :meth:`MainFrame.set_on_open_flagged` is the view's pure
    seam (it only fires ``callback(plate)``); this is the app's half
    that opens the live entry detail at the activated flagged row's
    plate, recording it as the current entry. A console-less
    route-level context has nothing to wire.
    """
    console_view = context.console_view
    if console_view is None:
        return
    console_view.set_on_open_flagged(lambda plate: _open_entry_detail_for(context, plate))


def _crossing_for_feed_row(engine: RideEngine, row: int) -> Crossing | None:
    """Return the crossing the console feed's *row* shows, or ``None``.

    The crossing rows are ``reversed(engine.crossings[-FEED_CAP:])``
    (``EngineDataSource.feed_rows``: newest first, R-32's 30-row cap),
    so row 0 is the newest crossing and the row index unwinds that same
    window. The cap drops the *oldest* crossings while every recorded
    crossing keeps its ride-wide ordinal, so an index outside the window
    -- a stale activation after the feed shrank -- names no crossing.

    K's misses interleave with the crossings, so this indexes the
    *crossing* rows only; :func:`_feed_row_target` is the feed-aware
    resolver the console's activation seam actually uses.
    """
    window = engine.crossings[-FEED_CAP:]
    if not 0 <= row < len(window):
        return None
    return window[len(window) - 1 - row]


def _feed_row_target(
    source: DataSource, engine: RideEngine, row: int
) -> Crossing | PendingMiss | None:
    """Return the target the console feed's *row* shows, or ``None``.

    K's interleaved feed (``EngineDataSource.feed_rows``) means a row
    index no longer names a fixed crossing: the row's own ``missed``
    flag decides. A miss row resolves to its :class:`PendingMiss` by
    ``miss_seq`` against the engine's pending misses; a crossing row
    resolves through :func:`_crossing_for_feed_row`, counting only the
    crossing rows before it (the misses it skips do not shift the
    30-row cap arithmetic). A row outside the feed -- a stale
    activation after the feed shrank -- names nothing.
    """
    rows = source.feed_rows()
    if not 0 <= row < len(rows):
        return None
    feed_row = rows[row]
    if feed_row.missed:
        return next(
            (miss for miss in engine.pending_misses() if miss.miss_seq == feed_row.miss_seq),
            None,
        )
    crossing_rows_before = sum(1 for earlier in rows[:row] if not earlier.missed)
    return _crossing_for_feed_row(engine, crossing_rows_before)


def _open_crossing_detail_for(context: _RouteContext, row: int) -> None:
    """Open ``crossing_detail_dlg`` on the feed row *row* (J2/K2).

    The console crossings feed's activation flow: wired as
    :meth:`MainFrame.set_on_open_crossing`'s callback
    (:func:`_wire_crossing_open_seam`), so a double-click (or Enter)
    on a feed row opens the row's detail -- the same decoration idiom
    :func:`_open_rider_editor_for` uses (zoom applied before
    decoration, shown through ``dialogs.run_dialog``, destroyed in a
    ``finally`` so a decoration raise cannot leak it).

    Differences: the console view hands over a *row index*, resolved
    here by :func:`_feed_row_target` to either the live ``Crossing``
    or the pending miss the row stands for. A crossing opens
    :class:`CrossingDetailView`, a miss opens
    :class:`MissDetailView`, so the same window serves the "wrong plate
    on the line" and "score the miss" corrections. The dialog commits
    straight through the console's engine, whose event sink already
    persists it, so nothing is read back afterwards.

    A console with no ride behind it (W1's no-ride bootstrap) has no
    presenter and no crossings, so there is nothing to show; the
    startup empty feed renders no row a double-click could reach.
    """
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see module docstring
    from rivercrossing.ui.views.crossing_detail import (  # noqa: PLC0415 -- deferred
        CrossingDetailView,
        MissDetailView,
    )

    presenter = context.presenter
    if presenter is None:
        return
    engine = presenter.engine
    target = _feed_row_target(presenter.source, engine, row)
    if target is None:
        return
    window = context.resource.LoadDialog(None, ids.CROSSING_DETAIL_DLG)
    if window is None:
        context.frame.SetStatusText("Crossing Detail — no window authored yet")
        return
    try:
        zoom.apply_to(window)
        if isinstance(target, PendingMiss):
            MissDetailView(window, miss=target, engine=engine)
        else:
            CrossingDetailView(window, crossing=target, roster=context.roster, engine=engine)
        dialogs.run_dialog(window, opener=context.frame)
    finally:
        if not window.IsBeingDeleted():
            window.Destroy()


def _wire_crossing_open_seam(context: _RouteContext) -> None:
    """Wire the console crossings feed's activation to Crossing Detail.

    J2: :meth:`MainFrame.set_on_open_crossing` is the view's pure seam
    (it only fires ``callback(row)``); this is the app's half that
    resolves the activated row to its live ``Crossing`` and opens the
    detail dialog on it. A console-less route-level context has nothing
    to wire.
    """
    console_view = context.console_view
    if console_view is None:
        return
    console_view.set_on_open_crossing(lambda row: _open_crossing_detail_for(context, row))


def _wire_finished_banner_actions(context: _RouteContext) -> None:
    """Wire the FINISHED banner's two buttons to the menu's flows.

    W11 F3: the console's ``finished_infobar`` (shown by
    :meth:`MainFrame.set_state` on FINISHED) carries a Reopen button
    and a View results button; the view is passive, so this is the
    app's half that points them at the same flows the menu rows run:
    ``on_reopen`` fires the same ``_handle_reopen_ride_route``
    ``mi_reopen_ride`` runs (confirm included), ``on_view_results``
    opens the same results frame the ``mi_standings`` row opens. A
    console-less route-level context has nothing to wire.
    """
    console_view = context.console_view
    if console_view is None:
        return
    console_view.set_finished_actions(
        on_reopen=lambda: _handle_reopen_ride_route(context),
        on_view_results=lambda: _open_target(context, commands.route_for_id("mi_standings")),
    )


def _confirm_quit(context: _RouteContext) -> quit_flow.QuitOutcome:
    """Run the quit-confirm dialog for the ride's current status.

    Loads :func:`quit_flow.dialog_for_status`'s target from
    *context*'s already-loaded resource -- ``exit_running_dlg`` for a
    RUNNING ride -- writes the running variant's ride-naming copy into
    its ``message_lbl`` (E5.2.3), binds ``finish_first_btn`` to
    ``EndModal`` (A1), shows it through
    :func:`~rivercrossing.ui.views.dialogs.run_dialog` -- the one seam
    every dialog in this codebase shows through -- and maps the
    result.

    H2: every other status has no ride to protect, so
    ``dialog_for_status`` returns ``None`` and the question is asked
    through the native
    :func:`~rivercrossing.ui.std_dialogs.show_confirm` -- Cancel is
    its default button, so a reflex Enter still cannot quit.

    The live ride status and name come from the console's own
    presenter engine (E5.4.2: the ``data_source`` seam is gone; the
    quit flow asks the live console, never a display-data source).
    W1: the no-ride bootstrap threads no presenter, so the
    DRAFT/"The ride" fallbacks are the real empty-console path -- with
    no ride open there is nothing to finish and no ride name to name.

    A confirmed ``QuitOutcome.QUIT`` stamps the open session's
    ``closed_at`` through :func:`_stamp_closed_session` (E5.2.1: a
    clean quit, not a crash, at the next launch); a
    ``QuitOutcome.FINISH_FIRST``
    hands off to the E4.4.4 finish flow -- :func:`_handle_finish_route`
    -- which shows the native finish confirm and, on OK, runs the live
    console presenter's ``on_finish`` (E5.2.3 replaces the old stub
    notice).
    """
    wx = require_wx()

    presenter = context.presenter
    # W1: the bootstrap's no-ride console threads no presenter, so the
    # DRAFT/"The ride" fallbacks below are the live empty-console path
    # (quitting from the no-ride console), not a presenter-less stub.
    status = presenter.engine.state if presenter is not None else RideStatus.DRAFT
    dialog_name = quit_flow.dialog_for_status(status)
    if dialog_name is None:
        return _confirm_quit_native(context)

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
        _stamp_closed_session(context)
    elif outcome is quit_flow.QuitOutcome.FINISH_FIRST:
        _handle_finish_route(context)
    return outcome


def _confirm_quit_native(context: _RouteContext) -> quit_flow.QuitOutcome:
    """Ask the no-ride quit confirm natively (R-51, H2).

    The non-RUNNING half of :func:`_confirm_quit`: there is no ride to
    protect, so the question and its frozen copy live in
    :mod:`rivercrossing.ui.quit_flow` and the answer comes back from
    the native :func:`~rivercrossing.ui.std_dialogs.show_confirm`
    (Cancel default). A confirmed OK stamps the open session's
    ``closed_at`` exactly as the XRC path does.

    Returns:
        ``QUIT`` when the operator confirmed; ``STAY`` otherwise.
    """
    from rivercrossing.ui import std_dialogs  # noqa: PLC0415 -- deferred, see module docstring

    result = std_dialogs.show_confirm(
        context.frame,
        quit_flow.EXIT_CONFIRM_TITLE,
        quit_flow.EXIT_CONFIRM_MESSAGE,
        quit_flow.EXIT_CONFIRM_OK_LABEL,
        quit_flow.EXIT_CONFIRM_CANCEL_LABEL,
    )
    outcome = quit_flow.outcome_for(result, ok_id=require_wx().ID_OK)
    if outcome is quit_flow.QuitOutcome.QUIT:
        _stamp_closed_session(context)
    return outcome


def _stamp_closed_session(context: _RouteContext) -> None:
    """Stamp the open session's ``closed_at`` (R-52); surface failures.

    A clean quit writes ``closed_at`` so the next launch reads a clean
    quit, not a crash. With no store open there is nothing to stamp.
    A refused write (a locked or unwritable database) surfaces as a
    status notice: this runs inside the quit flow's wx handlers, and
    an unguarded raise there is swallowed with zero signal (the
    measured note ``docs/EPIC3-SESSION-SUMMARY.md`` records) -- the
    next launch would then wrongly report a crash.
    """
    store = context.store
    if store is None:
        return
    try:
        store.close_session()
    except (OSError, sqlite3.Error) as exc:
        context.frame.SetStatusText(f"Could not close session: {exc}")


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
# Ride both open a native confirm then act on OK (like the finish
# route), so they dispatch through one table in _make_route_handler
# rather than two near-identical branches. H2: the keys are the rows'
# own COMMAND targets (their XRC dialog names retired).
_RIDE_CONFIRM_HANDLERS: dict[str, Callable[[_RouteContext], None]] = {
    "duplicate_ride": _handle_duplicate_ride_route,
    "reopen_ride": _handle_reopen_ride_route,
}

# ux-polish: the remaining dead Ride ▸ row dispatches by target the
# same way the E5.4.1 confirms above do: mi_set_start_time -> the
# set_start_dlg form + engine back-date (spec §3, 3d). Ride ▸
# Stop Ride… needs no entry here -- W5 gave it the "stop_ride"
# COMMAND target, dispatched straight from _make_route_handler to the
# live presenter's native stop-confirm flow.
_LIVE_FLOW_HANDLERS: dict[str, Callable[[_RouteContext], None]] = {
    ids.SET_START_DLG: _handle_set_start_time_route,
}


def _export_action(target: str) -> Callable[[_RouteContext], None]:
    """Return the handler for one export target (E6.4.2 dispatch)."""
    return lambda context: _handle_export_command(context, target)


# E6.4.2: the route targets with real actions, dispatched by
# route.target ahead of the generic "not yet implemented" stub.
_TARGET_ACTIONS: dict[str, Callable[[_RouteContext], None]] = {
    target: _export_action(target) for target in _EXPORT_SUGGESTED_NAMES
}
_TARGET_ACTIONS["preview_html_browser"] = _handle_preview_html_browser
_TARGET_ACTIONS["preview_pdf_browser"] = _handle_preview_pdf_browser
_TARGET_ACTIONS["backup_database"] = _handle_backup_database
_TARGET_ACTIONS[ids.CSV_PREVIEW_DLG] = _handle_import_csv
_TARGET_ACTIONS[ids.RIDER_ISSUES_DLG] = _handle_check_rider_issues
_TARGET_ACTIONS["open_user_guide"] = _handle_open_user_guide


def _correction_route_handler(
    context: _RouteContext, route: commands.MenuRoute
) -> Callable[[Any], None] | None:
    """Return the correction-route handler for *route*, if it is one.

    E7.2.1's six correction rows dispatch by their own item id, not by
    target -- ``mi_add_crossing_at`` and ``mi_edit_crossing`` share
    one target (``EDIT_CROSSING_DLG``) with different modes. Returns
    ``None`` for every other row, so ``_make_route_handler`` falls
    through to the generic open.
    """
    if route.ids and route.ids[0] in _CORRECTION_HANDLERS:
        return lambda _event: _CORRECTION_HANDLERS[route.ids[0]](context)
    return None


def _make_route_handler(  # noqa: PLR0911, PLR0912, C901 -- one early-return per route special case; each is a real action
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
    generic stub. ``start_ride`` (ux-polish) fires the live
    presenter's ``on_start`` the same way -- the engine's own start
    gate (empty roster, incomplete setup) refuses through
    ``StartBlockedError`` and the presenter opens the blocked-start
    issues dialog, one row per reason (Phase 5); with no presenter
    the fallback posts "Start Ride — no ride open".
    ``stop_ride`` (W5) fires the live presenter's native stop-confirm
    flow (``on_stop_requested``) the same way -- a riderless roster
    gets a native warning from the flow itself; with no presenter the
    fallback posts the generic stub. ``focus_review_panel``
    (ux-polish) focuses the console's
    review-panel "Needs Review" tab through the wired console view,
    with the generic stub standing in for a console-less route-level
    context. ``finish_ride`` (E4.4.4; H2's COMMAND target -- the XRC
    confirm retired for the native danger dialog) opens its confirm
    through :func:`_handle_finish_route`, which runs
    ``presenter.on_finish`` on a confirmed OK -- the same
    presenter-first shape ``undo_last_crossing`` uses -- instead of
    :func:`_open_target`'s generic open-and-return; ``duplicate_ride``
    and ``reopen_ride`` (H2) dispatch through
    :data:`_RIDE_CONFIRM_HANDLERS` the same target-keyed way.
    ``set_start_dlg``
    (ux-polish) dispatches through
    :data:`_LIVE_FLOW_HANDLERS` the same target-keyed way: Set Start
    Time… runs the
    ``set_start_dlg`` picker form and applies ``engine.set_start_time``
    (:func:`_handle_set_start_time_route`). ``csv_preview_dlg``
    (E3.4) is the one
    ``DIALOG`` target that needs a picker run before it opens, ahead
    of :func:`_open_target`'s generic path. ``open_user_guide``
    (E8.2.2) opens the bundled guide deep-linked to the active
    window's anchor (:func:`_handle_open_user_guide`), registered in
    :data:`_TARGET_ACTIONS` with the export targets and
    ``backup_database`` (ux-polish, :func:`_handle_backup_database`).
    Every other
    ``COMMAND`` row has no window to open and no ride engine yet to
    run its real action (EPIC 4+); it posts a status-bar notice
    instead of silently doing nothing. Every other ``WINDOW``/
    ``DIALOG`` row always attempts to open its target through
    :func:`_open_target`, which posts the same kind of notice if that
    target has no frozen window yet.
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
    if route.target == "start_ride":
        # A distinct local (not ``presenter``): the undo branch above
        # narrows its own binding into a lambda, and a later
        # reassignment of the same name would void that narrowing for
        # mypy (closures capture the variable, not the value).
        start_presenter = context.presenter
        if start_presenter is not None:
            return lambda _event: start_presenter.on_start()
        return lambda _event: context.frame.SetStatusText("Start Ride — no ride open")
    if route.target == "stop_ride":
        # W5: mi_stop_ride reaches the live presenter's native
        # stop-confirm flow (on_stop_requested) -- the identical
        # handler the console Stop button fires, so the menu row and
        # the button cannot drift. Same distinct-local reason as the
        # start branch above.
        stop_presenter = context.presenter
        if stop_presenter is not None:
            return lambda _event: stop_presenter.on_stop_requested()
        return lambda _event: context.frame.SetStatusText(f"{route.label} — not yet implemented")
    if route.target == "clear_ride":
        # D3: confirm, then reset the open ride to a fresh DRAFT. The
        # row dispatches through its own handler (like the finish and
        # ride-confirm rows), never the generic COMMAND notice.
        return lambda _event: _handle_clear_ride_route(context)
    if route.target == "finish_ride":
        # H2: the Finish Ride… row's XRC confirm retired for the native
        # show_danger; the target-keyed branch keeps the confirm ->
        # on_finish flow ahead of the generic COMMAND notice.
        return lambda _event: _handle_finish_route(context)
    if route.target == "focus_review_panel":
        console_view = context.console_view
        if console_view is not None:
            return lambda _event: console_view.focus_review_panel()
        return lambda _event: context.frame.SetStatusText(f"{route.label} — not yet implemented")
    # E5.4.1/H2's two native ride confirms both need a real handler
    # ahead of the generic COMMAND notice (their confirm -> action
    # shape, like the finish route), so they share one dispatch table
    # instead of two branches.
    ride_confirm_handler = _RIDE_CONFIRM_HANDLERS.get(route.target)
    if ride_confirm_handler is not None:
        return lambda _event: ride_confirm_handler(context)
    # ux-polish: the remaining dead Ride ▸ row (Set Start Time…)
    # dispatches by target through the same table shape -- its DIALOG
    # target opened generically before, and a confirmed OK did
    # nothing. (Stop Ride… dispatched this way too until W5 retired
    # its dialog for the "stop_ride" COMMAND branch above.)
    live_flow_handler = _LIVE_FLOW_HANDLERS.get(route.target)
    if live_flow_handler is not None:
        return lambda _event: live_flow_handler(context)
    # E7.2.1: the six correction rows dispatch by their own item id,
    # not by target -- mi_add_crossing_at and mi_edit_crossing share
    # one target (EDIT_CROSSING_DLG) with different modes.
    correction_handler = _correction_route_handler(context, route)
    if correction_handler is not None:
        return correction_handler
    target_action = _TARGET_ACTIONS.get(route.target)
    if target_action is not None:
        return lambda _event: target_action(context)
    if route.kind is commands.TargetKind.COMMAND:
        return lambda _event: context.frame.SetStatusText(f"{route.label} — not yet implemented")
    return lambda _event: _open_target(context, route)


def _menu_logging_handler(  # noqa: PLR0913, PLR0917 -- (log, id, route, handler): the F3 wrapper's inputs
    log: Logging,
    item_id: int,
    route: commands.MenuRoute,
    handler: Callable[[Any], None],
) -> Callable[[Any], None]:
    """Wrap *handler* so the selection is logged before it runs (F3).

    F4's menu half: the event filter deliberately ignores ``EVT_MENU``
    (menus are bound routes, not free-floating controls), so every
    route's own wrapper records the §15 selection -- by the id the
    item was bound under, which an accelerator-triggered event carries
    too -- then dispatches unchanged.
    """

    def _fire(event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        log.menu(item_id, route.menu, route.label)
        handler(event)

    return _fire


def _bind_routes(context: _RouteContext) -> None:
    """Bind every ``commands.ROUTE_TABLE`` id to a live handler.

    Iterates the table itself, never a hand-copied id list, so a
    route added later is bound automatically and cannot be missed
    (R-73).

    F3: with a structured log threaded, each bound handler is wrapped
    so the operator's menu selection is recorded before it dispatches;
    without one the bare handler is bound, so a log-less construction
    behaves exactly as before.
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    log = _log(context)
    for route in commands.ROUTE_TABLE:
        handler = _make_route_handler(context, route)
        for item_id in route.ids:
            real_id = wx.xrc.XRCID(item_id)
            bound = handler if log is None else _menu_logging_handler(log, real_id, route, handler)
            context.frame.Bind(wx.EVT_MENU, bound, id=real_id)


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


def _launch_choice(session: PreviousSession, *, ride_open: bool) -> str:
    """Return the launch prompt a store-backed start needs (W3).

    The pure decision behind :func:`_run_launch_flow`, kept out of the
    wx shell so the flow's choice is unit-testable headless. The
    session-row semantics are unchanged: :func:`resume_flow.
    resume_dialog_for` still owns which previous sessions warrant
    ``resume_dlg`` (R-52) -- including a REOPENED ride's continue --
    and this only adds the No Ride Open choice for a start that
    resumes nothing.

    Args:
        session: The previous session's resume record.
        ride_open: Whether a store ride is already open in the
            console.

    Returns:
        ``"resume"`` when *session* warrants ``resume_dlg``;
        ``"no_ride"`` when nothing is open and nothing resumes (show
        the No Ride Open alert); ``"none"`` otherwise -- a ride is
        already open, so the console already answers the launch.
    """
    if resume_flow.resume_dialog_for(session) is not None:
        return "resume"
    return "none" if ride_open else "no_ride"


def _run_resume_dialog(
    context: _RouteContext, store: Store, previous: PreviousSession
) -> str | None:
    """Show ``resume_dlg`` over the visible frame; return the choice.

    The R-52 dialog machinery that used to run inside
    ``build_main_window`` -- before the frame was shown, where a modal
    that cannot be presented blocks the launch invisibly (the "app
    never starts again" regression) -- now runs from
    :func:`_run_launch_flow`, with the frame already visible. Loads
    ``resume_dlg``, writes the quit-vs-crash copy
    (:func:`~rivercrossing.ui.resume_flow.resume_message`) into its
    ``message_lbl`` -- a blank label is a failed assertion, never a
    cosmetic one -- binds ``continue_btn``/``library_btn`` to end the
    modal (spec §15b's code-side ``SetAffirmativeId`` contract, and
    E1.5.3's Escape->library decision), and maps the outcome.

    Args:
        context: The route context whose frame/resource host the
            dialog.
        store: The live Store the ride name is read from.
        previous: The previous session record the copy words
            ``resume_dlg`` from (its ``ride_id``/``ended_at`` are
            guaranteed by :func:`_run_launch_flow`'s guard).

    Returns:
        ``"continue"`` when the operator chose Continue, ``"library"``
        for the non-committal choice (``library_btn``'s click or
        Escape), ``None`` when the dialog could not be loaded (a
        status notice was posted instead).
    """
    wx = require_wx()
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see module docstring

    ride_id = previous.ride_id
    ended_at = previous.ended_at
    if ride_id is None or ended_at is None:
        # logic-coverage-exempt: T-3 -- resume_flow.resume_dialog_for
        # only warrants a dialog for a session that carried a running
        # ride, and such a session always has an end instant (closed_at
        # for a quit, heartbeat/opened_at for a crash); this guard
        # only narrows types for mypy.
        raise RuntimeError("resume dialog warranted without a ride or end time")

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
    finally:
        if not dialog.IsBeingDeleted():
            dialog.Destroy()

    if continue_id is not None and result == continue_id:
        return "continue"
    # Open library instead of resuming (also Escape's target): any
    # non-Continue result is the same non-committal choice.
    return "library"


def _defer_open_library(context: _RouteContext) -> None:
    """Open the ride library through ``wx.CallAfter`` (E1.5.3).

    ``resume_dlg``'s non-committal outcome (``library_btn``'s click or
    Escape) opens ``ride_library_dlg`` on the running event loop: a
    modal opened synchronously right after the resume modal's own
    unwind -- still inside the launch flow -- is not dismissible by
    the functional harness (measured; the child hit its bound in a
    hung ride_library_dlg). The ``CallAfter`` fires on the running
    event loop (``main()``'s ``MainLoop``), right at startup. The
    console underneath stays the visible placeholder; the library's
    Open replaces it.
    """
    wx = require_wx()
    wx.CallAfter(lambda: _open_target(context, commands.route_for_id("mi_open_library")))


def _resume_continue(  # noqa: PLR0913, PLR0917 -- (context, store, ride_id, clock): the resume-continue action needs the host, the store, the ride and the clock seam
    context: _RouteContext,
    store: Store,
    ride_id: int,
    clock: Callable[[], datetime] | None,
) -> None:
    """Continue on ``resume_dlg``: mark the ride active, switch to it.

    Continue keeps the E5.2.2 contract -- :meth:`Store.set_active_ride`
    first, so a later clean quit stamps ``closed_at`` on a session that
    names the ride (R-52) -- then reuses the library-Open console
    switch (:func:`_switch_console_to_ride`, with the launch clock
    threaded into ``Store.load_engine``), the same store-load applied
    at launch instead of mid-session.

    A replay failure -- :meth:`Store.load_engine` raising a
    :class:`~rivercrossing.ride.RideEngineError` when the audit stream
    no longer matches the ride's stored roster (e.g. the "no crossing
    with entry_id ... for ..." raise), or a
    :class:`~rivercrossing.store.StoreError` -- must not take the
    launch down: the message is wrapped so it names the ride as well
    as the offending event, the status bar and an error alert surface
    it, the session marker is cleared (so the next launch does not
    offer a ride this store cannot load), and the visible placeholder
    console stays up.
    """
    store.set_active_ride(ride_id)
    try:
        _switch_console_to_ride(context, ride_id, clock=clock)
    except (RideEngineError, StoreError) as exc:
        # W3 replay-error clarity: wrap at this catch site, never
        # inside Store.load_engine, so the message names the ride and
        # the plate/event the replay error already names.
        ride_name = next(
            (ride.name for ride in store.rides() if ride.id == ride_id),
            "The ride",
        )
        wrapped = RideEngineError(f"cannot resume ride {ride_name}: {exc}")
        context.frame.SetStatusText(f"Could not resume ride: {wrapped}")
        from rivercrossing.ui import std_dialogs  # noqa: PLC0415 -- deferred, see module docstring

        std_dialogs.show_error(context.frame, "Cannot Resume Ride", str(wrapped))
        store.clear_active_ride()


_NO_RIDE_TITLE = "No Ride Open"
_NO_RIDE_MESSAGE = "No ride is loaded. Create a new one or load an existing one."


def _show_no_ride_info(parent: Any) -> None:  # noqa: ANN401 -- wx Window; wx ships no stubs
    """Show the launch's No Ride Open alert over *parent* (W3).

    Replaces ``no_ride_dlg``, the XRC window this code stopped loading
    in W3 and W15 removed from dialogs.xrc: a store-backed launch that
    resumes no ride and opens no ride gets this one information alert
    instead of an unexplained empty console, then nothing -- the
    console is visible and the operator uses the menus. The retired
    window's Create/Open-library choice is the File menus' own job.
    """
    from rivercrossing.ui import std_dialogs  # noqa: PLC0415 -- deferred, see module docstring

    std_dialogs.show_info(parent, _NO_RIDE_TITLE, _NO_RIDE_MESSAGE)


def _run_launch_flow(
    context: _RouteContext, store: Store | None, clock: Callable[[], datetime] | None = None
) -> None:
    """Run the post-launch flow on a store-backed start (W3).

    Called by :func:`main` (and the functional suite's own
    main-equivalent helpers) once the frame is visible;
    ``build_main_window`` itself shows no modal. The flow decides from
    the store's previous-session record (E5.2.2's reading; no
    audit-based decisions):

    - a session that left a ride running shows ``resume_dlg`` (R-52)
      -- Continue reuses the library-Open console switch, Open
      library defers ``ride_library_dlg``;
    - otherwise, with no ride open, it shows the No Ride Open alert
      (the console stays up; the operator uses the menus);
    - otherwise it does nothing -- a ride is already open.

    A store-less build returns immediately: there is no session row to
    read and no launch prompt (E5.4.2's in-memory behavior).

    Args:
        context: The assembled route context (``build_main_window``
            hands it to the app as ``launch_context``).
        store: The live Store, or ``None`` for a store-less build.
        clock: Wall-clock source for a resumed engine, threaded to
            :func:`_switch_console_to_ride`; ``None`` uses the
            engine's own default (``datetime.now``).
    """
    if store is None:
        return
    previous = store.previous_session()
    choice = _launch_choice(previous, ride_open=context.active_ride_id is not None)
    log = _log(context)
    if log is not None:
        log.launch(
            previous_state=previous.state.value,
            previous_ride_id=previous.ride_id,
            choice=choice,
        )
    if choice == "no_ride":
        _show_no_ride_info(context.frame)
        return
    if choice != "resume":
        return
    ride_id = previous.ride_id
    if ride_id is None or previous.ended_at is None:
        # logic-coverage-exempt: T-3 -- resume_flow.resume_dialog_for
        # only warrants a dialog for a session that carried a running
        # ride, and such a session always has an end instant (closed_at
        # for a quit, heartbeat/opened_at for a crash); this guard only
        # narrows types for mypy.
        raise RuntimeError("resume dialog warranted without a ride or end time")
    outcome = _run_resume_dialog(context, store, previous)
    if outcome == "continue":
        _resume_continue(context, store, ride_id, clock)
    elif outcome == "library":
        _defer_open_library(context)


def build_main_window(
    app: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    store: Store | None = None,
    settings_path: Path | None = None,
) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Build and wire ``main_frame``, complete but not yet shown.

    Loads every packaged XRC resource, builds the console
    (:class:`~rivercrossing.ui.views.MainFrame`) and attaches its
    menubar via ``LoadMenuBar`` (never ``FindWindowByName`` -- the XRC
    menubar handler drops the name, spec.md §15b), ticks the loaded
    hide-times check item and zoom radio (the radio defaults the XRC
    cannot declare: ``<checked>`` is a no-op on ``wxITEM_RADIO``),
    applies the accelerator table, binds every §15 route and the theme
    controller's own ``EVT_SYS_COLOUR_CHANGED`` re-apply, and wires
    the two process-quit paths ``EVT_CLOSE``/``wxEVT_QUERY_END_SESSION``
    (Phase 8, P8-D1/P8-D2/P8-D4). The bootstrap roster is empty (no
    store-backed ride is open), and both the console (W1:
    :data:`_EMPTY_SOURCE` + :meth:`MainFrame.show_no_ride`) and the
    E6/E7 windows read the empty state.

    E8.1.1 loads the per-user settings file at startup and applies
    what already has live paths: the persisted appearance through
    :class:`~rivercrossing.ui.theme.ThemeController` (constructed with
    the loaded mode; W13 removed the View-menu theme trio, so there is
    no menu radio to tick), the sound
    mute through :func:`~rivercrossing.ui.sound.set_muted`, zoom
    through :func:`~rivercrossing.ui.zoom.set_percent` (with the menu
    radio synced, E8.1.4), and the saved splitter sash / frame
    geometry through :class:`MainFrame`'s layout seams. Hide-times
    (E8.1.3) applies with the first ride attach, since the no-ride
    bootstrap has no presenter to call ``on_hide_times`` on (its menu
    check item is still synced here). The settings file path and
    current :class:`AppSettings` are kept on :class:`_RouteContext`,
    and the layout save callback persists sash/geometry changes back
    to the file.

    ux-polish adds one post-wiring step: the console Riders tab's
    double-click seam is wired to the rider editor
    (:func:`_wire_rider_open_seam`); W11 adds the flagged tab's
    activation seam the same way (:func:`_wire_flagged_open_seam` ->
    live entry detail at the flagged plate) and the FINISHED banner's
    two buttons (:func:`_wire_finished_banner_actions` -> the reopen
    and results flows); J2 adds the crossings feed's own activation
    seam (:func:`_wire_crossing_open_seam` -> the read-only Crossing
    Detail dialog on the activated row's live crossing). W3 retired
    every launch modal from this function -- ``resume_dlg``, the
    no-ride prompt and the
    R-44 self-test all ran here, before the frame was shown, where a
    modal that cannot be presented blocks the launch invisibly (the
    "app never starts again" regression). The console always opens on
    the empty bootstrap engine, and the post-Show launch flow
    (:func:`_run_launch_flow`, called by :func:`main`) shows the
    resume dialog or the No Ride Open alert over the visible frame;
    Continue swaps the console through :func:`_switch_console_to_ride`.

    Split out of :func:`main` so a test can drive the whole
    construction path without ever entering ``MainLoop``, which
    blocks.

    Args:
        app: The live app :func:`main`/:func:`build_app` already
            constructed. An App must exist before any wx object is
            built; this function also hands it *frame*, for
            ``RiverCrossingApp.MacReopenApp`` to restore later, binds
            its ``wxEVT_QUERY_END_SESSION``, and keeps the assembled
            :class:`_RouteContext` as ``launch_context`` -- the handle
            :func:`main`'s post-Show launch flow and the functional
            helpers use.
        store: The live :class:`~rivercrossing.store.Store`, when the
            caller opened one (E5.2.1). Threaded through
            :class:`_RouteContext` so a confirmed quit stamps the
            open session's ``closed_at`` (:func:`_confirm_quit`), and
            read by :func:`_run_launch_flow` so a running ride at the
            previous exit opens ``resume_dlg`` (E5.2.2, R-52);
            ``None`` until the store-backed bootstrap (E5.4.1).
        settings_path: The per-user settings file to load at startup
            and write layout saves back to; ``None`` uses
            :func:`~rivercrossing.ui.presenters.settings.default_path`.
            The functional suite injects a temp path so no test ever
            touches the real user config dir.

    Returns:
        The loaded, fully wired ``main_frame``, not yet shown.
    """
    from rivercrossing.ui.views import MainFrame  # noqa: PLC0415 -- deferred, see module docstring
    from rivercrossing.ui.views.main_frame import (  # noqa: PLC0415 -- deferred, see module docstring
        REQUIRED_CONTROL_CLASSES,
        REQUIRED_CONTROLS,
    )

    # F1: the second ordered bootstrap step -- main() already built and
    # installed the log; a construction without one (the functional
    # helpers build the window directly) logs nothing.
    log = getattr(app, "log", None)
    if log is not None:
        log.marker("building the main window")
    # logic-coverage-exempt: T-3 -- this guard's live arm runs only
    # under main() against a real wx.App (build_main_window loads XRC
    # and constructs the console), so the unit suite can exercise only
    # the log-less arm; tests/functional/test_app_bootstrap.py's probe
    # is what covers the log-present arm.

    # E8.1.1: load the per-user settings once, at startup; every apply
    # below reads the same loaded object, and the layout save callback
    # writes back through the same path.
    settings_path = settings_path if settings_path is not None else settings_store.default_path()
    loaded_settings = settings_store.load_settings(settings_path)
    loaded_mode = theme.ThemeMode(loaded_settings.appearance)

    resource = _load_xrc_resources()

    # Fault-B (degraded-XRC-load) guard: verify every control
    # MainFrame.__init__ needs actually resolved to its concrete class,
    # and rebuild once from a fresh private XmlResource if the singleton
    # skipped a subtree (or a stale wrong-typed wrapper poisoned the
    # lookup); the frame and resource used downstream are the verified
    # ones.
    frame = _load_frame_verified(resource, REQUIRED_CONTROLS, REQUIRED_CONTROL_CLASSES)
    menubar = resource.LoadMenuBar(None, ids.MAIN_MENUBAR)
    frame.SetMenuBar(menubar)
    _check_loaded_hide_times(menubar, hide=loaded_settings.hide_times)
    _check_loaded_zoom_radio(menubar, loaded_settings.zoom_percent)

    # E5.4.2: no store-backed ride is open at bootstrap, so the roster
    # is empty (rider_editor_dlg shows the empty state; the library
    # Open / resume flow replaces it with the store's roster). The
    # mixed/pooled mode keeps the E3.2 default shape; W8 adds the
    # fixed team-logo seed so Pick card works before any ride opens.
    roster = Roster(
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        max_team_size=_SEEDED_MAX_TEAM_SIZE,
        team_logo_seed=_SEEDED_TEAM_LOGO_SEED,
    )
    theme_controller = theme.ThemeController(app, mode=loaded_mode)
    context = _RouteContext(
        frame=frame,
        resource=resource,
        roster=roster,
        app=app,
        theme_controller=theme_controller,
        store=store,
        settings=loaded_settings,
        settings_path=settings_path,
        # Presenter is threaded below with dataclasses.replace, once the
        # live console exists; the resume flow and route binding only
        # need the pieces already set here.
    )

    # W3/W1: no launch modal runs here, and no ride is built either.
    # With no store-backed ride open the console holds a true empty
    # state (no engine, no presenter); the post-Show launch flow
    # (main()'s _run_launch_flow) shows resume_dlg / the No Ride Open
    # alert over the visible frame, and Continue -- or the library's
    # Open -- swaps the console onto the store ride afterwards, so a
    # replay against a drifted roster can never take the build down.
    # _build_console_engine stays for Clear Ride's no-store fallback
    # (and the unit tests); the bootstrap no longer calls it.

    def _save_layout(sash: int | None, geometry: tuple[int, int, int, int] | None) -> None:
        """Persist the console's layout and keep the context current."""
        _save_layout_settings(context, sash, geometry)

    _console = MainFrame(
        frame,
        data_source=_EMPTY_SOURCE,
        initial_sash=loaded_settings.splitter_sash,
        initial_geometry=loaded_settings.window_geometry,
        on_layout_changed=_save_layout,
    )
    _console.show_no_ride()

    # E8.1.1-E8.1.4: apply the persisted settings that have live
    # paths -- appearance (the ThemeController, constructed with the
    # loaded mode), sound and zoom. hide-times applies when a ride
    # attaches (a new presenter renders it), not at the no-ride
    # bootstrap where there is no presenter to call.
    sound.set_muted(muted=not loaded_settings.sound_on)
    zoom.set_percent(loaded_settings.zoom_percent)

    _apply_accelerators(frame, menubar)
    # theme_controller is kept alive by _RouteContext, threaded through
    # every route handler. console_view is threaded the same way so
    # E5.4.1's library Open can swap the console's presenter; the
    # presenter starts as None (W1) and _swap_console_onto sets it on
    # the first ride attach. active_ride_id records the store ride the
    # launch flow's Continue opened, if any (File ▸ Duplicate Ride…
    # reads it).
    context = replace(
        context,
        console_view=_console,
    )
    _bind_routes(context)
    _wire_rider_open_seam(context)
    _wire_flagged_open_seam(context)
    _wire_crossing_open_seam(context)
    _wire_finished_banner_actions(context)
    _bind_process_quit_paths(context)
    _bind_theme(context)
    # E7.2.1: the live menu-enablement binder (E1.4.2's missing half).
    # set_on_ride_changed fires on every ride-state change (the
    # console's own seam) and on every feed re-render, so the §15
    # "Enabled when" cells hold in the app -- the initial call below
    # applies them to the no-ride state (DRAFT, ride_open=False).
    _console.set_on_ride_changed(lambda status: _apply_menu_state(context, status))
    _apply_menu_state(context, RideStatus.DRAFT)

    # W3: the post-Show launch flow needs the assembled context, and
    # the app object is the one handle main() and the functional
    # helpers share (the same reason main_frame lives on it).
    app.launch_context = context
    return frame


def _log(context: _RouteContext) -> Logging | None:
    """Return the launch's structured log, when the app carries one.

    ``None`` for a construction whose app predates the F1 log (a
    route-level test's ``app=None``, or the functional helpers that
    build a window without going through :func:`main`), so every
    logging call site is a silent no-op instead of a crash.
    """
    return cast("Logging | None", getattr(context.app, "log", None))


def _log_file_name(log: Logging) -> str:
    """Return the name of the NDJSON file *log* writes to.

    The uncaught-exception notice names the file its traceback landed
    in, so the operator can point a support session at the right one.
    """
    handler = log._handler  # noqa: SLF001 -- Logging exposes no path accessor
    return Path(handler.baseFilename).name


def _make_event_filter(log: Logging) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Build the whitelisted control-event filter the app installs (F4).

    The control-event half of F4: menu selections are logged by
    :func:`_bind_routes`' own wrapper, so this filter covers the five
    control events the app never binds itself -- button, checkbox,
    radio button, choice and text -- and ignores everything else,
    paint/mouse/timer/idle traffic included. It always answers
    ``Event_Skip`` so a filter can never swallow an event, and a
    non-verbose log short-circuits before any control is read.

    Args:
        log: The live log the whitelisted events are written to.

    Returns:
        A ``wx.EventFilter`` ready for ``wx.App.AddFilter``.
    """
    wx = require_wx()
    button_type = wx.EVT_BUTTON.typeId
    control_kinds = {
        wx.EVT_CHECKBOX.typeId: "CheckBox",
        wx.EVT_RADIOBUTTON.typeId: "RadioButton",
        wx.EVT_CHOICE.typeId: "Choice",
        wx.EVT_TEXT.typeId: "TextCtrl",
    }

    # wx ships no stubs, so ``wx.EventFilter`` resolves to Any and mypy
    # refuses to subclass Any (the same reasoning _build_app_class's own
    # ``wx.App`` subclass records, plus the dotted-Any base's
    # name-defined noise).
    class _ControlEventFilter(wx.EventFilter):  # type: ignore[misc, name-defined]
        """Log whitelisted control events; never swallow one (F4)."""

        def FilterEvent(self, event: Any) -> int:  # noqa: ANN401, N802 -- wx's own override name
            """Log one whitelisted activation, or pass the event on."""
            if log.verbose:
                event_type = event.GetEventType()
                control = event.GetEventObject()
                if control is not None:
                    if event_type == button_type:
                        log.button(control.GetName(), control.GetLabel())
                    elif event_type in control_kinds:
                        log.control(control.GetName(), control_kinds[event_type])
            return wx.EventFilter.Event_Skip  # type: ignore[no-any-return]

    return _ControlEventFilter()


def _install_crash_excepthook(app: Any) -> None:  # noqa: ANN401 -- the live wx.App
    """Route unhandled exceptions to *app*'s exception handler.

    wxPython 4.3.1 swallows a Python exception that escapes an event
    handler only after routing it through ``sys.excepthook``
    (measured: the main loop calls ``PyErr_Print``, and an
    ``OnExceptionInMainLoop`` override is never dispatched), so this
    hook is the one seam that sees both the pre-MainLoop bootstrap
    path and the handler exceptions the loop swallows. The windowed
    bundle has no console to show the default hook's stderr traceback,
    so the app's own handler takes its place: the exception record in
    the launch's NDJSON log, plus a one-line status notice
    (:func:`main` installs this before the bootstrap).
    """

    def _hook(
        exc_type: type[BaseException], exc_value: BaseException, exc_tb: TracebackType | None
    ) -> None:
        app._handle_uncaught_exception(  # noqa: SLF001 -- the app seam this hook exists to call
            exc_type, exc_value, exc_tb
        )

    sys.excepthook = _hook


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
        is safe even before then. ``log`` is this launch's NDJSON
        :class:`~rivercrossing.ui.logging.Logging`, attached by
        :func:`main`; ``None`` for an app a test or the functional
        harness built without going through it.
        """

        main_frame: Any = None
        really_quitting: bool = False
        # F1: this launch's NDJSON log, attached by main() after it is
        # built next to settings.json. ``None`` for an app a test or
        # the functional harness built without going through main(),
        # so every logging call site is a silent no-op.
        log: Logging | None = None

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

        def _handle_uncaught_exception(
            self,
            exc_type: type[BaseException],
            exc_value: BaseException,
            exc_tb: TracebackType | None,
        ) -> None:
            """Log an uncaught exception; show a one-line notice.

            The target of :func:`_install_crash_excepthook` (the
            ``sys.excepthook`` :func:`main` installs): wxPython 4.3.1
            swallows a Python exception that escapes an event handler
            only after routing it through ``sys.excepthook``, so this
            handler covers both the pre-MainLoop bootstrap path and
            the main-loop exceptions that would otherwise vanish with
            zero signal in the windowed bundle (the measured note
            ``docs/EPIC3-SESSION-SUMMARY.md`` records). Writes the
            record to :attr:`log` and posts a one-line notice naming
            that file on the status bar when a frame exists.
            """
            if self.log is None:
                return
            self.log.exception(  # noqa: LOG004 -- the hook's own exception triple
                exc_type, exc_value, exc_tb
            )
            if self.main_frame is not None:
                self.main_frame.SetStatusText(
                    f"An unexpected error occurred — see {_log_file_name(self.log)} for details"
                )

    return RiverCrossingApp


def build_app() -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Construct the one live ``RiverCrossingApp`` instance."""
    return _build_app_class()()


def _bootstrap_window(  # noqa: PLR0913 -- (app, db_path, store, settings_path): the bootstrap seams, mirroring build_main_window
    app: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    db_path: Path | None = None,
    store: Store | None = None,
    settings_path: Path | None = None,
) -> tuple[Any, Store]:
    """Open the store and build the main window, the way main() does.

    E9.1.1: the store-backed bootstrap, split out so a test can drive
    the whole open-store-and-build path without ever entering
    ``MainLoop``, which blocks (the same reason
    :func:`build_main_window` exists). W3: :func:`main` opens the
    Store itself -- its ``finally`` must own the close even when the
    build raises -- and passes it in as ``store=``; callers that want
    the one-call open-and-build seam (the functional/acceptance
    helpers that mirror main() minus the loop) pass ``db_path=`` and
    receive the opened Store back. Whichever way it is opened, the
    store threads into the window so the launch flow reads the
    previous session (R-52) and the quit flow stamps ``closed_at`` on
    a confirmed exit. No launch modal runs here: the resume dialog
    and the No Ride Open alert belong to :func:`main`'s post-Show
    :func:`_run_launch_flow`.

    Args:
        app: The live app :func:`main`/:func:`build_app` constructed.
        db_path: The rides database to open when *store* is not
            supplied; ``None`` uses
            :func:`~rivercrossing.store.default_db_path`.
        store: An already-open Store to build over (main()'s W3
            ownership); ``None`` opens one from *db_path*.
        settings_path: The per-user settings file, threaded to
            :func:`build_main_window`.

    Returns:
        ``(frame, store)`` -- the loaded main frame and the Store it
        was built over, which the caller owns until the app quits.
    """
    if store is None:
        store = Store.open(default_db_path(db_path))
    frame = build_main_window(app, store=store, settings_path=settings_path)
    return frame, store


def _resolve_db_path(db_path: Path | None) -> Path | None:
    """Resolve the database path: explicit arg, env, then the default.

    E9.1.1's launch seam: ``RIVERCROSSING_DB_PATH`` points the bundled
    binary at a temp ``rides.db`` (the packaged-app smoke stages one
    through it), while an explicit ``db_path`` -- the functional
    suite's own staging -- still wins. ``None`` means no override:
    :func:`~rivercrossing.store.default_db_path` falls back to the
    per-user default.

    Args:
        db_path: The explicit override, or ``None``.

    Returns:
        The path to open, or ``None`` for the per-user default.
    """
    if db_path is not None:
        return db_path
    env_path = os.environ.get(_DB_PATH_ENV)
    return Path(env_path) if env_path else None


def main(db_path: Path | None = None) -> int:
    """Run the RiverCrossing GUI application.

    Builds and shows ``main_frame`` with its menubar, accelerators and
    every §15 route bound (:func:`build_main_window`), then runs the
    event loop until the last top-level window closes.

    F1/F3/F4: the per-invocation NDJSON log is constructed here, next
    to ``settings.json``, from the loaded settings' ``verbose_logging``
    flag (:func:`~rivercrossing.ui.logging.build_log_path`); the
    launch context is recorded before anything can raise, and the
    whitelisted control-event filter is installed on the app. The
    separate plain-text crash log is gone (workstream A2).

    F1 log ordering: load the settings, prune the old invocation logs
    (:func:`~rivercrossing.ui.logging.prune_logs`), open this launch's
    file and record the launch context, then build the app and attach
    the log to it. The ``finally`` closes the log alongside the store,
    so a start failure still flushes the launch record.

    W3 launch ordering (the "app never starts again" fixes): opens the
    rides database HERE -- a ``finally`` always owns the Store, so a
    bootstrap raise still closes it -- builds the window with no
    modal, shows it, then defers both the launch flow
    (:func:`_run_launch_flow`: ``resume_dlg`` when the previous
    session left a ride running, else the No Ride Open alert) and the
    R-44 self-test to the running event loop, and enters
    ``MainLoop``. A raise anywhere before the loop shows a parentless
    error box (:func:`~rivercrossing.ui.std_dialogs.show_error`) and
    is re-raised, so the crash excepthook still records the exception
    -- except
    :class:`~rivercrossing.store.SchemaVersionMismatchError`, whose
    branch reports "Database Mismatch" and returns 1 without
    re-raising, so no exception record is filed.

    Args:
        db_path: The rides database to open; ``None`` uses the per-user
            default (:func:`~rivercrossing.store.default_db_path`),
            or the ``RIVERCROSSING_DB_PATH`` override when set
            (:func:`_resolve_db_path`). The one argument a caller may
            supply -- the functional suite stages a temp ``rides.db``
            through it.

    Returns:
        The process exit code; ``0`` on a clean shutdown.

    Raises:
        WxUnavailableError: If ``wx`` cannot be imported.
    """
    settings = settings_store.load_settings()
    log_dir = settings_store.default_path().parent
    prune_logs(log_dir, keep=20)
    log = Logging(
        build_log_path(log_dir, datetime.now(UTC)),
        verbose=settings.verbose_logging,
    )
    log.startup(
        verbose=settings.verbose_logging,
        started_at=datetime.now(UTC),
        version=__version__,
        platform=sys.platform,
        python=platform.python_version(),
        pid=os.getpid(),
    )

    wx = require_wx()
    app = build_app()  # bound for this whole call -- an unbound App is collected immediately
    app.log = log
    # Route unhandled exceptions (bootstrap and main-loop alike) to
    # this launch's log before anything can raise.
    _install_crash_excepthook(app)
    # ``wx.App.AddFilter`` is ``wxEvtHandler.AddFilter``: it keeps a raw
    # C++ pointer, not a Python reference, so the filter has to stay
    # bound to the app -- a throwaway reference is collected and wx then
    # dispatches the first event into freed memory (the frozen bundle's
    # launch SIGSEGV). The app already retains its log the same way.
    app.event_filter = _make_event_filter(log)
    wx.App.AddFilter(app.event_filter)
    wx.Log.SetActiveTarget(wx.LogStderr())  # see module docstring: the exit-time modal hang

    store: Store | None = None
    try:
        store = Store.open(default_db_path(_resolve_db_path(db_path)))
        frame, _opened = _bootstrap_window(app, store=store)
        frame.Show()
        # G: the launch flow's resume/No-Ride dialog runs on the running
        # event loop, after Show() and after the menubar/routes are live
        # (mirroring the deferred self-test below) -- a modal opened
        # synchronously here is the "app never starts again" shape.
        wx.CallAfter(_run_launch_flow, app.launch_context, store)
        wx.CallAfter(_run_launch_self_test, app.launch_context)
        app.MainLoop()
    except SchemaVersionMismatchError as exc:
        # A version mismatch is an expected condition, not a crash: the
        # database was written by a different build. Tell the operator
        # the way out (rename or delete the file) and exit without
        # re-raising, so the crash excepthook files no exception.
        from rivercrossing.ui import std_dialogs  # noqa: PLC0415 -- deferred, see module docstring

        std_dialogs.show_error(None, "Database Mismatch", str(exc))
        return 1
    except Exception as exc:
        # W3: a raise with no frame to own the notice (a store open or
        # bootstrap failure) still gets a parentless error box, then
        # the raise propagates to the crash excepthook, which records
        # the exception in the log.
        from rivercrossing.ui import std_dialogs  # noqa: PLC0415 -- deferred, see module docstring

        std_dialogs.show_error(
            None,
            "RiverCrossing Could Not Start",
            f"RiverCrossing could not start:\n{exc}",
        )
        raise
    finally:
        if store is not None:
            store.close()
        log.close()

    return 0
