# SPDX-License-Identifier: GPL-3.0-only
r"""PDF export tests (P7, E6.3.1 / P8, E6.3.2) -- tests first, per R-70.

Pins spec §8b's render contract: ``pdfexport.render(ride, placed,
opts, path)`` writes a print-ready PDF whose sections and flags mirror
the HTML export (R-63) -- per-kind podiums (3/3 on a team event, 3 on
a solo one), per-kind top lists (5/5 or 10), the laps boards (5/5 or
10), the flat ten-row time board, and the full field under its
umbrella heading with "Teams"/"Solo riders" subsections; show/hide
times and all-cards-drawn still switch -- with the retired designs'
print geometry ([5a]-[5c]): Letter/A4, 0.58in margins, footer rule +
"Page n of N" on every page, a page-2+ running title, Barlow + Barlow
Condensed headings + DejaVu Sans suit glyphs, and the
ink/steel/deep-steel tokens. Teams never show a plate at any of the
six render sites (podium card, top-list column, laps row, time row,
full-field row, poster card), mirroring the HTML's own rule. It also
pins P8's ``podium_poster`` sibling ([5d]): one celebratory Letter
page (A4 via ``letter=False``) -- a team event's two stacked compact
sections ("Teams" top 3 then "Solo riders" top 3, plate-less team
cards), a solo event's top five at full sizing -- hand prose (D1, not
ALL-CAPS), and a credit-line footer with no page count.

Determinism (R-62, D14) is the load-bearing claim: identical inputs
plus the pinned aware-UTC creation stamp produce byte-identical
files, and the committed goldens at
``tests/unit/fixtures/pdfexport/epic-2026-results.pdf`` and
``epic-2026-podium.pdf`` regenerate byte-for-byte from this renderer
(the honest regeneration pattern gen_rank_vectors.py established).
pypdf reads the bytes back to prove page count, page size, section
presence/absence under each flag, DNF marking, the per-kind top-3
cards and the per-page footer.
"""

import base64
import io
import os
import re
import zlib
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
from rivercrossing.htmlexport import ExportOptions, build_payload
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


