# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for RideEngine's E7.1.1 audited correction commands.

Every correction -- edit/void a crossing, add-at-time, reassign a
plate, mark DNF, void a dealt card -- requires a non-empty reason,
writes exactly one :class:`~rivercrossing.ride.Event` via ``_append``,
and is refused in the wrong ride state or for an unknown plate. The
three recompute cascades the task briefs name are pinned here: void
renumbers the entry's later laps, reassign reattributes the crossing
*and its card* (ruling C), and DNF keeps laps/cards while flipping
``snapshot().dnf``. The ``apply`` replay seam is covered for each new
action, matching tests/unit/test_ride.py's own apply suite.

The engine under test is the same :class:`~rivercrossing.ride.
RideEngine` test_ride.py drives; the small helpers below are copied
here because this repo keeps every test module self-contained (each
of test_ride.py / tests/simulations/test_ride_replay.py /
tests/property/test_store_replay.py defines its own fake clock).
"""

import re
from datetime import date, datetime, timedelta

import pytest

from rivercrossing.cards import Card, Shoe
from rivercrossing.hands import best_hand
from rivercrossing.ride import (
    Event,
    IllegalStateError,
    RideConfig,
    RideEngine,
    UnknownPlateError,
)
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster

# A minimal, always-valid kwarg set every test overrides from -- the
# same fixture values tests/unit/test_ride.py uses, so a correction's
# lap-time expectations match that suite's timing math.
_VALID_KWARGS: dict[str, object] = {
    "name": "GORBA EPIC 2026",
    "event_date": date(2026, 9, 20),
    "venue": "Sea to Sky Gondola",
    "lap_km": 8.0,
    "organizer": "GORBA",
    "scorer": "K. Singh",
    "planned_start": datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive by design, RideConfig's own convention
    "planned_duration_s": 21600,
    "min_lap_s": 1080,  # 18 min: every 10:3x crossing below is a normal lap
    "entry_mode": EntryMode.MIXED,
    "plate_model": PlateModel.RIDER_POOLED,
}


def _config(**overrides: object) -> RideConfig:
    """Build a valid RideConfig, overriding only what a test names."""
    return RideConfig(**{**_VALID_KWARGS, **overrides})  # type: ignore[arg-type]


class _FakeClock:
    """A scriptable wall clock for RideEngine's injected clock."""

    def __init__(self, start: datetime) -> None:
        """Freeze the fake clock at *start*."""
        self._now = start

    def __call__(self) -> datetime:
        """Return the current fake time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward by *seconds*."""
        self._now = self._now + timedelta(seconds=seconds)


