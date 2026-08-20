# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.ride's RideConfig (E3.5.1).

Written first, against a ``RideConfig`` that does not exist yet
(R-70): module-skeletons.md S4's own reserved name --
``RideEngine.__init__(config: RideConfig, ...)`` in E4 -- pre-created
here next to ``RideStatus``, mirroring how ``RideStatus`` itself was
pre-created ahead of the state machine that consumes it.

Boundary rows follow this repo's own T-4 convention (min-1, min,
min+1, max-1, max, max+1) for every bounded field Phase 5's own brief
named: ``max_team_size`` (2..10, R-12), ``deck_count`` (>=1, spec.md
§4), ``planned_duration_s``/``min_lap_s`` (positive, spec.md §2/§6).
"""

import re
from dataclasses import FrozenInstanceError
from datetime import date, datetime
from pathlib import Path

import pytest

from rivercrossing.ride import (
    DEFAULT_DECK_COUNT,
    DEFAULT_JOKERS_PER_DECK,
    TIEBREAK_HIGH_CARD,
    TIEBREAK_LAPS,
    TIEBREAK_TOTAL_TIME,
    RideConfig,
    RideConfigError,
)
from rivercrossing.roster import EntryMode, PlateModel

# A minimal, always-valid kwarg set every test overrides from -- one
# required field at a time, never guessing at a second field's own
# validity while probing the first (T-8's one-focused-assertion
# spirit, applied to arrange too).
_VALID_KWARGS: dict[str, object] = {
    "name": "GORBA EPIC 2026",
    "event_date": date(2026, 9, 20),
    "venue": "Sea to Sky Gondola",
    "lap_km": 8.0,
    "organizer": "GORBA",
    "scorer": "K. Singh",
    # naive, by design: planned_start is a pre-persistence, local
    # wall-clock value (RideConfig's own docstring) -- UTC-epoch
    # conversion is EPIC 5's Store concern, not this dataclass's.
    "planned_start": datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001
    "planned_duration_s": 21600,
    "min_lap_s": 1080,
    "entry_mode": EntryMode.MIXED,
    "plate_model": PlateModel.RIDER_POOLED,
}


def _config(**overrides: object) -> RideConfig:
    """Build a valid RideConfig, overriding only what a test names."""
    return RideConfig(**{**_VALID_KWARGS, **overrides})  # type: ignore[arg-type]


# ------------------------------------------------------------- defaults


def test_ride_config_bare_required_fields_defaults_max_team_size_to_four() -> None:
    """max_team_size defaults to 4 (spec.md §1/§2, R-12)."""
    config = _config()

    assert config.max_team_size == 4


def test_ride_config_bare_required_fields_defaults_deck_count_to_eight() -> None:
    """decks_spin's own presenter-supplied default (spec.md §4)."""
    config = _config()

    assert config.deck_count == DEFAULT_DECK_COUNT


def test_ride_config_bare_required_fields_defaults_jokers_per_deck_to_two() -> None:
    """jokers_2_radio's XRC default (xrc-windows.md's setup mock)."""
    config = _config()

    assert config.jokers_per_deck == DEFAULT_JOKERS_PER_DECK


def test_ride_config_bare_required_fields_defaults_max_cards_to_uncapped() -> None:
    """cap_chk unticked by default: max_cards is None (uncapped)."""
    config = _config()

    assert config.max_cards is None


def test_ride_config_bare_required_fields_defaults_tiebreak_order_to_the_spec_order() -> None:
    """R-14's own order: laps, then total time, then high-card draw."""
    config = _config()

    assert config.tiebreak_order == (TIEBREAK_LAPS, TIEBREAK_TOTAL_TIME, TIEBREAK_HIGH_CARD)


def test_ride_config_bare_required_fields_defaults_logo_path_to_none() -> None:
    """logo_picker empty by default: no logo chosen (E5 owns BLOBs)."""
    config = _config()

    assert config.logo_path is None


def test_ride_config_given_a_logo_path_stores_it_verbatim() -> None:
    """A chosen logo_picker path round-trips exactly."""
    path = Path("/tmp/gorba-logo.png")  # noqa: S108 -- a stored value, never opened here

    config = _config(logo_path=path)

    assert config.logo_path == path


