# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.hands (E2.1.1, E2.1.2).

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
"""

import csv
import itertools
import re
import time
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.cards import Card, Rank, Suit
from rivercrossing.hands import (
    NATURAL_RANK_COUNT,
    EvaluatedHand,
    HandClass,
    InvalidHandError,
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


def _natural_rank(hand: EvaluatedHand) -> int:
    """Recover phevaluator's natural rank from an evaluated hand.

    The inverse of the ``tiebreak`` inversion documented on
    ``EvaluatedHand`` (hands.py): this is test-only convenience, not
    part of the module's public contract.
    """
    return NATURAL_RANK_COUNT + 1 - hand.tiebreak[0]


# ---------------------------------------------------------- the sweep


def test_eval5_sweep_matches_every_committed_rank_within_budget() -> None:
    """Every one of the 7,462 committed ranks round-trips through eval5.

    Also cross-checks ``eval5``'s table-driven ``HandClass`` against
    the CSV's independently (first-principles) computed one for every
    row -- not just the aggregate counts below.

    # logic-coverage-exempt: T-8 -- this *is* the brief-named "sweep":
    # one hand per distinct phevaluator rank, all 7,462 of them, with
    # a single measured wall-clock budget for the whole pass.
    # Splitting it into 7,462 parametrize rows would both defeat that
    # aggregate timing assertion and turn one conceptual test into
    # thousands of near-identical ones (CODINGSTANDARDS-SIMPLECODE.md
    # rule 7).
    """
    rows = _load_rank_sweep_rows(_RANK_SWEEP_CSV)
    evaluated_ranks: set[int] = set()

    start = time.perf_counter()
    for row in rows:
        evaluated = eval5(_cards(row.cards))
        assert _natural_rank(evaluated) == row.rank
        assert evaluated.cls.name == row.hand_class
        evaluated_ranks.add(_natural_rank(evaluated))
    elapsed = time.perf_counter() - start

    assert len(rows) == NATURAL_RANK_COUNT
    assert evaluated_ranks == set(range(1, NATURAL_RANK_COUNT + 1))
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
    """The royal flush lands in ``HandClass.ROYAL_FLUSH``, rank 1."""
    royal = eval5(_cards("TS JS QS KS AS"))

    assert royal.cls == HandClass.ROYAL_FLUSH
    assert _natural_rank(royal) == 1


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
    expected_tiebreak: int
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
                expected_tiebreak=int(row["expected_tiebreak"]),
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
    assert evaluated.tiebreak == (row.expected_tiebreak,)
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

    assert (best.cls.name, best.tiebreak) == (row.expected_class, (row.expected_tiebreak,))
