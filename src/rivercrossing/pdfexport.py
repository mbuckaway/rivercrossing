# SPDX-License-Identifier: GPL-3.0-only
"""PDF results report: fpdf2 renderer for a finished ride (spec §8b).

One shared model, two renderers: :func:`render` builds the export
payload through ``rivercrossing.htmlexport.build_payload`` and renders
the same per-kind section plan (``htmlexport.sections``) the HTML page
renders -- per-kind podiums (3/3 on a team event, 3 solo), per-kind
top lists (5/5 or 10), per-kind laps boards (5/5 or 10), a flat
ten-row time board, and the full field under its umbrella heading with
"Teams"/"Solo riders" subsections. Teams never show a plate: the
section name carries the kind, exactly as on the page. Flags mirror
the HTML (R-63): cover block, DNF marks, all-cards-drawn sub-rows and
Total/Best-lap columns only when ``ExportOptions.show_times`` is on.
Only the *layout* stays per-format -- the retired designs' print
geometry ([5a]-[5c]): Letter (A4 via ``letter=False``), 0.58in
margins, footer rule + "Page n of N · Generated … · RiverCrossing" on
every page, a one-line running title from page 2 on, Barlow + Barlow
Condensed headings + DejaVu Sans suit glyphs (``♠♥♦♣★``), corner
registration marks (two 11pt hairlines per corner), and the industry
tokens ink ``#1D1F20``, steel ``#416180`` (hearts/diamonds/jokers and
hand names), deep steel ``#1D2D3D`` (the P1 podium plate) -- no red.
The poster is the same shared model: a team event stocks one Letter
page with two compact titled sections ("Teams" top 3, then "Solo
riders" top 3); a solo event lists the top five at full card sizing.

Determinism (R-62, D14) is the module's load-bearing contract.
:func:`render` embeds exactly one timestamp -- the ``created_at``
stamp, required tz-aware UTC so ``/CreationDate`` never bakes a
machine-local offset -- and formats the footer's visible stamp with
:func:`rivercrossing.htmlexport.format_generated`, a pure function of
that stamp: no local-time conversion, so identical inputs plus the
same stamp produce byte-identical files on every machine.
``tools/gen_pdfexport_fixtures.py`` freezes the committed golden from
this renderer.

Public API (module-skeletons.md): :func:`render` writes the report to
the caller-supplied *path*; :func:`podium_poster` writes the one-page
prize-table poster ([5d]) the same way. The ``{ride-slug}-results.pdf``
and ``{ride-slug}-podium.pdf`` naming is the menu handler's job, never
this module's. Pure Python -- no ``wx`` (R-71), no ``ui`` import.
"""

import base64
import io
import os
import re
import zlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from rivercrossing import htmlexport
from rivercrossing.cards import Suit
from rivercrossing.standings import EntryResult, hand_name

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rivercrossing.cards import Card, Rank
    from rivercrossing.hands import EvaluatedHand
    from rivercrossing.htmlexport import (
        CardPair,
        ExportOptions,
        LapsBoardRow,
        RacePayload,
        ResultRow,
        Sections,
        TimeBoardRow,
    )
    from rivercrossing.standings import Placed

__all__ = ["podium_poster", "render"]

# ------------------------------------------------------------- tokens

# The Industry tokens as RGB (ui-designs-retired.md [5c]): ink for
# body text and spades/clubs, steel for hearts/diamonds/jokers and
# hand names, deep steel for the P1 podium plate. No red anywhere.
_INK = (29, 31, 32)  # #1D1F20
_STEEL = (65, 97, 128)  # #416180
_DEEP_STEEL = (29, 45, 61)  # #1D2D3D

# Print geometry ([5c]): Letter 8.5x11in (A4 selectable), 0.58in
# margins. The auto-page-break margin leaves the footer room below the
# content so a row can never collide with the footer rule.
_MARGIN_IN = 0.58
_FOOTER_GAP_IN = 0.82

# The podium/place special cases PLR2004's named constants demand.
_FIRST_PAGE = 1
_FIRST_PLACE = 1

_ROW_HEIGHT = 0.24

# The Best-5 card column's width, in inches: wide enough for a "★
# JOKER" joker face alongside the hand name without clipping. The
# team field's hand column then takes the remaining content width.
_CARDS_COL = 1.60

# A team row's inline logo (R-61's base64 card bitmap) at the HTML's
# compact inline size.
_INLINE_LOGO = 0.14

# ------------------------------------------------------------- fonts

# fpdf2 embeds each TTF into the PDF bytes (D14): absolute paths
# resolved from this module's own ``fonts/`` directory -- bare
# filenames only search the cwd. ``uni=`` is a deprecated no-op in
# 2.8.8 and is deliberately absent. Barlow Condensed SemiBold
# registers as the bold style of "Barlow", so headings are
# ``set_font("Barlow", "B", size)``; DejaVu Sans supplies the suit
# glyphs no Barlow face carries.
_FONTS_DIR = Path(__file__).resolve().parent / "pdfexport" / "fonts"
_FONT_BODY = "Barlow"
_FONT_HEADING = "Barlow"
_FONT_GLYPH = "DejaVu"
_FONT_FILES: tuple[tuple[tuple[str, str], Path], ...] = (
    ((_FONT_BODY, ""), _FONTS_DIR / "Barlow-Regular.ttf"),
    ((_FONT_BODY, "B"), _FONTS_DIR / "BarlowCondensed-SemiBold.ttf"),
    ((_FONT_GLYPH, ""), _FONTS_DIR / "DejaVuSans.ttf"),
)

# -------------------------------------------------------- display copy

_RUNNING_TITLE_SEP = " — Official results"

