# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.hands (E2.1.1, E2.1.2, E2.1.3).

``tests/vectors/rank_sweep.csv`` (tools/gen_rank_vectors.py) is E2.1.1's
specification: one representative 5-card hand per phevaluator rank, all
7,462 of them, with a hand class computed independently of phevaluator
(module docstring there; tests/unit/test_gen_rank_vectors.py covers the
generator itself). The sweep test below is that task's named
acceptance test -- "all 7,462 distinct ranks hit exactly once across
the class sweep" -- everything else in that section pins the other
brief-named behaviours (the wheel, flush over straight, the negative
card-count case) plus the card model this module consumes.

``tests/vectors/joker_vectors.csv`` is E2.1.2's specification: 28
hand-authored wild-card vectors (spec section 5's joker layer), each
row's expected class/tiebreak/resolution reasoned from the spec, not
from this module -- see the CSV's own description column and
design/docs-md/spec.md section 5's joker vector table.

E2.1.3 adds physical-cards duplicate handling (the segfault regression
below), ``best_hand``'s best-5-of-N search, its partial-hand rule for
fewer than 5 cards, and the card-cap fixtures -- all per spec section 5
and the ruling recorded in design/docs-md/spec.md.
"""

import csv
import itertools
import math
import random
import re
import time
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rivercrossing.cards import Card, Rank, Suit
from rivercrossing.hands import (
    NATURAL_HAND_SIZE,
    NATURAL_RANK_COUNT,
    EvaluatedHand,
    HandClass,
    InvalidHandError,
    _kicker_tiebreak,
    best_hand,
    classify_pattern,
    compare,
    eval5,
)

_VECTORS_DIR = Path(__file__).resolve().parents[2] / "tests" / "vectors"
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
    """One row of tests/vectors/rank_sweep.csv."""

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

    assert len(rows) == NATURAL_RANK_COUNT
    assert ranks_in_hand_order == list(range(1, NATURAL_RANK_COUNT + 1))
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
    """One row of tests/vectors/joker_vectors.csv."""

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
    joker_ranks = tuple(card.rank.name for card in evaluated.jokers_played_as)
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
