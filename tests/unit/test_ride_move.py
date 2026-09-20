# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for RideEngine's pooled live rider moves (E3.1.2, R-17).

The two engine-level move primitives -- ``move_rider`` (team to team,
or solo to team) and ``extract_rider_to_solo`` (team to solo) -- plus
the direction-agnostic re-attribution core they share. Each is gated
on a stopped-running or REOPENED ride and a ``rider_pooled`` model,
moves the rider's *data* (live laps, held card, credited cards,
restored voided laps) with them, and appends one replayed audit
event. The hard gate is replay equivalence: the live and replayed
crossings, hold queue, credited hands and voided record must be
byte-identical.

The arrange-time builders (``_config``/``_dt``/``_make_engine``/
``_two_team_pooled_roster``) are imported from ``test_ride_corrections``
so the two suites' fixture values cannot drift; this repo otherwise
keeps every test module self-contained.
"""

import re

import pytest
from test_ride_corrections import _config, _dt, _make_engine, _two_team_pooled_roster

from conftest import entry_key, restore_entry_keys
from rivercrossing import ride as ride_module
from rivercrossing.ride import (
    Event,
    IllegalStateError,
    RideConfig,
    RideEngine,
    UnknownPlateError,
)
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster

# ------------------------------------------------------------- fixtures


def _solo_and_team_roster() -> Roster:
    """Build a pooled roster: solo rider "5" plus two-rider Team B."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Sol", last_name="", plate="5")
    roster.create_team_entry(
        display_name="Team B",
        riders=[
            Rider(first_name="Cleo", last_name="", plate="3"),
            Rider(first_name="Dana", last_name="", plate="4"),
        ],
    )
    return roster


def _started(roster: Roster, *, config: RideConfig | None = None) -> RideEngine:
    """Build an engine RUNNING on *roster* since 10:00 (arrange)."""
    engine, _clock = _make_engine(roster=roster, config=config)
    engine.start(at=_dt(10, 0))
    return engine


def _state(engine: RideEngine) -> tuple[object, ...]:
    """Return every replayable projection of *engine* (arrange).

    ``Store.load_engine`` must rebuild the identical ride, so these
    are compared byte for byte. Every ``Crossing``/``Card`` is a
    frozen value, so tuple equality covers an entry's stable key, its
    seq, the crossing instant, the rider plate it was filed under and
    the card code.
    """
    return (
        engine.crossings,
        engine.held_crossings(),
        tuple(engine.credited_cards(entry.key) for entry in engine._roster.entries),
        tuple(engine._voided),
    )


def _replay(engine: RideEngine, roster: Roster) -> RideEngine:
    """Rebuild *engine* the way ``Store.load_engine`` does (arrange).

    A fresh same-config engine over the FINAL *roster* -- its entry
    keys restored from the live engine's, which is what
    ``Store.save_roster``/``_load_roster`` round-trip -- with the live
    engine's own event log applied.
    """
    replayed, _clock = _make_engine(roster=roster, config=engine.config)
    restore_entry_keys(replayed, engine)
    for event in engine.events:
        replayed.apply(event)
    return replayed


def _final_two_team_roster() -> Roster:
    """Build the two-team roster a team-to-team replay is handed.

    The membership change the live roster already carries -- rider "2"
    now on Team B -- applied to a fresh roster whose entries are in the
    same order, which is what ``Store`` reloads.
    """
    roster = _two_team_pooled_roster()
    _team_a, team_b = roster.entries
    rider = next(member for member in _team_a.riders if member.plate == "2")
    roster.move_rider(rider, to_entry=team_b)
    return roster


def _final_extracted_roster() -> Roster:
    """Build the roster a team-to-solo replay is handed.

    Rider "2" already sits in the solo entry the extraction minted,
    exactly as ``Store`` reloads it.
    """
    roster = _two_team_pooled_roster()
    rider = next(member for member in roster.entries[0].riders if member.plate == "2")
    roster.extract_rider_to_solo(rider)
    return roster


# ================================================= team -> team move


