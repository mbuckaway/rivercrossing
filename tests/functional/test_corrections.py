# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for the E7.2.1 correction dialogs and menu routes.

The six correction dialogs (edit_crossing_dlg in add + edit modes,
reassign_dlg, manual_deal_dlg, dnf_confirm_dlg, void_card_confirm_dlg)
and the Cards/Riders menu routes that open them are E7.2.1's wiring:
the dialogs are shared by the entry-detail buttons and the menu, each
requires a non-empty reason, and a confirmed submission runs the
matching ``RideEngine`` correction command.

What only a real, loaded wx session can prove lives here (the rules
themselves are ``commands.py``/``presenters/detail.py`` unit-tested):

* the frozen §15b control names resolve inside each dialog;
* the message helpers name the object and are never blank
  (UX-DESKTOP §4), and the runner writes them;
* edit_crossing_dlg's add-vs-edit prefill (title, plate, time,
  void_btn visibility) is what the canvas promises;
* the destructive confirms (void card, DNF) default + focus Cancel;
* Escape cancels without acting (R-76);
* each §15 Cards-menu route, fired as a real ``EVT_MENU`` at a live
  app whose engine has entries, runs its dialog and applies its engine
  command -- the status bar confirms and the engine's audit trail
  records the correction.

Like the rest of ``tests/functional/`` these run only in the Tart VM,
never directly on the host (the suite opens real wx windows).
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import harness
import pages
import pytest
import wx
import wx.xrc

from rivercrossing.cards import Shoe
from rivercrossing.ride import RideConfig, RideEngine
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids, theme
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EngineDataSource
from rivercrossing.ui.views import MainFrame, corrections, dialogs

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.functional

_TIMEOUT_SENTINEL = -999

# The §15 correction rows and the dialogs they open, for the
# route-exercises-its-command tests below.
_ROUTE_CASES = (
    (ids.MI_ADD_CROSSING_AT, ids.EDIT_CROSSING_DLG, "Crossing added"),
    (ids.MI_DEAL_MANUAL, ids.MANUAL_DEAL_DLG, "Card dealt"),
)

_DIALOG_CONTROLS = (
    (ids.EDIT_CROSSING_DLG, (ids.PLATE_INPUT, ids.TIME_PICKER, ids.REASON_INPUT, ids.VOID_BTN)),
    (ids.REASSIGN_DLG, (ids.CROSSING_LBL, ids.NEW_PLATE_INPUT, ids.REASON_INPUT)),
    (ids.MANUAL_DEAL_DLG, (ids.PLATE_INPUT, ids.REASON_INPUT)),
    (ids.DNF_CONFIRM_DLG, (ids.ENTRY_LBL, ids.REASON_INPUT)),
    (ids.VOID_CARD_CONFIRM_DLG, (ids.CARD_LBL, ids.REASON_INPUT)),
)

# ------------------------------------------------------------ helpers


def _show(xrc_resource: object, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Load, show and pump *name* from *resource*."""
    dialog = harness.load_window_verified(xrc_resource, name, frame=False)
    try:
        dialog.Show()
        harness.pump()
    except Exception:
        harness.close_window(dialog)
        raise
    return dialog


def _send_escape(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Post a real Escape ``CHAR_HOOK`` at *dialog* (proven to work)."""
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_ESCAPE)
    dialog.GetEventHandler().ProcessEvent(event)


def _end_modal_if_undecided(dialog: Any) -> None:  # noqa: ANN401
    """Fire the safety-net ``EndModal`` only when nothing else has."""
    if not dialog.IsModal() or dialog.GetReturnCode() != 0:
        return
    dialog.EndModal(_TIMEOUT_SENTINEL)


def _run_with_action(dialog: Any, action: Callable[[], None]) -> int:  # noqa: ANN401
    """Run *action* while scheduling it once the modal loop pumps."""
    wx.CallAfter(action)
    wx.CallAfter(_end_modal_if_undecided, dialog)
    return int(dialog.ShowModal())


# ---------------------------- live app the routes fire at


