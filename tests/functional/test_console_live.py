# SPDX-License-Identifier: GPL-3.0-only
"""Real-toolkit tests for the live console (E4.4.1/E4.4.2).

``MainFrame`` wired to a real ``RideEngine`` through
``EngineDataSource`` + ``ConsolePresenter``: typed plates land in the
feed with a card chip (R-31/R-32), counters update, a flagged row is
bold (R-34), arm enables Stop and the confirm flow stops the engine
(R-35). Sound cues are asserted at the unit level (``test_sound.py``,
fake backend -- spec §10's "no audio hardware in CI"); here the real
view drives the real engine through the real harness (direct event
injection per ``harness.py``).

Structure mirrors ``test_console_demo.py``'s measured guidance:
read-only assertions share one module-scoped ``shared_live_console``
(constructing a ``MainFrame`` decodes the 53-card imagelist and
appends 7 DataView columns, and doing it per-test raises this wx
build's own address-reuse hazard), while every state-mutating scenario
runs in its own spawned interpreter via ``scenario_runner``.
"""

import re
from typing import Any

import harness
import pytest
import scenario_runner
import wx

from rivercrossing.cards import Shoe
from rivercrossing.ride import RideConfig, RideEngine
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import feed_model, ids
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EngineDataSource
from rivercrossing.ui.views import MainFrame
from rivercrossing.ui.views.gauges import RaceClock, StopLight

pytestmark = pytest.mark.functional


def _build_live_console(
    xrc_resource: object,
) -> tuple[Any, MainFrame, RideEngine, ConsolePresenter]:
    """Build a RUNNING live console over a real engine, fully wired."""
    from datetime import date, datetime  # noqa: PLC0415 -- local helper import

    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider", last_name="12", plate="12")
    roster.create_solo_entry(first_name="Rider", last_name="34", plate="34")
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
    # The real wall-clock seam: consistent aware-UTC nows, matching the
    # app bootstrap's engine (ride.py's clock contract).
    import datetime as _dt  # noqa: PLC0415

    engine = RideEngine(
        config=config,
        shoe=shoe,
        clock=lambda: _dt.datetime.now(_dt.UTC),
        roster=roster,
    )
    engine.start()
    source = EngineDataSource(engine, roster)
    window = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        console = MainFrame(window, data_source=source, resource=xrc_resource)
        presenter = ConsolePresenter(console, engine=engine, source=source)
        console.wire_entry(presenter.on_plate_entered)
        console.wire_console(presenter)
        console.set_state(source.ride_status())
    except Exception:
        # Fault A: a construct-phase raise (a degraded load, a wiring
        # failure) must not leak the window the caller's fixture never
        # yields -- this builder raised before returning it.
        harness.close_window(window)
        raise
    return window, console, engine, presenter


@pytest.fixture(scope="module")
def shared_live_console(xrc_resource: object) -> tuple[Any, RideEngine, ConsolePresenter]:
    """One live ``MainFrame`` for every read-only assertion below.

    Nothing in the read-only tests below records a crossing, arms Stop
    or mutates the feed, so one instance safely serves them all (see
    the module docstring for why sharing matters here). The presenter
    is yielded too: the clock renders on its 1 s tick, and the gauge
    test drives one tick synchronously instead of waiting on a
    ``wx.Timer`` that never fires under a bare pump.
    """
    window, console, engine, presenter = _build_live_console(xrc_resource)
    try:
        yield window, engine, presenter
    finally:
        del console, presenter
        harness.close_window(window)


def _feed_plates(window: Any) -> tuple[str, ...]:  # noqa: ANN401 -- wx ships no stubs
    """Return every feed row's Plate cell, in model row order."""
    model = harness.find_control(window, ids.CROSSINGS_LIST).GetModel()
    return tuple(model.GetValueByRow(row, feed_model.COL_PLATE) for row in range(model.GetCount()))


# --- read-only: the live console at startup -------------------------


def test_live_console_given_a_fresh_engine_shows_an_empty_feed(
    shared_live_console: tuple[Any, RideEngine, ConsolePresenter],
) -> None:
    """R-32: the feed reads from the engine, empty at startup."""
    window, _engine, _presenter = shared_live_console

    assert _feed_plates(window) == ()


def test_live_console_shows_zero_counters_at_startup(
    shared_live_console: tuple[Any, RideEngine, ConsolePresenter],
) -> None:
    """A fresh ride counts 0 crossings, cards, on course; full shoe."""
    window, _engine, _presenter = shared_live_console
    labels = (
        harness.find_control(window, ids.CROSSINGS_COUNT_LBL).GetLabelText(),
        harness.find_control(window, ids.CARDS_COUNT_LBL).GetLabelText(),
        harness.find_control(window, ids.ON_COURSE_LBL).GetLabelText(),
        harness.find_control(window, ids.SHOE_LBL).GetLabelText(),
    )

    assert labels == ("0", "0", "0", "432/432")


def test_live_console_starts_running_with_entry_enabled_and_stop_disabled(
    shared_live_console: tuple[Any, RideEngine, ConsolePresenter],
) -> None:
    """R-35 gate: Stop stays disabled until Arm; entry is live."""
    window, _engine, _presenter = shared_live_console

    assert (
        harness.find_control(window, ids.PLATE_INPUT).IsEnabled(),
        harness.find_control(window, ids.RECORD_BTN).IsEnabled(),
        harness.find_control(window, ids.STOP_BTN).IsEnabled(),
        harness.find_control(window, ids.ARM_STOP_CHK).GetValue(),
        harness.find_control(window, ids.RIDE_STATUS_LBL).GetLabelText(),
    ) == (True, True, False, False, "RUNNING")


