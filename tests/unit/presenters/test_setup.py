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
import tempfile
from datetime import date, datetime, time
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ride import (
    DEFAULT_DECK_COUNT,
    DEFAULT_LAP_KM,
    DEFAULT_TIEBREAK_ORDER,
    RideConfig,
    RideStatus,
)
from rivercrossing.roster import EntryMode, PlateModel, Roster, can_edit_structure
from rivercrossing.ui.presenters.setup import (
    SetupFormValues,
    SetupPresenter,
    _format_duration,
    _format_min_lap,
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

    def show_lap_km(self, lap_km: float) -> None:
        """Record lap_km_spin's rendered value."""
        self.calls.append(("show_lap_km", (lap_km,)))

    def show_entry_settings(
        self, *, entry_mode: EntryMode, max_team_size: int, plate_model: PlateModel
    ) -> None:
        """Record the roster-sourced entry/team-size/plate-model."""
        self.calls.append(("show_entry_settings", (entry_mode, max_team_size, plate_model)))

    def show_name(self, name: str) -> None:
        """Record name_input's rendered value (D2 preload)."""
        self.calls.append(("show_name", (name,)))

    def show_date(self, event_date: date) -> None:
        """Record date_picker's rendered value (D2 preload)."""
        self.calls.append(("show_date", (event_date,)))

    def show_start_time(self, start_time: time) -> None:
        """Record start_time_picker's rendered value (D2 preload)."""
        self.calls.append(("show_start_time", (start_time,)))

    def show_venue(self, venue: str) -> None:
        """Record venue_input's rendered value (D2 preload)."""
        self.calls.append(("show_venue", (venue,)))

    def show_organizer(self, organizer: str) -> None:
        """Record organizer_input's rendered value (D2 preload)."""
        self.calls.append(("show_organizer", (organizer,)))

    def show_scorer(self, scorer: str) -> None:
        """Record scorer_input's rendered value (D2 preload)."""
        self.calls.append(("show_scorer", (scorer,)))

    def show_duration(self, seconds: int) -> None:
        """Record duration_input's rendered "H:MM" text (D2 preload)."""
        self.calls.append(("show_duration", (seconds,)))

    def show_min_lap(self, seconds: int) -> None:
        """Record min_lap_input's rendered "M:SS" text (D2 preload)."""
        self.calls.append(("show_min_lap", (seconds,)))

    def show_short_lap_policy(self, *, hold_short_laps: bool) -> None:
        """Record the W4 radio pair's rendered policy (D2 preload)."""
        self.calls.append(("show_short_lap_policy", (hold_short_laps,)))

    def show_jokers_per_deck(self, count: int) -> None:
        """Record the jokers group's rendered value (D2)."""
        self.calls.append(("show_jokers_per_deck", (count,)))

    def show_card_cap(self, max_cards: int | None) -> None:
        """Record cap_chk/cap_spin's rendered cap (D2 preload)."""
        self.calls.append(("show_card_cap", (max_cards,)))

    def show_tiebreak_order(self, order: tuple[str, str, str]) -> None:
        """Record tiebreak_list's rendered row order (D2 preload)."""
        self.calls.append(("show_tiebreak_order", (order,)))

    def show_logo(self, logo_path: Path | None) -> None:
        """Record the logo column's rendered path (D2 preload)."""
        self.calls.append(("show_logo", (logo_path,)))

    def set_structure_enabled(self, *, enabled: bool) -> None:
        """Record the D2 structural-field gate."""
        self.calls.append(("set_structure_enabled", (enabled,)))

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
    "hold_short_laps": False,
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


# The stored ride an Edit Ride dialog (D2) opens onto. Every field is
# deliberately different from a New Ride's defaults (DEFAULT_DECK_COUNT
# / DEFAULT_LAP_KM / the XRC radio defaults / the high-card-first
# tie-break default), so a preload that quietly fell back to the
# defaults would disagree with it.
def _stored_config(**overrides: object) -> RideConfig:
    """Build the ride config an Edit Ride dialog preloads from."""
    kwargs: dict[str, object] = {
        "name": "GORBA EPIC 2026",
        "event_date": date(2026, 9, 20),
        "venue": "Sea to Sky Gondola",
        "lap_km": 6.5,
        "organizer": "GORBA",
        "scorer": "K. Singh",
        "planned_start": datetime(2026, 9, 20, 10, 30),  # noqa: DTZ001 -- naive by design
        "planned_duration_s": 7200,
        "min_lap_s": 90,
        "entry_mode": EntryMode.MIXED,
        "plate_model": PlateModel.TEAM_RELAY,
        "max_team_size": 6,
        "deck_count": 2,
        "jokers_per_deck": 4,
        "max_cards": 5,
        "tiebreak_order": ("laps", "total_time", "high_card"),
        "hold_short_laps": True,
        **overrides,
    }
    return RideConfig(**kwargs)  # type: ignore[arg-type]


# One (seam, arguments) row per field SetupPresenter._load renders from
# a stored config, transcribed from the ride record's own columns (not
# from the module under test).
_PRELOAD_CALLS: tuple[tuple[str, tuple[object, ...]], ...] = (
    ("show_name", ("GORBA EPIC 2026",)),
    ("show_date", (date(2026, 9, 20),)),
    ("show_start_time", (time(10, 30),)),
    ("show_venue", ("Sea to Sky Gondola",)),
    ("show_organizer", ("GORBA",)),
    ("show_scorer", ("K. Singh",)),
    ("show_duration", (7200,)),
    ("show_min_lap", (90,)),
    ("show_lap_km", (6.5,)),
    ("show_short_lap_policy", (True,)),
    ("show_entry_settings", (EntryMode.MIXED, 6, PlateModel.TEAM_RELAY)),
    ("show_deck_count", (2,)),
    ("show_jokers_per_deck", (4,)),
    ("show_card_cap", (5,)),
    ("show_tiebreak_order", (("laps", "total_time", "high_card"),)),
    ("show_logo", (None,)),
)
_PRELOAD_SEAMS = tuple(name for name, _args in _PRELOAD_CALLS)

# The three seams a New Ride's load ALSO pushes (its own deck/lap
# defaults and the roster's entry settings) -- so only the other
# thirteen are config-preload-only.
_DEFAULTS_LOAD_SEAMS = frozenset({"show_deck_count", "show_lap_km", "show_entry_settings"})


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


def test_setup_presenter_init_shows_the_presenter_supplied_lap_km() -> None:
    """lap_km_spin has no XRC value; the presenter supplies 8.0 (W4)."""
    view = RecordingSetupView()

    SetupPresenter(view, Roster())

    assert ("show_lap_km", (DEFAULT_LAP_KM,)) in view.calls


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


# ------------------------------------------- D2: Edit Ride preload


@pytest.mark.parametrize(("seam", "expected_args"), _PRELOAD_CALLS, ids=_PRELOAD_SEAMS)
def test_setup_presenter_init_given_a_stored_config_preloads_the_field(
    seam: str, expected_args: tuple[object, ...]
) -> None:
    """D2: Edit Ride opens PRELOADED with the ride's own config."""
    view = RecordingSetupView()

    SetupPresenter(view, _mixed_relay_roster(RideStatus.DRAFT), _stored_config())

    assert (seam, expected_args) in view.calls


def test_setup_presenter_init_given_a_stored_config_skips_the_new_ride_defaults() -> None:
    """D2: the defaults never overwrite the stored ride's own."""
    view = RecordingSetupView()

    SetupPresenter(view, _mixed_pooled_roster(), _stored_config())

    assert ("show_deck_count", (DEFAULT_DECK_COUNT,)) not in view.calls
    assert ("show_lap_km", (DEFAULT_LAP_KM,)) not in view.calls


def test_setup_presenter_init_given_no_config_leaves_the_preload_seams_uncalled() -> None:
    """A New Ride has no ride record to preload from (D2)."""
    view = RecordingSetupView()

    SetupPresenter(view, _mixed_pooled_roster())

    config_only = [
        name
        for name, _args in view.calls
        if name in _PRELOAD_SEAMS and name not in _DEFAULTS_LOAD_SEAMS
    ]
    assert config_only == []


def test_setup_presenter_init_given_a_stored_logo_preloads_its_path() -> None:
    """D2: the logo column opens on the ride's own logo."""
    view = RecordingSetupView()
    logo = Path(tempfile.gettempdir()) / "gorba-logo.png"

    SetupPresenter(view, _mixed_relay_roster(RideStatus.DRAFT), _stored_config(logo_path=logo))

    assert ("show_logo", (logo,)) in view.calls


def test_setup_presenter_init_given_an_uncapped_stored_config_preloads_none() -> None:
    """D2 nullable: an uncapped ride (max_cards NULL) preloads None."""
    view = RecordingSetupView()

    SetupPresenter(
        view,
        _mixed_relay_roster(RideStatus.DRAFT),
        _stored_config(max_cards=None),
    )

    assert ("show_card_cap", (None,)) in view.calls


def test_setup_presenter_init_given_no_config_keeps_structure_editing_enabled() -> None:
    """A New Ride is always DRAFT: structure stays editable (D2)."""
    view = RecordingSetupView()

    SetupPresenter(view, _mixed_pooled_roster())

    assert ("set_structure_enabled", (True,)) in view.calls


@pytest.mark.parametrize(
    "status",
    [RideStatus.DRAFT, RideStatus.RUNNING, RideStatus.FINISHED, RideStatus.REOPENED],
)
def test_setup_presenter_init_gates_structure_editing_on_the_ride_state(
    status: RideStatus,
) -> None:
    """D2: only a DRAFT ride's structure may be edited."""
    view = RecordingSetupView()

    SetupPresenter(view, _mixed_pooled_roster_at(status), _stored_config())

    assert ("set_structure_enabled", (can_edit_structure(status),)) in view.calls


def test_setup_presenter_init_given_a_stored_relay_ride_locks_from_its_own_plate_model() -> None:
    """D2: the lock reads the CONFIG's plate model, not the roster's.

    A stored relay ride left DRAFT locks the entry/plate group; the
    preloaded config is the authoritative shape it locks against.
    """
    view = RecordingSetupView()
    running_relay = _mixed_relay_roster(RideStatus.RUNNING)

    SetupPresenter(view, running_relay, _stored_config())

    assert ("set_entry_locked", (True,)) in view.calls


def test_on_submit_given_a_preloaded_config_still_builds_from_the_form() -> None:
    """D2: an edit submit rebuilds the config from the form."""
    presenter = SetupPresenter(RecordingSetupView(), _mixed_pooled_roster(), _stored_config())

    config = presenter.on_submit(_form(name="Renamed Ride"))

    assert config is not None
    assert config.name == "Renamed Ride"


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
    """A staged logo path round-trips onto the built config."""
    presenter = SetupPresenter(RecordingSetupView(), Roster())
    path = Path(tempfile.gettempdir()) / "gorba-logo.png"

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


@pytest.mark.parametrize("duration_text", ["", "   "], ids=["empty", "whitespace"])
def test_on_submit_given_a_blank_duration_shows_the_blank_message_and_refuses(
    duration_text: str,
) -> None:
    """A stripped-empty duration_input names the blank, not a format."""
    view = RecordingSetupView()
    presenter = SetupPresenter(view, Roster())

    result = presenter.on_submit(_form(duration_text=duration_text))

    assert result is None
    assert view.calls[-1] == (
        "show_validation",
        ("Duration is empty and must be completed",),
    )


@pytest.mark.parametrize("min_lap_text", ["", "   "], ids=["empty", "whitespace"])
def test_on_submit_given_a_blank_min_lap_shows_the_blank_message_and_refuses(
    min_lap_text: str,
) -> None:
    """A stripped-empty min_lap_input names the blank, not a format."""
    view = RecordingSetupView()
    presenter = SetupPresenter(view, Roster())

    result = presenter.on_submit(_form(min_lap_text=min_lap_text))

    assert result is None
    assert view.calls[-1] == ("show_validation", ("Min lap is blank and must be completed",))


def test_on_submit_given_hold_short_laps_checked_carries_it_onto_the_config() -> None:
    """hold_short_radio checked flows through to the built config."""
    presenter = SetupPresenter(RecordingSetupView(), Roster())

    config = presenter.on_submit(_form(hold_short_laps=True))

    assert config is not None
    assert config.hold_short_laps is True


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


# ----------------------- minimum-setup gate (R-20, on-submit refusal)
# A complete form must clear setup_minimum_violations -- the same
# minimum-setup floor ride.start() enforces (blank name/venue/
# organizer/scorer or a non-positive lap length refuses the submit).


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("name", "name is required"),
        ("venue", "venue is required"),
        ("organizer", "organizer is required"),
        ("scorer", "scorer is required"),
    ],
    ids=["name", "venue", "organizer", "scorer"],
)
def test_on_submit_given_a_blank_required_field_shows_validation_and_refuses(
    field: str, reason: str
) -> None:
    """Whitespace-only counts as blank; the reason shows (R-20)."""
    view = RecordingSetupView()
    presenter = SetupPresenter(view, Roster())

    result = presenter.on_submit(_form(**{field: "   "}))  # type: ignore[arg-type]

    assert result is None
    assert view.calls[-1] == ("show_validation", (reason,))


