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

The goldens at
``tests/unit/fixtures/htmlexport/epic-2026-results*.html`` were
regenerated once by ``tools/gen_htmlexport_goldens.py`` from this
renderer; regenerating them is deliberate (Spec section 8 tests).
"""

import base64
import json
import re
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from htmlexport_fixtures import (
    GOLDEN_NO_TIMES,
    GOLDEN_TIMES,
    NO_TIMES_FIXTURE,
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
    RacePayload,
    ResultRow,
    render,
)
from rivercrossing.standings import EntryResult, Placed

_TIMES_PAYLOAD = load_race_payload(TIMES_FIXTURE)
_NO_TIMES_PAYLOAD = load_race_payload(NO_TIMES_FIXTURE)

_FIXTURE_GENERATED = _TIMES_PAYLOAD.event.generated

_FIVE_CARDS = (
    Card(Rank.NINE, Suit.SPADES),
    Card(Rank.NINE, Suit.DIAMONDS),
    Card(Rank.NINE, Suit.CLUBS),
    Card(Rank.KING, Suit.HEARTS),
    Card(Rank.TWO, Suit.SPADES),
)


def _sample_entry(  # noqa: PLR0913 -- (plate, name, laps, dnf): the EntryResult's own fields
    plate: str, name: str, laps: int, *, dnf: bool = False
) -> EntryResult:
    """Build one finished-ride EntryResult for the render() seam tests.

    The same five-card hand stands in for every entry's draw.
    """
    return EntryResult(
        entry_id=plate,
        plate=plate,
        name=name,
        kind="solo",
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


# -------------------------------------------------- record round-trip


@pytest.mark.parametrize("fixture", [TIMES_FIXTURE, NO_TIMES_FIXTURE])
def test_payload_from_record_round_trips_fixture_record(fixture: Path) -> None:
    """Record -> RacePayload -> record is identity for both samples."""
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
    del context["results"]

    with pytest.raises(UndefinedError, match=re.escape("'results' is undefined")):
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


def test_render_public_logo_path_embeds_base64_data_uri() -> None:
    """logo_path bytes become the data URI (R-61 logo base64)."""
    logo = base64.b64decode(_TRANSPARENT_PNG.split(",", 1)[1])
    with tempfile.TemporaryDirectory() as tmp_dir:
        logo_file = Path(tmp_dir) / "logo.png"
        logo_file.write_bytes(logo)
        html = render(_StubRide(), _placed_pair(), ExportOptions(), logo_path=logo_file)

    assert "data:image/png;base64," in html
