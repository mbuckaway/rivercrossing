# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for ``ui.presenters.ride_defaults`` (test-ride setup).

``ride_defaults`` is pure Python (R-71), so its whole surface -- the
``next_test_ride_name`` numbering rule, the module's fixed default
constants and the :class:`~rivercrossing.ride.RideConfig` that
:func:`build_test_ride_config` builds -- is exercised headless here.
The built config is also put through ``setup_minimum_violations``, so
the test ride is proved to be one a DRAFT ride may actually start.
"""

from datetime import date, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ride import (
    DEFAULT_JOKERS_MODE,
    DEFAULT_TIEBREAK_ORDER,
    RideConfig,
    setup_minimum_violations,
)
from rivercrossing.roster import EntryMode, PlateModel
from rivercrossing.ui.presenters.ride_defaults import (
    DEFAULT_MAX_TEAM_SIZE,
    DEFAULT_MIN_LAP_S,
    DEFAULT_ORGANIZER,
    DEFAULT_PLANNED_DURATION_S,
    DEFAULT_SCORER,
    DEFAULT_VENUE,
    TEST_RIDE_NAME_PREFIX,
    build_test_ride_config,
    next_test_ride_name,
)

# The identity arguments every builder test below hands in, fixed so a
# failure names the field that drifted rather than the argument.
NAME = "GORBA Test Ride #7"
EVENT_DATE = date(2026, 9, 20)
# Naive-local is RideConfig's own contract (app.py builds the same
# shape for the bootstrap ride).
PLANNED_START = datetime(2026, 9, 20, 10, 0)  # noqa: DTZ001 -- local wall clock, pre-persistence

# The decoys the numbering rule must read past: only a name carrying
# the exact prefix with an ASCII decimal tail counts as a test ride.
UNRELATED_NAME = "GORBA EPIC 2026"
LOWER_CASE_NAME = "gorba test ride #9"
INFIX_NAME = "xGORBA Test Ride #9"
UNPREFIXED_NAME = "GORBA Test Ride 9"
EMPTY_SUFFIX_NAME = "GORBA Test Ride #"
WORD_SUFFIX_NAME = "GORBA Test Ride #x"
MIXED_SUFFIX_NAME = "GORBA Test Ride #9x"
NEGATIVE_SUFFIX_NAME = "GORBA Test Ride #-1"
PADDED_SUFFIX_NAME = "GORBA Test Ride # 9"
TRAILING_SPACE_NAME = "GORBA Test Ride #9 "
# ٩ is an Arabic-Indic nine: a decimal digit, but not an ASCII one.
NON_ASCII_SUFFIX_NAME = "GORBA Test Ride #٩"


# --------------------------------------------------- the frozen prefix


def test_ride_defaults_prefix_is_the_gorba_test_ride_prefix() -> None:
    """Every generated name carries this one frozen prefix."""
    assert TEST_RIDE_NAME_PREFIX == "GORBA Test Ride #"


# ------------------------------------------------ next_test_ride_name


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        ([], "GORBA Test Ride #1"),
        ([UNRELATED_NAME], "GORBA Test Ride #1"),
        ([LOWER_CASE_NAME], "GORBA Test Ride #1"),
        ([INFIX_NAME], "GORBA Test Ride #1"),
        ([UNPREFIXED_NAME], "GORBA Test Ride #1"),
        ([EMPTY_SUFFIX_NAME], "GORBA Test Ride #1"),
        ([WORD_SUFFIX_NAME], "GORBA Test Ride #1"),
        ([MIXED_SUFFIX_NAME], "GORBA Test Ride #1"),
        ([NEGATIVE_SUFFIX_NAME], "GORBA Test Ride #1"),
        ([PADDED_SUFFIX_NAME], "GORBA Test Ride #1"),
        ([TRAILING_SPACE_NAME], "GORBA Test Ride #1"),
        ([NON_ASCII_SUFFIX_NAME], "GORBA Test Ride #1"),
        (["GORBA Test Ride #1"], "GORBA Test Ride #2"),
        (["GORBA Test Ride #0"], "GORBA Test Ride #1"),
        (["GORBA Test Ride #99"], "GORBA Test Ride #100"),
        (["GORBA Test Ride #1", "GORBA Test Ride #3"], "GORBA Test Ride #4"),
        (["GORBA Test Ride #3", "GORBA Test Ride #1"], "GORBA Test Ride #4"),
        (
            ["GORBA Test Ride #2", UNRELATED_NAME, "GORBA Test Ride #5"],
            "GORBA Test Ride #6",
        ),
        ([WORD_SUFFIX_NAME, "GORBA Test Ride #2"], "GORBA Test Ride #3"),
        (["GORBA Test Ride #1"] * 4, "GORBA Test Ride #2"),
    ],
    ids=[
        "no_names",
        "unrelated_name",
        "lower_case",
        "prefixed_infix",
        "no_hash",
        "empty_suffix",
        "word_suffix",
        "mixed_suffix",
        "negative_suffix",
        "padded_suffix",
        "trailing_space",
        "non_ascii_suffix",
        "single_previous",
        "zero_suffix",
        "two_digits",
        "highest_of_two",
        "order_independent",
        "decoy_between",
        "non_numeric_beside_numeric",
        "duplicates",
    ],
)
def test_next_test_ride_name_given_names_returns_the_highest_suffix_plus_one(
    existing: list[str], expected: str
) -> None:
    """Only exact-prefix decimal tails count; the highest wins."""
    assert next_test_ride_name(existing) == expected


def test_next_test_ride_name_given_a_bare_prefix_matches_nothing() -> None:
    """The prefix alone is not a numbered test ride."""
    assert next_test_ride_name([TEST_RIDE_NAME_PREFIX]) == "GORBA Test Ride #1"


def test_next_test_ride_name_given_a_set_of_names_returns_the_next_suffix() -> None:
    """T-4 collection: any iterable counts, not just a list."""
    assert next_test_ride_name({"GORBA Test Ride #1", "GORBA Test Ride #8"}) == (
        "GORBA Test Ride #9"
    )


def test_next_test_ride_name_given_an_iterator_returns_the_next_suffix() -> None:
    """T-4 collection: a one-shot iterable is consumed, not re-read."""
    assert next_test_ride_name(iter(["GORBA Test Ride #4"])) == "GORBA Test Ride #5"


def test_next_test_ride_name_given_the_largest_suffix_still_returns_a_larger_one() -> None:
    """T-4 boundary: the generator has no ceiling to fall off."""
    assert next_test_ride_name(["GORBA Test Ride #999999"]) == "GORBA Test Ride #1000000"


@given(suffixes=st.lists(st.integers(min_value=1, max_value=10**6), max_size=20))
def test_next_test_ride_name_given_registered_names_never_reuses_a_suffix(
    suffixes: list[int],
) -> None:
    """T-7 invariant: the returned name is never already taken."""
    existing = [f"{TEST_RIDE_NAME_PREFIX}{suffix}" for suffix in suffixes]

    result = next_test_ride_name(existing)

    assert result not in existing


@given(suffixes=st.lists(st.integers(min_value=1, max_value=10**6), max_size=20))
def test_next_test_ride_name_given_the_returned_name_advances_by_exactly_one(
    suffixes: list[int],
) -> None:
    """T-7 invariant: the counter advances by exactly one."""
    existing = [f"{TEST_RIDE_NAME_PREFIX}{suffix}" for suffix in suffixes]
    highest = max(suffixes, default=0)

    result = next_test_ride_name([*existing, next_test_ride_name(existing)])

    assert result == f"{TEST_RIDE_NAME_PREFIX}{highest + 2}"


# --------------------------------------------- the default constants


def test_ride_defaults_identity_constants_pin_the_gorba_reference_ride() -> None:
    """Venue, organizer and scorer are the GORBA ride's own."""
    assert (DEFAULT_VENUE, DEFAULT_ORGANIZER, DEFAULT_SCORER) == (
        "Sea to Sky Gondola",
        "GORBA",
        "K. Singh",
    )


