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
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.cards import (
    Card,
    Rank,
    RestitutionError,
    Shoe,
    ShoeClosedError,
    ShoeEmpty,
    Suit,
    _shuffled_sequence,
    draw_key,
    high_card_draw,
    seeded_card_codes,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_SEED = 8843
_DECKS = 2
_JOKERS_PER_DECK = 2
_SHOE_TOTAL = _DECKS * (52 + _JOKERS_PER_DECK)  # 108, task-briefs.md's own number


def _codes(cards: Sequence[Card]) -> list[str]:
    """List each card's stored code ("AS", "10D", "JK"), in order."""
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


def test_shoe_deal_sequence_given_the_same_seed_matches_after_a_full_cycle() -> None:
    """A second instance reproduces the order an earlier shoe dealt.

    The pin above builds its two instances back to back; this one
    builds the second only after the first has dealt its whole cycle
    and reshuffled, so state an earlier shoe left behind -- a shared
    deal cursor, the process-wide RNG -- shows up as a mismatched
    order.
    """
    first = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    first_sequence = _codes(_deal_all(first))
    first.reshuffle()
    _deal_all(first)

    second = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)

    assert _codes(_deal_all(second)) == first_sequence


def test_shoe_deal_sequence_given_the_cards_config_is_the_fresh_multiset_shuffled() -> None:
    """T-7 invariant: the shuffle permutes the cards, never edits them.

    The oracle is the configured multiset built from the public
    ``Rank``/``Suit`` enums: every natural card once per deck, plus
    ``decks x jokers_per_deck`` jokers. A shuffle that lost,
    duplicated or invented a card fails here instead of passing on a
    matching total count.
    """
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    naturals = Counter(
        Card(rank=rank, suit=suit) for _ in range(_DECKS) for rank in Rank for suit in Suit
    )
    jokers = Counter({Card(rank=None, suit=None, joker=True): _DECKS * _JOKERS_PER_DECK})
    expected = naturals + jokers

    dealt = Counter(_deal_all(shoe))

    assert dealt == expected


def test_shoe_deal_sequence_given_seeds_differing_only_above_bit_62_differs() -> None:
    """Seeds 1 and 1 + 2**62 deal different orders: no seed truncation.

    The pair differs only in its high bits, so a shoe that folded the
    seed down to its low bits before shuffling would deal one order
    twice -- a failure the adjacent-seed pin above cannot see. Pinned
    as a known-differing pair (confirmed against CPython's
    ``random.Random(seed).shuffle`` before writing this test).
    """
    low = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=1)
    high = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=1 + 2**62)

    assert _codes(_deal_all(low)) != _codes(_deal_all(high))


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


def test_shoe_seed_given_a_constructed_shoe_returns_the_constructor_seed() -> None:
    """Seed is the stored rng_seed a replay rebuilds from."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)

    assert shoe.seed == _SEED


def test_shoe_seed_given_a_reshuffle_is_unchanged() -> None:
    """reshuffle() derives each cycle's seed; the stored one stays."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    _deal_all(shoe)

    shoe.reshuffle()

    assert shoe.seed == _SEED


# ------------------------------------------------ jokers_in_cycle (6d)
#
# The reshuffle audit's own reading (ride.py's ``_deal_card``): how many
# jokers the cycle now being built carries. Per-deck mode re-deals
# jokers_per_deck every cycle, so the reading is that constant; total
# mode re-deals only what the ride's budget has left, so the reading is
# the live budget -- which is what makes "N jokers added" true.


@pytest.mark.parametrize("jokers_per_deck", [0, 1, 4], ids=["none", "one", "four"])
def test_shoe_jokers_in_cycle_given_per_deck_mode_is_the_configured_count(
    jokers_per_deck: int,
) -> None:
    """T-4 bounds: per-deck mode never spends the count down."""
    shoe = Shoe(decks=1, jokers_per_deck=jokers_per_deck, seed=_SEED)

    assert shoe.jokers_in_cycle == jokers_per_deck


def test_shoe_jokers_in_cycle_given_per_deck_mode_holds_after_a_reshuffle() -> None:
    """Per-deck mode re-deals the same joker count every cycle."""
    shoe = Shoe(decks=1, jokers_per_deck=2, seed=_SEED)
    _deal_all(shoe)

    shoe.reshuffle()

    assert shoe.jokers_in_cycle == 2