def _entry(  # noqa: PLR0913 -- (plate, name, laps, kind, dnf, sex): the EntryResult's own fields
    plate: str,
    name: str,
    laps: int,
    *,
    kind: str = "solo",
    dnf: bool = False,
    sex: str | None = None,
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
        sex=sex,
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
    """Eight placed entries in the app's own order: teams, then solo.

    Per-kind places -- three teams ranked 1-3, then five solo riders
    ranked 1-5 (a DNF and a no-show at the tail), exactly the sequence
    ``rank_by_kind`` hands the exporters.
    """
    return (
        Placed(
            place=1,
            result=_entry("127", "Dirt Dynamos", 10, kind="team"),
            tie_note=None,
            draw_required=False,
        ),
        Placed(
            place=2,
            result=_entry("64", "Fat Tire Four", 9, kind="team"),
            tie_note=None,
            draw_required=False,
        ),
        Placed(
            place=3,
            result=_entry("33", "Mud Slingers", 8, kind="team"),
            tie_note=None,
            draw_required=False,
        ),
        Placed(
            place=1,
            result=_entry("88", "Moss Ridge Riders", 11),
            tie_note=None,
            draw_required=False,
        ),
        Placed(
            place=2, result=_entry("7", "Luca Ferrari", 10), tie_note=None, draw_required=False
        ),
        Placed(place=3, result=_entry("55", "Ana Souza", 9), tie_note=None, draw_required=False),
        Placed(
            place=4,
            result=_entry("94", "Ted Novak", 4, dnf=True),
            tie_note=None,
            draw_required=False,
        ),
        Placed(place=5, result=_entry("1", "No Show", 0), tie_note=None, draw_required=False),
    )


def _team_field(teams: int, solo: int) -> tuple[Placed, ...]:
    """Build a per-kind ranked field: *teams* rows, then *solo*.

    Team plates are 600+ and solo plates 500+, so every plate assertion
    names its own kind; laps fall with place so the boards' order is
    deterministic.
    """
    entries = [
        Placed(
            place=index + 1,
            result=_entry(str(600 + index), f"Team {index + 1}", 12 - index, kind="team"),
            tie_note=None,
            draw_required=False,
        )
        for index in range(teams)
    ]
    entries += [
        Placed(
            place=index + 1,
            result=_entry(str(500 + index), f"Solo {index + 1}", 11 - index),
            tie_note=None,
            draw_required=False,
        )
        for index in range(solo)
    ]
    return tuple(entries)


def _ranked_solo(count: int) -> tuple[Placed, ...]:
    """Build *count* solo entries with laps falling from 20."""
    return tuple(
        Placed(
            place=index + 1,
            result=_entry(str(500 + index), f"Solo {index + 1}", 20 - index),
            tie_note=None,
            draw_required=False,
        )
        for index in range(count)
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


def _section(text: str, heading: str, until: str | None = None) -> str:
    """Return the text from *heading* up to the next *until* heading.

    Extraction follows drawing order, so a slice keeps each assertion
    scoped to its own section -- a plate absent from the Teams table
    cannot be satisfied by the Solo table's own plate below it.
    """
    start = text.find(heading)
    assert start != -1, f"heading not found: {heading!r}"
    if until is None:
        return text[start:]
    stop = text.find(until, start + len(heading))
    assert stop != -1, f"following heading not found: {until!r}"
    return text[start:stop]


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


def test_render_full_field_splits_into_teams_then_solo_sections(tmp_path: Path) -> None:
    """The umbrella heading stays; the subsections are titled."""
    text = _text(_render(tmp_path, build_placed(9), golden_opts()))

    field = _section(text, "Full field")
    assert "Teams" in field
    assert "Solo riders" in field
    assert field.find("Teams") < field.find("Solo riders")


def test_render_full_field_solo_only_field_omits_the_teams_section(tmp_path: Path) -> None:
    """A solo-only field renders one subsection: Solo riders."""
    opts = ExportOptions(full_field=True, laps_board=False, time_board=False)
    solo_only = _placed_three()[:2]  # two solo entries, no team

    text = _text(_render(tmp_path, solo_only, opts))

    assert "Solo riders" in text
    assert "Teams" not in text


def test_render_full_field_team_only_field_omits_the_solo_section(tmp_path: Path) -> None:
    """A team-only field renders one subsection: Teams."""
    opts = ExportOptions(full_field=True, laps_board=False, time_board=False)
    team_only = _placed_three()[2:]  # the one team entry

    text = _text(_render(tmp_path, team_only, opts))

    assert "Teams" in text
    assert "Solo riders" not in text


@pytest.mark.parametrize(
    ("name", "kind", "sex", "expected_cell"),
    [
        ("Luca Ferrari", "solo", "M", "Luca Ferrari (M)"),
        ("Luca Ferrari", "solo", "F", "Luca Ferrari (F)"),
        ("Luca Ferrari", "solo", None, "Luca Ferrari"),
        ("Moss Ridge Riders", "team", None, "Moss Ridge Riders"),
    ],
)
def test_render_full_field_marks_the_sex_for_solo_entries_only(  # noqa: PLR0913, PLR0917 -- the parametrize row's four inputs
    tmp_path: Path, name: str, kind: str, sex: str | None, expected_cell: str
) -> None:
    """The full-field entry cell appends the sex for a solo only."""
    placed = (
        Placed(
            place=1,
            result=_entry("88", name, 10, kind=kind, sex=sex),
            tie_note=None,
            draw_required=False,
        ),
    )
    opts = ExportOptions(full_field=True, laps_board=False, time_board=False)

    text = _text(_render(tmp_path, placed, opts))

    assert expected_cell in text


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


def test_render_podium_shows_each_kinds_top_three_with_plate_less_team_cards(
    tmp_path: Path,
) -> None:
    """Team event: a teams podium (no plate) then a solo podium."""
    text = _text(_render(tmp_path, _team_field(4, 4), golden_opts()))

    teams = _section(text, "Best hands — teams", "Best hands — solo riders")
    assert "Team 3" in teams
    assert "Team 4" not in teams
    assert "#600" not in teams
    solo = _section(text, "Best hands — solo riders", "Top teams")
    assert "#500 Solo 1" in solo
    assert "Solo 4" not in solo


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
    """The footer names the credits and the shared stamp."""
    text = _text(_render(tmp_path, _placed_three(), golden_opts()))

    assert "Organizer: GORBA — J. Marsden · Scorer: D. Whitfield" in text
    assert "Generated 20:07, Sept 20 2026" in text
    assert "RiverCrossing" in text


def test_render_team_event_top_lists_split_per_kind_without_a_team_plate_column(
    tmp_path: Path,
) -> None:
    """Top teams (5, no Plate column) then Top solo riders (5)."""
    text = _text(_render(tmp_path, _team_field(6, 6), golden_opts()))

    teams = _section(text, "Top teams", "Top solo riders")
    assert "Team 5" in teams
    assert "Team 6" not in teams
    assert "#600" not in teams
    assert "Plate" not in teams
    solo = _section(text, "Top solo riders", "Most laps")
    assert "Solo 5" in solo
    assert "Solo 6" not in solo
    assert "500 Solo 1" in solo


def test_render_team_event_laps_boards_split_per_kind(tmp_path: Path) -> None:
    """Most laps — teams (no plate) then — solo riders (plate)."""
    text = _text(_render(tmp_path, _team_field(6, 6), golden_opts()))

    teams = _section(text, "Most laps — teams", "Most laps — solo riders")
    assert "Team 5" in teams
    assert "Team 6" not in teams
    assert "#600" not in teams
    solo = _section(text, "Most laps — solo riders", "Fastest — laps then time")
    assert "Solo 5" in solo
    assert "Solo 6" not in solo
    assert "#500" in solo


def test_render_team_event_time_board_stays_flat_and_hides_team_plates(
    tmp_path: Path,
) -> None:
    """One flat board; a team row shows no plate."""
    text = _text(_render(tmp_path, _team_field(6, 6), golden_opts()))

    fastest = _section(text, "Fastest — laps then time", "Full field")
    assert "Team 1" in fastest
    assert "#600" not in fastest
    assert "#500 Solo 1" in fastest


def test_render_team_event_full_field_teams_table_drops_plate_and_type(
    tmp_path: Path,
) -> None:
    """The Teams table is Place|Entry|Laps|times|Cards|Hand."""
    text = _text(_render(tmp_path, _team_field(4, 4), golden_opts()))

    teams = _section(text, "Teams", "Solo riders")
    assert "Team 4" in teams
    assert "#600" not in teams
    assert "Type" not in teams
    assert "Cards" in teams
    assert "500 Solo 1" in _section(text, "Solo riders")


def test_render_team_field_row_draws_the_inline_logo_when_the_row_has_one(
    tmp_path: Path,
) -> None:
    """A team full-field row draws its small inline logo."""
    logo = _data_uri(_logo_png(tmp_path))
    payload = build_payload(
        build_ride(),
        _team_field(1, 0),
        golden_opts(),
        "Generated 20:07, Sept 20 2026",
        team_logos={"600": logo},
    )
    report = pdfexport._ReportPDF(
        build_ride(), golden_opts(), letter=True, created_at=FIXED_CREATED
    )
    report.add_page()
    widths = pdfexport._team_field_widths(show_times=True, content=report._content_width())

    report._team_field_row(widths, payload.results[0])

    page = PdfReader(io.BytesIO(bytes(report.output()))).pages[0]
    assert len(page.images) == 1


def test_render_team_field_row_draws_no_image_when_the_row_has_no_logo() -> None:
    """A row without a logo renders text only."""
    payload = build_payload(
        build_ride(), _team_field(1, 0), golden_opts(), "Generated 20:07, Sept 20 2026"
    )
    report = pdfexport._ReportPDF(
        build_ride(), golden_opts(), letter=True, created_at=FIXED_CREATED
    )
    report.add_page()
    widths = pdfexport._team_field_widths(show_times=True, content=report._content_width())

    report._team_field_row(widths, payload.results[0])

    page = PdfReader(io.BytesIO(bytes(report.output()))).pages[0]
    assert len(page.images) == 0


def test_render_solo_event_keeps_the_single_section_headings(tmp_path: Path) -> None:
    """A solo field: Top ten, Most laps, Solo riders."""
    text = _text(_render(tmp_path, _ranked_solo(12), golden_opts()))

    assert "Best hands — top 3" in text
    assert "Top ten" in text
    assert "Most laps — solo riders" not in text
    assert "Top teams" not in text
    assert "Best hands — teams" not in text
    assert "Full field" in text
    assert "Solo riders" in text
    assert "Teams" not in text


def test_render_solo_event_top_lists_and_laps_board_cap_at_ten(tmp_path: Path) -> None:
    """A solo field keeps the HTML's ten-row top list and laps board."""
    text = _text(_render(tmp_path, _ranked_solo(12), golden_opts()))

    top = _section(text, "Top ten", "Most laps")
    assert "Solo 10" in top
    assert "Solo 11" not in top
    board = _section(text, "Most laps", "Fastest — laps then time")
    assert "Solo 10" in board
    assert "Solo 11" not in board


def test_render_time_board_lists_ten_rows_mirroring_the_html(tmp_path: Path) -> None:
    """The fastest board keeps the HTML's ten rows, not five."""
    text = _text(_render(tmp_path, _ranked_solo(12), golden_opts()))

    fastest = _section(text, "Fastest — laps then time", "Full field")
    assert "Solo 10" in fastest
    assert "Solo 11" not in fastest


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


def test_podium_poster_shows_each_kinds_top_three_with_plate_less_team_cards(
    tmp_path: Path,
) -> None:
    """Team cards show no plate; solo cards keep theirs."""
    text = _text(_poster(tmp_path, _team_field(4, 4)))

    teams = _section(text, "Teams", "Solo riders")
    assert "Team 3" in teams
    assert "Team 4" not in teams
    assert "#600" not in teams
    solo = _section(text, "Solo riders")
    assert "#500 Solo 1" in solo
    assert "Solo 4" not in solo


def test_podium_poster_team_event_stacks_teams_over_solo_riders(tmp_path: Path) -> None:
    """A team event's poster carries two titled sections."""
    text = _text(_poster(tmp_path, build_placed()))

    assert "Teams" in text
    assert "Solo riders" in text
    assert text.find("Teams") < text.find("Solo riders")


def test_podium_poster_empty_field_renders_one_page(tmp_path: Path) -> None:
    """A zero-entry field still renders the poster's cover, one page."""
    out = _poster(tmp_path, ())

    text = _text(out)
    assert "GORBA EPIC & MTB Festival 2026" in text
    assert len(PdfReader(str(out)).pages) == 1


def test_podium_poster_team_only_field_omits_the_solo_riders_section(
    tmp_path: Path,
) -> None:
    """A team-only event's poster renders the Teams section alone."""
    text = _text(_poster(tmp_path, _team_field(3, 0)))

    assert "Teams" in text
    assert "Team 3" in text
    assert "Solo riders" not in text


def test_podium_poster_team_event_content_fits_one_letter_page() -> None:
    """Six compact cards and two headings end above the footer.

    The measured fit, not just a page count: the last card's baseline
    must leave the footer gap clear, or the poster is clipped.
    """
    poster = pdfexport._PosterPDF(
        build_ride(), letter=True, created_at=FIXED_CREATED, logo_path=None
    )

    poster.build(build_placed())

    assert poster.page_no() == 1
    assert poster.get_y() <= poster.h - pdfexport._FOOTER_GAP_IN


def test_podium_poster_solo_event_shows_the_top_five_solos(tmp_path: Path) -> None:
    """A solo poster lists five solos, one page."""
    out = _poster(tmp_path, _ranked_solo(7))

    text = _text(out)
    assert "#500 Solo 1" in text
    assert "#504 Solo 5" in text
    assert "Solo 6" not in text
    assert len(PdfReader(str(out)).pages) == 1


def test_podium_poster_omits_the_fourth_place_of_each_kind(tmp_path: Path) -> None:
    """Place 4 of either kind does not appear on the one-page poster."""
    text = _text(_poster(tmp_path, _team_field(4, 4)))

    assert "Team 4" not in text
    assert "Solo 4" not in text
    assert "#603" not in text
    assert "#503" not in text


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
    """Footer names the credits and the shared stamp."""
    text = _text(_poster(tmp_path, _placed_three()))

    assert "Organizer: GORBA — J. Marsden · Scorer: D. Whitfield" in text
    assert "Generated 20:07, Sept 20 2026" in text
    assert "RiverCrossing" in text
    assert "Page 1 of" not in text


def test_podium_poster_omits_the_dnf_tail_of_the_field(tmp_path: Path) -> None:
    """Solo places 4-5 stay off the poster."""
    text = _text(_poster(tmp_path, _placed_mixed()))

    assert "Ted Novak" not in text
    assert "No Show" not in text
    assert "#94" not in text


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


# -------------------------------------------- compression determinism


def test_render_pdf_stores_no_flatedecode_streams(tmp_path: Path) -> None:
    """The report never zlib-compresses its streams (R-62/D14).

    The python.org Windows build links zlib-ng while the macOS build
    uses the platform zlib, and the two emit different deflate bytes
    for identical input (measured: no zlib-ng level reproduces the
    macOS-compressed golden). Any compressed stream would therefore
    break the byte-for-byte golden tests on one OS or the other;
    uncompressed streams are deterministic by construction.
    """
    out = _render(tmp_path, build_placed(), golden_opts())

    assert b"/FlateDecode" not in out.read_bytes()


def test_podium_poster_stores_no_flatedecode_streams(tmp_path: Path) -> None:
    """The podium poster never zlib-compresses its streams (R-62/D14).

    Same cross-OS determinism contract as the report document: the
    poster must regenerate byte-identically on every platform.
    """
    out = _poster(tmp_path, build_placed())

    assert b"/FlateDecode" not in out.read_bytes()


def test_store_streams_raw_no_flatedecode_streams_returns_input_unchanged() -> None:
    """A raw document with no FlateDecode streams passes through (T-3).

    Every render()/podium_poster() output is already raw (R-62/D14),
    so re-running the stream pass over such a document must return the
    bytes untouched: the classic xref rebuild must not run on a
    document that has no streams to replace.
    """
    raw = GOLDEN_PDF.read_bytes()

    assert pdfexport._store_streams_raw(raw) == raw


def test_raw_stream_span_body_without_line_ending_still_replaces() -> None:
    """A stream body ending in neither CRLF nor LF still converts (T-3).

    fpdf2 always ends its stream bodies with a line ending, but the
    span logic trims the trailing newline only when one is present. A
    body whose final byte is not a newline must still deflate and be
    replaced raw, with the /Filter entry dropped and /Length updated.
    """
    content = b"BT (uncompressed text) Tj ET"
    body = zlib.compress(content) + b"\x00"  # final byte is not a newline
    pdf = (
        b"1 0 obj\n"
        b"<<\n/Filter /FlateDecode\n/Length 0\n"
        b">>\nstream\n" + body + b"endstream\nendobj\n"
    )
    match = re.search(rb"stream\r?\n", pdf)

    span = pdfexport._raw_stream_span(pdf, match)

    assert span is not None
    replacement = span[3]
    length_entry = b"<<\n/Length " + str(len(content)).encode("ascii") + b"\n>>\nstream\n"
    assert replacement == length_entry + content + b"\nendstream"


def test_raw_stream_span_without_a_length_entry_falls_back_to_the_keyword() -> None:
    """A dict with no /Length resolves the body via "endstream" (T-3).

    Every fpdf2 stream dict carries /Length, but the span logic must
    still inflate a document whose dict omits it.
    """
    content = b"BT (uncompressed text) Tj ET"
    pdf = (
        b"1 0 obj\n"
        b"<<\n/Filter /FlateDecode\n"
        b">>\nstream\n" + zlib.compress(content) + b"\nendstream\nendobj\n"
    )
    match = re.search(rb"stream\r?\n", pdf)

    span = pdfexport._raw_stream_span(pdf, match)

    assert span is not None
    assert span[3] == b"<<\n>>\nstream\n" + content + b"\nendstream"


@pytest.mark.parametrize(
    ("kind", "sex", "name", "laps", "expected"),
    [
        ("solo", "M", "Luca Ferrari", 10, "Solo (M) — Luca Ferrari · 10 laps"),
        ("solo", "F", "Ana Souza", 8, "Solo (F) — Ana Souza · 8 laps"),
        ("solo", None, "Peter Kim", 9, "Solo — Peter Kim · 9 laps"),
        ("team", None, "Dirt Dynamos", 10, "Team — Dirt Dynamos · 10 laps"),
        ("team", None, "Moss Ridge Riders", 11, "Team — Moss Ridge Riders · 11 laps"),
    ],
)
def test_poster_subtitle_formats_team_and_solo_lines(  # noqa: PLR0913, PLR0917 -- the parametrize row's five inputs
    kind: str, sex: str | None, name: str, laps: int, expected: str
) -> None:
    """The poster's team/solo line renders kind, sex, name and laps."""
    assert pdfexport._poster_subtitle(_entry("88", name, laps, kind=kind, sex=sex)) == expected


@given(
    name=st.text(max_size=40),
    laps=st.integers(min_value=0, max_value=60),
    sex=st.sampled_from(["M", "F", None]),
)
def test_poster_subtitle_preserves_the_solo_name_laps_and_sex(
    name: str, laps: int, sex: str | None
) -> None:
    """Property: a solo line keeps its name, laps and sex marker."""
    line = pdfexport._poster_subtitle(_entry("88", name, laps, sex=sex))
    marker = f" ({sex})" if sex is not None else ""

    assert line == f"Solo{marker} — {name} · {laps} laps"


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
    assert pdfexport._hand_label("") == ""


def test_hand_label_uppercases_the_prose_name() -> None:
    """Hands render uppercase in the PDF, matching the HTML's CSS."""
    assert pdfexport._hand_label("Three of a Kind — Nines") == "THREE OF A KIND — NINES"


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

    report._cards_cell((("9", "s"), ("9", "d"), ("9", "c"), ("K", "h"), ("2", "s")), 0.05)

    assert report.get_x() == report.l_margin


# ------------------------------------------------- logo seam (E6.4.2)


def _logo_png(tmp_path: Path) -> Path:
    """Write a tiny valid PNG and return its path (1x1 transparent)."""
    logo = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    logo_file = tmp_path / "logo.png"
    logo_file.write_bytes(logo)
    return logo_file


def _data_uri(path: Path) -> str:
    """Return the PNG at *path* as the payload's own logo data URI."""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


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


# -------------------------------------------- R-52 atomic export writes


def test_render_stages_a_temp_file_then_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """render() swaps the destination in wholesale via os.replace().

    The recording double forwards to the real os.replace, proving both
    halves of the R-52 guarantee: the PDF bytes land in a same-directory
    temp sibling first (the destination is never truncated in place),
    and after the swap no temp file remains next to it.
    """
    out = tmp_path / "results.pdf"
    calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def recording_replace(src: str, dst: str) -> None:
        calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", recording_replace)

    pdfexport.render(build_ride(), build_placed(), golden_opts(), out, created_at=FIXED_CREATED)

    assert calls == [(str(out.with_name(out.name + ".tmp")), str(out))]
    assert out.read_bytes() == GOLDEN_PDF.read_bytes()
    assert sorted(tmp_path.iterdir()) == [out]


def test_podium_poster_stages_a_temp_file_then_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """podium_poster() also writes via a temp sibling + os.replace()."""
    out = tmp_path / "poster.pdf"
    calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def recording_replace(src: str, dst: str) -> None:
        calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", recording_replace)

    pdfexport.podium_poster(build_ride(), build_placed(), out, created_at=FIXED_CREATED)

    assert calls == [(str(out.with_name(out.name + ".tmp")), str(out))]
    assert out.read_bytes() == GOLDEN_POSTER.read_bytes()
    assert sorted(tmp_path.iterdir()) == [out]


def test_render_failure_during_replace_leaves_the_previous_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash at the swap leaves the old complete PDF intact."""
    out = tmp_path / "results.pdf"
    out.write_bytes(GOLDEN_PDF.read_bytes())
    ride, placed = build_ride(), build_placed()

    def crashing_replace(_src: str, _dst: str) -> None:
        raise OSError("simulated crash during atomic replace")

    monkeypatch.setattr(os, "replace", crashing_replace)

    with pytest.raises(OSError, match=re.escape("simulated crash")):
        pdfexport.render(ride, placed, golden_opts(), out, created_at=FIXED_CREATED)

    assert out.read_bytes() == GOLDEN_PDF.read_bytes()
