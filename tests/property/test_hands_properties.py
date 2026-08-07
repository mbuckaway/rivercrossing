# SPDX-License-Identifier: GPL-3.0-only
"""Hypothesis property suite for the finished evaluator (E2.1.4).

The brief for this task *is* the four property groups below -- no new
production code is expected, only fuzzing of ``eval5``/``best_hand``
already built in E2.1.1-E2.1.3. Every card strategy here draws
multi-deck style: naturals and jokers with replacement, so a hand may
legally repeat a code exactly as a real multi-deck shoe can deal one
(module docstring of ``rivercrossing.hands``).

``_bounded_n_card_hand`` caps its joker count the same way
``tests/unit/test_hands.py``'s own ``_hand_with_bounded_jokers``
already does: unbounded joker density makes ``best_hand``'s wild
search combinatorially expensive (``_search_best_fill``'s docstring),
so this keeps every property example fast without narrowing which
code paths get exercised -- the 5-card strategies below stay
unbounded, since a 5-card wild search is cheap regardless of joker
count (at most 5 cards are ever in play).
"""

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from rivercrossing.cards import Card, Rank, Suit
from rivercrossing.hands import EvaluatedHand, best_hand, compare, eval5

_RANKS: tuple[Rank, ...] = tuple(Rank)
_SUITS: tuple[Suit, ...] = tuple(Suit)

_NATURAL_CARD = st.builds(Card, rank=st.sampled_from(_RANKS), suit=st.sampled_from(_SUITS))
_JOKER_CARD = st.just(Card(rank=None, suit=None, joker=True))
_ANY_CARD = _NATURAL_CARD | _JOKER_CARD

_FIVE_CARD_HANDS = st.lists(_ANY_CARD, min_size=5, max_size=5)


@st.composite
def _bounded_n_card_hand(draw: st.DrawFn) -> list[Card]:
    """Draw a 0..12 card hand, joker count capped at 2 (see above)."""
    naturals = draw(st.lists(_NATURAL_CARD, min_size=0, max_size=10))
    joker_count = draw(st.integers(min_value=0, max_value=2))
    jokers = [Card(rank=None, suit=None, joker=True) for _ in range(joker_count)]
    return [*naturals, *jokers]


_N_CARD_HANDS = _bounded_n_card_hand()


def _evaluate_five(cards: list[Card]) -> EvaluatedHand:
    """Evaluate a fixed 5-card hand via ``eval5``."""
    return eval5(cards)


_EVALUATED_HANDS = _FIVE_CARD_HANDS.map(_evaluate_five)


def _natural_indices(cards: list[Card]) -> list[int]:
    """List every index in *cards* holding a natural, non-joker card."""
    return [index for index, card in enumerate(cards) if not card.joker]


def _wildcard_at(cards: list[Card], index: int) -> list[Card]:
    """Return *cards* with the card at *index* replaced by a joker."""
    return [*cards[:index], Card(rank=None, suit=None, joker=True), *cards[index + 1 :]]


# ------------------------------------------ rank total-order properties


@given(hand=_EVALUATED_HANDS)
@settings(max_examples=2000, deadline=None)
def test_compare_reflexive_for_any_evaluated_hand(hand: EvaluatedHand) -> None:
    """Every evaluated hand compares equal to itself."""
    assert compare(hand, hand) == 0


@given(first=_EVALUATED_HANDS, second=_EVALUATED_HANDS)
@settings(max_examples=1800, deadline=None)
def test_compare_antisymmetric_for_any_two_evaluated_hands(
    first: EvaluatedHand, second: EvaluatedHand
) -> None:
    """Swapping compare's two arguments negates its result."""
    assert compare(first, second) == -compare(second, first)


@given(first=_EVALUATED_HANDS, second=_EVALUATED_HANDS, third=_EVALUATED_HANDS)
@settings(max_examples=450, deadline=None)
def test_compare_transitive_across_three_evaluated_hands(
    first: EvaluatedHand, second: EvaluatedHand, third: EvaluatedHand
) -> None:
    """a<=b and b<=c together imply a<=c, for compare's own order."""
    assume(compare(first, second) <= 0)
    assume(compare(second, third) <= 0)

    assert compare(first, third) <= 0


