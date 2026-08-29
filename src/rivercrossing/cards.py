# SPDX-License-Identifier: GPL-3.0-only
"""Card model and seeded shoe: suits, ranks, jokers, and Shoe (§4).

module-skeletons.md S4 places the multi-deck shoe (``Shoe``) in this
same module, alongside the immutable card vocabulary the hand
evaluator (``rivercrossing.hands``) also builds on.
"""

import random
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


class ShoeEmpty(Exception):  # noqa: N818 -- frozen name, module-skeletons.md S4
    """Raised by Shoe.deal() once the cycle has no cards left.

    The shoe never reshuffles itself (spec section 4): the caller
    catches this and calls Shoe.reshuffle(), which is also where
    the caller writes the reshuffle's own audit entry.
    """


class ShoeClosedError(Exception):
    """Raised by any Shoe mutation once Shoe.close() has run.

    Ride Finish closes the shoe (task-briefs.md E2.2.1's own negative
    case, "deal after close raises"); nothing may deal, reshuffle or
    undo against it while it stays closed. Ride Reopen calls
    :meth:`Shoe.reopen` to open it again for corrections (spec §15).
    """


class RestitutionError(Exception):
    """Raised by Shoe.restitute() when *card* was not the last deal.

    Ctrl+Z undo can only ever return the single most recently
    dealt card -- anything else would silently rewrite history.
    """


def _fresh_deck(jokers_per_deck: int) -> list[Card]:
    """Build one unshuffled deck: the 52 naturals plus its jokers."""
    naturals = [Card(rank=rank, suit=suit) for rank in Rank for suit in Suit]
    jokers = [Card(rank=None, suit=None, joker=True) for _ in range(jokers_per_deck)]
    return naturals + jokers


def _shuffled_sequence(decks: int, jokers_per_deck: int, seed: int) -> list[Card]:
    """Fisher-Yates shuffle *decks* fresh decks under *seed* (§4).

    ``random.Random(seed).shuffle`` is CPython's Fisher-Yates and
    is deterministic for a given seed -- R-40's "replaying the
    seed reproduces every card" guarantee rides on that.
    """
    cards = [card for _ in range(decks) for card in _fresh_deck(jokers_per_deck)]
    random.Random(seed).shuffle(cards)  # noqa: S311 -- deterministic Fisher-Yates by design, not crypto
    return cards