# cards.Rank's integer values to the display rank letters; the ten is
# "10" -- the golden pages' own spelling, and Card.code()'s too.
_RANK_LETTER: dict[int, str] = {
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
_SUIT_GLYPH: dict[Suit, str] = {
    Suit.SPADES: "♠",
    Suit.HEARTS: "♥",
    Suit.DIAMONDS: "♦",
    Suit.CLUBS: "♣",
}
_JOKER_TEXT = "★ JOKER"

# The payload's own suit letters (``CardPair``'s second element) to the
# glyphs above -- derived, never a second glyph table.
_SUIT_GLYPH_BY_LETTER: dict[str, str] = {
    suit.value.lower(): glyph for suit, glyph in _SUIT_GLYPH.items()
}

# ------------------------------------------------------- text helpers


def _format_km(lap_km: float) -> str:
    """Format ``lap_km`` as "8" for 8.0, "8.5" otherwise (D5)."""
    if float(lap_km).is_integer():
        return str(int(lap_km))
    return str(lap_km)


def _pair_text(pair: CardPair) -> str:
    """Return one pair's text: rank letter + suit glyph, or ★ JOKER."""
    rank, suit = pair
    if rank == "JK":
        return _JOKER_TEXT
    return f"{rank}{_SUIT_GLYPH_BY_LETTER[suit]}"


def _pair_is_steel(pair: CardPair) -> bool:
    """Return True for a payload pair in the steel accent."""
    rank, suit = pair
    return rank == "JK" or suit in ("h", "d")


# The muted whole-hand sub-row's sizing: the DejaVu glyph face -- the
# one that carries the suit glyphs -- at 6.5pt on a 0.12in leading plus
# a 0.04in trailing gap. The poster scales all three by its card
# geometry, whose compact cards are drawn at 68%.
_DRAWN_SIZE = 6.5
_DRAWN_LEADING = 0.12
_DRAWN_GAP = 0.04


def _drawn_text(cards: Sequence[CardPair]) -> str:
    """Return the whole-hand line the field and both podiums share.

    "All N cards, in draw order: …" -- one spelling for the full
    field's sub-row and both podium cards, so the three surfaces can
    never drift apart on the wording.
    """
    run = " ".join(_pair_text(pair) for pair in cards)
    return f"All {len(cards)} cards, in draw order: {run}"


def _is_team_row(row: ResultRow) -> bool:
    """Return whether a payload row is a team."""
    return row.entry_type.upper().startswith("TEAM")


def _decode_logo(logo: str) -> io.BytesIO:
    """Decode a payload logo data URI into the bytes fpdf2 embeds.

    The shared model carries the logo as a ``data:image/png;base64,``
    URI; fpdf2 wants a path or a binary stream, so the base64 body is
    decoded here. Malformed input fails loudly in fpdf2's own parser.
    """
    encoded = logo.partition(",")[2]
    return io.BytesIO(base64.b64decode(encoded))


def _card_text(card: Card) -> str:
    """Return one card's text: rank letter + suit glyph, or ★ JOKER."""
    if card.joker:
        return _JOKER_TEXT
    rank = cast("Rank", card.rank)
    suit = cast("Suit", card.suit)
    return f"{_RANK_LETTER[rank.value]}{_SUIT_GLYPH[suit]}"


def _is_steel_card(card: Card) -> bool:
    """Return True for hearts/diamonds/jokers (the steel accent)."""
    return card.joker or card.suit in (Suit.HEARTS, Suit.DIAMONDS)


def _poster_card_text(card: Card) -> str:
    """Return one large poster face: rank+suit, or ★ JOKER for a joker.

    Natural cards reuse :func:`_card_text`'s "9♠" spelling; the joker
    spells its face out (the [5d] mock's own "★JOKER", matching the
    report's "★ JOKER" chip).
    """
    if card.joker:
        return _JOKER_TEXT
    return _card_text(card)


def _hand_prose(hand: EvaluatedHand) -> str:
    """Return *hand*'s title-case prose name, or "" for a no-card hand.

    The poster shows the D1 prose casing (mock [5d]'s ALL-CAPS is a
    mock artifact, prose wins); the report's table cells uppercase the
    same prose via :func:`_hand_label`.
    """
    if not hand.best5:
        return ""
    return hand_name(hand)


def _hand_label(hand: str) -> str:
    """Return the payload's hand prose as the table cell's text.

    The shared model carries the prose (``standings.hand_name``); the
    HTML page uppercases it with CSS ``text-transform``, which fpdf2
    has no equivalent of, so the report uppercases in Python. A no-show
    entry's prose is the empty string, which renders blank.
    """
    return hand.upper()


def _draw_marker(row: ResultRow) -> str:
    """Return a row's drawn tie-break card marker, "" when it drew none.

    R-14's draw rides after the hand prose in the same cell --
    "THREE OF A KIND — NINES · DRAW A♥" -- so no layout width moves.
    The separator is dropped when there is no hand prose to lead with,
    so a no-show entry's cell reads "DRAW ★ JOKER" rather than
    starting with one. The caller draws the marker as its own run, in
    the DejaVu face, because Barlow carries no suit glyph (measured:
    fpdf2 drops ♥ and ★ from it); the two runs share the one column's
    width.

    A DNS row (Phase 7) drew nothing: it never started, so the card
    the finish's draw recorded against its tied empty hand is refused
    here -- the rule holds even for a record built with ``dns`` and
    ``draw`` both set, which the parsed ``race-data`` path can carry.
    """
    if row.dns or row.draw is None:
        return ""
    card = _pair_text(row.draw)
    return f" · DRAW {card}" if row.hand else f"DRAW {card}"


def _laps_cell(row: ResultRow) -> str:
    """Return a row's Laps cell: the word "DNS", else the count.

    Phase 7: a DNS row never started, so its cell names that instead of
    printing the 0 laps it recorded -- matching the page's own row
    macros and the standings CSV.
    """
    return "DNS" if row.dns else str(row.laps)


def _time_cell(row: ResultRow, value: str | None) -> str:
    """Return a Total time / Best lap cell: blank for a DNS row.

    Phase 7: a DNS entry never started, so it holds no reading at all
    (the paired payload row's ``total``/``best_lap`` are ``None``); the
    column itself stays, so no table width moves. A ``None`` from any
    other source renders blank rather than the text "None".
    """
    if row.dns:
        return ""
    return value if value is not None else ""


def _poster_subtitle(result: EntryResult) -> str:
    """Compose the poster's team/solo line from one result.

    The [5d] mock's "Team of 4 -- member names" lists rider names and
    a team size that ``EntryResult`` does not carry (the same seam
    htmlexport documents for "TEAM by 4"), so the line names the
    entry itself from the payload: "Solo (M) -- Luca Ferrari · 10
    laps" or "Team -- Dirt Dynamos · 10 laps". A solo's sex (E7) is
    the rider's own "M"/"F"; a team's is None (no single sex), so its
    line is unchanged.
    """
    kind = "Team" if result.kind == "team" else "Solo"
    sex = f" ({result.sex})" if result.sex else ""
    return f"{kind}{sex} — {result.name} · {result.laps} laps"


class _RideLike(Protocol):
    """The ride fields :func:`render` reads (documented seam, D15).

    ``RideConfig`` satisfies this structurally (the read-only property
    members match its frozen fields); the Protocol lets the
    render tests pass a tiny stub instead of constructing a full ride.
    Read fields: ``name`` -> title, ``event_date``/``venue``/``lap_km``
    -> meta, ``organizer``/``scorer`` -> footer credits.
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


def _top_ten_widths(*, show_times: bool, content: float) -> list[float]:
    """Return the solo top-list table's column widths, in inches.

    Place|Plate|Entry|Laps|[Total time]|Best 5 cards|Hand -- the solo
    event's "Top ten" and the team event's "Top solo riders" table.
    """
    widths = [0.40, 0.62, 1.50, 0.45]
    if show_times:
        widths.append(0.90)
    widths += [_CARDS_COL, content - sum(widths) - _CARDS_COL]
    return widths


def _team_top_widths(*, show_times: bool, content: float) -> list[float]:
    """Return the Top teams table's column widths, in inches.

    Place|Entry|Laps|[Total time]|Best 5 cards|Hand -- no Plate column,
    matching the HTML: the section names the kind, so a team's plate has
    no cell to sit in.
    """
    widths = [0.40, 1.80, 0.45]
    if show_times:
        widths.append(0.90)
    widths += [_CARDS_COL, content - sum(widths) - _CARDS_COL]
    return widths


def _field_widths(*, show_times: bool, content: float) -> list[float]:
    """Return the solo full-field table's column widths, in inches.

    Place|Plate|Entry|Type|Laps|[Total time|Best lap]|Best hand; the
    final column holds the inline cards plus the hand name.
    """
    widths = [0.35, 0.58, 1.30, 0.62, 0.40]
    if show_times:
        widths += [0.85, 0.80]
    widths.append(content - sum(widths))
    return widths


def _team_field_widths(*, show_times: bool, content: float) -> list[float]:
    """Return the Teams full-field table's column widths, in inches.

    Place|Entry|Laps|[Total time|Best lap]|Cards|Hand -- compact, no
    Plate or Type column, and cards split from the hand name so a team
    row reads like the HTML's own compact team row.
    """
    widths = [0.35, 1.40, 0.40]
    if show_times:
        widths += [0.85, 0.80]
    widths += [_CARDS_COL, content - sum(widths) - _CARDS_COL]
    return widths


def _laps_widths(*, show_times: bool, content: float) -> list[float]:
    """Return the solo "Most laps" board's column widths, in inches."""
    widths = [0.35, 0.58, 0.60]
    if show_times:
        widths.append(0.90)
    widths.insert(2, content - sum(widths))
    return widths


def _team_laps_widths(*, show_times: bool, content: float) -> list[float]:
    """Return the team "Most laps" board's column widths, in inches.

    #|Entry|Laps|[Total] -- no plate cell: the board's title names the
    kind, exactly as the HTML macro's ``show_plate=false`` does.
    """
    widths = [0.35, 0.60]
    if show_times:
        widths.append(0.90)
    widths.insert(1, content - sum(widths))
    return widths


def _time_widths(content: float) -> list[float]:
    """Return the "Fastest" board's column widths, in inches."""
    widths = [0.35, 0.58, 0.90, 0.95, 1.05]
    widths.insert(2, content - sum(widths))
    return widths


# ------------------------------------------------------ cell styles


@dataclass(frozen=True, slots=True)
class _TextStyle:
    """One table cell's font, weight, size, color and alignment."""

    font: str
    size: float
    color: tuple[int, int, int]
    bold: bool = False
    align: str = "L"


@dataclass(frozen=True, slots=True)
class _Cell:
    """One text cell: geometry and style, drawn at the current x."""

    width: float
    text: str
    style: _TextStyle
    height: float = _ROW_HEIGHT


_ROW_STYLE = _TextStyle(_FONT_BODY, 7.5, _INK)
_ROW_BOLD = _TextStyle(_FONT_BODY, 7.5, _INK, bold=True)
_ROW_RIGHT = _TextStyle(_FONT_BODY, 7.5, _INK, align="R")
_FIELD_STYLE = _TextStyle(_FONT_BODY, 7.0, _INK)
_FIELD_BOLD = _TextStyle(_FONT_BODY, 7.0, _INK, bold=True)
_HAND_STYLE = _TextStyle(_FONT_HEADING, 6.5, _STEEL, bold=True)
# The report's podium card hand line -- the [5a] card's larger cell.
# The poster card sizes its own, scaled to the card it sits in.
_PODIUM_HAND_STYLE = _TextStyle(_FONT_HEADING, 9.5, _STEEL, bold=True)
_BOARD_TOTAL = _TextStyle(_FONT_BODY, 7.5, _INK, bold=True)


def _marker_style(hand_style: _TextStyle) -> _TextStyle:
    """Return the drawn-card marker style for a hand run's own size.

    R-14's marker is drawn in DejaVu -- the face that carries the suit
    glyphs and the joker's "★ JOKER" text Barlow lacks (measured: fpdf2
    drops ♥ and ★ from it) -- at the hand prose's size, so the two runs
    read as one line whatever cell they ride in. Never bold: DejaVu is
    registered for the regular style only.
    """
    return _TextStyle(_FONT_GLYPH, hand_style.size, _STEEL)


def _set_font(pdf: FPDF, style: _TextStyle) -> None:
    """Select one text style's font face, weight and size."""
    pdf.set_font(style.font, "B" if style.bold else "", style.size)


def _cell_text(pdf: FPDF, cell: _Cell) -> None:
    """Draw one styled text cell at the current x."""
    _set_font(pdf, cell.style)
    pdf.set_text_color(*cell.style.color)
    pdf.cell(cell.width, cell.height, text=cell.text, align=cell.style.align)


def _hand_runs(pdf: FPDF, cell: _Cell, marker: str) -> None:
    """Draw a hand prose cell and, after it, its drawn-card marker run.

    *cell* carries the hand prose with its style, width and height;
    *marker* is R-14's drawn-card run, drawn in DejaVu at the prose's
    own size so the two fonts share one column and no table geometry
    moves. The prose is measured too -- the marker sits where the prose
    ends, not at the column's right edge -- and is bounded by the cell
    so a long hand and a drawn card cannot push each other off the
    margin. An empty marker draws the single cell exactly as before,
    which is every undrawn row.
    """
    style = cell.style
    if not marker:
        _cell_text(pdf, cell)
        return
    marker_style = _marker_style(style)
    _set_font(pdf, marker_style)
    marker_width = pdf.get_string_width(marker)
    _set_font(pdf, style)
    hand_width = min(pdf.get_string_width(cell.text), max(cell.width - marker_width, 0.0))
    _cell_text(pdf, _Cell(hand_width, cell.text, style, height=cell.height))
    _cell_text(pdf, _Cell(marker_width, marker, marker_style, height=cell.height))


# ------------------------------------------------------------ document


def _open_document(pdf: FPDF, title: str, *, created_at: datetime) -> None:
    """Open one deterministic PDF: geometry, fonts, metadata (D14).

    Shared by the report and poster documents: the Letter/A4 geometry
    with 0.58in margins, the pinned aware-UTC creation date and empty
    creator/author/subject so nothing environment-derived leaks into
    the bytes, and the three embedded TTF faces. The caller chooses
    the paper format in its own ``super().__init__`` first.

    Raises:
        ValueError: *created_at* is naive -- D14: a naive stamp bakes
            the machine's local offset into /CreationDate and breaks
            cross-OS byte-identity.
    """
    if created_at.tzinfo is None:
        msg = "created_at must be tz-aware (D14: a naive stamp bakes a local offset)"
        raise ValueError(msg)
    # Stream compression off (R-62/D14). Deflate output is not
    # canonical across zlib builds: the python.org Windows builds link
    # zlib-ng while the macOS builds use the platform zlib, and the two
    # emit different bytes for identical input (measured: no zlib-ng
    # level reproduces a macOS-compressed golden). A compressed stream
    # would make the byte-for-byte golden tests fail on one OS or the
    # other, so the streams are stored raw -- deterministic by
    # construction, at the cost of a larger file.
    pdf.set_compression(False)
    pdf.set_margins(_MARGIN_IN, _MARGIN_IN, _MARGIN_IN)
    pdf.set_auto_page_break(True, margin=_FOOTER_GAP_IN)
    pdf.set_creation_date(created_at)
    pdf.set_creator("")
    pdf.set_author("")
    pdf.set_subject("")
    pdf.set_title(title)
    for (family, style), fname in _FONT_FILES:
        pdf.add_font(family, style, str(fname))


def _draw_registration_marks(pdf: FPDF, *, top: bool) -> None:
    """Draw the two 11pt hairlines at each margin-box corner.

    A short horizontal and a short vertical tick -- the print-shop
    registration L -- at all four corners, on every page. Shared by
    the report and poster documents.
    """
    m = _MARGIN_IN
    tick = 11.0 / 72.0
    pdf.set_draw_color(*_INK)
    pdf.set_line_width(0.5 / 72.0)
    left = m
    right = pdf.w - m
    y = m if top else pdf.h - m
    vert = -tick if top else tick
    pdf.line(left, y, left - tick, y)
    pdf.line(left, y, left, y + vert)
    pdf.line(right, y, right + tick, y)
    pdf.line(right, y, right, y + vert)


def _draw_rule(pdf: FPDF) -> None:
    """Draw the section rule across the content at the current y."""
    y = pdf.get_y()
    pdf.set_draw_color(*_INK)
    pdf.set_line_width(0.75 / 72.0)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)


