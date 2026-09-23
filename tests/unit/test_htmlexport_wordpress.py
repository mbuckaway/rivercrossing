# SPDX-License-Identifier: GPL-3.0-only
r"""WordPress content-fragment renderer tests (R-61).

``htmlexport.render_wordpress`` renders one finished ride as an inner
HTML fragment for a WordPress Page's ``content``: the same shared model
and the same macros as :func:`rivercrossing.htmlexport.render`, minus
the document scaffold (no ``<!DOCTYPE>``/``<html>``/``<head>``/
``<body>``) and minus the embedded ``race-data`` record -- the page a
fragment lands in carries its own document, and nothing on the site
reads the record. The whole fragment is wrapped in
``<div class="rc-results">`` and inlines ``compiled_css_wp``, the
stylesheet ``tools/gen_css.py`` has already scoped under that wrapper,
so publishing it cannot restyle the site's own theme.

``htmlexport.render_poster_wordpress`` is the podium poster in that same
fragment shape: the poster's cards and its counter header, for a page
that already carries a document.

Written FIRST: this file is red until ``wordpress.html.j2``,
``poster_wordpress.html.j2`` and their renderers land.
"""

import base64
import re
from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING

import pytest

from rivercrossing.cards import Card, Rank, Suit
from rivercrossing.hands import best_hand
from rivercrossing.htmlexport import (
    _TRANSPARENT_PNG,
    SELF_TEST_NOTE,
    ExportOptions,
    render,
    render_poster_wordpress,
    render_wordpress,
)
from rivercrossing.standings import EntryResult, Placed

if TYPE_CHECKING:
    from pathlib import Path

_OPTIONS = ExportOptions(show_times=True)
_GENERATED = "Generated 09:00, June 1 2026"

_FIVE_CARDS = (
    Card(Rank.NINE, Suit.SPADES),
    Card(Rank.NINE, Suit.DIAMONDS),
    Card(Rank.NINE, Suit.CLUBS),
    Card(Rank.KING, Suit.HEARTS),
    Card(Rank.TWO, Suit.SPADES),
)


class _StubRide:
    """The render seam's ride-like object (D15): the six read fields."""

    name = "WordPress Fragment Run 2026"
    event_date = date(2026, 6, 6)
    venue = "Test Venue"
    lap_km = 8.0
    organizer = "Test Org"
    scorer = "T. Ester"


def _entry(  # noqa: PLR0913 -- (plate, name, laps, kind) as the seam's own fields
    plate: str, name: str, laps: int, *, kind: str = "solo"
) -> EntryResult:
    """Build one finished-ride EntryResult for the render seam."""
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
        dnf=False,
    )


def _placed(place: int, result: EntryResult) -> Placed:
    """Place one finished entry: no tie note, no draw required."""
    return Placed(place=place, result=result, tie_note=None, draw_required=False)


def _solo_field() -> tuple[Placed, ...]:
    """Build two placed solo riders, ranked 1 and 2."""
    return (
        _placed(1, _entry("88", "Moss Ridge Riders", 11)),
        _placed(2, _entry("7", "Luca Ferrari", 10)),
    )


def _mixed_field() -> tuple[Placed, ...]:
    """Build a team and a solo rider, each ranked 1st in its kind."""
    return (
        _placed(1, _entry("501", "Team Alpha", 30, kind="team")),
        _placed(1, _entry("11", "Solo One", 20)),
    )


# The macros' own output is the comparable part of both renders: the
# standalone page wraps it in a document and the fragment wraps it in
# .rc-results, but the markup itself -- event header through footer --
# comes from the same macros and must be identical.
_MACRO_OUTPUT_START = '<header class="bp'
_MACRO_OUTPUT_END = "</footer>"


def _macro_output(markup: str) -> str:
    """Return the macros' markup: ``<header>`` through ``</footer>``.

    Every podium card, top list, leaderboard, field table and footer
    line lives in that slice, so comparing it between the two renderers
    is the "same content, different shell" claim -- independent of how
    either document wraps it.
    """
    start = markup.index(_MACRO_OUTPUT_START)
    end = markup.index(_MACRO_OUTPUT_END) + len(_MACRO_OUTPUT_END)
    return markup[start:end]


@pytest.fixture(scope="module")
def page() -> str:
    """Render the standalone results page for the solo field."""
    return render(_StubRide(), _solo_field(), _OPTIONS, generated=_GENERATED)


@pytest.fixture(scope="module")
def fragment() -> str:
    """Render the WordPress content fragment for the same solo field."""
    return render_wordpress(_StubRide(), _solo_field(), _OPTIONS, generated=_GENERATED)


