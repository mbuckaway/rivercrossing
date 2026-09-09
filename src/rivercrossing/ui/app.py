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
import re
import sqlite3
import sys
import threading
import traceback
import webbrowser
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from rivercrossing import csvio, htmlexport, pdfexport
from rivercrossing.cards import Card, Shoe, ShoeClosedError
from rivercrossing.htmlexport import ExportOptions
from rivercrossing.ride import (
    IllegalStateError,
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
from rivercrossing.ui.presenters import settings as settings_store
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EmptyDataSource, EngineDataSource, RideSummary

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

    from rivercrossing.ride import Event
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

# The View row's own commands.py target (P8-D8): its 11 ids share one
# route, dispatched further by event id below -- the theme trio to
# theme.ThemeController, the other 8 to the generic COMMAND stub.
_VIEW_ROUTE_TARGET = "view_setting"

# E9.1.1's launch seam: the env var that points the bundled binary at
# a temp rides.db (the packaged-app smoke stages one through it), with
# an explicit ``main(db_path=...)`` argument taking precedence over it.
_DB_PATH_ENV = "RIVERCROSSING_DB_PATH"

# The per-user crash log's file name; it lives next to settings.json
# (the same per-user config directory, E8.1.1) so both the operator
# and a support session can find it in one place.
_CRASH_LOG_NAME = "rivercrossing.log"


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
    # E6.4.2: the most recent results export, backing Preview in
    # Browser (commands.RideState.export_exists derives from it).
    last_export_path: Path | None = None
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


def _check_loaded_theme_radio(menubar: Any, mode: theme.ThemeMode) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Tick the appearance radio for *mode* (E8.1.1, startup + mirror).

    ``wxMenuBar.Check`` also unchecks the theme trio's other two
    members (measured, ``_handle_view_row``'s own note), so ticking the
    mode's radio alone restores the selection. Called at startup after
    ``_check_default_menu_radios`` (ticking System there is a no-op --
    it is already checked) and whenever the settings dialog applies a
    new appearance (E8.1.2's mirror), where the previously checked
    radio must be reverted too.
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    menubar.Check(wx.xrc.XRCID(theme.menu_item_id_for(mode)), True)  # noqa: FBT003 -- wx API takes a positional bool


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
    """Tick the zoom radio for *percent* (E8.1.4, startup + mirror).

    ``wxMenuBar.Check`` also unchecks the zoom group's other six
    members (measured, ``_handle_view_row``'s own note), so ticking the
    percent's radio alone restores the selection. Called at startup
    after ``_check_default_menu_radios`` (ticking 100 there is a no-op
    -- it is already checked) and whenever the settings dialog applies
    a new zoom (the mirror), where the previously checked radio must
    be reverted too.
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


def _zoom_item_id_for(real_id: int) -> str | None:
    """Return the zoom radio's own XRC name for *real_id*, if any.

    The zoom analogue of :func:`_theme_item_id_for` -- an ``EVT_MENU``
    carries only the resolved runtime int, never the XRC name.
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    return next(
        (item_id for item_id in zoom.ZOOM_MENU_ITEM_IDS if wx.xrc.XRCID(item_id) == real_id),
        None,
    )


def _handle_view_row(context: _RouteContext, route: commands.MenuRoute, event: Any) -> None:  # noqa: ANN401
    """Dispatch the View row: theme, hide-times and zoom ids, else stub.

    P8-D4. A synthetic ``EVT_MENU`` never flips a menu item's own
    checked state the way a genuine native click does (measured: this
    harness's functional suite has no delivery mechanism but direct
    event injection, harness.py's own module docstring), so each
    branch sets its item's checked state explicitly; ``wxMenuBar.
    Check`` also unchecks the other members of a radio group
    (measured), matching a real click's native handling.

    E8.1.2 closed the appearance mirror: a theme radio click now also
    persists the choice (and updates ``context.settings``), so the
    next Settings dialog open renders it -- the same file the dialog's
    OK writes. E8.1.3 adds ``mi_hide_times``: a live toggle -- flip
    ``context.settings.hide_times``, apply through the console
    presenter, persist, and set the check item explicitly (a synthetic
    event does not auto-toggle check items either). E8.1.4 adds the
    seven ``mi_zoom_*`` radios: apply through the zoom controller,
    persist, and tick the fired radio explicitly.
    """
    require_wx()
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    item_id = _theme_item_id_for(event.GetId())
    if item_id is not None:
        context.frame.GetMenuBar().Check(event.GetId(), True)  # noqa: FBT003 -- wx API takes a positional bool
        notice = context.theme_controller.on_menu(item_id)
        if notice is not None:
            context.frame.SetStatusText(notice)
        # E8.1.2: persist the choice -- only appearance changes; the
        # other fields stay as currently held.
        mode = theme.mode_for_menu_id(item_id)
        updated = replace(context.settings, appearance=mode.value)
        settings_store.save_settings(updated, context.settings_path)
        context.settings = updated
        return
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
    _wire_store_append(engine, store, ride_id, notify=_status_notice(context))
    source = EngineDataSource(engine, roster)
    presenter = ConsolePresenter(context.console_view, engine=engine, source=source)
    context.console_view.set_presenter(presenter)
    context.console_view.show_ride_name(engine.config.name)
    context.console_view.set_state(source.ride_status())
    context.console_view.show_feed(source.feed_rows())
    context.console_view.show_counters(source.counters())
    context.console_view.focus_entry()
    context.presenter = presenter
    context.roster = roster
    context.active_ride_id = ride_id


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
      appears immediately (R-15). A refused duplicate surfaces as a
      status notice: an unguarded raise from this button callback is
      swallowed by wx with zero signal (the measured note
      ``docs/EPIC3-SESSION-SUMMARY.md`` records).

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
        try:
            store.duplicate_ride(selected.ride_id)
        except (OSError, sqlite3.Error) as exc:
            context.frame.SetStatusText(f"Could not duplicate ride: {exc}")

    return _open, _new, _duplicate


def _apply_settings_live(context: _RouteContext, settings: AppSettings) -> None:
    """Persist *settings* and apply its live paths (E8.1.2-E8.1.4).

    The settings dialog's OK callback: saves to the config file,
    updates the context's current settings, then applies what has live
    paths -- appearance through the live theme controller (which
    re-checks the View-menu radio via ``_check_loaded_theme_radio``),
    sound through :func:`~rivercrossing.ui.sound.set_muted`, hide-times
    through the console presenter's ``on_hide_times`` (when a
    presenter is threaded) with the View-menu check item synced
    (``_check_loaded_hide_times``), and zoom through
    :func:`~rivercrossing.ui.zoom.set_percent` when the choice changed
    (E8.1.4), with the View-menu zoom radio synced
    (``_check_loaded_zoom_radio``).
    """
    zoom_changed = settings.zoom_percent != context.settings.zoom_percent
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
    notice = context.theme_controller.on_menu(theme.menu_item_id_for(mode))
    if notice is not None:
        context.frame.SetStatusText(notice)
    _check_loaded_theme_radio(context.frame.GetMenuBar(), mode)
    _check_loaded_hide_times(context.frame.GetMenuBar(), hide=settings.hide_times)
    _check_loaded_zoom_radio(context.frame.GetMenuBar(), settings.zoom_percent)
    sound.set_muted(muted=not settings.sound_on)
    presenter = context.presenter
    if presenter is not None:
        presenter.on_hide_times(hide=settings.hide_times)
    if zoom_changed:
        zoom.set_percent(settings.zoom_percent)


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
    from rivercrossing.ui.views.team_editor import TeamEditor  # noqa: PLC0415

    if route.target == ids.RIDE_LIBRARY_DLG:
        if context.store is not None:
            on_open, on_new, on_duplicate = _live_library_callbacks(context, window, context.store)
            RideLibrary(
                window,
                data_source=_StoreLibrarySource(context.store),
                on_delete=_library_delete_callback(context, window),
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
    elif route.target == ids.ENTRY_DETAIL_DLG:
        # E7.2.1 (shared with the W11 F2a flagged seam): the live
        # branch opens the selected entry over the live seams; the
        # empty branch keeps the E5.4.2 empty state. See
        # _open_entry_detail_dialog's own docstring.
        _open_entry_detail_dialog(context, window, context.detail_plate or "")
    elif route.target == ids.RESULTS_FRAME:
        # E6.4.1 (D10): with a live console threaded, results render
        # the real placed rows from the console's EngineDataSource
        # (the same live source build_main_window wired, so the
        # roster always matches the engine -- the resume path never
        # updates context.roster) and seed the tie-break list from
        # the ride's stored order. The E5.4.2 empty state stays for
        # the no-presenter path (route-level tests). ux-polish: the
        # results frame's reopen_btn is wired to the same
        # _handle_reopen_ride_route flow mi_reopen_ride runs; a
        # results window with no live ride (the empty path) gets no
        # callback and its button stays inert.
        presenter = context.presenter
        if presenter is not None:
            ResultsWindow(
                window,
                data_source=presenter.source,
                tiebreak_order=presenter.engine.config.tiebreak_order,
                export_watermark=context.export_watermark,
                on_reopen=lambda: _handle_reopen_ride_route(context),
                # W11: the four export buttons fire the same
                # _handle_export_command route the matching mi_export_*
                # menu row runs -- the dead synthetic-EVT_MENU
                # forwarding is gone (the parentless results frame
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
        export_exists=context.last_export_path is not None,
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

    A conversion made inside the dialog mutates the shared in-memory
    roster; persist + rebuild after the modal ends, exactly like a CSV
    import (persist first, then ``_switch_console_to_ride`` with the
    live clock), so a converted team-of-one survives a relaunch. A
    refused roster save surfaces as a status notice and skips the
    rebuild -- the same guard :func:`_handle_import_csv` uses, for
    the same wx-swallowed-raise reason (the measured note
    ``docs/EPIC3-SESSION-SUMMARY.md`` records).
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


def _pick_export_path(suggested_name: str) -> Path | None:
    """Open the OS save dialog for one export (E6.4.2 picker seam).

    Returns the chosen path, or None on cancel (a silent no-op, the
    same shape :func:`_handle_export_csv` uses). Tests monkeypatch
    this to write tmp files (the ``rider_editor._pick_export_path``
    precedent, test_csv_route_flows.py).
    """
    wx = require_wx()
    dialog = wx.FileDialog(
        None,
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
    frame = wx.FindWindowByName(ids.RESULTS_FRAME)
    presenter = getattr(frame, "presenter", None)
    if presenter is not None:
        return cast("ExportOptions", presenter.export_options())
    return ExportOptions()


def _team_logo_srcs(roster: Roster | None) -> dict[str, str]:
    """Map every logo-carrying TEAM entry's plate to its data URI (W8).

    The HTML export's roster-entry lookup seam: htmlexport renders
    each placed row's small logo image from this map, keyed by the
    entry's plate. A stored image bytes become a PNG data URI
    directly; a card code resolves to its packaged 48x64 card bitmap
    (the same asset key the wx imagelist uses, read at the 2x scale
    so the ~48px-tall page image stays crisp). A team with no logo,
    or a code with no asset behind it, contributes no entry --
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
        if entry.logo_png is not None:
            encoded = base64.b64encode(entry.logo_png).decode("ascii")
            srcs[entry.plate] = f"data:image/png;base64,{encoded}"
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
    posts the status notice and records ``last_export_path`` /
    ``export_watermark`` through ``wx.CallAfter`` (the E5-recorded
    mechanism), keeping every wx touch on the main thread. E7.3.2:
    the successful export also advances the route context's export
    watermark and clears an open results window's stale banner -- only
    on success, so a failed write never masks a post-export
    correction. Failures surface on the status bar instead of the
    console.
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
        wx.CallAfter(setattr, context, "last_export_path", path)
        wx.CallAfter(setattr, context, "export_watermark", watermark)
        wx.CallAfter(_clear_results_stale, watermark)

    threading.Thread(target=write, daemon=True).start()


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
    frame = wx.FindWindowByName(ids.RESULTS_FRAME)
    presenter = getattr(frame, "presenter", None)
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


def _handle_preview_browser(context: _RouteContext) -> None:
    """Results ▸ Preview in Browser: open the last export (E6.4.2)."""
    if context.last_export_path is None:
        context.frame.SetStatusText("No export yet — generate one first")
        return
    _open_in_browser(context.last_export_path)
    context.frame.SetStatusText(f"Opened {context.last_export_path.name}")


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


def _handle_focus_tiebreak(context: _RouteContext) -> None:
    """Results ▸ Tie-break Order…: open Results and focus the list."""
    wx = require_wx()
    frame = wx.FindWindowByName(ids.RESULTS_FRAME)
    if frame is None:
        _open_target(context, commands.route_for_id("mi_standings"))
        frame = wx.FindWindowByName(ids.RESULTS_FRAME)
    if frame is None:
        context.frame.SetStatusText("Open Results to set the tie-break order")
        return
    control = frame.FindWindowByName(ids.TIEBREAK_LIST)
    if control is not None:
        control.SetFocus()


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

    Loads ``finish_confirm_dlg`` and shows it through
    :func:`~rivercrossing.ui.views.dialogs.run_dialog` -- the one seam
    every dialog in this codebase shows through -- and only a confirmed
    ``wx.ID_OK`` fires the live console presenter's ``on_finish``,
    which consults ``FINISH_GATE`` -- the hook that runs the
    evaluator's real self-test suite -- and calls ``engine.finish()``.
    Mirrors the
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
    # E7.2.2: REOPENED's single primary action is "Finish again" (spec
    # §3 design 8c) -- the same finish_confirm_dlg, re-labelled. The
    # engine is read BEFORE the confirm so the label reflects the state
    # the dialog opens in, not the post-finish one.
    presenter_for_label = context.presenter
    if presenter_for_label is not None and presenter_for_label.engine.state is RideStatus.REOPENED:
        title, ok_label = dialogs.finish_again_labels()
        dialog.SetTitle(title)
        ok_button = wx.Window.FindWindowById(wx.ID_OK, dialog)
        if ok_button is not None:
            ok_button.SetLabel(ok_label)
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
        ids.DUPLICATE_RIDE_DLG,
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

    ``LoadFrame``/``LoadDialog`` return ``None`` rather than raise
    when *route.target* names no XRC resource at all (harness.py's
    own measured note) -- no §15 route is un-authored anymore (E5.4.1
    and E7 authored Duplicate Ride, Reopen Ride, Void Card), but the
    branch stays as the safety net for any future route whose target
    is not yet authored, with no change needed here: a route never
    silently does nothing, it always says so on the status bar instead.
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
        # E8.1.4: every window opened later inherits the current zoom.
        # Applied BEFORE decoration so a view's value-setting runs last:
        # the recursive SetFont walk resets a wxChoice's selection to -1
        # on this pin (measured in the VM: settings_dlg's zoom_choice),
        # and the view's show_settings re-sets it. Base fonts come from
        # the fresh XRC load, scaled once (never compounded).
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
    if is_frame:
        # ux-polish: apply the light-mode panel tint to the results
        # frame at open. The frame is modeless, so -- unlike the modal
        # dialogs run_dialog tints -- it can stay open across a live
        # macOS theme switch; re-applying (or clearing) the tint when
        # the theme changes while it is open is deliberately out of
        # scope (theme.apply_light_mode_panel_bg's docstring): closing
        # and reopening re-tints. results.xrc carries its top sizer
        # directly on the frame, so the frame background is the only
        # surface.
        theme.apply_light_mode_panel_bg(window)
        window.Show()
        window.Raise()
        return

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

    A confirmed ``QuitOutcome.QUIT`` stamps the open session's
    ``closed_at`` through :func:`_stamp_closed_session` (E5.2.1: a
    clean quit, not a crash, at the next launch); a
    ``QuitOutcome.FINISH_FIRST``
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
        _stamp_closed_session(context)
    elif outcome is quit_flow.QuitOutcome.FINISH_FIRST:
        _handle_finish_route(context)
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
# Ride both open a confirm dialog then act on OK (like the finish
# route), so they dispatch through one table in _make_route_handler
# rather than two near-identical branches.
_RIDE_CONFIRM_HANDLERS: dict[str, Callable[[_RouteContext], None]] = {
    ids.DUPLICATE_RIDE_DLG: _handle_duplicate_ride_route,
    ids.REOPEN_RIDE_DLG: _handle_reopen_ride_route,
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
_TARGET_ACTIONS["preview_in_browser"] = _handle_preview_browser
_TARGET_ACTIONS["focus_tiebreak_control"] = _handle_focus_tiebreak
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
    ``StartBlockedError`` and the presenter surfaces a native warning
    (W5); with no presenter the fallback posts "Start Ride — no ride
    open".
    ``stop_ride`` (W5) fires the live presenter's native stop-confirm
    flow (``on_stop_requested``) the same way -- a riderless roster
    gets a native warning from the flow itself; with no presenter the
    fallback posts the generic stub. ``focus_review_panel``
    (ux-polish) focuses the console's
    review-panel "Needs Review" tab through the wired console view,
    with the generic stub standing in for a console-less route-level
    context. ``finish_confirm_dlg`` (E4.4.4) opens its confirm
    through :func:`_handle_finish_route`, which runs
    ``presenter.on_finish`` on a confirmed OK -- the same
    presenter-first shape ``undo_last_crossing`` uses -- instead of
    :func:`_open_target`'s generic open-and-return. ``set_start_dlg``
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
    if route.target == "focus_review_panel":
        console_view = context.console_view
        if console_view is not None:
            return lambda _event: console_view.focus_review_panel()
        return lambda _event: context.frame.SetStatusText(f"{route.label} — not yet implemented")
    if route.target == ids.FINISH_CONFIRM_DLG:
        return lambda _event: _handle_finish_route(context)
    # E5.4.1's two mock-first confirms both need a real handler ahead
    # of _open_target (their confirm -> action shape, like the finish
    # route), so they share one dispatch table instead of two branches.
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

    Replaces the retired ``no_ride_dlg`` window (its Create/Open-
    library choice is the File menus' own job): a store-backed launch
    that resumes no ride and opens no ride gets this one information
    alert instead of an unexplained empty console, then nothing -- the
    console is visible and the operator uses the menus. ``no_ride_dlg``
    stays authored in dialogs.xrc until W15 removes the window itself;
    this is the code that no longer loads it.
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

    E8.1.1 loads the per-user settings file at startup and applies
    what already has live paths: the persisted appearance through
    :class:`~rivercrossing.ui.theme.ThemeController` (constructed with
    the loaded mode, and the matching menu radio checked), the sound
    mute through :func:`~rivercrossing.ui.sound.set_muted`, hide-times
    through the console presenter's ``on_hide_times`` (with the menu
    check item synced, E8.1.3), zoom through
    :func:`~rivercrossing.ui.zoom.set_percent` (with the menu radio
    synced, E8.1.4), and the saved splitter sash / frame geometry
    through :class:`MainFrame`'s layout seams. The settings file path
    and current :class:`AppSettings` are kept on
    :class:`_RouteContext`, and the layout save callback persists
    sash/geometry changes back to the file.

    ux-polish adds one post-wiring step: the console Riders tab's
    double-click seam is wired to the rider editor
    (:func:`_wire_rider_open_seam`); W11 F2a adds the flagged tab's
    activation seam the same way
    (:func:`_wire_flagged_open_seam` -> live entry detail at the
    flagged plate). W3 retired every launch modal
    from this function -- ``resume_dlg``, the no-ride prompt and the
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

    # E8.1.1: load the per-user settings once, at startup; every apply
    # below reads the same loaded object, and the layout save callback
    # writes back through the same path.
    settings_path = settings_path if settings_path is not None else settings_store.default_path()
    # The crash log follows the launch's real settings file (E8.1.1's
    # directory), including a temp path injected by the test suites.
    app.crash_log_path = Path(settings_path).with_name(_CRASH_LOG_NAME)
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
    _check_default_menu_radios(menubar)
    _check_loaded_theme_radio(menubar, loaded_mode)
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

    # W3: no launch modal runs here. The console always opens on the
    # empty DRAFT engine (E5.4.2); the post-Show launch flow
    # (main()'s _run_launch_flow) shows resume_dlg / the No Ride Open
    # alert over the visible frame, and Continue -- or the library's
    # Open -- swaps the console onto the store ride afterwards, so a
    # replay against a drifted roster can never take the build down.
    engine, engine_source = _build_console_engine(roster)

    def _save_layout(sash: int | None, geometry: tuple[int, int, int, int] | None) -> None:
        """Persist the console's layout and keep the context current."""
        _save_layout_settings(context, sash, geometry)

    _console = MainFrame(
        frame,
        data_source=engine_source,
        initial_sash=loaded_settings.splitter_sash,
        initial_geometry=loaded_settings.window_geometry,
        on_layout_changed=_save_layout,
    )

    # _presenter is kept alive the same way: wire_entry/wire_console's
    # closures hold its bound handlers, which wx's own event table and
    # the tick timer then hold.
    _presenter = ConsolePresenter(_console, engine=engine, source=engine_source)
    _console.wire_entry(_presenter.on_plate_entered)
    _console.wire_console(_presenter)
    _console.set_state(engine_source.ride_status())
    _console.focus_entry()

    # E8.1.1-E8.1.4: apply the persisted settings that have live
    # paths -- appearance (the ThemeController, constructed with the
    # loaded mode), sound, hide-times and zoom.
    sound.set_muted(muted=not loaded_settings.sound_on)
    _presenter.on_hide_times(hide=loaded_settings.hide_times)
    zoom.set_percent(loaded_settings.zoom_percent)

    _apply_accelerators(frame, menubar)
    # theme_controller is kept alive by _RouteContext, threaded through
    # every route handler. console_view is threaded the same way so
    # E5.4.1's library Open can swap the console's presenter;
    # active_ride_id records the store ride the launch flow's Continue
    # opened, if any (File ▸ Duplicate Ride… reads it).
    context = replace(
        context,
        presenter=_presenter,
        console_view=_console,
    )
    _bind_routes(context)
    _wire_rider_open_seam(context)
    _wire_flagged_open_seam(context)
    _bind_process_quit_paths(context)
    _bind_theme(context)
    # E7.2.1: the live menu-enablement binder (E1.4.2's missing half).
    # set_on_ride_changed fires on every ride-state change (the
    # console's own seam) and on every feed re-render, so the §15
    # "Enabled when" cells hold in the app -- the initial call below
    # applies them to the bootstrap's DRAFT ride.
    _console.set_on_ride_changed(lambda status: _apply_menu_state(context, status))
    _apply_menu_state(context, engine.state)

    # W3: the post-Show launch flow needs the assembled context, and
    # the app object is the one handle main() and the functional
    # helpers share (the same reason main_frame lives on it).
    app.launch_context = context
    return frame


def _append_exception_log(  # noqa: PLR0913, PLR0917 -- (type, value, traceback) is sys.excepthook's own triple, plus the target path
    path: Path,
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: TracebackType | None,
) -> Path:
    """Append *exc*'s formatted traceback to the crash log at *path*.

    The one writer the app's uncaught-exception handler and the
    bootstrap hook use. A failed write (an unwritable log directory,
    a full disk) is swallowed: crash logging must never replace the
    original error with a second exception raised from inside
    ``sys.excepthook`` -- the caller still surfaces its own notice.

    Returns:
        *path*.
    """
    header = f"\n===== {datetime.now(UTC).isoformat(timespec='seconds')} =====\n"
    payload = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(header + payload)
    except OSError:
        # A crash log that cannot be written must not crash the app
        # a second time from inside sys.excepthook.
        return path
    return path


def _install_crash_excepthook(app: Any) -> None:  # noqa: ANN401 -- the live wx.App
    """Route unhandled exceptions to *app*'s crash-log handler.

    wxPython 4.3.1 swallows a Python exception that escapes an event
    handler only after routing it through ``sys.excepthook``
    (measured: the main loop calls ``PyErr_Print``, and an
    ``OnExceptionInMainLoop`` override is never dispatched), so this
    hook is the one seam that sees both the pre-MainLoop bootstrap
    path and the handler exceptions the loop swallows. The windowed
    bundle has no console to show the default hook's stderr traceback,
    so the app's own handler takes its place: full traceback to the
    per-user crash log next to the settings file, plus a one-line
    status notice (:func:`main` installs this before the bootstrap).
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
        is safe even before then. ``crash_log_path`` (the
        :meth:`_handle_uncaught_exception` writer's target) defaults
        to the per-user config directory and is re-pointed at the
        launch's actual settings_path by :func:`build_main_window`.
        """

        main_frame: Any = None
        really_quitting: bool = False
        # The per-user crash log, next to the settings file (E8.1.1's
        # directory); ``build_main_window`` re-points this at the
        # launch's actual settings_path when one is injected.
        crash_log_path: Path = settings_store.default_path().with_name(_CRASH_LOG_NAME)

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
            full traceback to :attr:`crash_log_path` and posts a
            one-line notice on the status bar when a frame exists.
            """
            _append_exception_log(self.crash_log_path, exc_type, exc_value, exc_tb)
            if self.main_frame is not None:
                self.main_frame.SetStatusText(
                    f"An unexpected error occurred — see {self.crash_log_path.name} for details"
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

    W3 launch ordering (the "app never starts again" fixes): opens the
    rides database HERE -- a ``finally`` always owns the Store, so a
    bootstrap raise still closes it -- builds the window with no
    modal, shows it, then runs the launch flow
    (:func:`_run_launch_flow`: ``resume_dlg`` when the previous
    session left a ride running, else the No Ride Open alert), defers
    the R-44 self-test to the running event loop, and enters
    ``MainLoop``. A raise anywhere before the loop shows a parentless
    error box (:func:`~rivercrossing.ui.std_dialogs.show_error`) and
    is re-raised, so the crash excepthook still appends the crash
    log.

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
    wx = require_wx()
    app = build_app()  # bound for this whole call -- an unbound App is collected immediately
    # Route unhandled exceptions (bootstrap and main-loop alike) to
    # the per-user crash log before anything can raise.
    _install_crash_excepthook(app)
    wx.Log.SetActiveTarget(wx.LogStderr())  # see module docstring: the exit-time modal hang

    store: Store | None = None
    try:
        store = Store.open(default_db_path(_resolve_db_path(db_path)))
        frame, _opened = _bootstrap_window(app, store=store)
        frame.Show()
        _run_launch_flow(app.launch_context, store)
        wx.CallAfter(_run_launch_self_test, app.launch_context)
        app.MainLoop()
    except Exception as exc:
        # W3: a raise with no frame to own the notice (a store open or
        # bootstrap failure) still gets a parentless error box, then
        # the raise propagates to the crash excepthook, which appends
        # the crash log.
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

    return 0
