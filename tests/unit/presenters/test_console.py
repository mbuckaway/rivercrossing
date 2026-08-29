# SPDX-License-Identifier: GPL-3.0-only
"""ConsolePresenter + EngineDataSource unit tests (E4.4.1/2/3).

The console is the live-timing screen: a ``ConsolePresenter`` holds
``(view, engine, source)`` -- the engine owns the write side
(``record_crossing``/``undo_last``/``start``/``stop``/``finish``), the
read-only ``EngineDataSource`` serves feed/counters/status, and the
view is a recording fake. These tests drive the presenter's event
handlers against a real ``RideEngine``/``Roster``/``Shoe`` (never wx),
asserting the cue fired (spec §10), the feed/counters refreshed, the
field cleared or kept (R-31), the arm/stop flow (R-35) with a fake
monotonic tick, hide-times forwarding (R-37), tick refresh, and the
E6.4.3 finish-gate hook consulted before finishing.

``EngineDataSource`` is the first real ``DataSource`` implementation
over ``(engine, roster)``; its mapping tests pin the feed shape (R-32:
time, plate, entry, lap, lap time, total, card or "held", flagged,
newest-first, cap 30), the counters, and the non-console methods
(standings/entry_detail/audit_rows/riders/rides) implemented simply
for E5/E6 to replace.
"""

import re
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.cards import Card, Shoe
from rivercrossing.ride import (
    RideConfig,
    RideEngine,
    RideStatus,
)
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.standings import (
    DEFAULT_TIEBREAK_ORDER,
    TieBreak,
    hand_name,
    tiebreak_order_from_spellings,
)
from rivercrossing.ui.presenters import Cue, EngineDataSource
from rivercrossing.ui.presenters import console as console_module
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import (
    Counters,
    DataSource,
    EmptyDataSource,
    EntryDetail,
    FeedRow,
    RiderRow,
    StandingsRow,
    format_duration,
)

# -------------------------------------------------------------- helpers

_EVENT_DAY = date(2026, 9, 20)


