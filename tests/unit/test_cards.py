# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.cards's Shoe (E2.2.1, E2.2.2).

Spec section 4 is this task's specification: a shoe is
``decks * (52 + jokers_per_deck)`` cards, Fisher-Yates shuffled
under a stored seed, dealt as ``shoe[deal_index++]``; the whole
sequence is "deterministic and auditable" -- replaying the seed
reproduces every card (R-40) -- and an empty shoe reshuffles under
``seed + (cycle - 1)`` with an audit entry the *caller* writes
(module-skeletons.md S4: reshuffle is caller-triggered, not
automatic).

Seed 8843 throughout is the exact worked example on the main
frame's own status bar ("Shoe cycle 1 - seed 8843",
xrc-windows.md section A), and 2 decks / 2 jokers-per-deck / 108
total cards is task-briefs.md's own E2.2.1 worked example.

E2.2.2's composition-count matrix is the parametrized test at the
bottom of this file, over every decks/jokers_per_deck combination
the #setupdlg Cards fieldset offers (jokers_per_deck in {0, 2, 4}
per R-13; decks_spin is a free spinner, xrc-windows.md section B).
Its Hypothesis properties live in
tests/property/test_cards_properties.py instead (module-skeletons
S5 names that directory for shoe determinism properties).
"""

import re
from collections import Counter
from typing import TYPE_CHECKING

import pytest

from rivercrossing.cards import (
    Card,
    Rank,
    RestitutionError,
    Shoe,
    ShoeClosedError,
    ShoeEmpty,
    Suit,
    seeded_card_codes,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_SEED = 8843
_DECKS = 2
_JOKERS_PER_DECK = 2
_SHOE_TOTAL = _DECKS * (52 + _JOKERS_PER_DECK)  # 108, task-briefs.md's own number


def _codes(cards: Sequence[Card]) -> list[str]:
    """List each card's stored two-character code, in order."""
    return [card.code() for card in cards]


def _deal_all(shoe: Shoe) -> list[Card]:
    """Deal every remaining card of *shoe*'s current cycle, in order."""
    dealt: list[Card] = []
    while True:
        try:
            card, _ = shoe.deal()
        except ShoeEmpty:
            return dealt
        dealt.append(card)


def _composition(
    cards: Sequence[Card],
) -> tuple[Counter[tuple[Rank | None, Suit | None]], int]:
    """Split *cards* into a natural rank/suit count map, joker count."""
    naturals = Counter((card.rank, card.suit) for card in cards if not card.joker)
    joker_count = sum(1 for card in cards if card.joker)
    return naturals, joker_count


# ------------------------------------------------------- determinism


def test_shoe_deal_sequence_same_config_and_seed_matches_across_instances() -> None:
    """Two shoes from the same decks/jokers/seed deal identically."""
    first = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    second = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)

    first_sequence = _codes(_deal_all(first))
    second_sequence = _codes(_deal_all(second))

    assert first_sequence == second_sequence


def test_shoe_deal_sequence_different_seed_differs_from_original() -> None:
    """A different seed (same config) deals a different full sequence.

    Seeds 8843/8844 are pinned as a known-differing pair (confirmed
    against CPython's random.Random(seed).shuffle before writing
    this test) rather than trusted to differ "astronomically".
    """
    original = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    other_seed = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED + 1)

    original_sequence = _codes(_deal_all(original))
    other_sequence = _codes(_deal_all(other_seed))

    assert original_sequence != other_sequence


# ------------------------------------ deal_index/dealt/remaining/cycle


def test_shoe_deal_returns_deal_index_starting_at_zero_and_incrementing() -> None:
    """deal()'s returned index starts at 0 and increments by 1."""
    shoe = Shoe(decks=1, jokers_per_deck=0, seed=_SEED)

    _, first_index = shoe.deal()
    _, second_index = shoe.deal()
    _, third_index = shoe.deal()

    assert (first_index, second_index, third_index) == (0, 1, 2)


