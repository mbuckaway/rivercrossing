# SPDX-License-Identifier: GPL-3.0-only
r"""PDF export tests (P7, E6.3.1 / P8, E6.3.2) -- tests first, per R-70.

Pins spec §8b's render contract: ``pdfexport.render(ride, placed,
opts, path)`` writes a print-ready PDF whose sections and flags mirror
the HTML export (R-63) -- podium, top ten, optional laps/time boards,
full field, show/hide times, all cards drawn -- with the retired
designs' print geometry ([5a]-[5c]): Letter/A4, 0.58in margins, footer
rule + "Page n of N" on every page, a page-2+ running title, Barlow +
Barlow Condensed headings + DejaVu Sans suit glyphs, and the
ink/steel/deep-steel tokens. It also pins P8's ``podium_poster``
sibling ([5d]): one celebratory Letter page (A4 via ``letter=False``),
the top-3 placings as large podium cards, hand prose (D1, not
ALL-CAPS), and a credit-line footer with no page count.

Determinism (R-62, D14) is the load-bearing claim: identical inputs
plus the pinned aware-UTC creation stamp produce byte-identical
files, and the committed goldens at
``tests/unit/fixtures/pdfexport/epic-2026-results.pdf`` and
``epic-2026-podium.pdf`` regenerate byte-for-byte from this renderer
(the honest regeneration pattern gen_rank_vectors.py established).
pypdf reads the bytes back to prove page count, page size, section
presence/absence under each flag, DNF marking, the podium's top-3
plates and the per-page footer.
"""

import base64
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pdfexport_fixtures import (
    FIXED_CREATED,
    GOLDEN_PDF,
    GOLDEN_POSTER,
    build_placed,
    build_ride,
    golden_opts,
)
from pypdf import PdfReader

from rivercrossing import pdfexport
from rivercrossing.cards import Card, Rank, Suit
from rivercrossing.hands import best_hand
from rivercrossing.htmlexport import ExportOptions
from rivercrossing.standings import EntryResult, Placed

if TYPE_CHECKING:
    from pathlib import Path

# The podium trio's shared cards: three nines over king and two, so
# every hand in the tiny tests names "Three of a Kind -- Nines".
_FIVE_CARDS = (
    Card(Rank.NINE, Suit.SPADES),
    Card(Rank.NINE, Suit.DIAMONDS),
    Card(Rank.NINE, Suit.CLUBS),
    Card(Rank.KING, Suit.HEARTS),
    Card(Rank.TWO, Suit.SPADES),
)


def _entry(  # noqa: PLR0913 -- (plate, name, laps, kind, dnf): the EntryResult's own fields
    plate: str, name: str, laps: int, *, kind: str = "solo", dnf: bool = False
) -> EntryResult:
    """Build one finished-ride EntryResult for the render() tests.

    The same five-card hand stands in for every entry's draw; total
    time is ``laps * 30min + 2min`` so entry 88's 11 laps read
    "5:32:00" and the R-63 hide-times checks have a known string.
    """
    return EntryResult(
        entry_id=plate,
        plate=plate,
        name=name,
        kind=kind,
        laps=laps,
        total_time=float(laps * 1800 + 120),
        best_lap=1800.0,
        cards=_FIVE_CARDS,
        hand=best_hand(_FIVE_CARDS),
        dnf=dnf,
    )


def _placed_three() -> tuple[Placed, ...]:
    """Build the [5a] podium trio as placed standings."""
    return (
        Placed(
            place=1,
            result=_entry("88", "Moss Ridge Riders", 11),
            tie_note=None,
            draw_required=False,
        ),
        Placed(
            place=2, result=_entry("7", "Luca Ferrari", 10), tie_note=None, draw_required=False
        ),
        Placed(
            place=3,
            result=_entry("127", "Dirt Dynamos", 10, kind="team"),
            tie_note=None,
            draw_required=False,
        ),
    )