def _dt(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Build a naive datetime on the fixed event day."""
    return datetime(2026, 9, 20, hour, minute, second)  # noqa: DTZ001 -- naive by design, as RideConfig.planned_start


def _config(*, min_lap_s: int = 1) -> RideConfig:
    """Build a valid, always-valid config with a tunable min-lap."""
    return RideConfig(
        name="GORBA EPIC 2026",
        event_date=_EVENT_DAY,
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=_dt(10, 0),
        planned_duration_s=21600,
        min_lap_s=min_lap_s,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )


class _FakeDatetimeClock:
    """Wall-clock source the engine can advance deterministically."""

    def __init__(self, start: datetime) -> None:
        """Start the fake clock at *start*."""
        self._now = start

    def __call__(self) -> datetime:
        """Return the current fake time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward by *seconds*."""
        self._now = self._now + timedelta(seconds=seconds)


class _FakeMonotonicClock:
    """Monotonic clock the presenter's 10 s arm timeout reads (R-35)."""

    def __init__(self, start: float = 0.0) -> None:
        """Start the fake monotonic clock at *start*."""
        self._now = start

    def __call__(self) -> float:
        """Return the current fake monotonic time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the fake monotonic clock forward by *seconds*."""
        self._now += seconds


def _roster_with_entries(*plates: str) -> Roster:
    """Build a MIXED rider_pooled roster of one solo entry per plate."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    for plate in plates:
        roster.create_solo_entry(name=f"Rider {plate}", plate=plate)
    return roster


def _make_engine(
    *,
    roster: Roster | None = None,
    min_lap_s: int = 1,
) -> tuple[RideEngine, _FakeDatetimeClock]:
    """Build a DRAFT engine over a valid config, shoe and roster."""
    config = _config(min_lap_s=min_lap_s)
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    clock = _FakeDatetimeClock(config.planned_start)
    roster = roster if roster is not None else _roster_with_entries("12", "34")
    engine = RideEngine(config=config, shoe=shoe, clock=clock, roster=roster)
    return engine, clock


def _running_engine(*, min_lap_s: int = 1) -> tuple[RideEngine, _FakeDatetimeClock]:
    """Build an engine already started, ready to record crossings."""
    engine, clock = _make_engine(min_lap_s=min_lap_s)
    engine.start()
    return engine, clock


def _record(  # noqa: PLR0913 -- seeded crossing helper: (engine, clock, plate) + lap_time_s
    engine: RideEngine,
    clock: _FakeDatetimeClock,
    plate: str,
    *,
    lap_time_s: float,
) -> Any:  # noqa: ANN401 -- CrossingResult is a dataclass, not Any
    """Record one crossing, clock advanced by *lap_time_s*."""
    clock.advance(lap_time_s)
    return engine.record_crossing(plate)


# ----------------------------------------------------------------- view


class FakeConsoleView:
    """A recording ``ConsoleView`` spy for headless presenter tests.

    Each channel keeps the last value it was shown, plus counters for
    the one-shot actions (focus/clear) -- enough for T-8's single-Act,
    one-focused-assertion-block tests below.
    """

    def __init__(self) -> None:
        """Start every channel empty."""
        self.cues: list[Cue] = []
        self.last_feed: list[FeedRow] = []
        self.last_counters: Counters | None = None
        self.last_flash: FeedRow | None = None
        self.last_state: RideStatus | None = None
        self.last_notice: str | None = None
        self.last_clock: tuple[str, str] | None = None
        self.last_hide: bool | None = None
        self.stop_enabled: bool | None = None
        self.entry_locked: bool | None = None
        self.focus_count = 0
        self.clear_count = 0

    def show_feed(self, rows: list[FeedRow]) -> None:
        """Record the fed rows."""
        self.last_feed = list(rows)

    def show_counters(self, c: Counters) -> None:
        """Record the counters."""
        self.last_counters = c

    def flash_crossing(self, r: FeedRow) -> None:
        """Record the flashed crossing."""
        self.last_flash = r

    def set_state(self, status: RideStatus) -> None:
        """Record the ride state."""
        self.last_state = status

    def focus_entry(self) -> None:
        """Record one focus request."""
        self.focus_count += 1

    def play(self, cue: Cue) -> None:
        """Record the played cue."""
        self.cues.append(cue)

    def show_notice(self, text: str) -> None:
        """Record the shown notice."""
        self.last_notice = text

    def clear_entry(self) -> None:
        """Record one clear request."""
        self.clear_count += 1

    def set_stop_enabled(self, *, enabled: bool) -> None:
        """Record the stop button's enablement (R-35)."""
        self.stop_enabled = enabled

    def set_hide_times(self, *, hide: bool) -> None:
        """Record the hide-times request (R-37)."""
        self.last_hide = hide

    def show_clock(self, elapsed: str, remaining: str) -> None:
        """Record the clock labels."""
        self.last_clock = (elapsed, remaining)

    def set_entry_locked(self, *, locked: bool) -> None:
        """Record the entry-field lock request (R-35)."""
        self.entry_locked = locked


def _make_presenter(
    engine: RideEngine,
    view: FakeConsoleView,
    *,
    mono: _FakeMonotonicClock | None = None,
) -> ConsolePresenter:
    """Build the presenter over a real engine source and a fake view."""
    source = EngineDataSource(engine, engine._roster)
    return ConsolePresenter(view, engine=engine, source=source, now=mono)


def _assert_rejected(  # noqa: PLR0913 -- shared rejection assertion: view + notice/engine + two counts
    view: FakeConsoleView,
    *,
    notice: str,
    engine: RideEngine,
    clear_count: int = 0,
    focus_count: int = 1,
) -> None:
    """Shared rejection: error cue, notice, focus, no clear."""
    assert view.cues == [Cue.ERROR]
    assert view.last_notice == notice
    assert view.clear_count == clear_count
    assert view.focus_count == focus_count
    assert len(engine.crossings) == 0


# -------------------------------------------------- EngineDataSource

# ------------------------------------------- E5.4.2 EmptyDataSource


def test_empty_data_source_isinstance_satisfies_data_source_protocol() -> None:
    """E5.4.2: ``EmptyDataSource`` is a structural ``DataSource``.

    The empty state is a real production implementation (the windows
    with no store-backed data yet read it), not a test double -- so it
    must conform to the same Protocol ``DemoDataSource`` and
    ``EngineDataSource`` do.
    """
    assert isinstance(EmptyDataSource(), DataSource)


def test_empty_data_source_returns_zero_rows_for_every_screen() -> None:
    """E5.4.2: the empty state reads no rows anywhere.

    Library (``rides``), rider editor (``riders``), results
    (``standings``) and audit (``audit_rows``) all render empty until
    E6/E7 wire real data; the console feed (``feed_rows``) is empty
    because no crossings exist.
    """
    source = EmptyDataSource()

    assert (
        source.feed_rows(),
        source.rides(),
        source.riders(),
        source.standings(),
        source.audit_rows(),
    ) == ([], [], [], [], [])


def test_empty_data_source_counters_and_status_report_no_ride() -> None:
    """E5.4.2: zero counters and DRAFT -- nothing is running."""
    source = EmptyDataSource()

    assert source.counters() == Counters(
        crossings=0, cards_dealt=0, on_course=0, shoe_remaining=0, shoe_total=0
    )
    assert source.ride_status() is RideStatus.DRAFT


def test_empty_data_source_entry_detail_returns_an_empty_view_model() -> None:
    """E5.4.2: any plate resolves to an empty detail, never raises.

    ``entry_detail_dlg`` opens with no ride selected; the view renders
    the empty header/members/cards/laps rather than crashing on a
    plate that no store-backed entry owns yet (E7 wires the real
    per-entry lookup).
    """
    detail = EmptyDataSource().entry_detail("77")

    assert detail == EntryDetail(header="", members="", cards_held=(), laps=())


# ------------------------------------------------------------- feed


def test_engine_data_source_feed_rows_given_crossings_returns_newest_first() -> None:
    """R-32: feed rows carry the seven canvas columns."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=100)
    _record(engine, clock, "12", lap_time_s=100)
    source = EngineDataSource(engine, engine._roster)

    feed = source.feed_rows()

    assert len(feed) == 2
    assert feed[0].lap == 2  # newest first
    assert feed[1].lap == 1
    assert feed[0].plate == "12"
    assert feed[0].entry == "Rider 12"
    assert feed[0].time == "10:03:20"  # start 10:00 + 200 s
    assert feed[0].lap_time == "1:40"  # 100 s between laps
    assert feed[0].total == "0:03:20"  # 200 s from the gun
    assert feed[0].card == engine.card_for(engine.crossings[-1]).code()
    assert feed[0].flagged is False


@pytest.mark.parametrize(
    ("recorded", "shown"),
    [(29, 29), (30, 30), (31, 30)],
    ids=["below_cap", "at_cap", "past_cap"],
)
def test_engine_data_source_feed_rows_caps_at_thirty_rows(recorded: int, shown: int) -> None:
    """R-32's 20-30 cap: rows stay at 30 past the cap, newest first."""
    engine, clock = _running_engine()
    for _index in range(recorded):
        _record(engine, clock, "12", lap_time_s=10)
    source = EngineDataSource(engine, engine._roster)

    feed = source.feed_rows()

    assert len(feed) == shown
    assert [row.lap for row in feed] == list(range(recorded, recorded - shown, -1))


def test_engine_data_source_feed_rows_given_flagged_crossing_reports_held_card() -> None:
    """R-34: a short lap's row is flagged and shows 'held', no card."""
    engine, clock = _running_engine(min_lap_s=60)
    _record(engine, clock, "12", lap_time_s=5)  # 5 s < 60 s min lap
    source = EngineDataSource(engine, engine._roster)

    feed = source.feed_rows()

    assert feed[0].flagged is True
    assert feed[0].card == "held"


# ----------------------------------------------------------- counters


def test_engine_data_source_counters_reflect_engine_state() -> None:
    """R-32: crossings/cards/on-course/shoe read from the engine."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=100)
    _record(engine, clock, "12", lap_time_s=100)
    source = EngineDataSource(engine, engine._roster)

    counters = source.counters()

    assert counters == Counters(
        crossings=2,
        cards_dealt=2,  # both credited -- no held card
        on_course=0,  # plate 12 has 2 (even) laps
        shoe_remaining=430,  # 8x54 shoe, 2 dealt
        shoe_total=432,
    )


def test_engine_data_source_counters_exclude_held_cards_from_cards_dealt() -> None:
    """A held card is dealt but not credited (R-34) -- 1124-32."""
    engine, clock = _running_engine(min_lap_s=60)
    _record(engine, clock, "12", lap_time_s=5)
    source = EngineDataSource(engine, engine._roster)

    counters = source.counters()

    assert (counters.crossings, counters.cards_dealt) == (1, 0)


# ------------------------------------------------------------ status


def test_engine_data_source_ride_status_tracks_the_engine() -> None:
    """The console's lifecycle banner follows the engine's state."""
    engine, _clock = _make_engine()
    source = EngineDataSource(engine, engine._roster)
    assert source.ride_status() is RideStatus.DRAFT

    engine.start()

    assert source.ride_status() is RideStatus.RUNNING


# ---------------------------------------------------------- standings


def test_engine_data_source_standings_maps_the_ranked_snapshot() -> None:
    """Standings rows come from standings.rank(engine.snapshot())."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=100)
    _record(engine, clock, "34", lap_time_s=100)
    engine.finish()
    source = EngineDataSource(engine, engine._roster)
    snapshots = {result.plate: result for result in engine.snapshot()}

    standings = source.standings()

    assert len(standings) == 2
    assert {row.plate for row in standings} == {"12", "34"}
    assert all(row.place in (1, 2) for row in standings)
    assert {row.plate: row.hand for row in standings} == {
        plate: hand_name(snapshots[plate].hand) for plate in snapshots
    }
    assert all(isinstance(row, StandingsRow) for row in standings)


def test_engine_data_source_standings_omitted_order_uses_the_default_constant() -> None:
    """E6.4.1: the defaulted ``order`` keeps today's behaviour."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=100)
    _record(engine, clock, "34", lap_time_s=100)
    engine.finish()
    source = EngineDataSource(engine, engine._roster)

    assert source.standings() == source.standings(order=DEFAULT_TIEBREAK_ORDER)


def test_engine_data_source_standings_reordered_order_changes_row_order() -> None:
    """A reordered tie-break order re-ranks the same snapshot live.

    Two entries hold byte-identical hands (the same pair-over-trips
    five) with different laps/totals; ``rank(snapshot, order)`` under
    most-laps-first and total-time-first must place them differently.
    The credited hands are written straight onto the engine's hand
    table (the same private-access style ``_make_presenter`` already
    uses for ``engine._roster``): the seeded shoe has no natural
    hand-tie pair in any reachable crossing pattern (probed), and
    this pins the forwarding, not the shoe's deal order.
    """
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=100)
    _record(engine, clock, "34", lap_time_s=60)
    _record(engine, clock, "12", lap_time_s=100)
    tied = [Card.parse(code) for code in ("5H", "5D", "2C", "3C", "4C")]
    engine._hand["12"] = list(tied)
    engine._hand["34"] = list(tied)
    engine.finish()
    source = EngineDataSource(engine, engine._roster)

    by_laps = source.standings(
        order=tiebreak_order_from_spellings(("laps", "total_time", "high_card"))
    )
    by_time = source.standings(
        order=tiebreak_order_from_spellings(("total_time", "laps", "high_card"))
    )

    assert [row.plate for row in by_laps] == ["12", "34"]
    assert [row.plate for row in by_time] == ["34", "12"]


def test_engine_data_source_standings_given_a_zero_card_entry_renders_a_blank_hand() -> None:
    """P1's 0-card guard: ``hand_name`` raises, the row renders ''.

    An entry that never crossed finishes with ``best_hand(())`` -- no
    rank to name (``hand_name`` raises ValueError) -- so the source
    renders an empty hand cell instead of crashing.
    """
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=100)
    engine.finish()
    source = EngineDataSource(engine, engine._roster)

    rows = source.standings()

    by_plate = {row.plate: row for row in rows}
    assert by_plate["12"].hand != ""  # one credited card has a real prose hand
    assert by_plate["34"].hand == ""


def test_empty_data_source_standings_accepts_the_order_argument() -> None:
    """E6.4.1: the empty state still returns no rows for any order."""
    source = EmptyDataSource()

    assert source.standings(order=(TieBreak.TOTAL_TIME, TieBreak.MOST_LAPS)) == []


# -------------------------------------------------------- entry detail


def test_engine_data_source_entry_detail_given_known_plate_builds_the_view_model() -> None:
    """Entry detail's laps/cards render from the engine's crossings."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=100)
    source = EngineDataSource(engine, engine._roster)

    detail = source.entry_detail("12")

    assert len(detail.laps) == 1
    assert detail.laps[0].lap == 1
    assert detail.laps[0].card == engine.card_for(engine.crossings[-1]).code()
    assert detail.cards_held == ()
    assert detail.header == "Solo · 1 riders · 1 laps · 0:01:40"
    assert detail.members == "Rider 12"


def test_engine_data_source_entry_detail_given_unknown_plate_raises() -> None:
    """Negative: a plate no entry owns cannot build a detail view."""
    engine, _clock = _running_engine()
    source = EngineDataSource(engine, engine._roster)

    with pytest.raises(LookupError, match=re.escape("no entry detail for plate '99'")):
        source.entry_detail("99")


# --------------------------------------------------------------- audit


def test_engine_data_source_audit_rows_maps_engine_events_newest_first() -> None:
    """R-38's newest-first audit shape from the engine's events."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=100)
    source = EngineDataSource(engine, engine._roster)

    rows = source.audit_rows()

    assert [row.action for row in rows] == ["record_crossing", "start"]
    assert rows[0].entry == "12"
    assert rows[0].who == "scorer"


# ------------------------------------------------------------- riders


def test_engine_data_source_riders_maps_roster_entries_and_team_members() -> None:
    """Rider editor rows project solo and pooled team members."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(name="Sam Ellis", plate="123")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[Rider(name="A. Roy", plate="77"), Rider(name="K. Singh", plate="78")],
    )
    engine, _clock = _make_engine(roster=roster)
    source = EngineDataSource(engine, roster)

    rows = source.riders()

    assert rows == [
        RiderRow(plate="123", name="Sam Ellis", team=None),
        RiderRow(plate="77", name="A. Roy", team="Trail Blazers"),
        RiderRow(plate="78", name="K. Singh", team="Trail Blazers"),
    ]


