# SPDX-License-Identifier: GPL-3.0-only
"""Ride standings: ranking, tie-breaks and leaderboards (spec §5/§6).

EPIC 4's pull-forward of the ranking core: order a finished ride's
:class:`EntryResult` snapshots by their *precomputed* best hand --
the caller (EPIC 4's ``RideEngine.snapshot``) runs
``hands.best_hand`` and stores the result, and :func:`rank` only
orders by it via ``hands.compare``, never re-deriving a hand from
``cards``. The human-readable hand-name renderer ("Four of a kind,
kings") is EPIC 6 display copy and deliberately absent.

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
"""

from dataclasses import dataclass
from enum import Enum
from functools import cmp_to_key
from typing import TYPE_CHECKING

from rivercrossing.hands import EvaluatedHand, compare

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rivercrossing.cards import Card

__all__ = [
    "DEFAULT_TIEBREAK_ORDER",
    "EntryResult",
    "Placed",
    "TieBreak",
    "laps_leaderboard",
    "rank",
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