def _dt(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Build a naive datetime on the fixed day, Sept 20, 2026."""
    return datetime(2026, 9, 20, hour, minute, second)  # noqa: DTZ001 -- naive by design, as RideConfig's planned_start


def _roster_with_entries(*plates: str) -> Roster:
    """Build a MIXED rider_pooled roster of one solo entry per plate."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    for plate in plates:
        roster.create_solo_entry(name=f"Rider {plate}", plate=plate)
    return roster


def _make_engine(
    *,
    roster: Roster | None = None,
    clock: _FakeClock | None = None,
    config: RideConfig | None = None,
) -> tuple[RideEngine, _FakeClock]:
    """Build a DRAFT engine over a valid config, shoe and roster."""
    config = config if config is not None else _config()
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    roster = roster if roster is not None else _roster_with_entries("12", "34")
    clock = clock if clock is not None else _FakeClock(config.planned_start)
    engine = RideEngine(config=config, shoe=shoe, clock=clock, roster=roster)
    return engine, clock


def _engine_in(state: str) -> tuple[RideEngine, _FakeClock]:
    """Build an engine already in one of the lifecycle states."""
    engine, clock = _make_engine()
    if state == "running":
        engine.start()
    elif state == "finished":
        engine.start()
        engine.finish()
    elif state == "reopened":
        engine.start()
        engine.finish()
        engine.reopen()
    return engine, clock


# ===================================================== edit_crossing


def test_edit_crossing_audits_action_entry_previous_and_new_time() -> None:
    """edit_crossing writes exactly one event naming both times."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    before = len(engine.events)

    event = engine.edit_crossing("12", 1, _dt(10, 31), reason="mis-keyed time")

    assert event == Event(
        action="edit_crossing",
        payload={
            "entry_id": "12",
            "seq": 1,
            "previous_crossed_at": "2026-09-20T10:30:00",
            "crossed_at": "2026-09-20T10:31:00",
            "reason": "mis-keyed time",
        },
    )
    assert len(engine.events) == before + 1  # exactly one correction row


def test_edit_crossing_changes_only_the_timestamp_and_recomputes_laps() -> None:
    """The crossing's card stays put and later lap times recompute."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    second = engine.record_crossing("12", at=_dt(10, 40))
    dealt_before = engine._shoe.dealt

    engine.edit_crossing("12", 2, _dt(10, 35), reason="mis-keyed time")

    assert engine.lap_times("12") == (1800.0, 300.0)  # 10:30-10:00, 10:35-10:30
    assert engine._shoe.dealt == dealt_before  # no re-deal on edit
    assert engine.crossings[-1].crossed_at == _dt(10, 35)
    assert engine.card_for(engine.crossings[-1]) == second.card


def test_edit_crossing_held_crossing_keeps_the_card_held() -> None:
    """Editing a held crossing's time never releases or re-deals it."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))  # 30 s < min_lap_s -> held
    held_before = engine.held_crossings()[0]

    engine.edit_crossing("12", 1, _dt(10, 0, 45), reason="mis-keyed time")

    held = engine.held_crossings()
    assert len(held) == 1
    assert held[0].crossing.seq == 1
    assert held[0].crossing.crossed_at == _dt(10, 0, 45)
    assert held[0].card == held_before.card


def test_edit_crossing_empty_reason_is_refused() -> None:
    """A correction with no reason is refused outright (R-33)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    with pytest.raises(ValueError, match=re.escape("reason must not be empty")):
        engine.edit_crossing("12", 1, _dt(10, 31), reason="")


@pytest.mark.parametrize(
    ("start_state", "match"),
    [
        ("draft", "cannot edit crossing from draft"),
        ("finished", "cannot edit crossing from finished"),
    ],
    ids=["draft_refused", "finished_refused"],
)
def test_edit_crossing_from_non_live_state_raises_illegal_state_error(
    start_state: str, match: str
) -> None:
    """edit_crossing is a corrections path: DRAFT/FINISHED raise."""
    engine, _ = _engine_in(start_state)

    with pytest.raises(IllegalStateError, match=re.escape(match)):
        engine.edit_crossing("12", 1, _dt(10, 31), reason="mis-keyed time")


@pytest.mark.parametrize(
    ("seq", "match"),
    [(0, "no crossing with entry_id 12 seq 0"), (9, "no crossing with entry_id 12 seq 9")],
    ids=["min-1", "max+1"],
)
def test_edit_crossing_unknown_crossing_raises_illegal_state_error(seq: int, match: str) -> None:
    """Editing a crossing the engine never recorded fails loudly."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    with pytest.raises(IllegalStateError, match=re.escape(match)):
        engine.edit_crossing("12", seq, _dt(10, 31), reason="mis-keyed time")


# ==================================================== void_crossing


def test_void_crossing_audits_entry_seq_and_reason() -> None:
    """void_crossing writes exactly one event naming the crossing."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    before = len(engine.events)

    event = engine.void_crossing("12", 1, reason="double entry")

    assert event == Event(
        action="void_crossing",
        payload={"entry_id": "12", "seq": 1, "reason": "double entry"},
    )
    assert len(engine.events) == before + 1


def test_void_crossing_voids_card_and_renumbers_later_laps() -> None:
    """A voided crossing leaves the lap sequence closed up (E7.1.2)."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    first = engine.record_crossing("12", at=_dt(10, 30))
    second = engine.record_crossing("12", at=_dt(10, 32))
    third = engine.record_crossing("12", at=_dt(10, 34))

    engine.void_crossing("12", 2, reason="double entry")

    assert [c.seq for c in engine.crossings] == [1, 2]
    assert [c.crossed_at for c in engine.crossings] == [_dt(10, 30), _dt(10, 34)]
    assert engine.lap_times("12") == (1800.0, 240.0)  # 10:30-10:00, 10:34-10:30
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].laps == 2
    assert results["12"].cards == (first.card, third.card)  # second's card voided
    assert second.card not in results["12"].cards


def test_void_crossing_does_not_restitute_the_card_to_the_shoe() -> None:
    """void_crossing never restitutes: that stays undo_last's job."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    dealt_before = engine._shoe.dealt

    engine.void_crossing("12", 1, reason="double entry")

    assert engine._shoe.dealt == dealt_before  # card retired, not returned


def test_void_crossing_empty_reason_is_refused() -> None:
    """A void with no reason is refused outright (R-33)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    with pytest.raises(ValueError, match=re.escape("reason must not be empty")):
        engine.void_crossing("12", 1, reason="")


@pytest.mark.parametrize(
    ("start_state", "match"),
    [
        ("draft", "cannot void crossing from draft"),
        ("finished", "cannot void crossing from finished"),
    ],
    ids=["draft_refused", "finished_refused"],
)
def test_void_crossing_from_non_live_state_raises_illegal_state_error(
    start_state: str, match: str
) -> None:
    """void_crossing is a corrections path: DRAFT/FINISHED raise."""
    engine, _ = _engine_in(start_state)

    with pytest.raises(IllegalStateError, match=re.escape(match)):
        engine.void_crossing("12", 1, reason="double entry")


@pytest.mark.parametrize(
    ("seq", "match"),
    [(0, "no crossing with entry_id 12 seq 0"), (9, "no crossing with entry_id 12 seq 9")],
    ids=["min-1", "max+1"],
)
def test_void_crossing_unknown_crossing_raises_illegal_state_error(seq: int, match: str) -> None:
    """Voiding a crossing the engine never recorded fails loudly."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    with pytest.raises(IllegalStateError, match=re.escape(match)):
        engine.void_crossing("12", seq, reason="double entry")


# =================================================== add_crossing_at


def test_add_crossing_at_audits_plate_entry_crossed_at_and_reason() -> None:
    """add_crossing_at writes one event with the explicit time."""
    engine, _ = _make_engine()
    engine.start()
    before = len(engine.events)

    event = engine.add_crossing_at("12", _dt(10, 15), reason="missed crossing")

    assert event == Event(
        action="add_crossing_at",
        payload={
            "plate": "12",
            "entry_id": "12",
            "crossed_at": "2026-09-20T10:15:00",
            "reason": "missed crossing",
        },
    )
    assert len(engine.events) == before + 1


def test_add_crossing_at_deals_next_card_and_credits_the_hand() -> None:
    """A missed crossing deals the shoe's next card (R-40)."""
    roster = _roster_with_entries("12", "34")
    engine, _ = _make_engine(roster=roster)
    engine.start()
    reference = Shoe(decks=8, jokers_per_deck=2, seed=20260920)

    engine.add_crossing_at("12", _dt(10, 15), reason="missed crossing")

    assert engine._shoe.dealt == 1
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].laps == 1
    assert results["12"].cards == (reference.deal()[0],)
    assert roster.entries[0].has_data is True


