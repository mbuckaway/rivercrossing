# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the app bootstrap's wx-free contract (E1.6.x).

Everything a real display would be needed to prove -- the menubar,
the accelerator table, every §15 route actually bound, real/empty
data on screen -- lives in ``tests/functional/test_app_bootstrap.py``
instead (mirroring ``test_commands.py``/``test_menu_coverage.py``'s
own split). What stays here is what ``ast`` and plain imports can
already prove without wx: that ``rivercrossing.ui.app`` itself never
needs a ``wx.App`` -- or even wx at all -- to import, that :func:`main`
is annotated, and that E5.4.2's demo retirement holds structurally:
the module imports no ``rivercrossing.demo``, constructs no
``DemoDataSource``, and carries no demo->roster seed helper -- the
bootstrap roster is empty and the E6/E7 windows read the module's
``EmptyDataSource``.
"""

import ast
import inspect
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rivercrossing.roster import EntryMode, PlateModel
from rivercrossing.ui import app
from rivercrossing.ui.presenters.data_source import EmptyDataSource

if TYPE_CHECKING:
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


def _demo_import_count(source: str) -> int:
    """Count every AST site that imports ``rivercrossing.demo``.

    Counts both import forms (``import rivercrossing.demo`` and
    ``from rivercrossing.demo import ...``) so the count is enforced
    structurally rather than by string-matching the source text.
    """
    tree = ast.parse(source)
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "rivercrossing.demo":
            count += 1
        elif isinstance(node, ast.Import):
            count += sum(1 for alias in node.names if alias.name == "rivercrossing.demo")
    return count


def _demo_construction_count(source: str) -> int:
    """Count every AST call site constructing ``DemoDataSource()``."""
    tree = ast.parse(source)
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "DemoDataSource"
    )


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
    argument a caller may supply -- the functional suite stages a temp
    ``rides.db`` through it. Nothing else may be threaded in.
    """
    parameters = inspect.signature(app.main).parameters

    assert tuple(parameters) == ("db_path",)
    assert parameters["db_path"].default is None


# --- E5.4.2 demo retirement: the seam is gone from app code --------

# The E1.2.4 seam proof ("deleting demo.py breaks exactly one import
# line") becomes the E5.4.2 proof that there is no line to break: the
# bootstrap imports no demo, constructs no DemoDataSource, and no
# longer carries the demo->roster seed helper -- the roster is empty
# until a store-backed ride is opened.


def test_app_module_source_never_imports_rivercrossing_demo() -> None:
    """E5.4.2: zero ``rivercrossing.demo`` import sites remain."""
    source = inspect.getsource(app)

    assert _demo_import_count(source) == 0


def test_app_module_source_never_constructs_demodatasource() -> None:
    """E5.4.2: zero ``DemoDataSource()`` construction sites remain."""
    source = inspect.getsource(app)

    assert _demo_construction_count(source) == 0


def test_app_module_source_defines_no_demo_roster_seed_helper() -> None:
    """E5.4.2: ``_seed_roster`` (demo rows -> a Roster) is gone.

    The demo rows were the only input the helper ever saw; with the
    seam retired the bootstrap roster is empty, so the conversion
    logic no longer exists in production (tests build seeded rosters
    from demo directly, ``_lists_common.demo_seeded_roster``).
    """
    source = inspect.getsource(app)

    assert "_seed_roster" not in source


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
    )

    assert (
        empty.entry_mode,
        empty.plate_model,
        empty.max_team_size,
        empty.entries,
    ) == (EntryMode.MIXED, PlateModel.RIDER_POOLED, 4, ())


# --- build_main_window is importable alongside main() --------------


def test_build_main_window_is_exported_from_the_module() -> None:
    """The construction path main() delegates to is public."""
    assert "build_main_window" in app.__all__


def test_build_main_window_is_callable() -> None:
    """main() delegates real construction to it; it must be callable."""
    assert inspect.isfunction(app.build_main_window)


# --- E9.1.1: RIVERCROSSING_DB_PATH precedence -----------------------


def test_resolve_db_path_given_no_override_and_no_env_returns_none() -> None:
    """No override, no env: let default_db_path pick the per-user file."""
    assert app._resolve_db_path(None) is None


def test_resolve_db_path_given_the_env_var_returns_the_env_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env var overrides the per-user default (E9.1.1 launch seam)."""
    monkeypatch.setenv("RIVERCROSSING_DB_PATH", "/tmp/env-rides.db")

    resolved = app._resolve_db_path(None)

    assert resolved == Path("/tmp/env-rides.db")


def test_resolve_db_path_given_an_explicit_override_beats_the_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The db_path argument -- the suite's own staging -- wins."""
    monkeypatch.setenv("RIVERCROSSING_DB_PATH", "/tmp/env-rides.db")

    resolved = app._resolve_db_path(Path("/tmp/explicit-rides.db"))

    assert resolved == Path("/tmp/explicit-rides.db")


def test_resolve_db_path_given_an_empty_env_value_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty env value is unset, never a path to open."""
    monkeypatch.setenv("RIVERCROSSING_DB_PATH", "")

    assert app._resolve_db_path(None) is None