@pytest.mark.parametrize("lap_km", [0.0, -1.0], ids=["zero", "negative"])
def test_on_submit_given_a_non_positive_lap_km_shows_validation_and_refuses(
    lap_km: float,
) -> None:
    """A zero/negative lap length refuses with its reason (R-20)."""
    view = RecordingSetupView()
    presenter = SetupPresenter(view, Roster())

    result = presenter.on_submit(_form(lap_km=lap_km))

    assert result is None
    assert view.calls[-1] == ("show_validation", ("lap length must be positive",))


def test_on_submit_given_multiple_missing_fields_joins_every_reason() -> None:
    """Every violation shows joined, ride.start()'s own shape."""
    view = RecordingSetupView()
    presenter = SetupPresenter(view, Roster())

    result = presenter.on_submit(_form(name="", venue=" ", lap_km=0.0))

    assert result is None
    assert view.calls[-1] == (
        "show_validation",
        ("name is required; venue is required; lap length must be positive",),
    )


def test_on_submit_given_a_complete_form_passes_the_minimum_setup_gate() -> None:
    """A complete form clears the gate with no show_validation call."""
    view = RecordingSetupView()
    presenter = SetupPresenter(view, Roster())
    view.calls.clear()

    config = presenter.on_submit(_form())

    assert config is not None
    assert config.name == "GORBA EPIC 2026"
    assert view.calls == []