# -------------------------------------------------------------- rides


def test_engine_data_source_rides_returns_one_summary_for_the_active_ride() -> None:
    """Ride library stays minimal: one summary for the console ride."""
    engine, _clock = _running_engine()
    source = EngineDataSource(engine, engine._roster)

    rides = source.rides()

    assert len(rides) == 1
    assert rides[0].name == "GORBA EPIC 2026"
    assert rides[0].date == "2026-09-20"
    assert rides[0].status is RideStatus.RUNNING
    assert rides[0].entries == 2


# ------------------------------------------------ ConsolePresenter


def test_console_presenter_holds_the_view_engine_and_source_it_was_given() -> None:
    """E4.4.1's three collaborators: view, engine, read source."""
    engine, _clock = _running_engine()
    view = FakeConsoleView()
    source = EngineDataSource(engine, engine._roster)

    presenter = ConsolePresenter(view, engine=engine, source=source)

    assert presenter.view is view
    assert presenter.engine is engine
    assert presenter.source is source


# ------------------------------------------------------- plate entered


def test_on_plate_entered_given_accepted_plate_refreshes_feed_flashes_and_plays_recorded() -> None:
    """R-31/R-32: a recorded plate lands in the feed, RECORDED cue."""
    engine, clock = _running_engine()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)
    clock.advance(100)

    presenter.on_plate_entered("12")

    assert len(engine.crossings) == 1
    assert [row.plate for row in view.last_feed] == ["12"]
    assert view.last_counters is not None
    assert view.last_counters.crossings == 1
    assert view.last_flash is not None
    assert view.last_flash.plate == "12"
    assert view.cues == [Cue.RECORDED]
    assert view.clear_count == 1
    assert view.focus_count == 1