def _draw_logo(pdf: FPDF, logo_path: Path | str | None) -> None:
    """Draw the organizer logo at the top right when one is set (5c).

    ``logo_path`` is the caller-supplied PNG; when absent the header
    renders as today (the golden fixtures carry no logo, so the
    byte-frozen outputs are untouched).
    """
    if logo_path is None:
        return
    width = 0.8
    pdf.image(
        str(logo_path),
        x=pdf.w - pdf.r_margin - width,
        y=pdf.t_margin,
        w=width,
    )


def _maybe_page_break(pdf: FPDF, height: float) -> None:
    """Add a page if a *height*-tall block would cross the break."""
    if pdf.will_page_break(height):
        pdf.add_page()


def _deflate_body(
    pdf: bytes, dict_span: tuple[int, int], body_start: int
) -> tuple[int, bytes] | None:
    """Return one stream's end offset and inflated bytes, or None.

    The dict's ``/Length`` is authoritative when it yields a body that
    inflates: a compressed body can itself contain an
    ``endstream``-looking byte run (measured -- an 8.5KB Barlow subset
    does), so searching for the keyword first mis-slices the body and
    leaves the stream compressed, which would break R-62's
    cross-platform byte-identity. The keyword search is the fallback
    for a document whose ``/Length`` is a placeholder.
    """
    dict_start, dict_end = dict_span
    candidates: list[int] = []
    length_match = re.search(rb"/Length (\d+)", pdf[dict_start:dict_end])
    if length_match is not None:
        candidates.append(body_start + int(length_match.group(1)))
    keyword_end = pdf.find(b"endstream", body_start)
    if keyword_end != -1:
        candidates.append(keyword_end)
    for body_end in candidates:
        body = pdf[body_start:body_end]
        if body.endswith(b"\r\n"):
            body = body[:-2]
        elif body.endswith(b"\n"):
            body = body[:-1]
        try:
            return body_end, zlib.decompress(body)
        except zlib.error:
            continue
    return None


def _raw_stream_span(
    pdf: bytes, stream_match: re.Match[bytes]
) -> tuple[int, int, int, bytes] | None:
    """Return one FlateDecode stream's replacement span, or None.

    The span is ``(start, body_start, body_end, replacement)``; scans
    back from *stream_match* to the owning object dict. None when the
    stream is not FlateDecode or does not deflate. The replacement is
    the dict (``/Filter`` dropped, ``/Length`` updated) plus the raw
    body.
    """
    dict_start = pdf.rfind(b"<<", 0, stream_match.start())
    dict_end = pdf.find(b">>", dict_start, stream_match.start())
    if dict_end == -1 or b"/FlateDecode" not in pdf[dict_start:dict_end]:
        return None
    body_start = stream_match.end()
    resolved = _deflate_body(pdf, (dict_start, dict_end), body_start)
    if resolved is None:
        return None  # not really deflate despite the filter entry
    body_end, raw = resolved
    dict_text = pdf[dict_start:dict_end]
    # fpdf2 emits one entry per line: "/Filter /FlateDecode\n".
    dict_text = dict_text.replace(b"/Filter /FlateDecode\n", b"")
    dict_text = re.sub(
        rb"/Length \d+",
        b"/Length " + str(len(raw)).encode("ascii"),
        dict_text,
        count=1,
    )
    replacement = dict_text + b">>\nstream\n" + raw + b"\nendstream"
    return dict_start, body_start, body_end, replacement


