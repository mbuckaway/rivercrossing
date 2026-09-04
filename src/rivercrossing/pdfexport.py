# SPDX-License-Identifier: GPL-3.0-only
"""PDF results report: fpdf2 renderer for a finished ride (spec §8b).

The second exporter over the frozen payload model: same sections and
flags as ``rivercrossing.htmlexport`` (R-63) -- cover block, podium
(top 3), top ten, optional laps/time boards, full field (DNF marked),
all-cards-drawn sub-rows, and Total/Best-lap columns only when
``ExportOptions.show_times`` is on -- rendered with the retired
designs' print geometry ([5a]-[5c]): Letter (A4 via ``letter=False``),
0.58in margins, footer rule + "Page n of N · generated … ·
RiverCrossing" on every page, a one-line running title from page 2
on, Barlow + Barlow Condensed headings + DejaVu Sans suit glyphs
(``♠♥♦♣★``), corner registration marks (two 11pt hairlines per
corner), and the industry tokens ink ``#1D1F20``, steel ``#416180``
(hearts/diamonds/jokers and hand names), deep steel ``#1D2D3D`` (the
P1 podium plate) -- no red.

Determinism (R-62, D14) is the module's load-bearing contract.
:func:`render` embeds exactly one timestamp -- the ``created_at``
stamp, required tz-aware UTC so ``/CreationDate`` never bakes a
machine-local offset -- and formats the footer's visible "generated
H:MM, Mon D YYYY" text from the same stamp without a local-time
conversion (a converted time would differ per machine and break
byte-identity). Identical inputs plus the same stamp produce
byte-identical files; ``tools/gen_pdfexport_fixtures.py`` freezes the
committed golden from this renderer.

Public API (module-skeletons.md): :func:`render` writes the report to
the caller-supplied *path*; :func:`podium_poster` writes the one-page
prize-table poster ([5d]) the same way. The ``{ride-slug}-results.pdf``
and ``{ride-slug}-podium.pdf`` naming is the menu handler's job, never
this module's. Pure Python -- no ``wx`` (R-71), no ``ui`` import: the
small duration formatter is duplicated from ``htmlexport`` rather than
imported, keeping the exporter independent of its sibling (and of the
UI layer's presenters).
"""

import os
import re
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from rivercrossing.cards import Suit
from rivercrossing.standings import EntryResult, hand_name, laps_leaderboard, time_leaderboard

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rivercrossing.cards import Card, Rank
    from rivercrossing.hands import EvaluatedHand
    from rivercrossing.htmlexport import ExportOptions
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

_KICKER = "Official results · poker run"
_RUNNING_TITLE_SEP = " — Official results"

# The footer's "Sept" spelling is the samples' own four-letter
# vocabulary, matching htmlexport's (not strftime's "Sep").
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

# cards.Rank's integer values to the display rank letters; the ten is
# "10" in the report (the golden pages' own spelling), unlike
# Card.code()'s "T".
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
_JOKER_GLYPH = "★"

# ------------------------------------------------------- text helpers


def _format_duration(seconds: float) -> str:
    """Format whole seconds as the golden pages' clock text.

    ``H:MM:SS`` for an hour or more, ``M:SS`` below -- "5:52:41" and
    "27:59" respectively. Duplicated from ``htmlexport`` rather than
    imported: pdfexport must not depend on its exporter sibling.
    """
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_event_date(day: date) -> str:
    """Format *day* as the golden pages' "Sunday September 20, 2026"."""
    return f"{day.strftime('%A %B')} {day.day}, {day.year}"


def _format_km(lap_km: float) -> str:
    """Format ``lap_km`` as "8" for 8.0, "8.5" otherwise (D5)."""
    if float(lap_km).is_integer():
        return str(int(lap_km))
    return str(lap_km)


def _format_generated(at: datetime) -> str:
    """Format the pinned creation stamp as the footer's clock text.

    Deliberately no local-time conversion: the aware-UTC stamp is
    formatted as-is so the visible text is byte-identical across
    machines (R-62, D14).
    """
    return f"generated {at.strftime('%H:%M')}, {_MONTH_ABBR[at.month - 1]} {at.day} {at.year}"