def test_shoe_deal_updates_dealt_and_remaining_counts() -> None:
    """dealt/remaining track how many cards have left the shoe."""
    shoe = Shoe(decks=1, jokers_per_deck=0, seed=_SEED)
    before = (shoe.dealt, shoe.remaining)

    shoe.deal()
    shoe.deal()

    after = (shoe.dealt, shoe.remaining)
    assert (before, after) == ((0, 52), (2, 50))


def test_shoe_cycle_starts_at_one_for_a_freshly_built_shoe() -> None:
    """A brand-new shoe reports cycle 1 (status bar: "Shoe cycle 1")."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)

    assert shoe.cycle == 1


# ---------------------------------------------- exhaustion + reshuffle


def test_shoe_deal_raises_shoe_empty_once_every_card_is_dealt() -> None:
    """Dealing past the last of 108 cards raises ShoeEmpty."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    _deal_all(shoe)

    with pytest.raises(ShoeEmpty, match=re.escape("shoe is empty")):
        shoe.deal()


def test_shoe_reshuffle_after_exhaustion_starts_cycle_two_at_full_count() -> None:
    """reshuffle() after exhaustion advances to cycle 2, full again."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    _deal_all(shoe)

    shoe.reshuffle()

    assert (shoe.cycle, shoe.remaining, shoe.dealt) == (2, _SHOE_TOTAL, 0)


def test_shoe_reshuffle_deals_the_seed_plus_one_fresh_shuffle() -> None:
    """Cycle 2's sequence equals a fresh shoe built with seed + 1.

    Pinned derivation: cycle n uses seed + (n - 1), so cycle 2 is
    exactly seed + 1 -- not some other offset.
    """
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    _deal_all(shoe)
    shoe.reshuffle()

    reshuffled_sequence = _codes(_deal_all(shoe))

    derived = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED + 1)
    derived_sequence = _codes(_deal_all(derived))
    assert reshuffled_sequence == derived_sequence


# --------------------------------------------------------- composition


def test_shoe_composition_holds_decks_copies_of_every_natural_and_all_jokers() -> None:
    """A fresh shoe's full deal is decks copies of naturals + jokers."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)

    naturals, joker_count = _composition(_deal_all(shoe))

    assert joker_count == _DECKS * _JOKERS_PER_DECK
    assert len(naturals) == len(Rank) * len(Suit)
    assert set(naturals.values()) == {_DECKS}


# --------------------------------------------------------------- replay


def test_shoe_replay_zero_deals_matches_a_freshly_built_shoe() -> None:
    """replay(deals=0, cycles=1) is indistinguishable from Shoe(...)."""
    replayed = Shoe.replay(
        decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED, deals=0, cycles=1
    )
    fresh = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)

    state = (replayed.dealt, replayed.remaining, replayed.cycle)
    assert state == (0, _SHOE_TOTAL, 1)
    assert _codes(_deal_all(replayed)) == _codes(_deal_all(fresh))


def test_shoe_replay_mid_cycle_matches_a_live_shoes_remaining_deals() -> None:
    """replay(deals=k, cycles=1) reproduces a live shoe's k deals."""
    live = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    for _ in range(5):
        live.deal()

    replayed = Shoe.replay(
        decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED, deals=5, cycles=1
    )

    state = (replayed.dealt, replayed.remaining, replayed.cycle)
    assert state == (live.dealt, live.remaining, live.cycle)
    assert _codes(_deal_all(replayed)) == _codes(_deal_all(live))