def test_move_rider_team_to_team_reattributes_the_riders_live_crossings() -> None:
    """A stopped pooled move re-keys the rider's laps to the team."""
    roster = _two_team_pooled_roster()
    team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("1", at=_dt(10, 30))
    engine.record_crossing("2", at=_dt(11, 0))
    engine.record_crossing("2", at=_dt(11, 30))
    engine.stop()

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert [(c.entry_id, c.rider_plate) for c in engine.crossings] == [
        (team_a.key, "1"),
        (team_b.key, "2"),
        (team_b.key, "2"),
    ]


def test_move_rider_team_to_team_renumbers_the_source_teams_later_laps() -> None:
    """Two removed laps close both source gaps: survivors stay 1..N."""
    roster = _two_team_pooled_roster()
    team_a, _team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("2", at=_dt(10, 30))  # Team A seq 1, moves
    engine.record_crossing("2", at=_dt(11, 0))  # Team A seq 2, moves
    engine.record_crossing("1", at=_dt(11, 30))  # Team A seq 3
    engine.record_crossing("1", at=_dt(12, 0))  # Team A seq 4
    engine.stop()

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert [(c.seq, c.rider_plate) for c in engine._laps_for(team_a.key)] == [
        (1, "1"),
        (2, "1"),
    ]


def test_move_rider_team_to_team_appends_the_arrivals_after_the_destinations_laps() -> None:
    """The destination numbers the arrivals after its own laps."""
    roster = _two_team_pooled_roster()
    _team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("3", at=_dt(10, 30))  # Team B seq 1
    engine.record_crossing("2", at=_dt(11, 0))  # Team A seq 1, moves
    engine.record_crossing("2", at=_dt(11, 30))  # Team A seq 2, moves
    engine.stop()

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert [(c.seq, c.rider_plate) for c in engine._laps_for(team_b.key)] == [
        (1, "3"),
        (2, "2"),
        (3, "2"),
    ]


def test_move_rider_team_to_team_appends_one_audit_event_naming_both_keys() -> None:
    """move_rider writes exactly one event carrying both stable keys."""
    roster = _two_team_pooled_roster()
    team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("2", at=_dt(10, 30))
    engine.stop()
    before = len(engine.events)

    event = engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert event == Event(
        action="move_rider",
        payload={
            "rider_plate": "2",
            "from_key": team_a.key,
            "to_team": "Team B",
            "to_key": team_b.key,
            "reason": "rider swapped teams",
        },
    )
    assert len(engine.events) == before + 1  # exactly one move row
    assert engine.events[-1] == event


def test_move_rider_team_to_team_moves_a_rider_with_no_recorded_laps() -> None:
    """A rider who has not crossed yet moves with an empty record."""
    roster = _two_team_pooled_roster()
    _team_a, team_b = roster.entries
    engine = _started(roster)
    engine.stop()

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert engine.crossings == ()
    assert engine.credited_cards(team_b.key) == ()
    assert [rider.plate for rider in team_b.riders] == ["3", "4", "2"]


def test_move_rider_team_to_team_leaves_the_other_members_data_alone() -> None:
    """Only the moved rider's laps and cards change hands."""
    roster = _two_team_pooled_roster()
    team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("1", at=_dt(10, 30))
    engine.record_crossing("2", at=_dt(11, 0))
    engine.stop()
    stay_card = engine.card_for(engine.crossings[0])
    move_card = engine.card_for(engine.crossings[1])

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert [c.rider_plate for c in engine._laps_for(team_a.key)] == ["1"]
    assert engine.credited_cards(team_a.key) == (stay_card,)
    assert engine.credited_cards(team_b.key) == (move_card,)


def test_move_rider_team_to_team_moves_the_credited_cards_and_their_tag() -> None:
    """Cards travel with the rider tag a per-rider DNF forfeits on."""
    roster = _two_team_pooled_roster()
    _team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("2", at=_dt(10, 30))
    engine.record_crossing("1", at=_dt(11, 0))
    engine.record_crossing("2", at=_dt(11, 30))
    engine.stop()
    first = engine.card_for(engine.crossings[0])
    second = engine.card_for(engine.crossings[2])

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert engine._hand[team_b.key] == [(first, "2"), (second, "2")]


