# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the app bootstrap's wx-free contract (E1.6.x).

Everything a real display would be needed to prove -- a live menubar
and frame, every §15 route actually bound, real/empty data on screen
-- is not covered here. What stays here is what plain imports,
``ast`` and ``inspect.getsource`` can already prove without a
``wx.App``: that ``rivercrossing.ui.app`` itself is importable
without wx at all, that :func:`main` is annotated, that the bootstrap
roster is empty with the E6/E7 windows reading the module's
``EmptyDataSource``, and that the bootstrap composes the accelerator
table from the menubar plus the console's own entries without ever
reading anything off the frame (E1.4.1). ``wx.AcceleratorEntry`` and
``wx.AcceleratorTable`` construct headlessly -- measured; only the
menubar's own XRC agreement needs a real toolkit.
"""

import inspect
import sys
from typing import TYPE_CHECKING

import wx
import wx.xrc

from rivercrossing.roster import EntryMode, PlateModel
from rivercrossing.ui import app
from rivercrossing.ui.presenters.data_source import EmptyDataSource

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _BlockWxFinder:
    """Meta path finder that fails any ``wx``/``wx.*`` import.

    Mirrors ``tests/unit/test_packaging.py``'s own finder: simulates a
    missing wxPython installation without ever touching the real
    install (a wxWidgets C++ assertion can abort the interpreter).
    """

    def find_spec(self, fullname: str, _path: object, _target: object | None = None) -> None:
        """Raise for ``wx``/``wx.*``; return for everything else."""
        if fullname == "wx" or fullname.startswith("wx."):
            raise ModuleNotFoundError(f"blocked for test: {fullname}")


def _block_wx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop any cached ``wx``/``wx.*`` module and reject re-import."""
    stale_modules = [name for name in sys.modules if name == "wx" or name.startswith("wx.")]
    for name in stale_modules:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BlockWxFinder(), *sys.meta_path])


# --- module importable without a wx.App, or wx at all ----------