def _placed_mixed() -> tuple[Placed, ...]:
    """Five placed entries: the podium trio plus a DNF and a no-show."""
    return (
        *_placed_three(),
        Placed(
            place=4,
            result=_entry("94", "Ted Novak", 4, dnf=True),
            tie_note=None,
            draw_required=False,
        ),
        Placed(place=5, result=_entry("1", "No Show", 0), tie_note=None, draw_required=False),
    )


def _render(  # noqa: PLR0913 -- (tmp_path, placed, opts, letter, created_at): the render() seam inputs
    tmp_path: Path,
    placed: tuple[Placed, ...],
    opts: ExportOptions,
    *,
    letter: bool = True,
    created_at: datetime | None = FIXED_CREATED,
) -> Path:
    """Render *placed* under *opts* to a scratch file and return it."""
    out = tmp_path / "results.pdf"
    pdfexport.render(build_ride(), placed, opts, out, letter=letter, created_at=created_at)
    return out


def _text(pdf_path: Path) -> str:
    """Extract every page's text, joined with newlines."""
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# --------------------------------------------------------- R-62 bytes


def test_render_identical_inputs_produce_identical_bytes(tmp_path: Path) -> None:
    """Two renders of the same inputs are byte-identical (R-62)."""
    first = _render(tmp_path, build_placed(), golden_opts())
    second = _render(tmp_path, build_placed(), golden_opts())

    assert first.read_bytes() == second.read_bytes()


def test_render_naive_created_at_raises_value_error(tmp_path: Path) -> None:
    """D14: a naive creation stamp bakes a local offset; reject it."""
    naive = datetime(2026, 9, 20, 20, 7, 0)  # noqa: DTZ001 -- the test deliberately builds a naive stamp to prove the D14 guard

    with pytest.raises(ValueError, match=re.escape("created_at must be tz-aware")):
        _render(tmp_path, _placed_three(), golden_opts(), created_at=naive)


def test_render_defaults_created_at_to_an_aware_utc_stamp(tmp_path: Path) -> None:
    """The default stamp is aware UTC, so /CreationDate is never local.

    The injected stamp is the determinism seam; the default exists for
    convenience and must still carry a tzinfo (D14), which pypdf reads
    back from the metadata.
    """
    out = _render(tmp_path, _placed_three(), golden_opts(), created_at=None)

    creation = PdfReader(str(out)).metadata.creation_date
    assert isinstance(creation, datetime)
    assert creation.utcoffset() == timedelta(0)


# -------------------------------------------------------------- golden


def test_render_matches_committed_golden_byte_for_byte(tmp_path: Path) -> None:
    """The golden dataset regenerates the frozen PDF exactly.

    The deliberate regeneration TB-5 permits; gen_rank_vectors.py's
    honesty pattern -- the committed artifact is the contract, and
    only a real renderer change justifies regenerating it.
    """
    out = _render(tmp_path, build_placed(), golden_opts())

    assert out.read_bytes() == GOLDEN_PDF.read_bytes()


# ------------------------------------------------------------ structure


def test_render_pdf_has_at_least_two_pages_for_full_field(tmp_path: Path) -> None:
    """The 50-entry full field spans two or more pages."""
    out = _render(tmp_path, build_placed(), golden_opts())

    assert len(PdfReader(str(out)).pages) >= 2


def test_render_pdf_pages_are_letter_size(tmp_path: Path) -> None:
    """Letter pages measure 612 x 792 pt (8.5 x 11 in)."""
    out = _render(tmp_path, build_placed(), golden_opts())
    reader = PdfReader(str(out))

    for page in reader.pages:
        assert float(page.mediabox.width) == pytest.approx(612.0)
        assert float(page.mediabox.height) == pytest.approx(792.0)


def test_render_letter_false_emits_a4_pages(tmp_path: Path) -> None:
    """letter=False selects A4: 595.28 x 841.89 pt."""
    out = _render(tmp_path, _placed_three(), golden_opts(), letter=False)
    page = PdfReader(str(out)).pages[0]

    assert float(page.mediabox.width) == pytest.approx(595.28, abs=0.01)
    assert float(page.mediabox.height) == pytest.approx(841.89, abs=0.01)


