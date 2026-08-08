# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.hands (E2.1.1, E2.1.2, E2.1.3, E2.4.1).

``src/rivercrossing/vectors/rank_sweep.csv`` (tools/gen_rank_vectors.py)
is E2.1.1's specification: one representative 5-card hand per
phevaluator rank, all 7,462 of them, with a hand class computed
independently of phevaluator (module docstring there;
tests/unit/test_gen_rank_vectors.py covers the generator itself). The
sweep test below is that task's named acceptance test -- "all 7,462
distinct ranks hit exactly once across the class sweep" -- everything
else in that section pins the other brief-named behaviours (the
wheel, flush over straight, the negative card-count case) plus the
card model this module consumes.

``src/rivercrossing/vectors/joker_vectors.csv`` is E2.1.2's
specification: 28 hand-authored wild-card vectors (spec section 5's
joker layer), each row's expected class/tiebreak/resolution reasoned
from the spec, not from this module -- see the CSV's own description
column and design/docs-md/spec.md section 5's joker vector table.
E2.4.1 (spec section 12, R-44) moved both CSVs from ``tests/vectors/``
into the package itself, as data ``rivercrossing.hands.self_test``
loads at runtime, so the bundled app can self-test at launch with no
``tests/`` tree riding along.

E2.1.3 adds physical-cards duplicate handling (the segfault regression
below), ``best_hand``'s best-5-of-N search, its partial-hand rule for
fewer than 5 cards, and the card-cap fixtures -- all per spec section 5
and the ruling recorded in design/docs-md/spec.md.

E2.4.1 adds the evaluator self-test suite itself: :func:`self_test`
re-runs the same rank sweep and joker vectors through a monkeypatchable
loader seam, so a corrupted table can be proven to fail the report
rather than merely trusted to pass it.
"""

import csv
import itertools
import math
import random
import re
import time
from collections import Counter
from pathlib import Path
from typing import NamedTuple, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rivercrossing import hands
from rivercrossing.cards import Card, Rank, Shoe, Suit
from rivercrossing.hands import (
    NATURAL_HAND_SIZE,
    NATURAL_RANK_COUNT,
    EvaluatedHand,
    HandClass,
    InvalidHandError,
    SelfTestCheck,
    SelfTestReport,
    _kicker_tiebreak,
    best_hand,
    classify_pattern,
    compare,
    eval5,
    self_test,
)

_VECTORS_DIR = Path(__file__).resolve().parents[2] / "src" / "rivercrossing" / "vectors"
_RANK_SWEEP_CSV = _VECTORS_DIR / "rank_sweep.csv"
_JOKER_VECTORS_CSV = _VECTORS_DIR / "joker_vectors.csv"

# Cactus Kev's published counts, folded to this project's own classes:
# ROYAL_FLUSH (1) + STRAIGHT_FLUSH (9) here match "straight flush,
# royal included, 10" in the brief and in every published reference.
_EXPECTED_CLASS_COUNTS = {
    "STRAIGHT_FLUSH_OR_ROYAL": 10,
    "QUADS": 156,
    "FULL_HOUSE": 156,
    "FLUSH": 1277,
    "STRAIGHT": 10,
    "TRIPS": 858,
    "TWO_PAIR": 858,
    "PAIR": 2860,
    "HIGH_CARD": 1277,
}

_SWEEP_BUDGET_SECONDS = 5.0


class _RankSweepRow(NamedTuple):
    """One row of src/rivercrossing/vectors/rank_sweep.csv."""

    cards: str
    rank: int
    hand_class: str


def _load_rank_sweep_rows(path: Path) -> list[_RankSweepRow]:
    """Read the committed rank sweep CSV into ``_RankSweepRow`` rows."""
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            _RankSweepRow(cards=row["cards"], rank=int(row["rank"]), hand_class=row["hand_class"])
            for row in csv.DictReader(handle)
        ]


def _cards(codes: str) -> list[Card]:
    """Parse a space-separated string of card codes into Cards."""
    return [Card.parse(code) for code in codes.split()]


# ---------------------------------------------------------- the sweep


def test_eval5_sweep_matches_every_committed_rank_within_budget() -> None:
    """Every one of the 7,462 committed ranks round-trips through eval5.

    Also cross-checks ``eval5``'s table-driven ``HandClass`` against
    the CSV's independently (first-principles) computed one for every
    row -- not just the aggregate counts below -- and that sorting by
    ``(cls, tiebreak)`` reproduces the CSV's own rank order exactly
    (see ``test_classify_pattern_matches_phevaluator_order`` for the
    same cross-check run directly against the first-principles path,
    bypassing eval5 and phevaluator both).

    # logic-coverage-exempt: T-8 -- this *is* the brief-named "sweep":
    # one hand per distinct phevaluator rank, all 7,462 of them, with
    # a single measured wall-clock budget for the whole pass.
    # Splitting it into 7,462 parametrize rows would both defeat that
    # aggregate timing assertion and turn one conceptual test into
    # thousands of near-identical ones (CODINGSTANDARDS-SIMPLECODE.md
    # rule 7).
    """
    rows = _load_rank_sweep_rows(_RANK_SWEEP_CSV)
    evaluated_by_row: list[tuple[EvaluatedHand, _RankSweepRow]] = []

    start = time.perf_counter()
    for row in rows:
        hand = eval5(_cards(row.cards))
        assert hand.cls.name == row.hand_class
        evaluated_by_row.append((hand, row))
    elapsed = time.perf_counter() - start

    best_to_worst = sorted(
        evaluated_by_row, key=lambda pair: (pair[0].cls, pair[0].tiebreak), reverse=True
    )
    ranks_in_hand_order = [row.rank for _, row in best_to_worst]
    keys = [(hand.cls, hand.tiebreak) for hand, _ in evaluated_by_row]

    assert len(rows) == NATURAL_RANK_COUNT
    assert ranks_in_hand_order == list(range(1, NATURAL_RANK_COUNT + 1))
    # Distinctness is not implied by the line above: sorted() is
    # stable, so two rows whose (cls, tiebreak) collapse to an equal
    # key keep the CSV's own already-sorted order and this list would
    # still read back as 1..7462 -- a regression that collapses
    # distinct ranks into ties could never fail the check above.
    # Proven: temporarily dropping TWO_PAIR's kicker in
    # _kicker_tiebreak collapsed 7,462 keys to 6,682 distinct ones
    # while the assertion above stayed green.
    assert len(set(keys)) == NATURAL_RANK_COUNT
    assert elapsed < _SWEEP_BUDGET_SECONDS


def test_eval5_sweep_class_counts_match_known_ground_truth() -> None:
    """The sweep's per-class counts match the published poker table."""
    rows = _load_rank_sweep_rows(_RANK_SWEEP_CSV)
    counts = Counter(row.hand_class for row in rows)
    combined = {
        "STRAIGHT_FLUSH_OR_ROYAL": counts["ROYAL_FLUSH"] + counts["STRAIGHT_FLUSH"],
        "QUADS": counts["QUADS"],
        "FULL_HOUSE": counts["FULL_HOUSE"],
        "FLUSH": counts["FLUSH"],
        "STRAIGHT": counts["STRAIGHT"],
        "TRIPS": counts["TRIPS"],
        "TWO_PAIR": counts["TWO_PAIR"],
        "PAIR": counts["PAIR"],
        "HIGH_CARD": counts["HIGH_CARD"],
    }

    assert combined == _EXPECTED_CLASS_COUNTS


