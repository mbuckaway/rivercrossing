# SPDX-License-Identifier: GPL-3.0-only
"""Card model: suits, ranks and the natural-card-or-joker vocabulary.

module-skeletons.md S4 owns the multi-deck shoe (``Shoe``, E2.2.1);
this module holds only the immutable card model the shoe and the
hand evaluator (``rivercrossing.hands``) both build on.
"""

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import cast


class Suit(Enum):
    """The four natural suits; a joker card carries no suit."""

    CLUBS = "C"
    DIAMONDS = "D"
    HEARTS = "H"
    SPADES = "S"


class Rank(IntEnum):
    """A natural card rank, Two low through Ace high."""

    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10
    JACK = 11
    QUEEN = 12
    KING = 13
    ACE = 14


_LETTER_BY_RANK: dict[Rank, str] = {
    Rank.TWO: "2",
    Rank.THREE: "3",
    Rank.FOUR: "4",
    Rank.FIVE: "5",
    Rank.SIX: "6",
    Rank.SEVEN: "7",
    Rank.EIGHT: "8",
    Rank.NINE: "9",
    Rank.TEN: "T",
    Rank.JACK: "J",
    Rank.QUEEN: "Q",
    Rank.KING: "K",
    Rank.ACE: "A",
}
_RANK_BY_LETTER: dict[str, Rank] = {letter: rank for rank, letter in _LETTER_BY_RANK.items()}
_SUIT_BY_LETTER: dict[str, Suit] = {suit.value: suit for suit in Suit}

_JOKER_CODE = "JK"


@dataclass(frozen=True)
class Card:
    """One card: a natural rank+suit pair, or a joker.

    A joker carries ``rank=None, suit=None, joker=True``; every
    natural card sets both ``rank`` and ``suit`` and leaves ``joker``
    at its default of ``False``.
    """

    rank: Rank | None
    suit: Suit | None
    joker: bool = False

    def code(self) -> str:
        """Return this card's stored two-character form.

        Examples: ``"AS"``, ``"TD"``, ``"JK"`` (joker).

        The two ``cast`` calls document, for mypy, the natural-card
        contract stated above -- a joker never reaches them because
        of the guard clause above -- without adding a second runtime
        check on top of the dataclass's own field types.
        """
        if self.joker:
            return _JOKER_CODE
        rank = cast("Rank", self.rank)
        suit = cast("Suit", self.suit)
        return f"{_LETTER_BY_RANK[rank]}{suit.value}"

    @staticmethod
    def parse(code: str) -> Card:
        """Parse a stored two-character code back into a Card."""
        if code == _JOKER_CODE:
            return Card(rank=None, suit=None, joker=True)
        return Card(rank=_RANK_BY_LETTER[code[0]], suit=_SUIT_BY_LETTER[code[1]], joker=False)