# ----------------------------------------- parsing: duration/min_lap


@pytest.mark.parametrize(
    ("text", "expected_seconds"),
    [("0:00", 0), ("6:00", 21600), ("12:30", 45000)],
    ids=["zero", "six_hours", "twelve_thirty"],
)
def test_parse_duration_given_h_mm_returns_seconds(text: str, expected_seconds: int) -> None:
    """duration_input's "H:MM" parses to whole seconds (spec §2)."""
    assert _parse_duration(text) == expected_seconds


@pytest.mark.parametrize("text", ["", "   ", "\t"], ids=["empty", "spaces", "tab"])
def test_parse_duration_given_a_blank_text_raises_the_blank_message(text: str) -> None:
    """T-5: a stripped-empty duration names the blank, not a format."""
    with pytest.raises(ValueError, match=re.escape("Duration is empty and must be completed")):
        _parse_duration(text)


@pytest.mark.parametrize(
    "text", ["6", "6:00:00", "a:bb"], ids=["no_colon", "two_colons", "non_numeric"]
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


@pytest.mark.parametrize("text", ["", "   ", "\t"], ids=["empty", "spaces", "tab"])
def test_parse_min_lap_given_a_blank_text_raises_the_blank_message(text: str) -> None:
    """T-5: a stripped-empty min lap names the blank, not a format."""
    with pytest.raises(ValueError, match=re.escape("Min lap is blank and must be completed")):
        _parse_min_lap(text)


@pytest.mark.parametrize(
    "text", ["18", "18:00:00", "a:bb"], ids=["no_colon", "two_colons", "non_numeric"]
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


# ------------------------- D2 preload formatting: "H:MM" / "M:SS"
# The inverse of the parsers above: a stored ride's whole-second
# planned_duration_s/min_lap_s must render into the same text fields
# the operator would have typed.


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0:00"), (59, "0:00"), (60, "0:01"), (21600, "6:00"), (45000, "12:30")],
    ids=["zero", "sub_minute", "one_minute", "six_hours", "twelve_thirty"],
)
def test_format_duration_given_seconds_returns_h_mm(seconds: int, expected: str) -> None:
    """duration_input's preloaded text is whole H:MM (spec §2)."""
    assert _format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0:00"), (59, "0:59"), (60, "1:00"), (125, "2:05"), (1080, "18:00")],
    ids=["zero", "sub_minute", "one_minute", "two_oh_five", "eighteen_minutes"],
)
def test_format_min_lap_given_seconds_returns_m_ss(seconds: int, expected: str) -> None:
    """min_lap_input's preloaded text is whole M:SS (spec §6)."""
    assert _format_min_lap(seconds) == expected


@given(
    hours=st.integers(min_value=0, max_value=23),
    minutes=st.integers(min_value=0, max_value=59),
)
def test_format_duration_round_trips_every_h_mm_shape(hours: int, minutes: int) -> None:
    """T-7 invariant: format(parse("H:MM")) is the identical text."""
    text = f"{hours}:{minutes:02d}"

    assert _format_duration(_parse_duration(text)) == text


@given(
    minutes=st.integers(min_value=0, max_value=59),
    seconds=st.integers(min_value=0, max_value=59),
)
def test_format_min_lap_round_trips_every_m_ss_shape(minutes: int, seconds: int) -> None:
    """T-7 invariant: format(parse("M:SS")) is the identical text."""
    text = f"{minutes}:{seconds:02d}"

    assert _format_min_lap(_parse_min_lap(text)) == text