def test_classify_pattern_matches_phevaluator_order_across_the_sweep() -> None:
    """The first-principles path orders the sweep like phevaluator does.

    The E2.1.3 ruling's cross-check: every rank_sweep.csv row is a
    distinct-code hand, so ``eval5`` always takes phevaluator's fast
    path for these -- this test instead calls ``classify_pattern``
    directly (never phevaluator, never ``eval5``) and confirms sorting
    by its ``(cls, tiebreak)`` reproduces the exact same rank order,
    so the two tiebreak sources can never silently disagree.
    """
    rows = _load_rank_sweep_rows(_RANK_SWEEP_CSV)
    scored: list[tuple[HandClass, tuple[int, ...], int]] = []
    for row in rows:
        cards = _cards(row.cards)
        ranks = [card.rank.value for card in cards if card.rank is not None]
        suits = [card.suit.value for card in cards if card.suit is not None]
        cls = classify_pattern(ranks, suits)
        scored.append((cls, _kicker_tiebreak(cls, ranks), row.rank))

    best_to_worst = sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)
    ranks_in_order = [rank for _, _, rank in best_to_worst]

    assert ranks_in_order == list(range(1, NATURAL_RANK_COUNT + 1))


# ------------------------------------------------- named brief vectors


def test_eval5_wheel_ranks_below_six_high_straight() -> None:
    """The wheel (A-2-3-4-5) is a worse hand than a 6-high straight."""
    wheel = eval5(_cards("AS 2C 3D 4H 5S"))
    six_high = eval5(_cards("2S 3C 4D 5H 6S"))

    assert compare(wheel, six_high) == -1


