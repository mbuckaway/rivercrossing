# SPDX-License-Identifier: GPL-3.0-only
r"""HTML results renderer tests (E6.2.2) -- tests first, per R-70.

Pins Spec section 8's render contract: ``_render_payload`` regenerates
the committed golden pages byte-for-byte from the fixture payloads
(TB-5), ``racejson`` escapes every ``</`` as ``<\\/`` (TB-6 injection),
the Environment runs StrictUndefined, the production page is
self-contained (zero external refs, vendored CSS/fonts inlined), the
embedded ``race-data`` record round-trips to ``payload.to_record()``,
and the public ``render(ride, placed, opts)`` builds a valid page from
the documented seam (D15): a ride-like object exposing
name/event_date/venue/lap_km/organizer/scorer plus a sequence of
``standings.Placed`` results.

Pins the shared results model too: ``format_generated`` is a pure
function of its instant (R-62), ``build_payload`` maps the ride seam
to the payload, and ``sections`` plans the per-kind page (3/3
podiums, 5/5 top lists and laps boards, solo top-10) from the payload
rows and the placed standings -- the template renders that plan, so
teams never show a plate and neither kind can crowd the other out.

The goldens at
``tests/unit/fixtures/htmlexport/epic-2026-results*.html`` (times,
no-times and the solo-only sample) were regenerated once by
``tools/gen_htmlexport_goldens.py`` from this renderer; regenerating
them is deliberate (Spec section 8 tests).
"""

import base64
import json
import re
import tempfile
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from pathlib import Path

import pytest
from htmlexport_fixtures import (
    GOLDEN_NO_TIMES,
    GOLDEN_SOLO,
    GOLDEN_TIMES,
    NO_TIMES_FIXTURE,
    SOLO_FIXTURE,
    TIMES_FIXTURE,
    load_race_payload,
    race_data_block,
)
from hypothesis import given
from hypothesis import strategies as st
from jinja2 import UndefinedError

from rivercrossing import htmlexport
from rivercrossing.cards import Card, Rank, Suit
from rivercrossing.hands import best_hand
from rivercrossing.htmlexport import (
    _TRANSPARENT_PNG,
    ExportOptions,
    LapsBoardRow,
    RacePayload,
    ResultRow,
    build_payload,
    format_generated,
    render,
    sections,
)
from rivercrossing.standings import EntryResult, Placed

_TIMES_PAYLOAD = load_race_payload(TIMES_FIXTURE)
_NO_TIMES_PAYLOAD = load_race_payload(NO_TIMES_FIXTURE)
_SOLO_PAYLOAD = load_race_payload(SOLO_FIXTURE)

_FIXTURE_GENERATED = _TIMES_PAYLOAD.event.generated

_FIVE_CARDS = (
    Card(Rank.NINE, Suit.SPADES),
    Card(Rank.NINE, Suit.DIAMONDS),
    Card(Rank.NINE, Suit.CLUBS),
    Card(Rank.KING, Suit.HEARTS),
    Card(Rank.TWO, Suit.SPADES),
)


def _sample_entry(  # noqa: PLR0913 -- (plate, name, laps, kind, dnf): the EntryResult's own fields
    plate: str,
    name: str,
    laps: int,
    *,
    kind: str = "solo",
    dnf: bool = False,
) -> EntryResult:
    """Build one finished-ride EntryResult for the render() seam tests.

    The same five-card hand stands in for every entry's draw.
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


def _placed_pair() -> tuple[Placed, Placed]:
    """Two placed entries: #88 with 11 laps and #7 with 10."""
    return (
        Placed(
            place=1,
            result=_sample_entry("88", "Moss Ridge Riders", 11),
            tie_note=None,
            draw_required=False,
        ),
        Placed(
            place=2,
            result=_sample_entry("7", "Luca Ferrari", 10),
            tie_note=None,
            draw_required=False,
        ),
    )


def _field(*, teams: int, solo: int) -> tuple[Placed, ...]:
    """Build a per-kind-ranked field of *teams* + *solo* entries.

    Teams take places 1..N first, then solo riders restart at 1 --
    the shape ``standings.rank_by_kind`` produces. Laps decrease down
    each kind's list, so entry #1 leads its kind's boards.
    """
    placed = [
        Placed(
            place=index + 1,
            result=_sample_entry(str(500 + index), f"Team {index + 1}", 30 - index, kind="team"),
            tie_note=None,
            draw_required=False,
        )
        for index in range(teams)
    ]
    placed.extend(
        Placed(
            place=index + 1,
            result=_sample_entry(str(10 + index), f"Solo {index + 1}", 20 - index),
            tie_note=None,
            draw_required=False,
        )
        for index in range(solo)
    )
    return tuple(placed)


def _dnf_field(count: int) -> tuple[Placed, ...]:
    """Build *count* placed entries, every one marked DNF.

    The boards list active entries only (``standings``' own rule), so
    an all-DNF field is the empty-board case with real rows behind it.
    """
    return tuple(
        Placed(
            place=index + 1,
            result=_sample_entry(str(10 + index), f"Solo {index + 1}", 9, dnf=True),
            tie_note=None,
            draw_required=False,
        )
        for index in range(count)
    )


class _StubRide:
    """The render() seam: a ride-like object with the five read fields.

    ``RideConfig`` satisfies the same Protocol structurally; the stub
    lets the render() tests avoid a heavy ride fixture (D15).
    """

    name = "Test Poker Run 2026"
    event_date = date(2026, 6, 6)
    venue = "Test Venue"
    lap_km = 8.0
    organizer = "Test Org"
    scorer = "T. Ester"


@pytest.fixture
def rendered_times() -> str:
    """Render the times-shown fixture payload for the tests."""
    return htmlexport._render_payload(
        _TIMES_PAYLOAD, dev=False, logo_src=_TRANSPARENT_PNG, generated=_FIXTURE_GENERATED
    )


@pytest.fixture
def rendered_no_times() -> str:
    """Render the times-hidden fixture payload for the tests."""
    return htmlexport._render_payload(
        _NO_TIMES_PAYLOAD, dev=False, logo_src=_TRANSPARENT_PNG, generated=_FIXTURE_GENERATED
    )


# ------------------------------------------------------------ goldens


def test_render_payload_times_matches_committed_golden_byte_for_byte() -> None:
    """The times-shown fixture payload regenerates the frozen golden."""
    rendered = htmlexport._render_payload(
        _TIMES_PAYLOAD, dev=False, logo_src=_TRANSPARENT_PNG, generated=_FIXTURE_GENERATED
    )

    assert rendered == GOLDEN_TIMES.read_text(encoding="utf-8")


def test_render_payload_no_times_matches_committed_golden_byte_for_byte() -> None:
    """The times-hidden payload regenerates the no-times golden."""
    rendered = htmlexport._render_payload(
        _NO_TIMES_PAYLOAD, dev=False, logo_src=_TRANSPARENT_PNG, generated=_FIXTURE_GENERATED
    )

    assert rendered == GOLDEN_NO_TIMES.read_text(encoding="utf-8")


def test_render_payload_solo_matches_committed_golden_byte_for_byte() -> None:
    """The solo-only fixture payload regenerates the solo golden."""
    rendered = htmlexport._render_payload(
        _SOLO_PAYLOAD,
        dev=False,
        logo_src=_TRANSPARENT_PNG,
        generated=_SOLO_PAYLOAD.event.generated,
    )

    assert rendered == GOLDEN_SOLO.read_text(encoding="utf-8")