@pytest.fixture(scope="module")
def poster_fragment() -> str:
    """Render the poster fragment for the same solo field."""
    return render_poster_wordpress(_StubRide(), _solo_field(), _OPTIONS, generated=_GENERATED)


# ------------------------------------------------- the fragment shell


@pytest.mark.parametrize("tag", ["!doctype", "html", "head", "body"])
def test_render_wordpress_emits_no_document_scaffolding(fragment: str, tag: str) -> None:
    """A WordPress Page supplies the document: this is inner HTML.

    Tags are matched with a boundary, so the fragment's own
    ``<header>`` element is not mistaken for a document ``<head>``.
    """
    assert re.search(rf"</?{tag}\b", fragment, re.IGNORECASE) is None


@pytest.mark.parametrize("token", ["<script", 'id="race-data"'])
def test_render_wordpress_emits_no_script_or_data_record(fragment: str, token: str) -> None:
    """No record and no script ship: nothing on the site reads them."""
    assert token not in fragment


def test_render_wordpress_wraps_the_content_in_the_scope_wrapper(fragment: str) -> None:
    """The wrapper class is what the scoped stylesheet matches."""
    assert fragment.startswith("<style>")
    assert fragment.count('<div class="rc-results"') == 1
    assert fragment.rstrip().endswith("</div>")


def test_render_wordpress_inlines_the_scoped_stylesheet_not_the_unscoped_one(
    fragment: str,
) -> None:
    """The fragment's CSS is compiled_css_wp, the fonts beside it.

    Inlining the unscoped artifact here would restyle the publishing
    site's theme, which is the whole point of the derived artifact.
    """
    assert "/* rivercrossing compiled_css_wp" in fragment
    assert "/* rivercrossing compiled_css —" not in fragment
    assert ".rc-results .mt-1{" in fragment
    assert 'font-family: "Barlow";' in fragment


# ----------------------------------------- the same content as the page


def test_render_wordpress_macro_markup_matches_the_standalone_page(
    page: str, fragment: str
) -> None:
    """Same ride, same payload, same markup -- a different shell."""
    assert _macro_output(fragment) == _macro_output(page)


def test_render_wordpress_macro_markup_matches_the_page_for_a_team_field() -> None:
    """The per-kind team sections render into the fragment too."""
    placed = _mixed_field()

    page = render(_StubRide(), placed, _OPTIONS, generated=_GENERATED)
    fragment = render_wordpress(_StubRide(), placed, _OPTIONS, generated=_GENERATED)

    assert _macro_output(fragment) == _macro_output(page)


@pytest.mark.parametrize("token", ["<section", "<table", "<tr", "<th", "<td", 'class="chip'])
def test_render_wordpress_element_counts_match_the_standalone_page(
    page: str, fragment: str, token: str
) -> None:
    """The page's tables, sections and chips, all present here too."""
    assert fragment.count(token) == page.count(token)


def test_render_wordpress_headings_match_the_standalone_page(page: str, fragment: str) -> None:
    """The section titles are the plan's, in the plan's order."""
    headings = re.findall(r"<h2[^>]*>(.*?)</h2>", fragment)

    assert headings == re.findall(r"<h2[^>]*>(.*?)</h2>", page)
    assert headings == ["Best hands — top 3", "Top ten", "Most laps", "Full field"]


def test_render_wordpress_given_a_rider_count_renders_it_in_the_header() -> None:
    """``riders`` is the header's fourth counter here too (Phase 7)."""
    fragment = render_wordpress(_StubRide(), _solo_field(), _OPTIONS, riders=207)

    assert "2 · 21 · 10 · 207" in fragment


def test_render_wordpress_omitted_rider_count_renders_zero_in_the_header(
    fragment: str,
) -> None:
    """A caller that threads no count renders 0, never a blank cell."""
    assert "2 · 21 · 10 · 0" in fragment


# --------------------------------------------------- the render seams


def test_render_wordpress_given_an_unverified_ride_carries_the_self_test_note() -> None:
    """E6.4.3: the caption reaches the fragment, not only the page."""
    fragment = render_wordpress(
        _StubRide(), _solo_field(), _OPTIONS, generated=_GENERATED, self_test_unverified=True
    )

    assert SELF_TEST_NOTE in fragment


def test_render_wordpress_given_a_verified_ride_carries_no_self_test_note(fragment: str) -> None:
    """The off-state renders no caption at all."""
    assert SELF_TEST_NOTE not in fragment


def test_render_wordpress_given_no_logo_embeds_the_transparent_png(fragment: str) -> None:
    """D8: the fragment's logo falls back to the transparent URI."""
    assert _TRANSPARENT_PNG in fragment
    assert 'src=""' not in fragment