def test_on_plate_entered_given_flagged_crossing_plays_flagged_cue() -> None:
    """R-34: a short-lap crossing's cue is FLAGGED, not RECORDED."""
    engine, clock = _running_engine(min_lap_s=60)
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)
    clock.advance(5)  # 5 s < 60 s min lap

    presenter.on_plate_entered("12")

    assert view.cues == [Cue.FLAGGED]
    assert view.last_feed[0].flagged is True
    assert view.clear_count == 1
    assert view.focus_count == 1


def test_on_plate_entered_given_unknown_plate_plays_error_keeps_focus_and_keeps_text() -> None:
    """R-31: rejection plays ERROR, notifies, never clears the field."""
    engine, _clock = _running_engine()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_plate_entered("99")

    _assert_rejected(view, notice="Unknown plate 99", engine=engine)
    assert view.last_feed == []


def test_on_plate_entered_given_not_running_engine_plays_error_and_keeps_text() -> None:
    """A DRAFT engine refuses with the ERROR cue and no clear."""
    engine, _clock = _make_engine()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_plate_entered("12")

    _assert_rejected(view, notice="The ride is not running", engine=engine)


def test_on_plate_entered_given_stopped_ride_plays_error_and_keeps_text() -> None:
    """E4.1.3: a stopped ride refuses crossings with the ERROR cue."""
    engine, _clock = _running_engine()
    engine.stop()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_plate_entered("12")

    _assert_rejected(view, notice="The ride is stopped", engine=engine)


