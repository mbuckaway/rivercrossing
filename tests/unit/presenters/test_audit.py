# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the E7.3.1 AuditPresenter (audit_dlg, R-38).

The audit trail dialog's two filters -- ``audit_search`` (plate OR
entry display name, resolved through the roster) and ``action_choice``
(§15-D action buckets) -- plus the entry-detail deep-link are wired
through ``AuditPresenter``: a wx-free coordinator holding ``(view,
data_source, roster)``. Each handler re-reads
``data_source.audit_rows`` (newest first), narrows by the current
search text and bucket, and renders through
``AuditView.show_audit_rows``.

These tests drive the presenter against a recording ``FakeAuditView``
and a fixed-row source (never wx -- R-71): newest-first rendering,
plate search, display-name search (solo and pooled team), the
All-actions default, every §15-D bucket, the combined filters, and the
deep-link pre-filter.
"""

import pytest

from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui.presenters.audit import (
    ACTION_BUCKETS,
    ALL_ACTIONS,
    AuditPresenter,
    AuditView,
)
from rivercrossing.ui.presenters.data_source import AuditRow

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
    """Build one fixed audit row (who is always the scorer)."""
    return AuditRow(when=when, who="scorer", action=action, entry=entry, reason=reason)


def _roster() -> Roster:
    """Build a MIXED rider_pooled roster: one solo + one pooled team."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(name="J. Okafor", plate="45")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[Rider(name="A. Roy", plate="77"), Rider(name="K. Singh", plate="78")],
    )
    return roster


def _mixed_rows() -> list[AuditRow]:
    """Rows spanning several buckets and the two roster entries."""
    return [
        _row("dnf", "45", reason="mechanical failure"),
        _row("shoe_reshuffle", ""),
        _row("deal_manual", "45", reason="flag confirmed"),
        _row("record_crossing", "45"),
        _row("edit_crossing", "77", reason="mis-keyed time"),
        _row("start", ""),
    ]


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


# ---------------------------------------------------- action buckets


_BUCKET_ROWS = [
    _row("start", ""),
    _row("record_crossing", "45"),
    _row("edit_crossing", "77", reason="mis-keyed"),
    _row("undo", "77"),
    _row("void_crossing", "77", reason="double entry"),
    _row("add_crossing_at", "45", reason="missed"),
    _row("reassign", "77", reason="wrong plate"),
    _row("deal_manual", "45", reason="flag confirmed"),
    _row("confirm_held", "45"),
    _row("void_held", "45"),
    _row("void_card", "45", reason="wrong card"),
    _row("dnf", "45", reason="mechanical"),
    _row("shoe_reshuffle", ""),
    _row("stop", ""),
]


@pytest.mark.parametrize(
    ("bucket", "expected_actions"),
    [
        (
            "Crossing edits",
            [
                "record_crossing",
                "edit_crossing",
                "undo",
                "void_crossing",
                "add_crossing_at",
                "reassign",
            ],
        ),
        ("Card deals/voids", ["deal_manual", "confirm_held", "void_held", "void_card"]),
        ("Moves", []),
        ("DNF", ["dnf"]),
        ("Shoe reshuffle", ["shoe_reshuffle"]),
    ],
)
def test_audit_action_choice_filters_by_bucket(bucket: str, expected_actions: list[str]) -> None:
    """Selecting a §15-D bucket keeps exactly that bucket's actions."""
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(_BUCKET_ROWS), roster=_roster())

    presenter.on_action_selected(bucket)

    assert [row.action for row in view.shown[-1]] == expected_actions


def test_audit_action_choice_defaults_to_all_actions() -> None:
    """The default bucket filters nothing."""
    view = FakeAuditView()

    AuditPresenter(view, _AuditSource(_BUCKET_ROWS), roster=_roster())

    assert view.shown[-1] == _BUCKET_ROWS


def test_audit_action_choice_all_actions_restores_every_row() -> None:
    """Selecting All actions clears the bucket filter."""
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(_BUCKET_ROWS), roster=_roster())
    presenter.on_action_selected("Crossing edits")

    presenter.on_action_selected(ALL_ACTIONS)

    assert view.shown[-1] == _BUCKET_ROWS


# ---------------------------------------------------- combined filters


def test_audit_search_and_action_bucket_combine() -> None:
    """Both filters narrow the same query (audit.xrc's own note)."""
    rows = [
        _row("record_crossing", "45"),
        _row("edit_crossing", "77", reason="mis-keyed"),
        _row("dnf", "45", reason="mechanical"),
    ]
    view = FakeAuditView()
    presenter = AuditPresenter(view, _AuditSource(rows), roster=_roster())

    presenter.on_search_text("45")
    presenter.on_action_selected("Crossing edits")

    assert [row.action for row in view.shown[-1]] == ["record_crossing"]


# ------------------------------------------------------------ deep-link


def test_audit_deep_link_prefills_search_and_filters_to_the_entry() -> None:
    """R-38: entry detail's audit button pre-filters to the entry."""
    view = FakeAuditView()

    AuditPresenter(view, _AuditSource(_mixed_rows()), roster=_roster(), entry_filter="45")

    assert view.entry_filter == "45"
    assert [row.entry for row in view.shown[-1]] == ["45", "45", "45"]


# ------------------------------------------------------------- mapping


def test_action_buckets_cover_the_correction_vocabulary_without_overlap() -> None:
    """The §15-D mapping is the task's action sets, no overlaps."""
    union = set().union(*ACTION_BUCKETS.values())

    assert union == {
        "record_crossing",
        "undo",
        "edit_crossing",
        "void_crossing",
        "add_crossing_at",
        "reassign",
        "deal_manual",
        "confirm_held",
        "void_held",
        "void_card",
        "move_rider",
        "add_rider_to_team",
        "extract_rider_to_solo",
        "change_solo_plate",
        "change_pooled_rider_plate",
        "change_team_plate",
        "dnf",
        "shoe_reshuffle",
    }
    assert sum(len(actions) for actions in ACTION_BUCKETS.values()) == len(union)
    assert ALL_ACTIONS not in ACTION_BUCKETS
    assert all(ACTION_BUCKETS.values())