def test_eval5_flush_outranks_straight() -> None:
    """A flush beats a straight (concrete, non-competing vectors)."""
    flush = eval5(_cards("2S 5S 7S 9S KS"))
    straight = eval5(_cards("3S 4C 5D 6H 7S"))

    assert compare(flush, straight) == 1


def test_compare_identical_hands_returns_zero() -> None:
    """Two evaluations of the same 5 cards compare as equal."""
    first = eval5(_cards("AS KS QS JS TS"))
    second = eval5(_cards("AS KS QS JS TS"))

    assert compare(first, second) == 0


def test_eval5_royal_flush_is_the_best_natural_hand() -> None:
    """The royal flush lands in ``HandClass.ROYAL_FLUSH``, no kicker."""
    royal = eval5(_cards("TS JS QS KS AS"))

    assert royal.cls == HandClass.ROYAL_FLUSH
    assert royal.tiebreak == (NATURAL_HAND_SIZE,)


# -------------------------------------------------------- negative path


@pytest.mark.parametrize("card_count", [0, 4, 6])
def test_eval5_wrong_card_count_raises_invalid_hand_error(card_count: int) -> None:
    """eval5 rejects anything but exactly 5 cards."""
    cards = _cards("2C 3D 4H 5S 6C 7D")[:card_count]

    with pytest.raises(InvalidHandError, match=re.escape(f"got {card_count}")):
        eval5(cards)


# ---------------------------------------------------- Card round trip


_NATURAL_CARDS = st.builds(
    Card, rank=st.sampled_from(list(Rank)), suit=st.sampled_from(list(Suit))
)
_JOKER_CARDS = st.just(Card(rank=None, suit=None, joker=True))


@given(card=_NATURAL_CARDS | _JOKER_CARDS)
def test_card_parse_round_trips_every_code(card: Card) -> None:
    """Card.parse(card.code()) reconstructs an equal card (T-7)."""
    assert Card.parse(card.code()) == card


# ------------------------------------------- E2.1.2: the joker vectors


class _JokerVectorRow(NamedTuple):
    """One row of src/rivercrossing/vectors/joker_vectors.csv."""

    cards: str
    expected_class: str
    expected_tiebreak: tuple[int, ...]
    expected_joker_ranks: tuple[str, ...]
    expected_joker_codes: tuple[str, ...]
    description: str


def _load_joker_vector_rows(path: Path) -> list[_JokerVectorRow]:
    """Read the committed joker vector CSV into ``_JokerVectorRow``s."""
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            _JokerVectorRow(
                cards=row["cards"],
                expected_class=row["expected_class"],
                expected_tiebreak=tuple(int(part) for part in row["expected_tiebreak"].split(";")),
                expected_joker_ranks=tuple(row["expected_joker_ranks"].split(";")),
                expected_joker_codes=(
                    tuple(row["expected_joker_codes"].split(";"))
                    if row["expected_joker_codes"]
                    else ()
                ),
                description=row["description"],
            )
            for row in csv.DictReader(handle)
        ]


_JOKER_VECTOR_ROWS = _load_joker_vector_rows(_JOKER_VECTORS_CSV)
_PINNED_CODE_ROWS = [row for row in _JOKER_VECTOR_ROWS if row.expected_joker_codes]
_ALL_52_NATURAL_CARDS = tuple(Card(rank=rank, suit=suit) for rank in Rank for suit in Suit)


def _joker_count(cards: list[Card]) -> int:
    """Count how many of *cards* are jokers."""
    return sum(1 for card in cards if card.joker)


# <=2 jokers, and not five-of-a-kind: that class needs a duplicate
# card id to be reachable at all (R1's own point), which is exactly
# what phevaluator's native evaluator cannot safely see (module
# docstring), so a *safely deduplicated* brute force could never
# reconstruct it -- not a gap in eval5, a mismatch between this
# verification technique and that one class.
_GENERAL_SEARCH_ROWS = [
    row
    for row in _JOKER_VECTOR_ROWS
    if _joker_count(_cards(row.cards)) <= 2 and row.expected_class != "FIVE_OF_A_KIND"
]


@pytest.mark.parametrize("row", _JOKER_VECTOR_ROWS, ids=lambda row: row.cards)
def test_eval5_joker_vector_matches_authored_class_tiebreak_and_ranks(
    row: _JokerVectorRow,
) -> None:
    """Each of the 28 authored joker vectors evaluates as expected."""
    evaluated = eval5(_cards(row.cards))

    assert evaluated.cls.name == row.expected_class
    assert evaluated.tiebreak == row.expected_tiebreak
    joker_ranks = tuple(cast("Rank", card.rank).name for card in evaluated.jokers_played_as)
    assert joker_ranks == row.expected_joker_ranks


