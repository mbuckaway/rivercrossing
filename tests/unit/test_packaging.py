# SPDX-License-Identifier: GPL-3.0-only
"""Packaging smoke tests (E1.1.1): install, version, wx guard."""

import importlib.metadata
import re
import sys

import pytest

import rivercrossing


def test_rivercrossing_package_import_succeeds() -> None:
    """The editable-installed package imports under its own name."""
    assert rivercrossing.__name__ == "rivercrossing"


def test_version_attribute_matches_installed_metadata_version() -> None:
    """``__version__`` and the installed distribution version agree."""
    installed_version = importlib.metadata.version("rivercrossing")
    assert installed_version == rivercrossing.__version__


def test_version_attribute_has_valid_pep440_release_segments() -> None:
    """``__version__`` parses as three non-negative integer segments."""
    segments = [int(part) for part in rivercrossing.__version__.split(".")]
    assert len(segments) == 3
    assert all(segment >= 0 for segment in segments)


def test_ui_package_imports_successfully_when_wx_available() -> None:
    """rivercrossing.ui imports cleanly when wxPython is installed."""
    module = importlib.import_module("rivercrossing.ui")
    assert module.__name__ == "rivercrossing.ui"


class _BlockWxFinder:
    """Meta path finder that fails any ``wx``/``wx.*`` import.

    Simulates a missing wxPython installation without uninstalling
    the real package -- a wxWidgets C++ assertion can abort the
    interpreter, so tests must never touch the real install.
    """

    def find_spec(self, fullname: str, _path: object, _target: object | None = None) -> None:
        """Raise for ``wx``/``wx.*``; return for everything else."""
        if fullname == "wx" or fullname.startswith("wx."):
            raise ModuleNotFoundError(f"blocked for test: {fullname}")


def test_ui_import_without_wx_raises_wx_unavailable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blocking every ``wx`` import forces the named, actionable error.

    ``rivercrossing.ui`` and every cached ``wx``/``wx.*`` module are
    dropped from ``sys.modules`` first so the guarded import in
    ``rivercrossing/ui/__init__.py`` actually re-runs; a re-executed
    module defines a new class object each time, so the exception is
    matched by its stable ``ImportError`` base plus its concrete type
    name rather than by a stale class reference held from an earlier
    import.
    """
    stale_modules = [name for name in sys.modules if name == "wx" or name.startswith("wx.")]
    stale_modules.append("rivercrossing.ui")
    for name in stale_modules:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BlockWxFinder(), *sys.meta_path])

    # logic-coverage-exempt: T-12 pins ImportError, not
    # WxUnavailableError, because forcing rivercrossing.ui to re-run
    # defines a new class object each time (verified empirically); a
    # class reference captured before the forced reimport would not
    # match the reimported exception via isinstance. The concrete
    # type is still asserted below, by name.
    with pytest.raises(ImportError, match=re.escape("wxPython~=4.3.1")) as exc_info:
        importlib.import_module("rivercrossing.ui")

    assert type(exc_info.value).__name__ == "WxUnavailableError"
