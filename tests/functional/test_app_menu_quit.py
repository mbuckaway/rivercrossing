# SPDX-License-Identifier: GPL-3.0-only
"""One whole-app functional test: open the real app and Quit it (R-51).

This is the one permitted functional test. It builds the whole app
through the real bootstrap -- the real ``RiverCrossingApp``, the real
main frame, the real store -- shows the window, and dispatches the
app-menu Quit (``wxID_EXIT``).

Menu dispatch is the one interaction ``docs/FUNCTIONAL-TESTING.md``
section 11 allows direct ``ProcessEvent`` injection for, hence the
``_via_injection`` suffix. The quit confirm is modal, so a
``wx.ModalDialogHook`` answers it -- and records the copy it carried --
instead of ever letting it block the run.
"""

import gc
import time
from typing import TYPE_CHECKING

import pytest
import wx

from rivercrossing.ui import app as app_module
from rivercrossing.ui import quit_flow

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from rivercrossing.store import Store

pytestmark = pytest.mark.functional

# Bounded idle drain: wx's own UpdateUI chatter reports pending idle
# work on almost every pass, so the flush needs a cap to stay finite.
_DRAIN_ROUNDS = 5


def _drain(app: wx.App) -> None:
    """Pump the event queue, then flush wx's deferred window deletions.

    wx frees a ``Destroy()``d top-level window only during idle
    processing, and idle only runs once the event queue drains -- so
    the queue is yielded and then ``ProcessIdle`` is driven directly,
    bounded. A throwaway event loop is created only when no loop is
    active (this suite never enters ``MainLoop``).
    """
    wx.SafeYield()
    loop = wx.EventLoopBase.GetActive()
    if loop is None:
        loop = app.GetTraits().CreateEventLoop()
        activator = wx.EventLoopActivator(loop)
    else:
        activator = None
    loop.YieldFor(wx.EVT_CATEGORY_ALL)
    for _ in range(_DRAIN_ROUNDS):
        if not loop.ProcessIdle():
            break
    del activator


# How long the shown app is left on screen to draw before the test acts.
_SETTLE_SECONDS = 3.0


def _settle(app: wx.App, *, seconds: float) -> None:
    """Pump the event loop for *seconds* so the shown frame draws first.

    The whole-app bootstrap never enters ``MainLoop``, so a shown
    frame's paint events only run while the queue is pumped. Keeping
    the app on screen this long lets it render before the test acts.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _drain(app)
        time.sleep(0.02)


class _RecordingQuitHook(wx.ModalDialogHook):
    """Answer the quit confirm with OK, recording its copy."""

    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.message: str | None = None
        self.ok_label: str | None = None
        self.cancel_label: str | None = None

    def Enter(self, dialog: wx.MessageDialog) -> int:  # noqa: N802 -- wx override name
        """Record the frozen copy; return OK so it never blocks."""
        self.title = dialog.GetTitle()
        self.message = dialog.GetMessage()
        self.ok_label = dialog.GetOKLabel()
        self.cancel_label = dialog.GetCancelLabel()
        return wx.ID_OK

    def Exit(self, dialog: wx.Dialog) -> None:  # noqa: N802 -- wx override name
        """Do nothing: the hook never let the dialog be shown."""


@pytest.fixture
def whole_app(tmp_path: Path) -> Iterator[tuple[wx.App, wx.Frame, Store]]:
    """Build, show, and tear down the whole real app for one test."""
    app = app_module.build_app()
    wx.Log.SetActiveTarget(wx.LogStderr())
    frame, store = app_module._bootstrap_window(
        app,
        db_path=tmp_path / "rides.db",
        settings_path=tmp_path / "settings.json",
    )
    frame.Show()
    app.SetTopWindow(frame)

    yield app, frame, store

    app.really_quitting = True
    for window in wx.GetTopLevelWindows():
        window.Close(force=True)
    _drain(app)
    store.close()
    gc.collect()


def test_app_menu_quit_confirms_via_injection(
    whole_app: tuple[wx.App, wx.Frame, Store],
) -> None:
    """Quit confirms with the frozen copy, then quits for real."""
    app, frame, _store = whole_app

    assert frame.GetMenuBar().FindItem(wx.ID_EXIT) is not None

    hook = _RecordingQuitHook()
    hook.Register()
    frame_handle = frame.GetHandle()

    # Leave the app on screen to draw before driving the close.
    _settle(app, seconds=_SETTLE_SECONDS)

    frame.GetEventHandler().ProcessEvent(
        wx.CommandEvent(wx.EVT_MENU.typeId, wx.ID_EXIT),
    )

    assert app.really_quitting is True
    assert hook.title == quit_flow.EXIT_CONFIRM_TITLE
    assert hook.message == quit_flow.EXIT_CONFIRM_MESSAGE
    assert hook.ok_label == quit_flow.EXIT_CONFIRM_OK_LABEL
    assert hook.cancel_label == quit_flow.EXIT_CONFIRM_CANCEL_LABEL

    _drain(app)
    assert frame_handle not in {window.GetHandle() for window in wx.GetTopLevelWindows()}
