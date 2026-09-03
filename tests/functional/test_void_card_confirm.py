# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for the E7.2.1 Void Card confirm flow.

Cards ▸ Void Card… (spec §15: "From entry detail, cards row →
``void_card_confirm_dlg`` (names the card, reason required) → voids
the dealt card"). This file pins the confirm-specific facts only a
real wx session can supply:

* ``card_lbl`` names the card + entry and is never blank (UX-DESKTOP
  §4) -- the helper's output and the runner's write of it;
* the confirm is a destructive scoring change: Cancel is the default
  + focused control (spec.md §13), and Escape cancels without acting;
* **void-card from a dealt cards row**: selecting a laps_list row in
  a live entry detail (over a real engine) and clicking Void card…
  opens the confirm naming that row's dealt card and its entry --
  the entry-detail button → presenter → view wiring;
* the Cards ▸ Void Card… menu route, fired at a live app, voids the
  current entry's latest dealt card on a confirmed OK.

Like the rest of ``tests/functional/`` these run only in the Tart VM,
never directly on the host.
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import harness
import pages
import pytest
import wx

from rivercrossing.cards import Shoe
from rivercrossing.ride import RideConfig, RideEngine
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids
from rivercrossing.ui.presenters.data_source import EngineDataSource
from rivercrossing.ui.views import corrections, dialogs
from rivercrossing.ui.views.entry_detail import EntryDetailDialog

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.functional

_TIMEOUT_SENTINEL = -999


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


def _build_live_entry_detail(
    xrc_resource: object,
) -> tuple[EntryDetailDialog, RideEngine]:
    """Build a live entry detail over a RUNNING engine with one lap."""
    import datetime as _dt  # noqa: PLC0415 -- local helper import, matching test_console_live

    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider", last_name="12", plate="12")
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
    window = harness.load_window_verified(xrc_resource, ids.ENTRY_DETAIL_DLG, frame=False)
    window.Show()
    harness.pump()
    view = EntryDetailDialog(
        window,
        "12",
        data_source=EngineDataSource(engine, roster),
        engine=engine,
        roster=roster,
        resource=xrc_resource,
    )
    return view, engine


# ----------------------------------- the naming copy is never blank


def test_void_card_message_never_blank_and_names_the_card_and_entry() -> None:
    """UX-DESKTOP §4: the confirm names card + entry, never blank."""
    label = dialogs.void_card_message("9H", "45 · J. Okafor")

    assert label != ""
    assert "9♥" in label  # the card renders with its suit glyph
    assert "45 · J. Okafor" in label


def test_void_card_confirm_runner_writes_the_card_label(
    xrc_resource: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_void_card writes card_lbl from the helper, never blank."""
    captured: dict[str, object] = {}

    def fake_run(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001 -- wx ships no stubs
        captured["card_lbl"] = harness.find_control(dialog, ids.CARD_LBL).GetLabelText()
        return wx.ID_CANCEL

    monkeypatch.setattr(dialogs, "run_dialog", fake_run)
    frame = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    try:
        result = corrections.run_void_card(
            xrc_resource,
            frame=frame,
            entry_id="45",
            card="9H",
            entry="45 · J. Okafor",
        )
    finally:
        harness.close_window(frame)

    assert result is None  # cancelled -- no command ran
    assert captured["card_lbl"] == "9♥ — 45 · J. Okafor"


# ------------------- destructive scoring change: Cancel default + focus


def test_void_card_confirm_defaults_to_cancel(xrc_resource: object) -> None:
    """A scoring change defaults + focuses Cancel: Enter is safe."""
    dialog = _show(xrc_resource, ids.VOID_CARD_CONFIRM_DLG)

    try:
        default_item = dialog.GetDefaultItem()
        default_name = default_item.GetName() if default_item is not None else None
    finally:
        harness.close_window(dialog)

    assert default_name == pages.WX_ID_CANCEL


def test_void_card_confirm_escape_cancels_without_acting(xrc_resource: object) -> None:
    """R-76: Esc cancels the void -- never the destructive path."""
    dialog = _show(xrc_resource, ids.VOID_CARD_CONFIRM_DLG)

    try:
        result = _run_with_action(dialog, lambda: _send_escape(dialog))
    finally:
        harness.close_window(dialog)

    assert result == wx.ID_CANCEL


# --------------------------- void-card from a dealt cards row


def test_entry_detail_void_button_targets_the_selected_dealt_card(
    xrc_resource: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting a laps row and clicking Void card… names that card.

    ``corrections.run_void_card`` is monkeypatched to capture what the
    entry-detail flow would show and return None (cancel) -- the modal
    would otherwise block with no user present; the assertion pins the
    presenter → view wiring: the selected row's dealt card and the
    entry label reach the confirm.
    """
    view, engine = _build_live_entry_detail(xrc_resource)
    dealt = engine.card_for(engine.crossings[0]).code()
    captured: dict[str, str] = {}

    def fake_void(  # noqa: PLR0913 -- (resource, frame, entry_id, card, entry): run_void_card's full signature
        _resource: object,
        *,
        frame: object,  # noqa: ARG001 -- the frame seam is not used by this spy
        entry_id: str,  # noqa: ARG001 -- the entry identity is not used by this spy
        card: str,
        entry: str,
    ) -> None:
        captured["card"] = card
        captured["entry"] = entry

    monkeypatch.setattr(corrections, "run_void_card", fake_void)
    try:
        harness.select_row(view.dialog, ids.LAPS_LIST, 0)
        harness.click(view.dialog, ids.VOID_CARD_BTN)
    finally:
        harness.close_window(view.dialog)

    assert captured["card"] == dealt
    assert captured["entry"] == "12 · Rider 12"


def test_cards_void_card_menu_route_voids_the_current_entrys_latest_card(
    live_context: tuple[app_module._RouteContext, RideEngine],
) -> None:
    """Cards ▸ Void Card… voids the current entry's latest dealt card.

    The confirm runs as a real modal (driven by the same
    direct-injection mechanism the harness documents); the audit trail
    records the void and the status bar confirms.
    """
    context, engine = live_context
    context.detail_plate = "12"
    before = len(engine.events)

    def _drive(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        card_lbl = harness.find_control(dialog, ids.CARD_LBL).GetLabelText()
        assert card_lbl != ""
        harness.type_text(dialog, ids.REASON_INPUT, "wrong card dealt")
        harness.click(dialog, "wxID_OK")

    _schedule_drive(ids.VOID_CARD_CONFIRM_DLG, _drive)
    _schedule_end_modal_if_undecided(ids.VOID_CARD_CONFIRM_DLG)
    harness.fire_menu_event(context.frame, ids.MI_VOID_CARD)

    assert context.frame.GetStatusBar().GetStatusText(0) == "Card voided"
    assert engine.events[-1].action == "void_card"
    assert engine.events[-1].payload["reason"] == "wrong card dealt"
    assert len(engine.events) == before + 1


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
) -> Iterator[tuple[app_module._RouteContext, RideEngine]]:
    """One live app context (seeded engine, real frame)."""
    import datetime as _dt  # noqa: PLC0415 -- local helper import

    from rivercrossing.ui import theme  # noqa: PLC0415 -- deferred, wx-touching
    from rivercrossing.ui.presenters.console import ConsolePresenter  # noqa: PLC0415
    from rivercrossing.ui.views import MainFrame  # noqa: PLC0415

    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider", last_name="12", plate="12")
    config = RideConfig(
        name="GORBA EPIC 2026",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001
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
    # The clock is fixed at 12:00 (start => actual_start 12:00); a
    # crossing at 12:01 laps 60s >= min_lap_s, so its card is DEALT,
    # not held -- the Void Card route needs a credited card.
    engine.record_crossing("12", at=datetime(2026, 9, 20, 12, 1))  # noqa: DTZ001
    source = EngineDataSource(engine, roster)
    frame = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    menubar = harness.load_menubar(xrc_resource, ids.MAIN_MENUBAR)
    frame.SetMenuBar(menubar)
    frame.Show()
    harness.pump()
    console = MainFrame(frame, data_source=source, resource=xrc_resource)
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
    # The menu-route tests fire real EVT_MENU events at this frame: bind
    # the §15 routes (and apply the live binder) exactly like
    # test_corrections' _build_live_context does, or the fired event has
    # no handler and the route silently does nothing.
    app_module._bind_routes(context)
    app_module._apply_menu_state(context, engine.state)
    try:
        yield context, engine
    finally:
        harness.close_window(frame)