def test_shoe_jokers_in_cycle_given_total_mode_starts_at_the_configured_count() -> None:
    """Total mode's first cycle carries the whole ride budget."""
    shoe = Shoe(decks=1, jokers_per_deck=2, seed=_SEED, jokers_total=True)

    assert shoe.jokers_in_cycle == 2


def test_shoe_jokers_in_cycle_given_total_mode_follows_the_spent_budget() -> None:
    """T-3: a dealt joker reduces what the next cycle carries."""
    shoe = Shoe(decks=1, jokers_per_deck=2, seed=_SEED, jokers_total=True)
    _deal_until_joker(shoe)

    shoe.reshuffle()

    assert shoe.jokers_in_cycle == 1


def test_shoe_jokers_in_cycle_given_total_mode_is_zero_once_the_budget_is_spent() -> None:
    """T-4 boundary at 0: a spent budget adds no jokers to a cycle."""
    shoe = Shoe(decks=1, jokers_per_deck=1, seed=_SEED, jokers_total=True)
    _deal_all(shoe)

    shoe.reshuffle()

    assert shoe.jokers_in_cycle == 0


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


# A reshuffle rebuilds the whole shoe, so every cycle deals exactly
# ``deck_count x (52 + jokers_per_deck)`` cards with the same joker
# count. A long ride therefore sees the joker total repeat each cycle:
# more than 16 jokers over a ride is expected behaviour, not a bug --
# the count scales with the ride's own Cards settings (R-13), it is
# never capped, and no cycle ever runs short of them.
RESHUFFLE_COMPOSITION_CASES = ((2, 2), (2, 4), (8, 4), (8, 2))
RESHUFFLE_COMPOSITION_IDS = [
    f"{decks}_decks_{jokers}_jokers" for decks, jokers in RESHUFFLE_COMPOSITION_CASES
]


@pytest.mark.parametrize(
    ("decks", "jokers_per_deck"),
    RESHUFFLE_COMPOSITION_CASES,
    ids=RESHUFFLE_COMPOSITION_IDS,
)
def test_shoe_reshuffle_next_cycle_deals_a_full_shoe_with_the_same_joker_count(
    decks: int, jokers_per_deck: int
) -> None:
    """Cycle 2 repeats the full composition, jokers included."""
    shoe = Shoe(decks=decks, jokers_per_deck=jokers_per_deck, seed=_SEED)
    first_naturals, first_jokers = _composition(_deal_all(shoe))

    shoe.reshuffle()
    second_naturals, second_jokers = _composition(_deal_all(shoe))

    assert (second_jokers, len(second_naturals)) == (first_jokers, len(first_naturals))
    assert (second_jokers, len(second_naturals)) == (
        decks * jokers_per_deck,
        len(Rank) * len(Suit),
    )


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


def test_seeded_card_codes_given_a_ten_spells_it_10_not_t() -> None:
    """The sequence's own ten codes are "10x" -- the stored form."""
    codes = seeded_card_codes(_SEED)

    ten_codes = sorted(code for code in codes if code[:-1] == "10")

    assert ten_codes == ["10C", "10D", "10H", "10S"]


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


# -------------------------------------------------------- Card.parse
# (The round-trip invariant that pins both parse() arms: whatever
# code() emits -- natural or joker -- parse() rebuilds the same card.)


@pytest.mark.parametrize(
    "code",
    ["AS", "2C", "10D", "9H", "KS", "4D"],
    ids=[
        "ace_of_spades",
        "two_of_clubs",
        "ten_of_diamonds",
        "nine_of_hearts",
        "king_of_spades",
        "four_of_diamonds",
    ],
)
def test_card_parse_round_trips_a_natural_code(code: str) -> None:
    """parse(natural) rebuilds the card whose code() emitted it."""
    parsed = Card.parse(code)

    assert parsed.joker is False
    assert parsed.code() == code


def test_card_code_given_the_ten_returns_the_three_character_10_form() -> None:
    """The stored code spells the ten "10", like its "10d" bitmap."""
    assert Card(rank=Rank.TEN, suit=Suit.DIAMONDS).code() == "10D"


