# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the crossing-detail workstream (J2).

Double-clicking a row in the console's ``crossings_list`` opens
``crossing_detail_dlg`` on that crossing. Three pieces are pinned here,
all without a display:

* **The field mapping** -- :func:`crossing_detail.build_fields` is a
  pure function over the live ``Crossing``, ``Roster`` and
  ``RideEngine`` (real domain objects, not doubles: they are the
  subject, not an I/O boundary -- T-10), so rider/team/plate/lap/times/
  card/held are asserted directly.
* **The view's three handlers** -- ``_on_ok`` (commit an edited plate
  or close), ``_on_delete`` (undo the newest crossing, void any other)
  and ``_on_edit`` (the §9 Number prompt, whose own loader
  :func:`crossing_detail.run_number_dialog` is pinned against a stub
  resource) -- driven against recording widget doubles built with
  ``object.__new__`` (``test_dialogs_positioning.py``'s precedent).
* **The two seams this workstream adds** -- ``MainFrame.
  set_on_open_crossing`` / ``_on_crossing_activated`` and the app's
  ``_crossing_for_feed_row`` / ``_feed_row_target``, whose row
  arithmetic unwinds the whole-ride (uncapped, Phase 4) console feed.

Real-window geometry, the loaded dialog's controls and click-through
behaviour need a real window and are not pinned here.
"""

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

import pytest
import wx
from hypothesis import given
from hypothesis import strategies as st

from conftest import gorba_config
from rivercrossing.cards import Card, Shoe
from rivercrossing.ride import Crossing, PendingMiss, RideEngine, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids, std_dialogs
from rivercrossing.ui.card_text import format_card
from rivercrossing.ui.presenters.data_source import EngineDataSource
from rivercrossing.ui.views import crossing_detail, dialogs, main_frame

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.ui.presenters.data_source import FeedRow


# ------------------------------------------------------- builders


def _dt(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Build a naive datetime on the fixed event day, Sept 20, 2026."""
    return datetime(2026, 9, 20, hour, minute, second)  # noqa: DTZ001 -- naive by design


def _frozen_clock() -> datetime:
    """Return the event start; tests stamp crossings explicitly."""
    return _dt(10, 0)


def _running_engine(
    roster: Roster, *, min_lap_s: int = 1, hold_short_laps: bool = False
) -> RideEngine:
    """Build a RUNNING engine over *roster* on the GORBA config."""
    config = gorba_config(min_lap_s=min_lap_s, hold_short_laps=hold_short_laps)
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    engine = RideEngine(config=config, shoe=shoe, clock=_frozen_clock, roster=roster)
    engine.start()
    return engine


def _solo_roster(name: str = "Amy", plate: str = "12") -> Roster:
    """Build a one-solo-entry roster."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name=name, plate=plate)
    return roster


def _two_solo_roster() -> Roster:
    """Build a roster holding Amy (12) and Bob (34)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Amy", plate="12")
    roster.create_solo_entry(first_name="Bob", plate="34")
    return roster


def _pooled_team_roster() -> Roster:
    """Build a rider_pooled team roster: Sarah (45), Priya (9)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(first_name="Sarah", plate="45"), Rider(first_name="Priya", plate="9")],
    )
    return roster


def _relay_team_roster() -> Roster:
    """Build a team_relay roster whose riders carry no plate (S1)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(first_name="Sarah"), Rider(first_name="Priya")],
        plate="9",
    )
    return roster


# ------------------------------------------------ the field mapping


def test_build_fields_given_a_solo_crossing_maps_every_field() -> None:
    """A solo crossing renders its rider, entry, lap and times."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))

    fields = crossing_detail.build_fields(engine.crossings[0], roster, engine)

    assert (fields.rider, fields.team, fields.plate, fields.lap) == ("Amy", "Amy", "12", "1")
    assert (fields.time, fields.lap_time, fields.total) == ("10:02:00", "2:00", "0:02:00")


def test_build_fields_given_a_pooled_team_crossing_names_the_typing_rider() -> None:
    """J1: the detail names the rider whose plate was typed."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 2))

    fields = crossing_detail.build_fields(engine.crossings[0], roster, engine)

    assert (fields.rider, fields.team, fields.plate) == ("Sarah", "Dirt Dynamos", "45")


def test_build_fields_given_a_relay_crossing_falls_back_to_the_entry() -> None:
    """Relay riders own no plate, so the entry identity stands."""
    roster = _relay_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("9", at=_dt(10, 2))

    fields = crossing_detail.build_fields(engine.crossings[0], roster, engine)

    assert (fields.rider, fields.team, fields.plate) == ("Dirt Dynamos", "Dirt Dynamos", "9")


def test_build_fields_given_a_second_lap_sums_the_running_total() -> None:
    """Total is the entry's running total, not the lap time."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))

    fields = crossing_detail.build_fields(engine.crossings[1], roster, engine)

    assert (fields.lap, fields.lap_time, fields.total) == ("2", "3:00", "0:05:00")


def test_build_fields_given_a_credited_card_reports_it_credited() -> None:
    """W4's always-deal default credits the card; the dialog says so."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))

    fields = crossing_detail.build_fields(engine.crossings[0], roster, engine)

    assert fields.held == "Credited"
    assert fields.card == format_card(engine.card_for(engine.crossings[0]).code())


def test_build_fields_given_a_held_card_reports_it_held() -> None:
    """R-34: a short lap under hold_short_laps holds its card."""
    roster = _solo_roster()
    engine = _running_engine(roster, min_lap_s=1080, hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 2))
    crossing = engine.crossings[0]
    held = engine.held_card_for(crossing)
    assert held is not None

    fields = crossing_detail.build_fields(crossing, roster, engine)

    assert fields.held == "Held — short lap awaiting review"
    assert fields.card == format_card(held.code())


def test_build_fields_given_a_voided_card_reports_it_voided() -> None:
    """A voided card is in neither the hold queue nor the hand."""
    roster = _solo_roster()
    engine = _running_engine(roster, min_lap_s=1080, hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 2))
    crossing = engine.crossings[0]
    engine.void_held(crossing)

    fields = crossing_detail.build_fields(crossing, roster, engine)

    assert fields.held == "Voided"


class _StubEngine:
    """A ``RideEngine`` double for the field builder's read surface."""

    def __init__(  # noqa: PLR0913 -- (lap_times, card, held, credited, state) scripted reads
        self,
        *,
        lap_times: tuple[float, ...] = (),
        card: str = "AS",
        held: str | None = None,
        credited: tuple[str, ...] = (),
        state: RideStatus = RideStatus.RUNNING,
    ) -> None:
        """Script the deal, the hold queue, the hand and state."""
        self.state = state
        self._lap_times = lap_times
        self._card = Card.parse(card)
        self._held = Card.parse(held) if held is not None else None
        self._credited = tuple(Card.parse(code) for code in credited)

    def lap_times(self, entry_id: str) -> tuple[float, ...]:  # noqa: ARG002
        """Return the scripted lap times."""
        return self._lap_times

    def card_for(self, crossing: Crossing) -> Card:  # noqa: ARG002
        """Return the scripted dealt card."""
        return self._card

    def held_card_for(self, crossing: Crossing) -> Card | None:  # noqa: ARG002
        """Return the scripted held card, if any."""
        return self._held

    def credited_cards(self, plate: str) -> tuple[Card, ...]:  # noqa: ARG002
        """Return the scripted credited hand."""
        return self._credited


def test_build_fields_given_a_seq_past_the_recorded_laps_renders_zero_times() -> None:
    """T-4 boundary: a stale seq cannot index past lap_times."""
    crossing = Crossing(entry_id="12", seq=3, crossed_at=_dt(10, 5), rider_plate="12")
    engine = _StubEngine(lap_times=(100.0, 120.0), credited=("AS",))

    fields = crossing_detail.build_fields(crossing, _solo_roster(), engine)

    assert (fields.lap_time, fields.total) == ("0:00", "0:00:00")


def test_build_fields_given_an_unknown_entry_falls_back_to_the_entry_id() -> None:
    """A crossing whose entry left the roster still renders its id."""
    crossing = Crossing(entry_id="99", seq=1, crossed_at=_dt(10, 5), rider_plate="99")
    engine = _StubEngine(lap_times=(60.0,), credited=("AS",))

    fields = crossing_detail.build_fields(crossing, _solo_roster(), engine)

    assert (fields.rider, fields.team, fields.plate) == ("99", "99", "99")