def _store_streams_raw(pdf: bytes) -> bytes:
    """Return *pdf* with every FlateDecode stream stored uncompressed.

    Deflate is not canonical across zlib builds: the python.org
    Windows builds link zlib-ng and the macOS builds use the platform
    zlib, and the two emit different bytes for identical input, so a
    compressed stream would break R-62's byte-identity on one OS or
    the other (measured: no zlib-ng level reproduces a macOS-compressed
    golden). fpdf2's ``set_compression(False)`` covers the page streams,
    but the CIDToGIDMap and the embedded font programs are written with
    a hardcoded ``compress=True`` and no public switch -- this pass
    finishes the job via :func:`_raw_stream_span`, then rebuilds the
    classic xref table plus ``startxref`` pointer for the moved object
    offsets. Streams stored raw are byte-identical across platforms by
    construction.

    Args:
        pdf: fpdf2 output bytes (classic xref table, no xref streams).

    Returns:
        The same document with uncompressed streams.
    """
    spans: list[tuple[int, int, int, bytes]] = []  # (start, body_start, end, replacement)
    bodies: list[tuple[int, int]] = []
    for match in re.finditer(rb"stream\r?\n", pdf):
        span = _raw_stream_span(pdf, match)
        if span is None:
            continue
        start, body_start, end, replacement = span
        spans.append((start, body_start, end, replacement))
        bodies.append((body_start, end))
    if not spans:
        return pdf

    buffer = pdf
    for start, _body_start, end, replacement in reversed(spans):
        buffer = buffer[:start] + replacement + buffer[end:]

    # Rebuild the classic xref table: the object offsets moved when the
    # streams grew. Text outside the stream bodies is preserved
    # verbatim, so take each object marker from the original buffer and
    # shift it by the net length change of the spans before it (scanning
    # the new buffer would risk matching "N 0 obj" inside raw binary
    # streams).
    deltas = [(len(replacement) - (end - start)) for start, _body_start, end, replacement in spans]
    objects: list[tuple[int, int]] = []  # (original offset, object number)
    for marker in re.finditer(rb"\n(\d+) 0 obj", pdf):
        # logic-coverage-exempt: T-3 -- True outcome unreachable in
        # this function's real input universe (fpdf2 output): every
        # stream body in the scanned original is deflate-compressed,
        # so the marker's ASCII pattern never occurs there. Only a
        # synthetic PDF could hit it, and crafting one needs
        # platform-specific deflate bytes -- the zlib-ng divergence
        # this pass exists to remove.
        if any(b_start <= marker.start() < b_end for b_start, b_end in bodies):
            continue
        # The xref offset must point at the object's first byte, not
        # the newline the pattern anchors on.
        objects.append((marker.start() + 1, int(marker.group(1))))
    count = max(number for _offset, number in objects) + 1
    shifted: list[tuple[int, int]] = []  # (object number, new offset)
    delta_index = 0
    for offset, number in objects:
        while delta_index < len(spans) and spans[delta_index][0] < offset:
            delta_index += 1
        shifted.append((number, offset + sum(deltas[:delta_index])))
    entries = [b"0000000000 65535 f \n"]
    for _number, new_offset in sorted(shifted):
        entries.append(b"%010d 00000 n \n" % new_offset)
    # Line-boundary anchors: a bare rfind("xref\n") would match the
    # "xref" inside "startxref\n", which is the last such occurrence.
    xref_match = list(re.finditer(rb"\nxref\n", buffer))[-1]
    xref_pos = xref_match.start() + 1
    trailer_match = list(re.finditer(rb"\ntrailer\n", buffer))[-1]
    trailer_pos = trailer_match.start() + 1
    trailer_end = list(re.finditer(rb"\nstartxref\n", buffer))[-1].start() + 1
    trailer = buffer[trailer_pos:trailer_end]
    xref = b"xref\n0 %d\n" % count + b"".join(entries)
    return (
        buffer[:xref_pos]
        + xref
        + trailer
        + b"startxref\n"
        + str(xref_pos).encode("ascii")
        + b"\n%%EOF\n"
    )


@dataclass(frozen=True, slots=True)
class _CardGeom:
    """A poster card's scale and its page-break guard, in inches.

    ``scale`` multiplies every card dimension (place number, name,
    subtitle, hand, whole-hand line, card faces); ``guard`` is the
    height the break check reserves before a card -- each sizing's own
    pitch (1.68 x ``scale``) plus a hair of slack.
    """

    scale: float
    guard: float


# The [5d] poster's two card sizings. Every card now carries the
# whole-hand line under its hand line, which lengthens its pitch from
# 1.52 x scale to 1.68 x scale, so both sizings came down to keep the
# poster one Letter page in its worst case -- with the organizer logo's
# own 0.42in of header. Measured with the logo drawn: a team event's
# six compact cards at 0.68 end 0.73in above the footer gap, and a solo
# event's five full cards at 0.92 end 0.25in above it even with the
# self-test note's own 0.20in.
_CARD_FULL = _CardGeom(0.92, 1.62)
_CARD_COMPACT = _CardGeom(0.68, 1.2)


def _poster_name(result: EntryResult) -> str:
    """Return a poster card's name line, plate-less for a team.

    The section heading names the kind, so a team card carries only its
    name; a solo card keeps the ``#plate`` prefix ([5d]).
    """
    if result.kind == "team":
        return result.name
    return f"#{result.plate} {result.name}"


