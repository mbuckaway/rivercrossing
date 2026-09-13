# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the data_source module's display helpers and source.

``DataSource`` / ``EmptyDataSource`` behaviour is driven through the
console, results and demo suites (test_console.py, test_results.py,
test_demo.py). These tests pin what those suites only reach indirectly:
the module's two pure display formatters -- the one-hour switch of
``_format_lap_time`` (durations past ``h:mm:ss``) and ``_event_time``'s
refusal arm for a payload timestamp that is not ISO-8601 -- plus J1's
per-rider crossing attribution, where ``EngineDataSource``'s feed rows
name the rider whose plate the operator typed and fall back to the
entry's plate/team name only when no roster rider owns that plate.
Pure functions over plain values -- no wx, no I/O, so there is nothing
to fake or mock (T-10).
"""

from datetime import datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from conftest import gorba_config
from rivercrossing.cards import Shoe
from rivercrossing.ride import Event, RideEngine
from rivercrossing.roster import Entry, EntryMode, EntryType, PlateModel, Rider, Roster
from rivercrossing.ui.presenters import data_source as data_source_module
from rivercrossing.ui.presenters.data_source import EngineDataSource

# ------------------------------------------------- lap-time format


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (3599, "59:59"),  # one second under the hour: still m:ss
        (3600, "1:00:00"),  # the exact hour flips the h:mm:ss arm on
        (3601, "1:00:01"),  # one second past the hour
        (7200, "2:00:00"),  # two full hours
    ],
    ids=["under_hour_control", "exact_hour", "one_second_past_hour", "two_hours"],
)
def test_format_lap_time_given_duration_of_an_hour_or_more_renders_h_mm_ss(
    seconds: float, expected: str
) -> None:
    """Lap times >= 1 h switch from m:ss to h:mm:ss (no zero pad)."""
    assert data_source_module._format_lap_time(seconds) == expected


# ------------------------------------------- elapsed origin (feed Time)
# The feed's Time column is the ride clock's reading at the crossing --
# ``crossed_at`` minus ``actual_start`` -- not the wall-clock instant.


def test_elapsed_seconds_given_a_ride_that_never_started_returns_zero() -> None:
    """T-4 nullable boundary: no start means no elapsed origin.

    A DRAFT ride has no ``actual_start``; the console's own clock shows
    ``0:00:00`` there (``ConsolePresenter._refresh_clock``), so the
    feed's elapsed reading follows the same convention.
    """
    assert data_source_module._elapsed_seconds(_dt(10, 2), None) == 0.0


@pytest.mark.parametrize(
    "offset_s",
    [0, 1, 3599, 3600, 3601, 86_400],
    ids=["zero", "one_second", "under_hour", "exact_hour", "past_hour", "one_day"],
)
def test_elapsed_seconds_given_an_offset_from_the_start_returns_the_gap(
    offset_s: int,
) -> None:
    """``0`` is the gun itself; every later reading is the true gap."""
    start = _dt(10, 0)

    elapsed = data_source_module._elapsed_seconds(start + timedelta(seconds=offset_s), start)

    assert elapsed == float(offset_s)


def test_elapsed_seconds_given_a_crossing_before_the_start_is_negative() -> None:
    """T-4 min-1: a back-dated gun leaves earlier crossings negative.

    The rendered cell clamps (``format_duration``); the numeric
    companion keeps the real reading so the Time sort stays honest.
    """
    start = _dt(10, 0)

    elapsed = data_source_module._elapsed_seconds(start - timedelta(seconds=1), start)

    assert elapsed == -1.0


@given(
    offset_s=st.integers(min_value=-86_400, max_value=86_400),
    start_s=st.integers(min_value=0, max_value=86_400),
)
def test_elapsed_seconds_given_any_instant_is_the_exact_second_gap(
    offset_s: int, start_s: int
) -> None:
    """Property: the reading is exactly ``crossed_at - start``."""
    start = _dt(10, 0) + timedelta(seconds=start_s)

    elapsed = data_source_module._elapsed_seconds(start + timedelta(seconds=offset_s), start)

    assert elapsed == float(offset_s)


# ----------------------------------------------------- event time


def test_event_time_given_non_iso_timestamp_renders_empty_string() -> None:
    """An unparseable payload timestamp refuses: the row shows ""."""
    event = Event(
        action="record_crossing",
        payload={"entry_id": "12", "crossed_at": "not an ISO timestamp"},
    )

    assert data_source_module._event_time(event) == ""


def test_event_time_given_payload_without_timestamp_key_renders_empty_string() -> None:
    """A payload with no ISO timestamp key renders "" (exhausted)."""
    event = Event(action="stop", payload={"entry_id": "12", "reason": "track blocked"})

    assert data_source_module._event_time(event) == ""


def test_event_time_given_iso_timestamp_renders_hh_mm_ss() -> None:
    """A parseable naive timestamp renders as stored, 24-hour clock."""
    event = Event(
        action="record_crossing",
        payload={"entry_id": "12", "crossed_at": "2026-09-20T10:30:00"},
    )

    assert data_source_module._event_time(event) == "10:30:00"


# --------------------------------------- per-rider crossing attribution


def _dt(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Build a naive datetime on the fixed event day, Sept 20, 2026."""
    return datetime(2026, 9, 20, hour, minute, second)  # noqa: DTZ001 -- naive by design


