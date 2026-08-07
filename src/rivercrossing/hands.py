# SPDX-License-Identifier: GPL-3.0-only
"""Best hand from 0..N cards: natural, wild or duplicate (spec S5).

``eval5`` wraps phevaluator's natural-card evaluator -- spec section 5's
algorithm sketch names it explicitly -- with a table-driven mapping from
its 7,462 distinct ranks onto this project's local :class:`HandClass`
ordering, a Royal-Flush-above-Straight-Flush split that phevaluator
itself does not make, and (E2.1.2) a five-of-a-kind check ranked above
every natural hand: jokers are wild and always resolve to whichever
natural completion maximizes the hand.

Physical-cards semantics (E2.1.3, spec section 5): a multi-deck shoe can
legally deal one entry two identical cards, so a "natural" 5-card hand
is not always 5 pairwise-distinct codes -- 9H 9H is simply a pair of
nines, and 9H 9H KH QH 2H is a king-high flush whose kickers happen to
include a paired card. phevaluator's native evaluator is undefined
(observed to segfault) on a repeated card id, so any hand with one
takes ``classify_pattern``'s first-principles path instead of
phevaluator's; both paths feed the same :func:`_kicker_tiebreak`, so a
hand's class and tiebreak always compare consistently regardless of
which path produced it. ``tools/gen_rank_vectors.py`` imports
``classify_pattern`` rather than keeping its own copy.

``best_hand`` is the best-5-of-N search (E2.1.3): 5 or more cards search
every 5-of-N natural subset with every joker kept in play (spec
section 5's ``best_hand`` pseudocode -- a wild never hurts); fewer than
5 cards score as the best *partial* hand those cards can make, and a
missing kicker always ranks below a present one. Card cap X is a
caller concern (R-13): slice to the first X dealt cards before calling
``best_hand``, and the later, non-scoring cards are simply not passed.
"""

import bisect
import itertools
from collections import Counter
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, cast

from phevaluator.evaluator import evaluate_cards

from rivercrossing.cards import Card, Rank, Suit

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

NATURAL_HAND_SIZE = 5
# spec section 5: "the 7,462 distinct natural ranks are stored as one
# integer per entry".
NATURAL_RANK_COUNT = 7462

# A straight's 5 distinct ranks span this many values (e.g. 6-2=4 for
# 2-3-4-5-6); the wheel (A-2-3-4-5) is the one straight that doesn't,
# and always plays as a 5-high straight, never ace-high.
_STRAIGHT_SPAN = 4
_WHEEL_RANKS = (2, 3, 4, 5, 14)
_WHEEL_HIGH = 5
_ROYAL_RANKS = (10, 11, 12, 13, 14)