def test_move_rider_team_to_team_keeps_a_manual_card_with_its_entry() -> None:
    """A bonus card's ``None`` tag keeps it off the move."""
    roster = _two_team_pooled_roster()
    team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("2", at=_dt(10, 30))
    crossing_card = engine.card_for(engine.crossings[0])
    engine.deal_manual("2", reason="bonus card")
    manual_card = engine.credited_cards(team_a.key)[-1]
    engine.stop()

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert engine.credited_cards(team_a.key) == (manual_card,)
    assert engine.credited_cards(team_b.key) == (crossing_card,)


def test_move_rider_team_to_team_moves_a_held_card_still_held() -> None:
    """A short lap's card stays in the hold queue, under the new key."""
    roster = _two_team_pooled_roster()
    _team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("2", at=_dt(10, 0, 30))  # 30 s -> held
    engine.stop()
    held_card = engine.held_crossings()[0].card

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert [
        (item.crossing.entry_id, item.crossing.rider_plate, item.card)
        for item in engine.held_crossings()
    ] == [(team_b.key, "2", held_card)]
    assert engine.credited_cards(team_b.key) == ()


def test_move_rider_team_to_team_leaves_a_voided_card_voided() -> None:
    """A void_card void is card-level: the moved lap stays cardless."""
    roster = _two_team_pooled_roster()
    _team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("2", at=_dt(10, 30))
    card = engine.card_for(engine.crossings[0])
    engine.void_card("2", card, reason="wrong card dealt")
    engine.stop()

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    moved = engine.crossings[0]
    assert (moved.entry_id, engine.card_for(moved)) == (team_b.key, card)
    assert engine.is_card_voided(card) is True
    assert engine.credited_cards(team_b.key) == ()


def test_move_rider_team_to_team_restores_a_voided_crossing_to_the_destination() -> None:
    """A void_crossing lap of the rider travels and re-credits."""
    roster = _two_team_pooled_roster()
    team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("2", at=_dt(10, 30))
    engine.record_crossing("2", at=_dt(11, 0))
    first = engine.card_for(engine.crossings[0])
    voided_card = engine.card_for(engine.crossings[1])
    engine.void_crossing(team_a.key, 2, reason="double entry")
    engine.stop()

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert [(c.seq, c.crossed_at) for c in engine._laps_for(team_b.key)] == [
        (1, _dt(10, 30)),
        (2, _dt(11, 0)),
    ]
    assert engine.is_card_voided(voided_card) is False
    assert engine.credited_cards(team_b.key) == (first, voided_card)
    assert engine._voided == []


def test_move_rider_team_to_team_reholds_a_short_voided_crossing() -> None:
    """A restored short lap re-enters the hold queue at the new team."""
    roster = _two_team_pooled_roster()
    team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("2", at=_dt(10, 30))  # 1800 s -> credited
    engine.record_crossing("2", at=_dt(10, 35))  # 300 s -> held, then voided
    first = engine.card_for(engine.crossings[0])
    short_card = engine.card_for(engine.crossings[1])
    engine.void_crossing(team_a.key, 2, reason="double entry")
    engine.stop()

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert [(item.crossing.entry_id, item.card) for item in engine.held_crossings()] == [
        (team_b.key, short_card)
    ]
    assert engine.credited_cards(team_b.key) == (first,)


def test_move_rider_team_to_team_credits_a_short_restored_lap_when_hold_is_off() -> None:
    """With hold_short_laps off, a restored short lap credits."""
    roster = _two_team_pooled_roster()
    team_a, team_b = roster.entries
    engine = _started(roster, config=_config(hold_short_laps=False))
    engine.record_crossing("2", at=_dt(10, 30))
    engine.record_crossing("2", at=_dt(10, 35))  # short, credited: always-deal
    first = engine.card_for(engine.crossings[0])
    short_card = engine.card_for(engine.crossings[1])
    engine.void_crossing(team_a.key, 2, reason="double entry")
    engine.stop()

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert engine.held_crossings() == ()
    assert engine.credited_cards(team_b.key) == (first, short_card)


