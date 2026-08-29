# SPDX-License-Identifier: GPL-3.0-only
"""Frozen results payload and the HTML renderer (Spec §8, R-61).

Package, not a module: ``design/templates/base.html.j2``'s header
comment binds ``Environment(PackageLoader("rivercrossing.htmlexport",
"templates"))``, and ``PackageLoader`` needs an importable *package*
to find its ``templates/`` resource directory -- a plain
``htmlexport.py`` module cannot host that. ``module-skeletons.md``
draws ``htmlexport.py`` as a single file; that line is a documented
defect, corrected here to a package (task E1.2.2).

The payload half freezes the *data* contract (Spec §8, R-61/63): the
dataclasses the UI and all three exporters share, plus ``to_record()``
methods that build the exact camelCase JSON the golden pages'
``<script id="race-data">`` block embeds (E1.2.2). The renderer half
(E6.2.2) implements ``render()`` per the template contract: a
StrictUndefined, autoescaping ``Environment`` over the vendored
templates, the ``racejson`` filter that escapes every ``</``, and a
self-contained production page with CSS/fonts inlined and the record
embedded.
"""

import base64
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from jinja2 import Environment, PackageLoader, StrictUndefined
from markupsafe import Markup

from rivercrossing.standings import hand_name, laps_leaderboard, time_leaderboard

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from rivercrossing.cards import Card, Rank, Suit
    from rivercrossing.hands import EvaluatedHand
    from rivercrossing.standings import Placed

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
    shadowing the builtin; it maps to the JSON key ``"type"``, and
    the templates read it through the :attr:`type` alias (D6).
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

    @property
    def type(self) -> str:
        """Alias of ``entry_type`` -- the templates read ``r.type``."""
        return self.entry_type

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


# ==================================================== E6.2.2 renderer

_KICKER = "Official results · poker run"

# D8's 1x1 transparent PNG fallback: a page without a ride logo must
# still carry a valid (never empty, never external) img src.
_TRANSPARENT_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

# The samples' footer style -- "Generated 16:07, Sept 20 2026" -- uses
# the four-letter "Sept", not the three-letter "Sep", so %b is not
# used; the abbreviation is the samples' own vocabulary.
_MONTH_ABBR = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sept",
    "Oct",
    "Nov",
    "Dec",
)

# cards.Rank's own integer values to the record's rank letters. The
# ten is "10" in the record (the golden pages' own spelling), unlike
# Card.code()'s "T" -- these are the payload pairs, not card codes.
_RANK_PAIR_LETTER: dict[int, str] = {
    2: "2",
    3: "3",
    4: "4",
    5: "5",
    6: "6",
    7: "7",
    8: "8",
    9: "9",
    10: "10",
    11: "J",
    12: "Q",
    13: "K",
    14: "A",
}

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


class _RideLike(Protocol):
    """The ride fields :func:`render` reads (documented seam, D15).

    ``RideConfig`` satisfies this structurally (the read-only property
    members match its frozen fields); the Protocol lets the
    ``render()`` tests pass a tiny stub instead of constructing a full
    ride. Read fields: ``name`` -> title, ``event_date``/``venue``/
    ``lap_km`` -> meta, ``organizer``/``scorer`` -> footer credits.
    """

    @property
    def name(self) -> str: ...

    @property
    def event_date(self) -> date: ...

    @property
    def venue(self) -> str: ...

    @property
    def lap_km(self) -> float: ...

    @property
    def organizer(self) -> str: ...

    @property
    def scorer(self) -> str: ...


def racejson(payload: RacePayload) -> Markup:
    r"""Serialize *payload* as the page's embedded ``race-data`` JSON.

    ``json.dumps`` with the golden pages' indent and ``ensure_ascii``
    off, then every ``</`` escaped as a backslash-slash pair
    (``<\\/``) so no team name can terminate the ``<script>`` block
    early (D7, TB-6). Returns :class:`markupsafe.Markup` so Jinja2's
    autoescape does not re-escape the JSON it already wrote.

    Args:
        payload: The payload to embed.

    Returns:
        The JSON text as Markup, safe for the ``| racejson`` filter.
    """
    record = json.dumps(payload.to_record(), indent=2, ensure_ascii=False)
    return Markup(record.replace("</", "<\\/"))  # noqa: S704 -- D7: trusted escaped JSON, intentionally Markup


