# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the menu route map and its rules (E1.4.1, E1.4.2).

Everything here runs without ``wx`` and without a display:
``commands.py`` imports no ``wx`` at all, so its 38-row route table,
its ``route_for_id`` dispatch, and its ``is_route_enabled`` /
``is_stop_button_enabled`` rules are pure Python -- exactly the kind
of logic R-71's >=90% branch-coverage gate is meant to cover, and
exactly why it belongs here rather than only in the functional suite
(``cards_imagelist``'s split between ``tests/unit/`` and
``tests/functional/`` is the precedent).

Two things a real ``wx.MenuBar`` can prove that nothing here can are
kept in ``tests/functional/test_menu_coverage.py`` instead: that a
real ``wx.CommandEvent(wx.EVT_MENU, ...)`` actually reaches a route
(R-73's "reachable and drivable"), and the macOS stock-item
relocation measurement. Everything else -- the route table's shape,
its kind/target transcription, ``route_for_id``'s dispatch and its
negative path, and the full item x state enablement matrix -- needs
no display and lives here.

Two independently-authored transcripts are the whole point of this
module: :data:`ROUTE_TARGETS` (§15's "Opens / does" column) and
:data:`ALLOWED_STATES` (§15's "Enabled when" column) are each typed
by hand from spec.md, not derived from ``commands.py`` itself, so a
transcription mistake in either place is caught by the other
disagreeing rather than the test only checking the implementation
against itself. This is what caught a real gap during development:
mutating one row's ``target`` in ``commands.py`` passed silently
until the target-transcript test below was added.
"""

import dataclasses
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ride import RideStatus
from rivercrossing.ui import commands, ids

# --- route table shape (E1.4.1) ---------------------------------------

ROUTE_COUNTS_BY_MENU = (
    ("File", 8),
    ("Ride", 7),
    ("Riders", 4),
    ("Cards", 7),
    ("Results", 7),
    ("View", 1),
    ("Help", 4),
)

# One (kind, target) pair per commands.ROUTE_TABLE row, in the same
# order (spec.md section 15's own row order), transcribed independently
# of commands.py's ROUTE_TABLE itself -- from each row's literal
# "Opens / does" text, not from the module under test -- so a wrong
# kind or target in the table is caught by this disagreeing with it,
# not by the table checking itself. COMMAND rows carry no spec-named
# target string (an OS-native picker or an external browser has no
# XRC name), so their expected target is None and is not asserted.
ROUTE_TARGETS = (
    (commands.TargetKind.WINDOW, ids.RIDE_SETUP_DLG),  # New Ride...
    (commands.TargetKind.WINDOW, ids.RIDE_LIBRARY_DLG),  # Ride Library
    (commands.TargetKind.DIALOG, ids.DUPLICATE_RIDE_DLG),  # Duplicate Ride... (E5.4.1)
    (commands.TargetKind.DIALOG, ids.CSV_PREVIEW_DLG),  # Import Riders CSV...
    (commands.TargetKind.COMMAND, None),  # Export Riders CSV...: OS-native save dialog
    (commands.TargetKind.COMMAND, None),  # Back Up Database...: OS-native save dialog
    (commands.TargetKind.WINDOW, ids.SETTINGS_DLG),  # Settings...
    (commands.TargetKind.COMMAND, None),  # Exit: branches, no single fixed target
    (commands.TargetKind.COMMAND, None),  # Start Ride: branches, no single fixed target
    (commands.TargetKind.DIALOG, ids.STOP_CONFIRM_DLG),  # Stop Ride...
    (commands.TargetKind.DIALOG, ids.SET_START_DLG),  # Set Start Time...
    (commands.TargetKind.DIALOG, ids.FINISH_CONFIRM_DLG),  # Finish Ride...
    (commands.TargetKind.DIALOG, ids.REOPEN_RIDE_DLG),  # Reopen Ride (E5.4.1)
    (commands.TargetKind.WINDOW, ids.AUDIT_DLG),  # Audit Trail...
    (commands.TargetKind.WINDOW, ids.RIDE_SETUP_DLG),  # Ride Setup...
    (commands.TargetKind.WINDOW, ids.RIDER_EDITOR_DLG),  # Rider Editor
    (commands.TargetKind.WINDOW, ids.RIDER_EDITOR_DLG),  # Add Rider/Entry...
    (commands.TargetKind.DIALOG, ids.DNF_CONFIRM_DLG),  # Mark DNF...
    (commands.TargetKind.WINDOW, ids.ENTRY_DETAIL_DLG),  # Entry Detail...
    (commands.TargetKind.COMMAND, None),  # Undo Last Crossing: "no dialog"
    (commands.TargetKind.DIALOG, ids.EDIT_CROSSING_DLG),  # Add Crossing at Time...
    (commands.TargetKind.DIALOG, ids.EDIT_CROSSING_DLG),  # Edit Crossing...
    (commands.TargetKind.DIALOG, ids.REASSIGN_DLG),  # Reassign Plate...
    (commands.TargetKind.DIALOG, ids.MANUAL_DEAL_DLG),  # Deal Manual Card...
    (commands.TargetKind.DIALOG, ids.VOID_CARD_CONFIRM_DLG),  # Void Card... (E7)
    (commands.TargetKind.COMMAND, None),  # Review Held Cards: focuses an existing panel
    (commands.TargetKind.WINDOW, ids.RESULTS_FRAME),  # Standings
    (commands.TargetKind.COMMAND, None),  # Generate HTML...: OS-native save dialog
    (commands.TargetKind.COMMAND, None),  # Export PDF...: OS-native save dialog
    (commands.TargetKind.COMMAND, None),  # Podium Poster PDF...: OS-native save dialog
    (commands.TargetKind.COMMAND, None),  # Export Standings CSV...: OS-native save dialog
    (commands.TargetKind.COMMAND, None),  # Preview in Browser: external browser
    (commands.TargetKind.COMMAND, None),  # Tie-break Order...: focuses an existing control
    (commands.TargetKind.COMMAND, None),  # Theme / Hide Times / Zoom: direct commands
    (commands.TargetKind.COMMAND, None),  # User Guide: external browser
    (commands.TargetKind.DIALOG, ids.SHORTCUTS_DLG),  # Keyboard Shortcuts
    (commands.TargetKind.DIALOG, ids.SELFTEST_DLG),  # Run Evaluator Self-test
    (commands.TargetKind.DIALOG, ids.ABOUT_DLG),  # About RiverCrossing
)
_ZIPPED_TARGETS = tuple(zip(commands.ROUTE_TABLE, ROUTE_TARGETS, strict=True))
KIND_CASES = tuple((route, kind) for route, (kind, _target) in _ZIPPED_TARGETS)
KIND_CASE_IDS = [f"{route.menu}:{route.label}" for route, _kind in KIND_CASES]
TARGET_CASES = tuple(
    (route, target) for route, (_kind, target) in _ZIPPED_TARGETS if target is not None
)
TARGET_CASE_IDS = [f"{route.menu}:{route.label}" for route, _target in TARGET_CASES]

ALL_ROUTE_IDS = tuple(item_id for route in commands.ROUTE_TABLE for item_id in route.ids)


def test_route_table_declares_exactly_the_thirty_eight_spec_15_rows() -> None:
    """A lost route shrinks this count, not the suite (spec.md §15)."""
    assert len(commands.ROUTE_TABLE) == 38


@pytest.mark.parametrize(("menu", "expected_rows"), ROUTE_COUNTS_BY_MENU)
def test_route_table_menu_breakdown_matches_spec_15(menu: str, expected_rows: int) -> None:
    """File 8, Ride 7, Riders 4, Cards 7, Results 7, View 1, Help 4."""
    rows = [route for route in commands.ROUTE_TABLE if route.menu == menu]

    assert len(rows) == expected_rows


def test_route_table_covers_all_forty_eight_real_menu_item_ids_once_each() -> None:
    """45 mi_* + 3 stock ids (main.xrc's own header), none repeated."""
    flat_ids = [item_id for route in commands.ROUTE_TABLE for item_id in route.ids]

    assert len(flat_ids) == 48
    assert len(set(flat_ids)) == 48


@pytest.mark.parametrize(("route", "expected_kind"), KIND_CASES, ids=KIND_CASE_IDS)
def test_route_kind_matches_spec_15_opens_does_column(
    route: commands.MenuRoute, expected_kind: commands.TargetKind
) -> None:
    """Independently transcribed WINDOW/DIALOG/COMMAND per §15 row."""
    assert route.kind is expected_kind


@pytest.mark.parametrize(("route", "expected_target"), TARGET_CASES, ids=TARGET_CASE_IDS)
def test_route_target_matches_spec_15_opens_does_column(
    route: commands.MenuRoute, expected_target: str
) -> None:
    """Independently transcribed target for every WINDOW/DIALOG row."""
    assert route.target == expected_target


def test_route_for_id_given_an_unrouted_fake_id_raises() -> None:
    """Negative (R-73): an unrouted mi_ id must fail the walk loudly."""
    fake_id = "mi_totally_fake_probe_id_not_in_any_route"

    with pytest.raises(commands.UnroutedMenuItemError, match=re.escape(fake_id)):
        commands.route_for_id(fake_id)


@given(st.sampled_from(ALL_ROUTE_IDS))
def test_route_for_id_given_any_registered_id_returns_a_route_that_declares_it(
    item_id: str,
) -> None:
    """Property: every id round-trips to a route that declares it."""
    resolved = commands.route_for_id(item_id)

    assert item_id in resolved.ids


# --- state enablement (E1.4.2) ----------------------------------------

STATUSES = (RideStatus.DRAFT, RideStatus.RUNNING, RideStatus.FINISHED, RideStatus.REOPENED)
STATUS_STRATEGY = st.sampled_from(STATUSES)

# One entry per commands.ROUTE_TABLE row, in the same order (spec.md
# section 15's own row order) -- the state-membership half of each
# row's "Enabled when" cell, transcribed independently of
# commands.Enablement so the two can disagree if either is wrong.
# None means the row does not gate on RideStatus at all (either
# "always", or a condition-only rule such as Review Held Cards').
ALLOWED_STATES = (
    None,  # File > New Ride...: "always"
    None,  # File > Ride Library: "always"
    None,  # File > Duplicate Ride...: "a ride is open"
    None,  # File > Import Riders CSV...: "ride open (DRAFT-only edits)"
    None,  # File > Export Riders CSV...: "ride open"
    None,  # File > Back Up Database...: "always"
    None,  # File > Settings...: "always"
    None,  # File > Exit: "always"
    frozenset({RideStatus.DRAFT, RideStatus.RUNNING}),  # Start Ride: "or stopped RUNNING"
    frozenset({RideStatus.RUNNING}),  # Ride > Stop Ride...: "RUNNING"
    frozenset({RideStatus.RUNNING, RideStatus.REOPENED}),  # Ride > Set Start Time...
    frozenset({RideStatus.RUNNING, RideStatus.REOPENED}),  # Ride > Finish Ride...
    frozenset({RideStatus.FINISHED}),  # Ride > Reopen Ride: "FINISHED"
    None,  # Ride > Audit Trail...: "ride open, >=1 audit row"
    None,  # Ride > Ride Setup...: "ride open (locks tighten after start)"
    None,  # Riders > Rider Editor: "ride open"
    None,  # Riders > Add Rider/Entry...: "ride open (new plates any time)"
    frozenset({RideStatus.RUNNING, RideStatus.REOPENED}),  # Riders > Mark DNF...
    None,  # Riders > Entry Detail...: "ride open"
    frozenset({RideStatus.RUNNING}),  # Cards > Undo Last Crossing: "RUNNING, >=1 crossing"
    frozenset({RideStatus.RUNNING, RideStatus.REOPENED}),  # Cards > Add Crossing at Time...
    frozenset({RideStatus.RUNNING, RideStatus.REOPENED}),  # Cards > Edit Crossing...
    frozenset({RideStatus.RUNNING, RideStatus.REOPENED}),  # Cards > Reassign Plate...
    frozenset({RideStatus.RUNNING, RideStatus.REOPENED}),  # Cards > Deal Manual Card...
    frozenset({RideStatus.RUNNING, RideStatus.REOPENED}),  # Cards > Void Card...
    None,  # Cards > Review Held Cards: "held cards > 0"
    None,  # Results > Standings: "ride open (live while running)"
    frozenset({RideStatus.FINISHED}),  # Results > Generate HTML...
    frozenset({RideStatus.FINISHED}),  # Results > Export PDF...
    frozenset({RideStatus.FINISHED}),  # Results > Podium Poster PDF...
    frozenset({RideStatus.FINISHED}),  # Results > Export Standings CSV...
    None,  # Results > Preview in Browser: "an export exists"
    None,  # Results > Tie-break Order...: "ride open"
    None,  # View > Theme / Hide Times / Zoom: "always"
    None,  # Help > User Guide: "always"
    None,  # Help > Keyboard Shortcuts: "always"
    None,  # Help > Run Evaluator Self-test: "always"
    None,  # Help > About RiverCrossing: "always"
)


def _baseline_state(status: RideStatus) -> commands.RideState:
    """Build a RideState with every non-status condition satisfied.

    Isolates the item x state matrix to state-gating alone; the
    per-condition tests below vary exactly one field away from this.
    """
    return commands.RideState(
        status=status,
        ride_open=True,
        ride_stopped=True,
        crossings=1,
        held_cards=1,
        audit_rows=1,
        entry_has_cards=True,
        export_exists=True,
    )


ITEM_STATE_MATRIX = tuple(
    (route, status, allowed is None or status in allowed)
    for route, allowed in zip(commands.ROUTE_TABLE, ALLOWED_STATES, strict=True)
    for status in STATUSES
)
ITEM_STATE_IDS = [
    f"{route.menu}:{route.label}[{status}]" for route, status, _ in ITEM_STATE_MATRIX
]

RIDE_OPEN_REQUIRING_ROUTES = tuple(
    route for route in commands.ROUTE_TABLE if route.enabled_when.requires_ride_open
)
CROSSINGS_GATED_ROUTES = tuple(
    route for route in commands.ROUTE_TABLE if route.enabled_when.min_crossings > 0
)
_ROUTES_BY_LABEL = {route.label: route for route in commands.ROUTE_TABLE}
REVIEW_HELD_CARDS_ROUTE = _ROUTES_BY_LABEL["Review Held Cards"]
AUDIT_TRAIL_ROUTE = _ROUTES_BY_LABEL["Audit Trail…"]
VOID_CARD_ROUTE = _ROUTES_BY_LABEL["Void Card…"]
PREVIEW_ROUTE = _ROUTES_BY_LABEL["Preview in Browser"]
START_RIDE_ROUTE = _ROUTES_BY_LABEL["Start Ride"]

RIDE_OPEN_CASES = (True, False)
RIDE_OPEN_CASE_IDS = ("ride_open", "no_ride_open")
CROSSINGS_BOUNDARY_CASES = (0, 1, 2)
HELD_CARDS_BOUNDARY_CASES = (0, 1, 2)
AUDIT_ROWS_BOUNDARY_CASES = (0, 1, 2)
ENTRY_HAS_CARDS_CASES = ((False, False), (True, True))
EXPORT_EXISTS_CASES = ((False, False), (True, True))
ARMED_CASES = ((False, False), (True, True))
START_RIDE_CASES = (
    (RideStatus.DRAFT, False, True),
    (RideStatus.DRAFT, True, True),
    (RideStatus.RUNNING, False, False),
    (RideStatus.RUNNING, True, True),
    (RideStatus.FINISHED, True, False),
    (RideStatus.REOPENED, True, False),
)


@pytest.mark.parametrize(
    ("route", "status", "expected_enabled"), ITEM_STATE_MATRIX, ids=ITEM_STATE_IDS
)
def test_is_route_enabled_given_item_and_state_matches_spec_15(
    route: commands.MenuRoute, status: RideStatus, *, expected_enabled: bool
) -> None:
    """Every §15 row across all four states, baseline-satisfied."""
    state = _baseline_state(status)

    result = commands.is_route_enabled(route, state)

    assert result is expected_enabled


@pytest.mark.parametrize(
    "route", RIDE_OPEN_REQUIRING_ROUTES, ids=lambda route: f"{route.menu}:{route.label}"
)
@pytest.mark.parametrize("ride_open", RIDE_OPEN_CASES, ids=RIDE_OPEN_CASE_IDS)
def test_is_route_enabled_given_ride_open_condition_gates_the_route(
    route: commands.MenuRoute, *, ride_open: bool
) -> None:
    """T-3: both branches of the ride-open guard, every gated row."""
    state = dataclasses.replace(_baseline_state(RideStatus.DRAFT), ride_open=ride_open)

    result = commands.is_route_enabled(route, state)

    assert result is ride_open


@pytest.mark.parametrize(
    "route", CROSSINGS_GATED_ROUTES, ids=lambda route: f"{route.menu}:{route.label}"
)
@pytest.mark.parametrize("crossings", CROSSINGS_BOUNDARY_CASES)
def test_is_route_enabled_given_crossings_boundary_matches_minimum(
    route: commands.MenuRoute, crossings: int
) -> None:
    """T-4 boundary (min-1, min, min+1) for the "≥1 crossing" rows."""
    state = dataclasses.replace(_baseline_state(RideStatus.RUNNING), crossings=crossings)

    result = commands.is_route_enabled(route, state)

    assert result is (crossings >= route.enabled_when.min_crossings)


@pytest.mark.parametrize("held_cards", HELD_CARDS_BOUNDARY_CASES)
def test_is_route_enabled_given_held_cards_boundary_matches_review_held_cards(
    held_cards: int,
) -> None:
    """T-4 boundary for Review Held Cards' "held cards > 0"."""
    state = dataclasses.replace(_baseline_state(RideStatus.DRAFT), held_cards=held_cards)

    result = commands.is_route_enabled(REVIEW_HELD_CARDS_ROUTE, state)

    assert result is (held_cards >= 1)


@pytest.mark.parametrize("audit_rows", AUDIT_ROWS_BOUNDARY_CASES)
def test_is_route_enabled_given_audit_rows_boundary_matches_audit_trail(audit_rows: int) -> None:
    """T-4 boundary for Audit Trail's "≥1 audit row"."""
    state = dataclasses.replace(_baseline_state(RideStatus.DRAFT), audit_rows=audit_rows)

    result = commands.is_route_enabled(AUDIT_TRAIL_ROUTE, state)

    assert result is (audit_rows >= 1)


@pytest.mark.parametrize(("entry_has_cards", "expected"), ENTRY_HAS_CARDS_CASES)
def test_is_route_enabled_given_entry_has_cards_condition_matches_void_card(
    *, entry_has_cards: bool, expected: bool
) -> None:
    """T-3: both branches of Void Card's "entry has cards" guard."""
    state = dataclasses.replace(
        _baseline_state(RideStatus.RUNNING), entry_has_cards=entry_has_cards
    )

    result = commands.is_route_enabled(VOID_CARD_ROUTE, state)

    assert result is expected


@pytest.mark.parametrize(("export_exists", "expected"), EXPORT_EXISTS_CASES)
def test_is_route_enabled_given_export_exists_condition_matches_preview_in_browser(
    *, export_exists: bool, expected: bool
) -> None:
    """T-3: both branches of Preview's export-exists guard."""
    state = dataclasses.replace(_baseline_state(RideStatus.DRAFT), export_exists=export_exists)

    result = commands.is_route_enabled(PREVIEW_ROUTE, state)

    assert result is expected


@pytest.mark.parametrize(("status", "ride_stopped", "expected_enabled"), START_RIDE_CASES)
def test_is_route_enabled_given_start_ride_stopped_condition_matches_spec(
    status: RideStatus, *, ride_stopped: bool, expected_enabled: bool
) -> None:
    """Start Ride: DRAFT, or stopped RUNNING -- a state/condition OR."""
    state = dataclasses.replace(_baseline_state(status), ride_stopped=ride_stopped)

    result = commands.is_route_enabled(START_RIDE_ROUTE, state)

    assert result is expected_enabled


# --- E7.2.1: the live binder's enable/disable table for the -----------
# --- correction rows (menu_state applies exactly this table) ----------

# spec.md §15's "Enabled when" cells for the six correction rows the
# live menu binder targets, transcribed independently of commands.py
# itself (the same double-transcription discipline as ALLOWED_STATES).
# The fourth field is Void Card's own "entry has cards" requirement;
# None means the row has no such condition.
_RUNNING_FOR_TESTS = frozenset({RideStatus.RUNNING})
_RUNNING_REOPENED_FOR_TESTS = frozenset({RideStatus.RUNNING, RideStatus.REOPENED})

_CORRECTION_ENABLEMENT = (
    ("Undo Last Crossing", _RUNNING_FOR_TESTS, 1, None),
    ("Add Crossing at Time…", _RUNNING_REOPENED_FOR_TESTS, 0, None),
    ("Edit Crossing…", _RUNNING_REOPENED_FOR_TESTS, 1, None),
    ("Reassign Plate…", _RUNNING_REOPENED_FOR_TESTS, 1, None),
    ("Deal Manual Card…", _RUNNING_REOPENED_FOR_TESTS, 0, None),
    ("Void Card…", _RUNNING_REOPENED_FOR_TESTS, 0, 1),
)


@pytest.mark.parametrize(
    ("label", "allowed", "min_crossings", "entry_cards"),
    _CORRECTION_ENABLEMENT,
)
@pytest.mark.parametrize("status", STATUSES, ids=lambda status: status.value)
def test_is_route_enabled_given_correction_route_matches_the_live_binder_table(  # noqa: PLR0913, PLR0917 -- (label, allowed, min_crossings, entry_cards, status)
    label: str,
    allowed: frozenset[RideStatus],
    min_crossings: int,
    entry_cards: int | None,
    *,
    status: RideStatus,
) -> None:
    """The six correction rows' verdicts are the §15 table, per state.

    Every row's state gate, its numeric minimum and Void Card's own
    entry-has-cards condition combine exactly as the live binder
    applies them (menu_state.enablement_table).
    """
    state = dataclasses.replace(
        _baseline_state(status),
        crossings=min_crossings,
        entry_has_cards=bool(entry_cards) if entry_cards is not None else True,
    )
    route = _ROUTES_BY_LABEL[label]
    expected = (
        status in allowed
        and state.crossings >= min_crossings
        and (entry_cards is None or state.entry_has_cards)
    )

    result = commands.is_route_enabled(route, state)

    assert result is expected


@pytest.mark.parametrize(("armed", "expected_enabled"), ARMED_CASES)
def test_is_stop_button_enabled_given_arm_checkbox_state_matches_r35(
    *, armed: bool, expected_enabled: bool
) -> None:
    """R-35: the console Stop button is gated on nothing but Arm."""
    result = commands.is_stop_button_enabled(armed=armed)

    assert result is expected_enabled


def _ride_states_with(*, ride_open: bool) -> st.SearchStrategy[commands.RideState]:
    """Build a RideState strategy with ride_open fixed, else free.

    Every other field is generated freely, so the property below
    holds regardless of what any of them is.
    """
    return st.builds(
        commands.RideState,
        status=STATUS_STRATEGY,
        ride_open=st.just(ride_open),
        ride_stopped=st.booleans(),
        crossings=st.integers(min_value=0, max_value=5),
        held_cards=st.integers(min_value=0, max_value=5),
        audit_rows=st.integers(min_value=0, max_value=5),
        entry_has_cards=st.booleans(),
        export_exists=st.booleans(),
    )


@given(
    route=st.sampled_from(RIDE_OPEN_REQUIRING_ROUTES),
    state=_ride_states_with(ride_open=False),
)
def test_is_route_enabled_given_ride_not_open_and_route_requires_it_stays_disabled(
    route: commands.MenuRoute, state: commands.RideState
) -> None:
    """Property: requires_ride_open always blocks a closed ride.

    Holds no matter what any other RideState field is.
    """
    result = commands.is_route_enabled(route, state)

    assert result is False
