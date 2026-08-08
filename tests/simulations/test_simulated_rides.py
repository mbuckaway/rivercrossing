# SPDX-License-Identifier: GPL-3.0-only
"""Whole-ride simulation suite (E2.3.1) for the Shoe/best_hand pairing.

Exercises cards.py's Shoe and hands.py's best_hand together across
three seeded 180-entry, 6h fields -- solo, mixed-pooled, and
mixed-relay (spec section 1/2/4/6, R-16; task-briefs.md's own E2.3.1
brief). No new production module here: the ride state machine that
will one day drive real crossings is EPIC 4's own work, so every
helper below -- entry generation, crossing schedule, the
caller-driven deal loop, rider pooling -- is a plain seeded test
helper standing in for it.

This suite's own scoping spike (run before any test here existed)
dealt a real 60-120 card rider-pooled hand -- R-16's uncapped default
plate model over a full 6h field -- against the then-current
best_hand() and measured 4-22+ seconds *per hand*, worse as the pool
grew, plus an independent correctness bug an oracle battery caught
(the same saturating pruning silently dropped a straight- or
flush-completing card). Both are fixed at the root in hands.py
(commits 8eaa5c4/1d1ece0): a 120-card pooled hand now scores in
~0.25ms. Every scenario below therefore uses realistic, uncapped
team sizes and the full 6h window -- no artificial team-size or lap
ceiling -- exactly what would have caught the original defect first.

Fixed literal seeds throughout: determinism is the point (no
Hypothesis here, unlike tests/property/).
"""

import itertools
import math
import random
import time
from dataclasses import dataclass
from functools import cmp_to_key
from typing import TYPE_CHECKING

import pytest

from rivercrossing.cards import Card, Shoe, ShoeEmpty
from rivercrossing.hands import EvaluatedHand, best_hand, compare

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

# ---------------------------------------------------------- constants

# Spec section 4's own default composition for a 180-entry field.
_SHOE_DECKS = 8
_SHOE_JOKERS_PER_DECK = 2
_SHOE_SIZE = _SHOE_DECKS * (52 + _SHOE_JOKERS_PER_DECK)  # 432

_FIELD_SIZE = 180
_MIXED_SOLO_COUNT = 160
_MIXED_TEAM_COUNT = 20  # 160 + 20 = 180, the same field size as solo

# Spec section 6: 6h ride window, 18:00 min_lap_s floor for an 8km loop.
_SIX_HOURS_S = 6 * 60 * 60
_MIN_LAP_S = 18 * 60
# Task-briefs.md's own "20-40 min" realistic lap spread, both bounds
# safely above _MIN_LAP_S so no short-lap handling is needed here.
_LAP_LOW_S = 20 * 60
_LAP_HIGH_S = 40 * 60
# Provable, seed-independent bracket for _lap_crossing_times below:
# 6h/40min = 9 is the fewest laps an all-slow stream can log, 6h/20min
# = 18 the most an all-fast stream can log. The brief's own "~9-17
# laps" is the typical case inside this bracket.
_MIN_LAPS_PER_STREAM = _SIX_HOURS_S // _LAP_HIGH_S
_MAX_LAPS_PER_STREAM = _SIX_HOURS_S // _LAP_LOW_S

# The brief's own constructed case: a steady rider's lone lap (a
# 1-card partial hand) against an out-lapping teammate's several more.
_OUT_LAPPING_LAP_COUNTS = (1, 6)

_SOLO_SEED = 300_001
_MIXED_POOLED_SEED = 300_002
_MIXED_RELAY_SEED = 300_003

# task-briefs.md's own done-when: "sims in CI stage 2 under 60s" --
# pinned explicitly, as test_hands.py's own field-budget test pins
# R-42, not left as an unenforced aspiration.
_MODULE_BUDGET_SECONDS = 60.0
_SCENARIO_BUDGET_SECONDS = 15.0  # local target, well inside the module contract

_DealLog = list[tuple[int, int]]  # (cycle, deal_index) per successful deal, in order


# ------------------------------------------------------------ entities


@dataclass(frozen=True)
class _Entry:
    """One field entry: a plate and its full dealt-card multiset.

    ``rider_card_counts`` is only ever non-empty for a rider-pooled
    team (R-16): solo and relay entries already have exactly one
    crossing stream (module docstring), so there is nothing to break
    down.
    """

    plate: str
    cards: tuple[Card, ...]
    rider_card_counts: tuple[int, ...] = ()