def _finalize_display(value: object) -> object:
    """Render an integral float as its int (D5: lap_km 8.0 -> "8")."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


@lru_cache(maxsize=2)
def _asset_text(name: str) -> str:
    """Read one vendored asset (``compiled_css`` or ``fonts_css``)."""
    return (_TEMPLATES_DIR / name).read_text(encoding="utf-8")


def _make_environment() -> Environment:
    """Build the Spec §8 environment with the ``racejson`` filter."""
    env = Environment(
        loader=PackageLoader("rivercrossing.htmlexport", "templates"),
        autoescape=True,
        undefined=StrictUndefined,
        finalize=_finalize_display,
    )
    env.filters["racejson"] = racejson
    return env


def _template_context(  # noqa: PLR0913 -- the four context inputs the template contract names
    payload: RacePayload,
    *,
    dev: bool,
    logo_src: str | None,
    generated: str | None,
) -> dict[str, object]:
    """Build the ``base.html.j2`` context from *payload* (Spec §8).

    ``generated`` overrides the payload's own event timestamp in both
    the footer and the embedded JSON record (D15's freeze seam);
    ``logo_src`` falls back to a 1x1 transparent PNG so the page never
    carries an empty ``src`` (D8).
    """
    if generated is not None:
        payload = replace(payload, event=replace(payload.event, generated=generated))
    return {
        "event": payload.event,
        "options": payload.options,
        "results": payload.results,
        "laps_board": payload.laps_board,
        "time_board": payload.time_board,
        "tie_note": payload.tie_note,
        "logo_src": logo_src if logo_src is not None else _TRANSPARENT_PNG,
        "logo_alt": payload.event.organizer,
        "payload": payload,
        "dev": dev,
        "compiled_css": _asset_text("compiled_css"),
        "fonts_css": _asset_text("fonts_css"),
    }


def _render_payload(  # noqa: PLR0913 -- the four context inputs the template contract names
    payload: RacePayload,
    *,
    dev: bool = False,
    logo_src: str | None = None,
    generated: str | None = None,
) -> str:
    """Render *payload* as the full results page (Spec §8).

    The golden-test seam: ``test_htmlexport.py`` drives this with the
    committed fixture payloads, and ``tools/gen_htmlexport_goldens.py``
    regenerates the frozen goldens through it. ``dev=True`` is the
    design-sample preview (CDN Tailwind + Google Fonts stand-ins);
    production renders vendored CSS/fonts and embeds the JSON record.
    """
    context = _template_context(payload, dev=dev, logo_src=logo_src, generated=generated)
    return _make_environment().get_template("base.html.j2").render(**context)


def _format_event_date(day: date) -> str:
    """Format *day* as the golden pages' "Sunday September 20, 2026"."""
    return f"{day.strftime('%A %B')} {day.day}, {day.year}"


def _generated_now() -> str:
    """Return the footer timestamp in the samples' local-time style.

    "Generated 16:07, Sept 20 2026" -- the operator's wall clock, so a
    tz-aware local ``now()`` feeds the format.
    """
    at = datetime.now(tz=timezone.utc).astimezone()  # noqa: UP017 -- this 3.14 build lacks datetime.UTC
    return f"Generated {at.strftime('%H:%M')}, {_MONTH_ABBR[at.month - 1]} {at.day} {at.year}"


def _format_duration(seconds: float) -> str:
    """Format whole seconds as the golden pages' clock text.

    ``H:MM:SS`` for an hour or more, ``M:SS`` below -- "5:52:41" and
    "27:59" respectively.
    """
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _parse_plate(plate: str) -> int:
    """Convert an entry's plate string to the record's integer form.

    Raises:
        ValueError: *plate* is not numeric. The app's generated plates
            are numeric (``Roster.next_free_plate``), so a non-numeric
            plate reaching the renderer is a caller bug worth naming.
    """
    try:
        return int(plate)
    except ValueError as exc:
        msg = f"plate {plate!r} is not numeric; htmlexport.render() emits plates as integers"
        raise ValueError(msg) from exc


def _hand_label(hand: EvaluatedHand) -> str:
    """Return *hand*'s prose name, or "" for a no-card hand.

    A finished ride can still hold an entry that never crossed; its
    snapshot hand is empty (``best_hand(())``) and names nothing, so
    the field renders blank rather than raising (standings.hand_name
    rejects the empty hand).
    """
    if not hand.best5:
        return ""
    return hand_name(hand)


def _card_pair(card: Card) -> CardPair:
    """Convert one Card to its record pair (rank letter, suit letter).

    The ten maps to "10" -- the record's own spelling, not
    ``Card.code``'s "T".
    """
    if card.joker:
        return ("JK", "j")
    rank = cast("Rank", card.rank)
    suit = cast("Suit", card.suit)
    return (_RANK_PAIR_LETTER[rank.value], suit.value.lower())


def _result_row_from_placed(placed: Placed) -> ResultRow:
    """Map one ``standings.Placed`` to the record's results row.

    ``result.hand.best5`` supplies the displayed best-5 cards and
    ``result.cards`` the full draw order, mirroring the golden pages'
    ``cards`` vs ``drawn`` split. ``entry_type`` is the kind in
    uppercase ("SOLO"/"TEAM"); the sample's team-size suffix (as in
    "TEAM by 4") is a display form ``EntryResult`` does not carry, so
    it is not reproduced (documented seam).
    """
    result = placed.result
    return ResultRow(
        place=placed.place,
        plate=_parse_plate(result.plate),
        entry=result.name,
        entry_type=result.kind.upper(),
        laps=result.laps,
        hand=_hand_label(result.hand),
        total=_format_duration(result.total_time),
        best_lap=_format_duration(result.best_lap),
        tie=placed.draw_required,
        dnf=result.dnf,
        cards=tuple(_card_pair(card) for card in result.hand.best5),
        drawn=tuple(_card_pair(card) for card in result.cards),
    )


def _format_meta(ride: _RideLike) -> str:
    """Compose the page's meta line from the ride (D15: venue/date)."""
    return (
        f"{_format_event_date(ride.event_date)} · {ride.venue} · "
        f"{_finalize_display(ride.lap_km)} km loop"
    )


def _boards_from_placed(
    placed: Sequence[Placed], opts: ExportOptions
) -> tuple[tuple[LapsBoardRow, ...], tuple[TimeBoardRow, ...]]:
    """Build the leaderboard rows the export options request (E6.4.2).

    ``laps_board`` renders only when ``opts.laps_board`` (rows carry a
    ``total`` only when times are shown, R-63); ``time_board`` is
    times-only by contract and renders only when ``opts.time_board``.
    Both boards order by most laps, then shortest total time
    (standings' own leaderboards).
    """
    results = [p.result for p in placed]
    laps = (
        tuple(
            LapsBoardRow(
                plate=_parse_plate(p.result.plate),
                entry=p.result.name,
                laps=p.result.laps,
                total=_format_duration(p.result.total_time) if opts.show_times else None,
            )
            for p in laps_leaderboard(results)
        )
        if opts.laps_board
        else ()
    )
    times = (
        tuple(
            TimeBoardRow(
                plate=_parse_plate(p.result.plate),
                entry=p.result.name,
                laps=p.result.laps,
                total=_format_duration(p.result.total_time),
                avg=_format_duration(p.result.total_time / p.result.laps)
                if p.result.laps
                else "—",
            )
            for p in time_leaderboard(results)
        )
        if opts.time_board
        else ()
    )
    return laps, times


def _payload_from_ride(  # noqa: PLR0913, PLR0917 -- (ride, placed, opts, generated): D15's mapping inputs
    ride: _RideLike,
    placed: Sequence[Placed],
    opts: ExportOptions,
    generated: str | None,
) -> RacePayload:
    """Build the export payload from a ride and its placed standings.

    The fixture payloads (which carry the golden boards) reach the
    page through ``_render_payload`` (D15); the public
    :func:`render` path derives its boards from *placed* here
    (E6.4.2).
    """
    results = tuple(_result_row_from_placed(p) for p in placed)
    laps_board, time_board = _boards_from_placed(placed, opts)
    event = EventInfo(
        kicker=_KICKER,
        title=ride.name,
        meta=_format_meta(ride),
        organizer=f"Organizer: {ride.organizer}",
        scorer=f"Scorer: {ride.scorer}",
        generated=generated if generated is not None else _generated_now(),
        entries=len(placed),
        laps=sum(p.result.laps for p in placed),
        cards=sum(len(p.result.cards) for p in placed),
    )
    return RacePayload(
        event=event,
        options=opts,
        tie_note=None,
        results=results,
        laps_board=laps_board,
        time_board=time_board,
    )


def _logo_data_uri(path: Path | str) -> str:
    """Encode the PNG at *path* as a base64 data URI (R-61)."""
    payload = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:image/png;base64,{payload}"


def render(  # noqa: PLR0913 -- D15's frozen signature (ride, placed, opts, logo_src, generated)
    ride: _RideLike,
    placed: Sequence[Placed],
    opts: ExportOptions,
    *,
    logo_src: str | None = None,
    generated: str | None = None,
    logo_path: Path | str | None = None,
) -> str:
    """Render one finished ride's results as a self-contained HTML page.

    Composes the page's ``EventInfo`` from *ride* (name -> title,
    venue/date -> meta, organizer/scorer, entries/laps/cards tallied
    from *placed*), one ``ResultRow`` per ``Placed``, and the laps /
    time boards when the options request them (E6.4.2), then renders
    through :func:`_render_payload`. ``generated`` pins the footer
    timestamp (defaults to now in the golden pages' style);
    ``logo_src`` is the ride logo as a base64 data URI, falling back
    to a transparent 1x1 PNG when absent (D8); ``logo_path`` is the
    alternative raw-file form, base64-encoded when *logo_src* is
    None.

    Args:
        ride: Ride-like object exposing ``name``/``event_date``/
            ``venue``/``lap_km``/``organizer``/``scorer``;
            ``RideConfig`` satisfies it structurally.
        placed: Ranked standings, one per entry.
        opts: Export flags (times/boards/full-field/all-cards).
        logo_src: Base64 logo data URI; transparent fallback when None.
        generated: Footer timestamp; defaults to now, samples' style.
        logo_path: Raw PNG path, base64-embedded when *logo_src* is
            None (R-61's logo-base64 rule).

    Returns:
        The full HTML page as a string.

    Raises:
        ValueError: An entry's plate is not numeric.
    """
    if logo_src is None and logo_path is not None:
        logo_src = _logo_data_uri(logo_path)
    payload = _payload_from_ride(ride, placed, opts, generated)
    return _render_payload(payload, logo_src=logo_src)


# ============================================ record -> payload (TB-5)


def _options_from_record(options: Mapping[str, object]) -> ExportOptions:
    """Build ``ExportOptions`` from the record's camelCase flag block.

    ``lap_km`` never appears in the record (render-only, R-63), so it
    keeps its default here.
    """
    return ExportOptions(
        show_times=cast("bool", options["showTimes"]),
        laps_board=cast("bool", options["lapsBoard"]),
        time_board=cast("bool", options["timeBoard"]),
        full_field=cast("bool", options["fullField"]),
        all_cards=cast("bool", options["allCards"]),
    )


def _card_pairs(value: object) -> tuple[CardPair, ...]:
    """Convert a record ``cards``/``drawn`` list to its pair tuples."""
    pairs: list[CardPair] = []
    for item in cast("list[object]", value):
        pair = cast("tuple[object, object]", item)
        pairs.append((cast("str", pair[0]), cast("str", pair[1])))
    return tuple(pairs)


def _result_row_from_record(row: Mapping[str, object]) -> ResultRow:
    """Build one ``ResultRow`` from its camelCase record row."""
    return ResultRow(
        place=cast("int", row["place"]),
        plate=cast("int", row["plate"]),
        entry=cast("str", row["entry"]),
        entry_type=cast("str", row["type"]),
        laps=cast("int", row["laps"]),
        hand=cast("str", row["hand"]),
        total=cast("str | None", row.get("total")),
        best_lap=cast("str | None", row.get("bestLap")),
        tie=cast("bool", row.get("tie", False)),
        dnf=cast("bool", row.get("dnf", False)),
        cards=_card_pairs(row.get("cards", [])),
        drawn=_card_pairs(row.get("drawn", [])),
    )


def _laps_board_row_from_record(row: Mapping[str, object]) -> LapsBoardRow:
    """Build one ``LapsBoardRow`` from its camelCase record row."""
    return LapsBoardRow(
        plate=cast("int", row["plate"]),
        entry=cast("str", row["entry"]),
        laps=cast("int", row["laps"]),
        total=cast("str | None", row.get("total")),
    )


def _time_board_row_from_record(row: Mapping[str, object]) -> TimeBoardRow:
    """Build one ``TimeBoardRow`` from its camelCase record row."""
    return TimeBoardRow(
        plate=cast("int", row["plate"]),
        entry=cast("str", row["entry"]),
        laps=cast("int", row["laps"]),
        total=cast("str", row["total"]),
        avg=cast("str", row["avg"]),
    )


def _payload_from_record(record: Mapping[str, object]) -> RacePayload:
    """Build a ``RacePayload`` from a parsed ``race-data`` JSON record.

    The inverse of :meth:`RacePayload.to_record`; the golden generator
    and the fixture loader reconstruct payloads this way, and
    ``_payload_from_record(record).to_record() == record`` is the
    value-parity check that lets the regenerated goldens replace the
    hand-assembled samples (TB-5).
    """
    event = cast("Mapping[str, object]", record["event"])
    return RacePayload(
        event=EventInfo(
            kicker=cast("str", event["kicker"]),
            title=cast("str", event["title"]),
            meta=cast("str", event["meta"]),
            organizer=cast("str", event["organizer"]),
            scorer=cast("str", event["scorer"]),
            generated=cast("str", event["generated"]),
            entries=cast("int", event["entries"]),
            laps=cast("int", event["laps"]),
            cards=cast("int", event["cards"]),
        ),
        options=_options_from_record(cast("Mapping[str, object]", record["options"])),
        tie_note=cast("str | None", record["tieNote"]),
        results=tuple(
            _result_row_from_record(cast("Mapping[str, object]", row))
            for row in cast("list[object]", record["results"])
        ),
        laps_board=tuple(
            _laps_board_row_from_record(cast("Mapping[str, object]", row))
            for row in cast("list[object]", record["lapsBoard"])
        ),
        time_board=tuple(
            _time_board_row_from_record(cast("Mapping[str, object]", row))
            for row in cast("list[object]", record["timeBoard"])
        ),
    )
