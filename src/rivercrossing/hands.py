# SPDX-License-Identifier: GPL-3.0-only
"""Best-of-5 poker hand evaluation, natural and wild (spec section 5).

``eval5`` wraps phevaluator's natural-card evaluator -- spec section 5's
algorithm sketch names it explicitly -- with a table-driven mapping from
its 7,462 distinct ranks onto this project's local :class:`HandClass`
ordering, a Royal-Flush-above-Straight-Flush split that phevaluator
itself does not make, and (E2.1.2) a five-of-a-kind check ranked above
every natural hand: jokers are wild and always resolve to whichever
natural completion maximizes the hand (spec section 5's ``best_hand``
pseudocode, restricted here to exactly 5 cards -- best-of-N is E2.1.3).
"""

import bisect
import itertools
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from phevaluator.evaluator import evaluate_cards

from rivercrossing.cards import Card, Rank, Suit

if TYPE_CHECKING:
    from collections.abc import Sequence

NATURAL_HAND_SIZE = 5
# spec section 5: "the 7,462 distinct natural ranks are stored as one
# integer per entry".
NATURAL_RANK_COUNT = 7462


class HandClass(IntEnum):
    """Hand categories, worst to best (spec section 5 table, reversed).

    Values increase with strength, so a plain ``IntEnum`` comparison
    already orders hand classes correctly. ``FIVE_OF_A_KIND`` needs at
    least one joker (E2.1.2); :func:`eval5` never produces it from 5
    natural cards.
    """

    HIGH_CARD = 1
    PAIR = 2
    TWO_PAIR = 3
    TRIPS = 4
    STRAIGHT = 5
    FLUSH = 6
    FULL_HOUSE = 7
    QUADS = 8
    STRAIGHT_FLUSH = 9
    ROYAL_FLUSH = 10
    FIVE_OF_A_KIND = 11


@dataclass(frozen=True)
class EvaluatedHand:
    """One evaluated hand; ``(cls, tiebreak)`` totally orders hands.

    For every class but ``FIVE_OF_A_KIND``, ``tiebreak`` inverts
    phevaluator's rank so plain tuple comparison of ``(cls,
    tiebreak)`` already puts the better hand higher: phevaluator
    numbers hands 1 (best) through 7,462 (worst), the opposite of this
    project's "higher sorts better" convention, so this stores
    ``NATURAL_RANK_COUNT + 1 - natural_rank`` instead of the raw
    phevaluator number. ``FIVE_OF_A_KIND`` has no phevaluator rank at
    all (a real deck has no fifth copy of a card); its tiebreak is
    simply the quint's own :class:`~rivercrossing.cards.Rank` value
    (2-14) -- a different, smaller scale that never needs to compare
    against another class's tiebreak, since ``cls`` alone already
    separates it from everything else.
    """

    cls: HandClass
    tiebreak: tuple[int, ...]
    best5: tuple[Card, ...]
    jokers_played_as: tuple[Card, ...]


class InvalidHandError(ValueError):
    """Raised when :func:`eval5` gets anything but exactly 5 cards."""


# phevaluator/treys' published rank partitioning (verified empirically
# against representative hands of every class -- see test_hands.py):
# each entry's rank is <= its upper bound and > the previous one's.
_UPPER_BOUNDS = (1, 10, 166, 322, 1599, 1609, 2467, 3325, 6185, 7462)
_CLASS_BY_UPPER_BOUND = (
    HandClass.ROYAL_FLUSH,
    HandClass.STRAIGHT_FLUSH,
    HandClass.QUADS,
    HandClass.FULL_HOUSE,
    HandClass.FLUSH,
    HandClass.STRAIGHT,
    HandClass.TRIPS,
    HandClass.TWO_PAIR,
    HandClass.PAIR,
    HandClass.HIGH_CARD,
)

# Rank-descending, then Suit's own declaration order within a rank --
# the fixed candidate order the wild search below walks. Combined with
# `combinations_with_replacement`'s index-order guarantee, this is also
# what makes a multi-joker resolution's *order* deterministic: the
# higher-ranked (then lower-suit-index) card always comes first.
_ALL_NATURAL_CARDS: tuple[Card, ...] = tuple(
    Card(rank=rank, suit=suit) for rank in sorted(Rank, reverse=True) for suit in Suit
)


def _classify(natural_rank: int) -> HandClass:
    """Look up *natural_rank*'s :class:`HandClass` via the table."""
    index = bisect.bisect_left(_UPPER_BOUNDS, natural_rank)
    return _CLASS_BY_UPPER_BOUND[index]


def _evaluate_natural_five(cards: Sequence[Card]) -> EvaluatedHand:
    """Evaluate 5 natural (joker-free) cards via phevaluator.

    Callers must ensure the 5 codes are pairwise distinct: phevaluator's
    native evaluator is undefined -- observed to segfault -- on a
    repeated card id, and no legal *natural* hand has one. The wild
    search below filters candidates before ever reaching this
    function; natural (0-joker) callers only ever pass a real 5-card
    hand, whose codes are already distinct by construction.
    """
    natural_rank: int = evaluate_cards(*(card.code() for card in cards))
    tiebreak = (NATURAL_RANK_COUNT + 1 - natural_rank,)
    return EvaluatedHand(
        cls=_classify(natural_rank),
        tiebreak=tiebreak,
        best5=tuple(cards),
        jokers_played_as=(),
    )