def test_build_fields_given_no_rider_plate_falls_back_to_the_entry_plate() -> None:
    """A crossing with no typed plate shows the entry's own plate."""
    crossing = Crossing(entry_id="12", seq=1, crossed_at=_dt(10, 5))
    engine = _StubEngine(lap_times=(60.0,), credited=("AS",))

    fields = crossing_detail.build_fields(crossing, _solo_roster(), engine)

    assert (fields.rider, fields.plate) == ("Amy", "12")


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-1.0, "0:00"),  # T-4 min - 1: a negative lap clamps to zero
        (0.0, "0:00"),  # T-4 min
        (1.0, "0:01"),  # T-4 min + 1
        (59.0, "0:59"),  # the last m:ss second below a minute
        (60.0, "1:00"),  # the first whole minute
        (3599.0, "59:59"),  # T-4 max - 1: still m:ss
        (3600.0, "1:00:00"),  # T-4 max: the hour switches the format
        (7200.0, "2:00:00"),  # T-4 max + 1
    ],
    ids=[
        "negative",
        "zero",
        "one_second",
        "fifty_nine",
        "one_minute",
        "under_hour",
        "one_hour",
        "two_hours",
    ],
)
def test_build_fields_given_a_lap_time_renders_the_feeds_own_m_ss_format(
    seconds: float, expected: str
) -> None:
    """T-4 boundaries: m:ss, switching to h:mm:ss at the hour."""
    crossing = Crossing(entry_id="12", seq=1, crossed_at=_dt(10, 5), rider_plate="12")
    engine = _StubEngine(lap_times=(seconds,), credited=("AS",))

    fields = crossing_detail.build_fields(crossing, _solo_roster(), engine)

    assert fields.lap_time == expected


# ------------------------------------------------- the Delete gate


def test_is_last_crossing_given_an_empty_engine_is_false() -> None:
    """T-4 boundary: nothing recorded means nothing to delete."""
    roster = _solo_roster()
    engine = _running_engine(roster)

    assert crossing_detail.is_last_crossing(engine, Crossing("12", 1, _dt(10, 0))) is False


def test_is_last_crossing_given_the_only_crossing_is_true() -> None:
    """T-4 boundary: a single crossing is both first and last."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))

    assert crossing_detail.is_last_crossing(engine, engine.crossings[0]) is True


def test_is_last_crossing_given_an_earlier_crossing_is_false() -> None:
    """Delete is offered only for the newest crossing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))

    assert crossing_detail.is_last_crossing(engine, engine.crossings[0]) is False


def test_is_last_crossing_given_the_newest_of_many_is_true() -> None:
    """T-4 boundary: the last of several is the deletable one."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("34", at=_dt(10, 3))
    engine.record_crossing("12", at=_dt(10, 6))

    assert crossing_detail.is_last_crossing(engine, engine.crossings[-1]) is True


# --------------------------------------------------------- doubles


class _RecordingDialog:
    """A ``wx.Dialog`` double recording the ids it ended on."""

    def __init__(self) -> None:
        """Start with no modal result recorded."""
        self.modal_ids: list[int] = []
        self.layouts = 0

    def EndModal(self, modal_id: int) -> None:  # noqa: N802
        """Record the id the view ended the dialog with."""
        self.modal_ids.append(modal_id)

    def Layout(self) -> None:  # noqa: N802
        """Record the relayout the refusal path asks for."""
        self.layouts += 1


class _RecordingLabel:
    """A ``wx.StaticText`` double recording the text it was given."""

    def __init__(self) -> None:
        """Start blank."""
        self.label = ""

    def SetLabel(self, text: str) -> None:  # noqa: N802
        """Record the rendered text."""
        self.label = text


class _RecordingText:
    """A ``wx.TextCtrl`` double recording value and enablement."""

    def __init__(self, value: str = "") -> None:
        """Start holding *value*."""
        self._value = value
        self.enabled: bool | None = None
        self.focused = False
        self.selected = False

    def GetValue(self) -> str:  # noqa: N802
        """Return the field's current text."""
        return self._value

    def SetValue(self, value: str) -> None:  # noqa: N802
        """Replace the field's text."""
        self._value = value

    def Enable(self, enabled: bool = True) -> None:  # noqa: N802, FBT001, FBT002
        """Record the enablement the view applied."""
        self.enabled = enabled

    def SetFocus(self) -> None:  # noqa: N802
        """Record that the view focused the field."""
        self.focused = True

    def SelectAll(self) -> None:  # noqa: N802
        """Record that the view selected the field's text."""
        self.selected = True


class _RecordingButton:
    """A ``wx.Button`` double recording its enablement."""

    def __init__(self) -> None:
        """Start with no enablement applied."""
        self.enabled: bool | None = None

    def Enable(self, enabled: bool = True) -> None:  # noqa: N802, FBT001, FBT002
        """Record the enablement the view applied."""
        self.enabled = enabled


class _RecordingInfoBar:
    """A ``wx.InfoBar`` double recording refusals."""

    def __init__(self) -> None:
        """Start with no message shown."""
        self.messages: list[str] = []

    def ShowMessage(self, message: str, icon: int = 0) -> None:  # noqa: N802, ARG002
        """Record the refusal text."""
        self.messages.append(message)

    def Dismiss(self) -> None:  # noqa: N802
        """No-op; the view never re-renders to dismiss a bar."""
        return


class _RecordingEvent:
    """A ``wx.Event`` double recording a handler's Skip."""

    def __init__(self) -> None:
        """Start before the handler ran."""
        self.skipped = False

    def Skip(self) -> None:  # noqa: N802
        """Record that the handler let the event continue."""
        self.skipped = True


# The nine read-only value labels, in the dialog's own canvas order.
_LABEL_ATTRS = (
    "crossing_rider_lbl",
    "crossing_team_lbl",
    "crossing_plate_lbl",
    "crossing_lap_lbl",
    "crossing_time_lbl",
    "crossing_lap_time_lbl",
    "crossing_total_lbl",
    "crossing_card_lbl",
    "crossing_held_lbl",
)


def _view(  # noqa: PLR0913 -- (engine, roster, crossing, plate) all matter
    engine: RideEngine | _StubEngine,
    *,
    roster: Roster,
    crossing: Crossing | None = None,
    plate: str = "",
) -> crossing_detail.CrossingDetailView:
    """Return a ``CrossingDetailView`` over recording widget doubles.

    Built with ``object.__new__`` (``test_dialogs_positioning.py``'s
    ``_view_over`` precedent): the three handlers under test touch only
    these attributes, so no desktop and no ``__init__`` control binding
    is needed.
    """
    view = object.__new__(crossing_detail.CrossingDetailView)
    view.dialog = _RecordingDialog()
    view.crossing = crossing if crossing is not None else engine.crossings[-1]
    view.roster = roster
    view.engine = engine
    for attr in _LABEL_ATTRS:
        setattr(view, attr, _RecordingLabel())
    view.edit_plate_input = _RecordingText(plate)
    view.edit_btn = _RecordingButton()
    view.delete_btn = _RecordingButton()
    view.ok_btn = _RecordingButton()
    view.crossing_detail_infobar = _RecordingInfoBar()
    return view


class _StubConsole:
    """A ``MainFrame`` double recording the crossing-open callback."""

    def __init__(self) -> None:
        """Start with no registered callback."""
        self._crossings_model: object | None = None
        self._on_open_crossing: Callable[[int], None] | None = None

    def set_on_open_crossing(self, callback: Callable[[int], None]) -> None:
        """Run the real seam so its registration is pinned."""
        main_frame.MainFrame.set_on_open_crossing(self, callback)


class _StubFeedModel:
    """A ``CrossingsFeedModel`` double reporting one scripted row."""

    def __init__(self, row: int) -> None:
        """Report *row* for every item."""
        self._row = row

    def GetRow(self, item: object) -> int:  # noqa: N802, ARG002
        """Report the scripted row index."""
        return self._row