def test_app_module_import_succeeds_when_wx_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The module's own top-level code touches no wx name at all.

    ``rivercrossing.ui.app`` is dropped from ``sys.modules`` first,
    forcing a genuine re-execution of its top-level code (not a cache
    hit) while wx is blocked -- proving the import path itself never
    touches wx, rather than observing an earlier, already-successful
    import that happens to still be cached.
    """
    monkeypatch.delitem(sys.modules, "rivercrossing.ui.app", raising=False)
    _block_wx(monkeypatch)

    module = __import__("rivercrossing.ui.app", fromlist=["main"])

    assert module.__name__ == "rivercrossing.ui.app"


def test_app_module_import_leaves_no_wx_app_constructed() -> None:
    """Importing the module must never eagerly build a ``wx.App``.

    This is the exact Phase-1 bootstrap bug in miniature: an eager
    ``wx.App()`` at import time (rather than inside :func:`main`)
    would already be visible here, without ever calling anything.
    """
    import wx  # noqa: PLC0415 -- this test alone needs the real wx module

    assert wx.GetApp() is None


# --- main() is annotated ------------------------------------------


def test_main_is_annotated_with_an_int_return_type() -> None:
    """T-9/D1: the exit code contract is a real ``int``, not ``Any``."""
    return_annotation = inspect.signature(app.main).return_annotation

    assert return_annotation is int


def test_main_takes_only_the_optional_db_path_override() -> None:
    """main() is the entry point; only the db override may be passed.

    E9.1.1: the db path override (defaulting to ``None``) is the one
    argument a caller may supply. Nothing else may be threaded in.
    """
    parameters = inspect.signature(app.main).parameters

    assert tuple(parameters) == ("db_path",)
    assert parameters["db_path"].default is None


def test_app_module_source_defines_the_empty_state_source() -> None:
    """E5.4.2: ``_EMPTY_SOURCE`` is the module's shared empty state.

    The E6/E7 windows (entry detail, results, the no-store library)
    read this one stateless ``EmptyDataSource`` instance; pinning it
    headless proves the wiring constant exists and is the empty state,
    not a leftover seam.
    """
    empty_source = app._EMPTY_SOURCE

    assert isinstance(empty_source, EmptyDataSource)


# --- the empty bootstrap roster's shape (E3.2's approved default) ---


def test_app_module_empty_bootstrap_roster_keeps_the_mixed_pooled_default() -> None:
    """E5.4.2: the empty roster still declares E3.2's ride settings.

    The bootstrap constructs an empty ``Roster`` (no store-backed ride
    is open) with the mixed/rider_pooled/max-4 shape E3.2 approved, so
    a ride the library later opens keeps the same default; the empty
    ``EntryMode``/``PlateModel`` import here is the same pair
    ``build_main_window`` passes to ``Roster(...)``.
    """
    empty = app.Roster(
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        max_team_size=app._SEEDED_MAX_TEAM_SIZE,
        # W8: the same fixed seed the bootstrap passes, so the pin
        # mirrors the production call site exactly.
        team_logo_seed=app._SEEDED_TEAM_LOGO_SEED,
    )

    assert (
        empty.entry_mode,
        empty.plate_model,
        empty.max_team_size,
        empty.team_logo_seed,
        empty.entries,
    ) == (EntryMode.MIXED, PlateModel.RIDER_POOLED, 4, app._SEEDED_TEAM_LOGO_SEED, ())


# --- build_main_window is importable alongside main() --------------


def test_build_main_window_is_exported_from_the_module() -> None:
    """The construction path main() delegates to is public."""
    assert "build_main_window" in app.__all__


def test_build_main_window_is_callable() -> None:
    """main() delegates real construction to it; it must be callable."""
    assert inspect.isfunction(app.build_main_window)


# --- E9.1.1: RIVERCROSSING_DB_PATH precedence -----------------------


def test_resolve_db_path_given_no_override_and_no_env_returns_none() -> None:
    """No override, no env: default_db_path picks the per-user file."""
    assert app._resolve_db_path(None) is None


# --- W3: main() owns the Store and runs the launch flow post-Show ---


def test_main_source_opens_the_store_before_it_builds_the_window() -> None:
    """W3: main() opens the Store itself so a finally always owns it."""
    source = inspect.getsource(app.main)

    assert "store = Store.open(default_db_path(_resolve_db_path(db_path)))" in source
    assert source.index("Store.open(") < source.index("_bootstrap_window(app, store=store)")


def test_main_source_defers_the_launch_flow_after_showing_the_frame() -> None:
    """G: Show() precedes the deferred flow, which precedes MainLoop.

    A launch modal must never run before the frame is visible, and --
    since G -- the flow itself runs on the running event loop (after
    the menubar and routes are live), so main() must schedule it
    through ``wx.CallAfter`` rather than call it inline.
    """
    source = inspect.getsource(app.main)
    scheduled = "wx.CallAfter(_run_launch_flow, app.launch_context, store)"

    assert scheduled in source
    assert source.index("frame.Show()") < source.index(scheduled) < source.index("MainLoop()")


def test_main_source_closes_the_store_inside_a_finally() -> None:
    """W3: a bootstrap raise still closes the Store (crash recovery)."""
    source = inspect.getsource(app.main)
    tail = source[source.index("finally:") :]

    assert "store.close()" in tail


def test_bootstrap_window_accepts_an_opened_store_and_still_opens_its_own() -> None:
    """W3: main() passes its Store in; helpers keep the db_path seam."""
    source = inspect.getsource(app._bootstrap_window)

    assert "store: Store | None = None" in source
    assert "if store is None:" in source
    assert "Store.open(default_db_path(db_path))" in source


def test_resolve_db_path_given_the_env_var_returns_the_env_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The env var overrides the per-user default (E9.1.1 seam)."""
    env_path = tmp_path / "env-rides.db"
    monkeypatch.setenv("RIVERCROSSING_DB_PATH", str(env_path))

    resolved = app._resolve_db_path(None)

    assert resolved == env_path


def test_resolve_db_path_given_an_explicit_override_beats_the_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The db_path argument -- the suite's own staging -- wins."""
    explicit = tmp_path / "explicit-rides.db"
    monkeypatch.setenv("RIVERCROSSING_DB_PATH", str(tmp_path / "env-rides.db"))

    resolved = app._resolve_db_path(explicit)

    assert resolved == explicit