@pytest.mark.parametrize("text", ["", "   "], ids=["empty", "whitespace_only"])
def test_on_plate_entered_given_blank_text_only_refocuses(text: str) -> None:
    """A3: a blank submission only returns focus -- no cue, no clear."""
    engine, _clock = _running_engine()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_plate_entered(text)

    assert view.focus_count == 1
    assert view.cues == []
    assert view.clear_count == 0
    assert len(engine.crossings) == 0


# ---------------------------------------------------------------- undo


def test_on_undo_given_crossings_removes_last_refreshes_feed_and_notices() -> None:
    """R-33: undo removes the newest crossing and re-renders feed."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=100)
    _record(engine, clock, "12", lap_time_s=100)
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_undo()

    assert len(engine.crossings) == 1
    assert len(view.last_feed) == 1
    assert view.last_notice == "Last crossing undone"


def test_on_undo_given_no_crossings_shows_a_notice_and_keeps_state() -> None:
    """Negative: undo with nothing to undo is a notice, no crash."""
    engine, _clock = _running_engine()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_undo()

    assert view.last_notice == "Undo unavailable: no crossings to undo"
    assert len(engine.crossings) == 0


# ----------------------------------------------------------- arm/stop


def test_on_arm_stop_given_armed_enables_the_stop_button() -> None:
    """R-35 act 1: ticking Arm is what enables Stop."""
    engine, _clock = _running_engine()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_arm_stop(armed=True)

    assert view.stop_enabled is True


def test_on_arm_stop_given_disarmed_disables_the_stop_button() -> None:
    """Unticking Arm disables Stop again immediately."""
    engine, _clock = _running_engine()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)
    presenter.on_arm_stop(armed=True)

    presenter.on_arm_stop(armed=False)

    assert view.stop_enabled is False


def test_arm_stop_auto_clears_after_ten_seconds_via_tick() -> None:
    """R-35: 10 s untouched -- the next tick disarms."""
    engine, _clock = _running_engine()
    view = FakeConsoleView()
    mono = _FakeMonotonicClock(0.0)
    presenter = _make_presenter(engine, view, mono=mono)
    presenter.on_arm_stop(armed=True)

    mono.advance(11.0)
    presenter.tick()

    assert view.stop_enabled is False


def test_arm_stop_within_ten_seconds_stays_armed_through_ticks() -> None:
    """R-35 boundary: 5 s in, a tick must not disarm yet."""
    engine, _clock = _running_engine()
    view = FakeConsoleView()
    mono = _FakeMonotonicClock(0.0)
    presenter = _make_presenter(engine, view, mono=mono)
    presenter.on_arm_stop(armed=True)

    mono.advance(5.0)
    presenter.tick()

    assert view.stop_enabled is True


def test_arm_stop_at_exactly_ten_seconds_disarms_on_the_next_tick() -> None:
    """R-35 boundary: at exactly 10.0 s the >= comparison disarms."""
    engine, _clock = _running_engine()
    view = FakeConsoleView()
    mono = _FakeMonotonicClock(0.0)
    presenter = _make_presenter(engine, view, mono=mono)
    presenter.on_arm_stop(armed=True)

    mono.advance(10.0)
    presenter.tick()

    assert view.stop_enabled is False


def test_on_stop_confirmed_given_running_ride_stops_disarms_and_locks_entry() -> None:
    """R-35 acts 2-3: confirming Stop locks the entry and disarms."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=100)
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)
    presenter.on_arm_stop(armed=True)

    presenter.on_stop_confirmed()

    assert engine.record_crossing("12").reason == "ride is stopped"
    assert view.stop_enabled is False  # auto-clear after use
    assert view.entry_locked is True
    assert view.last_state is RideStatus.RUNNING  # stop is a guard, not a state
    assert view.last_notice == "Ride stopped — continue to resume"


