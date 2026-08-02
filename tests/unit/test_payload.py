# SPDX-License-Identifier: GPL-3.0-only
"""Payload dataclass tests (E1.2.2) -- tests first, per R-70.

Freezes the data contract the UI and every exporter share: pins
``ExportOptions`` defaults (TB-1), ``frozen=True`` on every payload
dataclass, ``to_record()``'s camelCase JSON shape against both golden
results pages (Spec §8, R-61/63), and the sparse ``tie``/``dnf``
convention. Golden key sets are parsed from the committed fixture
pages rather than hard-coded, so drift in the samples fails here too.
"""

import json
import re
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.htmlexport import (
    EventInfo,
    ExportOptions,
    LapsBoardRow,
    RacePayload,
    ResultRow,
    TimeBoardRow,
    _snake_to_camel,
)

_EXPORTS_DIR = Path(__file__).resolve().parents[2] / "design" / "exports"
_TIMES_GOLDEN = _EXPORTS_DIR / "epic-2026-results.html"
_NO_TIMES_GOLDEN = _EXPORTS_DIR / "epic-2026-results-no-times.html"

_RACE_DATA_RE = re.compile(
    r'<script type="application/json" id="race-data">(.*?)</script>', re.DOTALL
)


def _parse_golden(path: Path) -> dict[str, Any]:
    """Extract and parse a golden page's ``race-data`` JSON block."""
    match = _RACE_DATA_RE.search(path.read_text(encoding="utf-8"))
    if match is None:
        pytest.fail(f"no race-data block found in {path}")
    return json.loads(match.group(1))


def _sample_event() -> EventInfo:
    return EventInfo(
        kicker="Official results · poker run",
        title="Test Poker Run 2026",
        meta="Saturday June 6, 2026 · Test Venue · 8 km loop",
        organizer="Organizer: Test Org",
        scorer="Scorer: T. Ester",
        generated="Generated 12:00, June 6 2026",
        entries=3,
        laps=27,
        cards=25,
    )


def _sample_results() -> tuple[ResultRow, ...]:
    return (
        ResultRow(
            place=1,
            plate=88,
            entry="Moss Ridge Riders",
            entry_type="TEAM x4",
            laps=11,
            hand="Four of a Kind — Nines",
            total="5:52:41",
            best_lap="27:59",
            cards=(("9", "s"), ("9", "d"), ("9", "c"), ("JK", "j"), ("K", "h")),
            drawn=(("9", "s"), ("9", "d")),
        ),
        ResultRow(
            place=3,
            plate=127,
            entry="Dirt Dynamos",
            entry_type="TEAM x3",
            laps=10,
            hand="Full House — Queens over Nines",
            total="5:48:19",
            best_lap="30:52",
            tie=True,
            cards=(("Q", "s"), ("Q", "d"), ("JK", "j"), ("9", "h"), ("9", "c")),
            drawn=(("Q", "s"),),
        ),
        ResultRow(
            place=14,
            plate=94,
            entry="Ted Novak",
            entry_type="SOLO",
            laps=4,
            hand="High Card — Ace",
            total="2:37:43",
            best_lap="31:07",
            dnf=True,
            cards=(("A", "d"), ("Q", "c"), ("9", "s"), ("5", "h")),
            drawn=(("A", "d"),),
        ),
    )


def _sample_laps_board() -> tuple[LapsBoardRow, ...]:
    return (
        LapsBoardRow(plate=88, entry="Moss Ridge Riders", laps=11, total="5:52:41"),
        LapsBoardRow(plate=7, entry="Luca Ferrari", laps=10, total="5:41:03"),
    )


def _sample_time_board() -> tuple[TimeBoardRow, ...]:
    return (
        TimeBoardRow(plate=88, entry="Moss Ridge Riders", laps=11, total="5:52:41", avg="32:03"),
    )


def _times_shown_payload() -> RacePayload:
    return RacePayload(
        event=_sample_event(),
        options=ExportOptions(show_times=True, time_board=True),
        tie_note="P3 held an identical hand — resolved by rule #1.",
        results=_sample_results(),
        laps_board=_sample_laps_board(),
        time_board=_sample_time_board(),
    )


