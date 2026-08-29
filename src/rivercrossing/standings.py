# SPDX-License-Identifier: GPL-3.0-only
"""Ride standings: ranking, tie-breaks and leaderboards (spec §5/§6).

EPIC 4's pull-forward of the ranking core: order a finished ride's
:class:`EntryResult` snapshots by their *precomputed* best hand --
the caller (EPIC 4's ``RideEngine.snapshot``) runs
``hands.best_hand`` and stores the result, and :func:`rank` only
orders by it via ``hands.compare``, never re-deriving a hand from
``cards``. The human-readable hand-name renderer (:func:`hand_name`)
is EPIC 6 display copy (decision D1): one title-case em-dash style,
vocabulary pinned by the golden exports, that the results window and
exports both consume.

Handling rules, in order of application:

- ACTIVE entries sort best hand first (``placed[0]`` is the winner).
  Byte-identical hand ranks tie; each criterion in ``order`` resolves
  a tie in sequence -- ``MOST_LAPS`` (more laps wins), ``TOTAL_TIME``
  (shorter total time wins) -- until ``HIGH_CARD_DRAW``, the venue
  event that resolves nothing: an entry pair still unresolved there is
  flagged ``draw_required=True`` with ``tie_note`` "draw required" and
  must never be ordered silently (R-43). ``place`` is 1-based, and a
  draw pair shares its place; the run after a two-way draw starts one
  place past the whole run (competition numbering: 1, 2, 2, 4).

- DNF entries keep all laps/cards (spec §6 "DNF keeps all laps/cards,
  listed and marked"), appear after every ACTIVE entry, and never
  displace an ACTIVE placing. Their places continue from
  ``len(ACTIVE) + 1`` in input order -- pinned by
  ``test_rank_dnf_entries_listed_last_with_continuing_place_numbers``
  and ``test_rank_all_dnf_results_keep_input_order_numbered_from_one``
  -- and they carry no tie note (their DNF state is the mark).

- The two leaderboards share spec §6's board rule, "laps DESC, then
  total ASC", capped at ``top``, ACTIVE entries only; a (laps, time)
  tie is a draw, marked exactly as in :func:`rank`. The skeleton's
  inline comment on :func:`time_leaderboard` -- "most laps, then
  time" -- is what makes the two boards identical here; the results
  window's "Fastest-time" label is a display concern (which columns
  that board shows), not a different order, and lives in EPIC 6.

This module imports ``hands`` and ``cards`` only (module-skeletons.md
S3, R-71): standings sits below ``ride``, ``roster`` and the UI, so it
can never depend on any of them. ``kind`` is the entry-type spelling
("solo"/"team") as a plain string for the same reason. The
:class:`TieBreak` values match ``ride.py``'s own ``TIEBREAK_*``
spellings so a stored ``tiebreak_order`` maps onto these members
without standings importing ``ride``; the ride-setup dialog re-ranks
live by passing a reordered tuple (R-14).
:func:`tiebreak_order_from_spellings` is that mapping as a function:
a stored order of ``ride.py`` spellings converts to the member tuple
:func:`rank` takes (empty = all-draw).
"""

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from functools import cmp_to_key
from typing import TYPE_CHECKING, cast

from rivercrossing.hands import EvaluatedHand, HandClass, compare

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rivercrossing.cards import Card, Rank

__all__ = [
    "DEFAULT_TIEBREAK_ORDER",
    "EntryResult",
    "Placed",
    "TieBreak",
    "hand_name",
    "laps_leaderboard",
    "rank",
    "tiebreak_order_from_spellings",
    "time_leaderboard",
]

DRAW_TIE_NOTE = "draw required"


class TieBreak(Enum):
    """One ride tie-break criterion (spec §5 ①②③, R-14).

    Values are ride.py's own ``TIEBREAK_*`` spellings ("laps",
    "total_time", "high_card"), duplicated here because standings may
    not import ``ride`` (module docstring); the member name for the
    venue event is ``HIGH_CARD_DRAW`` even though its stored spelling
    is ride's "high_card".
    """

    MOST_LAPS = "laps"
    TOTAL_TIME = "total_time"
    HIGH_CARD_DRAW = "high_card"


# R-14's default order, and :func:`rank`'s own default argument: ① most
# laps ② shortest total time ③ high-card draw (venue event, flags).
DEFAULT_TIEBREAK_ORDER: tuple[TieBreak, ...] = (
    TieBreak.MOST_LAPS,
    TieBreak.TOTAL_TIME,
    TieBreak.HIGH_CARD_DRAW,
)