def test_add_crossing_at_credits_directly_never_holds_a_short_lap() -> None:
    """A deliberate correction never routes through the held queue."""
    engine, _ = _make_engine()
    engine.start()

    engine.add_crossing_at("12", _dt(10, 0, 30), reason="missed crossing")

    assert engine.held_crossings() == ()
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert len(results["12"].cards) == 1


def test_add_crossing_at_empty_reason_is_refused() -> None:
    """An add with no reason is refused outright (R-33)."""
    engine, _ = _make_engine()
    engine.start()

    with pytest.raises(ValueError, match=re.escape("reason must not be empty")):
        engine.add_crossing_at("12", _dt(10, 15), reason="")


@pytest.mark.parametrize(
    ("start_state", "match"),
    [
        ("draft", "cannot add crossing from draft"),
        ("finished", "cannot add crossing from finished"),
    ],
    ids=["draft_refused", "finished_refused"],
)
def test_add_crossing_at_from_non_live_state_raises_illegal_state_error(
    start_state: str, match: str
) -> None:
    """add_crossing_at is a corrections path: DRAFT/FINISHED raise."""
    engine, _ = _engine_in(start_state)

    with pytest.raises(IllegalStateError, match=re.escape(match)):
        engine.add_crossing_at("12", _dt(10, 15), reason="missed crossing")


