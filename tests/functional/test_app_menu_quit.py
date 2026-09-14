# SPDX-License-Identifier: GPL-3.0-only
"""Whole-app smoke: create a DRAFT ride, import a CSV, quit.

The one permitted functional test; its contract is
``docs/FUNCTIONAL-TESTING.md``. It builds the whole app through the
real bootstrap -- the real ``RiverCrossingApp``, the real main frame,
the real store -- shows the window, then drives the operator's path:

1. Ride > New Ride... -- fill the setup dialog and Save, so a real
   DRAFT ride is created and the console switches onto it (this
   enables the two rows below, and decorates the simulator);
2. File > Import Riders CSV... -- the ``csv_preview_dlg`` preview and
   its Import button (the shared GORBA registration fixture);
3. File > Simulation... -- the ``simulation_dlg`` dialog opens and is
   then cancelled; the simulation itself is not run;
4. Exit -- the app-menu Quit, its confirm, and a clean shutdown.

Each step is followed by a fixed 1.5 s pause that keeps pumping the
event loop, so every window and dialog it opens is fully drawn first.

Interaction, per ``docs/FUNCTIONAL-TESTING.md``: menu rows and dialog
buttons use ``ProcessEvent`` injection (section 11's exceptions --
menu dispatch, and the case where real OS input is unavailable). Real
``wx.UIActionSimulator`` input was probed and does not land here: a
terminal-launched window never activates (section 7), so
``GetActiveWindow()`` is ``None`` and synthetic clicks go nowhere. The
modals are shown by the hook (``wx.ID_NONE``) so their buttons run for
real, with a bounded ``EndModal`` net so no modal can hang the run; the
native quit confirm is answered directly.
"""

import gc
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import wx
import wx.xrc

from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids, quit_flow
from rivercrossing.ui.views import rider_editor

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rivercrossing.store import Store

pytestmark = pytest.mark.functional

# Bounded idle drain: wx's own UpdateUI chatter reports pending idle
# work on almost every pass, so the flush needs a cap to stay finite.
_DRAIN_ROUNDS = 5

# How long the shown app is left on screen to draw before step one.
_SETTLE_SECONDS = 3.0

# The per-step pause: keeps pumping the loop so the code draws each
# window and dialog before the next step acts.
_PAUSE_SECONDS = 1.5

# A safety net: if a posted button event ever fails to close a shown
# modal, the fallback ends it so the smoke can never hang the run.
_MODAL_FALLBACK_MS = 1500

# The CSV the import step drives, straight from the shared fixtures.
_IMPORT_CSV = Path(__file__).resolve().parents[1] / "unit" / "fixtures" / "csv" / "gorba_epic.csv"

# The DRAFT ride the setup step creates. The duration and minimum lap
# time the operator supplied are set explicitly.
_RIDE_NAME = "Smoke DRAFT Ride"
_RIDE_VENUE = "Smoke Test Venue"
_RIDE_ORGANIZER = "Smoke Organizer"
_RIDE_SCORER = "Smoke Scorer"
_RIDE_LAP_KM = 8.0
_RIDE_DURATION = "6:00"
_RIDE_MIN_LAP = "30:00"


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


def _pause(app: wx.App, *, seconds: float = _PAUSE_SECONDS) -> None:
    """Pump the event loop for *seconds* so the screen is drawn first.

    The whole-app bootstrap never enters ``MainLoop``, so a shown
    window's paint events only run while the queue is pumped. Keeping
    the app on screen this long lets it render before the next step.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _drain(app)
        time.sleep(0.02)


def _dispatch_menu(frame: wx.Frame, item_id: str) -> None:
    """Fire a section 15 menu row by id (the section 11 exception)."""
    frame.GetEventHandler().ProcessEvent(
        wx.CommandEvent(wx.EVT_MENU.typeId, wx.xrc.XRCID(item_id)),
    )


def _press_button(window: wx.Window, button_id: int) -> None:
    """Click the button with *button_id* by posting its own event."""
    button = window.FindWindowById(button_id)
    if button is not None:
        wx.PostEvent(button, wx.CommandEvent(wx.EVT_BUTTON.typeId, button_id))


def _fill_and_save_ride(dialog: wx.Dialog) -> None:
    """Fill the ride-setup dialog's fields, then press Save."""
    dialog.FindWindowByName(ids.NAME_INPUT).SetValue(_RIDE_NAME)
    dialog.FindWindowByName(ids.VENUE_INPUT).SetValue(_RIDE_VENUE)
    dialog.FindWindowByName(ids.ORGANIZER_INPUT).SetValue(_RIDE_ORGANIZER)
    dialog.FindWindowByName(ids.SCORER_INPUT).SetValue(_RIDE_SCORER)
    dialog.FindWindowByName(ids.LAP_KM_SPIN).SetValue(_RIDE_LAP_KM)
    dialog.FindWindowByName(ids.DURATION_INPUT).SetValue(_RIDE_DURATION)
    dialog.FindWindowByName(ids.MIN_LAP_INPUT).SetValue(_RIDE_MIN_LAP)
    wx.CallAfter(_press_button, dialog, wx.ID_OK)


