# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.standings (E4.0).

module-skeletons.md S4's ``standings`` surface is this file's
specification: order a finished ride's :class:`EntryResult` snapshots
by their precomputed best hand, resolve byte-identical hand ties by
the ride's configured tie-break order (R-14), flag any pair still
unresolved at the venue's high-card draw as "draw required" and never
order it silently (R-43), and list DNF entries after every ACTIVE
entry (spec §6).

Written FIRST, against a module that does not exist yet: this file is
red until rivercrossing/standings.py lands.

Fixtures build :class:`~rivercrossing.hands.EvaluatedHand` values with
``hands.best_hand`` over ``Card.parse`` codes -- never by mocking
``hands`` (T-10): standings is allowed to call the evaluator's
precomputed hands, and the tests should pin that it orders by them.
Every code string below is already pinned in test_hands.py's own
vectors, so the hand classes asserted here never silently depend on a
regression in the evaluator itself.
"""

import itertools
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rivercrossing import hands
from rivercrossing.cards import Card
from rivercrossing.hands import EvaluatedHand, best_hand
from rivercrossing.standings import (
    DEFAULT_TIEBREAK_ORDER,
    EntryResult,
    TieBreak,
    laps_leaderboard,
    rank,
    time_leaderboard,
)


def _cards(codes: str) -> tuple[Card, ...]:
    """Parse a space-separated string of card codes into Cards."""
    return tuple(Card.parse(code) for code in codes.split())


def _result(  # noqa: PLR0913 -- a fixture builder mirroring S4 EntryResult's 10 fields
    entry_id: str,
    codes: str,
    *,
    laps: int = 0,
    total_time: float = 0.0,
    dnf: bool = False,
) -> EntryResult:
    """Build one EntryResult whose hand is best_hand of *codes*."""
    cards = _cards(codes)
    return EntryResult(
        entry_id=entry_id,
        plate=entry_id,
        name="Rider",
        kind="solo",
        laps=laps,
        total_time=total_time,
        best_lap=0.0,
        cards=cards,
        hand=best_hand(cards),
        dnf=dnf,
    )


# ------------------------------------------------------ basic ordering


def test_rank_best_hand_places_first_worst_last() -> None:
    """Best hand first: royal flush, then straight, then high card."""
    results = [
        _result("1", "AS QD 9H 5C 3S"),
        _result("2", "AS KS QS JS TS"),
        _result("3", "9H 8C 7D 6S 5H"),
    ]

    placed = rank(results)

    assert [p.result.entry_id for p in placed] == ["2", "3", "1"]
    assert [p.place for p in placed] == [1, 2, 3]


def test_rank_partial_four_card_hand_ranks_below_any_five_card_hand() -> None:
    """A capped 4-card ace-high sits under every 5-card hand."""
    five_card = _result("1", "AS QD 9H 5C 3S")
    partial = _result("2", "AS KD QH JC")

    placed = rank([partial, five_card])

    assert [p.result.entry_id for p in placed] == ["1", "2"]


# ------------------------------------------------------ tie resolution


def test_rank_hand_tie_resolved_by_most_laps() -> None:
    """Equal hand ranks: the entry with more laps wins (①)."""
    more_laps = _result("1", "AS KS QS JS TS", laps=5)
    fewer_laps = _result("2", "AH KH QH JH TH", laps=4)

    placed = rank([fewer_laps, more_laps])

    assert [p.result.entry_id for p in placed] == ["1", "2"]
    assert all(p.draw_required is False and p.tie_note is None for p in placed)


def test_rank_hand_tie_resolved_by_shortest_total_time() -> None:
    """Equal hand ranks and laps: the shorter total time wins (②)."""
    fast = _result("1", "AS KS QS JS TS", laps=5, total_time=90.0)
    slow = _result("2", "AH KH QH JH TH", laps=5, total_time=100.0)

    placed = rank([slow, fast])

    assert [p.result.entry_id for p in placed] == ["1", "2"]


def test_rank_unresolved_hand_tie_flags_draw_required_and_shares_place() -> None:
    """Equal hand, laps, time: flagged draw, never silently ordered."""
    first = _result("1", "AS KS QS JS TS", laps=5, total_time=100.0)
    second = _result("2", "AH KH QH JH TH", laps=5, total_time=100.0)

    placed = rank([first, second])

    assert [(p.place, p.draw_required, p.tie_note) for p in placed] == [
        (1, True, "draw required"),
        (1, True, "draw required"),
    ]


def test_rank_draw_pair_share_place_and_next_place_uses_competition_numbering() -> None:
    """A two-way draw at joint 1st leaves the next entry at 3rd."""
    first = _result("1", "AS KS QS JS TS", laps=5, total_time=100.0)
    second = _result("2", "AH KH QH JH TH", laps=5, total_time=100.0)
    next_entry = _result("3", "9H 8C 7D 6S 5H")

    placed = rank([first, second, next_entry])

    assert [p.place for p in placed] == [1, 1, 3]


def test_rank_empty_tiebreak_order_flags_every_hand_tie_as_draw() -> None:
    """With no criteria to resolve by, every hand tie is a draw."""
    first = _result("1", "AS KS QS JS TS", laps=5)
    second = _result("2", "AH KH QH JH TH", laps=4)

    placed = rank([first, second], order=())

    assert [p.draw_required for p in placed] == [True, True]
    assert placed[0].place == placed[1].place == 1


# -------------------------------------------- rule reorder (R-14)


def test_rank_reordered_tiebreak_order_changes_placings() -> None:
    """Swapping MOST_LAPS and TOTAL_TIME re-ranks the same results."""
    first = _result("1", "AS KS QS JS TS", laps=5, total_time=100.0)
    second = _result("2", "AH KH QH JH TH", laps=4, total_time=50.0)
    third = _result("3", "AD KD QD JD TD", laps=4, total_time=60.0)
    by_laps = rank(
        [first, second, third],
        (TieBreak.MOST_LAPS, TieBreak.TOTAL_TIME, TieBreak.HIGH_CARD_DRAW),
    )
    by_time = rank(
        [first, second, third],
        (TieBreak.TOTAL_TIME, TieBreak.MOST_LAPS, TieBreak.HIGH_CARD_DRAW),
    )

    assert [p.result.entry_id for p in by_laps] == ["1", "2", "3"]
    assert [p.result.entry_id for p in by_time] == ["2", "3", "1"]


def test_rank_high_card_draw_first_leaves_every_hand_tie_unresolved() -> None:
    """A front HIGH_CARD_DRAW draws before later criteria apply."""
    first = _result("1", "AS KS QS JS TS", laps=5)
    second = _result("2", "AH KH QH JH TH", laps=4)

    placed = rank([first, second], (TieBreak.HIGH_CARD_DRAW, TieBreak.MOST_LAPS))

    assert all(p.draw_required is True and p.place == 1 for p in placed)


def test_rank_default_order_matches_r14_constant() -> None:
    """The default order is ① laps ② time ③ high-card draw (R-14)."""
    assert DEFAULT_TIEBREAK_ORDER == (
        TieBreak.MOST_LAPS,
        TieBreak.TOTAL_TIME,
        TieBreak.HIGH_CARD_DRAW,
    )


def test_rank_omitted_order_uses_default_constant() -> None:
    """Omitting order ranks like the DEFAULT_TIEBREAK_ORDER."""
    results = [
        _result("1", "AS KS QS JS TS", laps=5, total_time=100.0),
        _result("2", "AH KH QH JH TH", laps=5, total_time=90.0),
    ]

    assert rank(results) == rank(results, DEFAULT_TIEBREAK_ORDER)


# -------------------------------------------------------------- DNF


def test_rank_dnf_entries_listed_last_with_continuing_place_numbers() -> None:
    """DNFs keep all laps/cards and appear after every ACTIVE entry."""
    active = [
        _result("1", "AS KS QS JS TS", laps=5),
        _result("2", "9H 8C 7D 6S 5H", laps=4),
    ]
    dnf = _result("3", "KH KC 5H 5D AS", laps=6, dnf=True)

    placed = rank([*active, dnf])

    assert [(p.place, p.result.entry_id, p.result.dnf) for p in placed] == [
        (1, "1", False),
        (2, "2", False),
        (3, "3", True),
    ]


def test_rank_dnf_entries_never_displace_active_placings() -> None:
    """A laps-leading DNF still ranks behind every ACTIVE entry."""
    dnf = _result("1", "AS KS QS JS TS", laps=99, dnf=True)
    active = _result("2", "9H 8C 7D 6S 5H", laps=4)

    placed = rank([dnf, active])

    assert [(p.place, p.result.entry_id) for p in placed] == [(1, "2"), (2, "1")]


def test_rank_all_dnf_results_keep_input_order_numbered_from_one() -> None:
    """With no ACTIVE entries, DNFs number 1..N in input order."""
    first = _result("1", "AS KS QS JS TS", dnf=True)
    second = _result("2", "9H 8C 7D 6S 5H", dnf=True)

    placed = rank([second, first])

    assert [(p.place, p.result.entry_id) for p in placed] == [(1, "2"), (2, "1")]


# ------------------------------------------------------ empty input


def test_rank_empty_results_returns_empty_list() -> None:
    """rank([]) is [], not an error."""
    assert rank([]) == []


@pytest.mark.parametrize("leaderboard", [laps_leaderboard, time_leaderboard])
def test_leaderboard_empty_results_returns_empty_list(leaderboard: object) -> None:
    """Both leaderboards turn an empty field into an empty board."""
    assert leaderboard([]) == []


# ------------------------------------------------------ leaderboards


def test_laps_leaderboard_orders_by_most_laps_then_shortest_time() -> None:
    """Most laps first; a 3-lap 80s entry beats a 3-lap 90s entry."""
    results = [
        _result("1", "AS KS QS JS TS", laps=5, total_time=100.0),
        _result("2", "9H 8C 7D 6S 5H", laps=3, total_time=90.0),
        _result("3", "JH JC JD 4H 4C", laps=3, total_time=80.0),
        _result("4", "KH KC 5H 5D AS", laps=2, total_time=50.0),
    ]

    placed = laps_leaderboard(results, top=2)

    assert [p.result.entry_id for p in placed] == ["1", "3"]
    assert [p.place for p in placed] == [1, 2]


def test_time_leaderboard_orders_by_most_laps_then_shortest_time() -> None:
    """Binding: the skeleton says "most laps, then time"."""
    many_laps_slow = _result("1", "AS KS QS JS TS", laps=3, total_time=300.0)
    few_laps_fast = _result("2", "9H 8C 7D 6S 5H", laps=2, total_time=50.0)

    placed = time_leaderboard([many_laps_slow, few_laps_fast], top=1)

    assert [p.result.entry_id for p in placed] == ["1"]


@pytest.mark.parametrize(("top", "expected_len"), [(0, 0), (1, 1), (9, 9), (10, 10), (11, 11)])
def test_laps_leaderboard_top_boundary_rows_return_capped_length(
    top: int, expected_len: int
) -> None:
    """Top 0..11 over a 12-entry field caps the board (T-4)."""
    results = [_result(str(i), "AS KS QS JS TS", laps=i) for i in range(12)]

    placed = laps_leaderboard(results, top=top)

    assert len(placed) == expected_len


@pytest.mark.parametrize("leaderboard", [laps_leaderboard, time_leaderboard])
def test_leaderboard_equal_laps_and_time_flags_draw_not_silently_ordered(
    leaderboard: object,
) -> None:
    """A (laps, time) tie is a draw: flagged, shared place (R-43)."""
    first = _result("1", "AS KS QS JS TS", laps=5, total_time=100.0)
    second = _result("2", "AH KH QH JH TH", laps=5, total_time=100.0)

    placed = leaderboard([first, second])

    assert [(p.place, p.draw_required, p.tie_note) for p in placed] == [
        (1, True, "draw required"),
        (1, True, "draw required"),
    ]


@pytest.mark.parametrize("leaderboard", [laps_leaderboard, time_leaderboard])
def test_leaderboard_excludes_dnf_entries(leaderboard: object) -> None:
    """Leaderboards are ACTIVE-only: no DNF entry ever appears."""
    dnf = _result("1", "AS KS QS JS TS", laps=99, dnf=True)
    active = _result("2", "9H 8C 7D 6S 5H", laps=4)

    placed = leaderboard([dnf, active])

    assert [p.result.entry_id for p in placed] == ["2"]


# ----------------------------------------------------- negative path


@pytest.mark.parametrize("leaderboard", [laps_leaderboard, time_leaderboard])
def test_leaderboard_negative_top_raises_value_error(leaderboard: object) -> None:
    """A negative top is rejected, never silently sliced."""
    with pytest.raises(ValueError, match=re.escape("top must be >= 0")):
        leaderboard([_result("1", "AS KS QS JS TS")], top=-1)


def test_rank_unknown_tiebreak_member_raises_type_error() -> None:
    """A non-TieBreak criterion is rejected, never silently ignored."""
    with pytest.raises(TypeError, match=re.escape("non-TieBreak")):
        rank(
            [_result("1", "AS KS QS JS TS")],
            order=(TieBreak.MOST_LAPS, "laps"),
        )


# ---------------------------------------- property invariants (T-7)


_HAND_POOL: tuple[EvaluatedHand, ...] = (
    best_hand(_cards("AS KS QS JS TS")),
    best_hand(_cards("8S 7S 6S 5S 4S")),
    best_hand(_cards("9H 9C 9D 9S KS")),
    best_hand(_cards("JH JC JD 4H 4C")),
    best_hand(_cards("KS JS 8S 6S 2S")),
    best_hand(_cards("9H 8C 7D 6S 5H")),
    best_hand(_cards("7H 7C 7D KS JH")),
    best_hand(_cards("KH KC 5H 5D AS")),
    best_hand(_cards("AH AC KS JD 8H")),
    best_hand(_cards("AS QD 9H 5C 3S")),
)


@st.composite
def _entry_result(draw: st.DrawFn) -> EntryResult:
    """Draw an EntryResult with a hand from the fixed strength pool.

    Times draw as whole seconds so the R-43 property's equality
    comparisons are exact, never float-noise dependent.
    """
    hand: EvaluatedHand = draw(st.sampled_from(_HAND_POOL))
    return EntryResult(
        entry_id=draw(st.uuids().map(str)),
        plate=draw(st.integers(min_value=1, max_value=9999).map(str)),
        name=draw(st.text(alphabet="abcdefgh", min_size=1, max_size=8)),
        kind=draw(st.sampled_from(["solo", "team"])),
        laps=draw(st.integers(min_value=0, max_value=8)),
        total_time=float(draw(st.integers(min_value=0, max_value=3600))),
        best_lap=float(draw(st.integers(min_value=0, max_value=900))),
        cards=hand.best5,
        hand=hand,
        dnf=draw(st.booleans()),
    )


@given(results=st.lists(_entry_result(), min_size=0, max_size=8))
@settings(max_examples=50, deadline=None)
def test_rank_output_orders_active_hands_best_first(results: list[EntryResult]) -> None:
    """Hand strength never increases down the ACTIVE stretch."""
    placed = rank(results)
    active_hands = [p.result.hand for p in placed if not p.result.dnf]

    assert all(
        hands.compare(earlier, later) >= 0 for earlier, later in itertools.pairwise(active_hands)
    )


@given(results=st.lists(_entry_result(), min_size=0, max_size=8))
@settings(max_examples=50, deadline=None)
def test_rank_identical_hand_laps_time_never_silently_ordered(
    results: list[EntryResult],
) -> None:
    """An unresolved pair is marked draw with a shared place (R-43)."""
    placed = rank(results)
    by_id = {p.result.entry_id: p for p in placed}
    unresolved = [
        (first, second)
        for first, second in itertools.combinations(results, 2)
        if not first.dnf
        and not second.dnf
        and hands.compare(first.hand, second.hand) == 0
        and first.laps == second.laps
        and first.total_time == second.total_time
    ]

    assert all(
        by_id[first.entry_id].draw_required is True
        and by_id[second.entry_id].draw_required is True
        for first, second in unresolved
    )
    assert all(
        by_id[first.entry_id].place == by_id[second.entry_id].place for first, second in unresolved
    )


@given(results=st.lists(_entry_result(), min_size=0, max_size=8))
@settings(max_examples=50, deadline=None)
def test_laps_leaderboard_orders_laps_desc_then_time_asc_and_respects_top(
    results: list[EntryResult],
) -> None:
    """The board sorts by (-laps, time) and never exceeds top."""
    placed = laps_leaderboard(results, top=3)
    keys = [(-p.result.laps, p.result.total_time) for p in placed]

    assert len(placed) <= 3
    assert keys == sorted(keys)