def _card_text(card: Card) -> str:
    """Return one card's text: rank letter + suit glyph, or ★."""
    if card.joker:
        return _JOKER_GLYPH
    rank = cast("Rank", card.rank)
    suit = cast("Suit", card.suit)
    return f"{_RANK_LETTER[rank.value]}{_SUIT_GLYPH[suit]}"


def _is_steel_card(card: Card) -> bool:
    """Return True for hearts/diamonds/jokers (the steel accent)."""
    return card.joker or card.suit in (Suit.HEARTS, Suit.DIAMONDS)


def _poster_card_text(card: Card) -> str:
    """Return one large poster face: rank+suit, or ★JOKER for a joker.

    Natural cards reuse :func:`_card_text`'s "9♠" spelling; the joker
    spells its face out at poster size (the [5d] mock's own "★JOKER")
    rather than the report's bare "★".
    """
    if card.joker:
        return "★JOKER"
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


def _hand_label(hand: EvaluatedHand) -> str:
    """Return *hand*'s uppercase prose name, or "" for a no-card hand.

    A finished ride can still hold an entry that never crossed; its
    snapshot hand is empty (``best_hand(())``) and names nothing, so
    the cell renders blank rather than raising (standings.hand_name
    rejects the empty hand). Uppercase because the PDF has no CSS
    ``text-transform`` to apply the HTML's hand-cell style with.
    """
    return _hand_prose(hand).upper()


def _poster_subtitle(result: EntryResult) -> str:
    """Compose the poster's team/solo line from one result.

    The [5d] mock's "Team of 4 -- member names" lists rider names and
    a team size that ``EntryResult`` does not carry (the same seam
    htmlexport documents for "TEAM by 4"), so the line names the
    entry itself from the payload: "Solo -- Luca Ferrari · 10 laps" or
    "Team -- Dirt Dynamos · 10 laps".
    """
    kind = "Team" if result.kind == "team" else "Solo"
    return f"{kind} — {result.name} · {result.laps} laps"


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


def _format_meta(ride: _RideLike) -> str:
    """Compose the cover meta line from the ride (D15: venue/date)."""
    return (
        f"{_format_event_date(ride.event_date)} · {ride.venue} · {_format_km(ride.lap_km)} km loop"
    )


def _top_ten_widths(*, show_times: bool, content: float) -> list[float]:
    """Return the top-ten table's column widths, in inches.

    Total/Best-lap columns exist only when times are shown (R-63); the
    Hand column absorbs the freed width when they are not.
    """
    widths = [0.40, 0.62, 1.50, 0.45]
    if show_times:
        widths.append(0.90)
    widths += [1.20, content - sum(widths) - 1.20]
    return widths


def _field_widths(*, show_times: bool, content: float) -> list[float]:
    """Return the full-field table's column widths, in inches.

    The final "Best hand" column holds the inline cards plus the hand
    name; without time columns it re-widens to absorb them.
    """
    widths = [0.35, 0.58, 1.30, 0.62, 0.40]
    if show_times:
        widths += [0.85, 0.80]
    widths.append(content - sum(widths))
    return widths


def _laps_widths(*, show_times: bool, content: float) -> list[float]:
    """Return the "Most laps" board's column widths, in inches."""
    widths = [0.35, 0.58, 0.60]
    if show_times:
        widths.append(0.90)
    widths.insert(2, content - sum(widths))
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
_BOARD_TOTAL = _TextStyle(_FONT_BODY, 7.5, _INK, bold=True)


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
    body_end = pdf.find(b"endstream", body_start)
    if body_end == -1:
        return None
    body = pdf[body_start:body_end]
    if body.endswith(b"\r\n"):
        body = body[:-2]
    elif body.endswith(b"\n"):
        body = body[:-1]
    try:
        raw = zlib.decompress(body)
    except zlib.error:
        return None  # not really deflate despite the filter entry
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