def _frozen_clock() -> datetime:
    """Return the event start; tests stamp crossings explicitly."""
    return _dt(10, 0)


def _running_engine(roster: Roster) -> RideEngine:
    """Build a RUNNING engine over *roster* on the GORBA config."""
    config = gorba_config(min_lap_s=1)
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    engine = RideEngine(config=config, shoe=shoe, clock=_frozen_clock, roster=roster)
    engine.start()
    return engine


def _pooled_team_roster() -> Roster:
    """Build a rider_pooled team roster: Sarah (45), Priya (9)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(first_name="Sarah", plate="45"), Rider(first_name="Priya", plate="9")],
    )
    return roster


def _relay_team_roster() -> Roster:
    """Build a team_relay roster whose riders carry no plate (S1)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(first_name="Sarah"), Rider(first_name="Priya")],
        plate="9",
    )
    return roster


def test_feed_rows_given_pooled_rider_plate_shows_the_typed_rider_not_the_team() -> None:
    """J1: the feed names the rider who typed a plate."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 2))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert [(row.plate, row.entry) for row in feed] == [("45", "Sarah")]


def test_feed_rows_given_team_relay_ride_keeps_the_entry_plate_and_team_name() -> None:
    """A relay rider carries no plate: the entry identity stands."""
    roster = _relay_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("9", at=_dt(10, 2))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert [(row.plate, row.entry) for row in feed] == [("9", "Dirt Dynamos")]


def test_feed_rows_given_solo_rider_shows_their_own_name_and_plate() -> None:
    """A solo rider shows their own name and entry plate."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Amy", plate="12")
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert [(row.plate, row.entry) for row in feed] == [("12", "Amy")]


def test_rider_name_for_given_matching_plate_returns_that_riders_full_name() -> None:
    """The resolver returns the owning rider's display name."""
    entry = Entry(
        plate="9",
        display_name="Dirt Dynamos",
        type=EntryType.TEAM,
        riders=[Rider(first_name="Sarah", plate="45"), Rider(first_name="Priya", plate="9")],
    )

    assert data_source_module._rider_name_for(entry, "45") == "Sarah"


def test_rider_name_for_given_no_entry_returns_none() -> None:
    """An unresolvable entry contributes no rider name."""
    assert data_source_module._rider_name_for(None, "45") is None


def test_rider_name_for_given_riders_without_plates_returns_none() -> None:
    """Relay riders hold no plate, so no typed plate matches."""
    entry = Entry(
        plate="9",
        display_name="Dirt Dynamos",
        type=EntryType.TEAM,
        riders=[Rider(first_name="Sarah"), Rider(first_name="Priya")],
    )

    assert data_source_module._rider_name_for(entry, "9") is None


def test_rider_name_for_given_no_typed_plate_returns_none() -> None:
    """A crossing with no recorded rider plate names no rider."""
    entry = Entry(
        plate="9",
        display_name="Dirt Dynamos",
        type=EntryType.TEAM,
        riders=[Rider(first_name="Sarah", plate="45"), Rider(first_name="Priya", plate="9")],
    )

    assert data_source_module._rider_name_for(entry, None) is None