class _PosterPDF(FPDF):
    """The one-page podium poster document ([5d]).

    A sibling of :class:`_ReportPDF` sharing its geometry, fonts and
    D14 metadata, fixed to a single celebratory Letter page: event
    meta, a "Best poker hands" heading + ride title, then the shared
    model's per-kind top three. A team event stacks two titled,
    compact sections -- "Teams" (no plate) then "Solo riders" -- so
    six cards still fit the one page; a solo event lists the top five
    at full card sizing. The footer is a credit line + generated stamp
    -- no "Page n of N", there is only one page.
    """

    # (ride, letter, created_at, logo_path, self_test_unverified,
    # all_cards): the poster's state inputs
    def __init__(  # noqa: PLR0913
        self,
        ride: _RideLike,
        *,
        letter: bool,
        created_at: datetime,
        logo_path: Path | str | None,
        self_test_unverified: bool = False,
        all_cards: bool = True,
    ) -> None:
        """Open one poster: geometry, fonts, metadata, footer stamp.

        Raises:
            ValueError: *created_at* is naive -- D14, same as the
                report document.
        """
        super().__init__(unit="in", format="Letter" if letter else "A4")
        _open_document(self, ride.name, created_at=created_at)
        self._ride = ride
        self._generated = htmlexport.format_generated(created_at)
        self._logo_path = logo_path
        self._self_test_unverified = self_test_unverified
        self._all_cards = all_cards

    def file_id(self) -> None:
        """Suppress the trailer /ID (R-62 determinism).

        fpdf2 derives the default /ID by hashing the assembled buffer,
        whose streams are compressed with the platform zlib -- the hash
        would therefore leak the build's zlib flavour into the bytes.
        Returning None emits no /ID at all.
        """

    def header(self) -> None:
        """Draw the corner marks; a one-pager has no running title."""
        _draw_registration_marks(self, top=True)
        self.set_y(self.t_margin)

    def footer(self) -> None:
        """Draw the footer rule, credits and stamp; no page count."""
        _draw_registration_marks(self, top=False)
        self.set_y(-_MARGIN_IN)
        self.set_draw_color(*_INK)
        self.set_line_width(0.5 / 72.0)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(0.07)
        self.set_font(_FONT_BODY, "", 7)
        self.set_text_color(*_STEEL)
        credits_line = f"Organizer: {self._ride.organizer} · Scorer: {self._ride.scorer}"
        self.cell(0, 0.12, text=credits_line)
        self.ln(0.13)
        self.cell(0, 0.12, text=f"{self._generated} · RiverCrossing")

    def build(self, placed: Sequence[Placed]) -> None:
        """Draw the header block and the poster's cards.

        The shared model supplies the cover meta (``EventInfo.meta``)
        and the R-14 drawn cards (each payload row's own ``draw``);
        the partition by ``EntryResult.kind`` picks the layout (any
        team entry is a team event, the exporters' own rule). The
        payload maps *placed* position for position, so the pair the
        cards draw from is a plain zip of the two.

        The cards come from the RANKED rows only: a DNS row (Phase 7)
        was never placed, so it is not a podium finisher -- and because
        ``_cards`` numbers its cards by position, a DNS row left in
        would borrow a place it never earned. The team-event decision
        still reads the whole partition, so a field whose teams are all
        DNS is still a team event (its "Teams" heading is skipped, that
        section having no cards).
        """
        payload = htmlexport.build_payload(
            self._ride,
            placed,
            htmlexport.ExportOptions(),
            self._generated,
            self_test_unverified=self._self_test_unverified,
        )
        self.add_page()
        self._header_block(payload.event.meta, payload.self_test_note)
        cards = list(zip(placed, payload.results, strict=True))
        ranked = [card for card in cards if not card[0].dns]
        teams = [card for card in ranked if card[0].result.kind == "team"]
        solo = [card for card in ranked if card[0].result.kind != "team"]
        if not any(card[0].result.kind == "team" for card in cards):
            self._cards(solo[: _FIRST_PLACE + 4], geom=_CARD_FULL)
            return
        if teams:
            self._section_head("Teams")
            self._cards(teams[: _FIRST_PLACE + 2], geom=_CARD_COMPACT)
        if solo:
            self._section_head("Solo riders")
            self._cards(solo[: _FIRST_PLACE + 2], geom=_CARD_COMPACT)

    def _header_block(self, meta: str, note: str | None) -> None:
        """Draw the event meta, "Best poker hands" heading and title.

        E6.4.3: *note* is the self-test caption, drawn under the rule
        where the poster's own text block ends -- the report cover's
        note seam, on the poster's one page.
        """
        _draw_logo(self, self._logo_path)
        if self._logo_path is not None:
            self.ln(0.42)
        self.set_font(_FONT_BODY, "", 9)
        self.set_text_color(*_STEEL)
        self.cell(0, 0.16, text=meta)
        self.ln(0.22)
        self.set_font(_FONT_HEADING, "B", 22)
        self.set_text_color(*_INK)
        self.cell(0, 0.34, text="Best poker hands")
        self.ln(0.40)
        self.set_font(_FONT_HEADING, "B", 13)
        self.set_text_color(*_INK)
        self.cell(0, 0.22, text=self._ride.name)
        self.ln(0.26)
        _draw_rule(self)
        self.ln(0.12)
        if note is not None:
            self.set_font(_FONT_BODY, "", 8.5)
            self.set_text_color(*_STEEL)
            self.multi_cell(0, 0.14, text=note, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.ln(0.06)

    def _section_head(self, text: str) -> None:
        """Draw a poster section head ("Teams"/"Solo riders")."""
        _maybe_page_break(self, 0.5)
        self.ln(0.04)
        self.set_font(_FONT_HEADING, "B", 13)
        self.set_text_color(*_INK)
        self.cell(0, 0.22, text=text)
        self.ln(0.26)

    def _cards(self, cards: Sequence[tuple[Placed, ResultRow]], *, geom: _CardGeom) -> None:
        """Draw a section's cards with places enumerated from 1.

        Each element pairs the placement (for its Card objects and run
        fields) with the shared payload row for the same entry -- the
        row that carries R-14's drawn card.
        """
        for index, card in enumerate(cards):
            self._podium_card(card, place=index + _FIRST_PLACE, geom=geom)

    def _podium_card(self, card: tuple[Placed, ResultRow], *, place: int, geom: _CardGeom) -> None:
        """Draw one podium card: place, name, run, hand, whole hand.

        *card* is the placement and its shared payload row; the entry
        supplies the Card objects the large faces need and the row
        R-14's drawn marker (the one the report's own card draws). When
        ``all_cards`` is on, the row's drawn run is spelled out under
        the hand line -- the report's own whole-hand sub-row, scaled to
        this card.
        """
        _maybe_page_break(self, geom.guard)
        entry, row = card
        result = entry.result
        scale = geom.scale
        self.set_font(_FONT_HEADING, "B", 34 * scale)
        self.set_text_color(_DEEP_STEEL if place == _FIRST_PLACE else _STEEL)
        self.cell(0, 0.5 * scale, text=str(place))
        self.ln(0.54 * scale)
        indent = 0.95 * scale
        self.set_font(_FONT_HEADING, "B", 14 * scale)
        self.set_text_color(*_INK)
        self.set_x(self.l_margin + indent)
        self.cell(0, 0.24 * scale, text=_poster_name(result))
        self.ln(0.28 * scale)
        self.set_font(_FONT_BODY, "", 8.5 * scale)
        self.set_text_color(*_INK)
        self.set_x(self.l_margin + indent)
        self.cell(0, 0.14 * scale, text=_poster_subtitle(result))
        self.ln(0.18 * scale)
        self.set_x(self.l_margin + indent)
        _hand_runs(
            self,
            _Cell(
                self.epw - indent,
                _hand_prose(result.hand),
                _TextStyle(_FONT_HEADING, 10 * scale, _STEEL, bold=True),
                height=0.16 * scale,
            ),
            _draw_marker(row),
        )
        self.ln(0.20 * scale)
        if self._all_cards:
            self._drawn_row(row.drawn, indent=indent, scale=scale)
        self.set_x(self.l_margin + indent)
        self._large_cards(result.hand.best5, size=18 * scale, height=0.30 * scale)
        self.ln(0.32 * scale)

    def _drawn_row(self, cards: Sequence[CardPair], *, indent: float, scale: float) -> None:
        """Draw a card's muted whole-hand line, scaled to the card.

        The report's own sub-row wording and DejaVu glyph face, sized,
        led and indented by the card's geometry so the line sits in the
        card at the same proportion the report's card uses.
        """
        self.set_x(self.l_margin + indent)
        self.set_font(_FONT_GLYPH, "", _DRAWN_SIZE * scale)
        self.set_text_color(*_INK)
        self.multi_cell(
            self.epw - indent,
            _DRAWN_LEADING * scale,
            text=_drawn_text(cards),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.ln(_DRAWN_GAP * scale)

    def _large_cards(self, cards: Sequence[Card], *, size: float, height: float) -> None:
        """Draw best-5 cards large; steel for red suits and jokers."""
        self.set_font(_FONT_GLYPH, "", size)
        for card in cards:
            text = _poster_card_text(card)
            width = self.get_string_width(text) + 0.04
            self.set_text_color(_STEEL if _is_steel_card(card) else _INK)
            self.cell(width, height, text=text)
        self.set_text_color(*_INK)


class _ReportPDF(FPDF):
    """The report document: header/footer plus the section drawers.

    Holds the PDF state for one report (fonts, metadata, the ride's
    display fields) and draws the shared section plan in the HTML
    export's order, one drawer per planned section.
    """

    # (ride, opts, letter, created_at, logo_path, self_test_unverified,
    # riders): the report's state inputs
    def __init__(  # noqa: PLR0913
        self,
        ride: _RideLike,
        opts: ExportOptions,
        *,
        letter: bool,
        created_at: datetime,
        logo_path: Path | str | None = None,
        self_test_unverified: bool = False,
        riders: int = 0,
    ) -> None:
        """Open one report: geometry, fonts, metadata, footer stamp.

        Raises:
            ValueError: *created_at* is naive -- D14: a naive stamp
                bakes the machine's local offset into /CreationDate
                and breaks cross-OS byte-identity.
        """
        super().__init__(unit="in", format="Letter" if letter else "A4")
        _open_document(self, ride.name, created_at=created_at)
        self._ride = ride
        self._opts = opts
        self._generated = htmlexport.format_generated(created_at)
        self._logo_path = logo_path
        self._self_test_unverified = self_test_unverified
        self._riders = riders
        self.alias_nb_pages("{nb}")

    def file_id(self) -> None:
        """Suppress the trailer /ID (R-62 determinism).

        fpdf2 derives the default /ID by hashing the assembled buffer,
        whose streams are compressed with the platform zlib -- the hash
        would therefore leak the build's zlib flavour into the bytes.
        Returning None emits no /ID at all.
        """

    # -------------------------------------------------- page furniture

    def header(self) -> None:
        """Draw the corner marks and, from page 2 on, the running title.

        The one-line running title "[ride] — Official results" sits in
        the top margin of every page after the cover; the mark drawing
        never moves the pen.
        """
        _draw_registration_marks(self, top=True)
        if self.page_no() > _FIRST_PAGE:
            self.set_font(_FONT_HEADING, "B", 11)
            self.set_text_color(*_INK)
            self.set_y(0.30)
            self.cell(0, 0.20, text=f"{self._ride.name}{_RUNNING_TITLE_SEP}")
        self.set_y(self.t_margin)

    def footer(self) -> None:
        """Draw the footer rule, credits and "Page n of N" each page.

        The rule sits at the bottom margin; the credits line and the
        page line follow inside it. ``{nb}`` is replaced by the final
        page count at output time via :meth:`alias_nb_pages`.
        """
        _draw_registration_marks(self, top=False)
        self.set_y(-_MARGIN_IN)
        self.set_draw_color(*_INK)
        self.set_line_width(0.5 / 72.0)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(0.07)
        self.set_font(_FONT_BODY, "", 7)
        self.set_text_color(*_STEEL)
        credits_line = f"Organizer: {self._ride.organizer} · Scorer: {self._ride.scorer}"
        self.cell(0, 0.12, text=credits_line)
        self.ln(0.13)
        self.cell(
            0,
            0.12,
            text=f"Page {self.page_no()} of {{nb}} · {self._generated} · RiverCrossing",
        )

    # ------------------------------------------------------ primitives

    def _content_width(self) -> float:
        """Return the printable width between the margins, inches."""
        return self.w - self.l_margin - self.r_margin

    def _maybe_page_break(self, height: float) -> None:
        """Add a page if a *height*-tall block would cross the break."""
        _maybe_page_break(self, height)

    def _rule(self) -> None:
        """Draw the section rule across the content at current y."""
        _draw_rule(self)

    def _section_heading(self, text: str) -> None:
        """Draw a section heading, page-breaking before it if needed."""
        self._maybe_page_break(0.45)
        self.ln(0.06)
        self.set_font(_FONT_HEADING, "B", 13)
        self.set_text_color(*_INK)
        self.cell(0, 0.22, text=text)
        self.ln(0.28)

    def _table_header(self, widths: Sequence[float], labels: Sequence[str]) -> None:
        """Draw a table header row, then the rule under it."""
        self.set_font(_FONT_HEADING, "B", 7)
        self.set_text_color(*_STEEL)
        for width, label in zip(widths, labels, strict=True):
            self.cell(width, 0.16, text=label)
        self.ln(0.18)
        self._rule()
        self.ln(0.03)

    def _at_column(self, widths: Sequence[float], index: int) -> None:
        """Move the pen to the start of column *index*."""
        self.set_x(self.l_margin + sum(widths[:index]))

    def _scalar(self, cell: _Cell) -> None:
        """Draw one styled text cell at the current x."""
        _cell_text(self, cell)

    def _cards_cell(self, cards: Sequence[CardPair], width: float) -> None:
        """Draw an inline card run at the current x, clipped to *width*.

        Each card is its own cell so hearts/diamonds/jokers can take
        the steel color; a card that would cross the column's right
        edge is dropped rather than let it spill into the next column.
        The cards are the shared model's rank/suit pairs, so a suit's
        glyph and its steel accent follow the pair, not a card object.
        """
        self.set_font(_FONT_GLYPH, "", 7)
        right = self.get_x() + width
        for pair in cards:
            text = _pair_text(pair)
            text_width = self.get_string_width(text)
            if self.get_x() + text_width > right:
                break
            self.set_text_color(_STEEL if _pair_is_steel(pair) else _INK)
            self.cell(text_width, _ROW_HEIGHT, text=text)
            self.set_x(self.get_x() + 0.04)
        self.set_text_color(*_INK)

    def _row_rule(self) -> None:
        """Finish one table row with the light separator rule."""
        self.ln(_ROW_HEIGHT)
        y = self.get_y()
        self.set_draw_color(*_INK)
        self.set_line_width(0.3 / 72.0)
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.ln(0.05)

    def _hand_cell(self, row: ResultRow, width: float) -> None:
        """Draw one row's hand cell at the current x, draw marker in.

        The prose renders in the frozen hand style; a row that drew a
        tie-break card (R-14) renders :func:`_draw_marker` immediately
        after it, as a measured second run in DejaVu, so the two fonts
        share one column and no table geometry moves. A row that drew
        nothing draws the single prose cell exactly as before.
        """
        _hand_runs(self, _Cell(width, _hand_label(row.hand), _HAND_STYLE), _draw_marker(row))

    # -------------------------------------------------------- sections

    def build(self, placed: Sequence[Placed]) -> None:
        """Draw every section, in the HTML export's order.

        The shared model does the planning: the payload carries the
        rows and the plan carries the per-kind sections, so this
        document's drawers only lay them out.
        """
        payload = htmlexport.build_payload(
            self._ride,
            placed,
            self._opts,
            self._generated,
            self_test_unverified=self._self_test_unverified,
            riders=self._riders,
        )
        plan = htmlexport.sections(payload, placed)
        self.add_page()
        self._cover(payload)
        self._podiums(plan)
        self._top_lists(plan)
        self._laps_boards(plan)
        self._time_board(plan)
        if self._opts.full_field:
            self._full_field(plan)

    def _cover(self, payload: RacePayload) -> None:
        """Draw the page-1 cover: meta, counters and the two notes."""
        event = payload.event
        _draw_logo(self, self._logo_path)
        if self._logo_path is not None:
            self.ln(0.42)
        self.set_font(_FONT_HEADING, "B", 10)
        self.set_text_color(*_STEEL)
        self.cell(0, 0.16, text=event.kicker)
        self.ln(0.20)
        self.set_font(_FONT_HEADING, "B", 26)
        self.set_text_color(*_INK)
        self.cell(0, 0.40, text=event.title)
        self.ln(0.44)
        self.set_font(_FONT_BODY, "", 9.5)
        self.set_text_color(*_INK)
        self.cell(0, 0.16, text=event.meta)
        self.ln(0.18)
        self.cell(
            0,
            0.16,
            text=f"{event.entries:,} · {event.laps:,} · {event.cards:,} · {event.riders:,}",
            align="R",
        )
        self.ln(0.17)
        self.set_font(_FONT_BODY, "", 7.5)
        self.cell(0, 0.13, text="entries · laps · cards dealt · unique riders", align="R")
        self.ln(0.05)
        self._rule()
        self.ln(0.10)
        self.set_font(_FONT_BODY, "", 8.5)
        self.set_text_color(*_INK)
        self.multi_cell(
            0,
            0.14,
            text="It's not a race, it's a poker run — placings are by best poker hand. "
            "Lap counts and times below are unofficial and shown for bragging rights only.",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.ln(0.04)
        if payload.self_test_note is not None:
            # E6.4.3: the cover's own note area says whether the ride
            # was closed over a failed evaluator self-test.
            self.set_font(_FONT_BODY, "", 8.5)
            self.set_text_color(*_STEEL)
            self.multi_cell(
                0,
                0.14,
                text=payload.self_test_note,
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            self.ln(0.04)

    def _podiums(self, plan: Sections) -> None:
        """Draw the per-kind "Best hands" podium cards."""
        if plan.podium_teams:
            self._section_heading("Best hands — teams")
            for row in plan.podium_teams:
                self._podium_card(row)
        if plan.podium_solo:
            title = "Best hands — solo riders" if plan.teams else "Best hands — top 3"
            self._section_heading(title)
            for row in plan.podium_solo:
                self._podium_card(row)

    def _podium_card(self, row: ResultRow) -> None:
        """Draw one podium card: place, name, run, hand, whole hand.

        A team card shows no plate -- its section's heading names the
        kind, mirroring the HTML's ``podium_card`` call. The hand line
        carries R-14's drawn card, as the top-list and field rows do:
        the draw is what decides 1st from 2nd, so it belongs on the
        most visible surface too. ``all_cards`` adds the full field's
        own whole-hand sub-row under that line, indented to the card's
        name column.
        """
        self._maybe_page_break(1.1)
        indent = 0.70
        self.set_font(_FONT_HEADING, "B", 30)
        self.set_text_color(_DEEP_STEEL if row.place == _FIRST_PLACE else _STEEL)
        self.cell(indent, 0.42, text=str(row.place))
        self.set_font(_FONT_HEADING, "B", 13)
        self.set_text_color(*_INK)
        name = row.entry if _is_team_row(row) else f"#{row.plate} {row.entry}"
        self.cell(0, 0.24, text=name)
        self.ln(0.26)
        self.set_font(_FONT_BODY, "", 8)
        subtitle = f"{row.entry_type} · {row.laps} laps"
        if self._opts.show_times:
            subtitle += f" · {row.total}"
        self.set_x(self.l_margin + indent)
        self.cell(0, 0.13, text=subtitle)
        self.ln(0.16)
        self.set_x(self.l_margin + indent)
        self._cards_cell(row.cards, 2.0)
        self.ln(0.24)
        self.set_x(self.l_margin + indent)
        _hand_runs(
            self,
            _Cell(
                self._content_width() - indent,
                _hand_label(row.hand),
                _PODIUM_HAND_STYLE,
                height=0.15,
            ),
            _draw_marker(row),
        )
        self.ln(0.20)
        if self._opts.all_cards:
            self._drawn_row(row.drawn, indent)
        self.ln(0.12)

    def _top_lists(self, plan: Sections) -> None:
        """Draw the standings tables (5/5 or the solo ten)."""
        if plan.top_teams:
            self._section_heading("Top teams")
            widths = _team_top_widths(
                show_times=self._opts.show_times, content=self._content_width()
            )
            self._table_header(widths, self._top_labels(show_plate=False))
            for row in plan.top_teams:
                self._maybe_page_break(_ROW_HEIGHT + 0.10)
                self._standings_row(widths, row, show_plate=False)
        if plan.top_solo:
            title = "Top solo riders" if plan.teams else "Top ten"
            self._section_heading(title)
            widths = _top_ten_widths(
                show_times=self._opts.show_times, content=self._content_width()
            )
            self._table_header(widths, self._top_labels(show_plate=True))
            for row in plan.top_solo:
                self._maybe_page_break(_ROW_HEIGHT + 0.10)
                self._standings_row(widths, row)

    def _top_labels(self, *, show_plate: bool) -> list[str]:
        """Return one top-list table's header labels."""
        labels = ["Place", "Entry", "Laps"]
        if show_plate:
            labels.insert(1, "Plate")
        if self._opts.show_times:
            labels.append("Total time")
        labels += ["Best 5 cards", "Hand"]
        return labels

    def _standings_row(
        self, widths: Sequence[float], row: ResultRow, *, show_plate: bool = True
    ) -> None:
        """Draw one top-list row (the team form has no Plate cell).

        A DNS row (Phase 7) has no place to print and no lap count to
        show: its Place cell is blank and its Laps cell carries the
        word "DNS", the same two readings the page's own row macros
        render.
        """
        self._at_column(widths, 0)
        self._scalar(_Cell(widths[0], "" if row.dns else str(row.place), _ROW_BOLD))
        col = 1
        if show_plate:
            self._at_column(widths, col)
            self._scalar(_Cell(widths[col], str(row.plate), _ROW_BOLD))
            col += 1
        self._at_column(widths, col)
        self._scalar(_Cell(widths[col], row.entry, _ROW_STYLE))
        col += 1
        self._at_column(widths, col)
        self._scalar(_Cell(widths[col], _laps_cell(row), _ROW_RIGHT))
        col += 1
        if self._opts.show_times:
            self._at_column(widths, col)
            self._scalar(_Cell(widths[col], cast("str", row.total), _ROW_STYLE))
            col += 1
        self._at_column(widths, col)
        self._cards_cell(row.cards, widths[col])
        col += 1
        self._at_column(widths, col)
        self._hand_cell(row, widths[col])
        self._row_rule()

    def _laps_boards(self, plan: Sections) -> None:
        """Draw the per-kind "Most laps" boards."""
        if plan.laps_teams:
            self._laps_board("Most laps — teams", plan.laps_teams, show_plate=False)
        if plan.laps_solo:
            title = "Most laps — solo riders" if plan.teams else "Most laps"
            self._laps_board(title, plan.laps_solo, show_plate=True)

    def _laps_board(self, title: str, rows: Sequence[LapsBoardRow], *, show_plate: bool) -> None:
        """Draw one "Most laps" board, positions enumerated.

        The rows are the shared plan's own leaderboard rows -- they
        carry no place, so the board numbers its rows as it draws them.
        """
        self._section_heading(title)
        self.set_font(_FONT_BODY, "", 7.5)
        self.set_text_color(*_INK)
        self.cell(0, 0.13, text=f"Unofficial — {_format_km(self._ride.lap_km)} km per lap.")
        self.ln(0.16)
        widths = self._laps_widths_for(show_plate=show_plate)
        labels = ["#", "Entry", "Laps"]
        if show_plate:
            labels.insert(1, "Plate")
        if self._opts.show_times:
            labels.append("Total")
        self._table_header(widths, labels)
        for place, row in enumerate(rows, start=_FIRST_PLACE):
            self._maybe_page_break(_ROW_HEIGHT + 0.10)
            self._laps_row(widths, row, place=place, show_plate=show_plate)

    def _laps_widths_for(self, *, show_plate: bool) -> list[float]:
        """Return the board's widths for its plate form."""
        content = self._content_width()
        if show_plate:
            return _laps_widths(show_times=self._opts.show_times, content=content)
        return _team_laps_widths(show_times=self._opts.show_times, content=content)

    def _laps_row(  # noqa: PLR0913 -- (widths, row, place, show_plate): one board row's inputs
        self, widths: Sequence[float], row: LapsBoardRow, *, place: int, show_plate: bool
    ) -> None:
        """Draw one "Most laps" board row."""
        self._at_column(widths, 0)
        self._scalar(_Cell(widths[0], str(place), _ROW_BOLD))
        col = 1
        if show_plate:
            self._at_column(widths, col)
            self._scalar(_Cell(widths[col], f"#{row.plate}", _ROW_BOLD))
            col += 1
        self._at_column(widths, col)
        self._scalar(_Cell(widths[col], row.entry, _ROW_STYLE))
        col += 1
        self._at_column(widths, col)
        self._scalar(_Cell(widths[col], str(row.laps), _ROW_RIGHT))
        col += 1
        if self._opts.show_times:
            self._at_column(widths, col)
            self._scalar(_Cell(widths[col], cast("str", row.total), _ROW_STYLE))
        self._row_rule()

    def _time_board(self, plan: Sections) -> None:
        """Draw the flat "Fastest — laps then time" board, top ten.

        The board is one flat planning list whatever the kinds; a team's
        row still shows no plate, so its cell is left blank against the
        solo rows' plates (the plan's team partition is the lookup).
        """
        if not plan.time_board:
            return
        self._section_heading("Fastest — laps then time")
        self.set_font(_FONT_BODY, "", 7.5)
        self.set_text_color(*_INK)
        self.cell(0, 0.13, text="Most laps, shortest elapsed to the last crossing.")
        self.ln(0.16)
        widths = _time_widths(self._content_width())
        self._table_header(widths, ["#", "Plate", "Entry", "Laps", "Total", "Avg lap"])
        team_plates = {row.plate for row in plan.teams}
        for place, row in enumerate(plan.time_board, start=_FIRST_PLACE):
            self._maybe_page_break(_ROW_HEIGHT + 0.10)
            self._time_row(widths, row, place=place, show_plate=row.plate not in team_plates)

    def _time_row(  # noqa: PLR0913 -- (widths, row, place, show_plate): one board row's inputs
        self, widths: Sequence[float], row: TimeBoardRow, *, place: int, show_plate: bool
    ) -> None:
        """Draw one "Fastest" board row, with the avg-lap time."""
        self._at_column(widths, 0)
        self._scalar(_Cell(widths[0], str(place), _ROW_BOLD))
        self._at_column(widths, 1)
        self._scalar(_Cell(widths[1], f"#{row.plate}" if show_plate else "", _ROW_BOLD))
        self._at_column(widths, 2)
        self._scalar(_Cell(widths[2], row.entry, _ROW_STYLE))
        self._at_column(widths, 3)
        self._scalar(_Cell(widths[3], f"{row.laps} laps", _ROW_STYLE))
        self._at_column(widths, 4)
        self._scalar(_Cell(widths[4], row.total, _BOARD_TOTAL))
        self._at_column(widths, 5)
        self._scalar(_Cell(widths[5], f"avg {row.avg}", _ROW_STYLE))
        self._row_rule()

    def _subsection_heading(self, label: str) -> None:
        """Draw one full-field subsection head ("Teams"/"Solo")."""
        self._maybe_page_break(0.45)
        self.ln(0.04)
        self.set_font(_FONT_HEADING, "B", 11)
        self.set_text_color(*_INK)
        self.cell(0, 0.20, text=label)
        self.ln(0.24)
        self._rule()
        self.ln(0.03)

    def _full_field(self, plan: Sections) -> None:
        """Draw the "Full field" umbrella and its two subsections."""
        self._section_heading("Full field")
        note = "Every entry, ordered by hand"
        if plan.teams:
            note += " — teams ranked against teams, solo riders against solo riders"
        note += ". ★ = joker, shown as the card it played."
        if self._opts.all_cards:
            note += " This export includes every card drawn (organizer option)."
        # DejaVu for the note: it carries the ★ glyph Barlow lacks.
        self.set_font(_FONT_GLYPH, "", 7)
        self.set_text_color(*_INK)
        self.multi_cell(0, 0.13, text=note, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(0.05)
        if plan.teams:
            self._teams_field(plan.teams)
        if plan.solo:
            self._solo_field(plan.solo)

    def _teams_field(self, rows: Sequence[ResultRow]) -> None:
        """Draw the Teams subsection: compact rows, no plate."""
        self._subsection_heading("Teams")
        widths = _team_field_widths(
            show_times=self._opts.show_times, content=self._content_width()
        )
        labels = ["Place", "Entry", "Laps"]
        if self._opts.show_times:
            labels += ["Total time", "Best lap"]
        labels += ["Cards", "Hand"]
        self._table_header(widths, labels)
        for row in rows:
            self._maybe_page_break(_ROW_HEIGHT + 0.10)
            self._team_field_row(widths, row)
            if self._opts.all_cards:
                self._drawn_row(row.drawn)

    def _solo_field(self, rows: Sequence[ResultRow]) -> None:
        """Draw the Solo riders subsection: the field columns."""
        self._subsection_heading("Solo riders")
        widths = _field_widths(show_times=self._opts.show_times, content=self._content_width())
        labels = ["Place", "Plate", "Entry", "Type", "Laps"]
        if self._opts.show_times:
            labels += ["Total time", "Best lap"]
        labels.append("Best hand")
        self._table_header(widths, labels)
        for row in rows:
            self._maybe_page_break(_ROW_HEIGHT + 0.10)
            self._field_row(widths, row)
            if self._opts.all_cards:
                self._drawn_row(row.drawn)

    def _team_field_row(self, widths: Sequence[float], row: ResultRow) -> None:
        """Draw one team full-field row: compact, no plate, no type.

        The row's small inline logo (the shared payload's own data URI)
        draws before the name when it carries one, mirroring the HTML's
        ``team_field_row``; the DNF mark follows the name and the last
        column carries R-14's drawn card like the solo row's own. A DNS
        row (Phase 7) prints a blank Place cell, "DNS" for its laps and
        blank Total time / Best lap cells.
        """
        name = f"{row.entry} DNF" if row.dnf else row.entry
        self._at_column(widths, 0)
        self._scalar(_Cell(widths[0], "" if row.dns else str(row.place), _FIELD_BOLD))
        self._at_column(widths, 1)
        logo_width = self._inline_logo(row.logo)
        self._scalar(_Cell(widths[1] - logo_width, name, _FIELD_STYLE))
        col = 2
        self._at_column(widths, col)
        self._scalar(_Cell(widths[col], _laps_cell(row), _FIELD_STYLE))
        col += 1
        if self._opts.show_times:
            self._at_column(widths, col)
            self._scalar(_Cell(widths[col], _time_cell(row, row.total), _FIELD_STYLE))
            col += 1
            self._at_column(widths, col)
            self._scalar(_Cell(widths[col], _time_cell(row, row.best_lap), _FIELD_STYLE))
            col += 1
        self._at_column(widths, col)
        self._cards_cell(row.cards, widths[col])
        col += 1
        self._at_column(widths, col)
        self._hand_cell(row, widths[col])
        self._row_rule()

    def _inline_logo(self, logo: str | None) -> float:
        """Draw a row's small inline logo; return the width it consumed.

        ``None`` consumes nothing. A drawn logo is the payload's own
        data URI bitmapped to the HTML's compact inline size.
        """
        if logo is None:
            return 0.0
        self.image(_decode_logo(logo), w=_INLINE_LOGO, h=_INLINE_LOGO)
        self.set_x(self.get_x() + _INLINE_LOGO + 0.03)
        return _INLINE_LOGO + 0.03

    def _field_row(self, widths: Sequence[float], row: ResultRow) -> None:
        """Draw one solo full-field row, DNF-marked after the name.

        The row's sex (E7) rides in the entry cell beside the name --
        "Luca Ferrari (M)" -- the least invasive of the two options,
        since a dedicated column would re-cut every width. A team's sex
        is None (no single sex) and renders nothing. A DNS row
        (Phase 7) prints a blank Place cell, "DNS" for its laps and
        blank Total time / Best lap cells.
        """
        cell_name = f"{row.entry} ({row.sex})" if row.sex else row.entry
        name = f"{cell_name} DNF" if row.dnf else cell_name
        self._at_column(widths, 0)
        self._scalar(_Cell(widths[0], "" if row.dns else str(row.place), _FIELD_BOLD))
        self._at_column(widths, 1)
        self._scalar(_Cell(widths[1], str(row.plate), _FIELD_BOLD))
        self._at_column(widths, 2)
        self._scalar(_Cell(widths[2], name, _FIELD_STYLE))
        self._at_column(widths, 3)
        self._scalar(_Cell(widths[3], row.entry_type, _FIELD_STYLE))
        self._at_column(widths, 4)
        self._scalar(_Cell(widths[4], _laps_cell(row), _FIELD_STYLE))
        col = 5
        if self._opts.show_times:
            self._at_column(widths, col)
            self._scalar(_Cell(widths[col], _time_cell(row, row.total), _FIELD_STYLE))
            col += 1
            self._at_column(widths, col)
            self._scalar(_Cell(widths[col], _time_cell(row, row.best_lap), _FIELD_STYLE))
            col += 1
        last = len(widths) - 1
        self._at_column(widths, last)
        self._cards_cell(row.cards, widths[last])
        remaining = self.l_margin + sum(widths[:last]) + widths[last] - self.get_x()
        self._hand_cell(row, max(remaining, 0.0))
        self._row_rule()

    def _drawn_row(self, cards: Sequence[CardPair], indent: float = 0.0) -> None:
        """Draw the muted "All N cards, in draw order" sub-row.

        *indent* moves the line's left edge onto a podium card's name
        column and narrows it to match; the full field draws it flush
        left. The DejaVu face supplies the run's suit glyphs.
        """
        self.set_x(self.l_margin + indent)
        self.set_font(_FONT_GLYPH, "", _DRAWN_SIZE)
        self.set_text_color(*_INK)
        self.multi_cell(
            self.epw - indent,
            _DRAWN_LEADING,
            text=_drawn_text(cards),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.ln(_DRAWN_GAP)


def _atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """Write *data* to *path* atomically: temp sibling, then os.replace.

    The bytes land in a same-directory ``<name>.tmp`` sibling first,
    then that file is swapped over *path* with :func:`os.replace` --
    the temp is fully written and closed before the swap -- so a crash
    mid-export leaves the previous complete PDF in place and a reader
    never observes a truncated one (R-52).
    """
    destination = Path(path)
    tmp = destination.with_name(destination.name + ".tmp")
    tmp.write_bytes(data)
    # R-52 mandates the os.replace atomic swap; tests patch it
    os.replace(tmp, destination)  # noqa: PTH105


# module-skeletons.md's frozen (ride, placed, opts, path) plus the
# letter/created_at/logo/self-test seams
def render(  # noqa: PLR0913, PLR0917
    ride: _RideLike,
    placed: Sequence[Placed],
    opts: ExportOptions,
    path: Path | str,
    *,
    letter: bool = True,
    created_at: datetime | None = None,
    logo_path: Path | str | None = None,
    self_test_unverified: bool = False,
    riders: int = 0,
) -> None:
    """Write one finished ride's results report PDF to *path*.

    Mirrors the HTML export's sections and flags (R-63): cover block,
    then the per-kind podiums and top lists (a team event: 3 team + 3
    solo places and 5+5 top lists; a solo event: a top-3 podium and a
    top ten), then the laps/time boards only when the corresponding
    option is on, then the full field (DNF marked, with all-cards-drawn
    sub-rows when ``all_cards``), and Total/Best-lap columns only when
    ``show_times``. *path* is the caller-supplied
    full file path -- the ``{ride-slug}-results.pdf`` naming is the
    menu handler's job, never this module's (module-skeletons.md).

    Determinism (R-62, D14): *created_at* defaults to now (aware UTC)
    and is the document's only timestamp; identical inputs plus the
    same stamp produce byte-identical files.

    Args:
        ride: Ride-like object exposing ``name``/``event_date``/
            ``venue``/``lap_km``/``organizer``/``scorer``;
            ``RideConfig`` satisfies it structurally.
        placed: Ranked standings, one per entry.
        opts: Export flags (times/boards/full-field/all-cards).
        path: Where to write the report; parents must already exist.
        letter: True for Letter paper, False for A4.
        created_at: The pinned aware-UTC creation stamp; defaults to
            now. Naive stamps are rejected (D14).
        logo_path: Optional organizer-logo PNG drawn at the top right
            of the cover (R-62/5c); None renders no logo.
        self_test_unverified: E6.4.3: whether the ride was finished
            over a failed evaluator self-test, which adds that note to
            the cover.
        riders: The number of individual riders the cover's fourth
            counter shows; 0 when the caller supplies no count.

    Raises:
        ValueError: *created_at* is not tz-aware.
    """
    stamp = created_at if created_at is not None else datetime.now(UTC)
    report = _ReportPDF(
        ride,
        opts,
        letter=letter,
        created_at=stamp,
        logo_path=logo_path,
        self_test_unverified=self_test_unverified,
        riders=riders,
    )
    report.build(placed)
    data = _store_streams_raw(bytes(report.output()))
    _atomic_write_bytes(path, data)


# module-skeletons.md's frozen (ride, placed, path) plus the
# letter/created_at/logo/self-test/all-cards seams
def podium_poster(  # noqa: PLR0913
    ride: _RideLike,
    placed: Sequence[Placed],
    path: Path | str,
    *,
    letter: bool = True,
    created_at: datetime | None = None,
    logo_path: Path | str | None = None,
    self_test_unverified: bool = False,
    all_cards: bool = True,
) -> None:
    """Write one finished ride's one-page podium poster PDF to *path*.

    The [5d] prize-table poster: a single celebratory page at Letter
    (A4 via ``letter=False``) carrying the event meta, a "Best poker
    hands" heading + ride title, then the top-3 placings as large
    podium cards -- big place number, ``#plate Entry name``, the
    team/solo line, the hand's title-case prose name, and the best-5
    cards as large faces (steel accent for hearts/diamonds/jokers).
    A placing that drew a tie-break card (R-14) renders that card
    beside its hand prose, as the report's own podium card does. With
    ``all_cards`` on (the default) each card also spells out the
    entry's ENTIRE hand under its hand line -- the report's own muted
    "All N cards, in draw order: …" sub-row, scaled to the card -- so
    the poster shows the whole deal, not just the best five faces. The
    footer is a credit line + generated stamp with no "Page n of N" --
    there is only one page. *path* is the caller-supplied full file
    path; the ``{ride-slug}-podium.pdf`` naming is the menu handler's
    job, never this module's (module-skeletons.md).

    Determinism (R-62, D14): *created_at* defaults to now (aware UTC)
    and is the document's only timestamp; identical inputs plus the
    same stamp produce byte-identical files.

    Args:
        ride: Ride-like object exposing ``name``/``event_date``/
            ``venue``/``lap_km``/``organizer``/``scorer``;
            ``RideConfig`` satisfies it structurally.
        placed: Ranked standings, one per entry; the top three render.
        path: Where to write the poster; parents must already exist.
        letter: True for Letter paper, False for A4.
        created_at: The pinned aware-UTC creation stamp; defaults to
            now. Naive stamps are rejected (D14).
        logo_path: Optional organizer-logo PNG drawn at the top right
            (R-62/5c); None renders no logo.
        self_test_unverified: E6.4.3: whether the ride was finished
            over a failed evaluator self-test, which adds that note to
            the poster's header block.
        all_cards: Whether each card spells out the entry's whole hand
            in draw order (R-63's all-cards flag, the report's own).

    Raises:
        ValueError: *created_at* is not tz-aware.
    """
    stamp = created_at if created_at is not None else datetime.now(UTC)
    poster = _PosterPDF(
        ride,
        letter=letter,
        created_at=stamp,
        logo_path=logo_path,
        self_test_unverified=self_test_unverified,
        all_cards=all_cards,
    )
    poster.build(placed)
    data = _store_streams_raw(bytes(poster.output()))
    _atomic_write_bytes(path, data)