class _PosterPDF(FPDF):
    """The one-page podium poster document ([5d]).

    A sibling of :class:`_ReportPDF` sharing its geometry, fonts and
    D14 metadata, fixed to a single celebratory Letter page: event
    meta, a "Best poker hands" heading + ride title, then the top-3
    placings as large podium cards (big place number, ``#plate`` +
    entry name, the team/solo line, the hand's title-case prose name,
    and large card faces with the steel accent for
    hearts/diamonds/jokers). The footer is a credit line + generated
    stamp -- no "Page n of N", there is only one page.
    """

    def __init__(  # noqa: PLR0913 -- (ride, letter, created_at, logo_path): the poster's state inputs
        self, ride: _RideLike, *, letter: bool, created_at: datetime, logo_path: Path | str | None
    ) -> None:
        """Open one poster: geometry, fonts, metadata, footer stamp.

        Raises:
            ValueError: *created_at* is naive -- D14, same as the
                report document.
        """
        super().__init__(unit="in", format="Letter" if letter else "A4")
        _open_document(self, ride.name, created_at=created_at)
        self._ride = ride
        self._generated = _format_generated(created_at)
        self._logo_path = logo_path

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
        """Draw the header block and the top-3 podium cards."""
        self.add_page()
        self._header_block()
        for entry in placed[: _FIRST_PLACE + 2]:
            self._podium_card(entry)

    def _header_block(self) -> None:
        """Draw the event meta, "Best poker hands" heading and title."""
        _draw_logo(self, self._logo_path)
        if self._logo_path is not None:
            self.ln(0.42)
        self.set_font(_FONT_BODY, "", 9)
        self.set_text_color(*_STEEL)
        self.cell(0, 0.16, text=_format_meta(self._ride))
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

    def _podium_card(self, entry: Placed) -> None:
        """Draw one large podium card: place, plate/name, run, hand."""
        _maybe_page_break(self, 1.9)
        result = entry.result
        self.set_font(_FONT_HEADING, "B", 34)
        self.set_text_color(_DEEP_STEEL if entry.place == _FIRST_PLACE else _STEEL)
        self.cell(0, 0.5, text=str(entry.place))
        self.ln(0.54)
        indent = 0.95
        self.set_font(_FONT_HEADING, "B", 14)
        self.set_text_color(*_INK)
        self.set_x(self.l_margin + indent)
        self.cell(0, 0.24, text=f"#{result.plate} {result.name}")
        self.ln(0.28)
        self.set_font(_FONT_BODY, "", 8.5)
        self.set_text_color(*_INK)
        self.set_x(self.l_margin + indent)
        self.cell(0, 0.14, text=_poster_subtitle(result))
        self.ln(0.18)
        self.set_x(self.l_margin + indent)
        self.set_font(_FONT_HEADING, "B", 10)
        self.set_text_color(*_STEEL)
        self.cell(0, 0.16, text=_hand_prose(result.hand))
        self.ln(0.20)
        self.set_x(self.l_margin + indent)
        self._large_cards(result.hand.best5)
        self.ln(0.32)

    def _large_cards(self, cards: Sequence[Card]) -> None:
        """Draw best-5 cards large; steel for red suits and jokers."""
        self.set_font(_FONT_GLYPH, "", 18)
        for card in cards:
            text = _poster_card_text(card)
            width = self.get_string_width(text) + 0.04
            self.set_text_color(_STEEL if _is_steel_card(card) else _INK)
            self.cell(width, 0.30, text=text)
        self.set_text_color(*_INK)