@given(
    riders=st.lists(
        st.builds(
            Rider,
            first_name=st.text(min_size=1),
            plate=st.integers(min_value=1, max_value=99).map(str),
        ),
        max_size=4,
        unique_by=lambda rider: rider.plate,
    ),
    query=st.text(),
)
def test_rider_name_for_given_any_entry_matches_the_rider_whose_plate_it_was(
    riders: list[Rider], query: str
) -> None:
    """The resolver is exactly the rider whose plate matches."""
    entry = Entry(plate="9", display_name="Dirt Dynamos", type=EntryType.TEAM, riders=riders)

    result = data_source_module._rider_name_for(entry, query)

    assert result == next((rider.full_name for rider in riders if rider.plate == query), None)


# ------------------------------------------------------------- misses
# A pending miss (K) is a passing whose number the scorer missed. It
# never enters _crossings: the feed synthesises a "-"/"missed" row with
# blank numeric cells, and the counters/standings never see it.


def test_feed_rows_given_a_pending_miss_renders_the_miss_row() -> None:
    """Plate "-", Name "missed", blank card/lap-time/total, no lap."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert (feed[0].plate, feed[0].entry, feed[0].missed) == ("-", "missed", True)
    assert (feed[0].lap, feed[0].card, feed[0].lap_time, feed[0].total) == (0, "", "", "")


def test_feed_rows_given_a_pending_miss_shows_the_miss_elapsed_time() -> None:
    """The miss row carries its own elapsed reading, like every row."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert feed[0].time == "0:02:00"


def test_feed_rows_given_a_miss_then_a_crossing_keeps_the_crossing_newest() -> None:
    """The feed stays newest-first across the miss/crossing merge."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    engine.record_crossing("45", at=_dt(10, 3))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert [(row.plate, row.missed) for row in feed] == [("45", False), ("-", True)]


def test_feed_rows_given_a_miss_between_crossings_orders_newest_first() -> None:
    """A miss slots between the crossings recorded either side of it."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 1))
    engine.record_miss(_dt(10, 2), reason="missed number")
    engine.record_crossing("45", at=_dt(10, 3))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert [(row.plate, row.missed) for row in feed] == [
        ("45", False),
        ("-", True),
        ("45", False),
    ]


def test_feed_rows_given_no_pending_misses_returns_only_crossings() -> None:
    """The merge leaves an all-crossing feed exactly as it was."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 1))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert [(row.plate, row.missed) for row in feed] == [("45", False)]


def test_counters_given_a_pending_miss_are_unchanged() -> None:
    """A miss moves no counter: not a crossing, no card dealt."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    before = EngineDataSource(engine, roster).counters()

    engine.record_miss(_dt(10, 2), reason="missed number")

    assert EngineDataSource(engine, roster).counters() == before


def test_feed_rows_given_an_assigned_miss_marks_the_recorded_row_edited() -> None:
    """assign_plate_to_miss is a correction: the row renders edited."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    engine.assign_plate_to_miss(1, "45", reason="rider identified")
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert [(row.plate, row.edited) for row in feed] == [("45", True)]


def test_results_stale_given_an_assigned_miss_is_true_after_the_watermark() -> None:
    """An assigned miss flags published results stale (E7.3.2)."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    watermark = len(engine.events)
    engine.assign_plate_to_miss(1, "45", reason="rider identified")
    source = EngineDataSource(engine, roster)

    assert source.results_stale(watermark) is True


def test_feed_rows_given_a_pending_miss_carries_its_miss_seq() -> None:
    """The miss row resolves to the pending miss through miss_seq."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    engine.record_miss(_dt(10, 5), reason="missed number")
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert [row.miss_seq for row in feed] == [2, 1]


def test_feed_rows_given_a_crossing_row_carries_no_miss_seq() -> None:
    """A real crossing row is never mistaken for a miss resolution."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 2))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert feed[0].miss_seq is None


# ------------------------------------------------- elapsed feed Time
# The feed's Time column reads the ride clock at the crossing -- elapsed
# since ``actual_start`` -- so the first row starts at zero and every
# later row counts up, matching the console's Elapsed label.


def test_feed_rows_given_a_crossing_at_the_gun_renders_the_zero_elapsed() -> None:
    """The first lap at ``actual_start`` reads 0:00:00."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 0))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert feed[0].time == "0:00:00"


def test_feed_rows_given_a_later_crossing_renders_its_own_elapsed_reading() -> None:
    """Two minutes past the gun reads 0:02:00, not the wall clock."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 2))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert feed[0].time == "0:02:00"


