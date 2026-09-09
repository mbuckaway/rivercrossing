# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for entry detail's actions and the menu binder.

E7.2.1 wires ``entry_detail_dlg``'s action row (edit crossing / deal
card / void card / move rider / mark DNF / audit trail) through
``DetailPresenter``, and wires the live menu-enablement binder (the
missing E1.4.2 half) to the console's ride-state-change seam so the
§15 "Enabled when" cells hold in the app.

What only a real, loaded wx session can prove lives here:

* the six action buttons resolve inside the authored XRC;
* ``move_rider_btn`` is enabled for a rider_pooled team entry and
  disabled for a solo entry (spec §15's "pooled only");
* the correction menu items genuinely follow the ride state through
  the live binder: RUNNING-with-crossings enables Edit Crossing /
  Reassign Plate, a FINISHED ride disables them, and a REOPENED ride
  re-enables them -- asserted against the real ``wx.MenuBar``'s
  ``IsEnabled()``, not ``commands.is_route_enabled``.

Like the rest of ``tests/functional/`` these run only in the Tart VM,
never directly on the host.
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import harness
import pytest
import wx
import wx.xrc

from rivercrossing.cards import Shoe
from rivercrossing.ride import RideConfig, RideEngine, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids, theme
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EngineDataSource
from rivercrossing.ui.views import MainFrame
from rivercrossing.ui.views.entry_detail import EntryDetailDialog

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.functional

_ACTION_BUTTONS = (
    ids.EDIT_CROSSING_BTN,
    ids.DEAL_CARD_BTN,
    ids.VOID_CARD_BTN,
    ids.MOVE_RIDER_BTN,
    ids.DNF_BTN,
    ids.AUDIT_BTN,
)


def _build_engine(*, roster: Roster) -> tuple[RideEngine, EngineDataSource]:
    """Build a started engine over *roster* and its live source."""
    import datetime as _dt  # noqa: PLC0415 -- local helper import, matching test_console_live

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
    engine = RideEngine(
        config=config,
        shoe=shoe,
        clock=lambda: _dt.datetime(2026, 9, 20, 12, 0),  # noqa: DTZ001 -- naive, RideConfig's contract
        roster=roster,
    )
    engine.start()
    engine.record_crossing("12", at=datetime(2026, 9, 20, 10, 30))  # noqa: DTZ001
    return engine, EngineDataSource(engine, roster)


def _pooled_team_roster() -> Roster:
    """Build a MIXED rider_pooled roster with one team and one solo."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider", last_name="12", plate="12")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    return roster


def _live_entry_detail(
    xrc_resource: object, *, plate: str, roster: Roster
) -> tuple[EntryDetailDialog, RideEngine]:
    """Build a live entry detail for *plate* over a real engine."""
    engine, source = _build_engine(roster=roster)
    window = harness.load_window_verified(xrc_resource, ids.ENTRY_DETAIL_DLG, frame=False)
    try:
        window.Show()
        harness.pump()
        view = EntryDetailDialog(
            window,
            plate,
            data_source=source,
            engine=engine,
            roster=roster,
            resource=xrc_resource,
        )
    except BaseException:
        # Fault A (the E7.2.2 leak shape, dialog variant): a view ctor
        # raise after Show() must not leak the shown dialog.
        harness.close_window(window)
        raise
    else:
        return view, engine


# ---------------------------- the six action buttons resolve


@pytest.mark.parametrize("button_name", _ACTION_BUTTONS, ids=lambda name: name)
def test_entry_detail_action_buttons_resolve(button_name: str, xrc_resource: object) -> None:
    """Every §15b action-button name resolves in entry_detail_dlg."""
    window = harness.load_window_verified(xrc_resource, ids.ENTRY_DETAIL_DLG, frame=False)

    try:
        control = harness.find_control(window, button_name)
        assert control.GetName() == button_name
    finally:
        harness.close_window(window)


# ------------------------- move_rider_btn is pooled-only


def test_move_rider_button_enabled_for_a_pooled_team_entry(xrc_resource: object) -> None:
    """Spec §15: move_rider_btn lives only on pooled team entries."""
    view, _engine = _live_entry_detail(xrc_resource, plate="77", roster=_pooled_team_roster())

    try:
        enabled = harness.find_control(view.dialog, ids.MOVE_RIDER_BTN).IsEnabled()
    finally:
        harness.close_window(view.dialog)

    assert enabled is True


def test_move_rider_button_disabled_for_a_solo_entry(xrc_resource: object) -> None:
    """A solo entry has one fixed rider: the move button stays off."""
    roster = _pooled_team_roster()
    view, _engine = _live_entry_detail(xrc_resource, plate="12", roster=roster)

    try:
        enabled = harness.find_control(view.dialog, ids.MOVE_RIDER_BTN).IsEnabled()
    finally:
        harness.close_window(view.dialog)

    assert enabled is False


# ---------------------------- W11 F2b: the plate picker


def test_entry_detail_plate_choice_lists_the_roster_and_selects_the_open_plate(
    xrc_resource: object,
) -> None:
    """F2b: the choice lists the entries; the open one is selected."""
    roster = _pooled_team_roster()
    view, _engine = _live_entry_detail(xrc_resource, plate="77", roster=roster)

    try:
        choice = harness.find_control(view.dialog, ids.PLATE_CHOICE)
        plates = tuple(choice.GetItems())
        selection = choice.GetStringSelection()
    finally:
        harness.close_window(view.dialog)

    assert plates == ("12", "77")  # roster order: the solo, then the team
    assert selection == "77"


def test_entry_detail_plate_choice_pick_rerenders_the_picked_entry(
    xrc_resource: object,
) -> None:
    """F2b: picking a plate re-renders the dialog for that entry."""
    roster = _pooled_team_roster()
    view, _engine = _live_entry_detail(xrc_resource, plate="12", roster=roster)

    try:
        harness.select_choice(view.dialog, ids.PLATE_CHOICE, "77")
        header = harness.find_control(view.dialog, ids.ENTRY_HEADER_LBL).GetLabelText()
    finally:
        harness.close_window(view.dialog)

    assert header.startswith("Team")  # the pooled team's detail, not the solo's
    assert "2 riders" in header


def test_entry_detail_plate_choice_pick_records_the_current_entry(
    live_context: tuple[Any, RideEngine],
) -> None:
    """F2b: a pick becomes the current entry the menu routes target.

    The app wires ``on_plate_picked`` to ``context.detail_plate``, so
    after this modal ends the correction menu routes (Mark DNF,
    Reassign Plate, Void Card) act on the picked entry instead of
    refusing with "open an entry first". The dialog opens at plate 12
    (a real selection, the way the flagged seam opens it), the drive
    picks plate 34 inside the modal, then the assertion reads the
    recorded context.
    """
    context, _engine = live_context
    frame = context.frame
    context.detail_plate = "12"

    def _drive_pick(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        harness.select_choice(dialog, ids.PLATE_CHOICE, "34")
        harness.click(dialog, "wxID_CLOSE")

    harness.dismiss_modal(
        ids.ENTRY_DETAIL_DLG,
        dismiss_with=wx.ID_CLOSE,
        drive=_drive_pick,
    )
    harness.fire_menu_event(frame, ids.MI_ENTRY_DETAIL)

    assert context.detail_plate == "34"


# ---------------- the live binder follows ride state on the menubar


@pytest.fixture(scope="module")
def live_context(xrc_resource: object, wx_app: object) -> Iterator[tuple[Any, RideEngine]]:
    """Yield a live app context (seeded engine, real frame)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider", last_name="12", plate="12")
    roster.create_solo_entry(first_name="Rider", last_name="34", plate="34")
    engine, source = _build_engine(roster=roster)
    frame = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    try:
        menubar = harness.load_menubar(xrc_resource, ids.MAIN_MENUBAR)
        frame.SetMenuBar(menubar)
        frame.Show()
        harness.pump()
        console = MainFrame(frame, data_source=source)
        presenter = ConsolePresenter(console, engine=engine, source=source)
        console.wire_entry(presenter.on_plate_entered)
        console.wire_console(presenter)
        console.set_state(source.ride_status())
        context = app_module._RouteContext(
            frame=frame,
            resource=xrc_resource,
            roster=roster,
            app=wx_app,
            theme_controller=theme.ThemeController(wx_app),
            presenter=presenter,
            console_view=console,
        )
        app_module._bind_routes(context)
        # the seam the bootstrap wires; a state change re-applies the
        # binder
        console.set_on_ride_changed(lambda status: app_module._apply_menu_state(context, status))
    except BaseException:
        # Fault A (the E7.2.2 leak): a wire-up raise after Show() must
        # not leak the shown frame the fixture's finally never runs for.
        harness.release_main_window(wx_app, frame)
        raise
    try:
        yield context, engine
    finally:
        harness.release_main_window(wx_app, frame)


def _menu_item_enabled(frame: Any, item_id: str) -> bool:  # noqa: ANN401 -- wx ships no stubs
    """Return whether *item_id* is enabled on *frame*'s menubar."""
    item, _menu = frame.GetMenuBar().FindItem(wx.xrc.XRCID(item_id))
    if item is None:  # pragma: no cover -- every routed id is on main.xrc's menubar
        raise AssertionError(f"menu item {item_id} is not on the live menubar")
    return item.IsEnabled()


def test_menu_binder_enables_corrections_while_running_and_disables_on_finish(
    live_context: tuple[Any, RideEngine],
) -> None:
    """RUNNING-with-crossings enables; FINISHED disables (E7.2.1)."""
    context, engine = live_context
    frame = context.frame
    assert engine.state is RideStatus.RUNNING

    assert _menu_item_enabled(frame, ids.MI_EDIT_CROSSING) is True
    assert _menu_item_enabled(frame, ids.MI_REASSIGN_PLATE) is True
    assert _menu_item_enabled(frame, ids.MI_UNDO_CROSSING) is True

    context.presenter.on_finish()

    assert engine.state is RideStatus.FINISHED
    assert _menu_item_enabled(frame, ids.MI_EDIT_CROSSING) is False
    assert _menu_item_enabled(frame, ids.MI_REASSIGN_PLATE) is False
    assert _menu_item_enabled(frame, ids.MI_UNDO_CROSSING) is False


def test_menu_binder_reenables_corrections_when_reopened(
    live_context: tuple[Any, RideEngine],
) -> None:
    """REOPENED re-opens the corrections rows (spec §3, R-36)."""
    context, engine = live_context
    frame = context.frame
    context.presenter.on_finish()
    assert engine.state is RideStatus.FINISHED

    context.presenter.on_reopen()

    assert engine.state is RideStatus.REOPENED
    assert _menu_item_enabled(frame, ids.MI_EDIT_CROSSING) is True
    assert _menu_item_enabled(frame, ids.MI_REASSIGN_PLATE) is True
    assert _menu_item_enabled(frame, ids.MI_ADD_CROSSING_AT) is True