class HandClass(IntEnum):
    """Hand categories, worst to best (spec section 5 table, reversed).

    Values increase with strength, so a plain ``IntEnum`` comparison
    already orders hand classes correctly. ``FIVE_OF_A_KIND`` needs
    either a joker (E2.1.2) or 5 physically identical cards from a
    multi-deck shoe (E2.1.3); :func:`eval5` never produces it from 5
    pairwise-distinct natural cards.
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

    ``tiebreak`` always leads with the card count, then the standard
    ordered-rank (kicker) comparison for ``cls`` (see
    :func:`_kicker_tiebreak`): the count prefix is what makes a
    shorter partial hand always sort below a longer one of the same
    class, whatever its kickers (spec section 5's partial-hand rule --
    "a 4-card ace-high sits under every 5-card ace-high"). Both the
    phevaluator-backed fast path and the first-principles path (E2.1.3
    module docstring) emit tiebreaks on this same shape, so a hand
    from either path compares correctly against the other.
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
_UPPER_BOUNDS = (1, 10, 166, 322, 1599, 1609, 2467, 3325, 6185, NATURAL_RANK_COUNT)
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
# the fixed candidate order every wild search below walks. Combined
# with `combinations_with_replacement`'s index-order guarantee, this is
# also what makes a multi-joker resolution's *order* deterministic: the
# higher-ranked (then lower-suit-index) card always comes first.
_ALL_NATURAL_CARDS: tuple[Card, ...] = tuple(
    Card(rank=rank, suit=suit) for rank in sorted(Rank, reverse=True) for suit in Suit
)


_QUADS_COUNT = 4
_TRIPS_COUNT = 3
_PAIR_COUNT = 2


def _straight_high(ranks: Sequence[int]) -> int | None:
    """Return a straight's high card, or None if *ranks* isn't one.

    Exactly 5 distinct, consecutive ranks -- or the wheel, A-2-3-4-5,
    which plays as a 5-high straight, never ace-high.
    """
    distinct = sorted(set(ranks))
    if tuple(distinct) == _WHEEL_RANKS:
        return _WHEEL_HIGH
    if len(distinct) == NATURAL_HAND_SIZE and distinct[-1] - distinct[0] == _STRAIGHT_SPAN:
        return distinct[-1]
    return None


def _classify_by_counts(counts: Sequence[int]) -> HandClass:
    """Classify by rank-count pattern alone, no flush or straight."""
    if counts[0] == _QUADS_COUNT:
        label = HandClass.QUADS
    elif counts[:2] == [_TRIPS_COUNT, _PAIR_COUNT]:
        label = HandClass.FULL_HOUSE
    elif counts[0] == _TRIPS_COUNT:
        label = HandClass.TRIPS
    elif counts[:2] == [_PAIR_COUNT, _PAIR_COUNT]:
        label = HandClass.TWO_PAIR
    elif counts[0] == _PAIR_COUNT:
        label = HandClass.PAIR
    else:
        label = HandClass.HIGH_CARD
    return label


def classify_pattern(ranks: Sequence[int], suits: Sequence[str]) -> HandClass:
    """Classify a 0..5-card rank/suit pattern, first principles.

    Independent of phevaluator: a flush is 5 matching suits, a
    straight is 5 consecutive rank values (or the wheel, A-2-3-4-5),
    and the rest follows the rank-value multiset -- never consults a
    phevaluator rank, so it can independently confirm one. Duplicate
    ranks and suits are not a foul (physical-cards semantics, module
    docstring): 5 cards sharing one rank is FIVE_OF_A_KIND, and a
    flush's "5 matching suits" is exactly as true when 2 of those 5
    happen to be the same physical card.

    Fewer than 5 ranks can never be a flush or a straight -- both
    inherently need all 5 cards to exist at all (spec section 5's
    partial-hand rule) -- so this only ever falls through to
    :func:`_classify_by_counts` for a short hand. A straight and a
    rank-count pattern (quads, full house, trips, two pair, pair)
    never both apply -- a straight needs 5 distinct ranks -- so their
    relative check order here does not affect the result.
    """
    if not ranks:
        return HandClass.HIGH_CARD
    counts = sorted(Counter(ranks).values(), reverse=True)
    if counts[0] >= NATURAL_HAND_SIZE:
        return HandClass.FIVE_OF_A_KIND

    is_full_hand = len(ranks) == NATURAL_HAND_SIZE
    is_flush = is_full_hand and len(set(suits)) == 1
    straight_high = _straight_high(ranks) if is_full_hand else None
    by_counts = _classify_by_counts(counts)

    if straight_high is not None and is_flush:
        is_royal = tuple(sorted(set(ranks))) == _ROYAL_RANKS
        label = HandClass.ROYAL_FLUSH if is_royal else HandClass.STRAIGHT_FLUSH
    elif by_counts in (HandClass.QUADS, HandClass.FULL_HOUSE):
        label = by_counts
    elif is_flush:
        label = HandClass.FLUSH
    elif straight_high is not None:
        label = HandClass.STRAIGHT
    else:
        label = by_counts
    return label


def _kicker_tiebreak(cls: HandClass, ranks: Sequence[int]) -> tuple[int, ...]:
    """Compute the count-then-kicker tiebreak tuple for *ranks*.

    The leading element is always the card count (see
    ``EvaluatedHand``'s own docstring for why); the rest follows each
    class's usual ordered-rank comparison.

    HIGH_CARD and FLUSH compare by raw rank value alone: neither class
    is *about* a same-rank group (a flush is just 5 cards of one suit,
    duplicate-legal ranks included -- physical-cards semantics), so a
    physically-paired card within one must never outrank a genuinely
    higher single card the way it would for PAIR/TRIPS/etc. (module
    docstring's own king-high-flush-with-paired-nines example). The
    two straight classes only ever differ by their high card (mindful
    of the wheel), and ROYAL_FLUSH never varies at all. Every other
    class -- the ones actually built from a same-rank group -- ranks
    by (how many of a rank, how high that rank is), the standard
    poker kicker order.
    """
    count = len(ranks)
    if not ranks or cls == HandClass.ROYAL_FLUSH:
        return (count,)
    if cls in (HandClass.STRAIGHT, HandClass.STRAIGHT_FLUSH):
        # cls already confirms ranks form a straight, so this is never
        # None here -- the cast documents that to mypy.
        return (count, cast("int", _straight_high(ranks)))
    if cls in (HandClass.HIGH_CARD, HandClass.FLUSH):
        return (count, *sorted(ranks, reverse=True))
    rank_counts = Counter(ranks)
    ordered = sorted(rank_counts, key=lambda rank: (rank_counts[rank], rank), reverse=True)
    return (count, *ordered)


def _classify(natural_rank: int) -> HandClass:
    """Look up *natural_rank*'s :class:`HandClass` via the table."""
    index = bisect.bisect_left(_UPPER_BOUNDS, natural_rank)
    return _CLASS_BY_UPPER_BOUND[index]


def _is_duplicate_free(cards: Sequence[Card]) -> bool:
    """Report whether *cards* are pairwise-distinct codes.

    Compares ``(rank, suit)`` pairs rather than calling ``.code()``:
    this runs on every wild-search candidate (spec section 5's
    performance budget, R-42), and skipping the string formatting
    matters at that volume.
    """
    return len({(card.rank, card.suit) for card in cards}) == len(cards)


def _natural_ranks(cards: Sequence[Card]) -> list[int]:
    """List each natural card's rank value.

    Narrows ``Rank | None`` for mypy: every caller here already only
    ever holds natural (non-joker) cards, which always carry a rank.
    A dict keyed by ``Card`` was measured *slower* here than this
    direct attribute read -- unlike :func:`_phevaluator_card_id`
    below, one ``.value`` access has less work to save than hashing
    a whole ``Card`` costs to look one up.
    """
    return [cast("Rank", card.rank).value for card in cards]


def _natural_suits(cards: Sequence[Card]) -> list[str]:
    """List each natural card's suit value (mypy narrowing above)."""
    return [cast("Suit", card.suit).value for card in cards]


# phevaluator's own native card id: rank_index (0 for Two .. 12 for
# Ace) * 4 + suit_index, verified against phevaluator.card.Card.to_id.
_PHEVALUATOR_SUIT_INDEX: dict[Suit, int] = {
    Suit.CLUBS: 0,
    Suit.DIAMONDS: 1,
    Suit.HEARTS: 2,
    Suit.SPADES: 3,
}

# Precomputed once for all 52 natural cards -- there are only ever 52
# to id -- rather than recomputing the rank/suit arithmetic (and its
# enum-attribute lookups) on every call: this runs on every wild-
# search candidate, and a dict lookup measurably beats that on the
# R-42 180x12 field budget (profiled: ~1.5M calls in one field pass).
_PHEVALUATOR_ID_BY_CARD: dict[Card, int] = {
    card: (cast("Rank", card.rank).value - Rank.TWO.value) * len(Suit)
    + _PHEVALUATOR_SUIT_INDEX[cast("Suit", card.suit)]
    for card in _ALL_NATURAL_CARDS
}


def _phevaluator_card_id(card: Card) -> int:
    """Look up phevaluator's native card id for *card*.

    ``Card.code()`` followed by phevaluator's own string parser reaches
    the exact same id; skipping the format-then-reparse round trip,
    and the per-call arithmetic in favour of a precomputed table,
    both matter here because this runs on every wild-search candidate
    (R-42's 180x12 field budget).
    """
    return _PHEVALUATOR_ID_BY_CARD[card]


_ClsTiebreak = tuple[HandClass, tuple[int, ...]]


def _classify_pattern_cards(cards: Sequence[Card]) -> _ClsTiebreak:
    """Classify 0..5 natural cards, first principles, cls+tiebreak only.

    :func:`_fill_score`'s per-candidate hot loop (R-42's 180x12 field
    budget) never needs the full :class:`EvaluatedHand` wrapper it
    would otherwise discard immediately -- only the ``(cls,
    tiebreak)`` pair that decides which candidate wins -- so this is
    the shared computation both it and :func:`_evaluate_pattern`
    (the public-shaped wrapper) build on.
    """
    ranks = _natural_ranks(cards)
    cls = classify_pattern(ranks, _natural_suits(cards))
    return cls, _kicker_tiebreak(cls, ranks)


def _evaluate_pattern(cards: Sequence[Card]) -> EvaluatedHand:
    """Evaluate 0..5 natural (joker-free) cards, first principles.

    No phevaluator fast path here: this always serves either a
    fewer-than-5-card partial hand or a duplicate-bearing 5-card one
    (:func:`classify_pattern`'s module docstring covers both), and a
    wild search's own candidate scoring, all cheap regardless.
    """
    cls, tiebreak = _classify_pattern_cards(cards)
    return EvaluatedHand(cls=cls, tiebreak=tiebreak, best5=tuple(cards), jokers_played_as=())


def _classify_natural_five(cards: Sequence[Card]) -> _ClsTiebreak:
    """Classify 5 pairwise-distinct natural cards, cls+tiebreak only.

    Callers must ensure the 5 codes are pairwise distinct (see
    :func:`_evaluate_natural_five`, the public-shaped wrapper this
    shares its computation with); :func:`_fill_score`'s hot loop is
    the reason this skips building an :class:`EvaluatedHand` (see
    :func:`_classify_pattern_cards`'s docstring, same rationale).
    """
    natural_rank: int = evaluate_cards(*(_phevaluator_card_id(card) for card in cards))
    cls = _classify(natural_rank)
    return cls, _kicker_tiebreak(cls, _natural_ranks(cards))


def _evaluate_natural_five(cards: Sequence[Card]) -> EvaluatedHand:
    """Evaluate 5 pairwise-distinct natural cards via phevaluator.

    Callers must ensure the 5 codes are pairwise distinct: phevaluator's
    native evaluator is undefined -- observed to segfault -- on a
    repeated card id. :func:`_evaluate_five_naturals` is the safe
    entry point; this direct call is only for the rank-sweep and
    joker-vector fast paths, which already know their input is clean.
    """
    cls, tiebreak = _classify_natural_five(cards)
    return EvaluatedHand(cls=cls, tiebreak=tiebreak, best5=tuple(cards), jokers_played_as=())


def _evaluate_five_naturals(cards: Sequence[Card]) -> EvaluatedHand:
    """Evaluate exactly 5 natural (joker-free) cards, safely.

    Distinct-code hands take phevaluator's fast native path;
    everything else -- legal under physical-cards semantics (module
    docstring) -- takes the first-principles path instead.
    """
    if _is_duplicate_free(cards):
        return _evaluate_natural_five(cards)
    return _evaluate_pattern(cards)


def _uniform_natural_rank(naturals: Sequence[Card]) -> Rank | None:
    """Return the one rank every natural card already shares, if any.

    True trivially with 0 or 1 naturals. A wild search can always
    reach every remaining card of that rank -- duplicating a suit is
    legal (physical-cards semantics) -- so whenever this returns a
    rank, matching every joker to it is unconditionally optimal:
    HandClass always dominates any kicker, so growing this rank's
    group (pair -> trips -> quads -> five-of-a-kind) beats any
    alternative regardless of what other ranks might offer. With no
    naturals at all, Ace is the unconstrained best choice -- spec
    section 5's ``j >= 5 -> FIVE_OF_KIND(Ace)`` shortcut falls out of
    this same rule with no extra branch.
    """
    ranks = {card.rank for card in naturals}
    if len(ranks) > 1:
        return None
    return next(iter(ranks)) if ranks else Rank.ACE


def _cycle_suits(count: int) -> tuple[Suit, ...]:
    """Pick *count* suits, cycling Suit's declaration order.

    Only used where a joker's exact suit is display-only and can never
    change the hand's class or tiebreak (matching an already-uniform
    rank: any suit reaches the same group). Duplicates are legal (R1
    of the joker vector table).
    """
    suits = tuple(Suit)
    return tuple(suits[index % len(suits)] for index in range(count))


def _five_of_a_kind_hand(cards: Sequence[Card], rank: Rank, joker_count: int) -> EvaluatedHand:
    """Build the FIVE_OF_A_KIND result for *rank*, filled by suit."""
    jokers_played_as = tuple(Card(rank=rank, suit=suit) for suit in _cycle_suits(joker_count))
    return EvaluatedHand(
        cls=HandClass.FIVE_OF_A_KIND,
        tiebreak=(NATURAL_HAND_SIZE, rank.value),
        best5=tuple(cards),
        jokers_played_as=jokers_played_as,
    )


_FillScore = tuple[HandClass, tuple[int, ...], bool]


def _fill_score(
    naturals: Sequence[Card], fill: Sequence[Card], *, allow_fast_path: bool
) -> _FillScore:
    """Score one candidate wild fill, combined with *naturals*.

    Inlines the duplicate check and rank extraction in one pass over
    the combined 5 cards, rather than calling
    :func:`_classify_natural_five`/:func:`_classify_pattern_cards`
    (each of which would redo both from scratch): this is the
    innermost loop of the whole wild search, called once per
    candidate fill (R-42's 180x12 field budget). *allow_fast_path* is
    False for a partial hand (module docstring of
    :func:`_partial_hand`): fewer than 5 cards is never something
    phevaluator can evaluate at all.

    The trailing "is this combination duplicate-free" flag is a pure
    tie-break preference, never part of the stored ``EvaluatedHand``:
    once a rank group's size is decided, which exact suit fills it
    never changes class or tiebreak, so among equally-scoring fills
    this prefers a fresh card over reusing one already in the hand.
    Reaching for a duplicate is never *necessary* here -- the one
    class where it can be (five-of-a-kind) is decided by
    :func:`_uniform_natural_rank` before this search ever runs.
    """
    combined = (*naturals, *fill)
    seen: set[tuple[Rank | None, Suit | None]] = set()
    ranks: list[int] = []
    for card in combined:
        seen.add((card.rank, card.suit))
        ranks.append(cast("Rank", card.rank).value)
    duplicate_free = len(seen) == len(combined)
    if allow_fast_path and duplicate_free:
        natural_rank: int = evaluate_cards(*(_PHEVALUATOR_ID_BY_CARD[card] for card in combined))
        cls = _classify(natural_rank)
    else:
        cls = classify_pattern(ranks, _natural_suits(combined))
    return (cls, _kicker_tiebreak(cls, ranks), duplicate_free)


def _straight_completion_ranks(natural_ranks: Sequence[int]) -> frozenset[int]:
    """List ranks that could complete or extend a straight in ranks.

    A straight needs 5 consecutive values spanning at most
    ``_STRAIGHT_SPAN``; every rank within that span of either end of
    the naturals is a plausible completion. The wheel (A-2-3-4-5)
    needs its own check -- the ace sits at value 14, nowhere near the
    low end numerically, even though it plays low there.

    Both callers only ever reach this with at least one rank already
    (:func:`_pruned_wild_candidates` and :func:`_relevant_naturals`
    only run once at least one natural card is known to exist), so an
    empty *natural_ranks* is a genuine invariant violation, not a
    case to handle quietly -- ``min()``/``max()`` raise on it.
    """
    completions: set[int] = set()
    if set(natural_ranks) <= set(_WHEEL_RANKS):
        completions |= set(_WHEEL_RANKS)
    low, high = min(natural_ranks), max(natural_ranks)
    if high - low <= _STRAIGHT_SPAN:
        completions |= set(range(max(2, high - _STRAIGHT_SPAN), min(14, low + _STRAIGHT_SPAN) + 1))
    return frozenset(completions)


def _plausible_flush_suits(naturals: Sequence[Card], joker_count: int) -> frozenset[Suit]:
    """List suits with enough natural cards to plausibly reach a flush.

    A flush needs 5 cards of one suit; if a suit's natural count plus
    every remaining joker still falls short, pursuing it can never
    help, so it is not worth a candidate at all.
    """
    suit_counts = Counter(card.suit for card in naturals if card.suit is not None)
    return frozenset(
        suit for suit, count in suit_counts.items() if count + joker_count >= NATURAL_HAND_SIZE
    )


def _pruned_wild_candidates(naturals: Sequence[Card], joker_count: int) -> tuple[Card, ...]:
    """Build a small, sufficient wild-fill candidate set for *naturals*.

    Spec section 5's own pruning heuristic: "ranks in subset,
    straight-completing ranks, suits present ... aces". The only ways
    a wild card can ever raise a hand's class are growing an existing
    rank's group, completing a straight, or completing a flush;
    anything else can only ever match the best possible plain kicker,
    which an ace, crossed with every suit (suit never matters for a
    rank group or a plain straight), already is. Filtering
    ``_ALL_NATURAL_CARDS`` instead of building a fresh set keeps this
    in that same fixed, deterministic order.
    """
    natural_ranks = [card.rank for card in naturals if card.rank is not None]
    rank_values = [rank.value for rank in natural_ranks]
    candidate_ranks = (
        set(natural_ranks)
        | {Rank(value) for value in _straight_completion_ranks(rank_values)}
        | {Rank.ACE}
    )
    flush_suits = _plausible_flush_suits(naturals, joker_count)
    return tuple(
        card
        for card in _ALL_NATURAL_CARDS
        if card.rank in candidate_ranks or card.suit in flush_suits
    )


def _pruned_wild_candidates_for_scoring(
    naturals: Sequence[Card], joker_count: int
) -> tuple[Card, ...]:
    """Build a scoring-only wild-fill candidate set, smaller still.

    Only ever used by :func:`_score_natural_subset` (``best_hand``'s
    cached subset search), which unlike :func:`eval5` never surfaces
    which exact suit a joker resolved to -- only the resulting
    ``(cls, tiebreak)``. :func:`_pruned_wild_candidates`'s own
    docstring already establishes that a rank-matching candidate's
    suit can never change its class or tiebreak (only a
    flush-plausible suit's exact identity can), so this keeps one
    representative suit per candidate rank instead of all four, on
    top of the full suit range for any flush-plausible suit --
    cutting the C(m, j) search :func:`_best_fill_from_candidates` runs
    by roughly ``len(Suit)`` per joker slot (R-42's 180x12 field
    budget: this is the search's own dominant cost, profiled).
    """
    natural_ranks = [card.rank for card in naturals if card.rank is not None]
    rank_values = [rank.value for rank in natural_ranks]
    candidate_ranks = (
        set(natural_ranks)
        | {Rank(value) for value in _straight_completion_ranks(rank_values)}
        | {Rank.ACE}
    )
    representative_suit = next(iter(Suit))
    rank_candidates = tuple(Card(rank=rank, suit=representative_suit) for rank in candidate_ranks)
    flush_suits = _plausible_flush_suits(naturals, joker_count)
    if not flush_suits:
        return rank_candidates
    flush_candidates = tuple(card for card in _ALL_NATURAL_CARDS if card.suit in flush_suits)
    return rank_candidates + flush_candidates


def _best_fill(
    combos: Iterable[tuple[Card, ...]], naturals: Sequence[Card], *, allow_fast_path: bool
) -> tuple[Card, ...]:
    """Keep the best-scoring candidate fill from *combos*."""
    return max(
        combos, key=lambda fill: _fill_score(naturals, fill, allow_fast_path=allow_fast_path)
    )


def _search_best_fill(
    naturals: Sequence[Card], joker_count: int, *, allow_fast_path: bool
) -> tuple[Card, ...]:
    """Search every way to fill *joker_count* wild slots, keep the best.

    Shared by eval5's exactly-5-card wild search and best_hand's
    partial-hand wild search; *allow_fast_path* is the only
    difference (see :func:`_fill_score`). Only ever called once
    :func:`_uniform_natural_rank` has ruled out the
    unconditionally-optimal shortcut, so *naturals* always has 2+
    distinct ranks -- meaning at most 3 jokers reach this function
    when filling to 5 cards (4 would mean 1 natural, always uniform)
    and at most 2 when filling a shorter partial hand (3 would mean 1
    natural). Candidates are pruned per :func:`_pruned_wild_candidates`
    -- typically under 30 rather than the full 52.

    ``best_hand``'s own N-card subset search does *not* come through
    here -- :func:`_score_natural_subset` uses a further-reduced
    candidate set of its own
    (:func:`_pruned_wild_candidates_for_scoring`), safe only where
    the exact suit a joker resolves to is never observed (unlike
    this function's two callers, both of which return that suit to a
    caller that may display it).
    """
    candidates = _pruned_wild_candidates(naturals, joker_count)
    combos = itertools.combinations_with_replacement(candidates, joker_count)
    return _best_fill(combos, naturals, allow_fast_path=allow_fast_path)


def _evaluate_with_wild_cards(
    cards: Sequence[Card], naturals: Sequence[Card], joker_count: int
) -> EvaluatedHand:
    """Resolve 1+ jokers to whatever completion maximizes the hand."""
    uniform_rank = _uniform_natural_rank(naturals)
    if uniform_rank is not None:
        return _five_of_a_kind_hand(cards, uniform_rank, joker_count)
    fill = _search_best_fill(naturals, joker_count, allow_fast_path=True)
    result = _evaluate_five_naturals((*naturals, *fill))
    return EvaluatedHand(
        cls=result.cls, tiebreak=result.tiebreak, best5=tuple(cards), jokers_played_as=fill
    )


def _require_five_cards(cards: Sequence[Card]) -> None:
    """Raise InvalidHandError unless *cards* has exactly 5 entries."""
    if len(cards) != NATURAL_HAND_SIZE:
        msg = f"eval5 requires exactly {NATURAL_HAND_SIZE} cards, got {len(cards)}"
        raise InvalidHandError(msg)


def eval5(cards: Sequence[Card]) -> EvaluatedHand:
    """Evaluate exactly 5 cards, natural or wild, into an EvaluatedHand.

    Args:
        cards: Exactly 5 cards; any number of them may be jokers, and
            any number of the natural ones may repeat a code (a
            multi-deck shoe can legally deal one entry two identical
            cards -- module docstring).

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
        return _evaluate_five_naturals(cards)
    return _evaluate_with_wild_cards(cards, naturals, joker_count)


def _partial_hand(cards: Sequence[Card]) -> EvaluatedHand:
    """Evaluate a 0..4 card hand -- spec section 5's partial-hand rule.

    Never a flush or straight (both need all 5 cards to exist at
    all -- :func:`classify_pattern`'s module docstring): the only
    thing a joker can do here is grow an existing natural rank's
    group, or -- with no natural anchor -- become an ace, exactly the
    same unconditionally-optimal shortcut :func:`eval5` uses.
    """
    naturals = [card for card in cards if not card.joker]
    joker_count = len(cards) - len(naturals)
    uniform_rank = _uniform_natural_rank(naturals)
    if uniform_rank is not None:
        fill = tuple(Card(rank=uniform_rank, suit=suit) for suit in _cycle_suits(joker_count))
    else:
        fill = _search_best_fill(naturals, joker_count, allow_fast_path=False)
    result = _evaluate_pattern((*naturals, *fill))
    return EvaluatedHand(
        cls=result.cls, tiebreak=result.tiebreak, best5=tuple(cards), jokers_played_as=fill
    )


def _relevant_naturals(naturals: Sequence[Card], joker_count: int) -> tuple[Card, ...]:
    """Prune *naturals* to the ones a winning 5-card subset could use.

    Mirrors :func:`_pruned_wild_candidates`'s reasoning, turned around
    for choosing *which naturals* the outer subset search bothers
    with (``best_hand``'s C(n, k) subset loop otherwise multiplies
    against every wild-search call -- R-42's 180x12 field budget
    needs this too, not just the wild search). A natural only ever
    earns a place in the best subset by (a) sharing a rank with
    another natural (growing a pair/trips/quads), (b) sitting in the
    straight-completion range for the pool's overall rank spread, (c)
    holding a suit that could plausibly complete a flush, or (d)
    simply ranking among the highest available plain kickers -- kept
    by original list position, not value, so a duplicate-legal repeat
    is never silently collapsed into "the same" candidate.
    """
    need = NATURAL_HAND_SIZE - joker_count
    rank_counts = Counter(card.rank for card in naturals)
    all_rank_values = [card.rank.value for card in naturals if card.rank is not None]
    straight_ranks = {Rank(value) for value in _straight_completion_ranks(all_rank_values)}
    flush_suits = _plausible_flush_suits(naturals, joker_count)
    by_rank_desc = sorted(
        range(len(naturals)), key=lambda i: cast("Rank", naturals[i].rank).value, reverse=True
    )
    top_kicker_indices = set(by_rank_desc[:need])
    return tuple(
        card
        for index, card in enumerate(naturals)
        if rank_counts[card.rank] > 1
        or card.rank in straight_ranks
        or card.suit in flush_suits
        or index in top_kicker_indices
    )


def _canonical_naturals(naturals: Sequence[Card]) -> tuple[Card, ...]:
    """Sort *naturals* into one fixed, rank-then-suit order.

    Every function a subset score depends on --
    :func:`_evaluate_five_naturals`, :func:`_uniform_natural_rank`,
    :func:`_best_fill` -- cares only about *naturals*'s rank/suit
    multiset, never its original order (module docstring), so this
    turns two subsets that are equal as multisets into one identical
    cache key for :func:`_score_natural_subset`.
    """
    return tuple(
        sorted(
            naturals,
            key=lambda card: (cast("Rank", card.rank).value, cast("Suit", card.suit).value),
        )
    )


_SubsetScore = tuple[HandClass, tuple[int, ...], tuple[Card, ...]]
_SubsetCache = dict[tuple[tuple[Card, ...], int], _SubsetScore]


def _score_natural_subset(
    naturals: Sequence[Card], joker_count: int, cache: _SubsetCache
) -> _SubsetScore:
    """Score *naturals* plus *joker_count* wild cards, cache the result.

    ``best_hand``'s own C(n, k) subset loop revisits the exact same
    rank/suit multiset constantly whenever its pool holds duplicate-
    legal cards (a birthday-paradox certainty in a 13-rank pool at
    12 cards) -- this collapses that repeated work behind
    :func:`_canonical_naturals`. *cache* is scoped to one ``best_hand``
    call, not module-global: the redundancy worth collapsing is
    within one entry's own search, not across different entries'
    largely-unrelated hands, so this never accumulates memory across
    a session (R-42's 180x12 field budget).

    The zero-joker case bypasses the cache entirely: measured, a
    duplicate 5-card natural multiset is uncommon enough (a 52-card
    pool, not a 13-rank one) that canonicalizing and hashing every
    subset costs more than the cache hits it buys back -- unlike the
    wild-search case below, where one avoided search is worth far
    more than one sort.
    """
    if joker_count == 0:
        evaluated = _evaluate_five_naturals(naturals)
        return (evaluated.cls, evaluated.tiebreak, ())
    key = (_canonical_naturals(naturals), joker_count)
    cached = cache.get(key)
    if cached is not None:
        return cached
    canonical, _ = key
    uniform_rank = _uniform_natural_rank(canonical)
    if uniform_rank is not None:
        hand = _five_of_a_kind_hand(canonical, uniform_rank, joker_count)
        score: _SubsetScore = (hand.cls, hand.tiebreak, hand.jokers_played_as)
    else:
        candidates = _pruned_wild_candidates_for_scoring(canonical, joker_count)
        combos = itertools.combinations_with_replacement(candidates, joker_count)
        fill = _best_fill(combos, canonical, allow_fast_path=True)
        evaluated = _evaluate_five_naturals((*canonical, *fill))
        score = (evaluated.cls, evaluated.tiebreak, fill)
    cache[key] = score
    return score


def best_hand(cards: Sequence[Card]) -> EvaluatedHand:
    """Find the best 5-card hand within *cards* (spec section 5).

    Args:
        cards: 0 or more cards, any number of them jokers or
            duplicate-legal repeats. Fewer than 5 cards score as the
            best partial hand those cards can make -- a missing
            kicker always ranks below a present one, however good the
            rest of the hand is (:func:`_partial_hand`). 5 or more
            cards search every 5-of-N natural subset with every joker
            kept in play (spec section 5's own pseudocode comment: "a
            wild never hurts -> all jokers play").

    Returns:
        The best :class:`EvaluatedHand` reachable from *cards*.

    Card cap X is a caller concern, not a parameter here (R-13): score
    the first X dealt cards by slicing *cards* before calling this
    function; later laps still count for laps/time even once they
    stop scoring.
    """
    if len(cards) < NATURAL_HAND_SIZE:
        return _partial_hand(cards)
    naturals = [card for card in cards if not card.joker]
    jokers = [card for card in cards if card.joker]
    if len(jokers) >= NATURAL_HAND_SIZE:
        chosen = tuple(jokers[:NATURAL_HAND_SIZE])
        return _five_of_a_kind_hand(chosen, Rank.ACE, NATURAL_HAND_SIZE)
    relevant = _relevant_naturals(naturals, len(jokers))
    subsets = itertools.combinations(relevant, NATURAL_HAND_SIZE - len(jokers))
    cache: _SubsetCache = {}
    scored = ((subset, _score_natural_subset(subset, len(jokers), cache)) for subset in subsets)
    best_subset, (cls, tiebreak, fill) = max(scored, key=lambda item: (item[1][0], item[1][1]))
    return EvaluatedHand(
        cls=cls, tiebreak=tiebreak, best5=(*best_subset, *jokers), jokers_played_as=fill
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