class _ActivationEvent:
    """A ``wx.dataview.DataViewEvent`` double carrying one item."""

    def __init__(self, item: object = "item") -> None:
        """Carry *item* as the activated row."""
        self._item = item

    def GetItem(self) -> object:  # noqa: N802
        """Return the activated item."""
        return self._item


# ---------------------------------------------------------- render


def test_render_given_a_lone_crossing_fills_every_label() -> None:
    """The nine read-only labels carry the built field values."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)

    view.render()

    assert (
        view.crossing_rider_lbl.label,
        view.crossing_team_lbl.label,
        view.crossing_plate_lbl.label,
        view.crossing_lap_lbl.label,
        view.crossing_time_lbl.label,
        view.crossing_lap_time_lbl.label,
        view.crossing_total_lbl.label,
    ) == ("Amy", "Amy", "12", "1", "10:02:00", "2:00", "0:02:00")
    assert view.crossing_card_lbl.label == format_card(engine.card_for(engine.crossings[0]).code())
    assert view.crossing_held_lbl.label == "Credited"


def test_render_given_a_lone_crossing_seeds_the_plate_field_read_only() -> None:
    """Edit starts from the current plate, with the field disabled."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster, plate="stale")

    view.render()

    assert (view.edit_plate_input.GetValue(), view.edit_plate_input.enabled) == ("12", False)


def test_render_given_the_newest_crossing_enables_delete() -> None:
    """§5: a live ride offers Delete on its newest crossing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))
    view = _view(engine, roster=roster)

    view.render()

    assert (view.delete_btn.enabled, view.edit_btn.enabled) == (True, True)


def test_render_given_an_earlier_crossing_enables_delete() -> None:
    """§5: any crossing of a RUNNING ride is deletable (voided)."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))
    view = _view(engine, roster=roster, crossing=engine.crossings[0])

    view.render()

    assert view.delete_btn.enabled is True


def test_render_given_a_reopened_ride_enables_delete() -> None:
    """§5: REOPENED corrections offer Delete on any crossing too."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.finish()
    engine.reopen()
    view = _view(engine, roster=roster, crossing=engine.crossings[0])

    view.render()

    assert (engine.state, view.delete_btn.enabled) == (RideStatus.REOPENED, True)


def test_render_given_a_finished_ride_disables_delete() -> None:
    """T-3 negative: a locked ride offers no correction."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.finish()
    view = _view(engine, roster=roster)

    view.render()

    assert (engine.state, view.delete_btn.enabled) == (RideStatus.FINISHED, False)


@pytest.mark.parametrize(
    "state", [RideStatus.DRAFT, RideStatus.FINISHED], ids=["draft", "finished"]
)
def test_render_given_a_ride_that_is_not_live_disables_delete(state: RideStatus) -> None:
    """T-13: only RUNNING and REOPENED are correction states."""
    crossing = Crossing(entry_id="12", seq=1, crossed_at=_dt(10, 5), rider_plate="12")
    engine = _StubEngine(lap_times=(60.0,), credited=("AS",), state=state)
    view = _view(engine, roster=_solo_roster(), crossing=crossing)

    view.render()

    assert view.delete_btn.enabled is False


# ------------------------------------------------------------ Edit
#
# §9: in crossing mode Edit opens the ``crossing_number_dlg``
# Save/Cancel prompt and commits its Save through the engine's own
# ``reassign_crossing``, which resolves the plate and refuses a blank
# or unknown one. ``run_number_dialog`` is the prompt's loader+show
# seam (the wx boundary); the tests below stub it so the handler's
# branches are pinned headlessly, and its own tests drive it against a
# stub resource.


def _stub_number_dialog(
    monkeypatch: pytest.MonkeyPatch, result: str | None
) -> list[dict[str, object]]:
    """Stub ``run_number_dialog`` to return *result*.

    Each call's ``(opener, plate)`` is recorded for the caller.
    """
    calls: list[dict[str, object]] = []

    def _run(_resource: object, *, opener: object, plate: str) -> str | None:
        calls.append({"opener": opener, "plate": plate})
        return result

    monkeypatch.setattr(crossing_detail, "run_number_dialog", _run)
    return calls


def test_on_edit_given_a_confirmed_number_reassigns_the_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§9: the prompt's Save commits the typed plate."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)
    _stub_number_dialog(monkeypatch, "34")
    event = _RecordingEvent()

    view._on_edit(event)

    assert [(c.entry_id, c.rider_plate) for c in engine.crossings] == [("34", "34")]
    assert engine.events[-1].action == "reassign"
    assert engine.events[-1].payload["reason"] == crossing_detail.EDIT_REASON
    assert engine.events[-1].payload["new_plate"] == "34"
    assert (view.dialog.modal_ids, event.skipped) == ([wx.ID_OK], True)


@pytest.mark.parametrize(
    ("rider_plate", "expected"),
    [("12", "12"), (None, "12")],
    ids=["typed_plate", "no_typed_plate"],
)
def test_on_edit_given_a_crossing_prefills_the_number_prompt(
    monkeypatch: pytest.MonkeyPatch,
    rider_plate: str | None,
    expected: str,
) -> None:
    """T-4 nullable: the prompt opens on the plate the dialog shows."""
    crossing = Crossing(entry_id="12", seq=1, crossed_at=_dt(10, 2), rider_plate=rider_plate)
    engine = _running_engine(_solo_roster())
    view = _view(engine, roster=_solo_roster(), crossing=crossing)
    calls = _stub_number_dialog(monkeypatch, None)

    view._on_edit(_RecordingEvent())

    assert calls == [{"opener": view.dialog, "plate": expected}]
    assert view.dialog.modal_ids == []


def test_on_edit_given_a_cancelled_number_prompt_leaves_the_ride_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: Cancel (and Escape) commits nothing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)
    _stub_number_dialog(monkeypatch, None)
    events_before = len(engine.events)

    view._on_edit(_RecordingEvent())

    assert (len(engine.events), view.dialog.modal_ids) == (events_before, [])
    assert view.crossing_detail_infobar.messages == []


def test_on_edit_given_a_blank_number_keeps_the_dialog_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§9: a cleared prompt is the engine's own blank-plate refusal."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)
    _stub_number_dialog(monkeypatch, "")

    view._on_edit(_RecordingEvent())

    assert view.dialog.modal_ids == []
    assert view.crossing_detail_infobar.messages == ["Could not reassign: unknown plate: "]


def test_on_edit_given_an_unknown_number_keeps_the_dialog_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-5 negative: a mistyped number refuses on the info bar."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)
    _stub_number_dialog(monkeypatch, "999")

    view._on_edit(_RecordingEvent())

    assert view.dialog.modal_ids == []
    assert view.crossing_detail_infobar.messages == ["Could not reassign: unknown plate: 999"]


def test_on_edit_given_a_finished_ride_keeps_the_dialog_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finished ride refuses; the dialog stays open."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.finish()
    view = _view(engine, roster=roster)
    _stub_number_dialog(monkeypatch, "34")

    view._on_edit(_RecordingEvent())

    assert view.dialog.modal_ids == []
    assert view.crossing_detail_infobar.messages == [
        "Could not reassign: cannot reassign crossing from finished"
    ]


def test_on_edit_given_a_stale_crossing_keeps_the_dialog_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: a crossing the engine no longer holds."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    stale = Crossing(entry_id="12", seq=1, crossed_at=_dt(10, 2), rider_plate="12")
    view = _view(engine, roster=roster, crossing=stale)
    _stub_number_dialog(monkeypatch, "34")

    view._on_edit(_RecordingEvent())

    assert view.dialog.modal_ids == []
    assert view.crossing_detail_infobar.messages == [
        "Could not reassign: this crossing is no longer recorded."
    ]


def test_on_edit_given_a_mid_ride_crossing_addresses_it_by_ride_wide_ordinal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt's Save moves *that* crossing, not lap 1."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))  # ride-wide 1, Amy lap 1
    engine.record_crossing("34", at=_dt(10, 3))  # ride-wide 2, Bob lap 1
    engine.record_crossing("12", at=_dt(10, 6))  # ride-wide 3, Amy lap 2
    view = _view(engine, roster=roster, crossing=engine.crossings[1])
    _stub_number_dialog(monkeypatch, "12")

    view._on_edit(_RecordingEvent())

    assert [c.entry_id for c in engine.crossings] == ["12", "12", "12"]
    assert engine.events[-1].payload["seq"] == 2
    assert engine.events[-1].payload["old_entry_id"] == "34"


