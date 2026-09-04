# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for the audit trail viewer (E7.3.1, R-38).

``audit_dlg`` is a plain XRC dialog until E7.3.1: the viewer binds
``audit_list``'s five columns and rows, ``audit_search`` (plate OR
entry display name) and ``action_choice`` (the §15-D buckets) through
``AuditDialog`` + ``AuditPresenter``, and the entry-detail deep-link
pre-fills the search. What only a real, loaded wx session can prove
lives here:

* the three filter controls resolve inside the authored XRC;
* rows render newest-first into ``audit_list``;
* typing in ``audit_search`` narrows by plate and by display name;
* choosing an ``action_choice`` bucket narrows by action;
* the two filters combine;
* the deep-link pre-fills ``audit_search`` and narrows to the entry;
* a 1000-row trail renders within the perf budget.

Like the rest of ``tests/functional/`` these run only in the Tart VM,
never directly on the host.
"""

import time

import harness
import pages
import pytest

from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui import ids
from rivercrossing.ui.presenters.data_source import AuditRow
from rivercrossing.ui.views.audit import AuditDialog

pytestmark = pytest.mark.functional

# The 1000-row perf budget (seconds): deliberately generous -- the
# point is to catch an O(n^2) render regression, not to shave
# milliseconds off a trivial 5-column list.
_PERF_BUDGET_S = 5.0

_COL_ACTION = 2
_COL_ENTRY = 3
_COL_REASON = 4

# E7.3.1's §15-D buckets, as the presenter encodes them (the same
# mapping the unit suite pins); the functional run drives the real
# wxChoice through it.
_ACTION_BUCKET_LABELS = ("Crossing edits", "Card deals/voids", "Moves", "DNF", "Shoe reshuffle")


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


def _row(  # noqa: PLR0913, PLR0917 -- (action, entry, reason, when): one audit row
    action: str, entry: str, reason: str = "", when: str = "14:00:00"
) -> AuditRow:
    """Build one audit row (who is always the scorer)."""
    return AuditRow(when=when, who="scorer", action=action, entry=entry, reason=reason)


class _AuditSource:
    """DataSource-shaped source over a fixed newest-first row list."""

    def __init__(self, rows: list[AuditRow]) -> None:
        """Wrap *rows* exactly as the source projects them."""
        self._rows = rows

    def audit_rows(self) -> list[AuditRow]:
        """Return the fixed rows, newest first."""
        return list(self._rows)


def _open_audit(  # noqa: PLR0913 -- (xrc_resource, rows, roster, entry_filter)
    xrc_resource: object,
    *,
    rows: list[AuditRow],
    roster: Roster | None = None,
    entry_filter: str = "",
) -> AuditDialog:
    """Load ``audit_dlg`` and bind the real viewer over *rows*."""
    window = harness.load_window_verified(xrc_resource, ids.AUDIT_DLG, frame=False)
    window.Show()
    harness.pump()
    return AuditDialog(
        window,
        data_source=_AuditSource(rows),
        roster=roster,
        entry_filter=entry_filter,
    )


def _visible_rows(view: AuditDialog) -> list[tuple[str, ...]]:
    """Return audit_list's rows as five-column tuples, top first."""
    model = view.audit_list.GetModel()
    return [
        tuple(model.GetValueByRow(row, col) for col in range(5)) for row in range(model.GetCount())
    ]


# ------------------------------------------- §15b names resolve


def test_audit_dialog_resolves_its_frozen_controls(xrc_resource: object) -> None:
    """Every §15b-registered name resolves inside audit_dlg."""
    window = harness.load_window_verified(xrc_resource, ids.AUDIT_DLG, frame=False)

    try:
        resolved = {
            name: harness.find_control(window, name).GetName()
            for name in (ids.AUDIT_SEARCH, ids.ACTION_CHOICE, ids.AUDIT_LIST, pages.WX_ID_CLOSE)
        }
    finally:
        harness.close_window(window)

    assert resolved == {
        ids.AUDIT_SEARCH: ids.AUDIT_SEARCH,
        ids.ACTION_CHOICE: ids.ACTION_CHOICE,
        ids.AUDIT_LIST: ids.AUDIT_LIST,
        pages.WX_ID_CLOSE: pages.WX_ID_CLOSE,
    }


def test_audit_action_choice_lists_the_five_buckets(xrc_resource: object) -> None:
    """action_choice carries exactly the §15-D bucket labels."""
    view = _open_audit(
        xrc_resource,
        rows=[],
        roster=_roster(),
    )

    try:
        labels = list(view.action_choice.GetStrings())
    finally:
        harness.close_window(view.dialog)

    assert labels == ["All actions", *_ACTION_BUCKET_LABELS]


# --------------------------------------------------------- rendering