def test_move_rider_team_to_team_leaves_another_riders_voided_lap_alone() -> None:
    """The restore pass selects the moved rider's plate alone."""
    roster = _two_team_pooled_roster()
    team_a, _team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("1", at=_dt(10, 30))
    engine.record_crossing("2", at=_dt(11, 0))
    stay_card = engine.card_for(engine.crossings[0])
    engine.void_crossing(team_a.key, 1, reason="double entry")
    engine.stop()

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert engine.is_card_voided(stay_card) is True
    assert len(engine._voided) == 1
    assert engine.credited_cards(team_a.key) == ()


def test_move_rider_of_the_anchor_rider_rekeys_both_teams_with_no_collision() -> None:
    """Moving A's anchor re-derives both plates; the keys stay."""
    roster = _two_team_pooled_roster()
    team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("1", at=_dt(10, 30))
    engine.record_crossing("2", at=_dt(11, 0))
    engine.stop()

    engine.move_rider("1", to_team="Team B", reason="rider swapped teams")

    assert [entry.plate for entry in engine._roster.entries] == ["2", "1"]
    assert [(c.entry_id, c.rider_plate) for c in engine.crossings] == [
        (team_a.key, "2"),
        (team_b.key, "1"),
    ]


# ================================================= team -> solo move


def test_extract_rider_to_solo_moves_the_riders_laps_to_the_new_key() -> None:
    """An extracted rider's laps and cards land on the new solo key."""
    roster = _two_team_pooled_roster()
    team_a, _team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("1", at=_dt(10, 30))
    engine.record_crossing("2", at=_dt(11, 0))
    engine.record_crossing("2", at=_dt(11, 30))
    engine.stop()
    stay_card = engine.card_for(engine.crossings[0])
    moved_cards = (engine.card_for(engine.crossings[1]), engine.card_for(engine.crossings[2]))

    engine.extract_rider_to_solo("2", reason="rider rides alone")

    solo_key = entry_key(engine._roster, "2")
    assert [(c.entry_id, c.rider_plate) for c in engine.crossings] == [
        (team_a.key, "1"),
        (solo_key, "2"),
        (solo_key, "2"),
    ]
    assert engine.credited_cards(solo_key) == moved_cards
    assert engine.credited_cards(team_a.key) == (stay_card,)


def test_extract_rider_to_solo_appends_one_audit_event_naming_both_keys() -> None:
    """extract_rider_to_solo writes one event with both stable keys."""
    roster = _two_team_pooled_roster()
    team_a, _team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("2", at=_dt(10, 30))
    engine.stop()

    event = engine.extract_rider_to_solo("2", reason="rider rides alone")

    assert event == Event(
        action="extract_rider_to_solo",
        payload={
            "rider_plate": "2",
            "from_key": team_a.key,
            "to_key": entry_key(engine._roster, "2"),
            "reason": "rider rides alone",
        },
    )
    assert engine.events[-1] == event


def test_extract_rider_to_solo_leaves_the_other_members_laps_behind() -> None:
    """The source team keeps its remaining member's laps and cards."""
    roster = _two_team_pooled_roster()
    team_a, _team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("1", at=_dt(10, 30))
    engine.record_crossing("2", at=_dt(11, 0))
    engine.stop()
    stay_card = engine.card_for(engine.crossings[0])

    engine.extract_rider_to_solo("2", reason="rider rides alone")

    assert [(c.seq, c.rider_plate) for c in engine._laps_for(team_a.key)] == [(1, "1")]
    assert engine.credited_cards(team_a.key) == (stay_card,)


# ================================================= solo -> team move