def test_ride_config_given_every_required_field_stores_each_verbatim() -> None:
    """Every required field round-trips exactly, in one built config."""
    config = _config()

    assert (config.name, config.venue, config.entry_mode, config.plate_model) == (
        "GORBA EPIC 2026",
        "Sea to Sky Gondola",
        EntryMode.MIXED,
        PlateModel.RIDER_POOLED,
    )


# ------------------------------------------------------ frozen/kw-only


def test_ride_config_mutation_raises_frozen_instance_error() -> None:
    """RideConfig is frozen (module-skeletons.md S4's own rule)."""
    config = _config()

    with pytest.raises(FrozenInstanceError, match=re.escape("cannot assign to field 'name'")):
        config.name = "Changed"  # type: ignore[misc]


def test_ride_config_requires_every_field_as_keyword() -> None:
    """RideConfig takes no positional arguments (kw_only=True)."""
    with pytest.raises(TypeError, match=re.escape("takes 1 positional argument")):
        RideConfig("GORBA EPIC 2026")  # type: ignore[misc, call-arg]


# ----------------------------------------------- max_team_size bound


@pytest.mark.parametrize("max_team_size", [1, 11], ids=["min-1", "max+1"])
def test_ride_config_max_team_size_out_of_range_raises(max_team_size: int) -> None:
    """max_team_size outside 2..10 raises (R-12)."""
    with pytest.raises(RideConfigError, match=re.escape("max_team_size")):
        _config(max_team_size=max_team_size)


@pytest.mark.parametrize("max_team_size", [2, 3, 9, 10], ids=["min", "min+1", "max-1", "max"])
def test_ride_config_max_team_size_in_range_is_accepted(max_team_size: int) -> None:
    """max_team_size within 2..10 is accepted as given."""
    config = _config(max_team_size=max_team_size)

    assert config.max_team_size == max_team_size


# --------------------------------------------------- deck_count bound


@pytest.mark.parametrize("deck_count", [0, -1], ids=["min-1", "min-2"])
def test_ride_config_deck_count_below_one_raises(deck_count: int) -> None:
    """deck_count below 1 raises (spec.md §4: >=1 deck needed)."""
    with pytest.raises(RideConfigError, match=re.escape("deck_count")):
        _config(deck_count=deck_count)


@pytest.mark.parametrize("deck_count", [1, 2, 8], ids=["min", "min+1", "default"])
def test_ride_config_deck_count_at_or_above_one_is_accepted(deck_count: int) -> None:
    """deck_count >= 1 is accepted as given."""
    config = _config(deck_count=deck_count)

    assert config.deck_count == deck_count


# ------------------------------------------- planned_duration_s bound


@pytest.mark.parametrize("planned_duration_s", [0, -1], ids=["zero", "negative"])
def test_ride_config_planned_duration_not_positive_raises(planned_duration_s: int) -> None:
    """planned_duration_s must be positive (spec.md §2)."""
    with pytest.raises(RideConfigError, match=re.escape("planned_duration_s")):
        _config(planned_duration_s=planned_duration_s)


@pytest.mark.parametrize("planned_duration_s", [1, 21600], ids=["min+1", "six_hours"])
def test_ride_config_planned_duration_positive_is_accepted(planned_duration_s: int) -> None:
    """A positive planned_duration_s is accepted as given."""
    config = _config(planned_duration_s=planned_duration_s)

    assert config.planned_duration_s == planned_duration_s


# --------------------------------------------------- min_lap_s bound


@pytest.mark.parametrize("min_lap_s", [0, -1], ids=["zero", "negative"])
def test_ride_config_min_lap_not_positive_raises(min_lap_s: int) -> None:
    """min_lap_s must be positive (spec.md §6)."""
    with pytest.raises(RideConfigError, match=re.escape("min_lap_s")):
        _config(min_lap_s=min_lap_s)


@pytest.mark.parametrize("min_lap_s", [1, 1080], ids=["min+1", "eighteen_minutes"])
def test_ride_config_min_lap_positive_is_accepted(min_lap_s: int) -> None:
    """A positive min_lap_s is accepted as given."""
    config = _config(min_lap_s=min_lap_s)

    assert config.min_lap_s == min_lap_s
