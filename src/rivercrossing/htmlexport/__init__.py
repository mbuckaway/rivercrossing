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
``<script id="race-data">`` block embeds (E1.2.2). The shared model's
public entry points are :func:`build_payload` (the ride seam),
:func:`sections` (the per-kind page plan both exporters render) and
:func:`format_generated` (the pure stamp formatter, R-62). The
renderer half (E6.2.2) implements ``render()`` per the template
contract: a StrictUndefined, autoescaping ``Environment`` over the
vendored templates, the ``racejson`` filter that escapes every
``</``, and a self-contained production page with CSS/fonts inlined
and the record embedded. :func:`render_poster` is that environment's
second page: the one-page podium poster ([5d]) the PDF exporter also
writes, from the same shared model. :func:`render_wordpress` is the
third rendering of it: the same macros' inner HTML for a WordPress
Page's ``content``, wrapped in ``.rc-results`` and inlining the
stylesheet ``tools/gen_css.py`` has scoped under that wrapper, so
publishing a result cannot restyle the site it lands in.
"""

import base64
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from jinja2 import Environment, PackageLoader, StrictUndefined

from rivercrossing.standings import hand_name, laps_leaderboard, time_leaderboard

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from rivercrossing.cards import Card, Rank, Suit
    from rivercrossing.hands import EvaluatedHand
    from rivercrossing.standings import Placed

__all__ = [
    "SELF_TEST_NOTE",
    "CardPair",
    "EventInfo",
    "ExportOptions",
    "LapsBoardRow",
    "RacePayload",
    "ResultRow",
    "Sections",
    "TimeBoardRow",
    "build_payload",
    "format_generated",
    "racejson",
    "render",
    "render_poster",
    "render_wordpress",
    "sections",
]

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
    ``tie``/``draw``/``dnf``/``logo``/``sex`` are sparse -- emitted only
    when set, never as a ``false``/``null`` key, matching the golden
    pages' shape exactly. ``draw`` is the card the entry drew for the
    venue's high-card tie-break (R-14), a ``(rank, suit)`` pair or
    ``None`` when the entry drew nothing: it is deliberately *not*
    ``tie``, which means the residual tie a configured order could not
    separate. ``sex`` is a solo rider's ``"M"``/``"F"`` and None
    for a team (no single sex), so a team row renders no marker. The
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
    # The solo rider's sex ("M"/"F"); None for a team, whose members
    # need not share one. Sparse in the record, like tie/draw/logo.
    sex: str | None = None
    tie: bool = False
    dnf: bool = False
    cards: tuple[CardPair, ...] = ()
    drawn: tuple[CardPair, ...] = ()
    # R-14's drawn tie-break card: the entry's own card, present only
    # when a draw recorded one.
    draw: CardPair | None = None
    # The entry's logo *card* as a data URI (the team's packaged card
    # bitmap, resolved by the caller), mirroring the org logo's own
    # logo_src mechanism. Sparse in the record -- absent rows have no
    # logo and render nothing. Phase 3 retired the logo image, so the
    # card is the whole logo.
    logo: str | None = None

    @property
    def type(self) -> str:
        """Alias of ``entry_type`` -- the templates read ``r.type``."""
        return self.entry_type

    def to_record(self, *, show_times: bool) -> dict[str, object]:
        """Return the JSON-record view for one results row.

        ``total``/``bestLap`` are included only when ``show_times``;
        ``sex``/``tie``/``dnf``/``draw`` are included only when set.
        """
        record: dict[str, object] = {
            "place": self.place,
            "plate": self.plate,
            "entry": self.entry,
            "type": self.entry_type,
        }
        if self.sex is not None:
            record["sex"] = self.sex
        record["laps"] = self.laps
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
        if self.draw is not None:
            record["draw"] = list(self.draw)
        if self.logo is not None:
            record["logo"] = self.logo
        return record


@dataclass(frozen=True, slots=True)
class LapsBoardRow:
    """One row of the "most laps" leaderboard (Spec §8).

    ``type`` is the row's kind ("TEAM"/"SOLO"), always recorded so the
    page and the full-results PDF can split one flat board into the
    per-kind boards; rows built before the field existed carry the
    empty default.
    """

    plate: int
    entry: str
    laps: int
    total: str | None = None
    type: str = ""

    def to_record(self, *, show_times: bool) -> dict[str, object]:
        """Return the JSON-record view (``total`` omitted, no times)."""
        record: dict[str, object] = {
            "plate": self.plate,
            "entry": self.entry,
            "type": self.type,
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
    parse back out -- Spec §8's round-trip test target. ``tie_note``
    and ``self_test_note`` are the page's two notes: the first explains
    a residual tie, the second (E6.4.3) records that the ride was
    finished over a failed evaluator self-test. Both are sparse in the
    record -- ``tieNote`` is always emitted (the samples carry it, as
    null), ``selfTestNote`` only when set, so every pre-E6.4.3 record
    round-trips byte-identically.
    """

    event: EventInfo
    options: ExportOptions
    tie_note: str | None
    results: tuple[ResultRow, ...]
    laps_board: tuple[LapsBoardRow, ...] = ()
    time_board: tuple[TimeBoardRow, ...] = ()
    self_test_note: str | None = None

    def to_record(self) -> dict[str, object]:
        """Return the full camelCase JSON record for the page."""
        show_times = self.options.show_times
        record: dict[str, object] = {
            "event": self.event.to_record(),
            "options": self.options.to_record(),
            "tieNote": self.tie_note,
            "results": [row.to_record(show_times=show_times) for row in self.results],
            "lapsBoard": [row.to_record(show_times=show_times) for row in self.laps_board],
            "timeBoard": [row.to_record() for row in self.time_board],
        }
        if self.self_test_note is not None:
            record["selfTestNote"] = self.self_test_note
        return record