def test_move_rider_solo_to_team_moves_the_solo_laps_onto_the_team_key() -> None:
    """A whole solo entry's laps and cards move onto the team."""
    roster = _solo_and_team_roster()
    solo, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("5", at=_dt(10, 30))
    engine.record_crossing("5", at=_dt(11, 0))
    engine.stop()
    cards = (engine.card_for(engine.crossings[0]), engine.card_for(engine.crossings[1]))

    engine.move_rider("5", to_team="Team B", reason="rider joins a team")

    assert [entry.plate for entry in engine._roster.entries] == ["3"]
    assert [(c.entry_id, c.seq) for c in engine._laps_for(team_b.key)] == [
        (team_b.key, 1),
        (team_b.key, 2),
    ]
    assert engine._laps_for(solo.key) == ()
    assert engine.credited_cards(team_b.key) == cards


def test_move_rider_solo_to_team_audits_the_dissolved_solo_key() -> None:
    """The payload names the solo entry the move dissolved."""
    roster = _solo_and_team_roster()
    solo, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("5", at=_dt(10, 30))
    engine.stop()

    event = engine.move_rider("5", to_team="Team B", reason="rider joins a team")

    assert event == Event(
        action="move_rider",
        payload={
            "rider_plate": "5",
            "from_key": solo.key,
            "to_team": "Team B",
            "to_key": team_b.key,
            "reason": "rider joins a team",
        },
    )
    assert engine.events[-1] == event


# ====================================================== move refusals


def test_move_rider_while_running_and_not_stopped_refuses_with_stop_first() -> None:
    """The live cell of the gate: stop the clock before a move."""
    engine = _started(_two_team_pooled_roster())

    with pytest.raises(IllegalStateError, match=re.escape("Stop the ride first")):
        engine.move_rider("2", to_team="Team B", reason="rider swapped teams")


def test_extract_rider_to_solo_while_running_and_not_stopped_refuses() -> None:
    """Extraction shares the same stopped-running gate."""
    engine = _started(_two_team_pooled_roster())

    with pytest.raises(IllegalStateError, match=re.escape("Stop the ride first")):
        engine.extract_rider_to_solo("2", reason="rider rides alone")


def test_move_rider_on_a_draft_ride_raises_illegal_state_error_naming_draft() -> None:
    """DRAFT refuses the move and names the state."""
    engine, _clock = _make_engine(roster=_two_team_pooled_roster())

    with pytest.raises(IllegalStateError, match=re.escape("cannot move a rider from draft")):
        engine.move_rider("2", to_team="Team B", reason="rider swapped teams")


def test_extract_rider_to_solo_on_a_draft_ride_raises_illegal_state_error() -> None:
    """DRAFT refuses the extraction and names the state."""
    engine, _clock = _make_engine(roster=_two_team_pooled_roster())

    with pytest.raises(IllegalStateError, match=re.escape("cannot move a rider from draft")):
        engine.extract_rider_to_solo("2", reason="rider rides alone")


def test_move_rider_on_a_finished_ride_raises_illegal_state_error_naming_finished() -> None:
    """FINISHED refuses the move: corrections go through Reopen."""
    engine = _started(_two_team_pooled_roster())
    engine.finish()

    with pytest.raises(IllegalStateError, match=re.escape("cannot move a rider from finished")):
        engine.move_rider("2", to_team="Team B", reason="rider swapped teams")


