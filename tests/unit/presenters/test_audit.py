# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the E7.3.1 AuditPresenter (audit_dlg, R-38).

The audit trail dialog's two filters -- ``audit_search`` (plate OR
entry display name, resolved through the roster) and ``action_choice``
(one row per audited action) -- plus the entry-detail deep-link are
wired through ``AuditPresenter``: a wx-free coordinator holding
``(view, data_source, roster)``. Each handler re-reads
``data_source.audit_rows`` (newest first), narrows by the current
search text and selected action, and renders through
``AuditView.show_audit_rows``.

``action_choice`` is flat: ``audit.xrc`` declares "All actions" plus one
item per audited action (30 in all), so the filter keeps exactly the
selected action's rows -- there is no bucket mapping any more. These
tests pin the flat list against the ``.xrc``'s own declared order and
against every action the app can audit, then drive the filters against
a recording ``FakeAuditView`` and a fixed-row source (never wx -- R-71).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from defusedxml.ElementTree import parse

from rivercrossing import ride
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui.presenters.audit import (
    ACTION_CHOICES,
    ALL_ACTIONS,
    AuditPresenter,
    AuditView,
)
from rivercrossing.ui.presenters.data_source import AuditRow

XRC_DIR = Path(__file__).resolve().parents[3] / "src" / "rivercrossing" / "ui" / "xrc"

# The roster mutations the app can persist as an audit row: the rider
# editor's own action spellings (``Roster._log``'s call sites), which
# live below the engine and so appear in no ride module's set.
ROSTER_ACTIONS = frozenset(
    {
        "move_rider",
        "add_rider_to_team",
        "extract_rider_to_solo",
        "change_solo_plate",
        "change_pooled_rider_plate",
        "change_team_plate",
        "remove_rider",
    }
)

# Every action the app can put in the audit trail: the engine's own
# event actions (``ride.REPLAY_ACTIONS`` -- the mutations ``apply``
# dispatches, and so exactly the engine rows a rebuild may hand it)
# plus the roster mutations above. Derived from the engine's own set
# rather than re-listed here, so a new engine event cannot be silently
# forgotten: the filter list and this expectation move together.
AUDITED_ACTIONS = ride.REPLAY_ACTIONS | ROSTER_ACTIONS

# ------------------------------------------------------------- fakes


class FakeAuditView:
    """A recording ``AuditView`` spy for headless presenter tests."""

    def __init__(self) -> None:
        """Start every channel empty."""
        self.shown: list[list[AuditRow]] = []
        self.entry_filter: str | None = None

    def show_audit_rows(self, rows: list[AuditRow]) -> None:
        """Record the rendered row list."""
        self.shown.append(list(rows))

    def set_entry_filter(self, entry: str) -> None:
        """Record the pre-filled search text."""
        self.entry_filter = entry


class _AuditSource:
    """A minimal ``DataSource``-shaped source with fixed audit rows."""

    def __init__(self, rows: list[AuditRow]) -> None:
        """Wrap *rows* exactly as the source projects them."""
        self._rows = rows

    def audit_rows(self) -> list[AuditRow]:
        """Return the fixed rows, newest first (source-order)."""
        return list(self._rows)


# ----------------------------------------------------------- helpers


def _row(  # noqa: PLR0913, PLR0917 -- (action, entry, reason, when): one fixed row
    action: str, entry: str, reason: str = "", when: str = "14:00:00"
) -> AuditRow:
    """Build one fixed audit row."""
    return AuditRow(when=when, action=action, entry=entry, reason=reason)


def _roster() -> Roster:
    """Build a MIXED rider_pooled roster: one solo + one pooled team."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="J.", last_name="Okafor", plate="45")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    return roster


def _mixed_rows() -> list[AuditRow]:
    """Rows spanning several actions and the two roster entries."""
    return [
        _row("dnf", "45", reason="mechanical failure"),
        _row("shoe_reshuffle", "", reason="0 jokers added"),
        _row("deal_manual", "45", reason="flag confirmed"),
        _row("record_crossing", "45", reason="J. Okafor · solo"),
        _row("edit_crossing", "77", reason="mis-keyed time"),
        _row("start", "", reason="0:00:00"),
    ]


def _rows_for_every_action() -> list[AuditRow]:
    """One fixed row per :data:`ACTION_CHOICES` action, list order."""
    return [_row(action, "45") for _label, action in ACTION_CHOICES]


def _action_choice_labels() -> list[str]:
    """Return audit.xrc's action_choice entries, in declared order."""
    root = parse(XRC_DIR / "audit.xrc").getroot()
    choice = next(obj for obj in root.iter("object") if obj.get("name") == "action_choice")
    content = next(child for child in choice if child.tag == "content")
    return [item.text or "" for item in content.findall("item")]