def test_render_footer_shows_page_n_of_n_on_every_page(tmp_path: Path) -> None:
    """Each page's footer carries "Page n of N" with the final count."""
    out = _render(tmp_path, build_placed(), golden_opts())
    reader = PdfReader(str(out))
    total = len(reader.pages)

    assert total >= 2
    for index, page in enumerate(reader.pages, start=1):
        assert f"Page {index} of {total}" in (page.extract_text() or "")


# ------------------------------------------------------- option flags


def test_render_laps_board_off_omits_most_laps_section(tmp_path: Path) -> None:
    """laps_board=False drops the "Most laps" board entirely."""
    opts = ExportOptions(laps_board=False, full_field=False, time_board=False)

    text = _text(_render(tmp_path, _placed_mixed(), opts))

    assert "Most laps" not in text


def test_render_laps_board_on_includes_most_laps_section(tmp_path: Path) -> None:
    """laps_board=True (the default) renders the board and its note."""
    opts = ExportOptions(full_field=False, time_board=False)

    text = _text(_render(tmp_path, _placed_mixed(), opts))

    assert "Most laps" in text
    assert "Unofficial — 8 km per lap." in text


def test_render_time_board_off_omits_fastest_section(tmp_path: Path) -> None:
    """time_board=False (the default) drops the "Fastest" board."""
    opts = ExportOptions(time_board=False, full_field=False, laps_board=False)

    text = _text(_render(tmp_path, _placed_mixed(), opts))

    assert "Fastest" not in text


def test_render_time_board_on_includes_fastest_section(tmp_path: Path) -> None:
    """time_board=True renders the "Fastest — laps then time" board."""
    opts = ExportOptions(time_board=True, show_times=True, full_field=False, laps_board=False)

    text = _text(_render(tmp_path, _placed_mixed(), opts))

    assert "Fastest — laps then time" in text


def test_render_show_times_off_omits_time_columns(tmp_path: Path) -> None:
    """R-63: times hidden means no total/best-lap text in any row."""
    opts = ExportOptions(show_times=False, full_field=True, laps_board=True)

    text = _text(_render(tmp_path, _placed_mixed(), opts))

    assert "5:32:00" not in text
    assert "Best lap" not in text


def test_render_show_times_on_includes_time_columns(tmp_path: Path) -> None:
    """show_times=True renders total and best-lap columns."""
    opts = ExportOptions(show_times=True, full_field=True, laps_board=True)

    text = _text(_render(tmp_path, _placed_mixed(), opts))

    assert "5:32:00" in text
    assert "Best lap" in text


def test_render_full_field_off_omits_full_field_section(tmp_path: Path) -> None:
    """full_field=False drops the full-field table."""
    opts = ExportOptions(full_field=False, laps_board=False, time_board=False)

    text = _text(_render(tmp_path, _placed_mixed(), opts))

    assert "Full field" not in text


def test_render_all_cards_off_omits_draw_order_rows(tmp_path: Path) -> None:
    """all_cards=False omits the per-entry draw-order sub-rows."""
    opts = ExportOptions(all_cards=False, full_field=True, laps_board=False, time_board=False)

    text = _text(_render(tmp_path, _placed_mixed(), opts))

    assert "in draw order" not in text


def test_render_all_cards_on_includes_draw_order_rows(tmp_path: Path) -> None:
    """all_cards=True (the default) renders each entry's drawn run."""
    opts = ExportOptions(all_cards=True, full_field=True, laps_board=False, time_board=False)

    text = _text(_render(tmp_path, _placed_mixed(), opts))

    assert "All 5 cards, in draw order:" in text


# -------------------------------------------------------------- content


def test_render_podium_shows_top_three_plates(tmp_path: Path) -> None:
    """The podium lists the top-3 plates with their entry names."""
    text = _text(_render(tmp_path, _placed_three(), golden_opts()))

    assert "#88 Moss Ridge Riders" in text
    assert "#7 Luca Ferrari" in text
    assert "#127 Dirt Dynamos" in text