def test_resolve_db_path_given_an_empty_env_value_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty env value is unset, never a path to open."""
    monkeypatch.setenv("RIVERCROSSING_DB_PATH", "")

    assert app._resolve_db_path(None) is None


def test_app_module_source_wires_a_fixed_team_logo_seed_into_the_bootstrap_roster() -> None:
    """W8: the bootstrap roster carries a fixed team-logo seed.

    Phase 4's Pick card button refused everything while no
    store-backed ride was open -- the bootstrap roster had no
    ``team_logo_seed`` at all, so ``next_team_logo_card`` reported
    "every card logo is already in use by a team" against zero teams.
    The seed constant beside ``_SEEDED_MAX_TEAM_SIZE`` fixes that;
    this source pin keeps the bootstrap call honest (the same
    inspect.getsource pattern this file's other wiring pins use).
    """
    assert isinstance(app._SEEDED_TEAM_LOGO_SEED, int)
    assert app._SEEDED_TEAM_LOGO_SEED > 0

    source = inspect.getsource(app.build_main_window)

    assert "team_logo_seed=_SEEDED_TEAM_LOGO_SEED" in source


# --- E1.4.1: the table is applied without reading the frame ---


class _AccelItemDouble:
    """The ``GetAccel()`` half of a menubar row."""

    def __init__(self, accel: wx.AcceleratorEntry) -> None:
        """Hold the row's own accelerator."""
        self._accel = accel

    def GetAccel(self) -> wx.AcceleratorEntry:  # noqa: N802 -- wx API name the double mirrors
        """Return the row's accelerator, as ``wx.MenuItem`` does."""
        return self._accel


class _MenuBarDouble:
    """A menubar whose rows all report one known Ctrl+Z accelerator."""

    def __init__(self) -> None:
        """Log the ids looked up, against one shared row double."""
        self.looked_up: list[int] = []
        self._item = _AccelItemDouble(wx.AcceleratorEntry(wx.ACCEL_CTRL, ord("Z"), 0))

    def FindItem(self, item_id: int) -> tuple[_AccelItemDouble, None]:  # noqa: N802 -- wx API name
        """Return the row double for *item_id*, recording the lookup."""
        self.looked_up.append(item_id)
        return self._item, None


class _FrameDouble:
    """A frame double offering ``SetAcceleratorTable`` and nothing else.

    It deliberately lacks ``accelerator_entries``: a raw ``wx.Frame``
    has no such method either, which is the shape ``build_main_window``
    hands :func:`rivercrossing.ui.app._apply_accelerators`.
    """

    def __init__(self) -> None:
        """Start with no applied table."""
        self.tables: list[object] = []

    def SetAcceleratorTable(self, table: object) -> None:  # noqa: N802 -- wx API name
        """Record *table*, the one call the production code may make."""
        self.tables.append(table)


def test_accelerator_table_entries_given_extra_entries_includes_both() -> None:
    """E1.4.1: the harvested three come first; the code-side F2 follows.

    The three ``menu_item_id`` rows are harvested from the menubar in
    ``ACCELERATOR_TABLE`` order (``Ctrl+Z``, ``F5``, ``F1``);
    ``extra_entries`` -- the console's own ``F2``, which has no menu
    item to harvest -- is appended after them, so a caller's entries
    can never displace the XRC ones.
    """
    menubar = _MenuBarDouble()
    # T-4 boundary: many extra entries, all appended in order.
    extra = [
        wx.AcceleratorEntry(wx.ACCEL_NORMAL, wx.WXK_F2, 4242),
        wx.AcceleratorEntry(wx.ACCEL_CTRL, ord("S"), 4343),
    ]

    entries = app._accelerator_table_entries(menubar, extra)

    assert [(entry.GetFlags(), entry.GetKeyCode(), entry.GetCommand()) for entry in entries] == [
        (wx.ACCEL_CTRL, ord("Z"), wx.xrc.XRCID("mi_undo_crossing")),
        (wx.ACCEL_CTRL, ord("Z"), wx.xrc.XRCID("mi_standings")),
        (wx.ACCEL_CTRL, ord("Z"), wx.xrc.XRCID("mi_user_guide")),
        (wx.ACCEL_NORMAL, wx.WXK_F2, 4242),
        (wx.ACCEL_CTRL, ord("S"), 4343),
    ]


def test_accelerator_table_entries_given_no_extra_entries_returns_the_harvest() -> None:
    """T-4 boundary: an empty ``extra_entries`` adds nothing."""
    menubar = _MenuBarDouble()

    entries = app._accelerator_table_entries(menubar, [])

    assert [entry.GetCommand() for entry in entries] == [
        wx.xrc.XRCID("mi_undo_crossing"),
        wx.xrc.XRCID("mi_standings"),
        wx.xrc.XRCID("mi_user_guide"),
    ]


def test_apply_accelerators_given_a_frame_without_entries_method_applies_table() -> None:
    """The launch blocker: the frame owns no entry list.

    A raw ``wx.Frame`` has no ``accelerator_entries``, but
    ``build_main_window`` passes exactly that, so reading the console's
    F2 entry off it raised AttributeError inside ``_apply_accelerators``
    and every launch died before ``MainLoop``. The entries now arrive as
    an argument instead.
    """
    frame = _FrameDouble()
    extra = [wx.AcceleratorEntry(wx.ACCEL_NORMAL, wx.WXK_F2, 4242)]

    app._apply_accelerators(frame, _MenuBarDouble(), extra_entries=extra)

    assert [type(table) for table in frame.tables] == [wx.AcceleratorTable]


def test_apply_accelerators_given_no_extra_entries_still_applies_the_table() -> None:
    """``extra_entries`` defaults to empty, so none is required."""
    frame = _FrameDouble()

    app._apply_accelerators(frame, _MenuBarDouble())

    assert [type(table) for table in frame.tables] == [wx.AcceleratorTable]


def test_build_main_window_passes_the_console_entries_into_apply_accelerators() -> None:
    """E1.4.1: the bootstrap supplies the console's F2 entry.

    The raw frame cannot supply it (it has no
    ``accelerator_entries``), so the call site names the console's own
    list -- the same source-pin style this file's other
    bootstrap-wiring tests use.
    """
    expected = "_apply_accelerators(frame, menubar, extra_entries=_console.accelerator_entries())"
    source = inspect.getsource(app.build_main_window)

    assert expected in source