# -------------------------------------------------------------- OK


def test_on_ok_given_an_unchanged_plate_closes_without_touching_the_engine() -> None:
    """An unedited plate is not a correction: OK just closes."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster, plate="12")
    events_before = len(engine.events)

    view._on_ok(_RecordingEvent())

    assert (view.dialog.modal_ids, len(engine.events)) == ([wx.ID_OK], events_before)
    assert view.crossing_detail_infobar.messages == []


def test_on_ok_given_an_unchanged_plate_does_not_skip_the_event() -> None:
    """Measured: a Skip lets wx's stock OK close the dialog."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster, plate="12")
    event = _RecordingEvent()

    view._on_ok(event)

    assert event.skipped is False


def test_on_ok_given_a_changed_plate_reassigns_the_crossing() -> None:
    """OK commits an edited plate through ``reassign_crossing``."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster, plate="34")

    view._on_ok(_RecordingEvent())

    assert view.dialog.modal_ids == [wx.ID_OK]
    assert [(c.entry_id, c.rider_plate) for c in engine.crossings] == [("34", "34")]
    assert engine.events[-1].action == "reassign"
    assert engine.events[-1].payload["reason"] == crossing_detail.EDIT_REASON
    assert engine.events[-1].payload["new_plate"] == "34"


def test_on_ok_given_a_changed_plate_trims_surrounding_whitespace() -> None:
    """A typed plate is trimmed before it is resolved or audited."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster, plate="  34  ")

    view._on_ok(_RecordingEvent())

    assert engine.events[-1].payload["new_plate"] == "34"


def test_on_ok_given_a_mid_ride_crossing_addresses_it_by_ride_wide_ordinal() -> None:
    """``reassign_crossing``'s seq is the ride-wide ordinal.

    Bob's lap 1 sits at ride-wide ordinal 2 (Amy's lap 1 precedes it).
    Reassigning it to Amy must move *that* crossing; passing the
    per-entry lap number would have moved Amy's own lap 1 instead.
    """
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))  # ride-wide 1, Amy lap 1
    engine.record_crossing("34", at=_dt(10, 3))  # ride-wide 2, Bob lap 1
    engine.record_crossing("12", at=_dt(10, 6))  # ride-wide 3, Amy lap 2
    view = _view(engine, roster=roster, crossing=engine.crossings[1], plate="12")

    view._on_ok(_RecordingEvent())

    assert [c.entry_id for c in engine.crossings] == ["12", "12", "12"]
    assert engine.events[-1].payload["seq"] == 2
    assert engine.events[-1].payload["old_entry_id"] == "34"


def test_on_ok_given_a_blank_plate_keeps_the_dialog_open() -> None:
    """T-3 negative: a cleared field refuses rather than reassigning."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster, plate="   ")
    events_before = len(engine.events)

    view._on_ok(_RecordingEvent())

    assert (view.dialog.modal_ids, len(engine.events)) == ([], events_before)
    assert view.crossing_detail_infobar.messages == [
        "Enter a plate number to reassign this crossing."
    ]


def test_on_ok_given_an_unknown_plate_keeps_the_dialog_open() -> None:
    """A mistyped plate refuses on the info bar, dialog open."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster, plate="999")

    view._on_ok(_RecordingEvent())

    assert view.dialog.modal_ids == []
    assert view.crossing_detail_infobar.messages == ["Could not reassign: unknown plate: 999"]


def test_on_ok_given_a_finished_ride_refuses_and_keeps_the_dialog_open() -> None:
    """Reassign is RUNNING/REOPENED only; a finished ride says so."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.finish()
    view = _view(engine, roster=roster, plate="34")

    view._on_ok(_RecordingEvent())

    assert view.dialog.modal_ids == []
    assert view.crossing_detail_infobar.messages == [
        "Could not reassign: cannot reassign crossing from finished"
    ]


# ---------------------------------------------------------- Delete

# The Delete confirm's own copy for Amy's 10:02 lap-1 crossing (Undo)
# and for Amy's 10:05 lap-2 crossing (the specific-crossing void).
_DELETE_MESSAGE = (
    "Undo crossing 10:02:00 · Amy · lap 1? The newest crossing and its dealt card are removed."
)
_VOID_MESSAGE = (
    "Void crossing 10:05:00 · Amy · lap 2? The crossing and its card are voided; "
    "the entry's later laps renumber."
)


def _delete_fields(
    *, lap: str = "1", time: str = "10:02:00"
) -> crossing_detail.CrossingDetailFields:
    """Return the view-model Delete's confirm copy renders from."""
    return crossing_detail.CrossingDetailFields(
        rider="Amy",
        team="Amy",
        plate="12",
        lap=lap,
        time=time,
        lap_time="3:00",
        total="0:05:00",
        card="A♠",
        held="Credited",
    )


def test_delete_message_given_a_newest_crossing_names_the_undo() -> None:
    """T-13 true: the newest crossing keeps the Undo wording."""
    message = crossing_detail.delete_message(_delete_fields(), newest=True)

    assert message == _DELETE_MESSAGE


def test_delete_message_given_an_earlier_crossing_names_the_void() -> None:
    """T-13 false: any other crossing's confirm says it is voided."""
    message = crossing_detail.delete_message(
        _delete_fields(lap="2", time="10:05:00"), newest=False
    )

    assert message == _VOID_MESSAGE


# T-7 property: delete_message is pure (fields in, string out), so the
# one invariant both branches must hold -- UX-DESKTOP §4's rule that a
# destructive confirm names the object it destroys -- is asserted over
# generated crossings rather than the two examples above. The case is
# one tuple to keep the test's own arity at one argument.
_DELETE_MESSAGE_CASES = st.tuples(
    st.times().map(lambda value: value.strftime("%H:%M:%S")),
    st.text(min_size=1, max_size=40),
    st.integers(min_value=1, max_value=999).map(str),
    st.booleans(),
)


@given(case=_DELETE_MESSAGE_CASES)
def test_delete_message_given_any_crossing_names_it(
    case: tuple[str, str, str, bool],
) -> None:
    """T-7: whichever branch runs, the crossing's identity is in it."""
    time_text, rider, lap, newest = case
    fields = replace(_delete_fields(), time=time_text, rider=rider, lap=lap)

    message = crossing_detail.delete_message(fields, newest=newest)

    assert f"{time_text} · {rider} · lap {lap}" in message


def _stub_danger(monkeypatch: pytest.MonkeyPatch, result: int) -> list[tuple[object, ...]]:
    """Stub ``show_danger`` to return *result*, recording each call."""
    calls: list[tuple[object, ...]] = []

    def _show(*args: object, **_kwargs: object) -> int:
        calls.append(args)
        return result

    monkeypatch.setattr(std_dialogs, "show_danger", _show)
    return calls


def test_on_delete_given_a_confirmed_undo_removes_the_newest_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delete runs ``undo_last``, the console's own Undo."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))
    view = _view(engine, roster=roster)
    _stub_danger(monkeypatch, wx.ID_OK)
    event = _RecordingEvent()

    view._on_delete(event)

    assert [c.seq for c in engine.crossings] == [1]
    assert (view.dialog.modal_ids, engine.events[-1].action) == ([wx.ID_OK], "undo")
    assert event.skipped is True


def test_on_delete_given_a_confirmed_undo_names_the_crossing_it_destroys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UX-DESKTOP §4: the question names the crossing itself."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)
    calls = _stub_danger(monkeypatch, wx.ID_CANCEL)

    view._on_delete(_RecordingEvent())

    assert calls == [(view.dialog, "Undo Last Crossing?", _DELETE_MESSAGE, "Undo", "Cancel")]


def test_on_delete_given_a_cancelled_confirm_leaves_the_ride_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: Cancel destroys nothing and closes nothing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)
    _stub_danger(monkeypatch, wx.ID_CANCEL)

    view._on_delete(_RecordingEvent())

    assert (len(engine.crossings), view.dialog.modal_ids) == (1, [])