# ------------------------------------------------------ construction


def test_fake_audit_view_satisfies_the_audit_view_protocol() -> None:
    """The recording fake structurally satisfies ``AuditView``."""
    assert isinstance(FakeAuditView(), AuditView)


def test_audit_presenter_constructor_renders_rows_newest_first() -> None:
    """Construction renders the source's rows in source order."""
    rows = _mixed_rows()
    view = FakeAuditView()

    presenter = AuditPresenter(view, _AuditSource(rows), roster=_roster())

    assert presenter.view is view
    assert view.shown[-1] == rows


def test_audit_presenter_without_a_roster_renders_all_rows() -> None:
    """No roster: rows still render; name search is unavailable."""
    view = FakeAuditView()

    AuditPresenter(view, _AuditSource(_mixed_rows()))

    assert view.shown[-1] == _mixed_rows()


def test_audit_presenter_given_no_rows_renders_an_empty_list() -> None:
    """T-4 empty collection: a ride with no events renders no rows."""
    view = FakeAuditView()

    AuditPresenter(view, _AuditSource([]), roster=_roster())

    assert view.shown[-1] == []


def test_audit_search_without_a_roster_matches_no_display_names() -> None:
    """A name search without a roster matches nothing (plate only)."""
    rows = [_row("record_crossing", "45"), _row("edit_crossing", "77", reason="mis-key")]
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(rows))

    presenter.on_search_text("okafor")

    assert view.shown[-1] == []


# ------------------------------------------------- search by plate


def test_audit_search_filters_by_plate() -> None:
    """Typing a plate narrows the list to that entry's rows."""
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(_mixed_rows()), roster=_roster())

    presenter.on_search_text("45")

    assert [row.entry for row in view.shown[-1]] == ["45", "45", "45"]


def test_audit_search_is_case_insensitive() -> None:
    """Plate search ignores case (a search box, not a code lookup)."""
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(_mixed_rows()), roster=_roster())

    presenter.on_search_text("77")

    assert [row.entry for row in view.shown[-1]] == ["77"]


def test_audit_search_clearing_restores_all_rows() -> None:
    """An emptied search box removes the entry filter."""
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(_mixed_rows()), roster=_roster())
    presenter.on_search_text("45")

    presenter.on_search_text("")

    assert view.shown[-1] == _mixed_rows()


# ----------------------------------------------- search (display name)


def test_audit_search_filters_by_entry_display_name() -> None:
    """A solo entry's display name matches its rows (plate + name)."""
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(_mixed_rows()), roster=_roster())

    presenter.on_search_text("okafor")

    assert [row.entry for row in view.shown[-1]] == ["45", "45", "45"]


def test_audit_search_matches_a_team_display_name_for_a_rider_plate() -> None:
    """A pooled rider's plate resolves to its team's display name."""
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(_mixed_rows()), roster=_roster())

    presenter.on_search_text("trail")

    assert [row.entry for row in view.shown[-1]] == ["77"]


def test_audit_search_ignores_reason_and_action_text() -> None:
    """Search narrows by plate/name only, never reason or action."""
    rows = [_row("edit_crossing", "45", reason="mis-keyed time")]
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(rows), roster=_roster())

    presenter.on_search_text("mis-keyed")

    assert view.shown[-1] == []


# ---------------------------------------------------- action choices


def test_action_choices_given_the_flat_canvas_order_match_audit_xrc() -> None:
    """The filter list and the dropdown are one list, in one order."""
    assert [label for label, _action in ACTION_CHOICES] == [
        label for label in _action_choice_labels() if label != ALL_ACTIONS
    ]