def test_move_rider_on_a_team_relay_ride_raises_illegal_state_error() -> None:
    """A relay ride is refused: its plate *is* the team."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.create_team_entry(
        display_name="Relay A",
        riders=[
            Rider(first_name="Ada", last_name="", plate="1"),
            Rider(first_name="Bea", last_name="", plate="2"),
        ],
        plate="10",
    )
    engine = _started(roster, config=_config(plate_model=PlateModel.TEAM_RELAY))
    engine.stop()

    with pytest.raises(IllegalStateError, match=re.escape("rider moves need a rider_pooled ride")):
        engine.move_rider("10", to_team="Relay A", reason="rider swapped teams")


def test_move_rider_onto_the_riders_own_team_raises_illegal_state_error() -> None:
    """A same-team move is refused, never a silent re-key."""
    engine = _started(_two_team_pooled_roster())
    engine.stop()

    with pytest.raises(IllegalStateError, match=re.escape("is already on team Team A")):
        engine.move_rider("2", to_team="Team A", reason="rider swapped teams")


@pytest.mark.parametrize("reason", ["", " ", "\t\n"])
def test_move_rider_with_a_blank_reason_raises_value_error(reason: str) -> None:
    """Every move is audited, so a blank reason is refused."""
    engine = _started(_two_team_pooled_roster())
    engine.stop()

    with pytest.raises(ValueError, match=re.escape("reason must not be empty")):
        engine.move_rider("2", to_team="Team B", reason=reason)


@pytest.mark.parametrize("reason", ["", " ", "\t\n"])
def test_extract_rider_to_solo_with_a_blank_reason_raises_value_error(reason: str) -> None:
    """Extraction needs a reason too."""
    engine = _started(_two_team_pooled_roster())
    engine.stop()

    with pytest.raises(ValueError, match=re.escape("reason must not be empty")):
        engine.extract_rider_to_solo("2", reason=reason)


@pytest.mark.parametrize(
    ("plate", "message"),
    [("99", "unknown plate: 99"), ("", "unknown plate: ")],
)
def test_move_rider_with_an_unknown_rider_plate_raises_unknown_plate_error(
    plate: str, message: str
) -> None:
    """A mistyped rider number fails loudly, an empty one too."""
    engine = _started(_two_team_pooled_roster())
    engine.stop()

    with pytest.raises(UnknownPlateError, match=re.escape(message)):
        engine.move_rider(plate, to_team="Team B", reason="rider swapped teams")


@pytest.mark.parametrize(
    ("team", "message"),
    [("Team Z", "unknown team: Team Z"), ("", "unknown team: ")],
)
def test_move_rider_with_an_unknown_destination_team_raises_unknown_plate_error(
    team: str, message: str
) -> None:
    """A mistyped team name fails loudly, an empty one too."""
    engine = _started(_two_team_pooled_roster())
    engine.stop()

    with pytest.raises(UnknownPlateError, match=re.escape(message)):
        engine.move_rider("2", to_team=team, reason="rider swapped teams")


def test_extract_rider_to_solo_from_a_solo_entry_raises_unknown_plate_error() -> None:
    """A solo rider has nothing to be extracted from."""
    engine = _started(_solo_and_team_roster())
    engine.stop()

    with pytest.raises(UnknownPlateError, match=re.escape("not a pooled team member")):
        engine.extract_rider_to_solo("5", reason="rider rides alone")


def test_move_rider_refused_while_running_leaves_the_ride_untouched() -> None:
    """A refused move touches no lap, no card and no audit row."""
    roster = _two_team_pooled_roster()
    team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("2", at=_dt(10, 30))
    before = (engine.crossings, engine.credited_cards(team_a.key), engine.events)

    with pytest.raises(IllegalStateError, match=re.escape("Stop the ride first")):
        engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert (engine.crossings, engine.credited_cards(team_a.key), engine.events) == before
    assert engine.credited_cards(team_b.key) == ()


def test_move_rider_on_a_reopened_ride_moves_the_rider() -> None:
    """REOPENED is the corrections cell the gate leaves open."""
    roster = _two_team_pooled_roster()
    _team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("2", at=_dt(10, 30))
    engine.finish()
    engine.reopen()

    engine.move_rider("2", to_team="Team B", reason="rider swapped teams")

    assert [(c.entry_id, c.seq) for c in engine.crossings] == [(team_b.key, 1)]


def test_pooled_move_actions_are_replayable() -> None:
    """Store.load_engine must replay both move rows, never skip them."""
    assert {"move_rider", "extract_rider_to_solo"} <= ride_module.REPLAY_ACTIONS


# ==================================================== replay gate


def test_move_rider_replay_reproduces_the_credited_hands() -> None:
    """A team-to-team move live equals a replay of its event log.

    The move's own row replays verbatim -- its payload carries the
    operator's reason, which no rebuilt roster re-derives -- while the
    earlier ``record_crossing`` rows carry a roster-derived ``reason``
    sentence that a replay legitimately recomputes (the class
    docstring's own "compares event count/actions, not payload fields"
    replay contract), so the whole ``events`` tuple is not compared.
    """
    live, _clock = _make_engine(roster=_two_team_pooled_roster(), config=_config())
    live.start(at=_dt(10, 0))
    live.record_crossing("2", at=_dt(10, 30))
    live.record_crossing("1", at=_dt(11, 0))
    live.record_crossing("2", at=_dt(11, 30))
    live.stop()
    live.move_rider("2", to_team="Team B", reason="rider swapped teams")

    replayed = _replay(live, _final_two_team_roster())

    assert _state(replayed) == _state(live)
    assert replayed.events[-1] == live.events[-1]


def test_move_rider_replay_reproduces_a_held_card_under_the_new_key() -> None:
    """A held card's destination key survives the rebuild."""
    live, _clock = _make_engine(roster=_two_team_pooled_roster(), config=_config())
    live.start(at=_dt(10, 0))
    live.record_crossing("2", at=_dt(10, 0, 30))  # held
    live.record_crossing("2", at=_dt(10, 30))  # credited
    live.stop()
    live.move_rider("2", to_team="Team B", reason="rider swapped teams")

    replayed = _replay(live, _final_two_team_roster())

    assert _state(replayed) == _state(live)