def test_on_delete_given_a_finished_ride_refuses_and_keeps_the_dialog_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Undo is RUNNING/REOPENED only; the bar carries the refusal."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.finish()
    view = _view(engine, roster=roster)
    _stub_danger(monkeypatch, wx.ID_OK)

    view._on_delete(_RecordingEvent())

    assert (len(engine.crossings), view.dialog.modal_ids) == (1, [])
    assert view.crossing_detail_infobar.messages == ["Undo unavailable: cannot undo from finished"]


def _three_lap_view(index: int) -> tuple[RideEngine, crossing_detail.CrossingDetailView]:
    """Return a RUNNING engine and the view of its *index*-th lap.

    Three Amy laps sit at 10:02 (lap 1), 10:05 (lap 2) and 10:07
    (lap 3), so index 1 is the mid-ride crossing a specific-crossing
    void addresses.
    """
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))
    engine.record_crossing("12", at=_dt(10, 7))
    return engine, _view(engine, roster=roster, crossing=engine.crossings[index])


def test_on_delete_given_a_confirmed_void_removes_only_that_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§5: a non-newest Delete voids it; later laps renumber."""
    engine, view = _three_lap_view(1)
    _stub_danger(monkeypatch, wx.ID_OK)
    event = _RecordingEvent()

    view._on_delete(event)

    assert [(c.seq, c.crossed_at) for c in engine.crossings] == [
        (1, _dt(10, 2)),
        (2, _dt(10, 7)),
    ]
    assert engine.events[-1].action == "void_crossing"
    assert engine.events[-1].payload == {
        "entry_id": "12",
        "seq": 2,
        "reason": crossing_detail.DELETE_REASON,
    }
    assert (view.dialog.modal_ids, event.skipped) == ([wx.ID_OK], True)


def test_on_delete_given_a_confirmed_void_voids_the_dealt_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A void removes the card from the hand, not the shoe."""
    engine, view = _three_lap_view(1)
    _stub_danger(monkeypatch, wx.ID_OK)

    view._on_delete(_RecordingEvent())

    assert (len(engine.credited_cards("12")), engine.shoe_remaining) == (2, engine.shoe_total - 3)


def test_on_delete_given_an_earlier_crossing_names_the_crossing_it_voids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UX-DESKTOP §4: the void question names the crossing."""
    _engine, view = _three_lap_view(1)
    calls = _stub_danger(monkeypatch, wx.ID_CANCEL)

    view._on_delete(_RecordingEvent())

    assert calls == [(view.dialog, "Void Crossing?", _VOID_MESSAGE, "Void", "Cancel")]


def test_on_delete_given_a_cancelled_void_leaves_the_ride_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: Cancel voids nothing and closes nothing."""
    engine, view = _three_lap_view(1)
    _stub_danger(monkeypatch, wx.ID_CANCEL)

    view._on_delete(_RecordingEvent())

    assert ([c.seq for c in engine.crossings], view.dialog.modal_ids) == ([1, 2, 3], [])


def test_on_delete_given_a_reopened_ride_voids_the_earlier_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§5: a REOPENED ride's corrections include the specific void."""
    engine, view = _three_lap_view(1)
    engine.finish()
    engine.reopen()
    _stub_danger(monkeypatch, wx.ID_OK)

    view._on_delete(_RecordingEvent())

    assert (engine.state, [c.seq for c in engine.crossings]) == (RideStatus.REOPENED, [1, 2])
    assert engine.events[-1].action == "void_crossing"