@pytest.mark.parametrize("row", _PINNED_CODE_ROWS, ids=lambda row: row.cards)
def test_eval5_joker_vector_matches_authored_exact_codes(row: _JokerVectorRow) -> None:
    """Rows pinning an exact suit resolve to that exact card."""
    evaluated = eval5(_cards(row.cards))

    joker_codes = tuple(card.code() for card in evaluated.jokers_played_as)
    assert joker_codes == row.expected_joker_codes


def test_eval5_five_of_a_kind_outranks_royal_flush() -> None:
    """Five of a kind (wild) always beats a natural royal flush."""
    five_of_a_kind = eval5(_cards("AS AD AH AC JK"))
    royal_flush = eval5(_cards("AS KS QS JS TS"))

    assert compare(five_of_a_kind, royal_flush) == 1


def test_eval5_joker_duplicate_natural_card_still_maximizes() -> None:
    """A joker may resolve to a card identical to one already held.

    All four ace suits are already in the natural cards, so the only
    way to reach five of a kind is to duplicate one -- duplicate-legal
    per the multi-deck shoe (spec section 4). This must not silently
    fall back to a lesser hand just because every suit is "taken".
    """
    evaluated = eval5(_cards("AS AD AH AC JK"))

    assert evaluated.cls == HandClass.FIVE_OF_A_KIND
    assert evaluated.jokers_played_as[0].rank == Rank.ACE


@pytest.mark.parametrize("row", _GENERAL_SEARCH_ROWS, ids=lambda row: row.cards)
def test_eval5_joker_resolution_matches_exhaustive_natural_search(row: _JokerVectorRow) -> None:
    """An independent brute force never beats the authored best hand.

    Guards against a pruning bug: this re-derives the best resolution
    by exhaustively substituting every combination of real cards for
    the joker slots and re-evaluating through eval5's own natural
    (0-joker) path -- never through its wild search -- so it cannot
    simply agree with a shared bug in that search. Limited to <= 2
    jokers, non-five-of-a-kind rows so the brute force stays cheap and
    every candidate is safe to hand to phevaluator (see
    ``_GENERAL_SEARCH_ROWS``); every 3+ joker or five-of-a-kind row in
    this vector table hits the spec's own shortcut instead, which is
    correct by construction (hands.py's own module docstring), not
    something this combinatorial check would add confidence to.
    """
    cards = _cards(row.cards)
    naturals = [card for card in cards if not card.joker]
    joker_count = len(cards) - len(naturals)
    fills = itertools.combinations_with_replacement(_ALL_52_NATURAL_CARDS, joker_count)
    candidates = (
        (*naturals, *fill) for fill in fills if len({c.code() for c in (*naturals, *fill)}) == 5
    )

    evaluated_candidates = (eval5(candidate) for candidate in candidates)
    best = max(evaluated_candidates, key=lambda hand: (hand.cls, hand.tiebreak))

    assert (best.cls.name, best.tiebreak) == (row.expected_class, row.expected_tiebreak)


# --------------------------------- E2.1.3: physical-cards duplicates


def test_eval5_two_identical_natural_cards_score_as_a_pair() -> None:
    """Two identical natural cards are simply a pair -- not a crash.

    Regression: this exact input previously reached phevaluator's
    native evaluator with a repeated card id, which segfaults
    (hands.py's module docstring). The ruling in design/docs-md/
    spec.md section 5 makes physical-cards duplicates legal, scored
    first principles instead.
    """
    evaluated = eval5(_cards("9H 9H KC QD 2S"))

    assert evaluated.cls == HandClass.PAIR
    assert evaluated.tiebreak == (NATURAL_HAND_SIZE, 9, 13, 12, 2)


def test_eval5_three_identical_natural_cards_score_as_trips() -> None:
    """Three identical-rank natural cards score as trips.

    The ruling's own example (spec section 5): 9H 9H 9S KC QD is
    trips, not a phevaluator crash.
    """
    evaluated = eval5(_cards("9H 9H 9S KC QD"))

    assert evaluated.cls == HandClass.TRIPS
    assert evaluated.tiebreak == (NATURAL_HAND_SIZE, 9, 13, 12)