def tiebreak_order_from_spellings(spellings: Sequence[str]) -> tuple[TieBreak, ...]:
    """Convert a stored ride.py tie-break order onto TieBreak members.

    ``ride.py`` persists its order as the ``TIEBREAK_*`` string
    spellings ("laps"/"total_time"/"high_card" -- module docstring);
    this maps each onto its :class:`TieBreak` member so a stored order
    can drive :func:`rank` directly. An empty *spellings* is valid: a
    ride with no criteria, where every hand tie is a draw.

    Args:
        spellings: The stored spellings, in priority order.

    Returns:
        The matching member tuple for :func:`rank`.

    Raises:
        ValueError: *spellings* holds a spelling no :class:`TieBreak`
            member has.
    """
    by_value = {member.value: member for member in TieBreak}
    order: list[TieBreak] = []
    for spelling in spellings:
        member = by_value.get(spelling)
        if member is None:
            msg = f"unknown tie-break spelling: {spelling!r}"
            raise ValueError(msg)
        order.append(member)
    return tuple(order)


@dataclass(frozen=True)
class EntryResult:
    """One entry's finished-ride snapshot (module-skeletons.md S4).

    ``hand`` is precomputed by the caller (``RideEngine.snapshot``);
    standings orders by it and never re-derives it from ``cards``.
    ``kind`` is the entry-type spelling ("solo"/"team") as a plain
    string, since standings must not import ``roster`` (R-71).
    ``total_time``/``best_lap`` are seconds; ``laps`` counts completed
    laps.
    """

    entry_id: str
    plate: str
    name: str
    kind: str
    laps: int
    total_time: float
    best_lap: float
    cards: tuple[Card, ...]
    hand: EvaluatedHand
    dnf: bool


@dataclass(frozen=True)
class Placed:
    """One ranked result: its 1-based place plus tie markings.

    ``draw_required`` is the R-43 flag -- the pair was not resolved by
    any configured tie-break and must never be ordered silently; the
    results window badges the row (xrc-windows.md). ``tie_note`` names
    the flag ("draw required") when set, else None.
    """

    place: int
    result: EntryResult
    tie_note: str | None
    draw_required: bool


def _validate_order(order: Sequence[object]) -> None:
    """Raise TypeError unless *order* holds only TieBreak members.

    A foreign criterion (for example a raw string spelling) would
    otherwise be silently skipped, leaving ties unresolved that the
    caller asked to break -- the failure this guard exists to surface.
    The parameter is ``object`` rather than ``TieBreak`` so the
    isinstance check below is meaningful at runtime and to mypy.
    """
    for criterion in order:
        if not isinstance(criterion, TieBreak):
            msg = f"order contains a non-TieBreak member: {criterion!r}"
            raise TypeError(msg)


def _compare_hands(a: EntryResult, b: EntryResult) -> int:
    """Compare two results' precomputed hands, worst to best."""
    return compare(a.hand, b.hand)


def _resolved_runs(
    group: Sequence[EntryResult], order: Sequence[TieBreak]
) -> list[list[EntryResult]]:
    """Split one hand-tie *group* into place runs under *order*.

    Every resolver before the first ``HIGH_CARD_DRAW`` contributes one
    element to a composite sort key; entries still equal on all of
    them afterwards form one draw run -- they share a place and are
    flagged, never silently ordered (R-43).
    """
    resolvers: list[TieBreak] = []
    for criterion in order:
        if criterion is TieBreak.HIGH_CARD_DRAW:
            break
        resolvers.append(criterion)

    def key(result: EntryResult) -> tuple[int | float, ...]:
        values: list[int | float] = []
        for resolver in resolvers:
            if resolver is TieBreak.MOST_LAPS:
                values.append(-result.laps)
            else:
                values.append(result.total_time)
        return tuple(values)

    ordered = sorted(group, key=key)
    runs: list[list[EntryResult]] = []
    for result in ordered:
        if runs and key(runs[-1][-1]) == key(result):
            runs[-1].append(result)
        else:
            runs.append([result])
    return runs


def _place_runs(runs: Sequence[Sequence[EntryResult]]) -> list[Placed]:
    """Assign 1-based places to *runs*; a run's entries share its place.

    Competition numbering: the run after a two-way draw starts one
    place past the whole draw, so places read 1, 2, 2, 4 rather than
    dense 1, 2, 2, 3.
    """
    placed: list[Placed] = []
    place = 1
    for run in runs:
        is_draw = len(run) > 1
        tie_note = DRAW_TIE_NOTE if is_draw else None
        placed.extend(
            Placed(place=place, result=result, tie_note=tie_note, draw_required=is_draw)
            for result in run
        )
        place += len(run)
    return placed