def test_on_stop_confirmed_given_not_running_ride_shows_a_notice() -> None:
    """Negative: Stop on a DRAFT ride cannot stop anything."""
    engine, _clock = _make_engine()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_stop_confirmed()

    assert view.last_notice == "Cannot stop: cannot stop a draft ride"
    assert view.entry_locked is None  # never reached the lock step


# ---------------------------------------------------------------- start


def test_on_start_given_draft_ride_starts_and_enables_entry() -> None:
    """Start Ride moves DRAFT -> RUNNING and unlocks the entry row."""
    engine, _clock = _make_engine()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_start()

    assert engine.state is RideStatus.RUNNING
    assert view.last_state is RideStatus.RUNNING
    assert view.entry_locked is False
    assert view.last_notice == "Ride started"
    assert engine.events[-1].action == "start"


def test_on_start_given_finished_ride_shows_a_notice() -> None:
    """Negative: Start on a FINISHED ride is refused with a notice."""
    engine, _clock = _running_engine()
    engine.finish()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_start()

    assert view.last_notice == "Cannot start: cannot start from finished"
    assert engine.state is RideStatus.FINISHED


# ----------------------------------------------------------- hide times


@pytest.mark.parametrize("hide", [True, False], ids=["hide", "show"])
def test_on_hide_times_forwards_the_setting_to_the_view(hide: bool) -> None:  # noqa: FBT001
    """R-37: the presenter forwards the toggle straight to the view."""
    engine, _clock = _running_engine()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_hide_times(hide=hide)

    assert view.last_hide is hide