def test_ride_defaults_timing_constants_pin_the_six_hour_six_lap_ride() -> None:
    """T-4 boundary: six hours planned, the 18-minute lap floor."""
    assert (DEFAULT_PLANNED_DURATION_S, DEFAULT_MIN_LAP_S) == (21600, 1080)


def test_ride_defaults_team_size_constant_is_five() -> None:
    """The test ride seats five on a team (R-12's 2..10 bound)."""
    assert DEFAULT_MAX_TEAM_SIZE == 5


# ------------------------------------------- build_test_ride_config


def _test_ride() -> RideConfig:
    """Build the test ride's config from the fixed identity."""
    return build_test_ride_config(name=NAME, event_date=EVENT_DATE, planned_start=PLANNED_START)


def test_build_test_ride_config_given_identity_arguments_returns_them_unchanged() -> None:
    """The three caller-supplied fields survive the build verbatim."""
    config = _test_ride()

    assert (config.name, config.event_date, config.planned_start) == (
        NAME,
        EVENT_DATE,
        PLANNED_START,
    )


def test_build_test_ride_config_given_identity_arguments_builds_a_ride_config() -> None:
    """The builder's own return type, not a duck-typed stand-in."""
    assert type(_test_ride()) is RideConfig


def test_build_test_ride_config_given_identity_arguments_uses_mixed_rider_pooled() -> None:
    """Teams are allowed; every rider carries their own plate."""
    config = _test_ride()

    assert (config.entry_mode, config.plate_model, config.max_team_size) == (
        EntryMode.MIXED,
        PlateModel.RIDER_POOLED,
        5,
    )