def test_eval5_duplicate_card_flush_kickers_are_not_grouped_by_count() -> None:
    """A flush's kickers compare by raw rank, even with a paired card.

    The ruling's own example (spec section 5): 9H 9H KH QH 2H is a
    king-high flush whose kickers include the paired nines -- the
    king must still outrank the pair of nines in the tiebreak, unlike
    PAIR/TRIPS/etc., where group size always dominates.
    """
    evaluated = eval5(_cards("9H 9H KH QH 2H"))

    assert evaluated.cls == HandClass.FLUSH
    assert evaluated.tiebreak == (NATURAL_HAND_SIZE, 13, 12, 9, 9, 2)


def test_eval5_five_identical_natural_cards_score_as_five_of_a_kind() -> None:
    """5 physically identical natural cards are FIVE_OF_A_KIND.

    No joker involved at all -- reachable only via a 5+ deck shoe
    (spec section 4), the natural counterpart to E2.1.2's wild
    five-of-a-kind.
    """
    evaluated = eval5(_cards("9H 9H 9H 9H 9H"))

    assert evaluated.cls == HandClass.FIVE_OF_A_KIND
    assert evaluated.tiebreak == (NATURAL_HAND_SIZE, 9)


# --------------------------------------------- E2.1.3: best_hand


def test_best_hand_zero_cards_is_the_lowest_possible_hand() -> None:
    """0 cards is the deliberately-defined lowest possible hand."""
    evaluated = best_hand([])

    assert evaluated.cls == HandClass.HIGH_CARD
    assert evaluated.tiebreak == (0,)


def test_best_hand_five_or_more_jokers_among_many_cards_is_five_aces() -> None:
    """5+ jokers among a larger N-card pool still resolve to five aces.

    Spec section 5's ``j >= 5 -> FIVE_OF_KIND(Ace)`` shortcut, reached
    here through best_hand's own N-card path rather than eval5's
    exactly-5 one: 6 jokers among 7 cards is unconditionally five
    aces, the fixed natural KS along for the ride never scores.
    """
    evaluated = best_hand([*_cards("KS"), *_cards("JK JK JK JK JK JK")])

    assert evaluated.cls == HandClass.FIVE_OF_A_KIND
    assert evaluated.tiebreak == (NATURAL_HAND_SIZE, Rank.ACE.value)


def test_best_hand_four_card_ace_high_sits_under_every_five_card_ace_high() -> None:
    """A 4-card ace-high hand sits under every 5-card ace-high hand.

    Spec section 5's own partial-hand sentence, tested literally: the
    4-card hand has objectively better kickers (A,K,Q,J) than the
    5-card one (A,9,7,5,3), yet must still lose -- a missing kicker
    always ranks below a present one.
    """
    four_card_ace_high = best_hand(_cards("AS KD QH JC"))
    worst_five_card_ace_high = eval5(_cards("AS 9D 7H 5C 3S"))

    assert four_card_ace_high.cls == HandClass.HIGH_CARD
    assert worst_five_card_ace_high.cls == HandClass.HIGH_CARD
    assert compare(four_card_ace_high, worst_five_card_ace_high) == -1


def test_best_hand_n12_j2_explores_exactly_120_natural_subsets() -> None:
    """N=12 with 2 jokers explores exactly C(10,3)=120 natural subsets.

    best_hand exposes no internal counter (frozen S4 signature), so
    this pins the subset math (spec section 5's pseudocode) indirectly:
    an independent brute force over the same C(10,3) subsets, counted
    explicitly, must agree with best_hand's own answer.
    """
    naturals = _cards("2C 3D 4H 5S 6C 7D 8H 9S TC JD")
    jokers = _cards("JK JK")
    subsets = list(itertools.combinations(naturals, NATURAL_HAND_SIZE - len(jokers)))
    brute_force_best = max(
        (eval5((*subset, *jokers)) for subset in subsets),
        key=lambda hand: (hand.cls, hand.tiebreak),
    )

    result = best_hand([*naturals, *jokers])

    assert len(subsets) == math.comb(10, 3)
    assert len(subsets) == 120
    assert (result.cls, result.tiebreak) == (brute_force_best.cls, brute_force_best.tiebreak)


def test_best_hand_card_cap_blocks_a_later_improving_card() -> None:
    """Card 11 would improve the hand; capping at 10 must not see it.

    Card cap X (spec section 5, R-13) is a caller concern: slicing to
    the first X dealt cards before calling best_hand is the whole
    contract. Card 11 (KH) pairs the existing KD -- a genuine class
    upgrade (HIGH_CARD -> PAIR) -- so scoring only the first 10 really
    does block an improvement the 11th card would have made.
    """
    first_ten = _cards("2C 3D 4H 5S 7C 8D 9H TS QC KD")
    first_eleven = [*first_ten, *_cards("KH")]

    capped = best_hand(first_ten)
    uncapped = best_hand(first_eleven)

    assert capped.cls == HandClass.HIGH_CARD
    assert uncapped.cls == HandClass.PAIR
    assert compare(capped, uncapped) == -1