def test_on_delete_given_a_finished_ride_refuses_the_void(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Void is RUNNING/REOPENED only; the bar carries the refusal."""
    engine, view = _three_lap_view(1)
    engine.finish()
    _stub_danger(monkeypatch, wx.ID_OK)

    view._on_delete(_RecordingEvent())

    assert (len(engine.crossings), view.dialog.modal_ids) == (3, [])
    assert view.crossing_detail_infobar.messages == [
        "Could not void: cannot void crossing from finished"
    ]


# ------------------------------------------------- the console seam


def test_set_on_open_crossing_registers_the_callback() -> None:
    """The console's third activation seam mirrors the other two."""
    shell = _StubConsole()
    opened: list[int] = []

    shell.set_on_open_crossing(opened.append)

    assert shell._on_open_crossing == opened.append


def test_on_crossing_activated_given_a_model_fires_the_seam_with_its_row() -> None:
    """The seam fires with the activated row's model index."""
    shell = _StubConsole()
    shell._crossings_model = _StubFeedModel(3)
    opened: list[int] = []
    shell._on_open_crossing = opened.append

    main_frame.MainFrame._on_crossing_activated(shell, _ActivationEvent())

    assert opened == [3]


def test_on_crossing_activated_given_no_model_fires_nothing() -> None:
    """T-3 negative: no rendered feed means no row to resolve."""
    shell = _StubConsole()
    opened: list[int] = []
    shell._on_open_crossing = opened.append

    main_frame.MainFrame._on_crossing_activated(shell, _ActivationEvent())

    assert opened == []


def test_on_crossing_activated_given_an_unresolved_item_fires_nothing() -> None:
    """T-3 negative: wx's NOT_FOUND names no row."""
    shell = _StubConsole()
    shell._crossings_model = _StubFeedModel(wx.NOT_FOUND)
    opened: list[int] = []
    shell._on_open_crossing = opened.append

    main_frame.MainFrame._on_crossing_activated(shell, _ActivationEvent())

    assert opened == []


def test_on_crossing_activated_given_no_callback_is_a_no_op() -> None:
    """T-3 negative: an unwired console is inert."""
    shell = _StubConsole()
    shell._crossings_model = _StubFeedModel(1)

    main_frame.MainFrame._on_crossing_activated(shell, _ActivationEvent())

    assert shell._on_open_crossing is None


# ------------------------------------------- the feed-row resolution
#
# The console feed renders ``reversed(engine.crossings)`` newest first
# over the *whole* ride (Phase 4 retired R-32's 30-row cap), so the
# app's row -> crossing arithmetic is a straight unwind of that list.


def test_crossing_for_feed_row_given_a_short_feed_maps_row_zero_to_the_newest() -> None:
    """Row 0 is the newest crossing (the feed's own first row)."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))

    assert app_module._crossing_for_feed_row(engine, 0) is engine.crossings[-1]


def test_crossing_for_feed_row_given_a_short_feed_maps_the_last_row_to_the_oldest() -> None:
    """The last rendered row is the oldest crossing on the ride."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))

    assert app_module._crossing_for_feed_row(engine, 1) is engine.crossings[0]


def test_crossing_for_feed_row_given_an_empty_engine_returns_none() -> None:
    """T-4 boundary: no crossings means no row resolves."""
    engine = _running_engine(_solo_roster())

    assert app_module._crossing_for_feed_row(engine, 0) is None


@pytest.mark.parametrize("row", [-1, 2, 30], ids=["min_minus_one", "max_plus_one", "past_ride"])
def test_crossing_for_feed_row_given_an_out_of_range_row_returns_none(row: int) -> None:
    """T-4 boundaries: a stale activation resolves to nothing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))

    assert app_module._crossing_for_feed_row(engine, row) is None


def test_crossing_for_feed_row_given_past_the_old_cap_maps_row_zero_to_the_newest() -> None:
    """No cap: row 0 is the newest even with 31 crossings recorded."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    for minute in range(31):
        engine.record_crossing("12", at=_dt(11, minute))

    assert app_module._crossing_for_feed_row(engine, 0) is engine.crossings[-1]


def test_crossing_for_feed_row_given_past_the_old_cap_still_maps_the_oldest_crossing() -> None:
    """The oldest crossing is rendered now: row 30 resolves to it."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    for minute in range(31):
        engine.record_crossing("12", at=_dt(11, minute))

    assert app_module._crossing_for_feed_row(engine, 30) is engine.crossings[0]


def test_crossing_for_feed_row_given_past_the_old_cap_has_no_row_past_the_ride() -> None:
    """T-4 max + 1: row 31 is outside the 31-crossing ride."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    for minute in range(31):
        engine.record_crossing("12", at=_dt(11, minute))

    assert app_module._crossing_for_feed_row(engine, 31) is None


# ------------------------------------------- the miss field mapping
# A pending miss (K) has no entry, lap or card: only the instant the
# operator signalled it is known, so every other cell is a placeholder.


def test_build_miss_fields_given_a_pending_miss_renders_the_unscored_placeholders() -> None:
    """The miss view-model carries only its time; all else is blank."""
    miss = PendingMiss(miss_seq=1, crossed_at=_dt(10, 2))

    fields = crossing_detail.build_miss_fields(miss)

    assert (fields.rider, fields.team, fields.plate, fields.lap) == ("-", "missed", "-", "")
    assert (fields.time, fields.lap_time, fields.total, fields.card) == (
        "10:02:00",
        "",
        "",
        "",
    )
    assert fields.held == "Not yet scored"


@given(
    crossed_at=st.datetimes(
        min_value=datetime(1900, 1, 1),  # noqa: DTZ001 -- naive by design
        max_value=datetime(2100, 1, 1),  # noqa: DTZ001 -- naive by design
    )
)
def test_build_miss_fields_given_any_naive_instant_keeps_the_placeholders(
    crossed_at: datetime,
) -> None:
    """T-7 property: only the time tracks the miss; rest is fixed."""
    fields = crossing_detail.build_miss_fields(PendingMiss(miss_seq=1, crossed_at=crossed_at))

    assert (fields.rider, fields.team, fields.plate, fields.lap) == ("-", "missed", "-", "")
    assert (fields.lap_time, fields.total, fields.card, fields.held) == (
        "",
        "",
        "",
        "Not yet scored",
    )
    assert fields.time == crossing_detail._local_time(crossed_at)


# ------------------------------------------------------- the miss view


def _miss_view(
    engine: RideEngine,
    *,
    miss: PendingMiss | None = None,
    plate: str = "",
) -> crossing_detail.MissDetailView:
    """Return a ``MissDetailView`` over recording widget doubles.

    Built with ``object.__new__`` (``_view``'s own precedent): the
    handlers under test touch only these attributes, so no desktop and
    no ``__init__`` control binding is needed.
    """
    view = object.__new__(crossing_detail.MissDetailView)
    view.dialog = _RecordingDialog()
    view.miss = miss if miss is not None else engine.pending_misses()[-1]
    view.engine = engine
    for attr in _LABEL_ATTRS:
        setattr(view, attr, _RecordingLabel())
    view.edit_plate_input = _RecordingText(plate)
    view.edit_btn = _RecordingButton()
    view.delete_btn = _RecordingButton()
    view.ok_btn = _RecordingButton()
    view.crossing_detail_infobar = _RecordingInfoBar()
    return view


def test_render_given_a_pending_miss_fills_the_placeholder_cells() -> None:
    """The miss renders `-`/`missed`/blank card, held unscored."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine)

    view.render()

    assert (
        view.crossing_rider_lbl.label,
        view.crossing_team_lbl.label,
        view.crossing_plate_lbl.label,
        view.crossing_lap_lbl.label,
        view.crossing_time_lbl.label,
        view.crossing_lap_time_lbl.label,
        view.crossing_total_lbl.label,
        view.crossing_card_lbl.label,
        view.crossing_held_lbl.label,
    ) == ("-", "missed", "-", "", "10:02:00", "", "", "", "Not yet scored")


def test_render_given_a_pending_miss_seeds_a_blank_disabled_plate_field() -> None:
    """There is no current plate, so Edit starts from an empty field."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, plate="stale")

    view.render()

    assert (view.edit_plate_input.GetValue(), view.edit_plate_input.enabled) == ("", False)


def test_render_given_a_pending_miss_disables_delete_and_enables_edit() -> None:
    """A miss is not a crossing: it cannot be undone, only scored."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine)

    view.render()

    assert (view.delete_btn.enabled, view.edit_btn.enabled) == (False, True)


# ------------------------------------------------------- miss Edit/OK


def test_on_edit_given_a_miss_enables_focuses_and_selects_the_blank_field() -> None:
    """Edit is the one path that unlocks the miss's plate field."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine)
    event = _RecordingEvent()

    view._on_edit(event)

    assert (view.edit_plate_input.enabled, view.edit_plate_input.focused) == (True, True)
    assert (view.edit_plate_input.selected, event.skipped) == (True, True)


def test_on_ok_given_a_plate_assigns_it_to_the_miss_and_closes() -> None:
    """OK records the crossing and deals its card at that instant."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, plate="34")
    shoe_before = engine.shoe_remaining

    view._on_ok(_RecordingEvent())

    assert view.dialog.modal_ids == [wx.ID_OK]
    assert engine.pending_misses() == ()
    assert [(c.entry_id, c.rider_plate, c.crossed_at) for c in engine.crossings] == [
        ("34", "34", _dt(10, 2))
    ]
    assert (engine.shoe_remaining, len(engine.credited_cards("34"))) == (shoe_before - 1, 1)
    assert engine.events[-1].action == "assign_plate_to_miss"
    assert engine.events[-1].payload["reason"] == crossing_detail.MISS_EDIT_REASON
    assert engine.events[-1].payload["new_plate"] == "34"


def test_on_ok_given_a_plate_trims_surrounding_whitespace() -> None:
    """A typed plate is trimmed before it is resolved or audited."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, plate="  34  ")

    view._on_ok(_RecordingEvent())

    assert engine.events[-1].payload["new_plate"] == "34"


def test_on_ok_given_a_blank_plate_refuses_and_keeps_the_dialog_open() -> None:
    """T-3 negative: a blank field refuses rather than assigning."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, plate="   ")

    view._on_ok(_RecordingEvent())

    assert (view.dialog.modal_ids, len(engine.pending_misses())) == ([], 1)
    assert view.crossing_detail_infobar.messages == [
        "Enter a plate number to assign to this miss."
    ]


def test_on_ok_given_an_unknown_plate_refuses_and_keeps_the_dialog_open() -> None:
    """A mistyped plate refuses; the miss stays pending."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, plate="999")

    view._on_ok(_RecordingEvent())

    assert (view.dialog.modal_ids, len(engine.pending_misses())) == ([], 1)
    assert view.crossing_detail_infobar.messages == ["Could not assign: unknown plate: 999"]


def test_on_ok_given_a_finished_ride_keeps_the_miss_dialog_open() -> None:
    """Assigning is RUNNING/REOPENED only; a finished ride says so."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    engine.finish()
    view = _miss_view(engine, plate="34")

    view._on_ok(_RecordingEvent())

    assert (view.dialog.modal_ids, len(engine.pending_misses())) == ([], 1)
    assert view.crossing_detail_infobar.messages == [
        "Could not assign: cannot assign plate to miss from finished"
    ]


# -------------------------------------- the feed-row resolution
#
# The console feed interleaves pending misses with the crossings, so a
# row index no longer names a fixed crossing: `_feed_row_target` reads
# the row's own `missed` flag and resolves it to either the pending miss
# or the crossing that row shows. Phase 4 makes it read the *rendered*
# rows (the presenter's search-filtered list), so an index is resolved
# against exactly what the operator sees.


def test_feed_row_target_given_a_miss_row_resolves_the_pending_miss() -> None:
    """A missed feed row resolves to the engine's pending miss."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    rows = EngineDataSource(engine, roster).feed_rows()

    target = app_module._feed_row_target(rows, engine, 0)

    assert target is engine.pending_misses()[0]


def test_feed_row_target_given_a_crossing_row_resolves_the_crossing() -> None:
    """A non-missed feed row still resolves to its crossing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    rows = EngineDataSource(engine, roster).feed_rows()

    target = app_module._feed_row_target(rows, engine, 0)

    assert target is engine.crossings[0]


def test_feed_row_target_given_a_miss_between_crossings_maps_row_zero_to_the_newest() -> None:
    """The miss shifts later rows; row 0 is the newest crossing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 1))
    engine.record_miss(_dt(10, 2), reason="missed number")
    engine.record_crossing("12", at=_dt(10, 3))
    rows = EngineDataSource(engine, roster).feed_rows()

    target = app_module._feed_row_target(rows, engine, 0)

    assert target is engine.crossings[1]