def _times_hidden_payload() -> RacePayload:
    return RacePayload(
        event=_sample_event(),
        options=ExportOptions(show_times=False, time_board=False),
        tie_note="P3 held an identical hand — resolved by rule #1.",
        results=_sample_results(),
        laps_board=_sample_laps_board(),
        time_board=(),
    )


# --- defaults ---


def test_export_options_defaults_match_r63_and_tb1() -> None:
    """Defaults are TB-1's fix, times hidden by default (R-63)."""
    options = ExportOptions()

    assert (
        options.show_times,
        options.laps_board,
        options.time_board,
        options.full_field,
        options.all_cards,
        options.lap_km,
    ) == (False, True, False, True, True, 8.0)


# --- frozen ---


@pytest.mark.parametrize(
    ("instance", "field_name", "value"),
    [
        (ExportOptions(), "show_times", True),
        (_sample_event(), "title", "Changed"),
        (_sample_results()[0], "place", 2),
        (_sample_laps_board()[0], "laps", 99),
        (_sample_time_board()[0], "avg", "0:00"),
        (_times_shown_payload(), "tie_note", "changed"),
    ],
)
def test_payload_dataclass_mutation_raises_frozen_instance_error(
    instance: object, field_name: str, value: object
) -> None:
    """Every payload dataclass is frozen (module-skeletons.md S4)."""
    with pytest.raises(
        FrozenInstanceError, match=re.escape(f"cannot assign to field {field_name!r}")
    ):
        setattr(instance, field_name, value)


# --- negative kwarg ---


def test_export_options_construction_with_unknown_keyword_raises_type_error() -> None:
    """An unrecognized keyword is rejected, never silently ignored."""
    with pytest.raises(TypeError, match=re.escape("unexpected keyword argument 'bogus'")):
        ExportOptions(bogus=True)


# --- key parity ---


def test_race_payload_to_record_key_sets_match_times_shown_golden() -> None:
    """Every level's keys match epic-2026-results.html's race-data."""
    golden = _parse_golden(_TIMES_GOLDEN)

    record = _times_shown_payload().to_record()

    assert set(record) == set(golden)
    assert set(record["event"]) == set(golden["event"])
    assert set(record["options"]) == set(golden["options"])
    assert {key for row in record["results"] for key in row} == {
        key for row in golden["results"] for key in row
    }
    assert set(record["lapsBoard"][0]) == set(golden["lapsBoard"][0])
    assert set(record["timeBoard"][0]) == set(golden["timeBoard"][0])


def test_race_payload_to_record_key_sets_match_no_times_golden() -> None:
    """Every level's key set matches epic-2026-results-no-times.html."""
    golden = _parse_golden(_NO_TIMES_GOLDEN)

    record = _times_hidden_payload().to_record()

    assert set(record) == set(golden)
    assert set(record["event"]) == set(golden["event"])
    assert set(record["options"]) == set(golden["options"])
    assert {key for row in record["results"] for key in row} == {
        key for row in golden["results"] for key in row
    }
    assert set(record["lapsBoard"][0]) == set(golden["lapsBoard"][0])
    assert record["timeBoard"] == golden["timeBoard"]


# --- R-63 ---


def test_race_payload_to_record_hides_total_and_best_lap_when_times_off() -> None:
    """R-63: withheld time fields are absent from the row, not null."""
    record = _times_hidden_payload().to_record()

    first_result = record["results"][0]
    assert "total" not in first_result
    assert "bestLap" not in first_result


def test_race_payload_to_record_drops_total_from_laps_board_when_times_off() -> None:
    """R-63: the laps-board's total column disappears with times off."""
    record = _times_hidden_payload().to_record()

    assert "total" not in record["lapsBoard"][0]


def test_race_payload_to_record_empties_time_board_when_times_off() -> None:
    """R-63: no time board at all when times are hidden."""
    record = _times_hidden_payload().to_record()

    assert record["timeBoard"] == []