class Shoe:
    """A seeded, auditable multi-deck shoe (spec section 4).

    ``decks`` copies of a standard 52-card deck, plus
    ``jokers_per_deck`` jokers each, are Fisher-Yates shuffled
    under ``seed``; :meth:`deal` hands out ``shoe[deal_index]`` in
    that fixed order, so the whole sequence -- and therefore every
    card any entry ever received -- is recoverable from just the
    stored config and seed (R-40).
    """

    def __init__(self, decks: int, jokers_per_deck: int, seed: int) -> None:
        """Build and shuffle cycle 1 from decks/jokers_per_deck/seed."""
        self._decks = decks
        self._jokers_per_deck = jokers_per_deck
        self._seed = seed
        self._cycle = 1
        self._cards = _shuffled_sequence(decks, jokers_per_deck, seed)
        self._dealt = 0
        self._closed = False

    @property
    def remaining(self) -> int:
        """Count of undealt cards left in the current cycle."""
        return len(self._cards) - self._dealt

    @property
    def dealt(self) -> int:
        """Count of cards dealt so far in the current cycle."""
        return self._dealt

    @property
    def cycle(self) -> int:
        """The current 1-based shuffle cycle ("Shoe cycle N")."""
        return self._cycle

    @property
    def is_closed(self) -> bool:
        """Whether :meth:`close` has run and :meth:`reopen` has not.

        Read-only: the ride's ``finish``/``reopen`` transitions drive
        this flag (E4.3 closes on Finish, spec §15 re-opens on Reopen);
        the replay-equivalence contract compares it so a replayed
        ``reopen`` reproduces the open/closed state exactly.
        """
        return self._closed

    def _require_open(self) -> None:
        """Raise ShoeClosedError once :meth:`close` has run."""
        if self._closed:
            msg = "shoe is closed"
            raise ShoeClosedError(msg)

    def deal(self) -> tuple[Card, int]:
        """Deal the next card.

        Returns:
            The dealt card and its 0-based deal index.

        Raises:
            ShoeClosedError: the shoe is closed (see :meth:`close`).
            ShoeEmpty: the cycle has no cards left; call
                :meth:`reshuffle` before dealing again.
        """
        self._require_open()
        if self._dealt >= len(self._cards):
            msg = "shoe is empty"
            raise ShoeEmpty(msg)
        deal_index = self._dealt
        card = self._cards[deal_index]
        self._dealt += 1
        return card, deal_index

    def restitute(self, card: Card) -> None:
        """Undo the last deal, returning *card* to the front (Ctrl+Z).

        The next :meth:`deal` call re-deals the same card at the
        same deal index.

        Raises:
            ShoeClosedError: the shoe is closed (see :meth:`close`).
            RestitutionError: *card* was not the last card dealt.
        """
        self._require_open()
        if self._dealt == 0 or self._cards[self._dealt - 1] != card:
            msg = f"{card.code()} was not the last card dealt from this shoe"
            raise RestitutionError(msg)
        self._dealt -= 1

    def reshuffle(self) -> None:
        """Start a new cycle under seed + (cycle - 1) (spec §4).

        Caller-triggered, not automatic: the caller (which just saw
        :meth:`deal` raise :class:`ShoeEmpty`) writes the
        reshuffle's own audit entry.

        Raises:
            ShoeClosedError: the shoe is closed (see :meth:`close`).
        """
        self._require_open()
        self._cycle += 1
        derived_seed = self._seed + (self._cycle - 1)
        self._cards = _shuffled_sequence(self._decks, self._jokers_per_deck, derived_seed)
        self._dealt = 0

    def close(self) -> None:
        """Close the shoe; ride Finish calls this (task-briefs E2.2.1).

        Every later :meth:`deal`, :meth:`reshuffle` or
        :meth:`restitute` call raises :class:`ShoeClosedError` until
        :meth:`reopen` opens it again. The deal position is untouched:
        a reopened shoe continues the exact deal order.
        """
        self._closed = True

    def reopen(self) -> None:
        """Re-open the shoe; ride Reopen calls this (spec §15).

        Clears :meth:`close`'s flag -- nothing else changes: the deal
        position, cycle and remaining sequence are exactly as they were
        when the shoe closed, so the next :meth:`deal` continues the
        deterministic order (R-40). Ride Reopen uses this so the
        corrections commands (``deal_manual``, ``add_crossing_at``)
        can deal new cards in REOPENED.
        """
        self._closed = False

    @staticmethod
    def replay(  # noqa: PLR0913, PLR0917 -- frozen module-skeletons.md S4 API
        decks: int, jokers_per_deck: int, seed: int, deals: int, cycles: int
    ) -> Shoe:
        """Rebuild the exact shoe state after *cycles* and *deals*.

        *cycles* and *deals* mirror a live shoe's own
        ``cycle``/``dealt`` properties at the point being replayed:
        :meth:`reshuffle` runs ``cycles - 1`` times to reach the
        right cycle, then :meth:`deal` runs *deals* times within
        it (R-40). This is how a persisted (config, seed) plus its
        recorded deal/reshuffle events reconstructs a live shoe
        after a restart, without storing the shuffled sequence
        itself.
        """
        shoe = Shoe(decks=decks, jokers_per_deck=jokers_per_deck, seed=seed)
        for _ in range(cycles - 1):
            shoe.reshuffle()
        for _ in range(deals):
            shoe.deal()
        return shoe