def test_audit_list_renders_rows_newest_first(xrc_resource: object) -> None:
    """audit_list draws the source's rows top-first, five columns."""
    rows = [
        _row("record_crossing", "45"),
        _row("edit_crossing", "77", reason="mis-key"),
        _row("start", ""),
    ]
    view = _open_audit(xrc_resource, rows=rows, roster=_roster())

    try:
        visible = _visible_rows(view)
    finally:
        harness.close_window(view.dialog)

    assert [row[_COL_ACTION] for row in visible] == ["record_crossing", "edit_crossing", "start"]
    assert visible[0][_COL_ENTRY] == "45"
    assert visible[0][_COL_REASON] == ""


# --------------------------------------------------- search filtering


def test_audit_search_filters_by_plate(xrc_resource: object) -> None:
    """Typing a plate into audit_search narrows the list to it."""
    rows = [
        _row("record_crossing", "45"),
        _row("edit_crossing", "77", reason="mis-key"),
        _row("dnf", "45", reason="mechanical"),
    ]
    view = _open_audit(xrc_resource, rows=rows, roster=_roster())

    try:
        harness.type_text(view.dialog, ids.AUDIT_SEARCH, "45")
        entries = [row[_COL_ENTRY] for row in _visible_rows(view)]
    finally:
        harness.close_window(view.dialog)

    assert entries == ["45", "45"]


def test_audit_search_filters_by_entry_display_name(xrc_resource: object) -> None:
    """A display-name search narrows the list (plate + name ruling)."""
    rows = [
        _row("record_crossing", "45"),
        _row("edit_crossing", "77", reason="mis-key"),
    ]
    view = _open_audit(xrc_resource, rows=rows, roster=_roster())

    try:
        harness.type_text(view.dialog, ids.AUDIT_SEARCH, "okafor")
        entries = [row[_COL_ENTRY] for row in _visible_rows(view)]
    finally:
        harness.close_window(view.dialog)

    assert entries == ["45"]


# ------------------------------------------------- action filtering


def test_audit_action_choice_filters_by_bucket(xrc_resource: object) -> None:
    """Choosing Crossing edits keeps only that bucket's actions."""
    rows = [
        _row("record_crossing", "45"),
        _row("edit_crossing", "77", reason="mis-key"),
        _row("dnf", "45", reason="mechanical"),
        _row("deal_manual", "45", reason="flag confirmed"),
    ]
    view = _open_audit(xrc_resource, rows=rows, roster=_roster())

    try:
        harness.select_choice(view.dialog, ids.ACTION_CHOICE, "Crossing edits")
        actions = [row[_COL_ACTION] for row in _visible_rows(view)]
    finally:
        harness.close_window(view.dialog)

    assert actions == ["record_crossing", "edit_crossing"]


def test_audit_search_and_action_choice_combine(xrc_resource: object) -> None:
    """Both filters narrow the same query."""
    rows = [
        _row("record_crossing", "45"),
        _row("edit_crossing", "77", reason="mis-key"),
        _row("dnf", "45", reason="mechanical"),
    ]
    view = _open_audit(xrc_resource, rows=rows, roster=_roster())

    try:
        harness.type_text(view.dialog, ids.AUDIT_SEARCH, "45")
        harness.select_choice(view.dialog, ids.ACTION_CHOICE, "Crossing edits")
        actions = [row[_COL_ACTION] for row in _visible_rows(view)]
    finally:
        harness.close_window(view.dialog)

    assert actions == ["record_crossing"]


# ------------------------------------------------------- deep-link


def test_audit_deep_link_prefilters_to_the_entry(xrc_resource: object) -> None:
    """R-38: entry detail's audit button pre-filters to the entry."""
    rows = [
        _row("record_crossing", "45"),
        _row("edit_crossing", "77", reason="mis-key"),
        _row("dnf", "45", reason="mechanical"),
    ]
    view = _open_audit(xrc_resource, rows=rows, roster=_roster(), entry_filter="45")

    try:
        search_value = harness.find_control(view.dialog, ids.AUDIT_SEARCH).GetValue()
        entries = [row[_COL_ENTRY] for row in _visible_rows(view)]
    finally:
        harness.close_window(view.dialog)

    assert search_value == "45"
    assert entries == ["45", "45"]


# ------------------------------------------------------ perf budget


def test_audit_list_renders_a_thousand_rows_within_budget(
    xrc_resource: object,
) -> None:
    """A 1000-row trail renders inside the perf budget (R-38)."""
    rows = [_row("record_crossing", f"plate-{i}") for i in range(1000)]

    start = time.monotonic()
    view = _open_audit(xrc_resource, rows=rows, roster=_roster())
    elapsed = time.monotonic() - start

    try:
        count = view.audit_list.GetModel().GetCount()
    finally:
        harness.close_window(view.dialog)

    assert count == 1000
    assert elapsed < _PERF_BUDGET_S
