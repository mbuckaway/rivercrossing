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
    ``wx.App`` alive that goes to the default GUI log target, which
    queues rather than printing; unless something shows or clears it,
    ``wxApp::CleanUp()`` tries to pop a "Several errors occurred"
    dialog while the interpreter exits and hangs forever with no user
    present to dismiss it. This suite deliberately drives those
    ``WindowLoadError``/``ScreenshotError`` paths, so the log target is
    redirected for the whole session.

    Redirected to stderr rather than disabled outright. Both avoid the
    exit-time modal -- measured, all three ways -- but disabling also
    silences genuine wx diagnostics for every later test, and wx
    reports plenty worth reading (a failed XRC load says exactly which
    resource name it could not find). Keeping the messages costs
    nothing and makes the next wx problem debuggable.
    """
    wx = require_wx()
    app = wx.GetApp() or wx.App()
    wx.Log.SetActiveTarget(wx.LogStderr())
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


@pytest.fixture(scope="session", autouse=True)
def _assert_no_surviving_windows(wx_app: Any) -> Any:  # noqa: ANN401
    """Fault A sweep: no top-level wx window may outlive the session.

    A dialog whose load+construct path failed before its test's own
    ``try/finally`` (the Fault A leak class) is rerun-masked by
    ``--reruns 2`` but stays fully alive in the worker; the reap pin
    only catches it if it later collides with a same-named lookup.
    This per-worker-process end-of-suite check makes the leak itself
    fail the run deterministically: ``wx.GetTopLevelWindows()`` must
    be empty when the worker's tests are done. Autouse and session-
    scoped so it runs in every worker with no test needing to ask for
    it; depending on ``wx_app`` orders teardown so the app (and its
    window registry) is still alive when the assertion runs -- this
    fixture is finalized before ``wx_app`` itself is.
    """
    yield wx_app
    wx = require_wx()
    survivors = wx.GetTopLevelWindows()
    assert not survivors, (
        f"functional session ended with {len(survivors)} top-level wx "
        f"window(s) still alive: {[w.GetName() for w in survivors]!r} -- "
        "a load+construct path leaked (Fault A); fix the leak, never waive it"
    )