@dataclass
class _Dealer:
    """Bundles one scenario's shared Shoe and its own deal log.

    Every entry-building helper below takes one of these instead of a
    separate shoe/log pair, keeping each helper to at most 3
    parameters (CODINGSTANDARDS-SIMPLECODE.md's own guidance).
    """

    shoe: Shoe
    log: _DealLog


# -------------------------------------------------- crossing schedule


def _lap_crossing_times(rng: random.Random) -> list[int]:
    """List one rider's cumulative crossing times across a 6h ride.

    Each lap is drawn uniformly from the brief's own realistic spread
    (20-40 min); the stream stops as soon as the next lap would run
    past the ride's own window, matching how a real 6h ride ends --
    the module constants above prove that rule keeps every stream's
    lap count inside [9, 18], whatever the seed.
    """
    crossings: list[int] = []
    elapsed = 0
    while True:
        elapsed += rng.randint(_LAP_LOW_S, _LAP_HIGH_S)
        if elapsed > _SIX_HOURS_S:
            return crossings
        crossings.append(elapsed)


# ------------------------------------------------------------ deal loop


def _next_deal(shoe: Shoe) -> tuple[Card, int]:
    """Deal one card, reshuffling first if the current cycle is empty.

    Mirrors cards.py's own caller-driven contract (``ShoeEmpty``'s
    docstring): catch it, ``reshuffle()``, deal again -- exactly how a
    live crossing handler must drive the shoe (spec section 4).
    """
    try:
        return shoe.deal()
    except ShoeEmpty:
        shoe.reshuffle()
        return shoe.deal()


def _deal_one(dealer: _Dealer) -> Card:
    """Deal one card, appending its (cycle, deal_index) to the log."""
    card, deal_index = _next_deal(dealer.shoe)
    dealer.log.append((dealer.shoe.cycle, deal_index))
    return card


# -------------------------------------------------- entry generation


def _single_stream_entry(plate: str, rng: random.Random, dealer: _Dealer) -> _Entry:
    """Build one single-stream entry: a solo rider or a relay plate.

    Solo and team-relay share the same shoe-dealing shape -- one
    crossing sequence, one card per completed lap (spec section 4) --
    they differ only in what the plate represents (R-16), not in how
    cards reach it.
    """
    crossings = _lap_crossing_times(rng)
    cards = tuple(_deal_one(dealer) for _ in crossings)
    return _Entry(plate=plate, cards=cards)


def _pool_rider_cards(lap_count: int, dealer: _Dealer) -> tuple[Card, ...]:
    """Deal lap_count cards for one pooled rider's own crossings."""
    return tuple(_deal_one(dealer) for _ in range(lap_count))


def _pooled_team_from_lap_counts(plate: str, lap_counts: Sequence[int], dealer: _Dealer) -> _Entry:
    """Build one rider-pooled team from an explicit lap count per rider.

    Shared by the field's randomly-sized teams and the brief's own
    constructed out-lapping case (:data:`_OUT_LAPPING_LAP_COUNTS`) --
    both are just different lap-count sequences through the same
    uncapped pooling mechanism (R-16): every rider's own cards land
    in one shared pool, however many laps each one rides.
    """
    rider_cards = [_pool_rider_cards(count, dealer) for count in lap_counts]
    pooled = tuple(card for cards in rider_cards for card in cards)
    return _Entry(plate=plate, cards=pooled, rider_card_counts=tuple(lap_counts))


def _random_team_lap_counts(rider_count: int, rng: random.Random) -> list[int]:
    """List each rider's own lap count for one randomly-sized team."""
    return [len(_lap_crossing_times(rng)) for _ in range(rider_count)]


def _solo_field(count: int, rng: random.Random, dealer: _Dealer) -> list[_Entry]:
    """Build count solo entries from one shared rng/dealer stream."""
    return [_single_stream_entry(f"S{index:03d}", rng, dealer) for index in range(count)]


def _relay_field(count: int, rng: random.Random, dealer: _Dealer) -> list[_Entry]:
    """Build count relay-team entries from one shared rng/dealer."""
    return [_single_stream_entry(f"R{index:03d}", rng, dealer) for index in range(count)]


def _mixed_pooled_teams(count: int, rng: random.Random, dealer: _Dealer) -> list[_Entry]:
    """Build count pooled teams; team 0 is the brief's out-lapper."""
    teams = [_pooled_team_from_lap_counts("T000", _OUT_LAPPING_LAP_COUNTS, dealer)]
    for index in range(1, count):
        rider_count = rng.randint(2, 10)  # R-16's own max_team_size range
        lap_counts = _random_team_lap_counts(rider_count, rng)
        teams.append(_pooled_team_from_lap_counts(f"T{index:03d}", lap_counts, dealer))
    return teams