def test_add_crossing_at_unknown_plate_raises_unknown_plate_error() -> None:
    """An unresolvable plate raises UnknownPlateError, never deals."""
    engine, _ = _make_engine()
    engine.start()

    with pytest.raises(UnknownPlateError, match=re.escape("unknown plate")):
        engine.add_crossing_at("999", _dt(10, 15), reason="missed crossing")


def test_add_crossing_at_from_reopened_after_finish_deals_and_records_crossing() -> None:
    """REOPENED re-opens the shoe: add_crossing_at deals and records."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.finish()
    engine.reopen()

    event = engine.add_crossing_at("12", _dt(10, 35), reason="missed crossing")

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert event.action == "add_crossing_at"
    assert engine._shoe.is_closed is False
    assert engine._shoe.dealt == 2  # one live crossing + one reopened add
    assert results["12"].laps == 2
    assert len(results["12"].cards) == 2


# ================================================== reassign_crossing


def test_reassign_crossing_audits_seq_old_entry_new_entry_new_plate_and_reason() -> None:
    """Reassign writes exactly one event naming both entries."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 32))
    engine.record_crossing("34", at=_dt(10, 34))
    before = len(engine.events)

    event = engine.reassign_crossing(2, "34", reason="mis-keyed plate")

    assert event == Event(
        action="reassign",
        payload={
            "seq": 2,
            "old_entry_id": "12",
            "new_entry_id": "34",
            "new_plate": "34",
            "reason": "mis-keyed plate",
        },
    )
    assert len(engine.events) == before + 1


def test_reassign_crossing_moves_crossing_and_card_to_the_new_entry() -> None:
    """The crossing reattributes; its card travels with it (C)."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    first = engine.record_crossing("12", at=_dt(10, 30))
    second = engine.record_crossing("12", at=_dt(10, 32))
    engine.record_crossing("34", at=_dt(10, 34))
    third_34_card = engine.card_for(engine.crossings[-1])

    engine.reassign_crossing(2, "34", reason="mis-keyed plate")

    crossings_12 = [c for c in engine.crossings if c.entry_id == "12"]
    crossings_34 = [c for c in engine.crossings if c.entry_id == "34"]
    assert [(c.seq, c.crossed_at) for c in crossings_12] == [(1, _dt(10, 30))]
    assert [(c.seq, c.crossed_at) for c in crossings_34] == [
        (1, _dt(10, 34)),
        (2, _dt(10, 32)),
    ]
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == (first.card,)
    assert results["34"].cards == (third_34_card, second.card)


def test_reassign_crossing_held_card_travels_while_still_held() -> None:
    """A reassigned short-lap card stays in the hold queue."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 0, 30))  # short lap -> held
    engine.record_crossing("34", at=_dt(10, 34))
    held = engine.held_crossings()[0]

    engine.reassign_crossing(2, "34", reason="mis-keyed plate")

    moved = engine.held_crossings()
    assert len(moved) == 1
    assert moved[0].crossing.entry_id == "34"
    assert moved[0].card == held.card


