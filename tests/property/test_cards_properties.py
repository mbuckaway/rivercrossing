# SPDX-License-Identifier: GPL-3.0-only
"""Hypothesis property suite for Shoe (E2.2.2).

module-skeletons.md S5 names this directory for shoe determinism
properties. Two properties per the E2.2.2 brief: jokers_per_deck=0
never deals a joker, and the same (decks, jokers_per_deck, seed)
always deals an identical full sequence -- the fuzzed counterpart
to tests/unit/test_cards.py's fixed-example determinism test.

Example counts stay modest: spec section 4's shoe can be large (8
decks x 4 jokers is 432 cards), and dealing every card per example
adds up, so this keeps the whole file well inside a few seconds.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from rivercrossing.cards import Shoe, ShoeEmpty

_DECKS = st.integers(min_value=1, max_value=6)
_JOKERS_PER_DECK = st.sampled_from((0, 2, 4))  # R-13's #setupdlg Cards choices
_SEED = st.integers(min_value=0, max_value=1_000_000)

_JOKER_CODE = "JK"


def _deal_all_codes(shoe: Shoe) -> list[str]:
    """Deal *shoe* to exhaustion, listing each card's code in order."""
    codes: list[str] = []
    while True:
        try:
            card, _ = shoe.deal()
        except ShoeEmpty:
            return codes
        codes.append(card.code())


@given(decks=_DECKS, seed=_SEED)
@settings(max_examples=100, deadline=None)
def test_shoe_zero_jokers_per_deck_never_deals_a_joker(decks: int, seed: int) -> None:
    """jokers_per_deck=0 means no joker ever appears in a full deal."""
    shoe = Shoe(decks=decks, jokers_per_deck=0, seed=seed)

    codes = _deal_all_codes(shoe)

    assert _JOKER_CODE not in codes


@given(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
@settings(max_examples=100, deadline=None)
def test_shoe_same_config_and_seed_always_deals_the_identical_sequence(
    decks: int, jokers_per_deck: int, seed: int
) -> None:
    """The same decks/jokers_per_deck/seed always deals identically."""
    first = Shoe(decks=decks, jokers_per_deck=jokers_per_deck, seed=seed)
    second = Shoe(decks=decks, jokers_per_deck=jokers_per_deck, seed=seed)

    first_sequence = _deal_all_codes(first)
    second_sequence = _deal_all_codes(second)

    assert first_sequence == second_sequence