# The section plan's per-kind row counts -- the display contract both
# exporters render from (three podium cards, five per kind on a team
# event, today's ten on a solo one).
_PODIUM_ROWS = 3
_TOP_TEAM_ROWS = 5
_TOP_SOLO_ROWS = 5
_TOP_SOLO_ONLY_ROWS = 10


@dataclass(frozen=True, slots=True)
class Sections:
    """The planned page sections both exporters render (Spec §8).

    ``teams``/``solo`` partition the payload's rows: any TEAM row makes
    it a team event (a MIXED ride presents as one; the partition, never
    ``ride.entry_mode``, decides). The podium and top lists are their
    per-kind heads, and the laps boards come from the placed standings
    so one kind can never crowd the other out of a combined board.
    ``time_board`` passes through flat, exactly as recorded, and
    :attr:`team_plates` is derived from ``teams`` for its row lookup.
    """

    podium_teams: tuple[ResultRow, ...]
    podium_solo: tuple[ResultRow, ...]
    top_teams: tuple[ResultRow, ...]
    top_solo: tuple[ResultRow, ...]
    laps_teams: tuple[LapsBoardRow, ...]
    laps_solo: tuple[LapsBoardRow, ...]
    time_board: tuple[TimeBoardRow, ...]
    teams: tuple[ResultRow, ...]
    solo: tuple[ResultRow, ...]

    @property
    def team_plates(self) -> frozenset[int]:
        """Return the plates of the plan's team rows.

        The flat "Fastest" board crosses both kinds, so each of its
        rows asks this set whether its plate belongs to a team -- team
        rows render no plate anywhere, and the cell is left blank
        against the solo rows' own plates. Derived, never a field: the
        ``teams`` partition is the one source, so the HTML template and
        ``pdfexport._time_board`` (which builds the same set inline for
        ``_time_row``) can never disagree.
        """
        return frozenset(row.plate for row in self.teams)


# ==================================================== E6.2.2 renderer

_KICKER = "Official results · poker run"