# ----------------------------------------------------------------- tick


def test_on_tick_refreshes_feed_counters_and_clock() -> None:
    """The periodic tick keeps feed, counters and clock live (R-30)."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=100)
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.tick()

    assert len(view.last_feed) == 1
    assert view.last_counters is not None
    assert view.last_counters.crossings == 1
    assert view.last_clock == ("0:01:40", "5:58:20")  # 100 s elapsed of 6 h planned


def test_on_tick_given_draft_ride_shows_a_zeroed_clock() -> None:
    """DRAFT has no elapsed time yet -- the clock reads zeros."""
    engine, _clock = _make_engine()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.tick()

    assert view.last_clock == ("0:00:00", "0:00:00")


# ------------------------------------------------------------- finish


def test_on_finish_given_gate_clear_finishes_the_ride() -> None:
    """E4.4.2: the finish-gate hook (stub True) lets the ride finish."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=100)
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_finish()

    assert engine.state is RideStatus.FINISHED
    assert view.last_state is RideStatus.FINISHED
    assert view.last_notice == "Ride finished"


def test_on_finish_given_gate_blocked_refuses_and_notices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E6.4.3 red path: a failing evaluator self-test blocks Finish."""
    engine, _clock = _running_engine()
    monkeypatch.setattr(console_module, "FINISH_GATE", lambda: False)
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_finish()

    assert engine.state is RideStatus.RUNNING
    assert view.last_state is None  # no set_state ran on the blocked path
    assert view.last_notice == "Finish blocked: evaluator self-test did not pass"


def test_on_finish_given_draft_ride_shows_a_notice() -> None:
    """Negative: Finish before Start is refused by the engine."""
    engine, _clock = _make_engine()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_finish()

    assert view.last_notice == "Cannot finish: cannot finish from draft"
    assert engine.state is RideStatus.DRAFT


# ------------------------------------------------------------- reopen


def test_on_reopen_given_finished_ride_moves_console_to_reopened() -> None:
    """E5.4.1: Reopen Ride moves FINISHED -> REOPENED and refreshes."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=100)
    engine.finish()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_reopen()

    assert engine.state is RideStatus.REOPENED
    assert view.last_state is RideStatus.REOPENED
    assert view.last_notice == "Ride reopened for corrections"