def test_render_marks_dnf_entries_in_full_field(tmp_path: Path) -> None:
    """A DNF entry renders with the DNF mark beside its name."""
    opts = ExportOptions(full_field=True, laps_board=False, time_board=False)

    text = _text(_render(tmp_path, _placed_mixed(), opts))

    assert "Ted Novak DNF" in text


def test_render_zero_card_entry_renders_with_blank_hand(tmp_path: Path) -> None:
    """A no-show entry (zero cards) renders, with no hand name."""
    no_show = Placed(
        place=1,
        result=EntryResult(
            entry_id="1",
            plate="1",
            name="No Show",
            kind="solo",
            laps=0,
            total_time=0.0,
            best_lap=0.0,
            cards=(),
            hand=best_hand(()),
            dnf=False,
        ),
        tie_note=None,
        draw_required=False,
    )

    text = _text(_render(tmp_path, (no_show,), ExportOptions(full_field=True)))

    assert "No Show" in text


def test_render_empty_field_renders(tmp_path: Path) -> None:
    """A zero-entry field still renders (empty boards and tables)."""
    out = _render(tmp_path, (), ExportOptions(full_field=True, laps_board=True, time_board=True))

    text = _text(out)
    assert "0 · 0 · 0" in text
    assert "Full field" in text


def test_render_cover_block_shows_kicker_title_and_counters(tmp_path: Path) -> None:
    """The cover block carries the event header and tallied counters."""
    text = _text(_render(tmp_path, build_placed(), golden_opts()))

    assert "Official results · poker run" in text
    assert "GORBA EPIC & MTB Festival 2026" in text
    assert "50 · 347 · 349" in text
    assert "entries · laps · cards dealt" in text


def test_render_meta_line_formats_ride_fields(tmp_path: Path) -> None:
    """The meta line formats date, venue and lap_km (8.0 as "8")."""
    text = _text(_render(tmp_path, _placed_three(), golden_opts()))

    assert "Sunday September 20, 2026 · Guelph Lake MTB Trails · 8 km loop" in text


def test_render_credits_and_generated_footer_text(tmp_path: Path) -> None:
    """The footer names the organizer/scorer and the generated stamp."""
    text = _text(_render(tmp_path, _placed_three(), golden_opts()))

    assert "Organizer: GORBA — J. Marsden · Scorer: D. Whitfield" in text
    assert "generated 20:07, Sept 20 2026" in text
    assert "RiverCrossing" in text


# ------------------------------------------------------- poster (5d)


def _poster(  # noqa: PLR0913 -- (tmp_path, placed, letter, created_at): the poster seam inputs
    tmp_path: Path,
    placed: tuple[Placed, ...],
    *,
    letter: bool = True,
    created_at: datetime | None = FIXED_CREATED,
) -> Path:
    """Render *placed* as the podium poster to a scratch file."""
    out = tmp_path / "poster.pdf"
    pdfexport.podium_poster(build_ride(), placed, out, letter=letter, created_at=created_at)
    return out


def test_podium_poster_writes_single_letter_page(tmp_path: Path) -> None:
    """The poster is exactly one page at Letter (612 x 792 pt)."""
    out = _poster(tmp_path, build_placed())
    reader = PdfReader(str(out))

    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert float(page.mediabox.width) == pytest.approx(612.0)
    assert float(page.mediabox.height) == pytest.approx(792.0)


def test_podium_poster_letter_false_emits_a4_page(tmp_path: Path) -> None:
    """letter=False selects A4: 595.28 x 841.89 pt."""
    out = _poster(tmp_path, _placed_three(), letter=False)
    page = PdfReader(str(out)).pages[0]

    assert float(page.mediabox.width) == pytest.approx(595.28, abs=0.01)
    assert float(page.mediabox.height) == pytest.approx(841.89, abs=0.01)