# E6.4.3's note: the caption the page and the PDF render when the ride
# was finished over a failed evaluator self-test, so a published result
# never hides the fact. Composed here (like ``EventInfo``'s own
# "Organizer: ..." lines) rather than by each caller, so the page, the
# report and their tests read one string.
SELF_TEST_NOTE = (
    "Self-test unverified — these results were published over a failed evaluator self-test"
)

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
# ten is "10" -- the golden pages' own spelling and Card.code()'s too
# -- and these are the payload pairs, not card codes.
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


def racejson(payload: RacePayload) -> str:
    r"""Serialize *payload* as the page's embedded ``race-data`` JSON.

    ``json.dumps`` with the golden pages' indent and ``ensure_ascii``
    off, then every ``</`` escaped as a backslash-slash pair
    (``<\\/``) so no team name can terminate the ``<script>`` block
    early (D7, TB-6). The result is plain text -- the template is the
    one place that marks it safe (``| racejson | safe``), so no Python
    module wraps it in :class:`markupsafe.Markup`.

    Args:
        payload: The payload to embed.

    Returns:
        The escaped JSON text for the ``| racejson | safe`` filter.
    """
    record = json.dumps(payload.to_record(), indent=2, ensure_ascii=False)
    return record.replace("</", "<\\/")


def _finalize_display(value: object) -> object:
    """Render an integral float as its int (D5: lap_km 8.0 -> "8")."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _asset_text(name: str) -> str:
    """Read one vendored asset (``compiled_css`` or ``fonts_css``).

    Read uncached, once per render: the two assets are a few hundred
    kilobytes off a local disk and each render writes a whole page, so
    the read is not a measured hot path. Reinstate a cache only with a
    measurement recorded beside it.
    """
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


def _template_context(  # noqa: PLR0913 -- the six context inputs the templates name
    payload: RacePayload,
    *,
    dev: bool,
    logo_src: str | None,
    generated: str | None,
    placed: Sequence[Placed] | None = None,
    stylesheet: str = "compiled_css",
) -> dict[str, object]:
    """Build one page's template context from *payload* (Spec §8).

    ``generated`` overrides the payload's own event timestamp in both
    the footer and the embedded JSON record (D15's freeze seam);
    ``logo_src`` falls back to a 1x1 transparent PNG so the page never
    carries an empty ``src`` (D8).

    The context carries the planned :class:`Sections`, never sliced
    rows: with *placed* the plan's laps boards come from the standings
    (the live path through :func:`render`); without it they come from
    the payload's own per-kind board (the golden-record path). The
    plan's derived :attr:`Sections.team_plates` set is everything the
    "Fastest" board needs beyond its rows, so no extra context key is
    added for it. The
    record itself stays one flat ``results`` array (the app feeds it
    Teams-then-Solo with per-kind places); the partition is by the
    row's entry type, so a kind with no rows is simply absent. The
    golden samples' own display suffixes (a "TEAM" prefix plus a
    team-size note) still lead with "TEAM", hence the prefix match;
    the renderer's own rows are exactly "TEAM"/"SOLO"
    (``_result_row_from_placed``).

    *stylesheet* names the vendored CSS artifact the page inlines:
    ``compiled_css`` for the standalone page, ``compiled_css_wp`` for
    the WordPress fragment, whose selectors ``tools/gen_css.py`` has
    already scoped under the fragment's wrapper. The key carries that
    artifact's name, so neither template can inline the other's.
    """
    if generated is not None:
        payload = replace(payload, event=replace(payload.event, generated=generated))
    plan = sections(payload, placed) if placed is not None else _sections_from_payload(payload)
    return {
        "event": payload.event,
        "options": payload.options,
        "sections": plan,
        "tie_note": payload.tie_note,
        "self_test_note": payload.self_test_note,
        "logo_src": logo_src if logo_src is not None else _TRANSPARENT_PNG,
        "logo_alt": payload.event.organizer,
        "payload": payload,
        "dev": dev,
        stylesheet: _asset_text(stylesheet),
        "fonts_css": _asset_text("fonts_css"),
    }


def _render_payload(  # noqa: PLR0913 -- the five context inputs the template contract names
    payload: RacePayload,
    *,
    dev: bool = False,
    logo_src: str | None = None,
    generated: str | None = None,
    placed: Sequence[Placed] | None = None,
) -> str:
    """Render *payload* as the full results page (Spec §8).

    The golden-test seam: ``test_htmlexport.py`` drives this with the
    committed fixture payloads, and ``tools/gen_htmlexport_goldens.py``
    regenerates the frozen goldens through it. ``dev=True`` is the
    design-sample preview (CDN Tailwind + Google Fonts stand-ins);
    production renders vendored CSS/fonts and embeds the JSON record.
    *placed* is optional: the fixture path renders the payload's own
    boards, while :func:`render` passes the standings for the current
    per-kind boards.
    """
    context = _template_context(
        payload, dev=dev, logo_src=logo_src, generated=generated, placed=placed
    )
    return _make_environment().get_template("base.html.j2").render(**context)


def _format_event_date(day: date) -> str:
    """Format *day* as the golden pages' "Sunday September 20, 2026"."""
    return f"{day.strftime('%A %B')} {day.day}, {day.year}"