def test_move_rider_replay_reproduces_a_restored_voided_lap() -> None:
    """The void reset re-derives the same disposition on replay."""
    roster = _two_team_pooled_roster()
    team_a, _team_b = roster.entries
    live, _clock = _make_engine(roster=roster, config=_config())
    live.start(at=_dt(10, 0))
    live.record_crossing("2", at=_dt(10, 30))
    live.record_crossing("2", at=_dt(10, 35))  # short -> held, then voided
    live.void_crossing(team_a.key, 2, reason="double entry")
    live.stop()
    live.move_rider("2", to_team="Team B", reason="rider swapped teams")

    replayed = _replay(live, _final_two_team_roster())

    assert _state(replayed) == _state(live)


def test_extract_rider_to_solo_replay_stays_equivalent() -> None:
    """A team-to-solo extraction live equals a replay of its log.

    The extraction's destination key is the new solo entry's, which the
    replayed roster already carries (Store persists a *live* entry), so
    the recorded laps re-key onto it exactly as they did live.
    """
    live, _clock = _make_engine(roster=_two_team_pooled_roster(), config=_config())
    live.start(at=_dt(10, 0))
    live.record_crossing("2", at=_dt(10, 0, 30))  # held
    live.record_crossing("2", at=_dt(10, 30))  # credited
    live.record_crossing("1", at=_dt(11, 0))
    live.stop()
    live.extract_rider_to_solo("2", reason="rider rides alone")

    replayed = _replay(live, _final_extracted_roster())

    assert _state(replayed) == _state(live)
    assert replayed.events[-1] == live.events[-1]


def test_apply_move_rider_event_reattributes_from_the_payload_keys() -> None:
    """A hand-built row replays its keys, not the roster's plates."""
    roster = _two_team_pooled_roster()
    team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("2", at=_dt(10, 30))
    engine.stop()
    event = Event(
        action="move_rider",
        payload={
            "rider_plate": "2",
            "from_key": team_a.key,
            "to_team": "Not A Team",
            "to_key": team_b.key,
            "reason": "",
        },
    )

    engine.apply(event)

    assert [c.entry_id for c in engine.crossings] == [team_b.key]
    assert engine.events[-1] == event


def test_apply_extract_rider_to_solo_event_reattributes_from_the_payload() -> None:
    """The extract row replays onto a destination the roster holds."""
    roster = _two_team_pooled_roster()
    team_a, team_b = roster.entries
    engine = _started(roster)
    engine.record_crossing("2", at=_dt(10, 30))
    engine.stop()
    event = Event(
        action="extract_rider_to_solo",
        payload={
            "rider_plate": "2",
            "from_key": team_a.key,
            "to_key": team_b.key,
            "reason": "rider rides alone",
        },
    )

    engine.apply(event)

    assert [c.entry_id for c in engine.crossings] == [team_b.key]
    assert engine.events[-1] == event