def test_shoe_replay_after_a_reshuffle_matches_the_live_shoes_new_cycle() -> None:
    """replay(deals=k, cycles=2) reproduces a shoe past a reshuffle."""
    live = Shoe(decks=1, jokers_per_deck=0, seed=200)
    _deal_all(live)
    live.reshuffle()
    for _ in range(10):
        live.deal()

    replayed = Shoe.replay(decks=1, jokers_per_deck=0, seed=200, deals=10, cycles=2)

    state = (replayed.dealt, replayed.remaining, replayed.cycle)
    assert state == (live.dealt, live.remaining, live.cycle)
    assert _codes(_deal_all(replayed)) == _codes(_deal_all(live))


# ---------------------------------------------------- undo restitution


def test_shoe_restitute_returns_the_last_card_for_an_identical_redeal() -> None:
    """restitute() undoes the last deal; deal() repeats it exactly."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    dealt_card, dealt_index = shoe.deal()

    shoe.restitute(dealt_card)
    redealt_card, redealt_index = shoe.deal()

    assert (redealt_card, redealt_index) == (dealt_card, dealt_index)


def test_shoe_restitute_adjusts_dealt_and_remaining_back_by_one() -> None:
    """restitute() reverses deal()'s own dealt/remaining bookkeeping."""
    shoe = Shoe(decks=1, jokers_per_deck=0, seed=_SEED)
    dealt_card, _ = shoe.deal()
    after_deal = (shoe.dealt, shoe.remaining)

    shoe.restitute(dealt_card)

    assert after_deal == (1, 51)
    assert (shoe.dealt, shoe.remaining) == (0, 52)


def test_shoe_restitute_wrong_card_raises_restitution_error() -> None:
    """Restituting a card that was not the last deal raises."""
    shoe = Shoe(decks=1, jokers_per_deck=0, seed=_SEED)
    dealt_card, _ = shoe.deal()
    not_last_dealt = next(
        card
        for card in (Card(rank=rank, suit=suit) for rank in Rank for suit in Suit)
        if card != dealt_card
    )

    with pytest.raises(RestitutionError, match=re.escape(not_last_dealt.code())):
        shoe.restitute(not_last_dealt)


def test_shoe_restitute_with_nothing_dealt_yet_raises_restitution_error() -> None:
    """Restituting before any deal() has happened raises, no crash."""
    shoe = Shoe(decks=1, jokers_per_deck=0, seed=_SEED)
    never_dealt = Card(rank=Rank.ACE, suit=Suit.SPADES)

    with pytest.raises(RestitutionError, match=re.escape(never_dealt.code())):
        shoe.restitute(never_dealt)


# -------------------------------------------------------------- close


def test_shoe_deal_after_close_raises_shoe_closed_error_not_shoe_empty() -> None:
    """A closed shoe's deal() raises a type distinct from ShoeEmpty."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    shoe.close()

    with pytest.raises(ShoeClosedError, match=re.escape("shoe is closed")):
        shoe.deal()


def test_shoe_reshuffle_after_close_raises_shoe_closed_error() -> None:
    """A closed shoe's reshuffle() also raises ShoeClosedError."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    shoe.close()

    with pytest.raises(ShoeClosedError, match=re.escape("shoe is closed")):
        shoe.reshuffle()


def test_shoe_restitute_after_close_raises_shoe_closed_error() -> None:
    """A closed shoe's restitute() also raises ShoeClosedError.

    Ride Finish closes the shoe (module-skeletons.md S4); nothing,
    not even an in-flight undo, may still mutate it afterwards.
    """
    shoe = Shoe(decks=1, jokers_per_deck=0, seed=_SEED)
    dealt_card, _ = shoe.deal()
    shoe.close()

    with pytest.raises(ShoeClosedError, match=re.escape("shoe is closed")):
        shoe.restitute(dealt_card)


# ---------------------------------------------------------- reopen


def test_shoe_is_closed_is_false_for_a_freshly_built_shoe() -> None:
    """A fresh shoe is open: is_closed reads False."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)

    assert shoe.is_closed is False


def test_shoe_is_closed_is_true_after_close() -> None:
    """close() flips is_closed to True."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)

    shoe.close()

    assert shoe.is_closed is True