def test_on_reopen_given_draft_ride_shows_a_notice() -> None:
    """Negative: reopening a ride that is not FINISHED is refused."""
    engine, _clock = _make_engine()
    view = FakeConsoleView()
    presenter = _make_presenter(engine, view)

    presenter.on_reopen()

    assert view.last_notice == "Cannot reopen: cannot reopen from draft"
    assert engine.state is RideStatus.DRAFT


# ---------------------------------------------------- negative import


def test_finish_gate_is_a_module_level_callable_defaulting_to_clear() -> None:
    """The hook E6.4.3 rewires is importable and green by default."""
    assert callable(console_module.FINISH_GATE)
    assert console_module.FINISH_GATE() is True


# ------------------------------------------- T-3/T-7 closure tests


def test_rejection_notice_given_an_unknown_reason_still_names_the_plate() -> None:
    """T-3 fallback: a reason the engine does not emit stays honest."""
    notice = console_module._rejection_notice("12", "mystery_reason")

    assert notice == "Plate rejected: mystery_reason"


@given(seconds=st.floats(min_value=0.0, max_value=360000.0))
def test_format_duration_round_trips_through_its_hms_parts(seconds: float) -> None:
    """T-7: h:mm:ss formatting is invertible back to whole seconds."""
    hours, rest = format_duration(seconds).split(":", 1)
    minutes, secs = rest.split(":", 1)

    assert int(hours) * 3600 + int(minutes) * 60 + int(secs) == int(seconds)


def test_finish_gate_consults_the_evaluator_self_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E6.4.3: the gate is the self-test's green report (R-44)."""

    class _Red:
        """A report whose suite failed."""

        passed = False

    class _Green:
        """A report whose suite passed."""

        passed = True

    monkeypatch.setattr(console_module.hands, "self_test", _Green)
    assert console_module.FINISH_GATE() is True
    monkeypatch.setattr(console_module.hands, "self_test", _Red)
    assert console_module.FINISH_GATE() is False