def format_generated(at: datetime) -> str:
    """Render *at* as the footer's "Generated H:MM, Mon D YYYY" stamp.

    A pure function of its input: the instant's own offset is used as
    given -- no ``now()`` and no ``astimezone()`` inside -- so a pinned
    UTC instant renders byte-identical on every machine (R-62). The
    month abbreviation is the samples' own four-letter "Sept".
    """
    return f"Generated {at.strftime('%H:%M')}, {_MONTH_ABBR[at.month - 1]} {at.day} {at.year}"


def _generated_now() -> str:
    """Return the live footer stamp in the operator's own wall clock."""
    return format_generated(datetime.now(UTC).astimezone())


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

    The ten maps to "10", the same spelling ``Card.code`` uses.
    """
    if card.joker:
        return ("JK", "j")
    rank = cast("Rank", card.rank)
    suit = cast("Suit", card.suit)
    return (_RANK_PAIR_LETTER[rank.value], suit.value.lower())


def _result_row_from_placed(placed: Placed, *, logo: str | None = None) -> ResultRow:
    """Map one ``standings.Placed`` to the record's results row.

    ``result.hand.best5`` supplies the displayed best-5 cards and
    ``result.cards`` the full draw order, mirroring the golden pages'
    ``cards`` vs ``drawn`` split. ``entry_type`` is the kind in
    uppercase ("SOLO"/"TEAM"); the sample's team-size suffix (as in
    "TEAM by 4") is a display form ``EntryResult`` does not carry, so
    it is not reproduced (documented seam). *logo* (W8) is the row's
    logo data URI, resolved by the caller from the roster entry that
    holds the placed plate -- ``None`` renders no logo image. ``sex``
    (E7) is the solo rider's "M"/"F", None for a team. ``draw`` (R-14)
    is the card the entry drew for the venue's tie-break, absent for
    every entry that drew none.
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
        sex=result.sex,
        tie=placed.draw_required,
        dnf=result.dnf,
        cards=tuple(_card_pair(card) for card in result.hand.best5),
        drawn=tuple(_card_pair(card) for card in result.cards),
        draw=_card_pair(result.tiebreak_card) if result.tiebreak_card is not None else None,
        logo=logo,
    )


def _format_meta(ride: _RideLike) -> str:
    """Compose the page's meta line from the ride (D15: venue/date)."""
    return (
        f"{_format_event_date(ride.event_date)} · {ride.venue} · "
        f"{_finalize_display(ride.lap_km)} km loop"
    )


def _is_team_kind(kind: str) -> bool:
    """Return whether a kind spelling is a team (suffixes ok)."""
    return kind.upper().startswith("TEAM")