def test_reassign_crossing_empty_reason_is_refused() -> None:
    """A reassign with no reason is refused outright (R-33)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    with pytest.raises(ValueError, match=re.escape("reason must not be empty")):
        engine.reassign_crossing(1, "34", reason="")


@pytest.mark.parametrize(
    ("start_state", "match"),
    [
        ("draft", "cannot reassign crossing from draft"),
        ("finished", "cannot reassign crossing from finished"),
    ],
    ids=["draft_refused", "finished_refused"],
)
def test_reassign_crossing_from_non_live_state_raises_illegal_state_error(
    start_state: str, match: str
) -> None:
    """Reassign is a corrections path: DRAFT/FINISHED raise."""
    engine, _ = _engine_in(start_state)

    with pytest.raises(IllegalStateError, match=re.escape(match)):
        engine.reassign_crossing(1, "34", reason="mis-keyed plate")


def test_reassign_crossing_unknown_plate_raises_unknown_plate_error() -> None:
    """An unresolvable destination plate raises UnknownPlateError."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    with pytest.raises(UnknownPlateError, match=re.escape("unknown plate")):
        engine.reassign_crossing(1, "999", reason="mis-keyed plate")


@pytest.mark.parametrize("seq", [0, 9], ids=["min-1", "max+1"])
def test_reassign_crossing_unknown_ordinal_raises_illegal_state_error(seq: int) -> None:
    """An ordinal outside the recorded range fails loudly."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    with pytest.raises(IllegalStateError, match=re.escape(f"no crossing at ordinal {seq}")):
        engine.reassign_crossing(seq, "34", reason="mis-keyed plate")


# ========================================================= mark_dnf


def test_mark_dnf_audits_entry_and_reason() -> None:
    """mark_dnf writes exactly one event naming the entry."""
    engine, _ = _make_engine()
    engine.start()
    before = len(engine.events)

    event = engine.mark_dnf("12", reason="mechanical failure")

    assert event == Event(action="dnf", payload={"entry_id": "12", "reason": "mechanical failure"})
    assert len(engine.events) == before + 1


def test_mark_dnf_keeps_laps_and_cards_and_flips_snapshot_dnf() -> None:
    """DNF keeps every lap/card; the snapshot flags dnf for rank."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 32))

    engine.mark_dnf("12", reason="mechanical failure")

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].dnf is True
    assert results["12"].laps == 2
    assert len(results["12"].cards) == 2
    assert results["34"].dnf is False


def test_mark_dnf_pooled_rider_plate_marks_the_team_entry() -> None:
    """A rider's plate marks the owning team entry DNF (R-16)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(name="Sarah", plate="45"), Rider(name="Priya", plate="9")],
    )
    engine, _ = _make_engine(roster=roster)
    engine.start()

    event = engine.mark_dnf("45", reason="mechanical failure")

    assert event.payload["entry_id"] == "9"
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["9"].dnf is True


def test_mark_dnf_empty_reason_is_refused() -> None:
    """A DNF with no reason is refused outright (R-33)."""
    engine, _ = _make_engine()
    engine.start()

    with pytest.raises(ValueError, match=re.escape("reason must not be empty")):
        engine.mark_dnf("12", reason="")


@pytest.mark.parametrize(
    ("start_state", "match"),
    [
        ("draft", "cannot mark DNF from draft"),
        ("finished", "cannot mark DNF from finished"),
    ],
    ids=["draft_refused", "finished_refused"],
)
def test_mark_dnf_from_non_live_state_raises_illegal_state_error(
    start_state: str, match: str
) -> None:
    """mark_dnf is a live-ride correction: DRAFT/FINISHED raise."""
    engine, _ = _engine_in(start_state)

    with pytest.raises(IllegalStateError, match=re.escape(match)):
        engine.mark_dnf("12", reason="mechanical failure")


def test_mark_dnf_unknown_plate_raises_unknown_plate_error() -> None:
    """An unresolvable plate raises UnknownPlateError, never marks."""
    engine, _ = _make_engine()
    engine.start()

    with pytest.raises(UnknownPlateError, match=re.escape("unknown plate")):
        engine.mark_dnf("999", reason="mechanical failure")


# ======================================================== void_card


def test_void_card_audits_entry_card_and_reason() -> None:
    """void_card writes exactly one event naming the voided card."""
    engine, _ = _make_engine()
    engine.start()
    result = engine.record_crossing("12", at=_dt(10, 30))
    before = len(engine.events)

    event = engine.void_card("12", result.card, reason="wrong card dealt")

    assert event == Event(
        action="void_card",
        payload={"entry_id": "12", "card": result.card.code(), "reason": "wrong card dealt"},
    )
    assert len(engine.events) == before + 1


def test_void_card_removes_card_from_hand_and_keeps_the_lap() -> None:
    """The lap stays credited; only the card drops out of the hand."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    first = engine.record_crossing("12", at=_dt(10, 30))
    second = engine.record_crossing("12", at=_dt(10, 32))

    engine.void_card("12", second.card, reason="wrong card dealt")

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].laps == 2
    assert results["12"].cards == (first.card,)
    assert results["12"].hand == best_hand((first.card,))