def test_podium_poster_shows_top_three_plates_and_names(tmp_path: Path) -> None:
    """The poster lists the top-3 plates with their entry names."""
    text = _text(_poster(tmp_path, _placed_mixed()))

    assert "#88 Moss Ridge Riders" in text
    assert "#7 Luca Ferrari" in text
    assert "#127 Dirt Dynamos" in text


def test_podium_poster_omits_fourth_place(tmp_path: Path) -> None:
    """Place 4 does not appear on the one-page poster."""
    text = _text(_poster(tmp_path, _placed_mixed()))

    assert "Ted Novak" not in text
    assert "#94" not in text


def test_podium_poster_shows_hand_prose_not_all_caps(tmp_path: Path) -> None:
    """The hand name renders as D1 title-case prose, not ALL-CAPS."""
    text = _text(_poster(tmp_path, _placed_three()))

    assert "Three of a Kind — Nines" in text
    assert "THREE OF A KIND" not in text


def test_podium_poster_team_line_names_team_and_laps(tmp_path: Path) -> None:
    """A team's line reads "Team — name · N laps" from the payload."""
    text = _text(_poster(tmp_path, _placed_three()))

    assert "Team — Dirt Dynamos · 10 laps" in text


def test_podium_poster_solo_line_names_rider_and_laps(tmp_path: Path) -> None:
    """A solo line reads "Solo — name · N laps"."""
    text = _text(_poster(tmp_path, _placed_three()))

    assert "Solo — Moss Ridge Riders · 11 laps" in text
    assert "Solo — Luca Ferrari · 10 laps" in text


def test_podium_poster_card_faces_text_present(tmp_path: Path) -> None:
    """The large card faces carry each card's rank and suit glyphs."""
    text = _text(_poster(tmp_path, _placed_three()))

    assert "9♠" in text
    assert "9♦" in text
    assert "9♣" in text
    assert "K♥" in text
    assert "2♠" in text


def test_podium_poster_footer_shows_credits_and_generated_no_page_count(
    tmp_path: Path,
) -> None:
    """Footer names organizer/scorer and stamp; no "Page n of N"."""
    text = _text(_poster(tmp_path, _placed_three()))

    assert "Organizer: GORBA — J. Marsden · Scorer: D. Whitfield" in text
    assert "generated 20:07, Sept 20 2026" in text
    assert "RiverCrossing" in text
    assert "Page 1 of" not in text


def test_podium_poster_identical_inputs_produce_identical_bytes(tmp_path: Path) -> None:
    """Two renders of the same inputs are byte-identical (R-62)."""
    first = _poster(tmp_path, build_placed())
    second = _poster(tmp_path, build_placed())

    assert first.read_bytes() == second.read_bytes()


def test_podium_poster_naive_created_at_raises_value_error(tmp_path: Path) -> None:
    """D14: a naive creation stamp bakes a local offset; reject it."""
    naive = datetime(2026, 9, 20, 20, 7, 0)  # noqa: DTZ001 -- the test deliberately builds a naive stamp to prove the D14 guard

    with pytest.raises(ValueError, match=re.escape("created_at must be tz-aware")):
        _poster(tmp_path, _placed_three(), created_at=naive)


def test_podium_poster_fewer_than_three_entries_renders_available(tmp_path: Path) -> None:
    """A short field still renders the entries it has, on one page."""
    out = _poster(tmp_path, _placed_three()[:2])
    reader = PdfReader(str(out))

    assert len(reader.pages) == 1
    text = _text(out)
    assert "#88 Moss Ridge Riders" in text
    assert "#7 Luca Ferrari" in text
    assert "#127 Dirt Dynamos" not in text


def test_podium_poster_zero_card_entry_renders_with_blank_hand(tmp_path: Path) -> None:
    """A no-show entry (zero cards) renders, with no hand name."""
    no_show = Placed(
        place=1,
        result=EntryResult(
            entry_id="1",
            plate="1",
            name="No Show",
            kind="solo",
            laps=0,
            total_time=0.0,
            best_lap=0.0,
            cards=(),
            hand=best_hand(()),
            dnf=False,
        ),
        tie_note=None,
        draw_required=False,
    )

    text = _text(_poster(tmp_path, (no_show,)))

    assert "No Show" in text