def _laps_rows(  # noqa: PLR0913 -- (placed, kind, top, opts): one board's own inputs
    placed: Sequence[Placed], *, kind: str, top: int, opts: ExportOptions
) -> tuple[LapsBoardRow, ...]:
    """Build one kind's laps board from *placed* (standings' rules)."""
    results = [entry.result for entry in placed if entry.result.kind == kind]
    return tuple(
        LapsBoardRow(
            plate=_parse_plate(entry.result.plate),
            entry=entry.result.name,
            laps=entry.result.laps,
            total=_format_duration(entry.result.total_time) if opts.show_times else None,
            type=entry.result.kind.upper(),
        )
        for entry in laps_leaderboard(results, top=top)
    )


def _laps_record_board(placed: Sequence[Placed], opts: ExportOptions) -> tuple[LapsBoardRow, ...]:
    """Build the record's per-kind laps board (teams, then solo)."""
    mixed = any(entry.result.kind == "team" for entry in placed)
    team_rows = _laps_rows(placed, kind="team", top=_TOP_TEAM_ROWS, opts=opts) if mixed else ()
    solo_rows = _laps_rows(
        placed,
        kind="solo",
        top=_TOP_SOLO_ROWS if mixed else _TOP_SOLO_ONLY_ROWS,
        opts=opts,
    )
    return team_rows + solo_rows


def _boards_from_placed(
    placed: Sequence[Placed], opts: ExportOptions
) -> tuple[tuple[LapsBoardRow, ...], tuple[TimeBoardRow, ...]]:
    """Build the leaderboard rows the export options request (E6.4.2).

    The laps board is per kind -- a MIXED field gets one five-row board
    per kind (teams first, then solo) so neither kind can crowd the
    other out; a solo field keeps the top ten. ``laps_board`` renders
    only when ``opts.laps_board`` (rows carry a ``total`` only when
    times are shown, R-63). ``time_board`` is times-only by contract
    and stays one flat top ten (most laps, then shortest total time);
    it is built only when times are shown -- a time board is nothing
    but time data, so ``show_times`` off leaves it empty however
    ``opts.time_board`` is set (R-63).
    """
    results = [p.result for p in placed]
    laps = _laps_record_board(placed, opts) if opts.laps_board else ()
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
        if opts.time_board and opts.show_times
        else ()
    )
    return laps, times


def _sections_from_payload(payload: RacePayload) -> Sections:
    """Partition a payload's rows into its section plan (Spec §8).

    The record-only path -- the golden generator renders a payload with
    no placed standings -- so the laps boards come from the payload's
    own per-kind ``laps_board`` rows, capped per kind defensively.
    """
    teams = tuple(row for row in payload.results if _is_team_kind(row.entry_type))
    solo = tuple(row for row in payload.results if not _is_team_kind(row.entry_type))
    top_solo = _TOP_SOLO_ROWS if teams else _TOP_SOLO_ONLY_ROWS
    return Sections(
        podium_teams=teams[:_PODIUM_ROWS],
        podium_solo=solo[:_PODIUM_ROWS],
        top_teams=teams[:_TOP_TEAM_ROWS],
        top_solo=solo[:top_solo],
        laps_teams=tuple(row for row in payload.laps_board if _is_team_kind(row.type))[
            :_TOP_TEAM_ROWS
        ],
        laps_solo=tuple(row for row in payload.laps_board if not _is_team_kind(row.type))[
            :top_solo
        ],
        time_board=payload.time_board,
        teams=teams,
        solo=solo,
    )


def sections(payload: RacePayload, placed: Sequence[Placed]) -> Sections:
    """Plan the page's sections from the payload and its standings.

    The payload's rows and options decide the layout: any TEAM row makes
    it a team event (MIXED rides and the legacy samples' display
    suffixes alike), so the podiums are per kind (three each) and the
    top lists cap at five each; a solo field keeps today's single top
    ten. The laps boards are recomputed per kind from *placed* with
    :func:`standings.laps_leaderboard` (five each on a team event, ten
    solo) -- never a filter of one combined board, where a kind's rows
    could crowd the other out. ``time_board`` passes through flat.

    Args:
        payload: The export payload; its rows set the layout.
        placed: The ride's ranked standings, one per entry; an empty
            sequence is valid (a record-only plan with no boards).

    Returns:
        The :class:`Sections` both exporters render.
    """
    plan = _sections_from_payload(payload)
    if not payload.options.laps_board:
        return plan
    if plan.teams:
        laps_teams = _laps_rows(placed, kind="team", top=_TOP_TEAM_ROWS, opts=payload.options)
        laps_solo = _laps_rows(placed, kind="solo", top=_TOP_SOLO_ROWS, opts=payload.options)
    else:
        laps_teams = ()
        laps_solo = _laps_rows(placed, kind="solo", top=_TOP_SOLO_ONLY_ROWS, opts=payload.options)
    return replace(plan, laps_teams=laps_teams, laps_solo=laps_solo)


