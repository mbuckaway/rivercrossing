# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.hands (E2.1.1) -- written first, red.

``tests/vectors/rank_sweep.csv`` (tools/gen_rank_vectors.py) is this
suite's specification: one representative 5-card hand per phevaluator
rank, all 7,462 of them, with a hand class computed independently of
phevaluator (module docstring there; tests/unit/test_gen_rank_vectors.py
covers the generator itself). The sweep test below is this task's
named acceptance test -- "all 7,462 distinct ranks hit exactly once
across the class sweep" -- everything else pins the other brief-named
behaviours (the wheel, flush over straight, the negative card-count
case) plus the card model this module consumes.
"""

import csv
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

_RANK_SWEEP_CSV = Path(__file__).resolve().parents[2] / "tests" / "vectors" / "rank_sweep.csv"

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


def test_eval5_joker_card_raises_invalid_hand_error() -> None:
    """eval5 rejects a joker; the wild layer lands in E2.1.2."""
    cards = [*_cards("2C 3D 4H 5S"), Card(rank=None, suit=None, joker=True)]

    with pytest.raises(
        InvalidHandError,
        match=re.escape("does not accept jokers (E2.1.2): got JK"),
    ):
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