_ANY_CARD_STRATEGY = _NATURAL_CARDS | _JOKER_CARDS


@st.composite
def _hand_with_bounded_jokers(draw: st.DrawFn) -> list[Card]:
    """Draw 0-6 naturals plus 0-2 jokers.

    Unbounded joker density makes ``best_hand``'s wild search
    combinatorially expensive (hands.py's own ``_search_best_fill``
    docstring); this keeps the property test's examples fast without
    narrowing which code paths it exercises.
    """
    naturals = draw(st.lists(_NATURAL_CARDS, min_size=0, max_size=6))
    joker_count = draw(st.integers(min_value=0, max_value=2))
    jokers = [Card(rank=None, suit=None, joker=True) for _ in range(joker_count)]
    return [*naturals, *jokers]


@given(cards=_hand_with_bounded_jokers(), extra=_ANY_CARD_STRATEGY)
@settings(max_examples=50, deadline=None)
def test_best_hand_adding_a_card_never_lowers_the_result(cards: list[Card], extra: Card) -> None:
    """Adding any card never makes best_hand's result worse."""
    before = best_hand(cards)
    after = best_hand([*cards, extra])

    assert compare(after, before) >= 0


def test_best_hand_field_of_180_entries_by_12_cards_scores_within_measured_budget() -> None:
    """The whole 180x12 field scores in under 1 second (R-42).

    Seeded, duplicates and jokers included: an 8-deck/2-jokers-per-deck
    shoe (spec section 4's own example) has 16 jokers among 432 cards,
    so a 12-card sample draws one with roughly that same probability.
    R-42 is a MUST and this bound is the requirement itself, not a
    measured ceiling with headroom -- CI runners are slower than a
    development machine, so the implementation must clear this with
    real margin, not just on the fastest hardware available.
    """
    rng = random.Random(20260807)  # noqa: S311 -- a seeded test fixture, not a security use
    deck_codes = [f"{rank}{suit}" for rank in "23456789TJQKA" for suit in "CDHS"]
    joker_probability = 16 / 432
    field = [
        [
            Card(rank=None, suit=None, joker=True)
            if rng.random() < joker_probability
            else Card.parse(rng.choice(deck_codes))
            for _ in range(12)
        ]
        for _ in range(180)
    ]

    start = time.perf_counter()
    for entry in field:
        best_hand(entry)
    elapsed = time.perf_counter() - start

    assert len(field) == 180
    assert elapsed < 1.0  # R-42: the whole field scores in under 1 second


# ------------------------------------- E2.3.1: uncapped pooled budgets


# R-16 makes uncapped rider-pooled the DEFAULT plate model: a 3-10
# rider team over the 6h window pools 30-120+ cards. Measured on the
# unfixed evaluator: n=30 -> 0.57s, n=40 -> 2.6s, n=50 -> 8.5s,
# n=60 -> 22s (this machine: 21.4s at the seed below) -- the C(n, 5-j)
# natural-subset search's own pruning (_relevant_naturals /
# _plausible_flush_suits) silently saturates past ~20 cards, so every
# suit looks flush-plausible and nothing is pruned at all. Both seeds
# are chosen (not the first tried) to deal zero jokers: a joker count
# near NATURAL_HAND_SIZE shrinks the natural-subset size (5 - jokers)
# back down to trivial, which would mask the defect rather than pin it.
_POOLED_SCALE_BUDGETS = [(60, 20260867, 0.1), (120, 20260809, 0.2)]


@pytest.mark.parametrize(("card_count", "seed", "budget_seconds"), _POOLED_SCALE_BUDGETS)
def test_best_hand_pooled_scale_completes_within_measured_budget(
    card_count: int, seed: int, budget_seconds: float
) -> None:
    """A pooled-team hand of card_count cards scores within its budget.

    Today's evaluator fails this by two orders of magnitude at n=60
    (measured ~21-22s against a 0.1s budget); n=120's C(120,5) blowup
    is steeper still, pinning that the rewrite tames the growth curve,
    not just the single n=60 case.
    """
    shoe = Shoe(decks=8, jokers_per_deck=2, seed=seed)
    cards = [shoe.deal()[0] for _ in range(card_count)]

    start = time.perf_counter()
    best_hand(cards)
    elapsed = time.perf_counter() - start

    assert elapsed < budget_seconds