# (ride, placed, opts, generated, team_logos): D15's mapping inputs
def build_payload(  # noqa: PLR0913, PLR0917
    ride: _RideLike,
    placed: Sequence[Placed],
    opts: ExportOptions,
    generated: str | None,
    team_logos: Mapping[str, str] | None = None,
    *,
    self_test_unverified: bool = False,
) -> RacePayload:
    """Build the export payload from a ride and its placed standings.

    The shared results model: :func:`render` builds its page through
    this function, and the full-results PDF maps the same payload. The
    fixture payloads (which carry the golden boards) reach the page
    through ``_render_payload`` (D15). *team_logos* (W8) maps a placed
    entry's plate to its logo data URI -- the roster-entry lookup seam
    the app supplies. *self_test_unverified* (E6.4.3) is the engine's
    flag: when set, the payload carries :data:`SELF_TEST_NOTE` as
    ``self_test_note``, the caption both exporters render.

    Args:
        ride: Ride-like object exposing the six read fields.
        placed: Ranked standings, one per entry.
        opts: Export flags (times/boards/full-field/all-cards).
        generated: The pinned footer stamp; None stamps the local now.
        team_logos: Plate -> logo data URI for the entries that carry
            one; rows without a mapping render no logo.
        self_test_unverified: Whether the ride was finished over a
            failed evaluator self-test.

    Returns:
        The payload the page embeds and both exporters render from.
    """
    logos = team_logos if team_logos is not None else {}
    results = tuple(_result_row_from_placed(p, logo=logos.get(p.result.plate)) for p in placed)
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
        self_test_note=SELF_TEST_NOTE if self_test_unverified else None,
    )


def _logo_data_uri(path: Path | str) -> str:
    """Encode the PNG at *path* as a base64 data URI (R-61)."""
    payload = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:image/png;base64,{payload}"


# D15's frozen signature (ride, placed, opts, logo_src, generated,
# team_logos)
def render(  # noqa: PLR0913
    ride: _RideLike,
    placed: Sequence[Placed],
    opts: ExportOptions,
    *,
    logo_src: str | None = None,
    generated: str | None = None,
    logo_path: Path | str | None = None,
    team_logos: Mapping[str, str] | None = None,
    self_test_unverified: bool = False,
) -> str:
    """Render one finished ride's results as a self-contained HTML page.

    Composes the page's ``EventInfo`` from *ride* (name -> title,
    venue/date -> meta, organizer/scorer, entries/laps/cards tallied
    from *placed*), one ``ResultRow`` per ``Placed``, and the per-kind
    laps / flat time boards when the options request them (E6.4.2),
    then renders the :func:`sections` plan through
    :func:`_render_payload`. ``generated`` pins the footer timestamp
    (defaults to now in the golden pages' style); ``logo_src`` is the
    ride logo as a base64 data URI, falling back to a transparent 1x1
    PNG when absent (D8); ``logo_path`` is the alternative raw-file
    form, base64-encoded when *logo_src* is None. ``team_logos`` (W8)
    maps a placed entry's plate to its logo data URI (the team's card
    bitmap), rendered as a small image in the team's Top teams and
    Full field rows; absent entries render nothing.
    ``self_test_unverified`` (E6.4.3) publishes the self-test note.

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
        team_logos: Plate -> logo data URI for the entries that carry
            one; rows without a mapping render no logo.
        self_test_unverified: Whether the ride was finished over a
            failed evaluator self-test.

    Returns:
        The full HTML page as a string.

    Raises:
        ValueError: An entry's plate is not numeric.
    """
    if logo_src is None and logo_path is not None:
        logo_src = _logo_data_uri(logo_path)
    payload = build_payload(
        ride,
        placed,
        opts,
        generated,
        team_logos=team_logos,
        self_test_unverified=self_test_unverified,
    )
    return _render_payload(payload, logo_src=logo_src, placed=placed)