def test_render_payload_solo_golden_renders_the_single_titles_only() -> None:
    """The solo golden keeps today's single titles, no team sections."""
    rendered = htmlexport._render_payload(
        _SOLO_PAYLOAD,
        dev=False,
        logo_src=_TRANSPARENT_PNG,
        generated=_SOLO_PAYLOAD.event.generated,
    )

    assert ">Best hands — top 3</h2>" in rendered
    assert ">Top ten</h2>" in rendered
    assert ">Most laps</h2>" in rendered
    assert ">Teams</h3>" not in rendered
    assert ">Top teams</h2>" not in rendered


def test_render_payload_generated_override_replaces_footer_and_embedded_record() -> None:
    """``generated`` overrides the footer text and the JSON record."""
    rendered = htmlexport._render_payload(
        _TIMES_PAYLOAD,
        dev=False,
        logo_src=_TRANSPARENT_PNG,
        generated="Generated 01:00, Jan 1 2026",
    )

    assert "Generated 01:00, Jan 1 2026" in rendered
    record = json.loads(race_data_block(rendered))
    assert record["event"]["generated"] == "Generated 01:00, Jan 1 2026"


def test_render_payload_full_field_renders_teams_then_solo_sections(
    rendered_times: str,
) -> None:
    """The Full field keeps its heading and splits into subsections."""
    assert ">Full field</h2>" in rendered_times
    assert ">Teams</h3>" in rendered_times
    assert ">Solo riders</h3>" in rendered_times
    assert rendered_times.find(">Teams</h3>") < rendered_times.find(">Solo riders</h3>")


def test_render_public_solo_only_field_omits_the_teams_section() -> None:
    """A solo-only field renders the solo subsection only."""
    html = render(_StubRide(), _placed_pair(), ExportOptions())

    assert ">Solo riders</h3>" in html
    assert ">Teams</h3>" not in html
    assert ">Top teams</h2>" not in html


def _placed_two_kinds() -> tuple[Placed, Placed]:
    """Return a team and a solo, each first in its own section."""
    return (
        Placed(
            place=1,
            result=_sample_entry("88", "Moss Ridge Riders", 11, kind="team"),
            tie_note=None,
            draw_required=False,
        ),
        Placed(
            place=1,
            result=_sample_entry("7", "Luca Ferrari", 10),
            tie_note=None,
            draw_required=False,
        ),
    )


def test_render_public_mixed_field_orders_full_field_teams_before_solo() -> None:
    """Teams-then-Solo input stays ordered in page and record."""
    html = render(_StubRide(), _placed_two_kinds(), ExportOptions(show_times=True))

    teams = html.find(">Teams</h3>")
    solo = html.find(">Solo riders</h3>")
    assert teams != -1
    assert solo != -1
    assert teams < solo
    record = json.loads(race_data_block(html))
    assert [row["type"] for row in record["results"]] == ["TEAM", "SOLO"]
    assert [row["place"] for row in record["results"]] == [1, 1]


def test_render_public_full_field_off_omits_the_section_labels() -> None:
    """full_field=False drops the Full field section and its headers."""
    html = render(_StubRide(), _placed_two_kinds(), ExportOptions(full_field=False))

    assert "Full field" not in html
    assert ">Teams</h3>" not in html
    assert ">Solo riders</h3>" not in html


# -------------------------------------------------- record round-trip


@pytest.mark.parametrize("fixture", [TIMES_FIXTURE, NO_TIMES_FIXTURE, SOLO_FIXTURE])
def test_payload_from_record_round_trips_fixture_record(fixture: Path) -> None:
    """Record -> RacePayload -> record is identity for every sample."""
    record = json.loads(fixture.read_text(encoding="utf-8"))

    assert htmlexport._payload_from_record(record).to_record() == record


def test_rendered_race_data_round_trips_to_payload_record(rendered_times: str) -> None:
    """The embedded JSON is value-identical with the payload record."""
    record = json.loads(race_data_block(rendered_times))

    assert record == _TIMES_PAYLOAD.to_record()


# --------------------------------------------------- injection


def test_racejson_escapes_script_closing_tag_in_team_name() -> None:
    r"""``</script>`` team names render ``<\\/script>`` in the block."""
    html = htmlexport._render_payload(
        replace_row(_TIMES_PAYLOAD, 0, entry="Moss </script> Riders"),
        dev=False,
        logo_src=_TRANSPARENT_PNG,
        generated=_FIXTURE_GENERATED,
    )

    assert "<\\/script>" in html
    block = race_data_block(html)
    assert "</script>" not in block
    assert json.loads(block)["results"][0]["entry"] == "Moss </script> Riders"


def replace_row(payload: RacePayload, index: int, *, entry: str) -> RacePayload:
    """Rebuild a fixture payload with one row's entry name replaced."""
    results = payload.results
    row = replace(results[index], entry=entry)
    return replace(payload, results=(*results[:index], row, *results[index + 1 :]))


@given(name=st.text(max_size=80))
def test_racejson_round_trips_any_team_name_through_the_block(name: str) -> None:
    """Any team name survives the escaped JSON round-trip."""
    payload = replace_row(_TIMES_PAYLOAD, 0, entry=name)

    record = json.loads(str(htmlexport.racejson(payload)))

    assert record["results"][0]["entry"] == name


# ----------------------------------------------------- StrictUndefined


def test_render_missing_context_key_raises_undefined_error() -> None:
    """StrictUndefined: dropping any context key fails the render."""
    context = htmlexport._template_context(
        _TIMES_PAYLOAD, dev=False, logo_src=_TRANSPARENT_PNG, generated=None
    )
    del context["sections"]

    with pytest.raises(UndefinedError, match=re.escape("'sections' is undefined")):
        htmlexport._make_environment().get_template("base.html.j2").render(**context)


# ---------------------------------------------------- offline


def test_render_production_page_has_zero_external_references(rendered_times: str) -> None:
    """Nothing the page loads comes from the network (works from file://).

    ``https://`` itself is not asserted blanket-absent: the frozen
    ``compiled_css`` (E6.2.1) ships Tailwind's MIT license comment
    naming tailwindcss.com -- metadata text, never fetched, the same
    convention as E6.2.1's own ``url(http`` check. What is asserted is
    that no *fetchable* reference survives: no script/link tags, no
    CDN shorthand, no CSS ``url(http``, no dev stand-ins.
    """
    assert "http://" not in rendered_times
    assert "//cdn" not in rendered_times
    assert "url(http" not in rendered_times
    assert "<script src=" not in rendered_times
    assert "<link" not in rendered_times
    assert "@tailwindcss/browser" not in rendered_times
    assert rendered_times.count("<script") == 1
    assert 'id="race-data"' in rendered_times


def test_render_production_page_inlines_vendored_css_and_fonts(rendered_times: str) -> None:
    """compiled_css and fonts_css ship inside the page's one <style>."""
    assert "generated by tools/gen_css.py" in rendered_times
    assert "@font-face" in rendered_times
    assert rendered_times.count("<style>") == 1


