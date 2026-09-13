# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the rider-issues dialog's card check (plan §10).

``rider_issues_dlg`` gained one read-only line above its issue summary:
the ride's shoe size against the crossings its field is estimated to
record. The verdict maths is pure and tested in ``test_ride.py``; this
module pins the wx-side halves without ever constructing a window --

* :meth:`RiderIssuesView.show_card_check` renders the three verdicts
  and hides the line for ``None``, driven through an
  ``object.__new__``-built view over a recording ``wx.StaticText``
  double (the same seam ``test_dialogs_positioning.py`` uses, since
  ``__init__`` binds the whole .xrc window and needs a desktop), and
* :func:`run_rider_issues_flow` threads the live config and the stored
  average rider speed into ``check_card_sufficiency`` and passes the
  result to the view, driven through stubbed toolkit seams (the loaded
  window, the shared ``dialogs`` helpers) so no ``wx.App`` is needed.
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from rivercrossing.ride import FAR_TOO_MANY, NOT_ENOUGH, OK, CardCheck, RideConfig
from rivercrossing.roster import Entry, EntryMode, EntryType, PlateModel, Rider, Roster
from rivercrossing.ui.views import rider_issues
from rivercrossing.ui.views.rider_issues import RiderIssuesView, run_rider_issues_flow

# The default shoe: 8 decks x (52 + 2 jokers) = 432 cards. The roster
# and speed below are chosen with a 1 km lap at 3600 km/h (one second
# per lap) so one pooled solo rider's expected count is exactly the
# configured planned_duration_s.
_DEFAULT_SHOE = 432


def _config(*, planned_duration_s: int = 21600) -> RideConfig:
    """Return a valid ride config for the card-check flow."""
    return RideConfig(
        name="GORBA EPIC 2026",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=1.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive local, RideConfig's contract
        planned_duration_s=planned_duration_s,
        min_lap_s=1080,
        entry_mode=EntryMode.SOLO,
        plate_model=PlateModel.RIDER_POOLED,
    )


def _one_rider_roster() -> Roster:
    """Return a pooled roster with one solo entry, one rider."""
    roster = Roster(entry_mode=EntryMode.SOLO, plate_model=PlateModel.RIDER_POOLED)
    roster.load_entries(
        [
            Entry(
                plate="7",
                display_name="Luca Ferrari",
                type=EntryType.SOLO,
                riders=[Rider(first_name="Luca", last_name="Ferrari", plate="7")],
            )
        ]
    )
    return roster


# ------------------------------------------- the view's render seam