def test_live_console_clock_resolves_with_zero_elapsed_at_startup(
    shared_live_console: tuple[Any, RideEngine, ConsolePresenter],
) -> None:
    """The tick labels exist and start at the XRC zeros (R-30)."""
    window, _engine, _presenter = shared_live_console

    assert harness.find_control(window, ids.CLOCK_ELAPSED_LBL).GetLabelText() == "0:00:00"
    assert harness.find_control(window, ids.CLOCK_REMAINING_LBL).GetLabelText() == ""


# --- WS-D: the header gauges and the bitmap start/stop buttons --------


def test_live_console_header_gauges_resolve_under_their_frozen_names(
    shared_live_console: tuple[Any, RideEngine, ConsolePresenter],
) -> None:
    """WS-D: the two dials and the status lamp are live custom controls.

    The clock renders on the presenter's 1 s tick (a ``wx.Timer``
    never fires under a bare pump, measured), so the Act drives one
    ``presenter.tick()``: the RUNNING ride's real fractions must land
    on the dials -- elapsed just started (~0.0) and remaining still
    full (>= 0.99). The elapsed dial is not asserted at exactly 0.0:
    it reads the real wall clock, whose fraction is genuinely
    non-zero the instant a tick has rendered. The frozen names are
    code-side ``SetName()`` calls -- gauges.py classes XRC cannot
    author -- so they are found by literal here, never through
    ``ui.ids.py``.
    """
    window, _engine, presenter = shared_live_console
    elapsed = harness.find_control(window, "elapsed_clock")
    remaining = harness.find_control(window, "remaining_clock")
    light = harness.find_control(window, "ride_status_light")

    presenter.tick()

    assert isinstance(elapsed, RaceClock)
    assert isinstance(remaining, RaceClock)
    assert isinstance(light, StopLight)
    # 0.01 tolerates any elapsed under 3.6 min of wall clock between
    # the engine's start and this tick; the ride just started.
    assert elapsed.fraction == pytest.approx(0.0, abs=0.01)
    assert remaining.fraction >= 0.99  # ~full remaining: the ride just started
    assert light.mode == "green"


def test_live_console_start_stop_buttons_carry_the_go_stop_glyphs_and_labels(
    shared_live_console: tuple[Any, RideEngine, ConsolePresenter],
) -> None:
    """WS-D: start/stop are wxBitmapButton with the SVG glyphs set.

    The XRC bitmap-button handler ignores label text (measured), so
    the code re-applies the labels -- without them the buttons would
    be icon-only and unnamed to assistive tech.
    """
    window, _engine, _presenter = shared_live_console
    start = harness.find_control(window, ids.START_BTN)
    stop = harness.find_control(window, ids.STOP_BTN)

    assert isinstance(start, wx.BitmapButton)
    assert isinstance(stop, wx.BitmapButton)
    assert start.GetBitmap().IsOk()
    assert stop.GetBitmap().IsOk()
    assert start.GetLabelText() == "Start ride"
    assert stop.GetLabelText() == "Stop ride…"


def test_stop_light_rejects_an_unknown_mode(wx_app: object) -> None:  # noqa: ARG001 -- fixture requested for ordering
    """WS-D: an unmapped mode fails loudly, never dims silently."""
    parent = wx.Frame(None)
    try:
        light = StopLight(parent)
        with pytest.raises(ValueError, match=re.escape("unknown stop-light mode 'purple'")):
            light.set_mode("purple")
    finally:
        harness.close_window(parent)


# --- state-mutating: subprocess scenarios (test_console_demo) ----


def test_live_typed_plate_appears_in_feed_with_card_chip() -> None:
    """R-31/R-32: a typed plate is on screen with its card chip."""
    result = scenario_runner.run_scenario("live_typed_plate_appears_in_feed")

    assert result["ok"], result["context"]
    assert result["data"]["feed_plates"] == ["12"], result["context"]
    assert result["data"]["card_chip_ok"] is True, result["context"]
    assert result["data"]["crossings_label"] == "1", result["context"]
    assert result["data"]["field_cleared"] is True, result["context"]
    assert result["data"]["focused"] is True, result["context"]


def test_live_flagged_crossing_row_is_bold() -> None:
    """R-34: a short-lap row bolds and its held card draws no chip."""
    result = scenario_runner.run_scenario("live_flagged_crossing_row_is_bold")

    assert result["ok"], result["context"]
    assert result["data"]["row_bold"] is True, result["context"]
    assert result["data"]["card_chip_ok"] is False, result["context"]
    assert result["data"]["held_count"] == 1, result["context"]


def test_live_arm_enables_stop_and_confirmed_stop_locks_the_entry_field() -> None:
    """R-35's three deliberate acts through the real controls."""
    result = scenario_runner.run_scenario("live_arm_stop_confirm_flow")

    assert result["ok"], result["context"]
    assert result["data"]["stop_enabled_before_arm"] is False, result["context"]
    assert result["data"]["stop_enabled_while_armed"] is True, result["context"]
    assert result["data"]["stop_enabled_after_confirm"] is False, result["context"]
    assert result["data"]["arm_checked_after_confirm"] is False, result["context"]
    assert result["data"]["plate_enabled_after_stop"] is False, result["context"]
    assert result["data"]["refused_reason"] == "ride is stopped", result["context"]
