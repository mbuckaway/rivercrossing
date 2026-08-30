# SPDX-License-Identifier: GPL-3.0-only
"""Packaging smoke tests (E1.1.1): install, version, wx guard."""

import importlib.metadata
import re
import sys
from types import ModuleType

import pytest

import rivercrossing
from rivercrossing.ui import WxUnavailableError, require_wx


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


def _block_wx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop any cached ``wx``/``wx.*`` module and reject re-import."""
    stale_modules = [name for name in sys.modules if name == "wx" or name.startswith("wx.")]
    for name in stale_modules:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BlockWxFinder(), *sys.meta_path])


def test_ui_import_succeeds_when_wx_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing rivercrossing.ui never touches wx; blocking it is moot.

    ``rivercrossing.ui`` is dropped from ``sys.modules`` first, forcing
    a genuine re-execution of ``ui/__init__.py`` (not a cache hit)
    while wx is blocked -- proving the module's own top-level code
    never imports wx, rather than merely observing that an earlier,
    already-successful import is still cached.
    """
    monkeypatch.delitem(sys.modules, "rivercrossing.ui", raising=False)
    _block_wx(monkeypatch)

    module = importlib.import_module("rivercrossing.ui")

    assert module.__name__ == "rivercrossing.ui"


def test_require_wx_with_wx_blocked_raises_wx_unavailable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """require_wx() names the pinned dependency when wx cannot import.

    ``require_wx`` and ``WxUnavailableError`` are imported once, at
    this module's top, and stay bound to those same objects for the
    whole test run: unlike the eager package-level guard this
    replaces, nothing here forces ``rivercrossing.ui`` itself to
    re-execute, so there is no stale-class-identity concern to work
    around -- the raised exception really is an instance of the class
    already bound above.
    """
    _block_wx(monkeypatch)

    with pytest.raises(WxUnavailableError, match=re.escape("wxPython~=4.3.1")):
        require_wx()


def test_require_wx_with_wx_available_returns_the_wx_module() -> None:
    """require_wx() hands back the real wx module when installed."""
    wx_module = require_wx()

    assert isinstance(wx_module, ModuleType)
    assert wx_module.__name__ == "wx"


def test_platformdirs_runtime_dependency_is_installed() -> None:
    """The E8 settings store's runtime dependency is pinned (E8.0).

    ``platformdirs`` backs ``settings.default_path()`` (E8.1.1); the
    distribution must be present in the install so the settings store
    resolves a real per-user config directory on both platforms. The
    API call itself is asserted too -- ``user_config_dir`` is the
    stable public entry point the store calls, and the computed path
    is never created here (pure path arithmetic, no desktop I/O).
    """
    installed_version = importlib.metadata.version("platformdirs")
    assert re.match(r"^\d+\.\d+", installed_version) is not None

    import platformdirs  # noqa: PLC0415 -- dev-time dependency check

    path = platformdirs.user_config_dir("RiverCrossing")
    assert path
    assert platformdirs.__version__ == installed_version