def test_render_dev_mode_emits_cdn_standins_and_still_embeds_race_data() -> None:
    """dev=True previews the sample: CDN stand-ins, valid JSON."""
    html = htmlexport._render_payload(
        _TIMES_PAYLOAD,
        dev=True,
        logo_src="https://example.com/logo.png",
        generated=_FIXTURE_GENERATED,
    )

    assert "@tailwindcss/browser" in html
    assert "https://fonts.googleapis.com" in html
    assert json.loads(race_data_block(html)) == _TIMES_PAYLOAD.to_record()


# ------------------------------------------------------ markup content


def test_laps_board_subtitle_formats_integral_lap_km_without_decimal(
    rendered_times: str,
) -> None:
    """D5: lap_km 8.0 renders "8 km per lap", never "8.0"."""
    assert "Unofficial — 8 km per lap." in rendered_times
    assert "8.0 km per lap" not in rendered_times


def test_render_payload_renders_row_type_best_lap_tie_and_dnf_markers(
    rendered_times: str,
) -> None:
    """Every macro attribute (r.type/best_lap/tie/dnf) renders."""
    assert "TEAM ×4" in rendered_times  # noqa: RUF001 -- the golden's own display spelling
    assert "27:59" in rendered_times
    assert "tie-break" in rendered_times
    assert ">dnf<" in rendered_times


def test_render_no_times_page_has_no_time_markup_or_time_fields(
    rendered_no_times: str,
) -> None:
    """R-63: times hidden means absent markup and absent JSON keys.

    ``t-col`` appears inside the vendored CSS as part of
    ``-webkit-tap-highlight-color``, so the markup check targets the
    time cells' `` t-col"`` class attribute instead.
    """
    assert ' t-col"' not in rendered_no_times
    assert "(no times)" in rendered_no_times
    record = json.loads(race_data_block(rendered_no_times))
    assert "total" not in record["results"][0]
    assert "bestLap" not in record["results"][0]
    assert record["timeBoard"] == []
    # "avg" as a bare string appears inside the base64 fonts_css data;
    # the no-times contract is that the JSON record carries no avg key.
    assert '"avg"' not in race_data_block(rendered_no_times)


def test_result_row_type_property_aliases_entry_type() -> None:
    """D6: templates read r.type; the property aliases entry_type."""
    row = ResultRow(
        place=1, plate=88, entry="X", entry_type="SOLO", laps=11, hand="High Card — Ace"
    )

    assert row.type == "SOLO"


# ------------------------------------------------------- public render


def test_render_public_builds_valid_page_from_minimal_fake_ride() -> None:
    """render() composes the payload from the documented seam."""
    html = render(_StubRide(), _placed_pair(), ExportOptions(show_times=True))

    assert "Test Poker Run 2026" in html
    assert "Saturday June 6, 2026 · Test Venue · 8 km loop" in html
    assert "Organizer: Test Org" in html
    assert "Scorer: T. Ester" in html
    assert "2 · 21 · 10" in html
    assert "#88 Moss Ridge Riders" in html
    record = json.loads(race_data_block(html))
    assert record["event"]["entries"] == 2
    assert record["results"][0]["plate"] == 88
    assert record["results"][0]["total"] == "5:32:00"


def test_render_public_defaults_generated_to_samples_style() -> None:
    """The footer matches the samples' style without injection."""
    html = render(_StubRide(), _placed_pair(), ExportOptions())

    matches = re.findall(r"Generated \d{2}:\d{2}, [A-Z][a-z]{2,4} \d{1,2} \d{4}", html)
    assert len(matches) == 2  # the footer text plus the embedded JSON record


def test_render_public_injected_generated_appears_in_footer() -> None:
    """The seam pins the timestamp for reproducible exports."""
    html = render(
        _StubRide(), _placed_pair(), ExportOptions(), generated="Generated 09:00, June 1 2026"
    )

    assert "Generated 09:00, June 1 2026" in html


def test_render_public_logo_falls_back_to_transparent_png_when_none() -> None:
    """D8: no logo falls back to the transparent data URI."""
    html = render(_StubRide(), _placed_pair(), ExportOptions())

    assert _TRANSPARENT_PNG in html
    assert 'src=""' not in html


def test_render_public_uses_given_logo_src_over_transparent_fallback() -> None:
    """A provided logo URI replaces the transparent fallback."""
    html = render(
        _StubRide(), _placed_pair(), ExportOptions(), logo_src="data:image/png;base64,CUSTOMLOGO"
    )

    assert "data:image/png;base64,CUSTOMLOGO" in html
    assert _TRANSPARENT_PNG not in html


def test_render_public_logo_alt_is_organizer_name() -> None:
    """The logo's alt text names the organizer (template contract)."""
    html = render(_StubRide(), _placed_pair(), ExportOptions())

    assert 'alt="Organizer: Test Org"' in html


def test_render_public_maps_joker_card_to_jk_pair() -> None:
    """A drawn joker embeds as the ["JK", "j"] card pair."""
    cards = (
        Card(rank=None, suit=None, joker=True),
        Card(Rank.NINE, Suit.SPADES),
        Card(Rank.NINE, Suit.DIAMONDS),
        Card(Rank.NINE, Suit.CLUBS),
        Card(Rank.KING, Suit.HEARTS),
    )
    result = EntryResult(
        entry_id="88",
        plate="88",
        name="Joker Squad",
        kind="team",
        laps=11,
        total_time=3600.0,
        best_lap=1800.0,
        cards=cards,
        hand=best_hand(cards),
        dnf=False,
    )
    placed = (Placed(place=1, result=result, tie_note=None, draw_required=False),)

    html = render(_StubRide(), placed, ExportOptions())

    record = json.loads(race_data_block(html))
    assert ["JK", "j"] in record["results"][0]["cards"]


def test_render_public_entry_with_no_cards_renders_empty_hand_label() -> None:
    """A no-show entry (zero cards) still renders, with no hand name."""
    result = EntryResult(
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
    )
    placed = (Placed(place=1, result=result, tie_note=None, draw_required=False),)

    html = render(_StubRide(), placed, ExportOptions(show_times=True))

    assert "No Show" in html
    assert "0:00" in html


def test_render_public_non_numeric_plate_raises_value_error() -> None:
    """render() emits integer plates; a non-numeric one is rejected."""
    result = _sample_entry("ABC", "Bad Plate", 3)
    placed = (Placed(place=1, result=result, tie_note=None, draw_required=False),)

    with pytest.raises(ValueError, match=re.escape("plate 'ABC' is not numeric")):
        render(_StubRide(), placed, ExportOptions())


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
    assert htmlexport._format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (8.0, 8),
        (8.5, 8.5),
        (0.0, 0),
        (10.25, 10.25),
        (180.0, 180),
    ],
)
def test_finalize_display_formats_integral_floats_as_ints(value: float, expected: object) -> None:
    """D5: integral floats display as ints, others as-is."""
    assert htmlexport._finalize_display(value) == expected


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
    rendered = htmlexport._format_duration(float(seconds))

    assert _parse_duration(rendered) == seconds


@given(value=st.integers(min_value=-1_000_000, max_value=1_000_000))
def test_finalize_display_converts_integral_floats_to_ints(value: int) -> None:
    """Property: an integral float renders as its int."""
    rendered = htmlexport._finalize_display(float(value))

    assert isinstance(rendered, int)
    assert rendered == value