def test_race_payload_to_record_keeps_total_and_best_lap_when_times_on() -> None:
    """Times shown: total/bestLap are present with their values."""
    record = _times_shown_payload().to_record()

    first_result = record["results"][0]
    assert first_result["total"] == "5:52:41"
    assert first_result["bestLap"] == "27:59"


def test_result_row_to_record_emits_null_total_when_unset_and_times_on() -> None:
    """A row with no recorded total still emits the key, as null."""
    row = ResultRow(place=1, plate=1, entry="X", entry_type="SOLO", laps=1, hand="Pair")

    record = row.to_record(show_times=True)

    assert record["total"] is None
    assert record["bestLap"] is None


def test_result_row_to_record_defaults_cards_and_drawn_to_empty_lists() -> None:
    """A row with no cards yet emits empty lists, not missing keys."""
    row = ResultRow(place=1, plate=1, entry="X", entry_type="SOLO", laps=1, hand="Pair")

    record = row.to_record(show_times=False)

    assert record["cards"] == []
    assert record["drawn"] == []


def test_result_row_to_record_converts_a_single_card_pair_to_a_list() -> None:
    """A one-card hand's ``cards``/``drawn`` are single-item lists."""
    row = ResultRow(
        place=1,
        plate=1,
        entry="X",
        entry_type="SOLO",
        laps=1,
        hand="High Card",
        cards=(("A", "s"),),
        drawn=(("A", "s"),),
    )

    record = row.to_record(show_times=False)

    assert record["cards"] == [["A", "s"]]
    assert record["drawn"] == [["A", "s"]]


# --- sparse tie / dnf ---


def test_race_payload_to_record_omits_tie_key_when_row_not_tied() -> None:
    """A non-tied row carries no ``tie`` key at all."""
    record = _times_shown_payload().to_record()

    assert "tie" not in record["results"][0]


def test_race_payload_to_record_includes_tie_key_when_row_tied() -> None:
    """A tied row carries ``tie: true``."""
    record = _times_shown_payload().to_record()

    assert record["results"][1]["tie"] is True


def test_race_payload_to_record_omits_dnf_key_when_row_not_dnf() -> None:
    """A finishing row carries no ``dnf`` key at all."""
    record = _times_shown_payload().to_record()

    assert "dnf" not in record["results"][0]


def test_race_payload_to_record_includes_dnf_key_when_row_dnf() -> None:
    """A DNF row carries ``dnf: true``."""
    record = _times_shown_payload().to_record()

    assert record["results"][2]["dnf"] is True


# --- camelCase mapping ---


@pytest.mark.parametrize(
    ("snake", "camel"),
    [
        ("show_times", "showTimes"),
        ("best_lap", "bestLap"),
        ("laps_board", "lapsBoard"),
    ],
)
def test_snake_to_camel_converts_field_name_to_json_key(snake: str, camel: str) -> None:
    """Each frozen field name converts to its golden-page JSON key."""
    assert _snake_to_camel(snake) == camel


# _snake_to_camel's real domain is Python dataclass field names: PEP 8
# ascii_lowercase snake_case identifiers, never arbitrary Unicode --
# str.capitalize() is not length-preserving over the full Unicode
# category "Ll" (e.g. 'ῢ'.capitalize() == 'Ϋ̀', 3 chars -> 4), which
# would falsify a length-preservation invariant that has no bearing
# on this function's actual callers.
_snake_word = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll"), max_codepoint=0x7A),
    min_size=1,
    max_size=8,
)
_snake_case_name = st.lists(_snake_word, min_size=1, max_size=4).map("_".join)


@given(_snake_case_name)
def test_snake_to_camel_never_contains_underscore_and_preserves_letter_count(
    snake: str,
) -> None:
    """Property: no underscore survives, and no letter is dropped."""
    camel = _snake_to_camel(snake)

    assert "_" not in camel
    assert len(camel) == len(snake) - snake.count("_")
