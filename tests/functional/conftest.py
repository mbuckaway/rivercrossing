# SPDX-License-Identifier: GPL-3.0-only
"""Shared fixtures for the real-wx functional suite.

The ``wx_app`` fixture is the pattern ``test_cards_imagelist.py``
already established: a module-level ``functools.cache`` holds the
only strong reference to the process-wide ``wx.App``, because an
unbound ``wx.App()`` is collected as soon as the fixture that built
it goes out of scope and the interpreter then hangs at exit. Every
functional test module in this directory shares the one instance.
"""

from functools import cache
from typing import Any

import harness
import pytest

from rivercrossing.ui import require_wx


@cache
def _wx_app() -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Return the process-wide wx.App, creating it on first use.

    Measured: ``wx.xrc``'s ``LoadFrame``/``LoadDialog``/``LoadMenuBar``
    (and ``wx.Bitmap.SaveFile``) call ``wxLogError`` internally on
    failure, in addition to returning ``None``/``False``. With a real
    ``wx.App`` alive, that goes to the default GUI log target
    instead of stderr and queues rather than printing immediately;
    unless something shows or clears it, ``wxApp::CleanUp()`` tries
    to pop up a "Several errors occurred" dialog while the
    interpreter is exiting, and hangs forever with no user present
    to dismiss it. This suite deliberately drives those
    ``WindowLoadError``/``ScreenshotError`` paths (T-5's negative-path
    tests), so GUI logging is disabled for the whole session rather
    than patched around each call site.
    """
    wx = require_wx()
    app = wx.GetApp() or wx.App()
    wx.Log.EnableLogging(False)  # noqa: FBT003 -- wx's own positional bool
    return app


@pytest.fixture(scope="session")
def wx_app() -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Guarantee a live wx.App before any wx object is constructed."""
    return _wx_app()


@pytest.fixture(scope="session")
def xrc_resource(wx_app: Any) -> Any:  # noqa: ANN401, ARG001
    """Load every packaged ``.xrc`` file once for the whole session.

    Takes ``wx_app`` for ordering only: the app has to exist before
    ``wx.xrc.XmlResource`` decodes anything.
    """
    return harness.load_xrc_resources()