class _FakeLabel:
    """A ``wx.StaticText`` double recording its label and visibility."""

    def __init__(self) -> None:
        """Start unlabelled and visible."""
        self.label: str | None = None
        self.hidden = False

    def SetLabel(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record the rendered label text."""
        self.label = text

    def Hide(self) -> None:  # noqa: N802 -- wx API name
        """Record a hide."""
        self.hidden = True

    def Show(self) -> None:  # noqa: N802 -- wx API name
        """Record a show."""
        self.hidden = False


def _view_with_label() -> tuple[RiderIssuesView, _FakeLabel]:
    """Return a real view over a fake ``card_check_lbl``.

    Built with ``object.__new__``: the render seam touches only
    ``card_check_lbl`` (``__init__`` binds every control the .xrc
    window carries and needs a desktop).
    """
    view = object.__new__(RiderIssuesView)
    label = _FakeLabel()
    view.card_check_lbl = label
    return view, label


@pytest.mark.parametrize(
    ("card_check", "expected_text"),
    [
        # 433 > 432: one crossing past the shoe's own capacity.
        (
            CardCheck(shoe_cards=_DEFAULT_SHOE, expected=433, verdict=NOT_ENOUGH),
            "Shoe holds 432 cards · estimated 433 crossings — NOT ENOUGH",
        ),
        # 432 > 2 * 215: more than double the estimated demand.
        (
            CardCheck(shoe_cards=_DEFAULT_SHOE, expected=215, verdict=FAR_TOO_MANY),
            "Shoe holds 432 cards · estimated 215 crossings — far too many (2×+)",  # noqa: RUF001 -- the SUT's own display glyph
        ),
        (
            CardCheck(shoe_cards=_DEFAULT_SHOE, expected=216, verdict=OK),
            "Shoe holds 432 cards · estimated 216 crossings — OK",
        ),
    ],
    ids=["not_enough", "far_too_many", "ok"],
)
def test_show_card_check_given_each_verdict_renders_its_label(
    card_check: CardCheck, expected_text: str
) -> None:
    """Each verdict renders its own sentence, with both numbers."""
    view, label = _view_with_label()

    view.show_card_check(card_check)

    assert (label.label, label.hidden) == (expected_text, False)


def test_show_card_check_given_no_check_hides_the_label() -> None:
    """T-3 negative: no verdict hides the line instead of staling it."""
    view, label = _view_with_label()

    view.show_card_check(None)

    assert label.hidden is True


def test_show_card_check_given_a_check_after_none_shows_the_label_again() -> None:
    """T-3: a hidden line is shown again when a verdict arrives."""
    view, label = _view_with_label()
    view.show_card_check(None)

    view.show_card_check(CardCheck(shoe_cards=_DEFAULT_SHOE, expected=216, verdict=OK))

    assert label.hidden is False


# ------------------------------------- the flow's config + speed seam


class _StubWindow:
    """A loaded ``rider_issues_dlg`` double the flow can destroy."""

    def __init__(self) -> None:
        """Start undestroyed."""
        self.destroyed = False

    def IsBeingDeleted(self) -> bool:  # noqa: N802 -- wx API name
        """Report the window is alive."""
        return False

    def Destroy(self) -> None:  # noqa: N802 -- wx API name
        """Record the destroy the flow's ``finally`` runs."""
        self.destroyed = True


_FLOW_VIEW_CALLS: list[tuple[object, Roster, CardCheck | None]] = []
"""Every ``_RecordingFlowView`` construction, cleared per stub setup."""


class _RecordingFlowView:
    """A ``RiderIssuesView`` stand-in recording its args."""

    def __init__(
        self, dialog: object, *, roster: Roster, card_check: CardCheck | None = None
    ) -> None:
        """Record the window, roster and verdict threaded in."""
        _FLOW_VIEW_CALLS.append((dialog, roster, card_check))


class _FakeXmlResource:
    """The ``wx.xrc.XmlResource`` double the flow loads from."""

    scripted: _FakeXmlResource | None = None

    def __init__(self, window: _StubWindow | None) -> None:
        """Return *window* from every LoadDialog call."""
        self.window = window

    def LoadDialog(self, _parent: object, _name: object) -> _StubWindow | None:  # noqa: N802
        """Return the scripted window (or None when unauthored)."""
        return self.window

    @classmethod
    def Get(cls) -> _FakeXmlResource | None:  # noqa: N802 -- wx API name
        """Return the scripted resource (wx's process-global seam)."""
        return cls.scripted


def _stub_toolkit(monkeypatch: pytest.MonkeyPatch) -> tuple[_StubWindow, _FakeXmlResource]:
    """Point the flow's toolkit seams at recording doubles."""
    window = _StubWindow()
    resource = _FakeXmlResource(window)
    _FakeXmlResource.scripted = resource
    _FLOW_VIEW_CALLS.clear()
    monkeypatch.setattr(rider_issues, "RiderIssuesView", _RecordingFlowView)
    monkeypatch.setattr(
        rider_issues, "wx", SimpleNamespace(xrc=SimpleNamespace(XmlResource=_FakeXmlResource))
    )
    monkeypatch.setattr(rider_issues.dialogs, "default_button_for", lambda _name: None)
    monkeypatch.setattr(rider_issues.dialogs, "run_dialog", lambda _dialog, **_kwargs: None)
    return window, resource


def _only_view_call() -> tuple[object, Roster, CardCheck | None]:
    """Return the one recorded flow view construction."""
    (call,) = _FLOW_VIEW_CALLS
    return call


def test_run_rider_issues_flow_given_config_and_speed_threads_the_card_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both seams supplied: the view is handed the computed verdict."""
    window, _resource = _stub_toolkit(monkeypatch)
    roster = _one_rider_roster()

    run_rider_issues_flow(
        object(), roster, config=_config(planned_duration_s=27), avg_speed_kmh=3600.0
    )

    assert _only_view_call() == (
        window,
        roster,
        CardCheck(shoe_cards=_DEFAULT_SHOE, expected=27, verdict=FAR_TOO_MANY),
    )


def test_run_rider_issues_flow_given_no_config_threads_no_card_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: no live ride config means no verdict line."""
    _stub_toolkit(monkeypatch)
    roster = _one_rider_roster()

    run_rider_issues_flow(object(), roster, avg_speed_kmh=3600.0)

    assert _only_view_call()[2] is None


def test_run_rider_issues_flow_given_no_speed_threads_no_card_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: no stored speed means no verdict line."""
    _stub_toolkit(monkeypatch)
    roster = _one_rider_roster()

    run_rider_issues_flow(object(), roster, config=_config())

    assert _only_view_call()[2] is None


def test_run_rider_issues_flow_given_the_defaults_threads_no_card_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both keywords omitted is the pre-plan-§10 call shape."""
    _stub_toolkit(monkeypatch)
    roster = _one_rider_roster()

    run_rider_issues_flow(object(), roster)

    assert _only_view_call()[2] is None


def test_run_rider_issues_flow_given_no_roster_change_reports_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A report that fixes nothing reports no roster change."""
    _stub_toolkit(monkeypatch)

    changed = run_rider_issues_flow(
        object(), _one_rider_roster(), config=_config(), avg_speed_kmh=3600.0
    )

    assert changed is False


def test_run_rider_issues_flow_given_an_unauthored_dialog_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: LoadDialog returning None is a silent no-op."""
    _window, resource = _stub_toolkit(monkeypatch)
    resource.window = None
    roster = _one_rider_roster()

    changed = run_rider_issues_flow(object(), roster)

    assert changed is False
    assert _FLOW_VIEW_CALLS == []