def test_void_card_refuses_a_held_card() -> None:
    """A held card is refused; the review surface owns it."""
    engine, _ = _make_engine()
    engine.start()
    result = engine.record_crossing("12", at=_dt(10, 0, 30))  # short lap -> held
    assert result.flagged is True

    with pytest.raises(IllegalStateError, match=re.escape("card is held")):
        engine.void_card("12", result.card, reason="wrong card dealt")


def test_void_card_ignores_an_unrelated_held_card_and_voids_the_credited_one() -> None:
    """A held card does not block voiding a different credited one."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))  # short lap -> held
    engine.record_crossing("12", at=_dt(10, 40))  # normal lap -> credited
    credited = engine.card_for(engine.crossings[-1])
    held_before = engine.held_crossings()[0]

    engine.void_card("12", credited, reason="wrong card dealt")

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert credited not in results["12"].cards
    held = engine.held_crossings()
    assert len(held) == 1
    assert held[0].card == held_before.card  # the held card stays untouched


def test_void_card_empty_reason_is_refused() -> None:
    """A void with no reason is refused outright (R-33)."""
    engine, _ = _make_engine()
    engine.start()
    result = engine.record_crossing("12", at=_dt(10, 30))

    with pytest.raises(ValueError, match=re.escape("reason must not be empty")):
        engine.void_card("12", result.card, reason="")


@pytest.mark.parametrize(
    ("start_state", "match"),
    [
        ("draft", "cannot void card from draft"),
        ("finished", "cannot void card from finished"),
    ],
    ids=["draft_refused", "finished_refused"],
)
def test_void_card_from_non_live_state_raises_illegal_state_error(
    start_state: str, match: str
) -> None:
    """void_card is a corrections path: DRAFT/FINISHED raise."""
    engine, _ = _engine_in(start_state)

    with pytest.raises(IllegalStateError, match=re.escape(match)):
        engine.void_card("12", Card.parse("8C"), reason="wrong card dealt")


def test_void_card_not_credited_to_the_entry_raises_illegal_state_error() -> None:
    """A card dealt to another entry is not voidable from this one."""
    engine, _ = _make_engine()
    engine.start()
    result = engine.record_crossing("12", at=_dt(10, 30))

    with pytest.raises(IllegalStateError, match=re.escape("no dealt card")):
        engine.void_card("34", result.card, reason="wrong card dealt")


def test_void_card_unknown_plate_raises_unknown_plate_error() -> None:
    """An unresolvable plate raises UnknownPlateError, never voids."""
    engine, _ = _make_engine()
    engine.start()
    result = engine.record_crossing("12", at=_dt(10, 30))

    with pytest.raises(UnknownPlateError, match=re.escape("unknown plate")):
        engine.void_card("999", result.card, reason="wrong card dealt")


# ================================================ undo reason label


def test_undo_last_payload_includes_fixed_reason_label() -> None:
    """The undo event carries the fixed label (E7.1.1)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    event = engine.undo_last()

    assert event.action == "undo"
    assert event.payload["reason"] == "Undo last crossing"