# ======================================== WordPress fragment (R-61)

# The vendored artifact the fragment inlines: the compiled stylesheet
# with every selector already scoped under the ``.rc-results`` class
# wordpress.html.j2 wraps the fragment in (tools/gen_css.py). Published
# as a page's ``content``, those rules match only inside that wrapper,
# so nothing in them can restyle the site they land in.
_WP_STYLESHEET = "compiled_css_wp"


def render_wordpress(  # noqa: PLR0913 -- render()'s own signature, for a swap-in call site
    ride: _RideLike,
    placed: Sequence[Placed],
    opts: ExportOptions,
    *,
    logo_src: str | None = None,
    generated: str | None = None,
    logo_path: Path | str | None = None,
    team_logos: Mapping[str, str] | None = None,
    self_test_unverified: bool = False,
) -> str:
    """Render one finished ride as a WordPress page-content fragment.

    :func:`render`'s inputs and :func:`render`'s output, in the shape a
    WordPress Page's ``content`` takes: the same payload
    (:func:`build_payload`), the same :func:`sections` plan and the
    same macros, but no document scaffold -- no ``<!DOCTYPE>``,
    ``<html>``, ``<head>`` or ``<body>``, which the publishing site
    already has -- and no ``race-data`` record, which nothing on the
    site reads. Everything is wrapped in ``<div class="rc-results">``,
    and the fragment inlines ``compiled_css_wp``: the vendored
    stylesheet with every selector scoped under that wrapper
    (``tools/gen_css.py``), so publishing these results cannot restyle
    the site's own theme.

    Callers publish the returned string as-is
    (``rivercrossing.wordpress.publish_page(content=…)``).

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
        team_logos: Plate -> logo data URI for the entries that carry
            one; rows without a mapping render no logo.
        self_test_unverified: Whether the ride was finished over a
            failed evaluator self-test.

    Returns:
        The HTML fragment as a string.

    Raises:
        ValueError: An entry's plate is not numeric.
    """
    if logo_src is None and logo_path is not None:
        logo_src = _logo_data_uri(logo_path)
    payload = build_payload(
        ride,
        placed,
        opts,
        generated,
        team_logos=team_logos,
        self_test_unverified=self_test_unverified,
    )
    context = _template_context(
        payload,
        dev=False,
        logo_src=logo_src,
        generated=generated,
        placed=placed,
        stylesheet=_WP_STYLESHEET,
    )
    return _make_environment().get_template("wordpress.html.j2").render(**context)


# ============================================ poster renderer ([5d])

# The poster's per-kind card counts, mirroring ``pdfexport._PosterPDF``:
# the top three of each kind on a team event (two sections of three
# cards still fit one page), the top five on a solo-only field.
_POSTER_PODIUM_ROWS = 3
_POSTER_SOLO_ONLY_ROWS = 5


def _poster_sections(payload: RacePayload) -> tuple[tuple[str, tuple[ResultRow, ...]], ...]:
    """Plan the poster's ``(heading, rows)`` sections from *payload*.

    The same partition and the same per-kind cap ``_PosterPDF.build``
    applies: any team row makes it a team event, so both kinds get a
    titled section of their top three; a solo-only field gets one
    untitled section (its heading cell is empty) of its top five. A
    kind with no rows still yields its section -- the template skips an
    empty row tuple -- so a team-only event renders "Teams" alone.
    """
    teams = tuple(row for row in payload.results if _is_team_kind(row.entry_type))
    solo = tuple(row for row in payload.results if not _is_team_kind(row.entry_type))
    if not teams:
        return (("", solo[:_POSTER_SOLO_ONLY_ROWS]),)
    return (
        ("Teams", teams[:_POSTER_PODIUM_ROWS]),
        ("Solo riders", solo[:_POSTER_PODIUM_ROWS]),
    )


