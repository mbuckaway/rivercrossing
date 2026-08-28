# SPDX-License-Identifier: GPL-3.0-only
"""Seeded whole-ride engine replay: exact shoe and credit accounting.

E4.3.1's own "accounting exact in sim replay" (task-briefs.md) driven
through the real engine this time -- unlike tests/simulations/
test_simulated_rides.py, whose helpers stand in for the E2-era engine.
One seeded :class:`~rivercrossing.ride.RideEngine` over a real
:class:`~rivercrossing.roster.Roster` and :class:`~rivercrossing.cards.
Shoe`, with a fake clock, replays a mixed solo + rider-pooled field:
short-lap holds with one confirm and one void, a manual deal, and 53
total deals forcing one cycle exhaustion. The suite asserts, in one
pass, that the live deal sequence matches a reference shoe dealt from
the same seed, that ``Shoe.replay`` reproduces the identical next deal,
and that every entry's credited cards equal its ``EntryResult.cards``
-- held and voided cards never credited (spec section 4, R-16/R-34/
R-40).

Fixed literal seeds throughout: determinism is the point, exactly as
in test_simulated_rides.py.
"""

from datetime import date, datetime, timedelta

from rivercrossing.cards import Card, Shoe, ShoeEmpty
from rivercrossing.hands import best_hand
from rivercrossing.ride import RideConfig, RideEngine
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster

_REPLAY_SEED = 400_101
_DECK_COUNT = 1
_JOKERS_PER_DECK = 0
_SHOE_SIZE = _DECK_COUNT * 52
_START = datetime(2026, 9, 20, 10, 0)  # noqa: DTZ001 -- naive, like RideConfig's planned_start

_VALID_KWARGS: dict[str, object] = {
    "name": "GORBA EPIC 2026",
    "event_date": date(2026, 9, 20),
    "venue": "Sea to Sky Gondola",
    "lap_km": 8.0,
    "organizer": "GORBA",
    "scorer": "K. Singh",
    "planned_start": _START,
    "planned_duration_s": 21600,
    "min_lap_s": 60,
    "entry_mode": EntryMode.MIXED,
    "plate_model": PlateModel.RIDER_POOLED,
}


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


def _config() -> RideConfig:
    """Build the replay ride's fixed config over a 52-card shoe."""
    return RideConfig(**_VALID_KWARGS)  # type: ignore[arg-type]


def _make_replay_roster() -> Roster:
    """Build the replay ride's field: one solo, one pooled team."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(name="Alice", plate="12")
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(name="Sarah", plate="45"), Rider(name="Priya", plate="9")],
    )
    return roster


def _next_reference(shoe: Shoe) -> Card:
    """Deal one card, reshuffling first if the current cycle is empty.

    Mirrors the engine's own caller-driven deal loop (spec section 4):
    ``ShoeEmpty`` triggers ``reshuffle()``, then the deal succeeds.
    """
    try:
        card, _index = shoe.deal()
    except ShoeEmpty:
        shoe.reshuffle()
        card, _index = shoe.deal()
    return card


def test_seeded_ride_replay_accounts_every_deal_and_credit_exactly() -> None:
    """A seeded multi-entry ride's deals and credits replay exactly.

    One real engine over a real roster/shoe with a fake clock: solo
    plus rider-pooled entries, short-lap holds with one confirm and
    one void, a manual deal, and 53 total deals forcing one cycle
    exhaustion. Every dealt card must match a reference shoe dealt
    from the same seed; ``Shoe.replay`` must reproduce the live shoe's
    next deal; and each entry's credited cards must equal its
    ``EntryResult.cards``, held and voided cards never credited.
    """
    config = _config()
    shoe = Shoe(decks=_DECK_COUNT, jokers_per_deck=_JOKERS_PER_DECK, seed=_REPLAY_SEED)
    reference = Shoe(decks=_DECK_COUNT, jokers_per_deck=_JOKERS_PER_DECK, seed=_REPLAY_SEED)
    clock = _FakeClock(_START)
    roster = _make_replay_roster()
    engine = RideEngine(config=config, shoe=shoe, clock=clock, roster=roster)
    engine.start()

    dealt: list[Card] = []

    def crossing(plate: str, gap_s: float) -> None:
        """Record one crossing *gap_s* later, collecting its card."""
        clock.advance(gap_s)
        result = engine.record_crossing(plate)
        assert result.accepted is True
        dealt.append(result.card)

    # Deal 1-3: team laps for pooled riders -- lap 1 normal, then two
    # short laps back-to-back (30 s gaps, min_lap_s=60) held by R-34.
    crossing("45", 600)
    crossing("45", 30)
    engine.confirm_held(engine.held_crossings()[0].crossing)
    crossing("9", 30)
    engine.void_held(engine.held_crossings()[-1].crossing)
    # Deals 4-7: solo laps for "12".
    crossing("12", 600)
    crossing("12", 600)
    crossing("12", 600)
    crossing("12", 600)
    # Deal 8: one manual deal for "12" (spec section 4).
    manual = engine.deal_manual("12", reason="replacement card")
    dealt.append(Card.parse(str(manual.payload["card"])))
    # Deals 9-53: fill cycle 1 (52) and deal one into cycle 2.
    for _ in range(45):
        crossing("12", 600)

    results = {entry.plate: entry for entry in engine.snapshot()}

    # Cards dealt == accepted crossings (52) + manual deals (1).
    assert len(dealt) == 53
    assert all(card == _next_reference(reference) for card in dealt)

    # One cycle exhaustion: 52 in cycle 1, the 53rd into cycle 2.
    assert (shoe.cycle, shoe.dealt, shoe.remaining) == (2, 1, _SHOE_SIZE - 1)

    # Shoe.replay rebuilds the exact live state: identical next deal.
    replayed = Shoe.replay(
        decks=_DECK_COUNT,
        jokers_per_deck=_JOKERS_PER_DECK,
        seed=_REPLAY_SEED,
        deals=shoe.dealt,
        cycles=shoe.cycle,
    )
    assert replayed.deal()[0] == shoe.deal()[0]

    # Held and voided cards never credited; credited per entry matches
    # EntryResult.cards, and the snapshot hand evaluates from them.
    assert engine.held_crossings() == ()
    expected_team = (dealt[0], dealt[1])  # lap 1 normal + the confirmed short lap
    assert results["9"].cards == expected_team
    assert results["9"].hand == best_hand(expected_team)
    assert dealt[2] not in results["9"].cards  # the voided short-lap card
    expected_solo = (dealt[3], dealt[4], dealt[5], dealt[6], dealt[7], *dealt[8:])
    assert results["12"].cards == expected_solo
    assert results["12"].hand == best_hand(expected_solo)