def test_card_code_given_a_non_ten_stays_the_two_character_form() -> None:
    """Only the ten grows a character: "AS" is unchanged."""
    assert Card(rank=Rank.ACE, suit=Suit.SPADES).code() == "AS"


def test_card_parse_given_the_10_code_rebuilds_the_ten_of_diamonds() -> None:
    """parse("10D") reads rank "10" and suit "D" -- three characters."""
    parsed = Card.parse("10D")

    assert parsed == Card(rank=Rank.TEN, suit=Suit.DIAMONDS)
    assert parsed.code() == "10D"


def test_card_parse_given_the_retired_t_ten_code_raises_key_error() -> None:
    """T-5: no legacy "T" ten -- "TD" is not a card code."""
    with pytest.raises(KeyError, match=re.escape("'T'")):
        Card.parse("TD")


def test_card_parse_given_an_empty_code_raises_key_error() -> None:
    """T-4 empty boundary: "" has no rank, so the parse fails loud."""
    with pytest.raises(KeyError, match=re.escape("''")):
        Card.parse("")


def test_card_parse_round_trips_the_joker_code() -> None:
    """parse("JK") rebuilds the joker -- rankless, suitless, joker."""
    parsed = Card.parse("JK")

    assert parsed == Card(rank=None, suit=None, joker=True)
    assert parsed.code() == "JK"


# ======================== Phase 5: jokers_total mode (S4 amendment)
#
# Two modes share one shuffle. Per-deck -- Shoe's own default, the
# frozen S4 behaviour -- rebuilds ``decks x (52 + jokers_per_deck)``
# every cycle. ``jokers_total=True`` builds cycle 1 as
# ``decks x 52 + jokers_per_deck`` and spends that ride-wide budget as
# jokers are dealt, so each later cycle carries only what the deals so
# far left behind; once the budget reaches 0 the rest of the ride is
# naturals only. restitute() (Ctrl+Z) puts an undone joker back into
# the budget, so the accounting follows the shoe's real contents.

_TOTAL_DECKS = 2
_TOTAL_JOKERS = 3


def _joker_count(cards: Sequence[Card]) -> int:
    """Count how many of *cards* are jokers."""
    return sum(1 for card in cards if card.joker)


def _deal_until_joker(shoe: Shoe) -> list[Card]:
    """Deal cards until the first joker lands; return them, in order."""
    dealt: list[Card] = []
    while True:
        card, _ = shoe.deal()
        dealt.append(card)
        if card.joker:
            return dealt


@pytest.mark.parametrize(
    ("decks", "jokers_per_deck"),
    [(1, 0), (1, 1), (2, 2), (2, 4), (8, 10)],
    ids=["no_jokers", "min_positive", "two_decks_two", "two_decks_four", "max_jokers"],
)
def test_shoe_total_mode_cycle_one_holds_exactly_the_configured_jokers(
    decks: int, jokers_per_deck: int
) -> None:
    """T-4 bounds: cycle 1 is decks x 52 naturals + the budget, once."""
    shoe = Shoe(decks=decks, jokers_per_deck=jokers_per_deck, seed=_SEED, jokers_total=True)

    dealt = _deal_all(shoe)

    assert (len(dealt), _joker_count(dealt)) == (decks * 52 + jokers_per_deck, jokers_per_deck)


def test_shoe_total_mode_reshuffle_with_the_budget_unspent_keeps_the_jokers() -> None:
    """A cycle reshuffled before any deal re-deals the whole budget."""
    shoe = Shoe(decks=1, jokers_per_deck=2, seed=_SEED, jokers_total=True)

    shoe.reshuffle()
    dealt = _deal_all(shoe)

    assert (len(dealt), _joker_count(dealt)) == (54, 2)


def test_shoe_total_mode_dealing_a_joker_decrements_the_budget() -> None:
    """Cycle 2 re-deals only the jokers the deals so far left behind."""
    shoe = Shoe(decks=1, jokers_per_deck=2, seed=_SEED, jokers_total=True)
    first = _deal_until_joker(shoe)

    shoe.reshuffle()
    second = _deal_all(shoe)

    assert (_joker_count(first), len(second), _joker_count(second)) == (1, 53, 1)


