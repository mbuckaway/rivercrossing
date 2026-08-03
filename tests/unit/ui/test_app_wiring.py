# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the app bootstrap's wx-free contract (E1.6.x).

Everything a real display would be needed to prove -- the menubar,
the accelerator table, every §15 route actually bound, demo data on
screen -- lives in ``tests/functional/test_app_bootstrap.py`` instead
(mirroring ``test_commands.py``/``test_menu_coverage.py``'s own
split). What stays here is what ``ast`` and plain imports can already
prove without wx: that ``rivercrossing.ui.app`` itself never needs a
``wx.App`` -- or even wx at all -- to import, that :func:`main` is
annotated, and that its one demo-seam import is exactly that: one.
"""

import ast
import inspect
import sys
from typing import TYPE_CHECKING

from rivercrossing.ui import app

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


def test_main_takes_no_parameters() -> None:
    """main() is the entry point; nothing supplies it arguments."""
    parameters = inspect.signature(app.main).parameters

    assert parameters == {}


# --- the one-line demo seam (E1.2.4) -------------------------------


def test_app_module_source_imports_rivercrossing_demo_exactly_once() -> None:
    """Seam proof: deleting demo.py breaks only this one import line."""
    source = inspect.getsource(app)

    assert _demo_import_count(source) == 1


def test_app_module_source_constructs_demodatasource_exactly_once() -> None:
    """The one construction every later-opened window reuses."""
    source = inspect.getsource(app)

    assert _demo_construction_count(source) == 1


# --- build_main_window is importable alongside main() --------------


def test_build_main_window_is_exported_from_the_module() -> None:
    """The construction path main() delegates to is public."""
    assert "build_main_window" in app.__all__


def test_build_main_window_is_callable() -> None:
    """main() delegates real construction to it; it must be callable."""
    assert inspect.isfunction(app.build_main_window)