def test_shoe_reopen_after_close_opens_the_shoe_for_dealing() -> None:
    """reopen() after close() unblocks deal()/reshuffle()/restitute().

    Ride Reopen re-opens the shoe (task-briefs E7.1.1: corrections
    deal new cards in REOPENED), so the closed state is not sticky --
    finish closes, reopen opens again.
    """
    shoe = Shoe(decks=1, jokers_per_deck=0, seed=_SEED)
    shoe.close()
    shoe.reopen()

    assert shoe.is_closed is False
    assert shoe.deal()[0] == Shoe(decks=1, jokers_per_deck=0, seed=_SEED).deal()[0]


def test_shoe_reopen_continues_the_exact_deal_order() -> None:
    """reopen() never resets the deal order: the next card continues.

    Reopen only clears the closed flag -- the deal position, cycle
    and sequence stay untouched -- so a reopened shoe deals exactly
    the card a never-closed shoe would deal next (deterministic
    continuation, R-40).
    """
    shoe = Shoe(decks=1, jokers_per_deck=0, seed=_SEED)
    for _ in range(3):
        shoe.deal()
    reference = Shoe(decks=1, jokers_per_deck=0, seed=_SEED)
    for _ in range(3):
        reference.deal()

    shoe.close()
    shoe.reopen()
    reopened_card = shoe.deal()[0]
    never_closed_card = reference.deal()[0]

    assert reopened_card == never_closed_card
    assert shoe.dealt == 4


# ----------------------------------- E2.2.2 composition-count matrix


@pytest.mark.parametrize(
    ("decks", "jokers_per_deck"),
    [
        (1, 0),
        (1, 2),
        (1, 4),
        (2, 0),
        (2, 2),
        (2, 4),
        (4, 0),
        (4, 2),
        (4, 4),
        (8, 0),
        (8, 2),
        (8, 4),
    ],
)
def test_shoe_composition_matches_every_ride_setup_cards_config(
    decks: int, jokers_per_deck: int
) -> None:
    """Every #setupdlg Cards config (R-13) deals the expected counts."""
    shoe = Shoe(decks=decks, jokers_per_deck=jokers_per_deck, seed=_SEED)

    dealt = _deal_all(shoe)
    naturals, joker_count = _composition(dealt)

    assert len(dealt) == decks * (52 + jokers_per_deck)
    assert joker_count == decks * jokers_per_deck
    assert set(naturals.values()) == {decks}


# -------------------------------------------------- seeded_card_codes
# (Phase 4 team-logos: the deterministic natural-card sequence a
# Roster draws a team's logo card from, spec-independent -- every
# team logo is one natural card code, never a joker, and no two
# auto-assigned logos share.)


def test_seeded_card_codes_returns_every_natural_card_once_in_seed_order() -> None:
    """One deck's shuffle, jokers excluded -- 52 unique codes."""
    codes = seeded_card_codes(_SEED)

    assert len(codes) == 52
    assert len(set(codes)) == 52
    assert "JK" not in codes


def test_seeded_card_codes_multi_deck_is_deduped_across_decks() -> None:
    """More decks reshuffle the same 52 naturals -- never duplicates."""
    codes = seeded_card_codes(_SEED, decks=8)

    assert len(codes) == 52
    assert len(set(codes)) == 52
    assert "JK" not in codes


def test_seeded_card_codes_is_deterministic_for_a_given_seed() -> None:
    """R-40's replay discipline: the seed reproduces the sequence."""
    assert seeded_card_codes(_SEED, decks=2) == seeded_card_codes(_SEED, decks=2)


def test_seeded_card_codes_different_seeds_shuffle_differently() -> None:
    """Two seeds agree on the code set but not on its order."""
    a = seeded_card_codes(8843, decks=1)
    b = seeded_card_codes(8844, decks=1)

    assert set(a) == set(b)
    assert a != b