def test_shoe_total_mode_naturals_only_once_the_budget_is_spent() -> None:
    """Cycle 1 exhausts the budget; later cycles deal no jokers."""
    shoe = Shoe(decks=1, jokers_per_deck=2, seed=_SEED, jokers_total=True)
    _deal_all(shoe)

    shoe.reshuffle()
    second = _deal_all(shoe)
    shoe.reshuffle()
    third = _deal_all(shoe)

    assert (len(second), _joker_count(second)) == (52, 0)
    assert (len(third), _joker_count(third)) == (52, 0)


def test_shoe_total_mode_restitute_returns_the_undone_joker_to_the_budget() -> None:
    """T-3: undo puts the joker back, so the next cycle carries it."""
    shoe = Shoe(decks=1, jokers_per_deck=1, seed=_SEED, jokers_total=True)
    dealt = _deal_until_joker(shoe)

    shoe.restitute(dealt[-1])
    shoe.reshuffle()
    second = _deal_all(shoe)

    assert (len(second), _joker_count(second)) == (53, 1)


def test_shoe_per_deck_mode_ignores_the_total_budget() -> None:
    """The S4 per-deck shape is unchanged: jokers return each cycle."""
    shoe = Shoe(decks=1, jokers_per_deck=2, seed=_SEED)

    _deal_all(shoe)
    shoe.reshuffle()
    second = _deal_all(shoe)

    assert (len(second), _joker_count(second)) == (54, 2)


def test_shoe_per_deck_mode_is_the_default_the_new_keyword_leaves_alone() -> None:
    """Omitting jokers_total keeps the frozen S4 sequence exactly."""
    implicit = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    explicit = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED, jokers_total=False)

    assert _codes(_deal_all(implicit)) == _codes(_deal_all(explicit))


# ------------------------------------------------- total mode: replay


def test_shoe_replay_total_mode_given_no_deals_matches_a_fresh_total_shoe() -> None:
    """replay(deals=0, cycles=1) is a fresh total-mode shoe."""
    replayed = Shoe.replay(
        decks=_TOTAL_DECKS,
        jokers_per_deck=_TOTAL_JOKERS,
        seed=_SEED,
        deals=0,
        cycles=1,
        jokers_total=True,
    )

    dealt = _deal_all(replayed)

    assert (len(dealt), _joker_count(dealt)) == (_TOTAL_DECKS * 52 + _TOTAL_JOKERS, _TOTAL_JOKERS)


@pytest.mark.parametrize("cycles", [1, 2, 3], ids=["cycle_1", "cycle_2", "cycle_3"])
def test_shoe_replay_total_mode_reproduces_the_budget_state(cycles: int) -> None:
    """T-4: replay() rebuilds the live remaining sequence exactly.

    Each earlier cycle is dealt to exhaustion before its reshuffle --
    the only way a reshuffle happens (spec §4: the caller reshuffles
    after ShoeEmpty) -- which is what leaves the live total-mode budget
    where the live shoe had it.
    """
    # logic-coverage-exempt: T-8 -- the cycle loops are the arrangement
    # (a live shoe driven to cycle N); the assertion block below is the
    # single Act+Assert, and the loop count is parametrized.
    live = Shoe(decks=_TOTAL_DECKS, jokers_per_deck=_TOTAL_JOKERS, seed=_SEED, jokers_total=True)
    for _ in range(cycles - 1):
        _deal_all(live)
        live.reshuffle()
    for _ in range(7):
        live.deal()

    replayed = Shoe.replay(
        decks=_TOTAL_DECKS,
        jokers_per_deck=_TOTAL_JOKERS,
        seed=_SEED,
        deals=7,
        cycles=cycles,
        jokers_total=True,
    )

    assert (replayed.dealt, replayed.remaining, replayed.cycle) == (
        live.dealt,
        live.remaining,
        live.cycle,
    )
    assert _codes(_deal_all(replayed)) == _codes(_deal_all(live))


# ----------------------------------------------- total mode: invariant