@given(value=st.floats(allow_nan=False, allow_infinity=False).filter(lambda v: not v.is_integer()))
def test_finalize_display_passes_through_non_integral_floats(value: float) -> None:
    """Property: a non-integral float renders unchanged."""
    rendered = htmlexport._finalize_display(value)

    assert isinstance(rendered, float)
    assert rendered == value


# ---------------------------------- render() boards + logo (E6.4.2)


def test_render_public_laps_board_populated_when_option_on() -> None:
    """The laps_board option renders the Most-laps section + record."""
    html = render(_StubRide(), _placed_pair(), ExportOptions(laps_board=True))

    assert "Most laps" in html
    record = json.loads(race_data_block(html))
    assert record["lapsBoard"] != []


def test_render_public_time_board_populated_when_option_on() -> None:
    """The time_board option renders the Fastest section + rows."""
    html = render(_StubRide(), _placed_pair(), ExportOptions(show_times=True, time_board=True))

    assert "Fastest" in html
    record = json.loads(race_data_block(html))
    assert record["timeBoard"] != []
    assert "avg" in record["timeBoard"][0]


def test_render_public_boards_empty_when_options_off() -> None:
    """Boards absent from markup and record when both flags are off."""
    html = render(_StubRide(), _placed_pair(), ExportOptions(laps_board=False, time_board=False))

    record = json.loads(race_data_block(html))
    assert record["lapsBoard"] == []
    assert record["timeBoard"] == []


@pytest.mark.parametrize("entries", [0, 1, 2])
def test_render_public_time_board_with_no_active_rows_omits_the_fastest_section(
    entries: int,
) -> None:
    """Mirror the PDF's early return: no board rows, no section.

    A field with no active entries -- empty, or every entry DNF, which
    the boards never list -- has an empty ``sections.time_board``, and
    ``pdfexport._time_board`` returns before its heading in exactly
    that case. The HTML must not emit the heading, the note or a
    zero-row table either. The laps boards already guard on their own
    row lists, so they stay as they are.
    """
    opts = ExportOptions(show_times=True, time_board=True)

    html = render(_StubRide(), _dnf_field(entries), opts)

    assert "Fastest" not in html
    assert "Most laps" not in html


def test_render_public_logo_path_embeds_base64_data_uri() -> None:
    """logo_path bytes become the data URI (R-61 logo base64)."""
    logo = base64.b64decode(_TRANSPARENT_PNG.split(",", 1)[1])
    with tempfile.TemporaryDirectory() as tmp_dir:
        logo_file = Path(tmp_dir) / "logo.png"
        logo_file.write_bytes(logo)
        html = render(_StubRide(), _placed_pair(), ExportOptions(), logo_path=logo_file)

    assert "data:image/png;base64," in html


# ============================================================ W8
# Team logos on the results page: ResultRow carries an optional logo
# (a data URI, mirroring the org-logo mechanism), the record emits it
# sparsely, and the standings/full-field rows render a small image --
# nothing when the row has no logo.


_TEAM_LOGO_URI = "data:image/png;base64,TEAMLOGO"


def _team_and_solo_pair() -> tuple[Placed, Placed]:
    """Return a placed team (#88) and a placed solo (#7)."""
    return (
        Placed(
            place=1,
            result=_sample_entry("88", "Moss Ridge Riders", 11, kind="team"),
            tie_note=None,
            draw_required=False,
        ),
        Placed(
            place=2,
            result=_sample_entry("7", "Luca Ferrari", 10),
            tie_note=None,
            draw_required=False,
        ),
    )


def test_result_row_to_record_emits_the_logo_only_when_set() -> None:
    """Logo is a sparse record key, like tie/dnf."""
    with_logo = ResultRow(
        place=1,
        plate=88,
        entry="Moss Ridge Riders",
        entry_type="TEAM",
        laps=11,
        hand="Four of a Kind",
        logo=_TEAM_LOGO_URI,
    )
    without_logo = ResultRow(
        place=2,
        plate=7,
        entry="Luca Ferrari",
        entry_type="SOLO",
        laps=10,
        hand="Pair",
    )

    assert with_logo.to_record(show_times=False)["logo"] == _TEAM_LOGO_URI
    assert "logo" not in without_logo.to_record(show_times=False)


def test_render_public_embeds_team_logos_in_the_team_rows_only() -> None:
    """A supplied team_logos map renders the small image per team row.

    The mapping is keyed by the placed entry's plate, the same seam
    the org logo uses; the row appears once in the Top teams table and
    once in the Full field Teams table, so exactly two images render
    for the one logo-carrying team -- and none for the solo rows
    beside it or when the map is absent.
    """
    placed = _team_and_solo_pair()
    html = render(_StubRide(), placed, ExportOptions(), team_logos={"88": _TEAM_LOGO_URI})

    assert html.count('class="team-logo ') == 2
    # The URI appears in the two images plus the embedded race-data
    # record once -- the image markup is what this pins.
    assert html.count('<img src="data:image/png;base64,TEAMLOGO"') == 2

    plain = render(_StubRide(), placed, ExportOptions())

    assert 'class="team-logo ' not in plain


def test_render_public_team_logo_round_trips_through_the_embedded_record() -> None:
    """The race-data row keeps the logo through a parse round-trip."""
    placed = _team_and_solo_pair()
    html = render(_StubRide(), placed, ExportOptions(), team_logos={"88": _TEAM_LOGO_URI})

    record = json.loads(race_data_block(html))
    (team_row,) = [row for row in record["results"] if row["plate"] == 88]

    assert team_row["logo"] == _TEAM_LOGO_URI


# ============================================================ E7
# Sex on the results page: ResultRow carries a solo rider's "M"/"F"
# (None for a team, which has no single sex), the record emits it
# sparsely like tie/dnf/logo, and the podium card and full-field row
# render it as a small muted suffix on solo rows only.


@pytest.mark.parametrize("sex", ["M", "F"])
def test_result_row_to_record_emits_the_sex_letter_when_set(sex: str) -> None:
    """A solo row's "M"/"F" becomes the record's ``sex`` value."""
    row = ResultRow(
        place=2, plate=7, entry="Luca Ferrari", entry_type="SOLO", laps=10, hand="Pair", sex=sex
    )

    record = row.to_record(show_times=False)

    assert record["sex"] == sex


def test_result_row_to_record_omits_sex_when_unset() -> None:
    """A team row carries no ``sex`` key at all, never a null."""
    row = ResultRow(
        place=1, plate=88, entry="Moss Ridge Riders", entry_type="TEAM", laps=11, hand="Pair"
    )

    record = row.to_record(show_times=False)

    assert "sex" not in record


def test_render_public_marks_the_solo_rows_sex_and_leaves_the_team_row_blank() -> None:
    """Mixed field: markup and record carry sex on the solo row only."""
    placed = (
        Placed(
            place=1,
            result=_sample_entry("88", "Moss Ridge Riders", 11, kind="team"),
            tie_note=None,
            draw_required=False,
        ),
        Placed(
            place=2,
            result=replace(_sample_entry("7", "Luca Ferrari", 10), sex="F"),
            tie_note=None,
            draw_required=False,
        ),
    )

    html = render(_StubRide(), placed, ExportOptions())

    record = json.loads(race_data_block(html))
    assert [(row["type"], row.get("sex")) for row in record["results"]] == [
        ("TEAM", None),
        ("SOLO", "F"),
    ]
    assert html.count(" · F</span>") == 2  # the podium card and the full-field row