# --------------------------------------------------------- assertions


def _assert_shoe_accounting_exact(dealer: _Dealer, entries: Sequence[_Entry]) -> None:
    """Assert dealt/remaining/cycle/deal_index bookkeeping is exact.

    cycles = ceil(deals / shoe size) holds because reshuffle only
    ever fires the instant a deal is attempted against an exhausted
    cycle (cards.py's own ShoeEmpty contract) -- never early, never
    automatic -- so a scenario dealing exactly a multiple of the
    shoe size never crosses into a further cycle at all.
    """
    shoe = dealer.shoe
    expected_total = sum(len(entry.cards) for entry in entries)
    assert len(dealer.log) == expected_total

    expected_cycle = math.ceil(expected_total / _SHOE_SIZE) if expected_total else 1
    expected_dealt = expected_total - _SHOE_SIZE * (expected_cycle - 1)
    assert (shoe.cycle, shoe.dealt, shoe.remaining) == (
        expected_cycle,
        expected_dealt,
        _SHOE_SIZE - expected_dealt,
    )

    by_cycle: dict[int, list[int]] = {}
    for cycle, index in dealer.log:
        by_cycle.setdefault(cycle, []).append(index)
    for indices in by_cycle.values():
        assert indices == list(range(len(indices)))


def _assert_replay_matches_next_deal(shoe: Shoe, seed: int) -> None:
    """Assert Shoe.replay() reproduces the live shoe's own next deal.

    R-40's own audit guarantee: a persisted (config, seed, dealt,
    cycle) tuple is enough to rebuild the exact live shoe state and
    keep dealing identically after a restart.
    """
    replayed = Shoe.replay(
        decks=_SHOE_DECKS,
        jokers_per_deck=_SHOE_JOKERS_PER_DECK,
        seed=seed,
        deals=shoe.dealt,
        cycles=shoe.cycle,
    )
    assert _next_deal(replayed) == _next_deal(shoe)


def _hands_for(entries: Sequence[_Entry]) -> list[tuple[str, EvaluatedHand]]:
    """Evaluate best_hand for every entry -- "every hand evaluable"."""
    return [(entry.plate, best_hand(entry.cards)) for entry in entries]


def _compare_by_hand(a: tuple[str, EvaluatedHand], b: tuple[str, EvaluatedHand]) -> int:
    """Compare two (plate, hand) pairs by their hand alone."""
    return compare(a[1], b[1])


def _assert_field_totally_orderable(hands: Sequence[tuple[str, EvaluatedHand]]) -> None:
    """Assert compare() never raises and totally orders a field.

    Sorting by ``cmp_to_key(compare)`` must yield a permutation of
    the original entries in non-decreasing hand order -- the property
    the standings ranking table depends on.
    """
    ordered = sorted(hands, key=cmp_to_key(_compare_by_hand))
    assert sorted(plate for plate, _ in ordered) == sorted(plate for plate, _ in hands)
    for (_, worse), (_, better) in itertools.pairwise(ordered):
        assert compare(worse, better) <= 0


def _assert_pooled_teams_hold_every_riders_cards(teams: Sequence[_Entry]) -> None:
    """Assert each pooled team's pool sums to its riders' own cards."""
    for team in teams:
        assert sum(team.rider_card_counts) == len(team.cards)


def _assert_lap_counts_within_provable_bounds(counts: Iterable[int]) -> None:
    """Assert every stream's lap count sits in [9, 18] (see constants).

    Provable, not probabilistic -- see :data:`_MIN_LAPS_PER_STREAM`
    and :data:`_MAX_LAPS_PER_STREAM`'s own comment above.
    """
    for count in counts:
        assert _MIN_LAPS_PER_STREAM <= count <= _MAX_LAPS_PER_STREAM


@pytest.fixture(autouse=True, scope="module")
def _assert_module_stays_within_ci_budget() -> Iterator[None]:
    """Fail loudly if the whole module's wall-clock creeps toward 60s.

    task-briefs.md's own done-when: "sims in CI stage 2 under 60s".
    """
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    assert elapsed < _MODULE_BUDGET_SECONDS


# ----------------------------------------------------------------- solo


