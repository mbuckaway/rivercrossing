# SPDX-License-Identifier: GPL-3.0-only
"""Rider-issue report: the defects a roster still carries (spec S2/S3).

Before a ride starts an operator can open the rider editor on a roster
rebuilt from a CSV import or hand-entered one rider at a time.
:func:`rider_issues` is that editor's pre-flight check: it walks the
in-memory :class:`~rivercrossing.roster.Roster` and reports every
defect still present, in one stable order a UI can render and a test
can assert:

    1. team-of-one              -- a TEAM entry below MIN_TEAM_SIZE
    2. missing-name             -- a rider whose full_name is empty
    3. missing-number           -- a pooled rider with a blank plate
    4. duplicate-name           -- a duplicate rider name
    5. duplicate-team-name      -- two teams, one normalized name
    6. near-duplicate-team-name -- two teams, one fuzzy key
    7. duplicate-number         -- one plate claimed more than once

The checks are deliberately independent: one rider can carry several
defects, and each check reports only its own. The order is the stable
report order, cheapest structural problems first, plate-namespace
collisions last. The team-name checks reuse the CSV importer's own
identity rules -- :func:`~rivercrossing.roster.team_name_key` for the
fuzzy key (its shared public home) and the same trim/collapse/lower
normalization -- so the preview warnings and this report cannot drift.
A near-duplicate team name is a *warning*, not a hard defect, and says
so with the ``⚠ warning: `` message prefix the CSV preview renders
warnings with rather than a separate severity field.

Two further defects were found during this work but are deliberately
not checked, because no path through the app can produce them. A
**blank team name** never reaches a live roster: the Teams editor
refuses to save one ("enter a team name"), and the CSV importer
reports a nameless team row as a ``missing-name`` conflict. A team
**over max_team_size** cannot be built either:
:meth:`~rivercrossing.roster.Roster.create_team_entry` and
:meth:`~rivercrossing.roster.Roster.move_rider` both enforce R-12's
upper bound at mutation time, and
:meth:`~rivercrossing.roster.Roster.validate_for_start` only ever has
to re-check the lower bound.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rivercrossing.roster import (
    MIN_TEAM_SIZE,
    Entry,
    EntryType,
    PlateModel,
    Rider,
    Roster,
    rider_name_key,
    team_name_key,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["RiderIssue", "rider_issues"]


@dataclass(frozen=True)
class RiderIssue:
    """One defect currently present on a roster's entries or riders.

    ``entry`` is the offending :class:`~rivercrossing.roster.Entry`.
    ``rider`` names the offending rider when the issue is rider-scoped
    -- ``None`` for team-of-one and entry-scoped duplicate-number.
    ``kind`` is the stable machine-readable token; ``message`` is the
    operator-facing text.
    """

    entry: Entry
    rider: Rider | None
    kind: str
    message: str


def rider_issues(roster: Roster) -> tuple[RiderIssue, ...]:
    """Report every rider issue *roster* currently carries, in order.

    The five checks run back-to-back and append in the fixed order
    above, so the result is a stable sequence for display and tests.

    Returns:
        One :class:`RiderIssue` per defect. An empty tuple is a clean
        roster.
    """
    issues: list[RiderIssue] = []
    issues.extend(_team_of_one_issues(roster))
    issues.extend(_missing_name_issues(roster))
    issues.extend(_missing_number_issues(roster))
    issues.extend(_duplicate_name_issues(roster))
    issues.extend(_team_name_issues(roster))
    issues.extend(_duplicate_number_issues(roster))
    return tuple(issues)


def _team_of_one_issues(roster: Roster) -> Iterator[RiderIssue]:
    """Yield a team-of-one issue per TEAM entry below MIN_TEAM_SIZE."""
    for entry in roster.entries:
        if entry.type is EntryType.TEAM and entry.team_size < MIN_TEAM_SIZE:
            yield RiderIssue(
                entry=entry,
                rider=None,
                kind="team-of-one",
                message=f"team size must be at least {MIN_TEAM_SIZE}, got {entry.team_size}",
            )


def _missing_name_issues(roster: Roster) -> Iterator[RiderIssue]:
    """Yield a missing-name issue per rider whose full_name is empty."""
    for entry in roster.entries:
        for rider in entry.riders:
            if not rider.full_name:
                yield RiderIssue(
                    entry=entry,
                    rider=rider,
                    kind="missing-name",
                    message="missing name",
                )


def _missing_number_issues(roster: Roster) -> Iterator[RiderIssue]:
    """Yield a missing-number issue per plateless rider_pooled rider.

    A team_relay rider is legitimately plateless -- the plate belongs
    to the entry -- so this check is pooled-only.
    """
    if roster.plate_model is not PlateModel.RIDER_POOLED:
        return
    for entry in roster.entries:
        for rider in entry.riders:
            if rider.plate is None or not rider.plate.strip():
                yield RiderIssue(
                    entry=entry,
                    rider=rider,
                    kind="missing-number",
                    message="missing number",
                )


def _duplicate_name_issues(roster: Roster) -> Iterator[RiderIssue]:
    """Yield one issue per case/whitespace-duplicate rider name.

    Names compare through :func:`~rivercrossing.roster.rider_name_key`,
    so "Mary Anne Knibbe" and "  MARY   ANNE KNIBBE " are one rider.
    An empty name is a missing-name defect, not a duplicate, so it
    never participates here.
    """
    seen: set[str] = set()
    for entry in roster.entries:
        for rider in entry.riders:
            if not rider.full_name:
                continue
            key = rider_name_key(rider.first_name, rider.last_name)
            if key in seen:
                yield RiderIssue(
                    entry=entry,
                    rider=rider,
                    kind="duplicate-name",
                    message=f"duplicate rider name {rider.full_name}",
                )
            else:
                seen.add(key)


def _normalized_team_name(name: str) -> str:
    """Return *name*'s grouping identity, as the CSV importer forms it.

    Trim, collapse every run of internal whitespace to one space, and
    lowercase -- the same rule
    :func:`~rivercrossing.csvio._normalize_team_name` applies, so the
    importer's team grouping and this report's duplicate check agree on
    when two names are one team.
    """
    return " ".join(name.split()).lower()


def _team_name_issues(roster: Roster) -> Iterator[RiderIssue]:
    """Yield one issue per duplicate or near-duplicate team name.

    Two TEAM entries whose display names share one normalized form
    (:func:`_normalized_team_name`) yield a hard ``duplicate-team-name``
    on the later entry. The first entry of each normalized name is then
    grouped by the CSV importer's fuzzy key
    (:func:`~rivercrossing.roster.team_name_key`); a later name sharing
    a key with a *distinct* normalized name yields a
    ``near-duplicate-team-name`` warning, never also a hard duplicate.
    Solo entries are out of scope -- their display name mirrors a rider
    and is already covered by ``duplicate-name``.
    """
    first_by_name: dict[str, Entry] = {}
    order: list[str] = []
    for entry in roster.entries:
        if entry.type is not EntryType.TEAM:
            continue
        normalized = _normalized_team_name(entry.display_name)
        if normalized in first_by_name:
            yield RiderIssue(
                entry=entry,
                rider=None,
                kind="duplicate-team-name",
                message=f'duplicate team name "{entry.display_name}"',
            )
            continue
        first_by_name[normalized] = entry
        order.append(normalized)
    groups: dict[str, list[str]] = {}
    for name in order:
        groups.setdefault(team_name_key(name), []).append(name)
    for names in groups.values():
        for later in names[1:]:
            yield RiderIssue(
                entry=first_by_name[later],
                rider=None,
                kind="near-duplicate-team-name",
                message=(f'⚠ warning: possible duplicate team name: "{names[0]}" and "{later}"'),
            )


def _duplicate_number_issues(roster: Roster) -> Iterator[RiderIssue]:
    """Yield one issue per plate value claimed more than once (R-20)."""
    seen: set[str] = set()
    for entry, rider, plate in _number_claims(roster):
        if plate in seen:
            yield RiderIssue(
                entry=entry,
                rider=rider,
                kind="duplicate-number",
                message=f"duplicate number {plate}",
            )
        else:
            seen.add(plate)


def _number_claims(roster: Roster) -> Iterator[tuple[Entry, Rider | None, str]]:
    """Yield each plate claim, in entry then rider order.

    The one plate namespace is model-shaped (S1): a team_relay ride's
    entries own the plates (its riders are plateless), while a
    rider_pooled ride's riders own the plates and an entry only adopts
    its lowest-numbered rider's. Counting the adopted plate separately
    would flag every pooled entry as a duplicate of its own rider, so
    the pooled branch iterates rider plates only. Blank plates are
    missing-number defects, not duplicate candidates, and are skipped.
    """
    if roster.plate_model is PlateModel.RIDER_POOLED:
        for entry in roster.entries:
            for rider in entry.riders:
                if rider.plate is not None and rider.plate.strip():
                    yield entry, rider, rider.plate
        return
    for entry in roster.entries:
        if entry.plate.strip():
            yield entry, None, entry.plate