def test_render_wordpress_logo_path_is_embedded_when_no_logo_src_is_given(
    tmp_path: Path,
) -> None:
    """``logo_path`` is the raw-file form of the same logo seam."""
    logo = tmp_path / "org-logo.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\nlogo-bytes")
    encoded = base64.b64encode(logo.read_bytes()).decode("ascii")

    fragment = render_wordpress(_StubRide(), _solo_field(), _OPTIONS, logo_path=logo)

    assert f"data:image/png;base64,{encoded}" in fragment


# --------------------- the bragging-rights disclaimer (E1)

# The disclaimer's fixed first sentence. The clause that follows names
# the boards the options show: the Laps leaderboard gates the lap-count
# subject exactly as show_times gates times, so with the Laps board off
# the header must not promise lap counts are "for bragging rights".
_DISCLAIMER = "It's not a race, it's a poker run — placings are by best poker hand."

# All four flag combinations. The clause is the whole second sentence;
# the empty string is the off/off page, whose sentence ends at the hand
# clause.
_DISCLAIMER_CASES: tuple[tuple[ExportOptions, str], ...] = (
    (
        ExportOptions(laps_board=True, show_times=True),
        " Lap counts and times below are unofficial and shown for bragging rights only.",
    ),
    (
        ExportOptions(laps_board=True, show_times=False),
        " Lap counts below are unofficial and shown for bragging rights only.",
    ),
    (
        ExportOptions(laps_board=False, show_times=True),
        " Times below are unofficial and shown for bragging rights only.",
    ),
    (ExportOptions(laps_board=False, show_times=False), ""),
)

_DISCLAIMER_IDS = ["laps+times", "laps", "times", "neither"]


@pytest.mark.parametrize(("options", "clause"), _DISCLAIMER_CASES, ids=_DISCLAIMER_IDS)
def test_render_disclaimer_names_only_the_boards_the_options_show(
    options: ExportOptions, clause: str
) -> None:
    """The subject list follows the shown boards, not a fixed line."""
    html = render(_StubRide(), _solo_field(), options, generated=_GENERATED)

    assert f"{_DISCLAIMER}{clause}</p>" in html


def test_render_disclaimer_given_no_board_shown_omits_the_clause() -> None:
    """With both boards off nothing is shown for bragging rights."""
    html = render(
        _StubRide(),
        _solo_field(),
        ExportOptions(laps_board=False, show_times=False),
        generated=_GENERATED,
    )

    assert "bragging rights" not in html
    assert "unofficial and shown for bragging rights" not in html


@pytest.mark.parametrize(("options", "clause"), _DISCLAIMER_CASES, ids=_DISCLAIMER_IDS)
def test_render_wordpress_disclaimer_names_only_the_boards_the_options_show(
    options: ExportOptions, clause: str
) -> None:
    """The fragment's disclaimer is the page's, gated the same way."""
    fragment = render_wordpress(_StubRide(), _solo_field(), options, generated=_GENERATED)

    assert f"{_DISCLAIMER}{clause}</p>" in fragment


def test_render_wordpress_disclaimer_given_no_board_shown_omits_the_clause() -> None:
    """The fragment drops the clause too: one source, no drift."""
    fragment = render_wordpress(
        _StubRide(),
        _solo_field(),
        ExportOptions(laps_board=False, show_times=False),
        generated=_GENERATED,
    )

    assert "bragging rights" not in fragment
    assert "unofficial and shown for bragging rights" not in fragment


# ------------------------------- the poster fragment (Part C)

# ``render_poster_wordpress`` is the podium poster's content fragment:
# the poster's own cards and header, in ``render_wordpress``'s shell.
# The header here is the poster's ``<header class="bp">``, not the
# results page's, so no assertion below leans on the event_header
# macro.


@pytest.mark.parametrize("tag", ["!doctype", "html", "head", "body"])
def test_render_poster_wordpress_emits_no_document_scaffolding(
    poster_fragment: str, tag: str
) -> None:
    """A WordPress Page supplies the document: this is inner HTML.

    Tags are matched with a boundary, so the fragment's own
    ``<header>`` element is not mistaken for a document ``<head>``.
    """
    assert re.search(rf"</?{tag}\b", poster_fragment, re.IGNORECASE) is None


def test_render_poster_wordpress_wraps_the_content_in_the_scope_wrapper(
    poster_fragment: str,
) -> None:
    """The wrapper class is what the scoped stylesheet matches."""
    assert poster_fragment.startswith("<style>")
    assert poster_fragment.count('<div class="rc-results"') == 1
    assert poster_fragment.rstrip().endswith("</div>")