def test_render_payload_golden_renders_both_sex_letters(rendered_times: str) -> None:
    """The frozen golden carries a sex marker for M and F solo rows."""
    assert " · M</span>" in rendered_times
    assert " · F</span>" in rendered_times


# ======================================================== shared model
# One code path builds the results model and its section plan: the
# payload (build_payload), the stamp (format_generated) and the page's
# per-kind sections (sections). Both exporters render from this plan.


def test_htmlexport_exposes_the_shared_results_model_api() -> None:
    """The public model ships in __all__; the private name is gone."""
    assert {"build_payload", "format_generated", "sections"} <= set(htmlexport.__all__)
    assert "Sections" in htmlexport.__all__
    assert not hasattr(htmlexport, "_payload_from_ride")


def test_build_payload_maps_ride_placed_and_stamp_to_the_record() -> None:
    """build_payload is render()'s model: the same payload, publicly."""
    payload = build_payload(_StubRide(), _placed_pair(), ExportOptions(), _FIXTURE_GENERATED)

    assert payload.event.title == "Test Poker Run 2026"
    assert payload.event.meta == "Saturday June 6, 2026 · Test Venue · 8 km loop"
    assert payload.event.generated == _FIXTURE_GENERATED
    assert payload.event.entries == 2
    assert payload.event.laps == 21
    assert [row.entry for row in payload.results] == ["Moss Ridge Riders", "Luca Ferrari"]


def test_build_payload_applies_the_team_logo_map_by_plate() -> None:
    """A team_logos mapping keys the team's row; solo rows stay bare."""
    payload = build_payload(
        _StubRide(),
        _team_and_solo_pair(),
        ExportOptions(),
        _FIXTURE_GENERATED,
        team_logos={"88": _TEAM_LOGO_URI},
    )

    assert [row.logo for row in payload.results] == [_TEAM_LOGO_URI, None]


def test_build_payload_defaults_the_stamp_to_the_current_local_time() -> None:
    """generated=None stamps the local wall clock, samples style."""
    payload = build_payload(_StubRide(), _placed_pair(), ExportOptions(), None)

    matches = re.findall(
        r"Generated \d{2}:\d{2}, [A-Z][a-z]{2,4} \d{1,2} \d{4}", payload.event.generated
    )
    assert matches == [payload.event.generated]


def test_build_payload_laps_board_lists_teams_then_solo_with_types() -> None:
    """The record's laps board is per kind: five teams then solo."""
    placed = _field(teams=6, solo=6)

    payload = build_payload(_StubRide(), placed, ExportOptions(), _FIXTURE_GENERATED)

    assert [row.type for row in payload.laps_board] == ["TEAM"] * 5 + ["SOLO"] * 5
    assert [row.entry for row in payload.laps_board] == [
        "Team 1",
        "Team 2",
        "Team 3",
        "Team 4",
        "Team 5",
        "Solo 1",
        "Solo 2",
        "Solo 3",
        "Solo 4",
        "Solo 5",
    ]


def test_build_payload_solo_event_laps_board_keeps_the_top_ten() -> None:
    """A solo ride's board stays one ten-row list, all "SOLO"."""
    placed = _field(teams=0, solo=11)

    payload = build_payload(_StubRide(), placed, ExportOptions(), _FIXTURE_GENERATED)

    assert len(payload.laps_board) == 10
    assert {row.type for row in payload.laps_board} == {"SOLO"}
    assert payload.laps_board[0].entry == "Solo 1"
    assert payload.laps_board[-1].entry == "Solo 10"


def test_build_payload_time_board_stays_flat_across_kinds() -> None:
    """The time board keeps spec 6's single laps-then-time top ten."""
    placed = _field(teams=6, solo=6)

    payload = build_payload(
        _StubRide(), placed, ExportOptions(show_times=True, time_board=True), _FIXTURE_GENERATED
    )

    assert [row.entry for row in payload.time_board] == [
        "Team 1",
        "Team 2",
        "Team 3",
        "Team 4",
        "Team 5",
        "Team 6",
        "Solo 1",
        "Solo 2",
        "Solo 3",
        "Solo 4",
    ]


# ============================================= format_generated (R-62)


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        # Month boundaries: January and December (the table's ends).
        (datetime(2026, 1, 1, 0, 0, tzinfo=UTC), "Generated 00:00, Jan 1 2026"),
        (datetime(2026, 12, 31, 23, 59, tzinfo=UTC), "Generated 23:59, Dec 31 2026"),
        # The samples' four-letter "Sept", never strftime's "Sep".
        (datetime(2026, 9, 20, 16, 7, tzinfo=UTC), "Generated 16:07, Sept 20 2026"),
        # The instant's own offset is used, never converted (R-62).
        (
            datetime(2026, 9, 20, 16, 7, tzinfo=timezone(timedelta(hours=5, minutes=30))),
            "Generated 16:07, Sept 20 2026",
        ),
        (
            datetime(2026, 6, 6, 9, 5, tzinfo=timezone(timedelta(hours=-7))),
            "Generated 09:05, Jun 6 2026",
        ),
    ],
)
def test_format_generated_renders_the_instant_in_its_own_offset(
    at: datetime, expected: str
) -> None:
    """The stamp is a pure function of the instant's own wall clock."""
    assert format_generated(at) == expected


def test_format_generated_ignores_the_offset_for_equal_wall_clock_fields() -> None:
    """Two offsets, one wall clock: identical stamps (no conversion)."""
    wall = datetime(2026, 9, 20, 16, 7, tzinfo=UTC)

    west = format_generated(wall.replace(tzinfo=timezone(timedelta(hours=-7))))
    east = format_generated(wall.replace(tzinfo=timezone(timedelta(hours=13))))

    assert west == east == "Generated 16:07, Sept 20 2026"


