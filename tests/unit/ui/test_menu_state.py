# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the live menu-enablement binder (E1.4.2, E7.2.1).

``commands.py`` owns the §15 "Enabled when" rules as pure logic over
``commands.RideState``; ``ui.menu_state`` is the missing E1.4.2 half
that *applies* those rules to a real menu bar. This module pins the
binder headlessly:

1. ``enablement_table`` produces one enable/disable verdict per routed
   menu item id (48 ids, one per ``commands.ROUTE_TABLE`` row), and
   the verdicts agree with ``commands.is_route_enabled`` for every
   generated ``RideState`` (a Hypothesis property).
2. The correction rows' verdicts are parametrized over the four ride
   states and the §15 conditions -- the exact table the live binder
   applies when the console's state changes.
3. ``apply_to_menubar`` walks a fake menubar and ``Enable()``s each
   item with the computed verdict; the ``xrcid`` seam lets the same
   code run without wx, and a ``FindItem`` miss (an id with no live
   menu item) is a silent skip, never a crash.

The one fact only a real ``wx.MenuBar`` can prove -- that
``menubar.FindItem(XRCID(...)).Enable(...)`` genuinely flips a loaded
menu item -- lives in ``tests/functional/test_entry_detail_actions.py``
(and the bootstrap's ``set_state`` seam), mirroring the split
``test_commands.py``/``test_menu_coverage.py`` already use.
"""

import dataclasses

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ride import RideStatus
from rivercrossing.ui import commands, ids, menu_state

STATUSES = (RideStatus.DRAFT, RideStatus.RUNNING, RideStatus.FINISHED, RideStatus.REOPENED)
STATUS_STRATEGY = st.sampled_from(STATUSES)

_ALL_ROUTE_IDS = tuple(item_id for route in commands.ROUTE_TABLE for item_id in route.ids)

# The correction rows E7.2.1's binder targets (spec §15 "Enabled when"),
# as (label, allowed_states) -- transcribed independently from the
# route table's own ``enabled_when`` so the two can disagree if either
# is wrong.
_CORRECTION_CASES: tuple[tuple[str, frozenset[RideStatus]], ...] = (
    ("Undo Last Crossing", frozenset({RideStatus.RUNNING})),
    ("Add Crossing at Time…", frozenset({RideStatus.RUNNING, RideStatus.REOPENED})),
    ("Edit Crossing…", frozenset({RideStatus.RUNNING, RideStatus.REOPENED})),
    ("Reassign Plate…", frozenset({RideStatus.RUNNING, RideStatus.REOPENED})),
    ("Deal Manual Card…", frozenset({RideStatus.RUNNING, RideStatus.REOPENED})),
    ("Void Card…", frozenset({RideStatus.RUNNING, RideStatus.REOPENED})),
    ("Mark DNF…", frozenset({RideStatus.RUNNING, RideStatus.REOPENED})),
)
_CORRECTION_ROUTES = {
    label: next(route for route in commands.ROUTE_TABLE if route.label == label)
    for label, _allowed in _CORRECTION_CASES
}


def _baseline_state(status: RideStatus) -> commands.RideState:
    """Build a RideState with every non-status condition satisfied."""
    return commands.RideState(
        status=status,
        ride_open=True,
        ride_stopped=True,
        crossings=1,
        held_cards=1,
        audit_rows=1,
        entry_has_cards=True,
        html_exported=True,
        pdf_exported=True,
    )


def _state_strategy() -> st.SearchStrategy[commands.RideState]:
    """Generate every RideState field freely."""
    return st.builds(
        commands.RideState,
        status=STATUS_STRATEGY,
        ride_open=st.booleans(),
        ride_stopped=st.booleans(),
        crossings=st.integers(min_value=0, max_value=5),
        held_cards=st.integers(min_value=0, max_value=5),
        audit_rows=st.integers(min_value=0, max_value=5),
        entry_has_cards=st.booleans(),
        html_exported=st.booleans(),
        pdf_exported=st.booleans(),
    )


# ----------------------------------------------------- enablement_table


def test_enablement_table_covers_every_route_id_exactly_once() -> None:
    """One verdict per routed id -- the binder walks the table."""
    table = menu_state.enablement_table(_baseline_state(RideStatus.RUNNING))

    assert len(table) == len(_ALL_ROUTE_IDS)
    assert set(table) == set(_ALL_ROUTE_IDS)


@given(state=_state_strategy())
def test_enablement_table_agrees_with_is_route_enabled_for_every_route(
    state: commands.RideState,
) -> None:
    """T-7: the table equals is_route_enabled per route id."""
    table = menu_state.enablement_table(state)

    for route in commands.ROUTE_TABLE:
        expected = commands.is_route_enabled(route, state)
        for item_id in route.ids:
            assert table[item_id] is expected


@pytest.mark.parametrize(
    ("label", "allowed"),
    _CORRECTION_CASES,
    ids=lambda value: value,
)
@pytest.mark.parametrize("status", STATUSES, ids=lambda status: status.value)
def test_enablement_table_correction_route_follows_ride_state(
    label: str, allowed: frozenset[RideStatus], *, status: RideStatus
) -> None:
    """Each correction row is enabled only in its §15 state."""
    table = menu_state.enablement_table(_baseline_state(status))

    assert table[_CORRECTION_ROUTES[label].ids[0]] is (status in allowed)


@pytest.mark.parametrize("status", STATUSES, ids=lambda status: status.value)
def test_enablement_table_edit_and_reassign_need_a_crossing(status: RideStatus) -> None:
    """Edit/Reassign's ≥1-crossing condition gates the binder too.

    The condition only ever enables within the row's §15 states
    (RUNNING · REOPENED): a DRAFT/FINISHED ride stays disabled
    regardless of crossings.
    """
    empty = commands.RideState(status=status, ride_open=True, crossings=0)
    one = commands.RideState(status=status, ride_open=True, crossings=1)
    allowed = status in (RideStatus.RUNNING, RideStatus.REOPENED)

    assert menu_state.enablement_table(empty)[ids.MI_EDIT_CROSSING] is False
    assert menu_state.enablement_table(one)[ids.MI_EDIT_CROSSING] is allowed
    assert menu_state.enablement_table(empty)[ids.MI_REASSIGN_PLATE] is False
    assert menu_state.enablement_table(one)[ids.MI_REASSIGN_PLATE] is allowed


@pytest.mark.parametrize(
    ("status", "ride_stopped", "expected"),
    [
        (RideStatus.DRAFT, False, True),
        (RideStatus.RUNNING, True, True),
        (RideStatus.RUNNING, False, False),
        (RideStatus.REOPENED, False, True),
        (RideStatus.FINISHED, True, False),
    ],
    ids=["draft", "stopped_running", "live_running", "reopened", "finished"],
)
def test_enablement_table_start_ride_follows_state_and_stop_gate(
    status: RideStatus, *, ride_stopped: bool, expected: bool
) -> None:
    """C2: mi_start_ride enables in DRAFT, stopped RUNNING and REOPENED.

    The binder applies the same rule ``commands.is_route_enabled``
    evaluates: REOPENED continues riding, a live RUNNING ride is not
    startable, and FINISHED is refused by state alone.
    """
    state = commands.RideState(status=status, ride_stopped=ride_stopped)

    table = menu_state.enablement_table(state)

    assert table[ids.MI_START_RIDE] is expected


@pytest.mark.parametrize(
    ("status", "ride_stopped", "expected"),
    [
        (RideStatus.DRAFT, False, True),
        (RideStatus.DRAFT, True, True),
        (RideStatus.RUNNING, False, False),
        (RideStatus.RUNNING, True, True),
        (RideStatus.FINISHED, True, True),
        (RideStatus.REOPENED, True, False),
    ],
    ids=["draft", "draft_stopped", "live_running", "stopped_running", "finished", "reopened"],
)
def test_enablement_table_clear_ride_follows_state_and_stop_gate(
    status: RideStatus, *, ride_stopped: bool, expected: bool
) -> None:
    """D3: mi_clear_ride enables in DRAFT, stopped RUNNING and FINISHED.

    A live RUNNING ride has to stop first, and a REOPENED ride has to
    finish first -- Clear resets the ride it targets.
    """
    state = commands.RideState(status=status, ride_stopped=ride_stopped)

    table = menu_state.enablement_table(state)

    assert table[ids.MI_CLEAR_RIDE] is expected


@pytest.mark.parametrize("status", STATUSES, ids=lambda status: status.value)
def test_enablement_table_edit_ride_follows_the_open_ride(status: RideStatus) -> None:
    """D2: mi_edit_ride is bound and open-ride-gated in every state."""
    table = menu_state.enablement_table(commands.RideState(status=status, ride_open=True))

    assert table[ids.MI_EDIT_RIDE] is True


def test_enablement_table_carries_no_row_for_the_retired_add_entry_id() -> None:
    """D4: mi_add_entry is no longer a routed (or bound) id."""
    table = menu_state.enablement_table(_baseline_state(RideStatus.RUNNING))

    assert "mi_add_entry" not in table


@pytest.mark.parametrize("status", STATUSES, ids=lambda status: status.value)
def test_enablement_table_void_card_needs_the_entry_to_have_cards(status: RideStatus) -> None:
    """Void Card's 'entry has cards' condition gates the binder too.

    The condition only ever enables within the row's §15 states
    (RUNNING · REOPENED): a DRAFT/FINISHED ride stays disabled
    regardless of the entry's cards.
    """
    no_cards = commands.RideState(status=status, ride_open=True, entry_has_cards=False)
    has_cards = commands.RideState(status=status, ride_open=True, entry_has_cards=True)
    allowed = status in (RideStatus.RUNNING, RideStatus.REOPENED)

    assert menu_state.enablement_table(no_cards)[ids.MI_VOID_CARD] is False
    assert menu_state.enablement_table(has_cards)[ids.MI_VOID_CARD] is allowed


def test_enablement_table_standings_needs_no_ride() -> None:
    """Part D: Standings is always enabled -- no ride needed."""
    state = commands.RideState(status=RideStatus.DRAFT, ride_open=False)

    table = menu_state.enablement_table(state)

    assert table[ids.MI_STANDINGS] is True


@pytest.mark.parametrize(
    ("html_exported", "pdf_exported", "expected_html", "expected_pdf"),
    [
        (False, False, False, False),
        (True, False, True, False),
        (False, True, False, True),
        (True, True, True, True),
    ],
    ids=["none", "html_only", "pdf_only", "both"],
)
def test_enablement_table_preview_rows_follow_their_own_format_export(  # noqa: PLR0913 -- the decision-table rows
    *, html_exported: bool, pdf_exported: bool, expected_html: bool, expected_pdf: bool
) -> None:
    """Part D: each Preview row reads only its own export flag."""
    state = dataclasses.replace(
        _baseline_state(RideStatus.FINISHED),
        html_exported=html_exported,
        pdf_exported=pdf_exported,
    )

    table = menu_state.enablement_table(state)

    assert table[ids.MI_PREVIEW_HTML_BROWSER] is expected_html
    assert table[ids.MI_PREVIEW_PDF_BROWSER] is expected_pdf


@pytest.mark.parametrize(
    "status",
    [RideStatus.DRAFT, RideStatus.RUNNING, RideStatus.REOPENED],
    ids=lambda status: status.value,
)
def test_enablement_table_preview_rows_stay_disabled_before_finish(status: RideStatus) -> None:
    """Part D: a recorded export never enables Preview outside FINISHED.

    A ride reopened mid-export keeps its recorded paths but both
    Preview rows stay off until the ride finishes again.
    """
    state = _baseline_state(status)

    table = menu_state.enablement_table(state)

    assert table[ids.MI_PREVIEW_HTML_BROWSER] is False
    assert table[ids.MI_PREVIEW_PDF_BROWSER] is False


# ------------------------------------------------- apply_to_menubar


class _FakeMenuItem:
    """A recording menu item: Enable(bool) records the verdict."""

    def __init__(self) -> None:
        """Start with no recorded verdict."""
        self.enabled: bool | None = None

    def Enable(self, enabled: bool) -> None:  # noqa: N802, FBT001 -- wx API names; positional bool
        """Record the enablement verdict."""
        self.enabled = enabled


class _FakeMenuBar:
    """A recording menubar: FindItem returns the matching item."""

    def __init__(self, item_ids: set[str], fake_ids: dict[str, int]) -> None:
        """Build one item per routed id, keyed by its fake real id."""
        self.items: dict[int, _FakeMenuItem] = {
            fake_ids[item_id]: _FakeMenuItem() for item_id in item_ids
        }

    def FindItem(  # noqa: N802 -- wx API name
        self, real_id: int
    ) -> tuple[_FakeMenuItem | None, object]:
        """Return the item for *real_id*, or (None, None) on a miss."""
        item = self.items.get(real_id)
        return (item, None) if item is not None else (None, None)


# A stable name -> fake-real-id map shared by every test: indices over
# the sorted route ids, so a menubar and an xrcid seam agree.
_FAKE_IDS: dict[str, int] = {
    item_id: index for index, item_id in enumerate(sorted(_ALL_ROUTE_IDS))
}


def _menubar_and_table(
    state: commands.RideState,
    *,
    omit: str | None = None,
) -> tuple[_FakeMenuBar, dict[str, bool]]:
    """Build a fake menubar over the ids and the expected table."""
    table = menu_state.enablement_table(state)
    present = set(table) if omit is None else set(table) - {omit}
    return _FakeMenuBar(present, _FAKE_IDS), table


def test_apply_to_menubar_enables_and_disables_every_item_to_match_state() -> None:
    """The binder flips each item's Enable() to the computed verdict."""
    state = _baseline_state(RideStatus.RUNNING)
    menubar, table = _menubar_and_table(state)

    menu_state.apply_to_menubar(menubar, state, xrcid=_FAKE_IDS.__getitem__)

    for item_id, expected in table.items():
        assert menubar.items[_FAKE_IDS[item_id]].enabled is expected


def test_apply_to_menubar_re_disables_an_item_on_a_later_state_change() -> None:
    """A later state change re-disables an item the binder enabled."""
    state = _baseline_state(RideStatus.RUNNING)
    menubar, _table = _menubar_and_table(state)
    menu_state.apply_to_menubar(menubar, state, xrcid=_FAKE_IDS.__getitem__)

    finished = _baseline_state(RideStatus.FINISHED)
    menu_state.apply_to_menubar(menubar, finished, xrcid=_FAKE_IDS.__getitem__)

    assert menubar.items[_FAKE_IDS[ids.MI_UNDO_CROSSING]].enabled is False


def test_apply_to_menubar_missing_item_is_a_silent_skip() -> None:
    """A FindItem miss (id with no live item) never crashes the walk."""
    state = _baseline_state(RideStatus.RUNNING)
    menubar, _table = _menubar_and_table(state, omit=ids.MI_UNDO_CROSSING)

    menu_state.apply_to_menubar(menubar, state, xrcid=_FAKE_IDS.__getitem__)

    assert menubar.items[_FAKE_IDS[ids.MI_EDIT_CROSSING]].enabled is True


def test_apply_to_menubar_default_seam_resolves_ids_lazily() -> None:
    """The no-xrcid path defers wx (require_wx) until the call runs.

    Pinning the seam's shape: ``apply_to_menubar`` must not import wx
    at module scope (the binder module stays headless-importable); the
    default resolver is looked up inside the call. The functional
    suite drives the real ``wx.xrc.XRCID`` resolver against a real
    menubar; here a fake resolver stands in for it.
    """
    state = _baseline_state(RideStatus.RUNNING)
    menubar, _table = _menubar_and_table(state)

    menu_state.apply_to_menubar(menubar, state, xrcid=_FAKE_IDS.__getitem__)

    assert menubar.items[_FAKE_IDS[ids.MI_DEAL_MANUAL]].enabled is True


def test_apply_to_menubar_without_a_seam_walks_real_wx_xrcid_ids() -> None:
    """The no-``xrcid`` path resolves through ``wx.xrc.XRCID`` itself.

    ``apply_to_menubar``'s default resolver is the real
    ``wx.xrc.XRCID`` -- the same id space a loaded ``wx.MenuBar`` keys
    its items with -- and a ``FindItem`` miss on every other routed id
    (no live menubar holds them here) is a silent skip, never a crash.
    """
    import wx.xrc  # noqa: PLC0415 -- the real resolver this one test pins

    state = _baseline_state(RideStatus.RUNNING)
    real_id = wx.xrc.XRCID(ids.MI_UNDO_CROSSING)
    menubar = _FakeMenuBar({ids.MI_UNDO_CROSSING}, {ids.MI_UNDO_CROSSING: real_id})

    menu_state.apply_to_menubar(menubar, state)

    assert menubar.items[real_id].enabled is True
