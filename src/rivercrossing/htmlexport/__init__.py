# SPDX-License-Identifier: GPL-3.0-only
"""Frozen results payload shared by the UI and all three exporters.

Package, not a module: ``design/templates/base.html.j2``'s header
comment binds ``Environment(PackageLoader("rivercrossing.htmlexport",
"templates"))``, and ``PackageLoader`` needs an importable *package*
to find its ``templates/`` resource directory -- a plain
``htmlexport.py`` module cannot host that. ``module-skeletons.md``
draws ``htmlexport.py`` as a single file; that line is a documented
defect, corrected here to a package (task E1.2.2). The ``templates/``
directory itself is not created by this task -- copying the Jinja2
templates verbatim is EPIC 6 task E6.2.

This module freezes only the *data* contract (Spec §8, R-61/63): the
dataclasses the UI and all three exporters share, plus ``to_record()``
methods that build the exact camelCase JSON the golden pages'
``<script id="race-data">`` block embeds. No Jinja2 import and no
rendering here -- ``render()`` lands with the templates in E6.2.
"""

from dataclasses import dataclass

CardPair = tuple[str, str]
"""One drawn card as embedded in the JSON record: ``(rank, suit)``,
e.g. ``("9", "s")``, or ``("JK", "j")`` for a joker.
"""


def _snake_to_camel(name: str) -> str:
    """Convert a ``snake_case`` name to its ``camelCase`` JSON key."""
    head, *rest = name.split("_")
    return head + "".join(word.capitalize() for word in rest)


@dataclass(frozen=True, slots=True)
class EventInfo:
    """Header facts for the page and its JSON record (Spec §8)."""

    kicker: str
    title: str
    meta: str
    organizer: str
    scorer: str
    generated: str
    entries: int
    laps: int
    cards: int

    def to_record(self) -> dict[str, str | int]:
        """Return this event's JSON-record view."""
        return {
            "kicker": self.kicker,
            "title": self.title,
            "meta": self.meta,
            "organizer": self.organizer,
            "scorer": self.scorer,
            "generated": self.generated,
            "entries": self.entries,
            "laps": self.laps,
            "cards": self.cards,
        }


_OPTION_FIELDS = ("show_times", "laps_board", "time_board", "full_field", "all_cards")


@dataclass(frozen=True, slots=True)
class ExportOptions:
    """Export flags the UI and both exporters share (R-63, TB-1).

    Times are hidden by default. ``lap_km`` is a render-only setting
    (course-length text on the page) and is never part of the JSON
    record -- the golden pages' ``options`` block never carries it.
    """

    show_times: bool = False
    laps_board: bool = True
    time_board: bool = False
    full_field: bool = True
    all_cards: bool = True
    lap_km: float = 8.0

    def to_record(self) -> dict[str, bool]:
        """Return the camelCase flag record (``lap_km`` omitted)."""
        return {_snake_to_camel(name): getattr(self, name) for name in _OPTION_FIELDS}


@dataclass(frozen=True, slots=True)
class ResultRow:
    """One entry's placed result -- one row of the ``results`` record.

    ``total``/``best_lap`` are the only fields whose presence in
    ``to_record()`` depends on ``ExportOptions.show_times`` (R-63).
    ``tie``/``dnf`` are sparse -- emitted only when true, never as a
    ``false`` key, matching the golden pages' shape exactly. The
    field is named ``entry_type`` rather than ``type`` to avoid
    shadowing the builtin; it maps to the JSON key ``"type"``.
    """

    place: int
    plate: int
    entry: str
    entry_type: str
    laps: int
    hand: str
    total: str | None = None
    best_lap: str | None = None
    tie: bool = False
    dnf: bool = False
    cards: tuple[CardPair, ...] = ()
    drawn: tuple[CardPair, ...] = ()

    def to_record(self, *, show_times: bool) -> dict[str, object]:
        """Return the JSON-record view for one results row.

        ``total``/``bestLap`` are included only when ``show_times``;
        ``tie``/``dnf`` are included only when true.
        """
        record: dict[str, object] = {
            "place": self.place,
            "plate": self.plate,
            "entry": self.entry,
            "type": self.entry_type,
            "laps": self.laps,
        }
        if show_times:
            record["total"] = self.total
            record["bestLap"] = self.best_lap
        record["hand"] = self.hand
        if self.tie:
            record["tie"] = True
        if self.dnf:
            record["dnf"] = True
        record["cards"] = [list(pair) for pair in self.cards]
        record["drawn"] = [list(pair) for pair in self.drawn]
        return record


@dataclass(frozen=True, slots=True)
class LapsBoardRow:
    """One row of the "most laps" leaderboard (Spec §8)."""

    plate: int
    entry: str
    laps: int
    total: str | None = None

    def to_record(self, *, show_times: bool) -> dict[str, object]:
        """Return the JSON-record view (``total`` omitted, no times)."""
        record: dict[str, object] = {
            "plate": self.plate,
            "entry": self.entry,
            "laps": self.laps,
        }
        if show_times:
            record["total"] = self.total
        return record


@dataclass(frozen=True, slots=True)
class TimeBoardRow:
    """One row of the "fastest" leaderboard.

    Only ever populated when times are shown (R-63) -- an empty
    ``time_board`` tuple on ``RacePayload`` is the off-state, not a
    per-row flag, so this row carries no conditional fields.
    """

    plate: int
    entry: str
    laps: int
    total: str
    avg: str

    def to_record(self) -> dict[str, object]:
        """Return the JSON-record view for one time-board row."""
        return {
            "plate": self.plate,
            "entry": self.entry,
            "laps": self.laps,
            "total": self.total,
            "avg": self.avg,
        }


@dataclass(frozen=True, slots=True)
class RacePayload:
    """The full results record embedded in ``<script id="race-data">``.

    ``to_record()`` is the single source of the JSON the golden pages
    parse back out -- Spec §8's round-trip test target.
    """

    event: EventInfo
    options: ExportOptions
    tie_note: str | None
    results: tuple[ResultRow, ...]
    laps_board: tuple[LapsBoardRow, ...] = ()
    time_board: tuple[TimeBoardRow, ...] = ()

    def to_record(self) -> dict[str, object]:
        """Return the full camelCase JSON record for the page."""
        show_times = self.options.show_times
        return {
            "event": self.event.to_record(),
            "options": self.options.to_record(),
            "tieNote": self.tie_note,
            "results": [row.to_record(show_times=show_times) for row in self.results],
            "lapsBoard": [row.to_record(show_times=show_times) for row in self.laps_board],
            "timeBoard": [row.to_record() for row in self.time_board],
        }
