# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the setup presenter (E3.5.1), tests-first (R-70).

``SetupPresenter`` now drives ``ride_setup_dlg`` from a real, in-
memory :class:`~rivercrossing.roster.Roster` -- the ``(view,
data_source)`` no-op shape from E1.2.3 is gone for this presenter,
the same E3.2.1 change ``RidersPresenter`` already went through
(``test_protocols.py``'s own updated pins record the exclusion).
``RecordingSetupView`` follows ``test_riders.py``'s own
``RecordingRidersView`` pattern: a hand-written fake recording every
call, in order, with its exact arguments -- no ``unittest.mock`` is
needed since this presenter touches no I/O boundary (T-10).
"""

import re
from datetime import date, datetime, time
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ride import (
    DEFAULT_DECK_COUNT,
    DEFAULT_TIEBREAK_ORDER,
    RideConfig,
    RideStatus,
)
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui.presenters.setup import (
    SetupFormValues,
    SetupPresenter,
    _parse_duration,
    _parse_min_lap,
)

# ------------------------------------------------------------- fixtures


class RecordingSetupView:
    """A complete ``SetupView`` spy recording each call, in order."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def set_team_fields_enabled(self, *, enabled: bool) -> None:
        """Record team_size_spin/relay_radio's enabled state."""
        self.calls.append(("set_team_fields_enabled", (enabled,)))

    def set_entry_locked(self, *, locked: bool) -> None:
        """Record the entry/plate-model group's locked state."""
        self.calls.append(("set_entry_locked", (locked,)))

    def show_deck_count(self, count: int) -> None:
        """Record decks_spin's rendered value."""
        self.calls.append(("show_deck_count", (count,)))

    def show_entry_settings(
        self, *, entry_mode: EntryMode, max_team_size: int, plate_model: PlateModel
    ) -> None:
        """Record the roster-sourced entry/team-size/plate-model."""
        self.calls.append(("show_entry_settings", (entry_mode, max_team_size, plate_model)))

    def show_validation(self, message: str) -> None:
        """Record a refused-submit message."""
        self.calls.append(("show_validation", (message,)))


def _solo_roster() -> Roster:
    """Return a bare, solo-only DRAFT roster (Roster's own default)."""
    return Roster()


def _mixed_pooled_roster() -> Roster:
    """Return a mixed, rider_pooled, size-6 DRAFT roster."""
    return Roster(entry_mode=EntryMode.MIXED, max_team_size=6, plate_model=PlateModel.RIDER_POOLED)


def _mixed_relay_roster(status: RideStatus) -> Roster:
    """Return a mixed, team_relay roster at *status*."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.status = status
    return roster


def _mixed_pooled_roster_at(status: RideStatus) -> Roster:
    """Return a mixed, rider_pooled roster at *status*."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.status = status
    return roster


_VALID_FORM_KWARGS: dict[str, object] = {
    "name": "GORBA EPIC 2026",
    "event_date": date(2026, 9, 20),
    "venue": "Sea to Sky Gondola",
    "lap_km": 8.0,
    "organizer": "GORBA",
    "scorer": "K. Singh",
    "start_time": time(10, 0),
    "duration_text": "6:00",
    "min_lap_text": "18:00",
    "entry_mode": EntryMode.MIXED,
    "max_team_size": 4,
    "plate_model": PlateModel.RIDER_POOLED,
    "deck_count": 8,
    "jokers_per_deck": 2,
    "cap_enabled": False,
    "max_cards": 1,
    "tiebreak_order": DEFAULT_TIEBREAK_ORDER,
    "logo_path": None,
}


def _form(**overrides: object) -> SetupFormValues:
    """Build a valid SetupFormValues, overriding what a test names."""
    return SetupFormValues(**{**_VALID_FORM_KWARGS, **overrides})  # type: ignore[arg-type]


# ------------------------------------------------------ construction


def test_setup_presenter_holds_the_view_and_roster_given() -> None:
    """The presenter stores the exact view and roster given (E3.5.1)."""
    view = RecordingSetupView()
    roster = Roster()

    presenter = SetupPresenter(view, roster)

    assert (presenter.view, presenter.roster) == (view, roster)


# --------------------------------------------------------- initial load


def test_setup_presenter_init_shows_the_presenter_supplied_deck_count() -> None:
    """decks_spin has no XRC value; the presenter supplies 8 (§4)."""
    view = RecordingSetupView()

    SetupPresenter(view, Roster())

    assert ("show_deck_count", (DEFAULT_DECK_COUNT,)) in view.calls


def test_setup_presenter_init_shows_the_rosters_own_entry_settings() -> None:
    """Opening setup on a live roster shows ITS values, not XRC's."""
    view = RecordingSetupView()
    roster = _mixed_pooled_roster()

    SetupPresenter(view, roster)

    assert (
        "show_entry_settings",
        (EntryMode.MIXED, 6, PlateModel.RIDER_POOLED),
    ) in view.calls


def test_setup_presenter_init_given_solo_roster_disables_team_fields() -> None:
    """A solo-only roster starts with team_size_spin/relay_radio off."""
    view = RecordingSetupView()

    SetupPresenter(view, _solo_roster())

    assert ("set_team_fields_enabled", (False,)) in view.calls


def test_setup_presenter_init_given_mixed_roster_enables_team_fields() -> None:
    """A mixed roster starts with team_size_spin/relay_radio on."""
    view = RecordingSetupView()

    SetupPresenter(view, _mixed_pooled_roster())

    assert ("set_team_fields_enabled", (True,)) in view.calls


def test_setup_presenter_init_given_locked_relay_also_disables_team_fields() -> None:
    """A locked group disables team_size_spin/relay_radio too (R-17).

    Guards the exact overlap measured in test_ride_setup.py's own
    functional suite: set_team_fields_enabled and set_entry_locked
    both used to touch team_size_spin/relay_radio, and whichever ran
    last silently undid the other -- a mixed+relay+RUNNING roster is
    the one case where "entry_mode is MIXED" (True) and "locked"
    (True) disagree about these two controls' own enabled state.
    """
    view = RecordingSetupView()

    SetupPresenter(view, _mixed_relay_roster(RideStatus.RUNNING))

    assert ("set_team_fields_enabled", (False,)) in view.calls


# --- entry/plate-model lock: status x plate_model (E3.5's own matrix) -


@pytest.mark.parametrize(
    ("roster_factory", "status", "expected_locked"),
    [
        (_mixed_pooled_roster_at, RideStatus.DRAFT, False),
        (_mixed_relay_roster, RideStatus.DRAFT, False),
        (_mixed_pooled_roster_at, RideStatus.RUNNING, False),
        (_mixed_relay_roster, RideStatus.RUNNING, True),
        (_mixed_pooled_roster_at, RideStatus.FINISHED, False),
        (_mixed_relay_roster, RideStatus.FINISHED, True),
        (_mixed_pooled_roster_at, RideStatus.REOPENED, False),
        (_mixed_relay_roster, RideStatus.REOPENED, True),
    ],
    ids=[
        "draft_pooled",
        "draft_relay",
        "running_pooled",
        "running_relay",
        "finished_pooled",
        "finished_relay",
        "reopened_pooled",
        "reopened_relay",
    ],
)
def test_setup_presenter_init_locks_entry_group_only_post_start_relay(
    roster_factory: object,
    status: RideStatus,
    expected_locked: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
) -> None:
    """R-17: the group locks post-start for relay, stays open pooled."""
    view = RecordingSetupView()
    roster = roster_factory(status)  # type: ignore[operator]

    SetupPresenter(view, roster)

    assert ("set_entry_locked", (expected_locked,)) in view.calls


# --------------------------------------------- on_entry_mode_changed


def test_on_entry_mode_changed_given_mixed_enables_team_fields() -> None:
    """Selecting mixed_radio enables team_size_spin/relay_radio live."""
    view = RecordingSetupView()
    presenter = SetupPresenter(view, _solo_roster())
    view.calls.clear()

    presenter.on_entry_mode_changed(EntryMode.MIXED)

    assert view.calls == [("set_team_fields_enabled", (True,))]


def test_on_entry_mode_changed_given_solo_disables_team_fields() -> None:
    """Selecting solo_radio disables team_size_spin/relay_radio live."""
    view = RecordingSetupView()
    presenter = SetupPresenter(view, _mixed_pooled_roster())
    view.calls.clear()

    presenter.on_entry_mode_changed(EntryMode.SOLO)

    assert view.calls == [("set_team_fields_enabled", (False,))]


# ---------------------------------------------------------- on_submit


def test_on_submit_given_a_valid_form_returns_the_built_config() -> None:
    """A fully valid form maps field-for-field onto RideConfig."""
    presenter = SetupPresenter(RecordingSetupView(), Roster())

    config = presenter.on_submit(_form())

    assert config == RideConfig(
        name="GORBA EPIC 2026",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive by design
        planned_duration_s=21600,
        min_lap_s=1080,
        entry_mode=EntryMode.MIXED,
        max_team_size=4,
        plate_model=PlateModel.RIDER_POOLED,
        deck_count=8,
        jokers_per_deck=2,
        max_cards=None,
        tiebreak_order=DEFAULT_TIEBREAK_ORDER,
        logo_path=None,
    )


def test_on_submit_given_cap_disabled_builds_an_uncapped_config() -> None:
    """cap_chk unticked: max_cards is None regardless of cap_spin."""
    presenter = SetupPresenter(RecordingSetupView(), Roster())

    config = presenter.on_submit(_form(cap_enabled=False, max_cards=50))

    assert config is not None
    assert config.max_cards is None


def test_on_submit_given_cap_enabled_builds_a_capped_config() -> None:
    """cap_chk ticked: max_cards carries cap_spin's own value."""
    presenter = SetupPresenter(RecordingSetupView(), Roster())

    config = presenter.on_submit(_form(cap_enabled=True, max_cards=50))

    assert config is not None
    assert config.max_cards == 50


def test_on_submit_combines_event_date_and_start_time_into_planned_start() -> None:
    """date_picker + start_time_picker combine into planned_start."""
    presenter = SetupPresenter(RecordingSetupView(), Roster())

    config = presenter.on_submit(_form(event_date=date(2026, 1, 2), start_time=time(9, 30)))

    assert config is not None
    assert config.planned_start == datetime(2026, 1, 2, 9, 30)  # noqa: DTZ001 -- naive by design


def test_on_submit_given_a_logo_path_carries_it_onto_the_config() -> None:
    """logo_picker's chosen path round-trips onto the built config."""
    presenter = SetupPresenter(RecordingSetupView(), Roster())
    path = Path("/tmp/gorba-logo.png")  # noqa: S108 -- a stored value, never opened here

    config = presenter.on_submit(_form(logo_path=path))

    assert config is not None
    assert config.logo_path == path


def test_on_submit_given_a_malformed_duration_shows_validation_not_crash() -> None:
    """An unparsable duration_input refuses via show_validation."""
    view = RecordingSetupView()
    presenter = SetupPresenter(view, Roster())

    result = presenter.on_submit(_form(duration_text="not-a-duration"))

    assert result is None
    assert view.calls[-1][0] == "show_validation"


def test_on_submit_given_a_malformed_min_lap_shows_validation_not_crash() -> None:
    """An unparsable min_lap_input refuses via show_validation."""
    view = RecordingSetupView()
    presenter = SetupPresenter(view, Roster())

    result = presenter.on_submit(_form(min_lap_text="not-a-time"))

    assert result is None
    assert view.calls[-1][0] == "show_validation"


def test_on_submit_given_an_out_of_range_team_size_shows_validation_not_crash() -> None:
    """A RideConfig-level refusal (R-12) shows validation too (T-5)."""
    view = RecordingSetupView()
    presenter = SetupPresenter(view, Roster())

    result = presenter.on_submit(_form(max_team_size=1))

    assert result is None
    assert view.calls[-1] == (
        "show_validation",
        ("max_team_size must be 2..10, got 1",),
    )


# ----------------------------------------- parsing: duration/min_lap


@pytest.mark.parametrize(
    ("text", "expected_seconds"),
    [("0:00", 0), ("6:00", 21600), ("12:30", 45000)],
    ids=["zero", "six_hours", "twelve_thirty"],
)
def test_parse_duration_given_h_mm_returns_seconds(text: str, expected_seconds: int) -> None:
    """duration_input's "H:MM" parses to whole seconds (spec §2)."""
    assert _parse_duration(text) == expected_seconds


@pytest.mark.parametrize(
    "text", ["", "6", "6:00:00", "a:bb"], ids=["empty", "no_colon", "two_colons", "non_numeric"]
)
def test_parse_duration_given_malformed_text_raises(text: str) -> None:
    """T-5: _parse_duration's own raise, on every malformed shape."""
    with pytest.raises(ValueError, match=re.escape("Duration must be H:MM")):
        _parse_duration(text)


@pytest.mark.parametrize(
    ("text", "expected_seconds"),
    [("0:00", 0), ("18:00", 1080), ("2:05", 125)],
    ids=["zero", "eighteen_minutes", "two_oh_five"],
)
def test_parse_min_lap_given_m_ss_returns_seconds(text: str, expected_seconds: int) -> None:
    """min_lap_input's "M:SS" parses to whole seconds (spec §6)."""
    assert _parse_min_lap(text) == expected_seconds


@pytest.mark.parametrize(
    "text", ["", "18", "18:00:00", "a:bb"], ids=["empty", "no_colon", "two_colons", "non_numeric"]
)
def test_parse_min_lap_given_malformed_text_raises(text: str) -> None:
    """T-5: _parse_min_lap's own raise, on every malformed shape."""
    with pytest.raises(ValueError, match=re.escape("Min lap must be M:SS")):
        _parse_min_lap(text)


# -------------------------------------------------- property test (T-7)


@given(
    hours=st.integers(min_value=0, max_value=23), minutes=st.integers(min_value=0, max_value=59)
)
def test_parse_duration_round_trips_every_h_mm_shape(hours: int, minutes: int) -> None:
    """Invariant: parsing "H:MM" always equals h*3600 + m*60."""
    text = f"{hours}:{minutes:02d}"

    assert _parse_duration(text) == hours * 3600 + minutes * 60


@given(
    minutes=st.integers(min_value=0, max_value=59), seconds=st.integers(min_value=0, max_value=59)
)
def test_parse_min_lap_round_trips_every_m_ss_shape(minutes: int, seconds: int) -> None:
    """Invariant: parsing "M:SS" always equals m*60 + s."""
    text = f"{minutes}:{seconds:02d}"

    assert _parse_min_lap(text) == minutes * 60 + seconds