def test_action_choices_given_the_canvas_include_the_all_actions_item() -> None:
    """All actions stays the choice's first item, as declared."""
    assert _action_choice_labels()[0] == ALL_ACTIONS


def test_action_choices_given_every_audited_action_cover_it_exactly_once() -> None:
    """No audited action is missing, and none appears twice."""
    assert {action for _label, action in ACTION_CHOICES} == AUDITED_ACTIONS
    assert len(ACTION_CHOICES) == len(AUDITED_ACTIONS)


def test_action_choices_given_the_tiebreak_draw_offer_its_own_label() -> None:
    """R-14: the finish's own draw is a filterable action, titled."""
    labels = {action: label for label, action in ACTION_CHOICES}

    assert labels["tiebreak_draw"] == "High-card Draw"


def test_action_choices_given_the_dropdown_labels_are_unique() -> None:
    """Two rows cannot share a label: the choice would be ambiguous."""
    labels = [label for label, _action in ACTION_CHOICES]

    assert len(set(labels)) == len(labels)


def test_action_choices_given_the_all_actions_item_never_carries_an_action() -> None:
    """The All actions item is the no-filter value, never an action."""
    assert ALL_ACTIONS not in {action for _label, action in ACTION_CHOICES}
    assert ALL_ACTIONS not in {label for label, _action in ACTION_CHOICES}


@pytest.mark.parametrize(
    "action", [action for _label, action in ACTION_CHOICES], ids=[a for _l, a in ACTION_CHOICES]
)
def test_audit_action_choice_given_a_selected_action_keeps_only_its_rows(action: str) -> None:
    """T-13: every one of the 29 actions filters to its own rows."""
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(_rows_for_every_action()), roster=_roster())

    presenter.on_action_selected(action)

    assert [row.action for row in view.shown[-1]] == [action]


def test_audit_action_choice_defaults_to_all_actions() -> None:
    """The default choice filters nothing."""
    rows = _rows_for_every_action()
    view = FakeAuditView()

    AuditPresenter(view, _AuditSource(rows), roster=_roster())

    assert view.shown[-1] == rows


def test_audit_action_choice_all_actions_restores_every_row() -> None:
    """Selecting All actions clears the action filter."""
    rows = _rows_for_every_action()
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(rows), roster=_roster())
    presenter.on_action_selected("record_crossing")

    presenter.on_action_selected(ALL_ACTIONS)

    assert view.shown[-1] == rows


def test_audit_action_choice_given_an_unknown_action_keeps_nothing() -> None:
    """T-4 negative: an action no row carries matches no row."""
    rows = _rows_for_every_action()
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(rows), roster=_roster())

    presenter.on_action_selected("not_an_action")

    assert view.shown[-1] == []


# ---------------------------------------------------- combined filters


def test_audit_search_and_action_choice_combine() -> None:
    """Both filters narrow the same query (audit.xrc's own note)."""
    rows = [
        _row("record_crossing", "45"),
        _row("record_crossing", "77"),
        _row("edit_crossing", "45", reason="mis-keyed"),
        _row("dnf", "45", reason="mechanical"),
    ]
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(rows), roster=_roster())

    presenter.on_search_text("45")
    presenter.on_action_selected("record_crossing")

    assert [row.action for row in view.shown[-1]] == ["record_crossing"]


# ------------------------------------------------------------ deep-link


def test_audit_deep_link_prefills_search_and_filters_to_the_entry() -> None:
    """R-38: entry detail's audit button pre-filters to the entry."""
    view = FakeAuditView()

    AuditPresenter(view, _AuditSource(_mixed_rows()), roster=_roster(), entry_filter="45")

    assert view.entry_filter == "45"
    assert [row.entry for row in view.shown[-1]] == ["45", "45", "45"]


def test_audit_deep_link_given_an_action_filter_starts_on_it() -> None:
    """The deep-link can start on one action (a corrections route)."""
    view = FakeAuditView()

    AuditPresenter(
        view,
        _AuditSource(_mixed_rows()),
        roster=_roster(),
        entry_filter="45",
        action_filter="record_crossing",
    )

    assert [row.action for row in view.shown[-1]] == ["record_crossing"]