# ----------------------------------------------- permutation invariance


@given(cards=_N_CARD_HANDS, data=st.data())
@settings(max_examples=500, deadline=None)
def test_best_hand_permutation_invariant_for_any_shuffle(
    cards: list[Card], data: st.DataObject
) -> None:
    """Shuffling one card multiset never changes best_hand's result."""
    shuffled = data.draw(st.permutations(cards))
    original = best_hand(cards)

    reshuffled = best_hand(shuffled)

    assert (reshuffled.cls, reshuffled.tiebreak) == (original.cls, original.tiebreak)


@given(cards=_FIVE_CARD_HANDS, data=st.data())
@settings(max_examples=1500, deadline=None)
def test_eval5_permutation_invariant_for_any_shuffle_of_five_cards(
    cards: list[Card], data: st.DataObject
) -> None:
    """Shuffling the same 5 cards never changes eval5's result."""
    shuffled = data.draw(st.permutations(cards))
    original = eval5(cards)

    reshuffled = eval5(shuffled)

    assert (reshuffled.cls, reshuffled.tiebreak) == (original.cls, original.tiebreak)


# --------------------------------------------- joker-count monotonicity


@given(cards=_FIVE_CARD_HANDS, data=st.data())
@settings(max_examples=1000, deadline=None)
def test_eval5_joker_replacement_never_lowers_the_hand(
    cards: list[Card], data: st.DataObject
) -> None:
    """Wilding one natural card of a 5-card hand never lowers eval5.

    A wild can always play as the card it replaced, so the best
    achievable hand can only stay the same or improve.
    """
    natural_indices = _natural_indices(cards)
    assume(natural_indices)
    index = data.draw(st.sampled_from(natural_indices))
    wildcarded = _wildcard_at(cards, index)

    before = eval5(cards)
    after = eval5(wildcarded)

    assert compare(after, before) >= 0


@given(cards=_N_CARD_HANDS, data=st.data())
@settings(max_examples=500, deadline=None)
def test_best_hand_joker_replacement_never_lowers_the_hand(
    cards: list[Card], data: st.DataObject
) -> None:
    """Wilding one natural card among N cards never lowers best_hand."""
    natural_indices = _natural_indices(cards)
    assume(natural_indices)
    index = data.draw(st.sampled_from(natural_indices))
    wildcarded = _wildcard_at(cards, index)

    before = best_hand(cards)
    after = best_hand(wildcarded)

    assert compare(after, before) >= 0


# --------------------------------------------- serialization round trip


@given(card=_ANY_CARD)
@settings(deadline=None)
def test_card_round_trips_through_code_and_parse(card: Card) -> None:
    """Card.parse(card.code()) reconstructs an equal card, joker too.

    The domain is exactly the 52 naturals plus the joker, so
    Hypothesis exhausts it well before the default example budget.
    """
    assert Card.parse(card.code()) == card


@given(cards=_N_CARD_HANDS)
@settings(max_examples=1200, deadline=None)
def test_best_hand_best5_and_jokers_round_trip_preserving_evaluation(cards: list[Card]) -> None:
    """A hand's best5/jokers_played_as survive a code()/parse() trip.

    Re-parsing every code must reconstruct the exact same cards, and
    re-evaluating the round-tripped best5 must reproduce the same
    (cls, tiebreak) -- the codec never silently corrupts a card the
    evaluator already chose.
    """
    original = best_hand(cards)
    roundtripped_best5 = [Card.parse(card.code()) for card in original.best5]
    roundtripped_jokers = [Card.parse(card.code()) for card in original.jokers_played_as]

    reevaluated = best_hand(roundtripped_best5)

    assert tuple(roundtripped_best5) == original.best5
    assert tuple(roundtripped_jokers) == original.jokers_played_as
    assert (reevaluated.cls, reevaluated.tiebreak) == (original.cls, original.tiebreak)