@given(
    decks=st.integers(min_value=1, max_value=4),
    jokers_per_deck=st.integers(min_value=0, max_value=10),
    cycles=st.integers(min_value=1, max_value=4),
    seed=st.integers(min_value=0, max_value=10**6),
)
# 4 generated inputs
def test_shoe_total_mode_never_deals_more_than_the_budget(  # noqa: PLR0913, PLR0917
    decks: int, jokers_per_deck: int, cycles: int, seed: int
) -> None:
    """T-7 invariant: the ride-wide joker allowance is never exceeded.

    Per-deck mode repeats its jokers every cycle (decks x jokers per
    cycle); total mode spends them once, so however many cycles a ride
    runs, the jokers it ever deals cannot exceed jokers_per_deck.
    """
    shoe = Shoe(decks=decks, jokers_per_deck=jokers_per_deck, seed=seed, jokers_total=True)

    # logic-coverage-exempt: T-8 -- the repeated cycle is the property's
    # own subject (jokers dealt across cycles); Hypothesis generates the
    # cycle count, so a parametrize row would only re-run one case.
    dealt_jokers = 0
    for _ in range(cycles):
        dealt_jokers += _joker_count(_deal_all(shoe))
        shoe.reshuffle()

    assert dealt_jokers == jokers_per_deck


# ==================== D2: reconfigure (a DRAFT shoe-size edit)
#
# A DRAFT ride's shoe-structure edit (Edit Ride…) rebuilds the live shoe
# from the SAME stored seed under the new deck/joker settings, so the
# stored config and the live shoe can never silently diverge
# (SHOWMECHANICS.md's Edit-gating promise).


def test_shoe_reconfigure_given_new_decks_rebuilds_cycle_one_at_the_new_size() -> None:
    """A reconfigured shoe holds the new deck count, nothing dealt."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    shoe.deal()

    shoe.reconfigure(4, _JOKERS_PER_DECK, jokers_total=False)

    assert (shoe.cycle, shoe.dealt, shoe.remaining) == (1, 0, 4 * (52 + _JOKERS_PER_DECK))


def test_shoe_reconfigure_given_the_same_config_re_deals_the_stored_seed_sequence() -> None:
    """The rebuild keeps the stored seed, so the order is unchanged."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)

    shoe.reconfigure(_DECKS, _JOKERS_PER_DECK, jokers_total=False)

    reference = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    assert _codes(_deal_all(shoe)) == _codes(_deal_all(reference))


@pytest.mark.parametrize(
    ("jokers_total", "expected"),
    [(False, (_DECKS * (52 + 3), _DECKS * 3)), (True, (_DECKS * 52 + 3, 3))],
    ids=["per_deck", "total"],
)
def test_shoe_reconfigure_given_the_mode_flag_builds_that_modes_composition(
    jokers_total: bool,  # noqa: FBT001 -- a parametrize row value, not a call-site flag
    expected: tuple[int, int],
) -> None:
    """T-13 both arms: the cycle follows the requested jokers mode."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED, jokers_total=False)

    shoe.reconfigure(_DECKS, 3, jokers_total=jokers_total)

    dealt = _deal_all(shoe)
    assert (len(dealt), _joker_count(dealt)) == expected


def test_shoe_reconfigure_given_a_total_mode_rebuild_restores_the_joker_budget() -> None:
    """A spent budget is restored by the rebuild."""
    shoe = Shoe(decks=1, jokers_per_deck=2, seed=_SEED, jokers_total=True)
    _deal_all(shoe)

    shoe.reconfigure(1, 2, jokers_total=True)
    rebuilt = _deal_all(shoe)

    assert (len(rebuilt), _joker_count(rebuilt)) == (54, 2)


def test_shoe_reconfigure_given_a_closed_shoe_raises_shoe_closed_error() -> None:
    """T-5: a closed shoe refuses a rebuild, like any mutation."""
    shoe = Shoe(decks=_DECKS, jokers_per_deck=_JOKERS_PER_DECK, seed=_SEED)
    shoe.close()

    with pytest.raises(ShoeClosedError, match=re.escape("shoe is closed")):
        shoe.reconfigure(_DECKS, 0, jokers_total=False)


# =============== high-card tie-break draw (spec §5 rule ①, R-14)
#
# A hand tie a FINISHED ride's stored order still reaches is resolved
# at the venue by ① high-card draw: one fresh natural deck -- jokers
# are wild, never drawn -- shuffled under the ride's own seed (R-40's
# replay discipline), from which each tied entry draws a card.
# draw_key() orders what comes back -- rank major, suit minor with
# spades highest -- so the drawn pair is separated by the draw itself
# and never silently ordered (R-43).


def test_high_card_draw_given_the_same_seed_returns_the_identical_tuple() -> None:
    """R-40: the seed reproduces the draw exactly, card for card."""
    first = high_card_draw(_SEED, 5)
    second = high_card_draw(_SEED, 5)

    assert first == second
    assert len(first) == 5


@pytest.mark.parametrize("count", [-1, 0], ids=["negative", "zero"])
def test_high_card_draw_given_a_non_positive_count_returns_an_empty_tuple(count: int) -> None:
    """T-4 bounds: fewer than one card is not a draw."""
    assert high_card_draw(_SEED, count) == ()


@pytest.mark.parametrize(
    "count",
    [1, 2, 51, 52, 53],
    ids=["one", "two", "fifty_one", "full_deck", "past_the_deck"],
)
def test_high_card_draw_given_a_count_returns_the_shuffled_decks_prefix(count: int) -> None:
    """The draw is one fresh natural deck's own first *count* cards."""
    deck = _shuffled_sequence(decks=1, jokers_per_deck=0, seed=_SEED)

    assert high_card_draw(_SEED, count) == tuple(deck[:count])


