# SPDX-License-Identifier: GPL-3.0-only
"""E4.4.4 mini acceptance: the script IS the test (R-74's spirit).

One scripted 20-rider race runs end-to-end through the real console
UI -- the same ``MainFrame`` + ``ConsolePresenter`` +
``EngineDataSource`` wiring ``test_console_live.py`` drives -- on an
in-process harness (direct event injection per ``harness.py``, no
bare sleeps, no subprocess: this scenario builds one ``MainFrame``,
so the many-constructions address-reuse hazard the subprocess
scenarios isolate does not apply, and the modal dialogs are driven
with the suite's own ``wx.CallAfter`` + ``run_dialog`` technique).

The script, in order:

1. **Setup** -- a 20-rider roster (18 solos + one rider_pooled team),
   ``RideConfig`` with ``min_lap_s`` lowered to 60 s so simulated
   30 s laps flag, a seeded ``Shoe``, the engine, ``EngineDataSource``,
   ``MainFrame`` and ``ConsolePresenter`` wired as the app bootstrap
   wires them.
2. **Start** -- RUNNING state, entry enabled, stop disabled (R-35).
3. **60 crossings** typed through ``plate_input`` + Enter at fake-clock
   times, including two short laps (held cards), one undo, one held
   confirm and one held void, with counters tracked against the engine.
4. **Stop/continue** -- arm, confirm, entry locked and crossings
   refused; continue re-enables entry with ``actual_start`` unchanged.
5. **Finish** -- the ``mi_finish_ride`` menu route opens
   ``finish_confirm_dlg`` and, on confirm, runs ``presenter.on_finish``
   (the FINISH_GATE hook is consulted; the ride reaches FINISHED).
6. **Standings** -- ``standings.rank(engine.snapshot())`` equals the
   hand-verified literal fixture below.

**Fixture provenance.** The shoe is 8 decks x 2 jokers, Fisher-Yates
shuffled under seed ``20260920`` (spec section 4). Crossing #k deals
shoe deal index ``k - 1`` for ``k = 1..60``, except: the undo of
crossing #39 returns deal index 38 to the shoe front, so crossing #40
re-deals it (``Shoe.restitute``); crossings #59 and #60 are short laps
whose cards (deal 57 = ``QC``, deal 58 = ``2C``) are held -- ``QC`` is
confirmed into plate 1's hand, ``2C`` is voided. The credited hands
below therefore are:

- plate "1": deals 0 (``8C``), 19 (``6S``), 56 (``3C``), 57 (``QC``)
- every other entry: its three deals in order (team "21" credited via
  typed plates "21", "22", "21")

Ranking is by best hand (spec section 5): every pair sorts above every
high card; plate 1's four-card HIGH_CARD with 5 laps sorts above every
three-card HIGH_CARD (the partial-hand rule) but below all pairs.
Hand-verified from the card codes: winner plate 7 (``6C QH QS`` --
pair of queens), runner-up plate 4 (``JH 9S JH`` -- pair of jacks,
kicker 9), third plate 15 (``JC 6C JC`` -- pair of jacks, kicker 6),
mid-field plate 14 (``3D JK 5S`` -- the joker completes a pair of
fives) and plate 1 (``8C 6S 3C QC`` -- four-card high card).
"""

from datetime import date, datetime, timedelta
from typing import Any

import harness
import pytest
import wx
import wx.dataview

from rivercrossing.cards import Shoe
from rivercrossing.ride import RideConfig, RideEngine, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.standings import rank
from rivercrossing.ui import app as app_module
from rivercrossing.ui import feed_model, ids, theme
from rivercrossing.ui.presenters import console as console_module
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EngineDataSource
from rivercrossing.ui.views import MainFrame

pytestmark = pytest.mark.functional

# The shoe seed the whole race (and its standings) is deterministic on.
MINI_ACCEPTANCE_SEED = 20260920