def _build_live_context(
    xrc_resource: object, wx_app: object
) -> tuple[app_module._RouteContext, RideEngine, Roster]:
    """Build a live app context whose engine has two entries.

    Loads a real ``main_frame`` with its menubar, seeds a RUNNING
    engine (two solo entries, one recorded crossing), wires a live
    console + presenter, and threads a ``_RouteContext`` exactly like
    ``build_main_window`` does -- so the correction route handlers act
    on real data, and tests can set ``context.detail_plate`` for the
    routes that target "the current entry".
    """
    from rivercrossing.ui import app as app_mod  # noqa: PLC0415 -- module already imported

    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(name="Rider 12", plate="12")
    roster.create_solo_entry(name="Rider 34", plate="34")
    config = RideConfig(
        name="GORBA EPIC 2026",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive, RideConfig's own contract
        planned_duration_s=21600,
        min_lap_s=1,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    import datetime as _dt  # noqa: PLC0415 -- local helper import, matching test_console_live

    engine = RideEngine(
        config=config,
        shoe=shoe,
        clock=lambda: _dt.datetime(2026, 9, 20, 12, 0),  # noqa: DTZ001 -- naive, RideConfig's contract
        roster=roster,
    )
    engine.start()
    engine.record_crossing("12", at=datetime(2026, 9, 20, 10, 30))  # noqa: DTZ001
    source = EngineDataSource(engine, roster)
    frame = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    menubar = harness.load_menubar(xrc_resource, ids.MAIN_MENUBAR)
    frame.SetMenuBar(menubar)
    frame.Show()
    frame.Layout()
    harness.pump()
    console = MainFrame(frame, data_source=source, resource=xrc_resource)
    presenter = ConsolePresenter(console, engine=engine, source=source)
    console.wire_entry(presenter.on_plate_entered)
    console.wire_console(presenter)
    console.set_state(source.ride_status())
    context = app_mod._RouteContext(
        frame=frame,
        resource=xrc_resource,
        roster=roster,
        app=wx_app,
        theme_controller=theme.ThemeController(wx_app),
        presenter=presenter,
        console_view=console,
        detail_plate="12",
    )
    app_mod._bind_routes(context)
    app_mod._apply_menu_state(context, engine.state)
    return context, engine, roster


def _schedule_drive(dialog_name: str, drive: Callable[[Any], None]) -> None:
    """Schedule *drive* on the modal *dialog_name* once it opens."""

    def _run() -> None:
        dialog = wx.Window.FindWindowByName(dialog_name)
        if dialog is not None:
            drive(dialog)

    wx.CallAfter(_run)


def _schedule_end_modal_if_undecided(dialog_name: str) -> None:
    """Schedule the safety-net EndModal for the modal *dialog_name*."""

    def _run() -> None:
        dialog = wx.Window.FindWindowByName(dialog_name)
        if dialog is not None and dialog.IsModal() and dialog.GetReturnCode() == 0:
            dialog.EndModal(_TIMEOUT_SENTINEL)

    wx.CallAfter(_run)


@pytest.fixture(scope="module")
def live_context(
    xrc_resource: object, wx_app: object
) -> Iterator[tuple[app_module._RouteContext, RideEngine, Roster]]:
    """One live app context every route test shares."""
    context, engine, roster = _build_live_context(xrc_resource, wx_app)
    try:
        yield context, engine, roster
    finally:
        harness.close_window(context.frame)


# ------------------- §15b names resolve per correction dialog


@pytest.mark.parametrize(
    ("dialog_name", "expected_controls"),
    _DIALOG_CONTROLS,
    ids=lambda name: name,
)
def test_correction_dialog_resolves_its_frozen_controls(
    dialog_name: str, expected_controls: tuple[str, ...], xrc_resource: object
) -> None:
    """Every §15b-registered name resolves in the correction dialog."""
    dialog = _show(xrc_resource, dialog_name)

    try:
        resolved = {
            name: harness.find_control(dialog, name).GetName() for name in expected_controls
        }
    finally:
        harness.close_window(dialog)

    assert resolved == {name: name for name in expected_controls}


# ------------------------- message helpers name the object, never blank


@pytest.mark.parametrize(
    ("helper", "args", "needle"),
    [
        (dialogs.void_card_message, ("9H", "45 · J. Okafor"), "45 · J. Okafor"),
        (dialogs.dnf_message, ("212", "M. Chen"), "212 · M. Chen"),
        (dialogs.reassign_message, ("14:21:59", "45"), "14:21:59"),
    ],
    ids=lambda value: getattr(value, "__name__", value),
)
def test_correction_message_helper_never_blank_and_names_the_object(
    helper: Callable[..., str], args: tuple[str, str], needle: str
) -> None:
    """UX-DESKTOP §4: each confirm names its object, never blank."""
    label = helper(*args)

    assert label != ""
    assert needle in label


# --------------------------- edit-crossing add-vs-edit prefill


@pytest.mark.parametrize(
    ("adding", "expected_title", "void_shown"),
    [(True, "Add Crossing at Time", False), (False, "Edit Crossing", True)],
    ids=["add_mode", "edit_mode"],
)
def test_edit_crossing_dialog_add_vs_edit_prefill(  # noqa: PLR0913 -- (xrc_resource, monkeypatch, adding, expected_title, void_shown)
    xrc_resource: object,
    monkeypatch: pytest.MonkeyPatch,
    *,
    adding: bool,
    expected_title: str,
    void_shown: bool,
) -> None:
    """One dialog, two titles: prefill + void_btn visibility per mode.

    ``dialogs.run_dialog`` is monkeypatched to capture the dialog's
    state and return immediately -- ``ShowModal`` would block forever
    with no user present (the test_app_open_target precedent).
    """
    captured: dict[str, object] = {}

    def fake_run(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001 -- wx ships no stubs
        captured["title"] = dialog.GetTitle()
        captured["void_shown"] = harness.find_control(dialog, ids.VOID_BTN).IsShown()
        captured["plate"] = harness.find_control(dialog, ids.PLATE_INPUT).GetValue()
        captured["time"] = harness.find_control(dialog, ids.TIME_PICKER).GetValue()
        return wx.ID_CANCEL

    monkeypatch.setattr(dialogs, "run_dialog", fake_run)
    frame = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    try:
        result = corrections.run_edit_crossing(
            xrc_resource,
            frame=frame,
            adding=adding,
            plate="12",
            time="10:45:00",
            seq=1,
            base_date=date(2026, 9, 20),
        )
    finally:
        harness.close_window(frame)

    assert result is None  # cancelled -- no command ran
    assert captured["title"] == expected_title
    assert captured["void_shown"] is void_shown
    assert captured["plate"] == "12"
    picked = captured["time"]
    assert (picked.GetHour(), picked.GetMinute(), picked.GetSecond()) == (10, 45, 0)


# ----------------------- destructive confirms default + focus Cancel


@pytest.mark.parametrize(
    "dialog_name",
    [ids.VOID_CARD_CONFIRM_DLG, ids.DNF_CONFIRM_DLG],
    ids=lambda name: name,
)
def test_destructive_correction_confirm_defaults_to_cancel(
    dialog_name: str, xrc_resource: object
) -> None:
    """A scoring change defaults + focuses Cancel: Enter is safe."""
    dialog = _show(xrc_resource, dialog_name)

    try:
        default_item = dialog.GetDefaultItem()
        default_name = default_item.GetName() if default_item is not None else None
    finally:
        harness.close_window(dialog)

    assert default_name == pages.WX_ID_CANCEL


@pytest.mark.parametrize(
    "dialog_name",
    [ids.EDIT_CROSSING_DLG, ids.REASSIGN_DLG, ids.MANUAL_DEAL_DLG],
    ids=lambda name: name,
)
def test_form_correction_dialog_escape_cancels(dialog_name: str, xrc_resource: object) -> None:
    """R-76: Esc cancels the correction forms without acting."""
    dialog = _show(xrc_resource, dialog_name)

    try:
        result = _run_with_action(dialog, lambda: _send_escape(dialog))
    finally:
        harness.close_window(dialog)

    assert result == wx.ID_CANCEL


# --------------------- each §15 Cards-menu route exercises its command


@pytest.mark.parametrize(
    ("item_id", "dialog_name", "ok_notice"),
    _ROUTE_CASES,
)
def test_cards_menu_route_runs_its_dialog_and_applies_its_command(  # noqa: PLR0913 -- (live_context, item_id, dialog_name, ok_notice)
    live_context: tuple[app_module._RouteContext, RideEngine, Roster],
    *,
    item_id: str,
    dialog_name: str,
    ok_notice: str,
) -> None:
    """Firing the menu item opens its dialog and runs the command.

    The dialog is driven by the harness's direct-injection mechanism:
    ``wx.CallAfter`` runs inside the modal loop, types the reason and
    posts the OK click, and a safety-net ``EndModal`` guarantees no
    hang. The status bar confirms the route reached the engine
    command -- not the "not yet implemented" stub.
    """
    context, engine, _roster = live_context
    frame = context.frame
    before = len(engine.events)

    def _drive(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        if dialog_name in (ids.EDIT_CROSSING_DLG, ids.MANUAL_DEAL_DLG):
            harness.type_text(dialog, ids.PLATE_INPUT, "12")
        harness.type_text(dialog, ids.REASON_INPUT, "test reason")
        harness.click(dialog, "wxID_OK")

    _schedule_drive(dialog_name, _drive)
    _schedule_end_modal_if_undecided(dialog_name)
    harness.fire_menu_event(frame, item_id)

    assert frame.GetStatusBar().GetStatusText(0) == ok_notice
    assert len(engine.events) == before + 1


def test_edit_crossing_menu_route_edits_the_current_entrys_latest_lap(
    live_context: tuple[app_module._RouteContext, RideEngine, Roster],
) -> None:
    """Edit Crossing… targets the current entry's latest lap."""
    context, engine, _roster = live_context
    frame = context.frame
    context.detail_plate = "12"
    before = len(engine.events)

    def _drive(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        harness.type_text(dialog, ids.REASON_INPUT, "mis-keyed time")
        harness.click(dialog, "wxID_OK")

    _schedule_drive(ids.EDIT_CROSSING_DLG, _drive)
    _schedule_end_modal_if_undecided(ids.EDIT_CROSSING_DLG)
    harness.fire_menu_event(frame, ids.MI_EDIT_CROSSING)

    assert frame.GetStatusBar().GetStatusText(0) == "Crossing edited"
    assert engine.events[-1].action == "edit_crossing"
    assert len(engine.events) == before + 1


def test_reassign_menu_route_reassigns_the_current_entrys_latest_lap(
    live_context: tuple[app_module._RouteContext, RideEngine, Roster],
) -> None:
    """Reassign Plate… names the crossing, moves it to a new plate."""
    context, engine, _roster = live_context
    frame = context.frame
    context.detail_plate = "12"
    before = len(engine.events)

    def _drive(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        label = harness.find_control(dialog, ids.CROSSING_LBL).GetLabelText()
        assert "10:30:00" in label  # the recorded crossing is named, never blank
        harness.type_text(dialog, ids.NEW_PLATE_INPUT, "34")
        harness.type_text(dialog, ids.REASON_INPUT, "mis-keyed plate")
        harness.click(dialog, "wxID_OK")

    _schedule_drive(ids.REASSIGN_DLG, _drive)
    _schedule_end_modal_if_undecided(ids.REASSIGN_DLG)
    harness.fire_menu_event(frame, ids.MI_REASSIGN_PLATE)

    assert frame.GetStatusBar().GetStatusText(0) == "Crossing reassigned"
    assert engine.events[-1].action == "reassign"
    assert len(engine.events) == before + 1


def test_mark_dnf_menu_route_marks_the_current_entry(
    live_context: tuple[app_module._RouteContext, RideEngine, Roster],
) -> None:
    """Mark DNF… names the entry and flips its status."""
    context, engine, roster = live_context
    frame = context.frame
    context.detail_plate = "12"
    before = len(engine.events)

    def _drive(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        label = harness.find_control(dialog, ids.ENTRY_LBL).GetLabelText()
        assert "12 · Rider 12" in label
        harness.type_text(dialog, ids.REASON_INPUT, "mechanical failure")
        harness.click(dialog, "wxID_OK")

    _schedule_drive(ids.DNF_CONFIRM_DLG, _drive)
    _schedule_end_modal_if_undecided(ids.DNF_CONFIRM_DLG)
    harness.fire_menu_event(frame, ids.MI_MARK_DNF)

    assert frame.GetStatusBar().GetStatusText(0) == "Entry marked DNF"
    assert engine.events[-1].action == "dnf"
    assert roster.resolve_plate("12").status.value == "dnf"
    assert len(engine.events) == before + 1