def test_solo_ride_shoe_and_hands_hold_up_across_the_whole_field() -> None:
    """180 solos, 6h each: shoe accounting plus a totally-ordered field.

    One shared Shoe deals every crossing; ~2,000+ deals against its
    432-card cycle (spec section 4's own 180-entry-field composition)
    forces several reshuffle cycles, driven the caller way.
    """
    rng = random.Random(_SOLO_SEED)  # noqa: S311 -- a seeded test fixture, not a security use
    dealer = _Dealer(
        shoe=Shoe(decks=_SHOE_DECKS, jokers_per_deck=_SHOE_JOKERS_PER_DECK, seed=_SOLO_SEED),
        log=[],
    )

    start = time.perf_counter()
    entries = _solo_field(_FIELD_SIZE, rng, dealer)
    hands = _hands_for(entries)
    elapsed = time.perf_counter() - start

    _assert_shoe_accounting_exact(dealer, entries)
    _assert_replay_matches_next_deal(dealer.shoe, _SOLO_SEED)
    _assert_field_totally_orderable(hands)
    _assert_lap_counts_within_provable_bounds(len(entry.cards) for entry in entries)

    total_deals = sum(len(entry.cards) for entry in entries)
    assert total_deals >= 2000  # brief's own "~2,000+ deals" forcing multiple cycles
    assert dealer.shoe.cycle > 1
    assert elapsed < _SCENARIO_BUDGET_SECONDS


# ------------------------------------------------------- mixed-pooled


def test_mixed_pooled_ride_pools_every_riders_cards_uncapped() -> None:
    """Every rider's cards join their pooled team's uncapped total.

    Solos plus rider-pooled teams (2-10 riders, R-16), including the
    brief's own constructed case -- one rider out-laps their
    teammates -- whose pooled hand must strictly improve once the
    extra cards join.
    """
    rng = random.Random(_MIXED_POOLED_SEED)  # noqa: S311 -- seeded test fixture, not security
    dealer = _Dealer(
        shoe=Shoe(
            decks=_SHOE_DECKS, jokers_per_deck=_SHOE_JOKERS_PER_DECK, seed=_MIXED_POOLED_SEED
        ),
        log=[],
    )

    start = time.perf_counter()
    solos = _solo_field(_MIXED_SOLO_COUNT, rng, dealer)
    teams = _mixed_pooled_teams(_MIXED_TEAM_COUNT, rng, dealer)
    entries = [*solos, *teams]
    hands = _hands_for(entries)
    elapsed = time.perf_counter() - start

    _assert_shoe_accounting_exact(dealer, entries)
    _assert_replay_matches_next_deal(dealer.shoe, _MIXED_POOLED_SEED)
    _assert_field_totally_orderable(hands)
    _assert_pooled_teams_hold_every_riders_cards(teams)
    _assert_lap_counts_within_provable_bounds(len(entry.cards) for entry in solos)
    _assert_lap_counts_within_provable_bounds(
        count for team in teams[1:] for count in team.rider_card_counts
    )

    out_lapping_team = teams[0]
    steady_laps, out_lapper_laps = out_lapping_team.rider_card_counts
    steady_only_hand = best_hand(out_lapping_team.cards[:steady_laps])
    pooled_hand = best_hand(out_lapping_team.cards)
    assert len(out_lapping_team.cards) == steady_laps + out_lapper_laps
    assert compare(pooled_hand, steady_only_hand) == 1

    assert len(entries) == _FIELD_SIZE
    assert elapsed < _SCENARIO_BUDGET_SECONDS


# -------------------------------------------------------- mixed-relay


def test_mixed_relay_ride_deals_one_card_per_team_lap() -> None:
    """Relay teams deal one card per TEAM lap through one shared plate.

    Solos plus relay teams -- far fewer deals than pooling costs for
    the same laps, since only one rider is ever on course at a time
    (the EPIC's own format).
    """
    rng = random.Random(_MIXED_RELAY_SEED)  # noqa: S311 -- seeded test fixture, not security
    dealer = _Dealer(
        shoe=Shoe(
            decks=_SHOE_DECKS, jokers_per_deck=_SHOE_JOKERS_PER_DECK, seed=_MIXED_RELAY_SEED
        ),
        log=[],
    )

    start = time.perf_counter()
    solos = _solo_field(_MIXED_SOLO_COUNT, rng, dealer)
    relay_teams = _relay_field(_MIXED_TEAM_COUNT, rng, dealer)
    entries = [*solos, *relay_teams]
    hands = _hands_for(entries)
    elapsed = time.perf_counter() - start

    _assert_shoe_accounting_exact(dealer, entries)
    _assert_replay_matches_next_deal(dealer.shoe, _MIXED_RELAY_SEED)
    _assert_field_totally_orderable(hands)
    _assert_lap_counts_within_provable_bounds(len(entry.cards) for entry in entries)

    assert len(entries) == _FIELD_SIZE
    assert elapsed < _SCENARIO_BUDGET_SECONDS