def test_podium_poster_matches_committed_golden_byte_for_byte(tmp_path: Path) -> None:
    """The golden dataset regenerates the frozen poster exactly."""
    out = _poster(tmp_path, build_placed())

    assert out.read_bytes() == GOLDEN_POSTER.read_bytes()


@pytest.mark.parametrize(
    ("kind", "name", "laps", "expected"),
    [
        ("solo", "Luca Ferrari", 10, "Solo — Luca Ferrari · 10 laps"),
        ("team", "Dirt Dynamos", 10, "Team — Dirt Dynamos · 10 laps"),
        ("team", "Moss Ridge Riders", 11, "Team — Moss Ridge Riders · 11 laps"),
    ],
)
def test_poster_subtitle_formats_team_and_solo_lines(  # noqa: PLR0913, PLR0917 -- the parametrize row's four inputs
    kind: str, name: str, laps: int, expected: str
) -> None:
    """The poster's team/solo line renders kind, name and laps."""
    assert pdfexport._poster_subtitle(_entry("88", name, laps, kind=kind)) == expected


@pytest.mark.parametrize(
    ("card", "expected"),
    [
        (Card(Rank.NINE, Suit.SPADES), "9♠"),
        (Card(Rank.TEN, Suit.CLUBS), "10♣"),
        (Card(Rank.ACE, Suit.HEARTS), "A♥"),
        (Card(Rank.QUEEN, Suit.DIAMONDS), "Q♦"),
        (Card(rank=None, suit=None, joker=True), "★JOKER"),
    ],
)
def test_poster_card_text_renders_rank_suit_and_joker(card: Card, expected: str) -> None:
    """A large card face reads rank+suit, and "★JOKER" for the joker."""
    assert pdfexport._poster_card_text(card) == expected


def test_hand_prose_is_title_case_for_a_real_hand() -> None:
    """D1 prose casing, unlike the report's uppercase table label."""
    assert pdfexport._hand_prose(best_hand(_FIVE_CARDS)) == "Three of a Kind — Nines"


def test_hand_prose_is_blank_for_a_no_card_hand() -> None:
    """The empty-hand guard displays "" -- pinned (like the HTML)."""
    assert pdfexport._hand_prose(best_hand(())) == ""


@given(
    rank=st.sampled_from(list(Rank)),
    suit=st.sampled_from(list(Suit)),
)
def test_poster_card_text_embeds_rank_letter_and_suit_glyph(rank: Rank, suit: Suit) -> None:
    """Property: a natural face carries rank letter and suit glyph."""
    text = pdfexport._poster_card_text(Card(rank=rank, suit=suit))

    assert text[:-1] == pdfexport._RANK_LETTER[rank.value]
    assert text[-1] == pdfexport._SUIT_GLYPH[suit]


# ------------------------------------------------- pure-function bounds


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "0:00"),
        (59.0, "0:59"),
        (60.0, "1:00"),
        (3599.0, "59:59"),
        (3600.0, "1:00:00"),
        (3601.0, "1:00:01"),
        (1679.0, "27:59"),
        (21161.0, "5:52:41"),
    ],
)
def test_format_duration_formats_seconds_as_clock_text(seconds: float, expected: str) -> None:
    """Duration text matches the golden pages' clock format."""
    assert pdfexport._format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("card", "expected"),
    [
        (Card(Rank.NINE, Suit.SPADES), "9♠"),
        (Card(Rank.TEN, Suit.CLUBS), "10♣"),
        (Card(Rank.ACE, Suit.HEARTS), "A♥"),
        (Card(Rank.QUEEN, Suit.DIAMONDS), "Q♦"),
        (Card(rank=None, suit=None, joker=True), "★"),
    ],
)
def test_card_text_renders_rank_suit_and_joker(card: Card, expected: str) -> None:
    """One card renders as rank letter + suit glyph, joker as ★."""
    assert pdfexport._card_text(card) == expected