def _exhaustive_best_hand(cards: list[Card]) -> EvaluatedHand:
    """Compute the ORIGINAL exhaustive subset+substitution search.

    Deliberately independent of every one of hands.py's own (now
    removed) pruning helpers: every C(len(naturals), 5-j) natural
    subset, crossed with every full substitution of the j joker slots
    from all 52 real cards -- the ground truth ``best_hand``'s
    analytic construction must reproduce, computed a way that could
    never share a pruning bug with the code under test.
    """
    naturals = [card for card in cards if not card.joker]
    joker_count = len(cards) - len(naturals)
    need = NATURAL_HAND_SIZE - joker_count
    fills = (
        list(itertools.combinations_with_replacement(_ALL_52_NATURAL_CARDS, joker_count))
        if joker_count
        else [()]
    )
    candidates = (
        eval5((*subset, *fill))
        for subset in itertools.combinations(naturals, need)
        for fill in fills
    )
    return max(candidates, key=lambda hand: (hand.cls, hand.tiebreak))


# (seed, card_count, joker_count) -- n spans 5..12, jokers 0..3, chosen
# to keep the *oracle's* own O(C(n-j,5-j) * C(52+j-1,j)) cost bounded:
# n=12 with j=3 alone would need ~893k eval5 calls just for the oracle,
# so the widest n only pairs with j<=2 here.
_POOLED_EQUIVALENCE_BATTERY = [
    (1, 5, 0),
    (2, 5, 3),
    (3, 7, 1),
    (4, 7, 2),
    (5, 9, 0),
    (6, 9, 2),
    (7, 10, 1),
    (8, 12, 0),
    (9, 12, 1),
    (10, 12, 2),
]


def _duplicate_bearing_hand(seed: int, card_count: int, joker_count: int) -> list[Card]:
    """Draw card_count cards, joker_count jokers, with replacement.

    Sampling with replacement over all 52 naturals deliberately allows
    duplicates -- a multi-deck shoe can legally deal the same code
    twice (module docstring's physical-cards rule).
    """
    rng = random.Random(seed)  # noqa: S311 -- a seeded test fixture, not a security use
    naturals = [rng.choice(_ALL_52_NATURAL_CARDS) for _ in range(card_count - joker_count)]
    jokers = [Card(rank=None, suit=None, joker=True) for _ in range(joker_count)]
    return [*naturals, *jokers]


@pytest.mark.parametrize(
    ("seed", "card_count", "joker_count"), _POOLED_EQUIVALENCE_BATTERY, ids=str
)
def test_best_hand_matches_exhaustive_oracle_across_seeded_multi_deck_draws(
    seed: int, card_count: int, joker_count: int
) -> None:
    """best_hand's (cls, tiebreak) matches the true, unpruned best-of-N.

    Freezes correctness across the rewrite: today's pruned subset
    search can silently drop a natural card a winning straight or
    flush needed once the pool's overall rank span exceeds
    ``_STRAIGHT_SPAN`` (measured: seeds 4 and 5 below reach a real
    straight the pruned search misses entirely, settling for trips
    instead) -- a correctness bug, not merely a speed one, that this
    oracle catches independently of the growth-budget tests above.
    """
    cards = _duplicate_bearing_hand(seed, card_count, joker_count)
    expected = _exhaustive_best_hand(cards)

    result = best_hand(cards)

    assert (result.cls, result.tiebreak) == (expected.cls, expected.tiebreak)


# ------------------------------------------------- E2.4.1: self_test


def test_self_test_given_the_real_vectors_reports_all_four_checks_passed() -> None:
    """The real shipped vectors keep every self-test check green."""
    report = self_test()

    assert report.passed is True
    assert len(report.checks) == 4


def test_self_test_check_names_match_the_selftest_dlg_canvas_order() -> None:
    """The four checks appear in xrc-windows.md's own fixed order."""
    report = self_test()

    assert tuple(check.name for check in report.checks) == (
        "7,462 distinct ranks",
        "Joker vector table (28)",
        "Five-of-a-kind ordering",
        "Whole-field 180×12 timing",  # noqa: RUF001 -- xrc-windows.md's frozen text
    )


def test_self_test_field_timing_check_detail_reports_seconds_under_budget() -> None:
    """The field check's own detail is an "N.NN s" duration (R-42)."""
    report = self_test()
    timing_check = report.checks[3]

    assert re.fullmatch(r"\d+\.\d{2} s", timing_check.detail)
    assert timing_check.duration_seconds < 1.0