def _five_of_a_kind_rank(naturals: Sequence[Card]) -> Rank | None:
    """Return the rank a wild expansion could reach five-of-a-kind at.

    Reachable exactly when every natural card already shares one rank
    (trivially true with 0 or 1 naturals): the jokers can always
    duplicate a suit to fill the rest (duplicate-legal, spec section 4's
    multi-deck shoe). With no naturals at all, Ace is the unconstrained
    best choice -- spec section 5's ``j >= 5 -> FIVE_OF_KIND(Ace)``
    shortcut falls out of this same rule with no extra branch.
    """
    ranks = {card.rank for card in naturals}
    if len(ranks) > 1:
        return None
    return next(iter(ranks)) if ranks else Rank.ACE


def _cycle_suits(count: int) -> tuple[Suit, ...]:
    """Pick *count* suits, cycling Suit's declaration order.

    Only used where a joker's exact suit is display-only and can never
    change the hand's class or tiebreak (five-of-a-kind: any suit
    reaches the same quint). Duplicates are legal (R1 of the joker
    vector table).
    """
    suits = tuple(Suit)
    return tuple(suits[index % len(suits)] for index in range(count))


def _five_of_a_kind_hand(cards: Sequence[Card], rank: Rank, joker_count: int) -> EvaluatedHand:
    """Build the FIVE_OF_A_KIND result for *rank*, filled by suit."""
    jokers_played_as = tuple(Card(rank=rank, suit=suit) for suit in _cycle_suits(joker_count))
    return EvaluatedHand(
        cls=HandClass.FIVE_OF_A_KIND,
        tiebreak=(rank.value,),
        best5=tuple(cards),
        jokers_played_as=jokers_played_as,
    )


def _is_duplicate_free(cards: Sequence[Card]) -> bool:
    """Report whether *cards* are 5 pairwise-distinct codes."""
    return len({card.code() for card in cards}) == NATURAL_HAND_SIZE


_NaturalScore = tuple[HandClass, tuple[int, ...]]


def _natural_score(naturals: Sequence[Card], fill: Sequence[Card]) -> _NaturalScore:
    """Score one candidate wild fill by its class and tiebreak."""
    evaluated = _evaluate_natural_five((*naturals, *fill))
    return (evaluated.cls, evaluated.tiebreak)


def _best_wild_fill(naturals: Sequence[Card], joker_count: int) -> tuple[Card, ...]:
    """Search every distinct way to fill *joker_count* wild slots.

    Candidates are the full 52-card deck (spec section 5 explicitly
    allows this: "52 also fine" -- pruning to ranks-in-hand,
    straight-completers, suits-present and aces is the alternative it
    names, not a requirement). A combination containing a repeated
    card is skipped: it is never optimal here (this function only
    runs once :func:`_five_of_a_kind_rank` has ruled out
    five-of-a-kind, the one class where an exact duplicate helps) and
    passing one to phevaluator is unsafe (see
    :func:`_evaluate_natural_five`).

    This function is only ever called with 1-3 jokers: 4 jokers means
    exactly 1 natural card, always rank-homogeneous with itself, so
    :func:`_five_of_a_kind_rank` always short-circuits first. Worst
    case is 3 jokers, C(54, 3) = 24,804 native evaluator calls --
    comfortably fast.
    """
    candidates = itertools.combinations_with_replacement(_ALL_NATURAL_CARDS, joker_count)
    valid_fills = [fill for fill in candidates if _is_duplicate_free((*naturals, *fill))]
    return max(valid_fills, key=lambda fill: _natural_score(naturals, fill))


def _evaluate_with_wild_cards(
    cards: Sequence[Card], naturals: Sequence[Card], joker_count: int
) -> EvaluatedHand:
    """Resolve 1+ jokers to whichever completion maximizes the hand."""
    five_kind_rank = _five_of_a_kind_rank(naturals)
    if five_kind_rank is not None:
        return _five_of_a_kind_hand(cards, five_kind_rank, joker_count)
    fill = _best_wild_fill(naturals, joker_count)
    natural_result = _evaluate_natural_five((*naturals, *fill))
    return EvaluatedHand(
        cls=natural_result.cls,
        tiebreak=natural_result.tiebreak,
        best5=tuple(cards),
        jokers_played_as=fill,
    )


def _require_five_cards(cards: Sequence[Card]) -> None:
    """Raise InvalidHandError unless *cards* has exactly 5 entries."""
    if len(cards) != NATURAL_HAND_SIZE:
        msg = f"eval5 requires exactly {NATURAL_HAND_SIZE} cards, got {len(cards)}"
        raise InvalidHandError(msg)


def eval5(cards: Sequence[Card]) -> EvaluatedHand:
    """Evaluate exactly 5 cards, natural or wild, into an EvaluatedHand.

    Args:
        cards: Exactly 5 cards; any number of them may be jokers.

    Returns:
        The evaluated hand: its class, tiebreak score, the original 5
        cards (jokers included, for "played as a card" display), and
        each joker's maximizing resolution in ``jokers_played_as``
        (empty for an all-natural hand).

    Raises:
        InvalidHandError: *cards* is not exactly 5 cards.
    """
    _require_five_cards(cards)
    naturals = [card for card in cards if not card.joker]
    joker_count = len(cards) - len(naturals)
    if joker_count == 0:
        return _evaluate_natural_five(cards)
    return _evaluate_with_wild_cards(cards, naturals, joker_count)


def compare(a: EvaluatedHand, b: EvaluatedHand) -> int:
    """Compare two evaluated hands by ``(cls, tiebreak)``.

    Returns:
        -1 if *a* is the worse hand, 1 if *a* is the better hand, or
        0 if they tie exactly.
    """
    key_a = (a.cls, a.tiebreak)
    key_b = (b.cls, b.tiebreak)
    if key_a < key_b:
        return -1
    if key_a > key_b:
        return 1
    return 0