def _end_modal(dialog: wx.Dialog, return_id: int) -> None:
    """End *dialog* if a posted button did not -- a no-hang net."""
    if not dialog.IsBeingDeleted() and dialog.IsModal():
        dialog.EndModal(return_id)


class _SmokeDialogHook(wx.ModalDialogHook):
    """Drive every modal the smoke opens; never let one block the run.

    The flow dialogs are shown (``wx.ID_NONE``) so their buttons are
    really driven, with a bounded ``EndModal`` fallback behind each; the
    native quit confirm is answered OK without being shown.
    """

    def __init__(self) -> None:
        """Start with nothing seen and nothing recorded."""
        super().__init__()
        self.seen: list[str] = []
        self.quit_title: str | None = None
        self.quit_message: str | None = None
        self.quit_ok_label: str | None = None
        self.quit_cancel_label: str | None = None
        # The armed no-hang net for the modal shown; Exit stops it so
        # it can never act on a dialog already destroyed.
        self._fallback: wx.CallLater | None = None

    def Enter(self, dialog: wx.Dialog) -> int:  # noqa: N802 -- wx name
        """Show-and-drive the flow dialogs; answer the quit confirm."""
        name = dialog.GetName()
        self.seen.append(name)
        if name == ids.RIDE_SETUP_DLG:
            wx.CallAfter(_fill_and_save_ride, dialog)
            self._arm_fallback(dialog, wx.ID_CANCEL)
            return wx.ID_NONE
        if name == ids.CSV_PREVIEW_DLG:
            wx.CallAfter(_press_button, dialog, wx.ID_OK)
            self._arm_fallback(dialog, wx.ID_OK)
            return wx.ID_NONE
        if name == ids.SIMULATION_DLG:
            wx.CallAfter(_press_button, dialog, wx.ID_CANCEL)
            self._arm_fallback(dialog, wx.ID_CANCEL)
            return wx.ID_NONE
        # The native quit confirm has no frozen name; record its copy
        # and answer it so the quit proceeds.
        self.quit_title = dialog.GetTitle()
        self.quit_message = dialog.GetMessage()
        self.quit_ok_label = dialog.GetOKLabel()
        self.quit_cancel_label = dialog.GetCancelLabel()
        return wx.ID_OK

    def Exit(self, dialog: wx.Dialog) -> None:  # noqa: N802, ARG002 -- wx name
        """Cancel the armed fallback: the modal ended on its own."""
        if self._fallback is not None:
            self._fallback.Stop()
            self._fallback = None

    def _arm_fallback(self, dialog: wx.Dialog, return_id: int) -> None:
        """Arm the no-hang net; :meth:`Exit` stops it when it ends."""
        self._fallback = wx.CallLater(_MODAL_FALLBACK_MS, _end_modal, dialog, return_id)


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


def test_app_menu_import_simulate_quit_via_injection(
    whole_app: tuple[wx.App, wx.Frame, Store],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create a DRAFT ride, import a CSV, open the simulator, quit."""
    app, frame, _store = whole_app

    hook = _SmokeDialogHook()
    hook.Register()

    # The import step drives the real picker->preview->Import flow; the
    # native picker is the document's own monkeypatch seam.
    monkeypatch.setattr(rider_editor, "_pick_import_path", lambda _parent: _IMPORT_CSV)

    # Let the shown app draw before the first step.
    _pause(app, seconds=_SETTLE_SECONDS)

    # 1. Create a DRAFT ride (Ride > New Ride... -> Save), so the next
    #    two menu rows are enabled and the simulator is decorated.
    _dispatch_menu(frame, ids.MI_NEW_RIDE)
    _pause(app)
    assert frame.FindWindowByName(ids.RIDE_NAME_VALUE).GetValue() == _RIDE_NAME

    # 2. File > Import Riders CSV... -> csv_preview_dlg -> Import.
    _dispatch_menu(frame, ids.MI_IMPORT_CSV)
    _pause(app)

    # 3. File > Simulation... -> simulation_dlg opens -> Cancel.
    _dispatch_menu(frame, ids.MI_SIMULATION)
    _pause(app)

    # 4. Exit -> the quit confirm -> quit.
    _dispatch_menu(frame, "wxID_EXIT")
    _pause(app)

    # Every flow dialog opened; the confirm carried the frozen copy.
    assert ids.RIDE_SETUP_DLG in hook.seen
    assert ids.CSV_PREVIEW_DLG in hook.seen
    assert ids.SIMULATION_DLG in hook.seen
    assert hook.quit_title == quit_flow.EXIT_CONFIRM_TITLE
    assert hook.quit_message == quit_flow.EXIT_CONFIRM_MESSAGE
    assert hook.quit_ok_label == quit_flow.EXIT_CONFIRM_OK_LABEL
    assert hook.quit_cancel_label == quit_flow.EXIT_CONFIRM_CANCEL_LABEL

    # The application has completely exited: the quit was confirmed and
    # no top-level window -- the frame or any dialog -- is left.
    assert app.really_quitting is True
    _drain(app)
    assert len(wx.GetTopLevelWindows()) == 0