def rank(
    results: Sequence[EntryResult],
    order: tuple[TieBreak, ...] = DEFAULT_TIEBREAK_ORDER,
) -> list[Placed]:
    """Rank *results* best hand first, ties resolved by *order* (R-14).

    ACTIVE entries sort by precomputed hand strength, index 0 the
    winner; byte-identical hand ranks tie and resolve by *order* in
    sequence -- ``MOST_LAPS`` more laps wins, ``TOTAL_TIME`` shorter
    total time wins -- until ``HIGH_CARD_DRAW``, where an unresolved
    pair is flagged ``draw_required`` and never silently ordered
    (R-43). DNF entries keep all laps/cards, appear after every ACTIVE
    entry with places continuing from ``len(ACTIVE) + 1``, and never
    displace an ACTIVE placing (spec §6; module docstring pins the
    exact numbering).

    Args:
        results: The ride's finished snapshots, in any order.
        order: Tie-break criteria in priority sequence; defaults to
            ``(MOST_LAPS, TOTAL_TIME, HIGH_CARD_DRAW)`` (R-14).

    Returns:
        One :class:`Placed` per result, best hand first, DNFs last.

    Raises:
        TypeError: *order* contains something other than a
            :class:`TieBreak` member.
    """
    _validate_order(order)
    active = [result for result in results if not result.dnf]
    dnf = [result for result in results if result.dnf]

    active.sort(key=cmp_to_key(_compare_hands), reverse=True)
    hand_groups: list[list[EntryResult]] = []
    for result in active:
        if hand_groups and compare(hand_groups[-1][-1].hand, result.hand) == 0:
            hand_groups[-1].append(result)
        else:
            hand_groups.append([result])

    runs = [run for group in hand_groups for run in _resolved_runs(group, order)]
    placed = _place_runs(runs)

    dnf_place = len(active) + 1
    for result in dnf:
        placed.append(Placed(place=dnf_place, result=result, tie_note=None, draw_required=False))
        dnf_place += 1
    return placed


def _lap_time_key(result: EntryResult) -> tuple[int, float]:
    """Return the spec §6 board key: laps DESC, then total ASC."""
    return (-result.laps, result.total_time)


def _leaderboard(results: Sequence[EntryResult], top: int) -> list[Placed]:
    """Rank ACTIVE entries by most laps, then shortest time (spec §6).

    A (laps, total_time) tie is a draw: flagged with a shared place,
    never silently ordered (R-43). The board is capped at *top*
    entries.

    Raises:
        ValueError: *top* is negative.
    """
    if top < 0:
        msg = f"top must be >= 0, got {top}"
        raise ValueError(msg)
    active = [result for result in results if not result.dnf]
    ordered = sorted(active, key=_lap_time_key)
    runs: list[list[EntryResult]] = []
    for result in ordered:
        if runs and _lap_time_key(runs[-1][-1]) == _lap_time_key(result):
            runs[-1].append(result)
        else:
            runs.append([result])
    return _place_runs(runs)[:top]


def laps_leaderboard(results: Sequence[EntryResult], top: int = 10) -> list[Placed]:
    """Rank the ACTIVE entries by most laps, then shortest total time.

    Capped at *top*; a (laps, time) tie is a draw, never silently
    ordered. DNF entries never appear on a board.

    Raises:
        ValueError: *top* is negative.
    """
    return _leaderboard(results, top)


def time_leaderboard(results: Sequence[EntryResult], top: int = 10) -> list[Placed]:
    """Rank the ACTIVE entries by most laps, then shortest total time.

    The skeleton's own inline comment is binding -- "most laps, then
    time" -- so this shares :func:`laps_leaderboard`'s exact order and
    the same draw/flag rules; the results window's "Fastest-time"
    label is EPIC 6 display copy, not a different sort.

    Raises:
        ValueError: *top* is negative.
    """
    return _leaderboard(results, top)


# -------------------------------------------------- hand names (E6.1.1)

# The display vocabulary: each rank value (cards.Rank's own integer,
# 2..14, joker 0) maps to the singular and plural words the golden
# exports pin. The value -> letter identity (J=11 .. A=14) is
# cards.Rank's and never repeated here; these are the prose word forms
# only, and the two tables exist because English plurals are irregular
# (Fives, Sixes, Nines, Tens) rather than a rule worth encoding.
_RANK_WORD: dict[int, str] = {
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
    10: "Ten",
    11: "Jack",
    12: "Queen",
    13: "King",
    14: "Ace",
}

_RANK_PLURAL: dict[int, str] = {
    2: "Twos",
    3: "Threes",
    4: "Fours",
    5: "Fives",
    6: "Sixes",
    7: "Sevens",
    8: "Eights",
    9: "Nines",
    10: "Tens",
    11: "Jacks",
    12: "Queens",
    13: "Kings",
    14: "Aces",
}