def test_feed_rows_given_a_back_dated_start_follows_the_new_gun() -> None:
    """``set_start_time`` moves the feed's own elapsed origin."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 2))
    engine.set_start_time(_dt(10, 0, 30))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert feed[0].time == "0:01:30"


def test_feed_rows_given_a_crossing_before_a_back_dated_gun_renders_the_zero_clock() -> None:
    """T-4 min-1: the cell clamps at zero past a back-dated gun."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 2))
    engine.set_start_time(_dt(10, 3))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert (feed[0].time, feed[0].elapsed_s) == ("0:00:00", -60.0)


def test_feed_rows_given_a_crossing_carries_its_elapsed_seconds_for_sorting() -> None:
    """The numeric companion sorts the Time column numerically."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 2))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert feed[0].elapsed_s == 120.0


def test_feed_rows_given_a_crossing_carries_its_lap_and_total_seconds() -> None:
    """Lap time and Total keep numeric companions too."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 2))
    engine.record_crossing("45", at=_dt(10, 5))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert (feed[0].lap_time_s, feed[0].total_s) == (180.0, 300.0)


# ------------------------------------------------------------ the cap
# The feed used to stop at R-32's 30 rows; the console now scrolls the
# whole ride, so every crossing and every pending miss is a row.


def test_feed_rows_given_more_than_thirty_crossings_returns_every_row() -> None:
    """Past the old 30-row cap the feed keeps every crossing."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    for minute in range(31):
        engine.record_crossing("45", at=_dt(11, minute))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert len(feed) == 31


def test_feed_rows_given_more_than_thirty_crossings_still_returns_newest_first() -> None:
    """Newest first, with the oldest crossing last (no truncation)."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    for minute in range(31):
        engine.record_crossing("45", at=_dt(11, minute))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert (feed[0].lap, feed[-1].lap) == (31, 1)


def test_feed_rows_given_a_miss_and_more_than_thirty_crossings_keeps_the_miss() -> None:
    """The merged feed keeps the miss too -- the cap's casualty."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    for minute in range(31):
        engine.record_crossing("45", at=_dt(11, minute))
    engine.record_miss(_dt(11, 30), reason="missed number")
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert [row.missed for row in feed].count(True) == 1
    assert len(feed) == 32


# ------------------------------------------------------------- DNF rows
# A DNF rider's crossings are marked so the operator can see who is out
# of the standings. The row's own plate decides (a pooled team's other
# riders keep running), and a wholly-DNF entry marks every row it owns.


def test_feed_rows_given_a_solo_dnf_entry_marks_its_rows() -> None:
    """A solo rider's DNF is the entry's DNF, so its row is marked."""
    roster = _pooled_team_roster()
    roster.create_solo_entry(first_name="Amy", plate="12")
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.mark_dnf("12", reason="withdrawn")
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert feed[0].dnf is True


def test_feed_rows_given_a_healthy_entry_leaves_its_rows_unmarked() -> None:
    """T-3 negative: nothing marked means no row is DNF."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 2))
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert feed[0].dnf is False


def test_feed_rows_given_a_pooled_rider_dnf_marks_only_that_riders_rows() -> None:
    """One rider out leaves the team's other riders' rows unmarked."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 1))
    engine.record_crossing("9", at=_dt(10, 2))
    engine.mark_dnf("45", reason="mechanical failure")
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert [(row.plate, row.dnf) for row in feed] == [("9", False), ("45", True)]


def test_feed_rows_given_a_wholly_dnf_pooled_team_marks_every_row() -> None:
    """Every rider out means every one of its rows is marked."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 1))
    engine.record_crossing("9", at=_dt(10, 2))
    engine.mark_dnf("45", reason="mechanical failure")
    engine.mark_dnf("9", reason="mechanical failure")
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert [(row.plate, row.dnf) for row in feed] == [("9", True), ("45", True)]


def test_feed_rows_given_a_relay_entry_dnf_marks_its_plate_rows() -> None:
    """A relay rider carries no plate: the entry's DNF marks it."""
    roster = _relay_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("9", at=_dt(10, 2))
    engine.mark_dnf("9", reason="withdrawn")
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert feed[0].dnf is True


def test_feed_rows_given_a_pending_miss_since_a_dnf_is_never_marked() -> None:
    """A miss has no entry, so no DNF mark can reach its row."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.mark_dnf("45", reason="mechanical failure")
    engine.record_miss(_dt(10, 2), reason="missed number")
    source = EngineDataSource(engine, roster)

    feed = source.feed_rows()

    assert feed[0].dnf is False
