# SPDX-License-Identifier: GPL-3.0-only
"""Hypothesis property suite for csvio's export/import round trip.

task-briefs.md E3.3.3's own property: "random roster -> export ->
preview shows 0 conflicts -> commit -> equal models."
:func:`_random_roster` builds a valid ``Roster`` directly through its
own mutators (never a transient size-1 team -- that shape belongs to
tests/property/test_roster_properties.py, not this round trip), for
either plate model, with a random mix of solo entries and teams.

**Name/plate alphabet, and why.** Rider and display names are drawn
from ``a``-``z`` only, matching test_roster_properties.py's own
``_NAME`` strategy -- deliberately, not by coincidence. csvio's
``_field()`` strips every parsed value, so a name with leading or
trailing whitespace would silently lose it on the way back through
``preview()``, breaking exact equality without csvio doing anything
wrong; restricting the alphabet to non-whitespace characters
sidesteps that (and comma/quote CSV-escaping, which is Python's
``csv`` module's own well-tested concern, not this one's). Plates are
unique integers drawn per roster, exactly as many as the roster's
plate namespace needs (one per relay entry, one per pooled rider).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from hypothesis import given, settings
from hypothesis import strategies as st

from rivercrossing.csvio import commit, export, preview
from rivercrossing.roster import EntryMode, EntryType, PlateModel, Rider, Roster

if TYPE_CHECKING:
    from collections.abc import Iterator

_NAME = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=6
)
_MAX_TEAM_SIZE = st.integers(min_value=2, max_value=6)
_SOLO_COUNT = st.integers(min_value=0, max_value=5)


@dataclass
class _DrawCtx:
    """Shared state one _random_roster() draw threads through."""

    draw: st.DrawFn
    roster: Roster
    plates: Iterator[str]


@st.composite
def _random_roster(draw: st.DrawFn) -> Roster:
    """Build a random, valid Roster for either plate model (E3.3.3)."""
    plate_model = draw(st.sampled_from([PlateModel.RIDER_POOLED, PlateModel.TEAM_RELAY]))
    max_team_size = draw(_MAX_TEAM_SIZE)
    solo_count = draw(_SOLO_COUNT)
    team_sizes = draw(st.lists(st.integers(min_value=2, max_value=max_team_size), max_size=4))
    # rider_pooled's own team_name *is* the re-import merge key (S1), so
    # two teams sharing one name would collapse into one group on the
    # round trip -- a real CSV-format ambiguity, not a csvio bug. Unique
    # names sidestep it; harmless for team_relay, which merges by plate.
    team_names = draw(
        st.lists(_NAME, min_size=len(team_sizes), max_size=len(team_sizes), unique=True)
    )
    plates_needed = solo_count + (
        len(team_sizes) if plate_model is PlateModel.TEAM_RELAY else sum(team_sizes)
    )
    plates = draw(
        st.lists(
            st.integers(min_value=1, max_value=500).map(str),
            min_size=plates_needed,
            max_size=plates_needed,
            unique=True,
        )
    )
    roster = Roster(
        entry_mode=EntryMode.MIXED, max_team_size=max_team_size, plate_model=plate_model
    )
    ctx = _DrawCtx(draw=draw, roster=roster, plates=iter(plates))
    _draw_solos(ctx, solo_count)
    _draw_teams(ctx, team_sizes, team_names)
    return roster


def _draw_solos(ctx: _DrawCtx, count: int) -> None:
    """Create *count* solo entries, drawing each name and plate."""
    for _ in range(count):
        ctx.roster.create_solo_entry(name=ctx.draw(_NAME), plate=next(ctx.plates))


def _draw_teams(ctx: _DrawCtx, sizes: list[int], names: list[str]) -> None:
    """Create one team entry per (size, unique name) pair (E3.3.3)."""
    for size, display_name in zip(sizes, names, strict=True):
        riders = _draw_team_riders(ctx, size)
        team_plate = next(ctx.plates) if ctx.roster.plate_model is PlateModel.TEAM_RELAY else None
        ctx.roster.create_team_entry(display_name=display_name, riders=riders, plate=team_plate)


def _draw_team_riders(ctx: _DrawCtx, size: int) -> list[Rider]:
    """Return *size* fresh riders, plated only under rider_pooled."""
    riders = []
    for _ in range(size):
        plate = next(ctx.plates) if ctx.roster.plate_model is PlateModel.RIDER_POOLED else None
        riders.append(Rider(name=ctx.draw(_NAME), plate=plate))
    return riders


def _projected_entries(roster: Roster) -> frozenset[tuple[object, ...]]:
    """Return a value-comparable projection of every entry in *roster*.

    Entry/Rider compare by identity (roster.py's own ``eq=False``), so
    two structurally-equal rosters never compare ``==`` directly; each
    entry becomes a plain, hashable tuple instead -- plate, type,
    display_name, notes, and its riders as a name/plate pair set
    (unordered: row order isn't part of the property, membership is).
    """
    return frozenset(
        (
            entry.plate,
            entry.type,
            entry.display_name,
            entry.notes,
            frozenset((rider.name, rider.plate) for rider in entry.riders),
        )
        for entry in roster.entries
    )


@given(source=_random_roster())
@settings(max_examples=50, deadline=None)
def test_export_then_preview_then_commit_reconstructs_an_equal_roster(source: Roster) -> None:
    """Export, preview (0 conflicts), commit rebuild the same roster."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "riders.csv"
        export(source, path)
        target = Roster(
            entry_mode=EntryMode.MIXED,
            max_team_size=source.max_team_size,
            plate_model=source.plate_model,
        )

        result = preview(path, target)
        commit(result)

    assert (result.conflicts, _projected_entries(target)) == ((), _projected_entries(source))


@given(source=_random_roster())
@settings(max_examples=50, deadline=None)
def test_export_then_preview_reports_rider_and_team_counts_unchanged(source: Roster) -> None:
    """The preview's own rider/team counts match the source roster's."""
    source_riders = sum(entry.team_size for entry in source.entries)
    source_teams = sum(1 for entry in source.entries if entry.type is EntryType.TEAM)
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "riders.csv"
        export(source, path)
        target = Roster(
            entry_mode=EntryMode.MIXED,
            max_team_size=source.max_team_size,
            plate_model=source.plate_model,
        )

        result = preview(path, target)

    assert (result.rider_count, result.team_count) == (source_riders, source_teams)