def test_self_test_five_of_a_kind_check_passes_with_no_timing_detail() -> None:
    """Check (c) carries no timing detail -- only check (d) does."""
    report = self_test()

    assert report.checks[2].passed is True
    assert report.checks[2].detail == ""


def test_self_test_report_passed_true_when_every_check_passed() -> None:
    """SelfTestReport.passed is True when every check passed."""
    check = SelfTestCheck(name="x", passed=True, duration_seconds=0.0, detail="")
    report = SelfTestReport(checks=(check,))

    assert report.passed is True


def test_self_test_report_passed_false_when_any_check_failed() -> None:
    """SelfTestReport.passed is False when any single check failed."""
    passing = SelfTestCheck(name="x", passed=True, duration_seconds=0.0, detail="")
    failing = SelfTestCheck(name="y", passed=False, duration_seconds=0.0, detail="")
    report = SelfTestReport(checks=(passing, failing))

    assert report.passed is False


def test_self_test_corrupted_rank_sweep_short_table_fails_that_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong-length rank-sweep table fails the sweep check.

    Exercises the sweep check's ``and``'s left operand: the row-count
    mismatch alone fails this, before any rank is even compared (T-3;
    the same-length corruption below exercises the right operand).
    """
    monkeypatch.setattr(hands, "_load_rank_sweep_vectors", lambda: (("2C 3D 4H 5S 6C", 1),))

    report = self_test()

    assert report.checks[0].passed is False
    assert report.passed is False


def test_self_test_corrupted_rank_sweep_duplicate_rank_fails_that_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-length table with a duplicated rank still fails.

    The row count still matches NATURAL_RANK_COUNT, so only the
    ordering comparison (the ``and``'s right operand) can fail this.
    """
    real_rows = hands._load_rank_sweep_vectors()
    corrupted = list(real_rows)
    corrupted[1] = (corrupted[1][0], corrupted[0][1])  # duplicate row 0's rank onto row 1
    monkeypatch.setattr(hands, "_load_rank_sweep_vectors", lambda: tuple(corrupted))

    report = self_test()

    assert report.checks[0].passed is False
    assert len(corrupted) == NATURAL_RANK_COUNT


def test_self_test_corrupted_joker_vectors_short_table_fails_that_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty joker-vector table fails the joker-vector check.

    Exercises that check's ``and``'s left operand (T-3; the wrong-
    class corruption below exercises the right operand).
    """
    monkeypatch.setattr(hands, "_load_joker_vectors", lambda: ())

    report = self_test()

    assert report.checks[1].passed is False
    assert report.passed is False


def test_self_test_corrupted_joker_vectors_wrong_class_fails_that_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-length table with one wrong expected class still fails."""
    real_rows = hands._load_joker_vectors()
    bad_row = real_rows[0]
    corrupted = (
        hands._JokerVector(
            cards=bad_row.cards,
            expected_class="HIGH_CARD",
            expected_tiebreak=bad_row.expected_tiebreak,
        ),
        *real_rows[1:],
    )
    monkeypatch.setattr(hands, "_load_joker_vectors", lambda: corrupted)

    report = self_test()

    assert report.checks[1].passed is False
    assert len(corrupted) == len(real_rows)


def test_self_test_corrupted_joker_vectors_wrong_tiebreak_fails_that_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A right expected class but wrong tiebreak still fails the check.

    Exercises the mismatch filter's ``or``'s right operand (T-3): the
    class-mismatch test above only ever exercises its left operand,
    since a class mismatch short-circuits before the tiebreak compare.
    """
    real_rows = hands._load_joker_vectors()
    bad_row = real_rows[0]
    corrupted = (
        hands._JokerVector(
            cards=bad_row.cards,
            expected_class=bad_row.expected_class,
            expected_tiebreak=(*bad_row.expected_tiebreak, 999),
        ),
        *real_rows[1:],
    )
    monkeypatch.setattr(hands, "_load_joker_vectors", lambda: corrupted)

    report = self_test()

    assert report.checks[1].passed is False


def test_check_field_timing_given_a_slow_clock_reports_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A measured duration at/above the R-42 budget fails this check.

    Monkeypatches ``time.perf_counter`` -- an allowed clock seam, the
    same category as ``datetime.now`` -- instead of actually running
    slowly, so the budget comparison's false branch is exercised
    deterministically (T-3): the true branch is already covered by
    ``test_self_test_field_timing_check_detail_reports_seconds_under_budget``.
    """
    values = iter([0.0, 1.5])
    monkeypatch.setattr(hands.time, "perf_counter", lambda: next(values))

    passed, detail = hands._check_field_timing()

    assert passed is False
    assert detail == "1.50 s"