def test_high_card_draw_given_a_full_deck_returns_52_distinct_naturals() -> None:
    """count=52 is the whole deck: unique codes, no joker among them."""
    codes = _codes(high_card_draw(_SEED, 52))

    assert len(codes) == 52
    assert len(set(codes)) == 52
    assert "JK" not in codes


@pytest.mark.parametrize(
    ("rank", "suit", "expected"),
    [
        (Rank.TWO, Suit.CLUBS, 8),
        (Rank.TWO, Suit.SPADES, 11),
        (Rank.KING, Suit.CLUBS, 52),
        (Rank.KING, Suit.DIAMONDS, 53),
        (Rank.KING, Suit.HEARTS, 54),
        (Rank.KING, Suit.SPADES, 55),
        (Rank.ACE, Suit.CLUBS, 56),
        (Rank.ACE, Suit.SPADES, 59),
    ],
    ids=[
        "lowest_key",
        "two_of_spades",
        "king_of_clubs",
        "king_of_diamonds",
        "king_of_hearts",
        "king_of_spades",
        "ace_of_clubs",
        "highest_key",
    ],
)
def test_draw_key_given_a_natural_is_rank_major_with_spades_highest(
    rank: Rank, suit: Suit, expected: int
) -> None:
    """The key is rank x 4 + suit slot: clubs 0 through spades 3."""
    assert draw_key(Card(rank=rank, suit=suit)) == expected


def test_draw_key_given_all_52_naturals_produces_52_distinct_keys() -> None:
    """Every natural keys uniquely, so a draw separates a pair."""
    keys = {draw_key(Card(rank=rank, suit=suit)) for rank in Rank for suit in Suit}

    assert len(keys) == 52


def test_draw_key_given_ace_of_spades_exceeds_king_of_spades() -> None:
    """Rank leads: the highest natural outkeys the rank below it."""
    ace = draw_key(Card(rank=Rank.ACE, suit=Suit.SPADES))
    king = draw_key(Card(rank=Rank.KING, suit=Suit.SPADES))

    assert ace > king


def test_draw_key_given_king_of_spades_exceeds_king_of_hearts() -> None:
    """Suit breaks a rank tie: among kings the spade is the higher."""
    spades = draw_key(Card(rank=Rank.KING, suit=Suit.SPADES))
    hearts = draw_key(Card(rank=Rank.KING, suit=Suit.HEARTS))

    assert spades > hearts


def test_draw_key_given_a_joker_raises_value_error() -> None:
    """T-5: a joker is wild, never drawn -- it has no draw key."""
    joker = Card(rank=None, suit=None, joker=True)

    with pytest.raises(ValueError, match=re.escape("a joker has no draw key")):
        draw_key(joker)