def test_feed_row_target_given_a_miss_between_crossings_maps_row_one_to_the_miss() -> None:
    """Row 1 is the miss, not the crossing the old arithmetic named."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 1))
    engine.record_miss(_dt(10, 2), reason="missed number")
    engine.record_crossing("12", at=_dt(10, 3))
    rows = EngineDataSource(engine, roster).feed_rows()

    target = app_module._feed_row_target(rows, engine, 1)

    assert target is engine.pending_misses()[0]


def test_feed_row_target_given_a_miss_between_crossings_maps_row_two_to_the_oldest() -> None:
    """Row 2 unwinds past the miss to the oldest crossing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 1))
    engine.record_miss(_dt(10, 2), reason="missed number")
    engine.record_crossing("12", at=_dt(10, 3))
    rows = EngineDataSource(engine, roster).feed_rows()

    target = app_module._feed_row_target(rows, engine, 2)

    assert target is engine.crossings[0]


def test_feed_row_target_given_a_long_ride_maps_row_one_past_a_miss_to_the_newest() -> None:
    """A miss on a 31-crossing ride still leaves row 1 the newest."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    for minute in range(31):
        engine.record_crossing("12", at=_dt(11, minute))
    engine.record_miss(_dt(11, 59), reason="missed number")
    rows = EngineDataSource(engine, roster).feed_rows()

    target = app_module._feed_row_target(rows, engine, 1)

    assert target is engine.crossings[-1]


def test_feed_row_target_given_a_long_ride_still_reaches_the_oldest_crossing() -> None:
    """No cap: the last miss-shifted row is the first crossing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    for minute in range(31):
        engine.record_crossing("12", at=_dt(11, minute))
    engine.record_miss(_dt(11, 59), reason="missed number")
    rows = EngineDataSource(engine, roster).feed_rows()

    target = app_module._feed_row_target(rows, engine, 31)

    assert target is engine.crossings[0]


def test_feed_row_target_given_a_long_ride_has_no_row_past_the_last_one() -> None:
    """T-4 max + 1: one row past the merged feed names nothing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    for minute in range(31):
        engine.record_crossing("12", at=_dt(11, minute))
    engine.record_miss(_dt(11, 59), reason="missed number")
    rows = EngineDataSource(engine, roster).feed_rows()

    target = app_module._feed_row_target(rows, engine, 32)

    assert target is None


def test_feed_row_target_given_a_search_filtered_feed_resolves_the_visible_row() -> None:
    """Phase 4: row 0 of a narrowed feed is that feed's own first row.

    The presenter renders the search-filtered rows, so the app must
    resolve the activated index against those -- the unfiltered feed's
    row 0 would open a different crossing entirely.
    """
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 1))
    engine.record_crossing("12", at=_dt(10, 3))
    rendered = [row for row in EngineDataSource(engine, roster).feed_rows() if row.lap == 2]

    target = app_module._feed_row_target(rendered, engine, 0)

    assert (target, target is engine.crossings[1]) == (engine.crossings[1], True)


def test_feed_row_target_given_an_empty_feed_returns_none() -> None:
    """T-4 boundary: an empty feed has no row to resolve."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    rows = EngineDataSource(engine, roster).feed_rows()

    target = app_module._feed_row_target(rows, engine, 0)

    assert target is None


@pytest.mark.parametrize("row", [-1, 3], ids=["min_minus_one", "max_plus_one"])
def test_feed_row_target_given_an_out_of_range_row_returns_none(row: int) -> None:
    """T-4 boundaries: a stale activation resolves to nothing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    rows = EngineDataSource(engine, roster).feed_rows()

    target = app_module._feed_row_target(rows, engine, row)

    assert target is None


def test_feed_row_target_given_a_stale_miss_row_returns_none() -> None:
    """T-3: a miss assigned between the render and the click."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    stale_row = EngineDataSource(engine, roster).feed_rows()[0]
    engine.assign_plate_to_miss(1, "12", reason="rider identified")

    target = app_module._feed_row_target([stale_row], engine, 0)

    assert target is None


# ------------------------------------------------- the open seam (miss)


class _StubPresenter:
    """A console-presenter double carrying the engine and its source."""

    def __init__(self, engine: RideEngine, source: EngineDataSource) -> None:
        """Store the two collaborators the open seam reads."""
        self.engine = engine
        self.source = source

    def rendered_feed_rows(self) -> list[FeedRow]:
        """Return the rows the double's console would be showing.

        The real presenter returns the search-filtered list it last
        rendered; this double has no search box, so it answers the
        source's own feed (the unfiltered render).
        """
        return self.source.feed_rows()


class _StubFrame:
    """A ``MainFrame`` double recording the seam's status notices."""

    def __init__(self) -> None:
        """Start with no notices."""
        self.notices: list[str] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record *text* as the latest notice."""
        self.notices.append(text)


class _StubDialogWindow:
    """A loaded-dialog double: closable, optionally mid-delete."""

    def __init__(self, *, being_deleted: bool = False) -> None:
        """Start undestroyed and, by default, not mid-delete."""
        self.destroyed = False
        self._being_deleted = being_deleted

    def IsBeingDeleted(self) -> bool:  # noqa: N802 -- wx API name
        """Report the scripted mid-delete state."""
        return self._being_deleted

    def Destroy(self) -> None:  # noqa: N802 -- wx API name
        """Record the destroy the seam's ``finally`` runs."""
        self.destroyed = True


class _StubResource:
    """An ``XmlResource`` double returning one scripted window."""

    def __init__(self, window: _StubDialogWindow | None) -> None:
        """Store the window (or None) every LoadDialog returns."""
        self.window = window

    def LoadDialog(self, _parent: object, _name: object) -> _StubDialogWindow | None:  # noqa: N802
        """Return the scripted window, or None when unauthored."""
        return self.window


class _RefusingResource:
    """A resource double that fails if any dialog is loaded."""

    def LoadDialog(self, _parent: object, _name: object) -> object:  # noqa: N802
        """Raise: the seam must not load a dialog on this path."""
        raise AssertionError("no dialog may be loaded")


def _run_dialog_stub(_dialog: object, opener: object) -> int:  # noqa: ARG001 -- run_dialog's keyword
    """Stub ``run_dialog``: return OK without showing a window."""
    return wx.ID_OK


def _open_context(
    engine: RideEngine, source: EngineDataSource, roster: Roster
) -> tuple[app_module._RouteContext, _StubDialogWindow]:
    """Build a context whose resource returns one stub window."""
    window = _StubDialogWindow()
    context = app_module._RouteContext(
        frame=_StubFrame(),
        resource=_StubResource(window),
        roster=roster,
        app=None,
        theme_controller=None,
        presenter=_StubPresenter(engine, source),
    )
    return context, window


def test_open_crossing_detail_for_given_a_miss_row_opens_the_miss_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Double-clicking a miss row decorates the dialog in miss mode."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    source = EngineDataSource(engine, roster)
    context, window = _open_context(engine, source, roster)
    opened: list[tuple[object, ...]] = []

    class _RecordingMissView:
        def __init__(self, dialog: object, *, miss: object, engine: object) -> None:
            """Record the decorated dialog and the miss it shows."""
            opened.append((dialog, miss, engine))

    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(dialogs, "run_dialog", _run_dialog_stub)
    monkeypatch.setattr(crossing_detail, "MissDetailView", _RecordingMissView)

    app_module._open_crossing_detail_for(context, 0)

    assert opened == [(window, engine.pending_misses()[0], engine)]
    assert window.destroyed is True


def test_open_crossing_detail_for_given_a_crossing_row_opens_the_crossing_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crossing row still opens the crossing view, unchanged."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    source = EngineDataSource(engine, roster)
    context, window = _open_context(engine, source, roster)
    opened: list[tuple[object, ...]] = []

    class _RecordingCrossingView:
        def __init__(  # noqa: PLR0913 -- (dialog, crossing, roster, engine) mirrors the view
            self, dialog: object, *, crossing: object, roster: object, engine: object
        ) -> None:
            """Record the decorated dialog and the crossing it shows."""
            opened.append((dialog, crossing, roster, engine))

    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(dialogs, "run_dialog", _run_dialog_stub)
    monkeypatch.setattr(crossing_detail, "CrossingDetailView", _RecordingCrossingView)

    app_module._open_crossing_detail_for(context, 0)

    assert opened == [(window, engine.crossings[0], roster, engine)]
    assert window.destroyed is True