# (ride, placed, opts): the frozen signature plus the logo/generated
# seams
def render_poster(  # noqa: PLR0913
    ride: _RideLike,
    placed: Sequence[Placed],
    opts: ExportOptions,
    *,
    logo_path: Path | str | None = None,
    generated: str | None = None,
    self_test_unverified: bool = False,
) -> str:
    """Render one ride's podium poster as a self-contained page.

    The HTML sibling of ``pdfexport.podium_poster`` ([5d]), rendering
    the same shared model through the same environment the results page
    uses: one card per placing carrying the place number, the
    ``#plate`` entry name (a team card carries none -- its section
    names the kind), the team/solo line, the hand's title-case prose
    and the best-5 card chips. A team event stacks the top three teams
    over the top three solo riders; a solo-only field lists its top
    five, with no section heading to name a kind it does not have.
    A row that drew a tie-break card (R-14) renders that card's badge
    beside its hand prose, as the results page's own podium cards do.

    Args:
        ride: Ride-like object exposing ``name``/``event_date``/
            ``venue``/``lap_km``/``organizer``/``scorer``;
            ``RideConfig`` satisfies it structurally.
        placed: Ranked standings, one per entry (teams and solo riders
            may share the sequence; the kind partitions it).
        opts: Export flags; only ``show_times`` reaches the poster, as
            the card's trailing total time.
        logo_path: Raw PNG path, base64-embedded; None falls back to
            the transparent 1x1 URI (D8).
        generated: The pinned footer stamp; None stamps the local now.
        self_test_unverified: Whether the ride was finished over a
            failed evaluator self-test (E6.4.3).

    Returns:
        The full poster page as a string.

    Raises:
        ValueError: An entry's plate is not numeric.
    """
    payload = build_payload(
        ride, placed, opts, generated, self_test_unverified=self_test_unverified
    )
    context = {
        "event": payload.event,
        "options": payload.options,
        "poster_sections": _poster_sections(payload),
        "logo_src": _logo_data_uri(logo_path) if logo_path is not None else _TRANSPARENT_PNG,
        "logo_alt": payload.event.organizer,
        "self_test_note": payload.self_test_note,
        "compiled_css": _asset_text("compiled_css"),
        "fonts_css": _asset_text("fonts_css"),
    }
    return _make_environment().get_template("poster.html.j2").render(**context)


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


def _optional_card_pair(value: object) -> CardPair | None:
    """Convert a record's optional single ``draw`` pair to its tuple.

    ``None`` -- the key absent, or explicitly null -- is the ordinary
    undrawn row, so the caller keeps ``ResultRow.draw`` unset.
    """
    if value is None:
        return None
    pair = cast("tuple[object, object]", value)
    return (cast("str", pair[0]), cast("str", pair[1]))


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
        sex=cast("str | None", row.get("sex")),
        tie=cast("bool", row.get("tie", False)),
        dnf=cast("bool", row.get("dnf", False)),
        cards=_card_pairs(row.get("cards", [])),
        drawn=_card_pairs(row.get("drawn", [])),
        draw=_optional_card_pair(row.get("draw")),
        logo=cast("str | None", row.get("logo")),
    )


def _laps_board_row_from_record(row: Mapping[str, object]) -> LapsBoardRow:
    """Build one ``LapsBoardRow`` from its camelCase record row.

    ``type`` is read with the empty default so a pre-kind record still
    parses (the committed samples regenerate with it).
    """
    return LapsBoardRow(
        plate=cast("int", row["plate"]),
        entry=cast("str", row["entry"]),
        laps=cast("int", row["laps"]),
        total=cast("str | None", row.get("total")),
        type=cast("str", row.get("type", "")),
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
        self_test_note=cast("str | None", record.get("selfTestNote")),
    )