_MONTH_NAMES = (
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


@given(
    dt=st.datetimes(),
    tz=st.timezones(),
)
def test_format_generated_decodes_back_to_the_instant_own_fields(dt: datetime, tz: tzinfo) -> None:
    """Property: every stamp field is the instant's own, unconverted."""
    rendered = format_generated(dt.replace(tzinfo=tz))

    expected = (
        f"Generated {dt.hour:02d}:{dt.minute:02d}, {_MONTH_NAMES[dt.month - 1]} {dt.day} {dt.year}"
    )
    assert rendered == expected


# ============================================================= sections
# The section plan: per-kind podiums (3/3 or 3), top lists (5/5 or
# 10) and laps boards (5/5 or 10) built from `placed`, so one kind can
# never crowd the other out of the record's combined top ten; the
# time board stays flat and the full-field partitions stay uncapped.


def test_sections_mixed_field_plans_per_kind_podiums_and_top_fives() -> None:
    """A team event: 3/3 podiums and 5/5 top lists, per kind."""
    placed = _field(teams=6, solo=6)
    payload = build_payload(_StubRide(), placed, ExportOptions(), _FIXTURE_GENERATED)

    plan = sections(payload, placed)

    assert [row.entry for row in plan.podium_teams] == ["Team 1", "Team 2", "Team 3"]
    assert [row.entry for row in plan.podium_solo] == ["Solo 1", "Solo 2", "Solo 3"]
    assert [row.entry for row in plan.top_teams] == [
        "Team 1",
        "Team 2",
        "Team 3",
        "Team 4",
        "Team 5",
    ]
    assert [row.entry for row in plan.top_solo] == [
        "Solo 1",
        "Solo 2",
        "Solo 3",
        "Solo 4",
        "Solo 5",
    ]


def test_sections_mixed_field_keeps_every_row_in_the_full_field_lists() -> None:
    """The full-field partitions are uncapped: 7 teams, 7 solo."""
    placed = _field(teams=7, solo=7)
    payload = build_payload(_StubRide(), placed, ExportOptions(), _FIXTURE_GENERATED)

    plan = sections(payload, placed)

    assert len(plan.teams) == 7
    assert len(plan.solo) == 7
    assert {row.entry_type for row in plan.teams} == {"TEAM"}
    assert {row.entry_type for row in plan.solo} == {"SOLO"}


def test_sections_laps_boards_are_per_kind_not_a_crowded_combined_top_ten() -> None:
    """Eight teams out-lap every solo; both boards still fill to five.

    A filter of a combined top ten would leave the solo board with
    two rows -- the crowding this per-kind computation prevents.
    """
    placed = _field(teams=8, solo=6)
    payload = replace(
        build_payload(_StubRide(), placed, ExportOptions(), _FIXTURE_GENERATED),
        laps_board=(),
    )

    plan = sections(payload, placed)

    assert [row.entry for row in plan.laps_teams] == [
        "Team 1",
        "Team 2",
        "Team 3",
        "Team 4",
        "Team 5",
    ]
    assert [row.entry for row in plan.laps_solo] == [
        "Solo 1",
        "Solo 2",
        "Solo 3",
        "Solo 4",
        "Solo 5",
    ]
    assert plan.laps_teams[0].type == "TEAM"
    assert plan.laps_solo[0].type == "SOLO"


@pytest.mark.parametrize(
    ("teams", "solo", "expected_rows"),
    [
        (1, 1, (1, 1)),  # one row per kind
        (3, 3, (3, 3)),  # exactly the podium
        (4, 4, (4, 4)),  # podium + 1
        (5, 5, (5, 5)),  # exactly the cap
        (6, 6, (5, 5)),  # cap + 1
        (11, 9, (5, 5)),  # many
    ],
)
def test_sections_mixed_field_caps_each_top_list_at_five(
    teams: int, solo: int, expected_rows: tuple[int, int]
) -> None:
    """Both top lists cap at five across the slice boundaries."""
    placed = _field(teams=teams, solo=solo)
    payload = build_payload(_StubRide(), placed, ExportOptions(), _FIXTURE_GENERATED)

    plan = sections(payload, placed)

    assert (len(plan.top_teams), len(plan.top_solo)) == expected_rows


@pytest.mark.parametrize(
    ("solo", "expected_rows"),
    [(0, 0), (1, 1), (9, 9), (10, 10), (11, 10)],
)
def test_sections_solo_event_caps_the_top_list_at_ten(solo: int, expected_rows: int) -> None:
    """A solo ride keeps today's top ten, and only the solo lists."""
    placed = _field(teams=0, solo=solo)
    payload = build_payload(_StubRide(), placed, ExportOptions(), _FIXTURE_GENERATED)

    plan = sections(payload, placed)

    assert len(plan.top_solo) == expected_rows
    assert (plan.podium_teams, plan.top_teams, plan.laps_teams) == ((), (), ())


def test_sections_solo_event_keeps_the_podium_at_three_and_board_at_ten() -> None:
    """Solo event: podium 3, laps board 10, all solo."""
    placed = _field(teams=0, solo=11)
    payload = build_payload(_StubRide(), placed, ExportOptions(), _FIXTURE_GENERATED)

    plan = sections(payload, placed)

    assert [row.entry for row in plan.podium_solo] == ["Solo 1", "Solo 2", "Solo 3"]
    assert len(plan.laps_solo) == 10
    assert plan.laps_solo[0].entry == "Solo 1"


def test_sections_empty_field_plans_every_section_empty() -> None:
    """No entries: every section is empty, nothing raises."""
    payload = build_payload(_StubRide(), (), ExportOptions(), _FIXTURE_GENERATED)

    plan = sections(payload, ())

    empty = ((), (), (), (), (), (), (), (), ())
    assert (
        plan.podium_teams,
        plan.podium_solo,
        plan.top_teams,
        plan.top_solo,
        plan.laps_teams,
        plan.laps_solo,
        plan.time_board,
        plan.teams,
        plan.solo,
    ) == empty


def test_sections_treats_display_suffixed_team_rows_as_a_team_event() -> None:
    """The golden samples' suffixed TEAM rows select the team layout."""
    plan = sections(_TIMES_PAYLOAD, ())

    assert [row.entry for row in plan.podium_teams] == [
        "Moss Ridge Riders",
        "Dirt Dynamos",
        "Fat Tire Four",
    ]
    assert (plan.laps_teams, plan.laps_solo) == ((), ())
    assert plan.time_board == _TIMES_PAYLOAD.time_board


def test_sections_laps_boards_are_empty_when_the_option_is_off() -> None:
    """laps_board=False ships the boards off however many race."""
    placed = _field(teams=6, solo=6)
    payload = build_payload(
        _StubRide(), placed, ExportOptions(laps_board=False), _FIXTURE_GENERATED
    )

    plan = sections(payload, placed)

    assert (plan.laps_teams, plan.laps_solo) == ((), ())


def test_sections_flattens_the_time_board_from_the_payload() -> None:
    """time_board passes through unchanged: one flat, both-kind list."""
    placed = _field(teams=6, solo=6)
    payload = build_payload(
        _StubRide(),
        placed,
        ExportOptions(show_times=True, time_board=True),
        _FIXTURE_GENERATED,
    )

    plan = sections(payload, placed)

    assert plan.time_board == payload.time_board
    assert plan.time_board[0].entry == "Team 1"


@given(
    team_count=st.integers(min_value=0, max_value=8),
    solo_count=st.integers(min_value=0, max_value=12),
)
def test_sections_never_exceeds_the_per_kind_caps(team_count: int, solo_count: int) -> None:
    """Property: list lengths stay at or under their per-kind caps."""
    placed = _field(teams=team_count, solo=solo_count)
    payload = build_payload(_StubRide(), placed, ExportOptions(), _FIXTURE_GENERATED)

    plan = sections(payload, placed)

    assert len(plan.teams) == team_count
    assert len(plan.solo) == solo_count
    assert len(plan.top_teams) <= 5
    assert len(plan.podium_solo) <= 3
    assert len(plan.laps_solo) <= (5 if team_count else 10)


# ================================================== template (the plan)
# The page renders the planned sections: per-kind titles, per-kind
# tables (teams never show a plate), per-kind drawn-row colspans, and
# the tie note under each top-list table whose rows hold a tie.


def _headers(table: str) -> list[str]:
    """Return one table's ``th`` texts in document order."""
    return re.findall(r"<th[^>]*>([^<]+)</th>", table)


def _table_after(html: str, marker: str) -> str:
    """Return the markup from *marker* to its closing ``</table>``."""
    start = html.index(marker)
    return html[start : html.index("</table>", start)]


def _section_after(html: str, marker: str) -> str:
    """Return the markup from *marker* to its closing ``</section>``."""
    start = html.index(marker)
    return html[start : html.index("</section>", start)]


def _plate_cells(section: str) -> list[str]:
    """Return a board's plate-cell texts, one per row in document order.

    The plate cell is the ``w-12`` one; the ``w-8`` cell holds the
    row's enumerated place, so the two never collide.
    """
    return re.findall(r'<td class="py-2 pr-3 font-bold w-12">([^<]*)</td>', section)


def test_render_public_mixed_event_renders_the_per_kind_titles() -> None:
    """All six mixed titles appear; no solo-event title slips in."""
    html = render(_StubRide(), _field(teams=2, solo=2), ExportOptions())

    assert ">Best hands — teams</h2>" in html
    assert ">Best hands — solo riders</h2>" in html
    assert ">Top teams</h2>" in html
    assert ">Top solo riders</h2>" in html
    assert ">Most laps — teams</h2>" in html
    assert ">Most laps — solo riders</h2>" in html
    assert ">Best hands — top 3</h2>" not in html
    assert ">Top ten</h2>" not in html
    assert ">Most laps</h2>" not in html


def test_render_public_mixed_event_orders_the_team_sections_before_solo() -> None:
    """Mixed event: the team podium, list and board come first."""
    html = render(_StubRide(), _field(teams=2, solo=2), ExportOptions())

    assert html.index(">Best hands — teams</h2>") < html.index(">Best hands — solo riders</h2>")
    assert html.index(">Top teams</h2>") < html.index(">Top solo riders</h2>")
    assert html.index(">Most laps — teams</h2>") < html.index(">Most laps — solo riders</h2>")


def test_render_public_solo_event_renders_the_single_titles() -> None:
    """Solo event: the single podium/top/board titles, no team ones."""
    html = render(_StubRide(), _placed_pair(), ExportOptions())

    assert ">Best hands — top 3</h2>" in html
    assert ">Top ten</h2>" in html
    assert ">Most laps</h2>" in html
    assert ">Best hands — teams</h2>" not in html
    assert ">Top teams</h2>" not in html
    assert ">Most laps — teams</h2>" not in html


def test_render_public_mixed_event_team_podium_cards_drop_the_plate() -> None:
    """Team podium cards show no plate; solo cards keep theirs."""
    html = render(_StubRide(), _field(teams=1, solo=1), ExportOptions())

    team_podium = _section_after(html, ">Best hands — teams</h2>")
    solo_podium = _section_after(html, ">Best hands — solo riders</h2>")

    assert ">Team 1</div>" in team_podium
    assert "#500" not in team_podium
    assert "#10 Solo 1" in solo_podium


def test_render_public_mixed_event_top_tables_show_per_kind_columns() -> None:
    """Top teams has no Plate/Type; Top solo riders keeps both."""
    html = render(_StubRide(), _field(teams=2, solo=2), ExportOptions())

    assert _headers(_table_after(html, ">Top teams</h2>")) == [
        "Place",
        "Entry",
        "Laps",
        "Best 5 cards",
        "Hand",
    ]
    assert _headers(_table_after(html, ">Top solo riders</h2>")) == [
        "Place",
        "Plate",
        "Entry",
        "Laps",
        "Best 5 cards",
        "Hand",
    ]


def test_render_public_mixed_event_top_tables_add_the_time_column() -> None:
    """show_times inserts Total time after Laps in both top tables."""
    html = render(_StubRide(), _field(teams=2, solo=2), ExportOptions(show_times=True))

    assert _headers(_table_after(html, ">Top teams</h2>")) == [
        "Place",
        "Entry",
        "Laps",
        "Total time",
        "Best 5 cards",
        "Hand",
    ]
    assert _headers(_table_after(html, ">Top solo riders</h2>")) == [
        "Place",
        "Plate",
        "Entry",
        "Laps",
        "Total time",
        "Best 5 cards",
        "Hand",
    ]


def test_render_public_mixed_event_top_tables_carry_five_rows_per_kind() -> None:
    """The planned 5/5 lists render in full, not one shared top ten."""
    html = render(_StubRide(), _field(teams=6, solo=6), ExportOptions(all_cards=False))

    assert _table_after(html, ">Top teams</h2>").count("border-b border-ink/8") == 5
    assert _table_after(html, ">Top solo riders</h2>").count("border-b border-ink/8") == 5


def test_render_public_mixed_event_laps_boards_split_per_kind() -> None:
    """Most laps — teams drops the #plate cell; solo riders keeps it."""
    html = render(_StubRide(), _field(teams=2, solo=2), ExportOptions())

    team_board = _section_after(html, ">Most laps — teams</h2>")
    solo_board = _section_after(html, ">Most laps — solo riders</h2>")

    assert ">Team 1</td>" in team_board
    assert "#500" not in team_board
    assert "#10" in solo_board


def test_render_public_mixed_event_time_board_blanks_the_team_plates() -> None:
    """The flat Fastest board blanks team plates, exactly like the PDF.

    ``pdfexport._time_row`` is called with ``show_plate=row.plate not in
    {row.plate for row in plan.teams}``, so the two exports mirror: the
    team's row keeps its place and entry beside an empty plate cell,
    and the solo rows on the same board keep ``#plate``. The team row
    is on the board at all -- the board is one flat list across kinds.
    """
    opts = ExportOptions(show_times=True, time_board=True)

    html = render(_StubRide(), _field(teams=1, solo=1), opts)

    fastest = _section_after(html, ">Fastest — laps then time</h2>")

    assert ">Team 1</td>" in fastest
    assert ">#500</td>" not in fastest
    assert ">#10</td>" in fastest


@given(teams=st.integers(min_value=1, max_value=3), solo=st.integers(min_value=1, max_value=3))
def test_render_public_time_board_plate_cells_follow_the_team_plates_set(
    teams: int, solo: int
) -> None:
    """Property: a Fastest cell shows a plate iff its row is not a team.

    For every field shape the blank-or-keep decision is the plan's own
    ``team_plates`` set -- the exact expression ``pdfexport`` hands
    ``_time_row`` -- and the board keeps one row per planned board row,
    in plan order.
    """
    placed = _field(teams=teams, solo=solo)
    opts = ExportOptions(show_times=True, time_board=True)
    plan = sections(build_payload(_StubRide(), placed, opts, _FIXTURE_GENERATED), placed)

    html = render(_StubRide(), placed, opts)

    fastest = _section_after(html, ">Fastest — laps then time</h2>")

    assert _plate_cells(fastest) == [
        f"#{row.plate}" if row.plate not in plan.team_plates else "" for row in plan.time_board
    ]


def test_render_payload_times_golden_blanks_the_team_plate_on_the_fastest_board(
    rendered_times: str,
) -> None:
    """The committed times golden keeps only solo plates on the board.

    The golden payload's board is teams #88/#127 plus solo #7/#61/#102,
    so it is the fixture-scale version of the PDF-side pin (whose team
    plates are 600+ and solo plates 500+).
    """
    fastest = _section_after(rendered_times, ">Fastest — laps then time</h2>")

    assert ">Moss Ridge Riders</td>" in fastest
    assert ">#88</td>" not in fastest
    assert ">#127</td>" not in fastest
    assert ">#7</td>" in fastest


def test_render_public_mixed_event_full_field_keeps_umbrella_and_subsections() -> None:
    """Full field keeps its h2; the h3 subsections follow in order."""
    html = render(_StubRide(), _field(teams=2, solo=2), ExportOptions())

    full_field = _section_after(html, ">Full field</h2>")

    assert ">Teams</h3>" in full_field
    assert ">Solo riders</h3>" in full_field
    assert full_field.index(">Teams</h3>") < full_field.index(">Solo riders</h3>")


def test_render_public_mixed_event_full_field_teams_table_drops_plate_and_type() -> None:
    """Teams: Place | Entry | Laps | Cards | Hand, no Plate/Type."""
    html = render(_StubRide(), _field(teams=2, solo=2), ExportOptions())

    assert _headers(_table_after(html, ">Teams</h3>")) == [
        "Place",
        "Entry",
        "Laps",
        "Cards",
        "Hand",
    ]


def test_render_public_mixed_event_full_field_solo_table_keeps_its_columns() -> None:
    """Solo riders: the current solo columns, with Plate and Type."""
    html = render(_StubRide(), _field(teams=2, solo=2), ExportOptions())

    assert _headers(_table_after(html, ">Solo riders</h3>")) == [
        "Place",
        "Plate",
        "Entry",
        "Type",
        "Laps",
        "Best hand",
    ]


def test_render_public_mixed_event_full_field_headers_add_times_when_shown() -> None:
    """show_times adds Total time and Best lap to both tables."""
    html = render(_StubRide(), _field(teams=2, solo=2), ExportOptions(show_times=True))

    assert _headers(_table_after(html, ">Teams</h3>")) == [
        "Place",
        "Entry",
        "Laps",
        "Total time",
        "Best lap",
        "Cards",
        "Hand",
    ]
    assert _headers(_table_after(html, ">Solo riders</h3>")) == [
        "Place",
        "Plate",
        "Entry",
        "Type",
        "Laps",
        "Total time",
        "Best lap",
        "Best hand",
    ]


def test_render_public_mixed_event_full_field_lists_every_entry() -> None:
    """The full-field tables stay uncapped: all 6 teams, all 6 solo."""
    html = render(_StubRide(), _field(teams=6, solo=6), ExportOptions(all_cards=False))

    assert _table_after(html, ">Teams</h3>").count("border-b border-ink/8") == 6
    assert _table_after(html, ">Solo riders</h3>").count("border-b border-ink/8") == 6


@pytest.mark.parametrize(
    ("show_times", "team_colspan", "solo_colspan"),
    [(False, "3", "4"), (True, "5", "6")],
)
def test_render_public_full_field_drawn_rows_use_per_kind_colspans(
    show_times: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
    team_colspan: str,
    solo_colspan: str,
) -> None:
    """Drawn rows span the kind's own geometry (Teams 5/7, Solo 6/8)."""
    html = render(
        _StubRide(),
        _field(teams=2, solo=2),
        ExportOptions(show_times=show_times, all_cards=True),
    )

    assert f'colspan="{team_colspan}"' in _table_after(html, ">Teams</h3>")
    assert f'colspan="{solo_colspan}"' in _table_after(html, ">Solo riders</h3>")


def test_render_public_solo_event_full_field_renders_one_solo_table() -> None:
    """Solo event: one full-field table under the Solo riders title."""
    html = render(_StubRide(), _placed_pair(), ExportOptions())

    full_field = _section_after(html, ">Full field</h2>")

    assert ">Solo riders</h3>" in full_field
    assert ">Teams</h3>" not in full_field


# =========================================================== tie notes
# tie_note renders under each top-list table whose rows include a tie
# -- and nowhere else on the page.


_TIE_NOTE_TEXT = "P3/P4 held identical hands — resolved by rule #1, most laps (10 v 9)."
_TIE_NOTE_MARKUP = f'<p class="mt-2 text-xs text-ink/50">{_TIE_NOTE_TEXT}</p>'


def _tie_payload(*, team_tie: bool, solo_tie: bool) -> RacePayload:
    """One TEAM row and one SOLO row, their tie flags as asked."""
    return RacePayload(
        event=_TIMES_PAYLOAD.event,
        options=ExportOptions(),
        tie_note=_TIE_NOTE_TEXT,
        results=(
            ResultRow(
                place=1,
                plate=500,
                entry="Team A",
                entry_type="TEAM",
                laps=11,
                hand="Four of a Kind",
                tie=team_tie,
            ),
            ResultRow(
                place=1,
                plate=7,
                entry="Solo A",
                entry_type="SOLO",
                laps=10,
                hand="Pair",
                tie=solo_tie,
            ),
        ),
    )


@pytest.mark.parametrize(
    ("team_tie", "solo_tie", "expected_notes"),
    [(False, False, 0), (True, False, 1), (False, True, 1), (True, True, 2)],
)
def test_render_payload_tie_note_renders_once_per_tied_top_table(
    team_tie: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
    solo_tie: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
    expected_notes: int,
) -> None:
    """The note lands under exactly the top tables whose rows tie."""
    html = htmlexport._render_payload(
        _tie_payload(team_tie=team_tie, solo_tie=solo_tie),
        dev=False,
        logo_src=_TRANSPARENT_PNG,
        generated=_FIXTURE_GENERATED,
    )

    assert html.count(_TIE_NOTE_MARKUP) == expected_notes


def test_render_payload_tie_note_sits_under_the_tied_top_teams_table() -> None:
    """A team-only tie puts the note under Top teams, not Top solo."""
    html = htmlexport._render_payload(
        _tie_payload(team_tie=True, solo_tie=False),
        dev=False,
        logo_src=_TRANSPARENT_PNG,
        generated=_FIXTURE_GENERATED,
    )

    assert _TIE_NOTE_MARKUP in _section_after(html, ">Top teams</h2>")
    assert _TIE_NOTE_MARKUP not in _section_after(html, ">Top solo riders</h2>")


def test_render_payload_tie_note_sits_under_the_tied_top_solo_table() -> None:
    """A solo-only tie puts the note under Top solo, not Top teams."""
    html = htmlexport._render_payload(
        _tie_payload(team_tie=False, solo_tie=True),
        dev=False,
        logo_src=_TRANSPARENT_PNG,
        generated=_FIXTURE_GENERATED,
    )

    assert _TIE_NOTE_MARKUP in _section_after(html, ">Top solo riders</h2>")
    assert _TIE_NOTE_MARKUP not in _section_after(html, ">Top teams</h2>")


def test_template_context_carries_the_payloads_section_plan() -> None:
    """The context hands the template Sections, never sliced rows."""
    context = htmlexport._template_context(
        _TIMES_PAYLOAD, dev=False, logo_src=None, generated=None
    )

    plan = context["sections"]

    assert [row.entry for row in plan.top_teams] == [
        "Moss Ridge Riders",
        "Dirt Dynamos",
        "Fat Tire Four",
        "Singletrack Sisters",
        "Chain Gang",
    ]


def test_laps_board_row_from_record_reads_and_defaults_the_type_key() -> None:
    """The inverse parser reads type, defaulting the pre-change rows."""
    typed = htmlexport._laps_board_row_from_record(
        {"plate": 88, "entry": "Moss Ridge Riders", "type": "TEAM", "laps": 11}
    )
    untyped = htmlexport._laps_board_row_from_record(
        {"plate": 7, "entry": "Luca Ferrari", "laps": 10}
    )

    assert typed == LapsBoardRow(plate=88, entry="Moss Ridge Riders", laps=11, type="TEAM")
    assert untyped == LapsBoardRow(plate=7, entry="Luca Ferrari", laps=10)
