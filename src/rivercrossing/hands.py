# SPDX-License-Identifier: GPL-3.0-only
"""Best-of-5 poker hand evaluation over natural cards (spec section 5).

``eval5`` wraps phevaluator's natural-card evaluator -- spec section 5's
algorithm sketch names it explicitly -- with a table-driven mapping from
its 7,462 distinct ranks onto this project's local :class:`HandClass`
ordering, and a matching Royal-Flush-above-Straight-Flush split that
phevaluator itself does not make. The wild-card layer over the top
(jokers, Five of a Kind) is E2.1.2; this module only ever sees natural
cards.
"""

import bisect
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from phevaluator.evaluator import evaluate_cards

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rivercrossing.cards import Card

NATURAL_HAND_SIZE = 5
# spec section 5: "the 7,462 distinct natural ranks are stored as one
# integer per entry".
NATURAL_RANK_COUNT = 7462


class HandClass(IntEnum):
    """Hand categories, worst to best (spec section 5 table, reversed).

    Values increase with strength, so a plain ``IntEnum`` comparison
    already orders hand classes correctly. ``FIVE_OF_A_KIND`` needs a
    wild card and is never produced by :func:`eval5` (E2.1.2).
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

    ``tiebreak`` inverts phevaluator's rank so plain tuple comparison
    of ``(cls, tiebreak)`` already puts the better hand higher:
    phevaluator numbers hands 1 (best) through 7,462 (worst), the
    opposite of this project's "higher sorts better" convention, so
    this stores ``NATURAL_RANK_COUNT + 1 - natural_rank`` instead of
    the raw phevaluator number.
    """

    cls: HandClass
    tiebreak: tuple[int, ...]
    best5: tuple[Card, ...]
    jokers_played_as: tuple[Card, ...]


class InvalidHandError(ValueError):
    """Raised when :func:`eval5` gets anything but 5 natural cards."""


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


def _classify(natural_rank: int) -> HandClass:
    """Look up *natural_rank*'s :class:`HandClass` via the table."""
    index = bisect.bisect_left(_UPPER_BOUNDS, natural_rank)
    return _CLASS_BY_UPPER_BOUND[index]


def _require_natural_cards(cards: Sequence[Card]) -> None:
    """Raise InvalidHandError unless cards is 5 natural cards."""
    if len(cards) != NATURAL_HAND_SIZE:
        msg = f"eval5 requires exactly {NATURAL_HAND_SIZE} cards, got {len(cards)}"
        raise InvalidHandError(msg)
    for card in cards:
        if card.joker:
            msg = f"eval5 does not accept jokers (E2.1.2): got {card.code()}"
            raise InvalidHandError(msg)


def eval5(cards: Sequence[Card]) -> EvaluatedHand:
    """Evaluate exactly 5 natural cards into an :class:`EvaluatedHand`.

    Args:
        cards: Exactly 5 natural (non-joker) cards.

    Returns:
        The evaluated hand: its class, tiebreak score, the 5 cards
        making it up, and an empty ``jokers_played_as`` (natural
        hands play no wild cards).

    Raises:
        InvalidHandError: *cards* is not exactly 5 natural cards.
    """
    _require_natural_cards(cards)
    natural_rank: int = evaluate_cards(*(card.code() for card in cards))
    tiebreak = (NATURAL_RANK_COUNT + 1 - natural_rank,)
    return EvaluatedHand(
        cls=_classify(natural_rank),
        tiebreak=tiebreak,
        best5=tuple(cards),
        jokers_played_as=(),
    )


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