class _ReportPDF(FPDF):
    """The report document: header/footer plus the section drawers.

    Holds the PDF state for one report (fonts, metadata, the ride's
    display fields) and draws the sections in the HTML export's order.
    """

    def __init__(  # noqa: PLR0913 -- (ride, opts, letter, created_at, logo_path): the report's state inputs
        self,
        ride: _RideLike,
        opts: ExportOptions,
        *,
        letter: bool,
        created_at: datetime,
        logo_path: Path | str | None = None,
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
        self._generated = _format_generated(created_at)
        self._logo_path = logo_path
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
        self.set_font(cell.style.font, "B" if cell.style.bold else "", cell.style.size)
        self.set_text_color(*cell.style.color)
        self.cell(cell.width, cell.height, text=cell.text, align=cell.style.align)

    def _cards_cell(self, cards: Sequence[Card], width: float) -> None:
        """Draw an inline card run at the current x, clipped to *width*.

        Each card is its own cell so hearts/diamonds/jokers can take
        the steel color; a card that would cross the column's right
        edge is dropped rather than let it spill into the next column.
        """
        self.set_font(_FONT_GLYPH, "", 7)
        right = self.get_x() + width
        for card in cards:
            text = _card_text(card)
            text_width = self.get_string_width(text)
            if self.get_x() + text_width > right:
                break
            self.set_text_color(_STEEL if _is_steel_card(card) else _INK)
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

    # -------------------------------------------------------- sections

    def build(self, placed: Sequence[Placed]) -> None:
        """Draw every section, in the HTML export's order."""
        self.add_page()
        self._cover(placed)
        self._podium(placed)
        self._top_ten(placed)
        if self._opts.laps_board:
            self._laps_board(placed)
        if self._opts.time_board:
            self._time_board(placed)
        if self._opts.full_field:
            self._full_field(placed)

    def _cover(self, placed: Sequence[Placed]) -> None:
        """Draw the page-1 cover: kicker, title, counters, meta."""
        _draw_logo(self, self._logo_path)
        if self._logo_path is not None:
            self.ln(0.42)
        entries = len(placed)
        laps = sum(p.result.laps for p in placed)
        cards = sum(len(p.result.cards) for p in placed)
        self.set_font(_FONT_HEADING, "B", 10)
        self.set_text_color(*_STEEL)
        self.cell(0, 0.16, text=_KICKER)
        self.ln(0.20)
        self.set_font(_FONT_HEADING, "B", 26)
        self.set_text_color(*_INK)
        self.cell(0, 0.40, text=self._ride.name)
        self.ln(0.44)
        self.set_font(_FONT_BODY, "", 9.5)
        self.set_text_color(*_INK)
        self.cell(0, 0.16, text=_format_meta(self._ride))
        self.ln(0.18)
        self.cell(0, 0.16, text=f"{entries:,} · {laps:,} · {cards:,}", align="R")
        self.ln(0.17)
        self.set_font(_FONT_BODY, "", 7.5)
        self.cell(0, 0.13, text="entries · laps · cards dealt", align="R")
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

    def _podium(self, placed: Sequence[Placed]) -> None:
        """Draw the "Best hands — top 3" podium cards."""
        self._section_heading("Best hands — top 3")
        for entry in placed[: _FIRST_PLACE + 2]:
            self._podium_card(entry)

    def _podium_card(self, entry: Placed) -> None:
        """Draw one podium card: place, plate/name, run, hand."""
        self._maybe_page_break(1.1)
        result = entry.result
        indent = 0.70
        self.set_font(_FONT_HEADING, "B", 30)
        self.set_text_color(_DEEP_STEEL if entry.place == _FIRST_PLACE else _STEEL)
        self.cell(indent, 0.42, text=str(entry.place))
        self.set_font(_FONT_HEADING, "B", 13)
        self.set_text_color(*_INK)
        self.cell(0, 0.24, text=f"#{result.plate} {result.name}")
        self.ln(0.26)
        self.set_font(_FONT_BODY, "", 8)
        subtitle = f"{result.kind.upper()} · {result.laps} laps"
        if self._opts.show_times:
            subtitle += f" · {_format_duration(result.total_time)}"
        self.set_x(self.l_margin + indent)
        self.cell(0, 0.13, text=subtitle)
        self.ln(0.16)
        self.set_x(self.l_margin + indent)
        self._cards_cell(result.hand.best5, 2.0)
        self.ln(0.24)
        self.set_font(_FONT_HEADING, "B", 9.5)
        self.set_text_color(*_STEEL)
        self.set_x(self.l_margin + indent)
        self.cell(0, 0.15, text=_hand_label(result.hand))
        self.ln(0.32)

    def _top_ten(self, placed: Sequence[Placed]) -> None:
        """Draw the "Top ten" standings table."""
        self._section_heading("Top ten")
        widths = _top_ten_widths(show_times=self._opts.show_times, content=self._content_width())
        labels = ["Place", "Plate", "Entry", "Laps"]
        if self._opts.show_times:
            labels.append("Total time")
        labels += ["Best 5 cards", "Hand"]
        self._table_header(widths, labels)
        for entry in placed[:10]:
            self._maybe_page_break(_ROW_HEIGHT + 0.10)
            self._standings_row(widths, entry)

    def _standings_row(self, widths: Sequence[float], entry: Placed) -> None:
        """Draw one top-ten row: place, plate, entry, laps, hand."""
        result = entry.result
        self._at_column(widths, 0)
        self._scalar(_Cell(widths[0], str(entry.place), _ROW_BOLD))
        self._at_column(widths, 1)
        self._scalar(_Cell(widths[1], result.plate, _ROW_BOLD))
        self._at_column(widths, 2)
        self._scalar(_Cell(widths[2], result.name, _ROW_STYLE))
        self._at_column(widths, 3)
        self._scalar(_Cell(widths[3], str(result.laps), _ROW_RIGHT))
        col = 4
        if self._opts.show_times:
            self._at_column(widths, col)
            self._scalar(_Cell(widths[col], _format_duration(result.total_time), _ROW_STYLE))
            col += 1
        self._at_column(widths, col)
        self._cards_cell(result.hand.best5, widths[col])
        self._at_column(widths, col + 1)
        self._scalar(_Cell(widths[col + 1], _hand_label(result.hand), _HAND_STYLE))
        self._row_rule()

    def _laps_board(self, placed: Sequence[Placed]) -> None:
        """Draw the "Most laps" leaderboard, top 5 ACTIVE entries."""
        self._section_heading("Most laps")
        self.set_font(_FONT_BODY, "", 7.5)
        self.set_text_color(*_INK)
        self.cell(0, 0.13, text=f"Unofficial — {_format_km(self._ride.lap_km)} km per lap.")
        self.ln(0.16)
        widths = _laps_widths(show_times=self._opts.show_times, content=self._content_width())
        labels = ["#", "Plate", "Entry", "Laps"]
        if self._opts.show_times:
            labels.append("Total")
        self._table_header(widths, labels)
        results = [p.result for p in placed]
        for entry in laps_leaderboard(results, top=5):
            self._maybe_page_break(_ROW_HEIGHT + 0.10)
            self._laps_row(widths, entry)

    def _laps_row(self, widths: Sequence[float], entry: Placed) -> None:
        """Draw one "Most laps" board row."""
        result = entry.result
        self._at_column(widths, 0)
        self._scalar(_Cell(widths[0], str(entry.place), _ROW_BOLD))
        self._at_column(widths, 1)
        self._scalar(_Cell(widths[1], f"#{result.plate}", _ROW_BOLD))
        self._at_column(widths, 2)
        self._scalar(_Cell(widths[2], result.name, _ROW_STYLE))
        self._at_column(widths, 3)
        self._scalar(_Cell(widths[3], str(result.laps), _ROW_RIGHT))
        if self._opts.show_times:
            self._at_column(widths, 4)
            self._scalar(_Cell(widths[4], _format_duration(result.total_time), _ROW_STYLE))
        self._row_rule()

    def _time_board(self, placed: Sequence[Placed]) -> None:
        """Draw the "Fastest — laps then time" leaderboard, top 5."""
        self._section_heading("Fastest — laps then time")
        self.set_font(_FONT_BODY, "", 7.5)
        self.set_text_color(*_INK)
        self.cell(0, 0.13, text="Most laps, shortest elapsed to the last crossing.")
        self.ln(0.16)
        widths = _time_widths(self._content_width())
        self._table_header(widths, ["#", "Plate", "Entry", "Laps", "Total", "Avg lap"])
        results = [p.result for p in placed]
        for entry in time_leaderboard(results, top=5):
            self._maybe_page_break(_ROW_HEIGHT + 0.10)
            self._time_row(widths, entry)

    def _time_row(self, widths: Sequence[float], entry: Placed) -> None:
        """Draw one "Fastest" board row, with the avg-lap time."""
        result = entry.result
        avg = _format_duration(result.total_time / result.laps) if result.laps else ""
        self._at_column(widths, 0)
        self._scalar(_Cell(widths[0], str(entry.place), _ROW_BOLD))
        self._at_column(widths, 1)
        self._scalar(_Cell(widths[1], f"#{result.plate}", _ROW_BOLD))
        self._at_column(widths, 2)
        self._scalar(_Cell(widths[2], result.name, _ROW_STYLE))
        self._at_column(widths, 3)
        self._scalar(_Cell(widths[3], f"{result.laps} laps", _ROW_STYLE))
        self._at_column(widths, 4)
        self._scalar(_Cell(widths[4], _format_duration(result.total_time), _BOARD_TOTAL))
        self._at_column(widths, 5)
        self._scalar(_Cell(widths[5], f"avg {avg}", _ROW_STYLE))
        self._row_rule()

    def _kind_label(self, label: str) -> None:
        """Draw one full-field section label ("Teams"/"Solo").

        Phase 3 (team/solo results split): each non-empty kind in the
        full field is preceded by this small-caps label row so the two
        sections read as separate tables under the shared header.
        """
        self._maybe_page_break(0.30)
        self.ln(0.04)
        self.set_font(_FONT_HEADING, "B", 8.5)
        self.set_text_color(*_STEEL)
        self.cell(0, 0.16, text=label.upper())
        self.ln(0.18)
        self._rule()
        self.ln(0.03)

    def _full_field(self, placed: Sequence[Placed]) -> None:
        """Draw the "Full field" table: Teams then Solo sections."""
        self._section_heading("Full field")
        note = "Every entry, ordered by hand — teams ranked against teams, "
        note += "solo riders against solo riders. ★ = joker, shown as the card it played."
        if self._opts.all_cards:
            note += " This export includes every card drawn (organizer option)."
        # DejaVu for the note: it carries the ★ glyph Barlow lacks.
        self.set_font(_FONT_GLYPH, "", 7)
        self.set_text_color(*_INK)
        self.multi_cell(0, 0.13, text=note, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(0.05)
        widths = _field_widths(show_times=self._opts.show_times, content=self._content_width())
        labels = ["Place", "Plate", "Entry", "Type", "Laps"]
        if self._opts.show_times:
            labels += ["Total time", "Best lap"]
        labels.append("Best hand")
        self._table_header(widths, labels)
        for kind, section_label in (("team", "Teams"), ("solo", "Solo")):
            entries = [entry for entry in placed if entry.result.kind == kind]
            if not entries:
                continue  # a ride without this kind renders no section
            self._kind_label(section_label)
            for entry in entries:
                self._maybe_page_break(_ROW_HEIGHT + 0.10)
                self._field_row(widths, entry)
                if self._opts.all_cards:
                    self._drawn_row(entry.result)

    def _field_row(self, widths: Sequence[float], entry: Placed) -> None:
        """Draw one full-field row, with the DNF mark after the name."""
        result = entry.result
        name = f"{result.name} DNF" if result.dnf else result.name
        self._at_column(widths, 0)
        self._scalar(_Cell(widths[0], str(entry.place), _FIELD_BOLD))
        self._at_column(widths, 1)
        self._scalar(_Cell(widths[1], result.plate, _FIELD_BOLD))
        self._at_column(widths, 2)
        self._scalar(_Cell(widths[2], name, _FIELD_STYLE))
        self._at_column(widths, 3)
        self._scalar(_Cell(widths[3], result.kind.upper(), _FIELD_STYLE))
        self._at_column(widths, 4)
        self._scalar(_Cell(widths[4], str(result.laps), _FIELD_STYLE))
        col = 5
        if self._opts.show_times:
            self._at_column(widths, col)
            self._scalar(_Cell(widths[col], _format_duration(result.total_time), _FIELD_STYLE))
            col += 1
            self._at_column(widths, col)
            self._scalar(_Cell(widths[col], _format_duration(result.best_lap), _FIELD_STYLE))
            col += 1
        last = len(widths) - 1
        self._at_column(widths, last)
        self._cards_cell(result.hand.best5, widths[last])
        remaining = self.l_margin + sum(widths[:last]) + widths[last] - self.get_x()
        self.set_font(_HAND_STYLE.font, "B" if _HAND_STYLE.bold else "", _HAND_STYLE.size)
        self.set_text_color(*_HAND_STYLE.color)
        self.cell(max(remaining, 0.0), _ROW_HEIGHT, text=_hand_label(result.hand))
        self._row_rule()

    def _drawn_row(self, result: EntryResult) -> None:
        """Draw the muted "All N cards, in draw order" sub-row."""
        cards = result.cards
        run = " ".join(_card_text(card) for card in cards)
        # DejaVu for the run: the sub-row spells out the suit glyphs.
        self.set_font(_FONT_GLYPH, "", 6.5)
        self.set_text_color(*_INK)
        self.multi_cell(
            0,
            0.12,
            text=f"All {len(cards)} cards, in draw order: {run}",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.ln(0.04)


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
    os.replace(tmp, destination)  # noqa: PTH105 -- R-52 mandates the os.replace atomic swap; tests patch it


def render(  # noqa: PLR0913, PLR0917 -- module-skeletons.md's frozen (ride, placed, opts, path) plus the letter/created_at/logo seams
    ride: _RideLike,
    placed: Sequence[Placed],
    opts: ExportOptions,
    path: Path | str,
    *,
    letter: bool = True,
    created_at: datetime | None = None,
    logo_path: Path | str | None = None,
) -> None:
    """Write one finished ride's results report PDF to *path*.

    Mirrors the HTML export's sections and flags (R-63): cover block,
    podium (top 3), top ten, then the laps/time boards only when the
    corresponding option is on, then the full field (DNF marked, with
    all-cards-drawn sub-rows when ``all_cards``), and Total/Best-lap
    columns only when ``show_times``. *path* is the caller-supplied
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

    Raises:
        ValueError: *created_at* is not tz-aware.
    """
    stamp = (
        created_at
        if created_at is not None
        else datetime.now(
            timezone.utc  # noqa: UP017 -- this mypy build lacks datetime.UTC; the portable form
        )
    )
    report = _ReportPDF(ride, opts, letter=letter, created_at=stamp, logo_path=logo_path)
    report.build(placed)
    data = _store_streams_raw(bytes(report.output()))
    _atomic_write_bytes(path, data)


def podium_poster(  # noqa: PLR0913 -- module-skeletons.md's frozen (ride, placed, path) plus the letter/created_at/logo seams
    ride: _RideLike,
    placed: Sequence[Placed],
    path: Path | str,
    *,
    letter: bool = True,
    created_at: datetime | None = None,
    logo_path: Path | str | None = None,
) -> None:
    """Write one finished ride's one-page podium poster PDF to *path*.

    The [5d] prize-table poster: a single celebratory page at Letter
    (A4 via ``letter=False``) carrying the event meta, a "Best poker
    hands" heading + ride title, then the top-3 placings as large
    podium cards -- big place number, ``#plate Entry name``, the
    team/solo line, the hand's title-case prose name, and the best-5
    cards as large faces (steel accent for hearts/diamonds/jokers).
    The footer is a credit line + generated stamp with no "Page n of
    N" -- there is only one page. *path* is the caller-supplied full
    file path; the ``{ride-slug}-podium.pdf`` naming is the menu
    handler's job, never this module's (module-skeletons.md).

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

    Raises:
        ValueError: *created_at* is not tz-aware.
    """
    stamp = (
        created_at
        if created_at is not None
        else datetime.now(
            timezone.utc  # noqa: UP017 -- this mypy build lacks datetime.UTC; the portable form
        )
    )
    poster = _PosterPDF(ride, letter=letter, created_at=stamp, logo_path=logo_path)
    poster.build(placed)
    data = _store_streams_raw(bytes(poster.output()))
    _atomic_write_bytes(path, data)