# ==================================== E7.1.2 replay seam: apply


def test_apply_edit_crossing_event_recomputes_the_timestamp() -> None:
    """apply("edit_crossing") re-times the named crossing."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 30))
    event = Event(
        action="edit_crossing",
        payload={
            "entry_id": "12",
            "seq": 1,
            "previous_crossed_at": "2026-09-20T10:30:00",
            "crossed_at": "2026-09-20T10:35:00",
            "reason": "mis-keyed time",
        },
    )

    engine.apply(event)

    assert engine.lap_times("12") == (2100.0,)
    assert engine.events[-1] == event


def test_apply_void_crossing_event_voids_and_renumbers() -> None:
    """apply("void_crossing") voids by entry/seq and closes up laps."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 32))
    engine.record_crossing("12", at=_dt(10, 34))
    event = Event(
        action="void_crossing",
        payload={"entry_id": "12", "seq": 2, "reason": "double entry"},
    )

    engine.apply(event)

    assert [c.seq for c in engine.crossings] == [1, 2]
    assert engine.lap_times("12") == (1800.0, 240.0)
    assert engine.events[-1] == event


def test_apply_add_crossing_at_event_deals_the_next_card() -> None:
    """apply("add_crossing_at") records and deals."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    event = Event(
        action="add_crossing_at",
        payload={
            "plate": "12",
            "entry_id": "12",
            "crossed_at": "2026-09-20T10:15:00",
            "reason": "missed crossing",
        },
    )

    engine.apply(event)

    assert engine.lap_times("12") == (900.0,)
    assert engine.events[-1] == event
    assert engine._shoe.dealt == 1


def test_apply_reassign_event_moves_crossing_and_card() -> None:
    """apply("reassign") reattributes the crossing."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 32))
    engine.record_crossing("34", at=_dt(10, 34))
    moved = engine.card_for(engine.crossings[1])
    event = Event(
        action="reassign",
        payload={
            "seq": 2,
            "old_entry_id": "12",
            "new_entry_id": "34",
            "new_plate": "34",
            "reason": "mis-keyed plate",
        },
    )

    engine.apply(event)

    crossings_34 = [c for c in engine.crossings if c.entry_id == "34"]
    assert [(c.seq, c.crossed_at) for c in crossings_34] == [
        (1, _dt(10, 34)),
        (2, _dt(10, 32)),
    ]
    assert engine.card_for(crossings_34[-1]) == moved
    assert engine.events[-1] == event


def test_apply_dnf_event_marks_the_entry() -> None:
    """apply("dnf") sets the entry's DNF state from the payload."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    event = Event(action="dnf", payload={"entry_id": "12", "reason": "mechanical failure"})

    engine.apply(event)

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].dnf is True
    assert engine.events[-1] == event


def test_apply_void_card_event_removes_the_card_from_the_hand() -> None:
    """apply("void_card") voids the named card by its code."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 30))
    card = engine.card_for(engine.crossings[0])
    event = Event(
        action="void_card",
        payload={"entry_id": "12", "card": card.code(), "reason": "wrong card dealt"},
    )

    engine.apply(event)

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == ()
    assert engine.events[-1] == event


# ===================================================== snapshot DNF

# The snapshot DNF behavior itself is pinned in tests/unit/test_ride.py
# (test_snapshot_includes_dnf_entries_with_dnf_flag); mark_dnf's own
# "keeps laps/cards and flips snapshot().dnf" cascade is covered above.