# The scripted race: 60 (plate, t_seconds_after_start) crossings in
# strictly increasing t order. Waves 0-2 give every entry three 360 s
# laps; the tail gives plate "1" a normal lap then two 30 s laps that
# flag (R-34). Crossing #38 uses pooled rider plate "22", which the
# roster resolves to the team entry (R-16's rider-plate path).
SCRIPT: tuple[tuple[str, int], ...] = (
    ("1", 100),
    ("2", 110),
    ("3", 120),
    ("4", 130),
    ("5", 140),
    ("6", 150),
    ("7", 160),
    ("8", 170),
    ("9", 180),
    ("10", 190),
    ("11", 200),
    ("12", 210),
    ("13", 220),
    ("14", 230),
    ("15", 240),
    ("16", 250),
    ("17", 260),
    ("18", 270),
    ("21", 280),
    ("1", 460),
    ("2", 470),
    ("3", 480),
    ("4", 490),
    ("5", 500),
    ("6", 510),
    ("7", 520),
    ("8", 530),
    ("9", 540),
    ("10", 550),
    ("11", 560),
    ("12", 570),
    ("13", 580),
    ("14", 590),
    ("15", 600),
    ("16", 610),
    ("17", 620),
    ("18", 630),
    ("22", 640),
    ("1", 820),
    ("2", 830),
    ("3", 840),
    ("4", 850),
    ("5", 860),
    ("6", 870),
    ("7", 880),
    ("8", 890),
    ("9", 900),
    ("10", 910),
    ("11", 920),
    ("12", 930),
    ("13", 940),
    ("14", 950),
    ("15", 960),
    ("16", 970),
    ("17", 980),
    ("18", 990),
    ("21", 1000),
    ("1", 1180),
    ("1", 1210),
    ("1", 1240),
)

# Crossing #39 (plate "1" lap 3) is undone immediately after it is
# typed; its card (deal 38 = "4D") returns to the shoe front and
# crossing #40 re-deals it.
UNDO_SCRIPT_POSITION = 39

# The two short-lap cards held at the end of the script (deal 57 =
# crossing #59, deal 58 = crossing #60): "QC" is confirmed into plate
# 1's hand, "2C" is voided.
CONFIRM_CARD = "QC"
VOID_CARD = "2C"

# The hand-verified standings fixture (provenance in the module
# docstring): (place, plate, laps, best5 card codes, hand class).
EXPECTED_STANDINGS: tuple[tuple[int, str, int, tuple[str, ...], str], ...] = (
    (1, "7", 3, ("6C", "QH", "QS"), "PAIR"),
    (2, "4", 3, ("JH", "9S", "JH"), "PAIR"),
    (3, "15", 3, ("JC", "6C", "JC"), "PAIR"),
    (4, "18", 3, ("TS", "JK", "4S"), "PAIR"),
    (5, "16", 3, ("5H", "7C", "7S"), "PAIR"),
    (6, "14", 3, ("3D", "JK", "5S"), "PAIR"),
    (7, "10", 3, ("KS", "2S", "2S"), "PAIR"),
    (8, "1", 5, ("8C", "6S", "3C", "QC"), "HIGH_CARD"),
    (9, "11", 3, ("7D", "AH", "JS"), "HIGH_CARD"),
    (10, "2", 3, ("TH", "AH", "4D"), "HIGH_CARD"),
    (11, "12", 3, ("TS", "KS", "QS"), "HIGH_CARD"),
    (12, "3", 3, ("QD", "7H", "JC"), "HIGH_CARD"),
    (13, "21", 3, ("QD", "9D", "3D"), "HIGH_CARD"),
    (14, "13", 3, ("QC", "8S", "2H"), "HIGH_CARD"),
    (15, "5", 3, ("9C", "4C", "TD"), "HIGH_CARD"),
    (16, "17", 3, ("5H", "6C", "8H"), "HIGH_CARD"),
    (17, "9", 3, ("3D", "8S", "6S"), "HIGH_CARD"),
    (18, "8", 3, ("3H", "8C", "2C"), "HIGH_CARD"),
    (19, "6", 3, ("3S", "5C", "6H"), "HIGH_CARD"),
)


class _ScenarioClock:
    """An advanceable naive datetime clock for the scripted race."""

    def __init__(self, start: datetime) -> None:
        """Start at *start* (the planned start instant)."""
        self._now = start

    def __call__(self) -> datetime:
        """Return the current scenario time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock forward by *seconds*."""
        self._now = self._now + timedelta(seconds=seconds)