# The four classes built from a same-rank group, each named from that
# group's rank in the plural: "Pair -- Aces", "Three of a Kind --
# Sevens", "Four of a Kind -- Nines", "Five of a Kind -- Aces".
_GROUP_LABELS: dict[HandClass, str] = {
    HandClass.PAIR: "Pair",
    HandClass.TRIPS: "Three of a Kind",
    HandClass.QUADS: "Four of a Kind",
    HandClass.FIVE_OF_A_KIND: "Five of a Kind",
}
_GROUP_SIZE: dict[HandClass, int] = {
    HandClass.PAIR: 2,
    HandClass.TRIPS: 3,
    HandClass.QUADS: 4,
    HandClass.FIVE_OF_A_KIND: 5,
}

# The FULL_HOUSE branch needs its own two group sizes (3 over 2), and
# the TWO_PAIR branch filters on the pair count -- named here so no
# bare 2/3 sits in a comparison (PLR2004), matching hands.py's own
# _PAIR_COUNT/_TRIPS_COUNT convention.
_PAIR_COUNT = 2
_TRIPS_COUNT = 3


def _effective_ranks(hand: EvaluatedHand) -> list[int]:
    """List the rank values *hand*'s cards actually play as.

    ``best5`` still holds raw joker placeholders (rank None); each
    joker's resolution from ``jokers_played_as`` stands in for it, so a
    joker-completed hand names its true kickers (decision D1).
    """
    played = (*hand.best5, *hand.jokers_played_as)
    return [cast("Rank", card.rank).value for card in played if not card.joker]


def _rank_with_count(ranks: Sequence[int], count: int) -> int:
    """Return the one rank appearing exactly *count* times in *ranks*.

    The evaluated :class:`HandClass` guarantees such a rank exists; a
    miss is a hands bug and surfaces as :class:`KeyError` -- internal
    invariants fail loudly, they are not guessed at (module style).
    """
    by_count = {n: rank for rank, n in Counter(ranks).items()}
    return by_count[count]


def _straight_high(ranks: Sequence[int]) -> int:
    """Return a straight's display high rank, mindful of the wheel.

    A-2-3-4-5 plays as a 5-high straight, never ace-high (spec §5);
    every other straight names its top rank.
    """
    if set(ranks) == {2, 3, 4, 5, 14}:
        return 5
    return max(ranks)


def hand_name(hand: EvaluatedHand) -> str:
    """Return *hand*'s title-case prose name (decision D1, spec §5).

    The one em-dash style the results window and exports share, with
    the exact vocabulary the golden exports pin: "High Card -- Ace",
    "Pair -- Aces", "Two Pair -- Kings & Fives", "Full House -- Aces
    over Fours", "Straight -- Nine high" (wheel = "Five high"), and
    "Royal Flush" with no kicker suffix. A joker's resolution
    (``jokers_played_as``) supplies its rank, so a joker-completed hand
    names its true kickers. Fewer than 5 cards render the same prose
    form as the class they make, with no marker.

    Args:
        hand: The evaluated hand to name.

    Returns:
        The prose name, e.g. ``"Four of a Kind -- Nines"``.

    Raises:
        ValueError: *hand* has no cards at all (``best_hand(())``
            yields one); there is no rank to name.
    """
    ranks = _effective_ranks(hand)
    if not ranks:
        msg = "cannot name an empty hand"
        raise ValueError(msg)
    cls = hand.cls
    if cls in _GROUP_LABELS:
        rank = _rank_with_count(ranks, _GROUP_SIZE[cls])
        return f"{_GROUP_LABELS[cls]} — {_RANK_PLURAL[rank]}"
    if cls is HandClass.TWO_PAIR:
        pair_ranks = sorted(rank for rank, n in Counter(ranks).items() if n == _PAIR_COUNT)
        return f"Two Pair — {_RANK_PLURAL[pair_ranks[-1]]} & {_RANK_PLURAL[pair_ranks[0]]}"
    if cls is HandClass.FULL_HOUSE:
        trips = _rank_with_count(ranks, _TRIPS_COUNT)
        pair = _rank_with_count(ranks, _PAIR_COUNT)
        return f"Full House — {_RANK_PLURAL[trips]} over {_RANK_PLURAL[pair]}"
    if cls in (HandClass.STRAIGHT, HandClass.STRAIGHT_FLUSH):
        base = "Straight" if cls is HandClass.STRAIGHT else "Straight Flush"
        return f"{base} — {_RANK_WORD[_straight_high(ranks)]} high"
    if cls in (HandClass.HIGH_CARD, HandClass.FLUSH):
        base = "High Card" if cls is HandClass.HIGH_CARD else "Flush"
        # HIGH_CARD names the bare top rank, FLUSH the same rank plus
        # " high" -- the two kicker classes only differ in that suffix.
        suffix = "" if cls is HandClass.HIGH_CARD else " high"
        return f"{base} — {_RANK_WORD[max(ranks)]}{suffix}"
    # ROYAL_FLUSH names nothing -- it is the one kickerless class and
    # the fall-through above.
    return "Royal Flush"