def test_hand_label_is_blank_for_a_no_card_hand() -> None:
    """The empty-hand guard displays "" -- pinned (like the HTML)."""
    assert pdfexport._hand_label(best_hand(())) == ""


def test_hand_label_uppercases_the_prose_name() -> None:
    """Hands render uppercase in the PDF, matching the HTML's CSS."""
    assert pdfexport._hand_label(best_hand(_FIVE_CARDS)) == "THREE OF A KIND — NINES"


@pytest.mark.parametrize(
    ("lap_km", "expected"),
    [
        (8.0, "8"),
        (0.0, "0"),
        (180.0, "180"),
        (8.5, "8.5"),
        (10.25, "10.25"),
    ],
)
def test_format_km_formats_integral_and_fractional_lap_km(lap_km: float, expected: str) -> None:
    """D5: integral lap_km renders as "8", fractional as "8.5"."""
    assert pdfexport._format_km(lap_km) == expected


def test_cards_cell_clips_cards_wider_than_the_column() -> None:
    """A card crossing the column's right edge is dropped, not spilled.

    The full-field/top-ten card columns are wide enough that the guard
    never trips in a normal render; this pins its contract directly --
    a too-narrow column yields no text past its right edge.
    """
    report = pdfexport._ReportPDF(
        build_ride(), golden_opts(), letter=True, created_at=FIXED_CREATED
    )
    report.add_page()

    report._cards_cell(_FIVE_CARDS, 0.05)

    assert report.get_x() == report.l_margin


def _parse_duration(text: str) -> int:
    """Parse clock text back to whole seconds (test helper)."""
    parts = [int(part) for part in text.split(":")]
    seconds = parts[-1] + 60 * parts[-2]
    if len(parts) == 3:
        seconds += 3600 * parts[0]
    return seconds


@given(seconds=st.integers(min_value=0, max_value=86399))
def test_format_duration_round_trips_whole_seconds(seconds: int) -> None:
    """Property: clock text parses back to the same seconds."""
    rendered = pdfexport._format_duration(float(seconds))

    assert _parse_duration(rendered) == seconds


# ------------------------------------------------- logo seam (E6.4.2)


def _logo_png(tmp_path: Path) -> Path:
    """Write a tiny valid PNG and return its path (1x1 transparent)."""
    logo = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    logo_file = tmp_path / "logo.png"
    logo_file.write_bytes(logo)
    return logo_file


def test_render_with_logo_path_writes_a_valid_pdf(tmp_path: Path) -> None:
    """logo_path embeds the organizer logo without error (R-62/5c)."""
    logo = _logo_png(tmp_path)
    out = tmp_path / "report.pdf"
    stamp = FIXED_CREATED
    ride, placed = build_ride(), build_placed()

    pdfexport.render(ride, placed, ExportOptions(), out, created_at=stamp, logo_path=logo)

    reader = PdfReader(str(out))
    assert len(reader.pages) >= 1


def test_podium_poster_with_logo_path_stays_single_page(tmp_path: Path) -> None:
    """The poster's logo draws on the one celebratory page."""
    logo = _logo_png(tmp_path)
    out = tmp_path / "podium.pdf"
    stamp = FIXED_CREATED
    ride, placed = build_ride(), build_placed()

    pdfexport.podium_poster(ride, placed, out, created_at=stamp, logo_path=logo)

    assert len(PdfReader(str(out)).pages) == 1


def test_render_logo_keeps_byte_determinism(tmp_path: Path) -> None:
    """Two logo renders with the same stamp are byte-identical."""
    logo = _logo_png(tmp_path)
    stamp = FIXED_CREATED
    ride, placed = build_ride(), build_placed()
    first, second = tmp_path / "a.pdf", tmp_path / "b.pdf"

    pdfexport.render(ride, placed, ExportOptions(), first, created_at=stamp, logo_path=logo)
    pdfexport.render(ride, placed, ExportOptions(), second, created_at=stamp, logo_path=logo)

    assert first.read_bytes() == second.read_bytes()