def test_open_crossing_detail_for_given_no_presenter_opens_nothing() -> None:
    """T-3 negative: a console-less context opens nothing."""
    context = app_module._RouteContext(
        frame=_StubFrame(),
        resource=_RefusingResource(),
        roster=_solo_roster(),
        app=None,
        theme_controller=None,
    )

    app_module._open_crossing_detail_for(context, 0)


def test_open_crossing_detail_for_given_a_stale_row_opens_nothing() -> None:
    """T-3 negative: a row outside the feed loads no dialog."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    source = EngineDataSource(engine, roster)
    context = app_module._RouteContext(
        frame=_StubFrame(),
        resource=_RefusingResource(),
        roster=roster,
        app=None,
        theme_controller=None,
        presenter=_StubPresenter(engine, source),
    )

    app_module._open_crossing_detail_for(context, 5)


def test_open_crossing_detail_for_given_no_authored_window_posts_a_notice() -> None:
    """T-3 negative: a missing XRC dialog reports on the status bar."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    source = EngineDataSource(engine, roster)
    frame = _StubFrame()
    context = app_module._RouteContext(
        frame=frame,
        resource=_StubResource(None),
        roster=roster,
        app=None,
        theme_controller=None,
        presenter=_StubPresenter(engine, source),
    )

    app_module._open_crossing_detail_for(context, 0)

    assert frame.notices == ["Crossing Detail — no window authored yet"]


class _RecordingNoopView:
    """A view double that accepts the crossing keywords."""

    def __init__(
        self, _dialog: object, *, crossing: object, roster: object, engine: object
    ) -> None:
        """Accept the decoration arguments; build no window."""
        self.crossing = crossing
        self.roster = roster
        self.engine = engine


def test_open_crossing_detail_for_given_a_mid_delete_window_skips_destroy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3: an already-deleting window is never destroyed twice."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    source = EngineDataSource(engine, roster)
    window = _StubDialogWindow(being_deleted=True)
    context = app_module._RouteContext(
        frame=_StubFrame(),
        resource=_StubResource(window),
        roster=roster,
        app=None,
        theme_controller=None,
        presenter=_StubPresenter(engine, source),
    )
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(dialogs, "run_dialog", _run_dialog_stub)
    monkeypatch.setattr(crossing_detail, "CrossingDetailView", _RecordingNoopView)

    app_module._open_crossing_detail_for(context, 0)

    assert window.destroyed is False


# ------------------------------------------- the Number prompt's loader
#
# §9: ``run_number_dialog`` is the wx boundary the Edit handler calls.
# It loads ``crossing_number_dlg`` from the shared resource, prefills
# ``number_input`` with the crossing's current plate, shows it modally
# and returns Save's trimmed text -- or ``None`` on Cancel. Two
# toolkit seams are stubbed below: the resource double stands in for
# ``wx.xrc.XmlResource``, ``find_control`` is the wx lookup (its own
# module docstring: ``FindWindowByName`` plus the address-reuse settle
# loop) and ``dialogs.run_dialog`` is the one seam every dialog shows
# through. No window is created and no desktop is taken.


class _StubNumberWindow:
    """A loaded ``crossing_number_dlg`` double."""

    def __init__(self, *, being_deleted: bool = False) -> None:
        """Start undestroyed, with an empty four-digit field."""
        self.number_input = _RecordingText()
        self.shown_value: str | None = None
        self.destroyed = False
        self._being_deleted = being_deleted

    def IsBeingDeleted(self) -> bool:  # noqa: N802 -- wx API name
        """Report the scripted mid-delete state."""
        return self._being_deleted

    def Destroy(self) -> None:  # noqa: N802 -- wx API name
        """Record the destroy the loader's ``finally`` runs."""
        self.destroyed = True


class _RecordingResource:
    """An ``XmlResource`` double recording the dialog names it loads."""

    def __init__(self, window: _StubNumberWindow | None) -> None:
        """Return *window* (or ``None``) from every LoadDialog call."""
        self.window = window
        self.loaded: list[tuple[object, object]] = []

    def LoadDialog(self, parent: object, name: object) -> _StubNumberWindow | None:  # noqa: N802
        """Record the lookup and return the scripted window."""
        self.loaded.append((parent, name))
        return self.window


def _refuse_lookup(*_args: object, **_kwargs: object) -> object:
    """Fail the test if the loader looks a control up."""
    raise AssertionError("no control may be looked up")


def _stub_loader(
    monkeypatch: pytest.MonkeyPatch, window: _StubNumberWindow, answer: str | None
) -> None:
    """Point the loader's toolkit seams at *window* and a stubbed modal.

    The ``run_dialog`` double stands in for the modal itself: it
    records what the prompt opened on, types *answer* into the field
    when the test scripts an operator entry, and returns ``wx.ID_OK``
    -- or ``wx.ID_CANCEL`` when *answer* is ``None``, the operator's
    own cancel.
    """

    def _run(*_args: object, **_kwargs: object) -> int:
        window.shown_value = window.number_input.GetValue()
        if answer is None:
            return int(wx.ID_CANCEL)
        window.number_input.SetValue(answer)
        return int(wx.ID_OK)

    monkeypatch.setattr(crossing_detail, "find_control", lambda *_a, **_k: window.number_input)
    monkeypatch.setattr(dialogs, "run_dialog", _run)


def test_run_number_dialog_given_save_returns_the_typed_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§9: Save's value is what the Edit handler commits."""
    window = _StubNumberWindow()
    _stub_loader(monkeypatch, window, "34")

    result = crossing_detail.run_number_dialog(_StubResource(window), opener=object(), plate="12")

    assert result == "34"


def test_run_number_dialog_given_save_trims_the_typed_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typed number is trimmed before it reaches the engine."""
    window = _StubNumberWindow()
    _stub_loader(monkeypatch, window, "  34  ")

    result = crossing_detail.run_number_dialog(_StubResource(window), opener=object(), plate="12")

    assert result == "34"


def test_run_number_dialog_given_a_plate_prefills_and_selects_the_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§9: the prompt opens on the crossing's own plate."""
    window = _StubNumberWindow()
    _stub_loader(monkeypatch, window, None)

    crossing_detail.run_number_dialog(_StubResource(window), opener=object(), plate="12")

    assert window.shown_value == "12"
    assert (window.number_input.focused, window.number_input.selected) == (True, True)


def test_run_number_dialog_given_a_cancel_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: Cancel (and Escape) returns no number."""
    window = _StubNumberWindow()
    _stub_loader(monkeypatch, window, None)

    result = crossing_detail.run_number_dialog(_StubResource(window), opener=object(), plate="12")

    assert result is None


def test_run_number_dialog_given_an_unauthored_window_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: LoadDialog returns None when unauthored."""
    monkeypatch.setattr(crossing_detail, "find_control", _refuse_lookup)

    result = crossing_detail.run_number_dialog(_StubResource(None), opener=object(), plate="12")

    assert result is None


def test_run_number_dialog_given_save_destroys_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loaded window is destroyed however the prompt ends."""
    window = _StubNumberWindow()
    _stub_loader(monkeypatch, window, "34")

    crossing_detail.run_number_dialog(_StubResource(window), opener=object(), plate="12")

    assert window.destroyed is True


def test_run_number_dialog_given_a_cancel_destroys_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3: the cancel path destroys the window too."""
    window = _StubNumberWindow()
    _stub_loader(monkeypatch, window, None)

    crossing_detail.run_number_dialog(_StubResource(window), opener=object(), plate="12")

    assert window.destroyed is True


def test_run_number_dialog_given_a_mid_delete_window_skips_destroy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3: an already-deleting window is never destroyed twice."""
    window = _StubNumberWindow(being_deleted=True)
    _stub_loader(monkeypatch, window, "34")

    crossing_detail.run_number_dialog(_StubResource(window), opener=object(), plate="12")

    assert window.destroyed is False


def test_run_number_dialog_loads_the_crossing_number_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§9: the loader asks the shared resource for the frozen window."""
    window = _StubNumberWindow()
    resource = _RecordingResource(window)
    _stub_loader(monkeypatch, window, "34")

    result = crossing_detail.run_number_dialog(resource, opener=object(), plate="12")

    assert resource.loaded == [(None, ids.CROSSING_NUMBER_DLG)]
    assert result == "34"