def test_render_poster_wordpress_inlines_the_scoped_stylesheet_not_the_unscoped_one(
    poster_fragment: str,
) -> None:
    """The scoped stylesheet here, never the unscoped one."""
    assert "/* rivercrossing compiled_css_wp" in poster_fragment
    assert "/* rivercrossing compiled_css —" not in poster_fragment
    assert ".rc-results " in poster_fragment


def test_render_poster_wordpress_header_carries_the_unique_riders_counter() -> None:
    """The header's fourth counter is threaded here too (Phase 7)."""
    fragment = render_poster_wordpress(_StubRide(), _solo_field(), _OPTIONS, riders=207)

    assert "entries · laps · cards dealt · unique riders" in fragment
    assert "2 · 21 · 10 · 207" in fragment


def test_render_poster_wordpress_given_no_rider_count_renders_zero(
    poster_fragment: str,
) -> None:
    """A caller that threads no count renders 0, never a blank cell."""
    assert "2 · 21 · 10 · 0" in poster_fragment


def test_render_poster_wordpress_renders_podium_cards(poster_fragment: str) -> None:
    """The poster's cards render inside the fragment, chips and all."""
    assert "Best poker hands" in poster_fragment
    assert 'class="chip' in poster_fragment


def test_render_poster_wordpress_given_a_single_solo_entry_renders_one_card() -> None:
    """A one-entry field renders one card, not a section shell."""
    fragment = render_poster_wordpress(
        _StubRide(),
        (_placed(1, _entry("88", "Moss Ridge Riders", 11)),),
        _OPTIONS,
        generated=_GENERATED,
    )

    assert fragment.count('<div class="bp p-5') == 1


def test_render_poster_wordpress_given_a_non_numeric_plate_raises_value_error() -> None:
    """The fragment rejects a non-numeric plate, as the poster does."""
    placed = (_placed(1, _entry("ABC", "Bad Plate", 3)),)

    with pytest.raises(ValueError, match=re.escape("plate 'ABC' is not numeric")):
        render_poster_wordpress(_StubRide(), placed, _OPTIONS)


def test_render_poster_wordpress_given_a_team_field_titles_each_kinds_section() -> None:
    """A team field's two headings render, as the page's do."""
    fragment = render_poster_wordpress(_StubRide(), _mixed_field(), _OPTIONS, generated=_GENERATED)

    assert re.findall(r"<h3[^>]*>(.*?)</h3>", fragment) == ["Teams", "Solo riders"]


def test_render_poster_wordpress_given_an_empty_field_renders_no_section() -> None:
    """A field with no placed rows renders the header alone."""
    fragment = render_poster_wordpress(_StubRide(), (), _OPTIONS, generated=_GENERATED)

    assert "<section" not in fragment
    assert "Best poker hands" in fragment


def test_render_poster_wordpress_given_all_cards_prints_the_whole_hand_line() -> None:
    """``options.all_cards`` reaches the fragment's cards too."""
    options = ExportOptions(show_times=True, all_cards=True)

    fragment = render_poster_wordpress(_StubRide(), _solo_field(), options, generated=_GENERATED)

    assert "All 5 cards, in draw order: " in fragment


def test_render_poster_wordpress_given_a_drawn_card_renders_the_draw_badge() -> None:
    """R-14: a fragment card that drew shows the card it drew."""
    result = replace(
        _entry("88", "Moss Ridge Riders", 11), tiebreak_card=Card(Rank.ACE, Suit.HEARTS)
    )
    drawn = (_placed(1, result),)

    fragment = render_poster_wordpress(_StubRide(), drawn, _OPTIONS, generated=_GENERATED)

    assert 'draw <span class="chip r">A ♥</span>' in fragment


def test_render_poster_wordpress_given_an_unverified_ride_carries_the_self_test_note() -> None:
    """E6.4.3: the caption reaches the fragment too."""
    fragment = render_poster_wordpress(
        _StubRide(), _solo_field(), _OPTIONS, generated=_GENERATED, self_test_unverified=True
    )

    assert SELF_TEST_NOTE in fragment


def test_render_poster_wordpress_given_a_verified_ride_carries_no_self_test_note(
    poster_fragment: str,
) -> None:
    """The off-state renders no caption at all."""
    assert SELF_TEST_NOTE not in poster_fragment


def test_render_poster_wordpress_logo_path_is_embedded_when_no_logo_src_is_given(
    tmp_path: Path,
) -> None:
    """``logo_path`` is the raw-file form of the same logo seam."""
    logo = tmp_path / "org-logo.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\nlogo-bytes")
    encoded = base64.b64encode(logo.read_bytes()).decode("ascii")

    fragment = render_poster_wordpress(_StubRide(), _solo_field(), _OPTIONS, logo_path=logo)

    assert f"data:image/png;base64,{encoded}" in fragment