def _build_mini_console(
    xrc_resource: object,
) -> tuple[Any, MainFrame, ConsolePresenter, RideEngine, EngineDataSource, _ScenarioClock]:
    """Build a RUNNING live console over the 20-rider race engine.

    Returns ``(window, console, presenter, engine, source, clock)``,
    wired exactly as the app bootstrap wires the console (``wire_entry``
    + ``wire_console`` + ``set_state``) and with the ``mi_finish_ride``
    menu route bound through the app's own route plumbing (the E4.4.4
    finish wiring under test).
    """
    roster = Roster(
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        max_team_size=4,
    )
    for number in range(1, 19):
        roster.create_solo_entry(first_name=f"Rider {number:02d}", last_name="", plate=str(number))
    roster.create_team_entry(
        display_name="Team Alpha",
        riders=[
            Rider(first_name="Aya", last_name="Chen", plate="21"),
            Rider(first_name="Bo", last_name="Lin", plate="22"),
        ],
    )
    config = RideConfig(
        name="E4.4.4 Mini Acceptance",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- scenario clock is naive
        planned_duration_s=21600,
        min_lap_s=60,  # lowered so the 30 s simulated laps flag (R-34)
        entry_mode=roster.entry_mode,
        plate_model=roster.plate_model,
        max_team_size=roster.max_team_size,
    )
    shoe = Shoe(
        decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=MINI_ACCEPTANCE_SEED
    )
    clock = _ScenarioClock(config.planned_start)
    engine = RideEngine(config=config, shoe=shoe, clock=clock, roster=roster)
    engine.start()
    source = EngineDataSource(engine, roster)

    window = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    window.Show()
    window.Layout()
    harness.pump()
    console = MainFrame(window, data_source=source, resource=xrc_resource)
    presenter = ConsolePresenter(console, engine=engine, source=source)
    console.wire_entry(presenter.on_plate_entered)
    console.wire_console(presenter)
    console.set_state(source.ride_status())
    console.focus_entry()

    context = app_module._RouteContext(
        frame=window,
        resource=xrc_resource,
        roster=roster,
        app=wx.GetApp(),
        theme_controller=theme.ThemeController(wx.GetApp()),
        presenter=presenter,
    )
    app_module._bind_routes(context)
    return window, console, presenter, engine, source, clock