def test_build_test_ride_config_given_identity_arguments_uses_the_gorba_identity() -> None:
    """Venue, organizer and scorer come from the module constants."""
    config = _test_ride()

    assert (config.venue, config.organizer, config.scorer) == (
        "Sea to Sky Gondola",
        "GORBA",
        "K. Singh",
    )


def test_build_test_ride_config_given_identity_arguments_uses_the_eight_km_loop() -> None:
    """8 km loop, six hours planned, an 18-minute lap floor."""
    config = _test_ride()

    assert (config.lap_km, config.planned_duration_s, config.min_lap_s) == (8.0, 21600, 1080)


def test_build_test_ride_config_given_identity_arguments_uses_the_default_shoe() -> None:
    """The spec §4 shoe: 8 decks, 1 joker, spent ride-wide."""
    config = _test_ride()

    assert (config.deck_count, config.jokers_per_deck, config.jokers_mode) == (8, 1, "total")


def test_build_test_ride_config_given_identity_arguments_leaves_the_hand_uncapped() -> None:
    """R-13 nullable: no card cap, so every credited card scores."""
    assert _test_ride().max_cards is None


def test_build_test_ride_config_given_identity_arguments_keeps_the_default_tiebreak() -> None:
    """R-14: the frozen high-card-first order is the test ride's own."""
    assert _test_ride().tiebreak_order == DEFAULT_TIEBREAK_ORDER


def test_build_test_ride_config_given_identity_arguments_holds_short_laps() -> None:
    """W4: a short lap's card waits for review, the safe default."""
    assert _test_ride().hold_short_laps is True


def test_build_test_ride_config_given_identity_arguments_has_no_logo() -> None:
    """T-4 nullable: the test ride ships unstaged, no logo path."""
    assert _test_ride().logo_path is None


def test_build_test_ride_config_given_identity_arguments_passes_the_minimum_setup_rule() -> None:
    """The test ride is one a DRAFT ride may actually start (R-79)."""
    assert setup_minimum_violations(_test_ride()) == []


def test_build_test_ride_config_given_identity_arguments_uses_the_total_jokers_mode() -> None:
    """Phase 5: the mode spelling is the store's own."""
    assert _test_ride().jokers_mode == DEFAULT_JOKERS_MODE


@given(
    name=st.text(min_size=1, max_size=40),
    event_date=st.dates(),
    planned_start=st.datetimes(),
)
def test_build_test_ride_config_given_any_identity_keeps_the_ride_shape_fixed(
    name: str, event_date: date, planned_start: datetime
) -> None:
    """T-7 invariant: only the identity varies; the shape never does."""
    config = build_test_ride_config(name=name, event_date=event_date, planned_start=planned_start)

    assert (
        config.name,
        config.event_date,
        config.planned_start,
        config.entry_mode,
        config.plate_model,
        config.max_team_size,
        config.lap_km,
    ) == (name, event_date, planned_start, EntryMode.MIXED, PlateModel.RIDER_POOLED, 5, 8.0)