def _post_text_enter(control: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Post the event a real Enter keypress fires in *control*."""
    event = wx.CommandEvent(wx.EVT_TEXT_ENTER.typeId, control.GetId())
    event.SetEventObject(control)
    control.GetEventHandler().ProcessEvent(event)
    harness.pump()


def _type_crossing(window: Any, plate: str) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Type *plate* into the entry field and press Enter (R-31)."""
    plate_input = harness.find_control(window, ids.PLATE_INPUT)
    plate_input.SetValue(plate)
    _post_text_enter(plate_input)


def _set_checkbox(window: Any, name: str, *, value: bool) -> None:  # noqa: ANN401
    """Set *name*'s value and post the event a real click would fire."""
    control = harness.find_control(window, name)
    control.SetValue(value)
    event = wx.CommandEvent(wx.EVT_CHECKBOX.typeId, control.GetId())
    event.SetEventObject(control)
    control.GetEventHandler().ProcessEvent(event)
    harness.pump()


def _feed_model_rows(window: Any) -> tuple[tuple[str, int, bool, bool], ...]:  # noqa: ANN401
    """Read each feed row as ``(plate, lap, card_bitmap_ok, bold)``.

    The Card column renders a bitmap; a held crossing draws no chip
    (``wx.NullBitmap``), which is the UI half of R-34's "held" cell.
    """
    model = harness.find_control(window, ids.CROSSINGS_LIST).GetModel()
    rows = []
    for row in range(model.GetCount()):
        attr = wx.dataview.DataViewItemAttr()
        bold = model.GetAttrByRow(row, feed_model.COL_TIME, attr) and bool(attr.GetBold())
        bitmap = model.GetValueByRow(row, feed_model.COL_CARD)
        rows.append(
            (
                model.GetValueByRow(row, feed_model.COL_PLATE),
                int(model.GetValueByRow(row, feed_model.COL_LAP)),
                bool(bitmap is not None and bitmap.IsOk()),
                bold,
            )
        )
    return tuple(rows)


def test_mini_acceptance_scripted_race_runs_through_the_real_console(  # noqa: PLR0915 -- the script IS the test: one acceptance scenario
    xrc_resource: object,
) -> None:
    """The scripted 20-rider race passes end-to-end through the UI."""
    window, console, presenter, engine, source, clock = _build_mini_console(xrc_resource)
    try:
        # --- 2. start
        assert harness.find_control(window, ids.RIDE_STATUS_LBL).GetLabelText() == "RUNNING"
        assert harness.find_control(window, ids.PLATE_INPUT).IsEnabled() is True
        assert harness.find_control(window, ids.RECORD_BTN).IsEnabled() is True
        assert harness.find_control(window, ids.STOP_BTN).IsEnabled() is False

        # --- 3. sixty crossings through the real entry field
        elapsed = 0
        undone_card = ""
        for index, (plate, t) in enumerate(SCRIPT, start=1):
            clock.advance(t - elapsed)
            elapsed = t
            _type_crossing(window, plate)
            if index == UNDO_SCRIPT_POSITION:
                # Undo (R-33): the lap leaves the engine, its card
                # returns to the shoe front, and the next deal
                # reproduces that same card.
                undone_card = engine.card_for(engine.crossings[-1]).code()
                shoe_before = engine.shoe_remaining
                harness.click(window, ids.UNDO_BTN)
                assert len(engine.crossings) == index - 1
                assert engine.shoe_remaining == shoe_before + 1

        # The two short laps are flagged and their cards held (R-34).
        feed = source.feed_rows()
        assert feed[0].plate == "1"
        assert feed[0].lap == 5
        assert feed[0].card == "held"
        assert feed[0].flagged is True
        assert feed[1].plate == "1"
        assert feed[1].lap == 4
        assert feed[1].card == "held"
        assert feed[1].flagged is True
        model_rows = _feed_model_rows(window)
        assert model_rows[0][2] is False  # held: no chip
        assert model_rows[0][3] is True  # held: bold
        assert model_rows[1][2] is False
        assert model_rows[1][3] is True

        # The undo's restitution: the crossing typed right after the
        # undo (plate 2's lap 3) re-dealt the exact card the undone
        # crossing had (deal 38 = "4D").
        plate2_lap3 = next(c for c in engine.crossings if c.entry_id == "2" and c.seq == 3)
        assert engine.card_for(plate2_lap3).code() == undone_card == "4D"

        # Held confirm + held void (engine surface; E7 wires dialogs).
        held_by_code = {hc.card.code(): hc for hc in engine.held_crossings()}
        assert set(held_by_code) == {CONFIRM_CARD, VOID_CARD}
        engine.confirm_held(held_by_code[CONFIRM_CARD].crossing)
        presenter.tick()
        assert len(engine.held_crossings()) == 1
        feed = source.feed_rows()
        assert feed[1].card == CONFIRM_CARD  # #59 released
        assert feed[1].flagged is False
        assert feed[0].card == "held"  # #60 still held
        assert feed[0].flagged is True

        engine.void_held(held_by_code[VOID_CARD].crossing)
        presenter.tick()
        assert engine.held_crossings() == ()
        plate1 = next(r for r in engine.snapshot() if r.plate == "1")
        assert [c.code() for c in plate1.cards] == ["8C", "6S", "3C", "QC"]
        assert VOID_CARD not in [c.code() for c in plate1.cards]

        # Counters track the engine: 60 typed - 1 undo = 59 crossings,
        # no held cards, every entry on an odd lap, 373/432 in the shoe.
        counters = source.counters()
        assert (
            counters.crossings,
            counters.cards_dealt,
            counters.on_course,
            counters.shoe_remaining,
            counters.shoe_total,
        ) == (59, 59, 19, 373, 432)
        labels = (
            harness.find_control(window, ids.CROSSINGS_COUNT_LBL).GetLabelText(),
            harness.find_control(window, ids.CARDS_COUNT_LBL).GetLabelText(),
            harness.find_control(window, ids.ON_COURSE_LBL).GetLabelText(),
            harness.find_control(window, ids.SHOE_LBL).GetLabelText(),
        )
        assert labels == ("59", "59", "19", "373/432")

        # --- 4. stop / continue (R-35, spec section 3)
        elapsed_before_stop = engine.elapsed()
        _set_checkbox(window, ids.ARM_STOP_CHK, value=True)
        assert harness.find_control(window, ids.STOP_BTN).IsEnabled() is True

        def _click_stop_ok() -> None:
            dialog = wx.Window.FindWindowByName(ids.STOP_CONFIRM_DLG)
            harness.click(dialog, "wxID_OK")

        wx.CallAfter(_click_stop_ok)
        harness.click(window, ids.STOP_BTN)

        assert harness.find_control(window, ids.STOP_BTN).IsEnabled() is False
        assert harness.find_control(window, ids.ARM_STOP_CHK).GetValue() is False
        assert harness.find_control(window, ids.PLATE_INPUT).IsEnabled() is False

        # Refused crossings post a notice and keep the field (R-31).
        plate_input = harness.find_control(window, ids.PLATE_INPUT)
        plate_input.SetValue("5")
        _post_text_enter(plate_input)
        assert window.GetStatusBar().GetStatusText(0) == "The ride is stopped"
        assert plate_input.GetValue() == "5"
        assert engine.record_crossing("5").reason == "ride is stopped"

        # Continue: entry re-enabled; start and elapsed unchanged.
        harness.click(window, ids.START_BTN)
        assert harness.find_control(window, ids.PLATE_INPUT).IsEnabled() is True
        start_payload = next(e.payload for e in engine.events if e.action == "start")
        continue_payload = next(e.payload for e in engine.events if e.action == "continue")
        assert continue_payload["actual_start"] == start_payload["actual_start"]
        assert engine.elapsed() == elapsed_before_stop

        # --- 5. finish through the mi_finish_ride menu route
        original_gate = console_module.FINISH_GATE
        consulted: list[bool] = []

        def _recording_gate() -> bool:
            consulted.append(True)
            return original_gate()

        console_module.FINISH_GATE = _recording_gate
        try:

            def _click_finish_ok() -> None:
                dialog = wx.Window.FindWindowByName(ids.FINISH_CONFIRM_DLG)
                harness.click(dialog, "wxID_OK")

            wx.CallAfter(_click_finish_ok)
            harness.fire_menu_event(window, "mi_finish_ride")
        finally:
            console_module.FINISH_GATE = original_gate

        assert consulted == [True]
        assert engine.state is RideStatus.FINISHED
        assert harness.find_control(window, ids.RIDE_STATUS_LBL).GetLabelText() == "FINISHED"
        assert harness.find_control(window, ids.PLATE_INPUT).IsEnabled() is False

        # --- 6. standings equal the hand-verified fixture
        placed = rank(engine.snapshot())
        actual = tuple(
            (
                item.place,
                item.result.plate,
                item.result.laps,
                tuple(card.code() for card in item.result.hand.best5),
                item.result.hand.cls.name,
            )
            for item in placed
        )
        assert actual == EXPECTED_STANDINGS
    finally:
        del console
        harness.close_window(window)


def test_mini_acceptance_finish_confirm_cancel_leaves_ride_running(xrc_resource: object) -> None:
    """Cancelling finish_confirm_dlg does not finish the ride (R-35)."""
    # 2026-08-29: un-quarantined -- the flake's segfault was the
    # dangling console tick timer (never stopped on frame destroy;
    # any wxSafeYield fired it against freed memory), root-caused
    # + fixed in EPIC 6 (EVT_WINDOW_DESTROY -> timer.Stop();
    # tools/timer_repro.py; regression in test_app_bootstrap.py).
    # The crash mechanism is gone.
    window, console, _presenter, engine, _source, _clock = _build_mini_console(xrc_resource)
    try:
        original_gate = console_module.FINISH_GATE
        consulted: list[bool] = []

        def _recording_gate() -> bool:
            consulted.append(True)
            return True

        console_module.FINISH_GATE = _recording_gate
        try:

            def _click_finish_cancel() -> None:
                dialog = wx.Window.FindWindowByName(ids.FINISH_CONFIRM_DLG)
                harness.click(dialog, "wxID_CANCEL")

            wx.CallAfter(_click_finish_cancel)
            harness.fire_menu_event(window, "mi_finish_ride")
        finally:
            console_module.FINISH_GATE = original_gate

        assert engine.state is RideStatus.RUNNING
        assert consulted == []
        assert harness.find_control(window, ids.RIDE_STATUS_LBL).GetLabelText() == "RUNNING"
        assert harness.find_control(window, ids.PLATE_INPUT).IsEnabled() is True
    finally:
        del console
        harness.close_window(window)
