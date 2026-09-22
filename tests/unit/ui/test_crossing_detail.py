# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the crossing-detail workstream (J2).

Double-clicking a row in the console's ``crossings_list`` opens
``crossing_detail_dlg`` on that crossing. Four pieces are pinned here,
all without a display:

* **The field mapping** -- :func:`crossing_detail.build_fields` is a
  pure function over the live ``Crossing``, ``Roster`` and
  ``RideEngine`` (real domain objects, not doubles: they are the
  subject, not an I/O boundary -- T-10), so rider/team/plate/lap/times/
  card/held are asserted directly.
* **The view's handlers** -- ``_on_edit`` (the §9 Plate prompt, whose
  own loader :func:`crossing_detail.run_plate_dialog` is pinned against
  a stub resource), ``_on_edit_time`` and ``_on_void_card``. In
  crossing mode each commits through the engine and then re-renders the
  dialog **in place**, so a correction returns to the detail window
  rather than closing it; in miss mode Edit is the commit, and the same
  dialog is handed over to the crossing the miss became rather than
  closing. ``_on_ok`` is the one control that ends the dialog, in both
  modes,
  with Escape pointed at the same button. ``_on_delete`` is the one
  correction that still closes -- its crossing is gone. All are driven
  against recording widget doubles built with ``object.__new__``
  (``test_dialogs_positioning.py``'s precedent).
* **The dialog's authored shape** -- ``dialogs.xrc``'s
  ``crossing_detail_dlg`` declares no ``edit_plate_input`` row (the
  plate is read-only copy), labels ``edit_btn`` "Edit Plate" and
  authors its nine value fields as read-only entry boxes inside one
  "Details" group of four columns, pinned over the XML because a
  loader test cannot see any of it.
* **The two seams this workstream adds** -- ``MainFrame.
  set_on_open_crossing`` / ``_on_crossing_activated`` and the app's
  ``_crossing_for_feed_row`` / ``_feed_row_target``, whose row
  arithmetic unwinds the whole-ride (uncapped, Phase 4) console feed.

Real-window geometry, the loaded dialog's controls and click-through
behaviour need a real window and are not pinned here.
"""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import wx
from defusedxml.ElementTree import parse
from hypothesis import given
from hypothesis import strategies as st
from xrc_fixtures import pin_no_authored_window

from conftest import _pooled_team_roster, entry_key, gorba_config
from rivercrossing.cards import Card, Shoe
from rivercrossing.ride import Crossing, Event, PendingMiss, RideEngine, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids, std_dialogs
from rivercrossing.ui.card_text import format_card
from rivercrossing.ui.presenters.data_source import EngineDataSource
from rivercrossing.ui.views import corrections, crossing_detail, dialogs, main_frame
from rivercrossing.ui.views.corrections import CardVoid, CrossingEdit

if TYPE_CHECKING:
    from collections.abc import Callable
    from xml.etree.ElementTree import Element

    from rivercrossing.ui.presenters.data_source import FeedRow


# The authored dialog resource the two structural pins below parse
# (test_corrections.py's own ``_DIALOGS_XRC`` precedent). The path is
# derived from the view module so a move cannot silently skip the pins.
_DIALOGS_XRC = Path(crossing_detail.__file__).resolve().parent.parent / "xrc" / "dialogs.xrc"


# ------------------------------------------------------- builders


def _dt(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Build a naive datetime on the fixed event day, Sept 20, 2026."""
    return datetime(2026, 9, 20, hour, minute, second)  # noqa: DTZ001 -- naive by design


def _frozen_clock() -> datetime:
    """Return the event start; tests stamp crossings explicitly."""
    return _dt(10, 0)


def _running_engine(  # noqa: PLR0913 -- (roster, min_lap_s, hold_short_laps, engine_class)
    roster: Roster,
    *,
    min_lap_s: int = 1,
    hold_short_laps: bool = False,
    engine_class: type[RideEngine] = RideEngine,
) -> RideEngine:
    """Build a RUNNING engine over *roster* on the GORBA config.

    *engine_class* builds the instance: ``RideEngine`` itself
    everywhere but the void-card identity case, which needs its
    recording double.
    """
    config = gorba_config(min_lap_s=min_lap_s, hold_short_laps=hold_short_laps)
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    engine = engine_class(config=config, shoe=shoe, clock=_frozen_clock, roster=roster)
    engine.start()
    return engine


# The same-code pair the identity cases below need: two physically
# different cards sharing one code, both credited to one entry. The
# GORBA shoe's eight decks repeat no code early enough to be worth
# dealing; a two-deck shoe under this seed deals one code on two
# consecutive deals (the seed test_ride.py's own identity cases use).
_IDENTITY_SEED = 20260901


def _identity_engine(roster: Roster) -> RideEngine:
    """Build a RUNNING two-deck engine that repeats a code."""
    config = replace(
        gorba_config(min_lap_s=1, hold_short_laps=False),
        deck_count=2,
        jokers_per_deck=1,
    )
    shoe = Shoe(
        decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=_IDENTITY_SEED
    )
    engine = RideEngine(config=config, shoe=shoe, clock=_frozen_clock, roster=roster)
    engine.start()
    return engine


def _record_alias_pair(engine: RideEngine) -> tuple[Crossing, Crossing]:
    """Record seven laps for "12"; laps six and seven repeat a code.

    Returns:
        The aliased pair, earlier lap first -- the engine's own
        ``Crossing`` objects.
    """
    for index in range(6):
        engine.record_crossing("12", at=_dt(10, 0, 2 + index * 2))
    engine.record_crossing("12", at=_dt(10, 0, 14))
    return engine.crossings[5], engine.crossings[6]


def _voided_alias_ride(roster: Roster) -> tuple[RideEngine, Crossing, Crossing]:
    """Build a ride whose sixth lap's card is voided off the hand.

    Returns:
        The engine and the two aliased crossings -- the voided one and
        its still-credited sibling, in that order.
    """
    engine = _identity_engine(roster)
    voided, sibling = _record_alias_pair(engine)
    engine.void_card("12", engine.card_for(voided), reason="wrong card off the line")
    return engine, voided, sibling


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
    """A solo crossing renders its rider, "solo", lap and times."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))

    fields = crossing_detail.build_fields(engine.crossings[0], roster, engine)

    assert (fields.rider, fields.team, fields.plate, fields.lap) == ("Amy", "solo", "12", "1")
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

    assert fields.held == "Held - Review"
    assert fields.card == format_card(held.code())


def test_build_fields_given_a_voided_card_reports_it_voided() -> None:
    """A voided card is in neither the hold queue nor the hand."""
    roster = _solo_roster()
    engine = _running_engine(roster, min_lap_s=1080, hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 2))
    crossing = engine.crossings[0]
    engine.void_held(crossing)

    fields = crossing_detail.build_fields(crossing, roster, engine)

    assert fields.held == "Void"


def test_build_fields_given_a_voided_card_renders_void_in_the_card_field() -> None:
    """D3: the Card field drops the voided glyph for the word "Void".

    A card out of the ride is not the crossing's any more, so showing
    its glyph would name a card the entry does not hold; the field
    reads what the Status field beside it reads.
    """
    roster = _solo_roster()
    engine = _running_engine(roster, min_lap_s=1080, hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 2))
    crossing = engine.crossings[0]
    engine.void_held(crossing)

    fields = crossing_detail.build_fields(crossing, roster, engine)

    assert (fields.card, fields.held) == ("Void", "Void")


def test_build_fields_given_a_duplicate_card_keeps_its_glyph() -> None:
    """D3 guardrail: a duplicate's card is in the ride, so it shows.

    The pair membership outranks the card's own disposition
    (``_held_status``), so this is the one status that can sit beside a
    credited or held card and still read as a glyph-bearing row.
    """
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 2))
    crossing = engine.crossings[-1]
    glyph = format_card(engine.card_for(crossing).code())

    fields = crossing_detail.build_fields(crossing, roster, engine)

    assert (fields.card, fields.held) == (glyph, "Duplicate")


@pytest.mark.parametrize("index", [0, 1], ids=["older_twin", "newer_twin"])
def test_build_fields_given_either_half_of_a_duplicate_pair_reports_it_duplicate(
    index: int,
) -> None:
    """Phase 3: each half of a same-instant pair reads "Duplicate"."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 2))

    fields = crossing_detail.build_fields(engine.crossings[index], roster, engine)

    assert fields.held == "Duplicate"


def test_build_fields_given_a_held_duplicate_reports_duplicate_not_held() -> None:
    """T-13: the pair membership outranks the held disposition."""
    roster = _solo_roster()
    engine = _running_engine(roster, min_lap_s=1080, hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 2))
    crossing = engine.crossings[1]
    assert engine.held_card_for(crossing) is not None

    fields = crossing_detail.build_fields(crossing, roster, engine)

    assert fields.held == "Duplicate"


def test_build_fields_given_a_second_duplicate_pair_reports_it_duplicate() -> None:
    """T-4 [many]: the scan reaches every pair, not just the first."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("34", at=_dt(10, 5))
    engine.record_crossing("34", at=_dt(10, 5))

    fields = crossing_detail.build_fields(engine.crossings[3], roster, engine)

    assert fields.held == "Duplicate"


def test_build_fields_given_a_voided_card_beside_its_credited_code_alias_is_void() -> None:
    """T-3: a credited same-code sibling cannot mask the void.

    Voiding the sixth lap leaves the seventh's value-equal card in the
    entry's hand, so a credited-membership read would call a card the
    engine has retired "Credited" (an eight-deck alias in one hand).
    """
    roster = _solo_roster()
    engine, voided, _sibling = _voided_alias_ride(roster)

    fields = crossing_detail.build_fields(voided, roster, engine)

    assert (fields.held, fields.card) == ("Void", "Void")


def test_build_fields_given_the_credited_half_of_a_code_alias_stays_credited() -> None:
    """T-3 negative: the voided object does not taint its code alias."""
    roster = _solo_roster()
    engine, _voided, sibling = _voided_alias_ride(roster)

    fields = crossing_detail.build_fields(sibling, roster, engine)

    assert fields.held == "Credited"


def test_build_fields_given_a_voided_duplicate_reports_duplicate_not_void() -> None:
    """T-13: the pair membership outranks even a voided card.

    ``_held_status`` reads the duplicate pair before any card
    disposition, so the voided arm cannot take a twin's row away from
    the correction it needs.
    """
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 2))
    crossing = engine.crossings[1]
    engine.void_card("12", engine.card_for(crossing), reason="wrong card off the line")

    fields = crossing_detail.build_fields(crossing, roster, engine)

    assert fields.held == "Duplicate"


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

    def is_card_voided(self, card: Card) -> bool:  # noqa: ARG002
        """Report the scripted card (``card``) never voided.

        ``_StubEngine`` scripts credited hands only: a card outside
        them reads as voided through ``_held_status``' own fallback,
        which is the state the tests using this double pin.
        """
        return False

    def duplicate_crossings(self) -> tuple[tuple[Crossing, Crossing], ...]:
        """Report no pairs: every scripted crossing is a lone one."""
        return ()


def test_build_fields_given_a_seq_past_the_recorded_laps_renders_zero_times() -> None:
    """T-4 boundary: a stale seq cannot index past lap_times."""
    roster = _solo_roster()
    crossing = Crossing(
        entry_id=entry_key(roster, "12"), seq=3, crossed_at=_dt(10, 5), rider_plate="12"
    )
    engine = _StubEngine(lap_times=(100.0, 120.0), credited=("AS",))

    fields = crossing_detail.build_fields(crossing, roster, engine)

    assert (fields.lap_time, fields.total) == ("0:00", "0:00:00")


def test_build_fields_given_an_unknown_entry_falls_back_to_the_typed_plate() -> None:
    """A crossing whose entry left the roster still renders its plate.

    T-3 negative of the solo branch: with no entry to type-check, the
    Team field falls back to the plate the operator typed rather than
    "solo" -- never the crossing's internal stable key.
    """
    crossing = Crossing(entry_id="99", seq=1, crossed_at=_dt(10, 5), rider_plate="99")
    engine = _StubEngine(lap_times=(60.0,), credited=("AS",))

    fields = crossing_detail.build_fields(crossing, _solo_roster(), engine)

    assert (fields.rider, fields.team, fields.plate) == ("99", "99", "99")


def test_build_fields_given_no_rider_plate_falls_back_to_the_entry_plate() -> None:
    """A crossing with no typed plate shows the entry's own plate."""
    roster = _solo_roster()
    crossing = Crossing(entry_id=entry_key(roster, "12"), seq=1, crossed_at=_dt(10, 5))
    engine = _StubEngine(lap_times=(60.0,), credited=("AS",))

    fields = crossing_detail.build_fields(crossing, roster, engine)

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


class _RecordingValueBox:
    """A ``wx.TextCtrl`` double recording the value it was given."""

    def __init__(self) -> None:
        """Start blank."""
        self.value = ""

    def SetValue(self, text: str) -> None:  # noqa: N802
        """Record the rendered value."""
        self.value = text

    def GetValue(self) -> str:  # noqa: N802
        """Return the rendered value."""
        return self.value


class _RecordingText:
    """A ``wx.TextCtrl`` double recording value and text selection."""

    def __init__(self, value: str = "") -> None:
        """Start holding *value*."""
        self._value = value
        self.focused = False
        self.selected = False

    def GetValue(self) -> str:  # noqa: N802
        """Return the field's current text."""
        return self._value

    def SetValue(self, value: str) -> None:  # noqa: N802
        """Replace the field's text."""
        self._value = value

    def SetFocus(self) -> None:  # noqa: N802
        """Record that the view focused the field."""
        self.focused = True

    def SelectAll(self) -> None:  # noqa: N802
        """Record that the view selected the field's text."""
        self.selected = True


class _RecordingButton:
    """A ``wx.Button`` double recording its id and its enablement."""

    def __init__(self, button_id: int = 0) -> None:
        """Start with *button_id* and no enablement applied."""
        self.button_id = button_id
        self.enabled: bool | None = None

    def GetId(self) -> int:  # noqa: N802
        """Return the id Escape is pointed at through this button."""
        return self.button_id

    def Enable(self, enabled: bool = True) -> None:  # noqa: N802, FBT001, FBT002
        """Record the enablement the view applied."""
        self.enabled = enabled


class _RecordingInfoBar:
    """A ``wx.InfoBar`` double recording refusals and its best size."""

    def __init__(self, best_height: int = 0) -> None:
        """Start with no message shown and no measured height."""
        self.messages: list[str] = []
        self._best_height = best_height

    def GetBestSize(self) -> wx.Size:  # noqa: N802
        """Report the height the bar wants, 0 when none measured."""
        return wx.Size(0, self._best_height)

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


# The nine read-only value boxes, in the dialog's own canvas order.
_VALUE_ATTRS = (
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

# The nine frozen value names split across the dialog's two value
# columns (G2): the first five sit beside their captions in the first
# half, the last four in the second.
_VALUE_COLUMNS = (_VALUE_ATTRS[:5], _VALUE_ATTRS[5:])


def _view(
    engine: RideEngine | _StubEngine,
    *,
    roster: Roster,
    crossing: Crossing | None = None,
) -> crossing_detail.CrossingDetailView:
    """Return a ``CrossingDetailView`` over recording widget doubles.

    Built with ``object.__new__`` (``test_dialogs_positioning.py``'s
    ``_view_over`` precedent): the handlers under test touch only
    these attributes, so no desktop and no ``__init__`` control binding
    is needed.
    """
    view = object.__new__(crossing_detail.CrossingDetailView)
    view.dialog = _RecordingDialog()
    view.crossing = crossing if crossing is not None else engine.crossings[-1]
    view.roster = roster
    view.engine = engine
    for attr in _VALUE_ATTRS:
        setattr(view, attr, _RecordingValueBox())
    view.edit_btn = _RecordingButton()
    view.edit_time_btn = _RecordingButton()
    view.void_card_btn = _RecordingButton()
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


# ------------------------------------------ the dialog's authored shape
#
# Work item B's two changes to the frozen window itself, pinned over
# the XML (a loader test cannot see a label's text, and a view test
# cannot see a control that is absent).


def _crossing_detail_controls() -> list[Element]:
    """Return ``crossing_detail_dlg``'s authored elements."""
    for dialog in parse(_DIALOGS_XRC).iter("object"):
        if dialog.get("name") == ids.CROSSING_DETAIL_DLG:
            return list(dialog.iter("object"))
    message = f"no wxDialog named {ids.CROSSING_DETAIL_DLG!r}"
    raise AssertionError(message)


def _crossing_detail_button_labels() -> dict[str | None, str | None]:
    """Return ``crossing_detail_dlg``'s button labels by name."""
    return {
        element.get("name"): element.findtext("label")
        for element in _crossing_detail_controls()
        if element.get("class") == "wxButton"
    }


def test_crossing_detail_dlg_given_the_read_only_plate_drops_the_plate_field() -> None:
    """The plate is a label, so the editable field is gone."""
    names = [element.get("name") for element in _crossing_detail_controls()]

    assert "edit_plate_input" not in names


def test_crossing_detail_dlg_given_the_read_only_plate_drops_its_caption() -> None:
    """The "New plate" caption goes with the field it captioned."""
    labels = [element.findtext("label") for element in _crossing_detail_controls()]

    assert "New plate" not in labels


def test_crossing_detail_dlg_given_its_correction_row_labels_the_four_buttons() -> None:
    """Edit_btn opens the Plate prompt; the other three do not."""
    labels = _crossing_detail_button_labels()

    assert {
        name: labels.get(name) for name in (ids.EDIT_BTN, ids.EDIT_TIME_BTN, ids.VOID_CARD_BTN)
    } == {
        ids.EDIT_BTN: "Edit Plate",
        ids.EDIT_TIME_BTN: "Edit Time…",
        ids.VOID_CARD_BTN: "Void Card…",
    }
    assert labels.get(ids.DELETE_BTN) == "Delete"


def test_crossing_detail_dlg_given_the_stock_row_authors_no_cancel_button() -> None:
    """OK closes it: the window authors no Cancel to click."""
    labels = _crossing_detail_button_labels()

    assert (labels.get("wxID_OK"), "wxID_CANCEL" in labels) == ("OK", False)


def test_crossing_detail_dlg_given_the_stock_row_places_ok_alone() -> None:
    """Measured: a std sizer places only the stock ids it recognises."""
    sizer = next(
        element
        for element in _crossing_detail_controls()
        if element.get("class") == "wxStdDialogButtonSizer"
    )

    assert [button.find("object").get("name") for button in sizer.findall("object")] == ["wxID_OK"]


# ``_DetailDialogView.__init__``'s own wiring -- the five button
# binds, Escape's route and the dialog's size floor. All need the
# shared ``__init__`` to run, which the ``object.__new__`` doubles above
# cannot reach, so the window is a recording double too and the control
# lookups are stubbed out.


class _InitDialog:
    """A ``wx.Dialog`` double recording ``__init__``'s own wiring."""

    def __init__(self, fitted: tuple[int, int] = (400, 300)) -> None:
        """Report *fitted* from GetSize, like a just-Fit() dialog."""
        self._fitted = wx.Size(*fitted)
        self._sizer: object = object()
        self.escape_id: int | None = None
        self.calls: list[str] = []
        self.min_size: wx.Size | None = None
        self.size: wx.Size | None = None
        self.binds: list[tuple[object, object, object]] = []

    def Bind(self, event: object, handler: object, source: object = None) -> None:  # noqa: N802
        """Record one ``(event, handler, source)`` binding."""
        self.binds.append((event, handler, source))

    def SetEscapeId(self, escape_id: int) -> None:  # noqa: N802
        """Record the id Escape is pointed at."""
        self.escape_id = escape_id

    def GetSizer(self) -> object:  # noqa: N802
        """Return the authored content sizer."""
        return self._sizer

    def SetSizer(  # noqa: N802 -- wx's own method name
        self,
        sizer: object,
        deleteOld: bool = True,  # noqa: FBT001, FBT002, N803, ARG002 -- wx's own parameter
    ) -> None:
        """Record the outer sizer the info bar wraps the content in."""
        self._sizer = sizer

    def Fit(self) -> None:  # noqa: N802
        """Record the fitting call."""
        self.calls.append("Fit")

    def GetSize(self) -> wx.Size:  # noqa: N802
        """Record the read and report the fitted size."""
        self.calls.append("GetSize")
        return wx.Size(self._fitted)

    def SetMinSize(self, size: wx.Size) -> None:  # noqa: N802
        """Record the floor the view applied."""
        self.calls.append("SetMinSize")
        self.min_size = size

    def SetSize(self, size: wx.Size) -> None:  # noqa: N802
        """Record the size the view opened the dialog at."""
        self.calls.append("SetSize")
        self.size = size


_DISPLAY = (0, 25, 1920, 1080)


def _detail_view_init(  # noqa: PLR0913 -- (monkeypatch, fitted, infobar_height, display)
    monkeypatch: pytest.MonkeyPatch,
    *,
    fitted: tuple[int, int] = (400, 300),
    infobar_height: int = 0,
    display: tuple[int, int, int, int] = _DISPLAY,
) -> tuple[_InitDialog, _RecordingInfoBar, crossing_detail._DetailDialogView]:
    """Run ``_DetailDialogView.__init__`` over doubles; report wiring.

    The control lookups and the code-side info bar are both replaced --
    the real ones need a desktop -- so this drives the shared
    ``__init__``'s own three steps: find the nine boxes and five
    buttons, bind the five and point Escape at OK, then floor the
    dialog.
    """
    dialog = _InitDialog(fitted=fitted)
    bar = _RecordingInfoBar(best_height=infobar_height)
    controls: dict[str, object] = {}

    def _find(_self: object, name: str, _expected_type: object = None) -> object:
        control = controls.get(name)
        if control is None:
            control = (
                _RecordingButton(wx.ID_OK) if name == dialogs.WX_ID_OK else _RecordingValueBox()
            )
            controls[name] = control
        return control

    monkeypatch.setattr(crossing_detail._DetailDialogView, "_find", _find)
    monkeypatch.setattr(crossing_detail._DetailDialogView, "_build_infobar", lambda _self: bar)
    monkeypatch.setattr(wx, "GetClientDisplayRect", lambda: display)
    view = crossing_detail._DetailDialogView(dialog, _StubEngine())
    return dialog, bar, view


def test_detail_dialog_view_given_its_five_buttons_binds_each_one_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared base binds every button; a second Bind double-fires.

    The subclasses used to bind their own correction buttons, so leaving
    those in place would now give ``ok_btn`` two handlers and fire
    ``EndModal`` twice (``main_frame``'s measured duplicate-Bind note).
    """
    dialog, _bar, view = _detail_view_init(monkeypatch)

    assert [(event, handler.__name__, source) for event, handler, source in dialog.binds] == [
        (wx.EVT_BUTTON, "_on_ok", view.ok_btn),
        (wx.EVT_BUTTON, "_on_edit", view.edit_btn),
        (wx.EVT_BUTTON, "_on_edit_time", view.edit_time_btn),
        (wx.EVT_BUTTON, "_on_void_card", view.void_card_btn),
        (wx.EVT_BUTTON, "_on_delete", view.delete_btn),
    ]


def test_detail_dialog_view_given_the_cancel_less_row_points_escape_at_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-76: with no Cancel authored, Escape still leaves the dialog."""
    dialog, _bar, view = _detail_view_init(monkeypatch)

    assert (dialog.escape_id, view.ok_btn.button_id) == (wx.ID_OK, wx.ID_OK)


def test_detail_dialog_view_given_a_fitted_size_floors_the_dialog_by_the_infobar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fit() measures; the floor adds the info bar's own room."""
    dialog, _bar, _view = _detail_view_init(monkeypatch, fitted=(400, 300), infobar_height=40)

    assert (dialog.calls, (dialog.min_size.width, dialog.min_size.height)) == (
        ["Fit", "GetSize", "SetMinSize", "SetSize"],
        (400, 340),
    )
    assert (dialog.size.width, dialog.size.height) == (400, 340)


def test_detail_dialog_view_given_an_unmeasured_infobar_falls_back_to_the_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3/T-4 boundary: a 0-height report still reserves room."""
    dialog, _bar, _ok = _detail_view_init(monkeypatch, fitted=(400, 300), infobar_height=0)

    expected = (400, 300 + crossing_detail._INFOBAR_ALLOWANCE)
    assert (dialog.min_size.width, dialog.min_size.height) == expected
    assert (dialog.size.width, dialog.size.height) == expected


@pytest.mark.parametrize(
    ("fitted", "infobar_height", "display", "expected"),
    [
        ((400, 300), 40, (0, 25, 1920, 1080), (400, 340)),  # roomy: the floor stands
        ((400, 300), 40, (0, 25, 400, 340), (400, 340)),  # T-4 boundary: exactly the floor
        ((400, 300), 40, (0, 25, 400, 339), (400, 339)),  # max - 1: the display wins
        ((400, 300), 40, (0, 25, 300, 200), (300, 200)),  # a small screen clamps both
    ],
    ids=["roomy", "exact_floor", "one_short", "small_screen"],
)
def test_detail_dialog_view_given_a_display_clamps_the_floored_size(  # noqa: PLR0913, PLR0917
    monkeypatch: pytest.MonkeyPatch,
    fitted: tuple[int, int],
    infobar_height: int,
    display: tuple[int, int, int, int],
    expected: tuple[int, int],
) -> None:
    """The clamped size is both the floor and the opened size (T-4)."""
    dialog, _bar, _ok = _detail_view_init(
        monkeypatch, fitted=fitted, infobar_height=infobar_height, display=display
    )

    assert (dialog.min_size.width, dialog.min_size.height) == expected
    assert (dialog.size.width, dialog.size.height) == expected


def test_ids_given_the_removed_plate_field_declares_no_plate_input_constant() -> None:
    """ui/ids.py mirrors the .xrc (R-05); the constant goes too."""
    assert not hasattr(ids, "EDIT_PLATE_INPUT")


# ------------------------------------------- the "Details" group (G2)
#
# The nine value fields are read-only entry boxes now, laid out as four
# vertical columns in a row inside one "Details" group: the caption
# column and its own value column, twice. The nine frozen names are
# unchanged, so ui/ids.py is unchanged too -- only the class behind each
# name and the sizer structure around them moved.

# The two caption columns' text, in the order the dialog draws them.
_CAPTION_COLUMNS = (
    ("Rider", "Team", "Plate", "Lap #", "Crossing time"),
    ("Lap time", "Total time", "Card", "Status"),
)


def _param(element: Element, tag: str) -> str:
    """Return the text of *element*'s direct ``<tag>`` child, or ""."""
    child = element.find(tag)
    return "" if child is None or child.text is None else child.text


def _crossing_detail_objects() -> dict[str, Element]:
    """Map each named control of the dialog to its element."""
    return {
        element.get("name"): element
        for element in _crossing_detail_controls()
        if element.get("name") is not None
    }


def _details_box() -> Element:
    """Return ``crossing_detail_dlg``'s one ``wxStaticBoxSizer``."""
    return next(
        element
        for element in _crossing_detail_controls()
        if element.get("class") == "wxStaticBoxSizer"
    )


def _group_items() -> list[Element]:
    """Return the "Details" box's four column items, in order."""
    return [item for item in _details_box() if item.get("class") == "sizeritem"]


def _group_box(column: int) -> Element:
    """Return the ``wxBoxSizer`` one column sizer item wraps."""
    return _group_items()[column].find("object")


def _group_cells(column: int) -> list[Element]:
    """Return the controls one column holds, in order."""
    return [item.find("object") for item in _group_box(column).findall("object")]


def _value_sizeritem(name: str) -> Element:
    """Return the sizer item wrapping the control named *name*."""
    return next(
        element
        for element in _crossing_detail_controls()
        if element.get("class") == "sizeritem"
        and element.find("object") is not None
        and element.find("object").get("name") == name
    )


def test_crossing_detail_dlg_given_its_fields_wraps_them_in_one_details_group() -> None:
    """G2: one horizontal "Details" group box holds the fields."""
    boxes = [
        element
        for element in _crossing_detail_controls()
        if element.get("class") == "wxStaticBoxSizer"
    ]

    assert [(element.findtext("label"), _param(element, "orient")) for element in boxes] == [
        ("Details", "wxHORIZONTAL")
    ]


def test_crossing_detail_dlg_given_its_details_group_lays_four_columns_in_a_row() -> None:
    """G2: caption, value, caption, value -- four vertical columns."""
    columns = _group_items()

    assert [_param(item.find("object"), "orient") for item in columns] == ["wxVERTICAL"] * 4


@pytest.mark.parametrize(
    ("column", "captions"), [(0, 0), (2, 1)], ids=["first_half", "second_half"]
)
def test_crossing_detail_dlg_given_a_caption_column_lists_its_labels(
    column: int, captions: int
) -> None:
    """G2: the captions keep their copy and carry no frozen name."""
    cells = _group_cells(column)

    assert [cell.findtext("label") for cell in cells] == list(_CAPTION_COLUMNS[captions])
    assert [cell.get("name") for cell in cells] == [None] * len(_CAPTION_COLUMNS[captions])


@pytest.mark.parametrize(
    ("column", "names"),
    [(1, _VALUE_COLUMNS[0]), (3, _VALUE_COLUMNS[1])],
    ids=["first_half", "second_half"],
)
def test_crossing_detail_dlg_given_a_value_column_declares_its_frozen_names(
    column: int, names: tuple[str, ...]
) -> None:
    """G2: the nine frozen ``crossing_*_lbl`` names keep their order."""
    cells = _group_cells(column)

    assert [cell.get("name") for cell in cells] == list(names)


@pytest.mark.parametrize("value_name", _VALUE_ATTRS)
def test_crossing_detail_dlg_value_field_is_a_read_only_entry_box(value_name: str) -> None:
    """G2: each value field is a 180-wide read-only ``wxTextCtrl``."""
    control = _crossing_detail_objects()[value_name]

    assert (control.get("class"), _param(control, "style"), _param(control, "size")) == (
        "wxTextCtrl",
        "wxTE_READONLY",
        "180,-1",
    )


@pytest.mark.parametrize("value_name", _VALUE_ATTRS)
def test_crossing_detail_dlg_value_box_takes_the_columns_slack(value_name: str) -> None:
    """G2: each entry box is laid out with ``wxEXPAND``."""
    item = _value_sizeritem(value_name)

    assert "wxEXPAND" in _param(item, "flag")


@pytest.mark.parametrize("column", [0, 2], ids=["first_half", "second_half"])
def test_crossing_detail_dlg_caption_column_takes_no_option_growth(column: int) -> None:
    """G2: a caption column auto-sizes to its longest label."""
    assert _param(_group_items()[column], "option") == ""


@pytest.mark.parametrize("value_name", _VALUE_ATTRS)
def test_crossing_detail_dlg_value_row_holds_the_caption_rows_stride(value_name: str) -> None:
    """G2 measured: a 24 px box + 6 px == the 16 px caption + 14 px."""
    assert _param(_value_sizeritem(value_name), "border") == "6"


def test_crossing_detail_dlg_caption_row_holds_the_value_rows_stride() -> None:
    """G2: a caption column and its value column are separate sizers.

    Nothing aligns their rows for them, so both take a 30 px row stride
    (measured: a caption stands 16 px tall against a 24 px box) and each
    caption sits beside its own box.
    """
    borders = {
        _param(item, "border")
        for column in (0, 2)
        for item in _group_box(column).findall("object")
    }

    assert borders == {"14"}


# ---------------------------------------------------------- render


def test_render_given_a_lone_crossing_fills_every_value_box() -> None:
    """The nine read-only entry boxes carry the built field values."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)

    view.render()

    assert (
        view.crossing_rider_lbl.value,
        view.crossing_team_lbl.value,
        view.crossing_plate_lbl.value,
        view.crossing_lap_lbl.value,
        view.crossing_time_lbl.value,
        view.crossing_lap_time_lbl.value,
        view.crossing_total_lbl.value,
    ) == ("Amy", "solo", "12", "1", "10:02:00", "2:00", "0:02:00")
    assert view.crossing_card_lbl.value == format_card(engine.card_for(engine.crossings[0]).code())
    assert view.crossing_held_lbl.value == "Credited"


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


# ----------------------------------- Edit Time / Void Card (Phase 2)
#
# The crossing detail's two new corrections: edit_time_btn retimes the
# crossing through ``edit_crossing_dlg`` in edit mode, void_card_btn
# voids the crossing's own dealt card through ``void_card_confirm_dlg``.
#
# logic-coverage-exempt: T-13 -- the Void Card gate is two booleans,
# ``_card_is_credited() or _is_duplicate()``, and its fourth row
# (credited AND duplicate) is unobservable: ``or`` short-circuits and
# the right operand is a pure read, so (true, false) and (true, true)
# enable the identical button. The three observable rows are pinned
# below and in the duplicate cases above.


def test_render_given_a_credited_card_enables_the_new_corrections() -> None:
    """A credited card is voidable; Edit Time is always offered."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)

    view.render()

    assert (view.edit_time_btn.enabled, view.void_card_btn.enabled) == (True, True)


def test_render_given_a_held_card_disables_void_card() -> None:
    """R-34: a held card is the review surface's, not voidable here."""
    roster = _solo_roster()
    engine = _running_engine(roster, min_lap_s=1080, hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)

    view.render()

    assert (view.edit_time_btn.enabled, view.void_card_btn.enabled) == (True, False)


def test_render_given_a_voided_card_disables_void_card() -> None:
    """T-3 negative: a card already out of the ride cannot be voided."""
    roster = _solo_roster()
    engine = _running_engine(roster, min_lap_s=1080, hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.void_held(engine.crossings[0])
    view = _view(engine, roster=roster)

    view.render()

    assert (view.edit_time_btn.enabled, view.void_card_btn.enabled) == (True, False)


def test_render_given_a_duplicate_whose_card_is_held_enables_void_card() -> None:
    """Phase 3: voiding one twin is what a held duplicate needs."""
    roster = _solo_roster()
    engine = _running_engine(roster, min_lap_s=1080, hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster, crossing=engine.crossings[1])

    view.render()

    assert (view.void_card_btn.enabled, view.crossing_held_lbl.value) == (True, "Duplicate")


def test_render_given_a_pending_miss_disables_both_new_corrections() -> None:
    """A miss is neither retimeable nor voidable."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, roster=roster)

    view.render()

    assert (view.edit_time_btn.enabled, view.void_card_btn.enabled) == (False, False)


def _stub_run_edit_crossing(
    monkeypatch: pytest.MonkeyPatch, result: CrossingEdit | None
) -> list[dict[str, object]]:
    """Stub ``corrections.run_edit_crossing`` to return *result*."""
    calls: list[dict[str, object]] = []

    def _run(_resource: object, **kwargs: object) -> CrossingEdit | None:
        calls.append(kwargs)
        return result

    monkeypatch.setattr(corrections, "run_edit_crossing", _run)
    return calls


@pytest.mark.parametrize(
    ("rider_plate", "expected"),
    [("12", "12"), (None, "12")],
    ids=["typed_plate", "no_typed_plate"],
)
def test_on_edit_time_given_a_crossing_opens_the_locked_edit_time_dialog(
    monkeypatch: pytest.MonkeyPatch,
    rider_plate: str | None,
    expected: str,
) -> None:
    """T-4 nullable: the dialog opens on the plate the view shows."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    crossing = Crossing(
        entry_id=entry_key(roster, "12"), seq=1, crossed_at=_dt(10, 2), rider_plate=rider_plate
    )
    view = _view(engine, roster=roster, crossing=crossing)
    calls = _stub_run_edit_crossing(monkeypatch, None)

    view._on_edit_time(_RecordingEvent())

    assert calls == [
        {
            "frame": view.dialog,
            "adding": False,
            "plate": expected,
            "time": "10:02:00",
            "seq": 1,
            "base_date": engine.config.event_date,
            "title": "Edit Time",
            "read_only_plate": True,
            "suppress_void": True,
        }
    ]
    assert view.dialog.modal_ids == []


def test_on_edit_time_given_a_confirmed_edit_rerenders_the_crossing_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Work item B: the retime commits and keeps the dialog open."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)
    _stub_run_edit_crossing(
        monkeypatch,
        CrossingEdit(entry_id="12", seq=1, crossed_at=_dt(10, 3), reason="wrong clock"),
    )
    event = _RecordingEvent()

    view._on_edit_time(event)

    assert [(c.seq, c.crossed_at) for c in engine.crossings] == [(1, _dt(10, 3))]
    assert engine.events[-1].action == "edit_crossing"
    assert engine.events[-1].payload["reason"] == "wrong clock"
    assert (view.dialog.modal_ids, event.skipped) == ([], True)
    assert view.crossing is engine.crossings[0]
    assert view.crossing_time_lbl.value == "10:03:00"


def test_on_edit_time_given_a_mid_ride_crossing_rerenders_that_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The re-render stays on *this* crossing, not the ride's newest."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))
    engine.record_crossing("12", at=_dt(10, 7))
    view = _view(engine, roster=roster, crossing=engine.crossings[0])
    _stub_run_edit_crossing(
        monkeypatch,
        CrossingEdit(entry_id="12", seq=1, crossed_at=_dt(10, 1), reason="wrong clock"),
    )

    view._on_edit_time(_RecordingEvent())

    assert view.crossing is engine.crossings[0]
    assert [c.crossed_at for c in engine.crossings] == [_dt(10, 1), _dt(10, 5), _dt(10, 7)]
    assert (view.crossing_time_lbl.value, view.crossing_lap_lbl.value) == ("10:01:00", "1")


def test_on_edit_time_given_a_seqless_submission_retimes_the_crossings_own_lap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-4 nullable: with no seq the view's own lap is the target."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)
    _stub_run_edit_crossing(
        monkeypatch,
        CrossingEdit(entry_id="12", seq=None, crossed_at=_dt(10, 3), reason="wrong clock"),
    )

    view._on_edit_time(_RecordingEvent())

    assert [(c.seq, c.crossed_at) for c in engine.crossings] == [(1, _dt(10, 3))]
    assert engine.events[-1].payload["seq"] == 1


def test_on_edit_time_given_a_cancelled_dialog_leaves_the_ride_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: Cancel commits nothing and closes nothing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)
    _stub_run_edit_crossing(monkeypatch, None)
    events_before = len(engine.events)

    view._on_edit_time(_RecordingEvent())

    assert (len(engine.events), view.dialog.modal_ids) == (events_before, [])
    assert view.crossing_detail_infobar.messages == []


def test_on_edit_time_given_a_finished_ride_keeps_the_dialog_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finished ride refuses; the dialog stays open."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.finish()
    view = _view(engine, roster=roster)
    _stub_run_edit_crossing(
        monkeypatch,
        CrossingEdit(entry_id="12", seq=1, crossed_at=_dt(10, 3), reason="wrong clock"),
    )

    view._on_edit_time(_RecordingEvent())

    assert view.dialog.modal_ids == []
    assert view.crossing_detail_infobar.messages == [
        "Could not edit crossing: cannot edit crossing from finished"
    ]


@pytest.mark.parametrize(
    ("crossed_at", "expected"),
    [
        (_dt(10, 2), "2026-09-20T10:02:00 is not after 2026-09-20T10:02:00"),  # at: T-4 max
        (
            _dt(10, 1),
            "2026-09-20T10:01:00 is not after 2026-09-20T10:02:00",
        ),  # before: T-4 max + 1
    ],
    ids=["at_predecessor", "before_predecessor"],
)
def test_on_edit_time_given_a_lap_retimed_at_or_before_its_predecessor_keeps_the_open_dialog(
    monkeypatch: pytest.MonkeyPatch,
    crossed_at: datetime,
    expected: str,
) -> None:
    """T-5: a bad lap time is a ValueError, not a RideEngineError."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))
    view = _view(engine, roster=roster, crossing=engine.crossings[1])
    _stub_run_edit_crossing(
        monkeypatch,
        CrossingEdit(entry_id="12", seq=2, crossed_at=crossed_at, reason="wrong clock"),
    )

    view._on_edit_time(_RecordingEvent())

    assert view.dialog.modal_ids == []
    assert view.crossing_detail_infobar.messages == [
        f"Could not edit crossing: lap time must be positive for entry 12: {expected}"
    ]
    assert [c.crossed_at for c in engine.crossings] == [_dt(10, 2), _dt(10, 5)]


def test_on_edit_time_given_a_stale_crossing_keeps_the_dialog_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-5: a stale crossing is refused, not left to StopIteration."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    stale = Crossing(entry_id="12", seq=1, crossed_at=_dt(10, 2), rider_plate="12")
    view = _view(engine, roster=roster, crossing=stale)
    _stub_run_edit_crossing(
        monkeypatch,
        CrossingEdit(entry_id="12", seq=1, crossed_at=_dt(10, 3), reason="wrong clock"),
    )
    events_before = len(engine.events)

    view._on_edit_time(_RecordingEvent())

    assert view.dialog.modal_ids == []
    assert view.crossing_detail_infobar.messages == [
        "Could not edit crossing: this crossing is no longer recorded."
    ]
    assert (len(engine.events), [c.crossed_at for c in engine.crossings]) == (
        events_before,
        [_dt(10, 2)],
    )


def _stub_run_void_card(
    monkeypatch: pytest.MonkeyPatch, result: CardVoid | None
) -> list[dict[str, object]]:
    """Stub ``corrections.run_void_card`` to return *result*."""
    calls: list[dict[str, object]] = []

    def _run(_resource: object, **kwargs: object) -> CardVoid | None:
        calls.append(kwargs)
        return result

    monkeypatch.setattr(corrections, "run_void_card", _run)
    return calls


class _RecordingVoidEngine(RideEngine):
    """A ``RideEngine`` remembering the object ``void_card`` was handed.

    The one fact the dialog's own outcome cannot show: whether the
    handler passed the engine's dealt object or a fresh, value-equal
    ``Card.parse`` copy of the confirm dialog's code -- which Phase 1's
    identity-keyed registry and hold guard no longer recognise.
    """

    voided_card: Card | None = None

    def void_card(self, entry_id: str, card: Card, reason: str) -> Event:
        """Record *card*, then run the engine's own void."""
        self.voided_card = card
        return super().void_card(entry_id, card, reason)


def test_on_void_card_given_a_confirmed_void_passes_the_engines_own_dealt_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity, never the confirm dialog's returned code.

    ``void_card`` keys the registry and guards the hold queue by object
    identity, so a parsed copy of the code would name a different
    physical card -- and, on a held duplicate, slip past the guard.
    """
    roster = _solo_roster()
    engine = _running_engine(roster, engine_class=_RecordingVoidEngine)
    engine.record_crossing("12", at=_dt(10, 2))
    crossing = engine.crossings[0]
    card = engine.card_for(crossing)
    view = _view(engine, roster=roster)
    _stub_run_void_card(
        monkeypatch, CardVoid(entry_id="12", card=card.code(), reason="wrong card")
    )

    view._on_void_card(_RecordingEvent())

    assert engine.voided_card is card
    assert (view.crossing_held_lbl.value, engine.credited_cards(entry_key(roster, "12"))) == (
        "Void",
        (),
    )


def test_on_void_card_given_a_held_duplicate_card_keeps_the_held_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The identity hold guard still recognises the card it is given.

    A duplicate crossing's held card is voidable from this dialog, and
    ``void_card`` refuses it with the hold message. A parsed copy of
    the code is another object, so the guard misses and the refusal
    degrades to "no dealt card ... credited".
    """
    roster = _solo_roster()
    engine = _running_engine(roster, min_lap_s=1080, hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 2))
    crossing = engine.crossings[1]
    card = engine.card_for(crossing)
    view = _view(engine, roster=roster, crossing=crossing)
    _stub_run_void_card(
        monkeypatch, CardVoid(entry_id="12", card=card.code(), reason="wrong card")
    )

    view._on_void_card(_RecordingEvent())

    assert view.crossing_detail_infobar.messages == [
        "Could not void card: card is held; confirm or void it through the review panel"
    ]


def test_on_void_card_given_a_confirmed_void_rerenders_the_crossing_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Work item B: the void commits and keeps the dialog open."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    crossing = engine.crossings[0]
    card = engine.card_for(crossing)
    view = _view(engine, roster=roster)
    calls = _stub_run_void_card(
        monkeypatch, CardVoid(entry_id="12", card=card.code(), reason="wrong card")
    )
    event = _RecordingEvent()

    view._on_void_card(event)

    assert calls == [
        {
            "frame": view.dialog,
            "entry_id": "12",
            "card": card.code(),
            "entry": "12 · Amy",
        }
    ]
    assert engine.credited_cards(entry_key(roster, "12")) == ()
    assert engine.events[-1].action == "void_card"
    assert engine.events[-1].payload["reason"] == "wrong card"
    assert (view.dialog.modal_ids, event.skipped) == ([], True)
    assert (view.crossing_held_lbl.value, view.void_card_btn.enabled) == ("Void", False)


def test_on_void_card_given_a_pooled_team_crossing_names_the_typing_rider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """xrc-windows.md: the confirm names the dealt-to entry.

    "45 · J. Okafor" -- the rider whose plate was typed (J1), not the
    Team column's own display name and never a bare "solo".
    """
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_crossing("45", at=_dt(10, 2))
    card = engine.card_for(engine.crossings[0])
    view = _view(engine, roster=roster)
    calls = _stub_run_void_card(
        monkeypatch, CardVoid(entry_id="45", card=card.code(), reason="wrong card")
    )

    view._on_void_card(_RecordingEvent())

    assert calls == [
        {
            "frame": view.dialog,
            "entry_id": "9",
            "card": card.code(),
            "entry": "45 · Sarah",
        }
    ]
    assert engine.credited_cards(entry_key(engine._roster, "9")) == ()


def test_on_void_card_given_a_cancelled_dialog_leaves_the_ride_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: Cancel voids nothing and closes nothing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)
    _stub_run_void_card(monkeypatch, None)
    events_before = len(engine.events)

    view._on_void_card(_RecordingEvent())

    assert (len(engine.events), view.dialog.modal_ids) == (events_before, [])
    assert view.crossing_detail_infobar.messages == []


def test_on_void_card_given_a_finished_ride_keeps_the_dialog_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finished ride refuses; the dialog stays open."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    card = engine.card_for(engine.crossings[0])
    engine.finish()
    view = _view(engine, roster=roster)
    _stub_run_void_card(
        monkeypatch, CardVoid(entry_id="12", card=card.code(), reason="wrong card")
    )

    view._on_void_card(_RecordingEvent())

    assert view.dialog.modal_ids == []
    assert view.crossing_detail_infobar.messages == [
        "Could not void card: cannot void card from finished"
    ]


# ------------------------------------------------------------ Edit
#
# §9: in crossing mode Edit opens the ``crossing_number_dlg``
# Save/Cancel prompt and commits its Save through the engine's own
# ``reassign_crossing``, which resolves the plate and refuses a blank
# or unknown one. ``run_plate_dialog`` is the prompt's loader+show
# seam (the wx boundary); the tests below stub it so the handler's
# branches are pinned headlessly, and its own tests drive it against a
# stub resource.


def _stub_plate_dialog(
    monkeypatch: pytest.MonkeyPatch, result: str | None
) -> list[dict[str, object]]:
    """Stub ``run_plate_dialog`` to return *result*.

    Each call's ``(opener, plate)`` is recorded for the caller.
    """
    calls: list[dict[str, object]] = []

    def _run(_resource: object, *, opener: object, plate: str) -> str | None:
        calls.append({"opener": opener, "plate": plate})
        return result

    monkeypatch.setattr(crossing_detail, "run_plate_dialog", _run)
    return calls


def test_on_edit_given_a_confirmed_number_reassigns_and_rerenders_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§9 + work item B: the prompt's Save commits, the dialog stays."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)
    _stub_plate_dialog(monkeypatch, "34")
    event = _RecordingEvent()

    view._on_edit(event)

    assert [(c.entry_id, c.rider_plate) for c in engine.crossings] == [
        (entry_key(roster, "34"), "34")
    ]
    assert engine.events[-1].action == "reassign"
    assert engine.events[-1].payload["reason"] == crossing_detail.EDIT_REASON
    assert engine.events[-1].payload["new_plate"] == "34"
    assert (view.dialog.modal_ids, event.skipped) == ([], True)
    assert view.crossing is engine.crossings[0]
    assert view.crossing_plate_lbl.value == "34"


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
    roster = _solo_roster()
    crossing = Crossing(
        entry_id=entry_key(roster, "12"), seq=1, crossed_at=_dt(10, 2), rider_plate=rider_plate
    )
    engine = _running_engine(roster)
    view = _view(engine, roster=roster, crossing=crossing)
    calls = _stub_plate_dialog(monkeypatch, None)

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
    _stub_plate_dialog(monkeypatch, None)
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
    _stub_plate_dialog(monkeypatch, "")

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
    _stub_plate_dialog(monkeypatch, "999")

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
    _stub_plate_dialog(monkeypatch, "34")

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
    _stub_plate_dialog(monkeypatch, "34")

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
    _stub_plate_dialog(monkeypatch, "12")

    view._on_edit(_RecordingEvent())

    assert [c.entry_id for c in engine.crossings] == [entry_key(roster, "12")] * 3
    assert engine.events[-1].payload["seq"] == 2
    assert engine.events[-1].payload["old_entry_id"] == entry_key(roster, "34")
    assert (view.crossing.entry_id, view.crossing.seq) == (entry_key(roster, "12"), 3)
    assert view.crossing_time_lbl.value == "10:03:00"


# The re-render above rides on ``_commit_plate``'s own report: True when
# the reassign committed, False when the engine refused it.


def test_commit_plate_given_a_recorded_crossing_reports_success() -> None:
    """A committed plate moves the view onto the reassigned crossing."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)

    committed = view._commit_plate("34")

    assert (committed, view.dialog.modal_ids) == (True, [])
    assert (view.crossing.rider_plate, view.crossing_plate_lbl.value) == ("34", "34")
    assert view.crossing_detail_infobar.messages == []


def test_commit_plate_given_a_stale_crossing_reports_failure() -> None:
    """T-3 negative: a refused commit reports False and shows why."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    stale = Crossing(entry_id="12", seq=1, crossed_at=_dt(10, 2), rider_plate="12")
    view = _view(engine, roster=roster, crossing=stale)

    committed = view._commit_plate("34")

    assert (committed, view.dialog.modal_ids) == (False, [])
    assert view.crossing_detail_infobar.messages == [
        "Could not reassign: this crossing is no longer recorded."
    ]


def test_commit_plate_given_a_plate_already_crossing_then_points_at_the_new_crossing() -> None:
    """The locator regression: the reassign appends, so the last is it.

    ``reassign_crossing`` removes the crossing and appends its
    replacement, so a plate/instant lookup against the record would find
    the **older** twin recorded at the same instant under the same plate
    (the documented duplicate case) instead of the crossing just moved.
    """
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("34", at=_dt(10, 2))
    twin = engine.crossings[0]
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster, crossing=engine.crossings[1])

    committed = view._commit_plate("34")

    assert committed is True
    assert engine.duplicate_crossings() == ((twin, engine.crossings[-1]),)
    assert view.crossing is engine.crossings[-1]
    assert (view.crossing_plate_lbl.value, view.crossing_lap_lbl.value) == ("34", "2")


# -------------------------------------------------------------- OK


def test_on_ok_given_a_crossing_closes_without_touching_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Work item B: OK is the crossing mode's only exit."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)
    replays = _stub_reassign_plate(monkeypatch, None)
    events_before = len(engine.events)

    view._on_ok(_RecordingEvent())

    assert (view.dialog.modal_ids, len(engine.events)) == ([wx.ID_OK], events_before)
    assert (replays, view.crossing_detail_infobar.messages) == ([], [])


def test_on_ok_given_a_crossing_does_not_skip_the_event() -> None:
    """Measured: a Skip lets wx's stock OK close the dialog."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    view = _view(engine, roster=roster)
    event = _RecordingEvent()

    view._on_ok(event)

    assert event.skipped is False


# ---------------------------------------------------------- Delete

# The Delete confirm's own copy for Amy's 10:02 lap-1 crossing (Undo)
# and for Amy's 10:05 lap-2 crossing (the specific-crossing delete).
_DELETE_MESSAGE = (
    "Undo crossing 10:02:00 · Amy · lap 1? The newest crossing and its dealt card are removed."
)
_VOID_MESSAGE = (
    "Delete crossing 10:05:00 · Amy · lap 2? The crossing and its card are voided; "
    "the entry's later laps renumber."
)


def _delete_fields(
    *, lap: str = "1", time: str = "10:02:00"
) -> crossing_detail.CrossingDetailFields:
    """Return the view-model Delete's confirm copy renders from."""
    return crossing_detail.CrossingDetailFields(
        rider="Amy",
        team="solo",
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


def test_delete_message_given_an_earlier_crossing_names_the_delete() -> None:
    """T-13 false: any other crossing's confirm reads Delete."""
    message = crossing_detail.delete_message(
        _delete_fields(lap="2", time="10:05:00"), newest=False
    )

    assert message == _VOID_MESSAGE
    assert message.startswith("Delete crossing ")


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
        "entry_id": entry_key(engine._roster, "12"),
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

    assert (
        len(engine.credited_cards(entry_key(engine._roster, "12"))),
        engine.shoe_remaining,
    ) == (2, engine.shoe_total - 3)


def test_on_delete_given_an_earlier_crossing_names_the_crossing_it_voids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UX-DESKTOP §4: the void question names the crossing."""
    _engine, view = _three_lap_view(1)
    calls = _stub_danger(monkeypatch, wx.ID_CANCEL)

    view._on_delete(_RecordingEvent())

    assert calls == [(view.dialog, "Delete Crossing?", _VOID_MESSAGE, "Delete", "Cancel")]


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


# ------------------------------ the shared corrections (Phase 7)
#
# The Crossing Detail dialog's two corrections are the console feed's
# own key flows too (Delete/Ctrl+D removes the selected row, Ctrl+E
# retypes its plate), so both bodies are module-level helpers here: the
# dialog adds only the info bar and the EndModal, the app adds only the
# status bar and the feed refresh.


def test_confirm_delete_crossing_given_a_cancel_removes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: Cancel runs no engine command."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    _stub_danger(monkeypatch, wx.ID_CANCEL)
    refusals: list[str] = []
    events_before = len(engine.events)

    confirmed = crossing_detail.confirm_delete_crossing(
        _RecordingDialog(), engine.crossings[0], roster, engine, on_refusal=refusals.append
    )

    assert (confirmed, len(engine.crossings), len(engine.events)) == (False, 1, events_before)
    assert refusals == []


def test_confirm_delete_crossing_given_the_newest_crossing_names_the_undo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-13 true: the newest crossing keeps the Undo wording."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    parent = _RecordingDialog()
    refusals: list[str] = []
    calls = _stub_danger(monkeypatch, wx.ID_CANCEL)

    crossing_detail.confirm_delete_crossing(
        parent, engine.crossings[0], roster, engine, on_refusal=refusals.append
    )

    assert calls == [(parent, "Undo Last Crossing?", _DELETE_MESSAGE, "Undo", "Cancel")]


def test_confirm_delete_crossing_given_the_newest_crossing_runs_undo_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``undo_last`` removes the newest crossing; it reports True."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))
    _stub_danger(monkeypatch, wx.ID_OK)
    refusals: list[str] = []

    confirmed = crossing_detail.confirm_delete_crossing(
        _RecordingDialog(), engine.crossings[-1], roster, engine, on_refusal=refusals.append
    )

    assert (confirmed, [c.seq for c in engine.crossings], refusals) == (True, [1], [])
    assert engine.events[-1].action == "undo"


def test_confirm_delete_crossing_given_an_earlier_crossing_names_the_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-13 false: any other crossing's confirm reads Delete."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))
    engine.record_crossing("12", at=_dt(10, 7))
    parent = _RecordingDialog()
    refusals: list[str] = []
    calls = _stub_danger(monkeypatch, wx.ID_CANCEL)

    crossing_detail.confirm_delete_crossing(
        parent, engine.crossings[1], roster, engine, on_refusal=refusals.append
    )

    assert calls == [(parent, "Delete Crossing?", _VOID_MESSAGE, "Delete", "Cancel")]


def test_confirm_delete_crossing_given_an_earlier_crossing_voids_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§5: a non-newest crossing is voided, later laps renumbering."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))
    engine.record_crossing("12", at=_dt(10, 7))
    _stub_danger(monkeypatch, wx.ID_OK)
    refusals: list[str] = []

    confirmed = crossing_detail.confirm_delete_crossing(
        _RecordingDialog(), engine.crossings[1], roster, engine, on_refusal=refusals.append
    )

    assert (confirmed, refusals) == (True, [])
    assert [(c.seq, c.crossed_at) for c in engine.crossings] == [(1, _dt(10, 2)), (2, _dt(10, 7))]
    assert engine.events[-1].action == "void_crossing"
    assert engine.events[-1].payload["reason"] == crossing_detail.DELETE_REASON


def test_confirm_delete_crossing_given_a_finished_ride_reports_the_undo_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-5 negative: ``undo_last``'s refusal is reported, not raised."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.finish()
    _stub_danger(monkeypatch, wx.ID_OK)
    refusals: list[str] = []

    confirmed = crossing_detail.confirm_delete_crossing(
        _RecordingDialog(), engine.crossings[0], roster, engine, on_refusal=refusals.append
    )

    assert (confirmed, refusals) == (False, ["Undo unavailable: cannot undo from finished"])
    assert len(engine.crossings) == 1


def test_confirm_delete_crossing_given_a_finished_ride_reports_the_void_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-5 negative: ``void_crossing``'s refusal is reported too."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 5))
    engine.finish()
    _stub_danger(monkeypatch, wx.ID_OK)
    refusals: list[str] = []

    confirmed = crossing_detail.confirm_delete_crossing(
        _RecordingDialog(), engine.crossings[0], roster, engine, on_refusal=refusals.append
    )

    assert (confirmed, refusals) == (False, ["Could not void: cannot void crossing from finished"])
    assert len(engine.crossings) == 2


def test_reassign_crossing_plate_given_a_recorded_crossing_reassigns_it() -> None:
    """A recorded crossing moves to the new plate; ``None``."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))

    refusal = crossing_detail.reassign_crossing_plate(engine, engine.crossings[0], "34")

    assert refusal is None
    assert [(c.entry_id, c.rider_plate) for c in engine.crossings] == [
        (entry_key(roster, "34"), "34")
    ]
    assert engine.events[-1].action == "reassign"
    assert engine.events[-1].payload["reason"] == crossing_detail.EDIT_REASON
    assert engine.events[-1].payload["new_plate"] == "34"


def test_reassign_crossing_plate_given_a_mid_ride_crossing_uses_the_ride_wide_ordinal() -> None:
    """``reassign_crossing``'s seq is the record-order ordinal.

    Bob's lap 1 sits at ride-wide ordinal 2 (Amy's lap 1 precedes it),
    so reassigning it must move *that* crossing.
    """
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))  # ride-wide 1
    engine.record_crossing("34", at=_dt(10, 3))  # ride-wide 2
    engine.record_crossing("12", at=_dt(10, 6))  # ride-wide 3

    refusal = crossing_detail.reassign_crossing_plate(engine, engine.crossings[1], "12")

    assert refusal is None
    assert [c.entry_id for c in engine.crossings] == [entry_key(roster, "12")] * 3
    assert engine.events[-1].payload["seq"] == 2
    assert engine.events[-1].payload["old_entry_id"] == entry_key(roster, "34")


def test_reassign_crossing_plate_given_a_stale_crossing_returns_the_refusal() -> None:
    """T-5 negative: a crossing the engine no longer holds refuses."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    stale = Crossing(entry_id="12", seq=1, crossed_at=_dt(10, 2), rider_plate="12")
    events_before = len(engine.events)

    refusal = crossing_detail.reassign_crossing_plate(engine, stale, "12")

    assert refusal == "Could not reassign: this crossing is no longer recorded."
    assert len(engine.events) == events_before


def test_reassign_crossing_plate_given_an_unknown_plate_returns_the_refusal() -> None:
    """T-5 negative: an unknown plate's refusal is returned."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))

    refusal = crossing_detail.reassign_crossing_plate(engine, engine.crossings[0], "999")

    assert refusal == "Could not reassign: unknown plate: 999"


def test_reassign_crossing_plate_given_a_finished_ride_returns_the_refusal() -> None:
    """T-5 negative: a locked ride's refusal is returned, not raised."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    engine.finish()

    refusal = crossing_detail.reassign_crossing_plate(engine, engine.crossings[0], "34")

    assert refusal == "Could not reassign: cannot reassign crossing from finished"


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
    roster: Roster,
    miss: PendingMiss | None = None,
) -> crossing_detail.MissDetailView:
    """Return a ``MissDetailView`` over recording widget doubles.

    Built with ``object.__new__`` (``_view``'s own precedent): the
    handlers under test touch only these attributes, so no desktop and
    no ``__init__`` control binding is needed. *roster* is the ride's
    own roster, the attribute ``MissDetailView`` keeps beside the
    crossing mode's. ``crossing`` is ``None``: this view is in miss mode
    until its Edit scores the miss and the hand-off fills one in.
    """
    view = object.__new__(crossing_detail.MissDetailView)
    view.dialog = _RecordingDialog()
    view.crossing = None
    view.miss = miss if miss is not None else engine.pending_misses()[-1]
    view.roster = roster
    view.engine = engine
    for attr in _VALUE_ATTRS:
        setattr(view, attr, _RecordingValueBox())
    view.edit_btn = _RecordingButton()
    view.edit_time_btn = _RecordingButton()
    view.void_card_btn = _RecordingButton()
    view.delete_btn = _RecordingButton()
    view.ok_btn = _RecordingButton()
    view.crossing_detail_infobar = _RecordingInfoBar()
    return view


def test_render_given_a_pending_miss_fills_the_placeholder_cells() -> None:
    """The miss renders `-`/`missed`/blank card, held unscored."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, roster=roster)

    view.render()

    assert (
        view.crossing_rider_lbl.value,
        view.crossing_team_lbl.value,
        view.crossing_plate_lbl.value,
        view.crossing_lap_lbl.value,
        view.crossing_time_lbl.value,
        view.crossing_lap_time_lbl.value,
        view.crossing_total_lbl.value,
        view.crossing_card_lbl.value,
        view.crossing_held_lbl.value,
    ) == ("-", "missed", "-", "", "10:02:00", "", "", "", "Not yet scored")


def test_render_given_a_pending_miss_disables_delete_and_enables_edit() -> None:
    """A miss is not a crossing: it cannot be undone, only scored."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, roster=roster)

    view.render()

    assert (view.delete_btn.enabled, view.edit_btn.enabled) == (False, True)


# ------------------------------------------------------- miss Edit/OK
#
# Edit is the miss mode's one action: it prompts blank, then assigns
# the saved number through ``assign_plate_to_miss`` and hands the
# dialog over to the crossing that assignment recorded -- the same
# window, re-rendered in crossing mode. OK is a plain close in both
# modes -- the window's one control that ends it -- so nothing in miss
# mode refuses on it.


def test_on_edit_given_a_miss_opens_the_plate_prompt_on_an_empty_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A miss has no plate, so the prompt starts blank."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, roster=roster)
    calls = _stub_plate_dialog(monkeypatch, None)

    view._on_edit(_RecordingEvent())

    assert calls == [{"opener": view.dialog, "plate": ""}]
    assert (view.dialog.modal_ids, len(engine.pending_misses())) == ([], 1)


def test_on_edit_given_a_saved_number_scores_the_miss_and_stays_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The saved plate records the crossing and the dialog keeps it."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, roster=roster)
    _stub_plate_dialog(monkeypatch, "34")
    shoe_before = engine.shoe_remaining
    event = _RecordingEvent()

    view._on_edit(event)

    assert event.skipped is True
    assert view.dialog.modal_ids == []
    assert engine.pending_misses() == ()
    assert [(c.entry_id, c.rider_plate, c.crossed_at) for c in engine.crossings] == [
        (entry_key(roster, "34"), "34", _dt(10, 2))
    ]
    assert (engine.shoe_remaining, len(engine.credited_cards(entry_key(roster, "34")))) == (
        shoe_before - 1,
        1,
    )
    assert engine.events[-1].action == "assign_plate_to_miss"
    assert engine.events[-1].payload["reason"] == crossing_detail.MISS_EDIT_REASON
    assert engine.events[-1].payload["new_plate"] == "34"
    assert view.crossing is engine.crossings[-1]
    assert (
        view.crossing_rider_lbl.value,
        view.crossing_team_lbl.value,
        view.crossing_plate_lbl.value,
        view.crossing_lap_lbl.value,
        view.crossing_time_lbl.value,
        view.crossing_lap_time_lbl.value,
        view.crossing_total_lbl.value,
    ) == ("Bob", "solo", "34", "1", "10:02:00", "2:00", "0:02:00")
    assert view.crossing_card_lbl.value == format_card(engine.card_for(view.crossing).code())
    assert view.crossing_held_lbl.value == "Credited"


def test_on_edit_given_a_pooled_rider_number_scores_it_of_the_rider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pooled member's own plate attributes the team (J1)."""
    roster = _pooled_team_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, roster=roster)
    _stub_plate_dialog(monkeypatch, "45")

    view._on_edit(_RecordingEvent())

    assert [(c.entry_id, c.rider_plate) for c in engine.crossings] == [
        (entry_key(roster, "9"), "45")
    ]
    assert (view.dialog.modal_ids, engine.events[-1].payload["entry_id"]) == (
        [],
        entry_key(roster, "9"),
    )
    assert (view.crossing_rider_lbl.value, view.crossing_team_lbl.value) == (
        "Sarah",
        "Dirt Dynamos",
    )
    assert (view.edit_btn.enabled, view.edit_time_btn.enabled) == (True, True)
    assert (view.void_card_btn.enabled, view.delete_btn.enabled) == (True, True)


def test_on_edit_given_a_number_already_crossing_then_points_at_the_new_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The locator regression: the assignment appends, so last is it.

    A crossing already recorded at the miss's instant under the plate
    the operator types is the documented duplicate case
    (``RideEngine.duplicate_crossings``). ``assign_plate_to_miss``
    appends, so the crossing it just recorded is ``crossings[-1]`` --
    never the earlier twin the plate/instant lookup would find first.
    """
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("34", at=_dt(10, 2))
    twin = engine.crossings[0]
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, roster=roster)
    _stub_plate_dialog(monkeypatch, "34")

    view._on_edit(_RecordingEvent())

    assert engine.duplicate_crossings() == ((twin, engine.crossings[-1]),)
    assert view.crossing is engine.crossings[-1]
    assert (view.crossing_lap_lbl.value, view.crossing_held_lbl.value) == ("2", "Duplicate")


def _handed_over_view(
    monkeypatch: pytest.MonkeyPatch, *, plate: str = "34"
) -> tuple[RideEngine, crossing_detail.MissDetailView]:
    """Arrange a miss view whose Edit has just scored the miss.

    The dialog's post-hand-off state, reached through the real Edit seam
    so the crossing handlers are exercised on the very object the app is
    left holding (a miss is recorded first, at 10:02, and the assigned
    crossing takes that instant).
    """
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, roster=roster)
    _stub_plate_dialog(monkeypatch, plate)
    view._on_edit(_RecordingEvent())
    return engine, view


def test_on_edit_time_given_a_scored_miss_retimes_the_crossing_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-hand-off the view is crossing mode: a retime re-renders it.

    ``_handed_over_view`` is the arrange step and the retime is the act:
    the re-render must be the crossing's own -- a miss re-render would
    show the placeholder cells (and fail on the dropped ``miss``)
    instead of the retimed crossing the operator is now looking at.
    """
    engine, view = _handed_over_view(monkeypatch)
    _stub_run_edit_crossing(
        monkeypatch,
        CrossingEdit(entry_id="34", seq=1, crossed_at=_dt(10, 3), reason="wrong clock"),
    )

    view._on_edit_time(_RecordingEvent())

    assert [c.crossed_at for c in engine.crossings] == [_dt(10, 3)]
    assert view.crossing is engine.crossings[0]
    assert (view.crossing_time_lbl.value, view.crossing_lap_lbl.value) == ("10:03:00", "1")


def test_on_edit_given_a_blank_saved_plate_refuses_and_keeps_the_miss_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3/T-4 negative: blank is refused before the engine sees it."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, roster=roster)
    _stub_plate_dialog(monkeypatch, "")

    view._on_edit(_RecordingEvent())

    assert (view.dialog.modal_ids, len(engine.pending_misses())) == ([], 1)
    assert view.crossing_detail_infobar.messages == ["Enter a plate to assign to this miss."]


def test_on_edit_given_an_unknown_number_refuses_and_keeps_the_miss_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mistyped plate refuses; the miss stays pending."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, roster=roster)
    _stub_plate_dialog(monkeypatch, "999")

    view._on_edit(_RecordingEvent())

    assert (view.dialog.modal_ids, len(engine.pending_misses())) == ([], 1)
    assert view.crossing_detail_infobar.messages == ["Could not assign: unknown plate: 999"]


def test_on_edit_given_a_finished_ride_refuses_and_keeps_the_miss_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assigning is RUNNING/REOPENED only; a finished ride says so."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    engine.finish()
    view = _miss_view(engine, roster=roster)
    _stub_plate_dialog(monkeypatch, "34")

    view._on_edit(_RecordingEvent())

    assert (view.dialog.modal_ids, len(engine.pending_misses())) == ([], 1)
    assert view.crossing_detail_infobar.messages == [
        "Could not assign: cannot assign plate to miss from finished"
    ]


def test_on_ok_given_a_pending_miss_closes_without_assigning() -> None:
    """OK is a plain close in miss mode: only Edit commits."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    view = _miss_view(engine, roster=roster)

    view._on_ok(_RecordingEvent())

    assert view.dialog.modal_ids == [wx.ID_OK]
    assert (view.crossing_detail_infobar.messages, len(engine.pending_misses())) == ([], 1)


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
        self.refreshes = 0

    def rendered_feed_rows(self) -> list[FeedRow]:
        """Return the rows the double's console would be showing.

        The real presenter returns the search-filtered list it last
        rendered; this double has no search box, so it answers the
        source's own feed (the unfiltered render).
        """
        return self.source.feed_rows()

    def refresh_feed(self) -> None:
        """Record the post-correction re-render the app asked for."""
        self.refreshes += 1


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


# run_dialog's keyword
def _run_dialog_stub(_dialog: object, opener: object) -> int:  # noqa: ARG001
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
        def __init__(  # noqa: PLR0913 -- (dialog, miss, roster, engine) mirrors the view
            self, dialog: object, *, miss: object, roster: object, engine: object
        ) -> None:
            """Record the decorated dialog and the miss it shows."""
            opened.append((dialog, miss, roster, engine))

    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(dialogs, "run_dialog", _run_dialog_stub)
    monkeypatch.setattr(crossing_detail, "MissDetailView", _RecordingMissView)

    app_module._open_crossing_detail_for(context, 0)

    assert opened == [(window, engine.pending_misses()[0], roster, engine)]
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


def test_open_crossing_detail_for_given_no_authored_window_posts_a_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: a missing XRC dialog reports on the status bar."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    source = EngineDataSource(engine, roster)
    frame = _StubFrame()
    pin_no_authored_window(monkeypatch, _no_dialog_resource)
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


# ------------------------------------------- the Plate prompt's loader
#
# §9: ``run_plate_dialog`` is the wx boundary the Edit handler calls.
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


def _no_dialog_resource() -> _StubResource:
    """Return a resource double whose ``LoadDialog`` finds no window."""
    return _StubResource(None)


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


def test_run_plate_dialog_given_save_returns_the_typed_plate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§9: Save's value is what the Edit handler commits."""
    window = _StubNumberWindow()
    _stub_loader(monkeypatch, window, "34")

    result = crossing_detail.run_plate_dialog(_StubResource(window), opener=object(), plate="12")

    assert result == "34"


def test_run_plate_dialog_given_save_trims_the_typed_plate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typed plate is trimmed before it reaches the engine."""
    window = _StubNumberWindow()
    _stub_loader(monkeypatch, window, "  34  ")

    result = crossing_detail.run_plate_dialog(_StubResource(window), opener=object(), plate="12")

    assert result == "34"


def test_run_plate_dialog_given_a_plate_prefills_and_selects_the_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§9: the prompt opens on the crossing's own plate."""
    window = _StubNumberWindow()
    _stub_loader(monkeypatch, window, None)

    crossing_detail.run_plate_dialog(_StubResource(window), opener=object(), plate="12")

    assert window.shown_value == "12"
    assert (window.number_input.focused, window.number_input.selected) == (True, True)


def test_run_plate_dialog_given_a_cancel_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: Cancel (and Escape) returns no plate."""
    window = _StubNumberWindow()
    _stub_loader(monkeypatch, window, None)

    result = crossing_detail.run_plate_dialog(_StubResource(window), opener=object(), plate="12")

    assert result is None


def test_run_plate_dialog_given_an_unauthored_window_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: LoadDialog returns None when unauthored."""
    monkeypatch.setattr(crossing_detail, "find_control", _refuse_lookup)
    pin_no_authored_window(monkeypatch, _no_dialog_resource)

    result = crossing_detail.run_plate_dialog(_StubResource(None), opener=object(), plate="12")

    assert result is None


def test_run_plate_dialog_given_save_destroys_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loaded window is destroyed however the prompt ends."""
    window = _StubNumberWindow()
    _stub_loader(monkeypatch, window, "34")

    crossing_detail.run_plate_dialog(_StubResource(window), opener=object(), plate="12")

    assert window.destroyed is True


def test_run_plate_dialog_given_a_cancel_destroys_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3: the cancel path destroys the window too."""
    window = _StubNumberWindow()
    _stub_loader(monkeypatch, window, None)

    crossing_detail.run_plate_dialog(_StubResource(window), opener=object(), plate="12")

    assert window.destroyed is True


def test_run_plate_dialog_given_a_mid_delete_window_skips_destroy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3: an already-deleting window is never destroyed twice."""
    window = _StubNumberWindow(being_deleted=True)
    _stub_loader(monkeypatch, window, "34")

    crossing_detail.run_plate_dialog(_StubResource(window), opener=object(), plate="12")

    assert window.destroyed is False


def test_run_plate_dialog_loads_the_crossing_number_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§9: the loader asks the shared resource for the frozen window."""
    window = _StubNumberWindow()
    resource = _RecordingResource(window)
    _stub_loader(monkeypatch, window, "34")

    result = crossing_detail.run_plate_dialog(resource, opener=object(), plate="12")

    assert resource.loaded == [(None, ids.CROSSING_NUMBER_DLG)]
    assert result == "34"


# -------------------------------------- the feed hotkeys' app halves
#
# Delete/Ctrl+D and Ctrl+E fire the same ``(row)`` seam F2 does
# (``MainFrame.set_on_delete_crossing`` /
# ``set_on_edit_plate_crossing``); these are the app's halves. Both
# resolve the row index against the rendered feed exactly like
# ``_open_crossing_detail_for`` and then run the shared module-level
# helpers above, so the confirm/reassign text can never drift from the
# dialog's own buttons.


def _stub_confirm_delete(
    monkeypatch: pytest.MonkeyPatch, *, result: bool
) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    """Stub ``confirm_delete_crossing``; record each call."""
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _confirm(*args: object, **kwargs: object) -> bool:
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(crossing_detail, "confirm_delete_crossing", _confirm)
    return calls


def _stub_plate_prompt(
    monkeypatch: pytest.MonkeyPatch, result: str | None
) -> list[dict[str, object]]:
    """Stub ``run_plate_dialog``; record each call's kwargs."""
    calls: list[dict[str, object]] = []

    def _run(_resource: object, **kwargs: object) -> str | None:
        calls.append(kwargs)
        return result

    monkeypatch.setattr(crossing_detail, "run_plate_dialog", _run)
    return calls


def _stub_reassign_plate(
    monkeypatch: pytest.MonkeyPatch, refusal: str | None
) -> list[dict[str, object]]:
    """Stub ``reassign_crossing_plate``; record each call."""
    calls: list[dict[str, object]] = []

    def _reassign(engine: object, crossing: object, new_plate: object) -> str | None:
        calls.append({"engine": engine, "crossing": crossing, "new_plate": new_plate})
        return refusal

    monkeypatch.setattr(crossing_detail, "reassign_crossing_plate", _reassign)
    return calls


def _hotkey_context(engine: RideEngine, roster: Roster) -> app_module._RouteContext:
    """Build a route context over a live engine and its console feed."""
    source = EngineDataSource(engine, roster)
    context, _window = _open_context(engine, source, roster)
    return context


# ---------------------------------------------------------- Delete


def test_delete_crossing_for_given_no_presenter_confirms_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: a console-less context has no ride to correct."""
    context = app_module._RouteContext(
        frame=_StubFrame(),
        resource=_RefusingResource(),
        roster=_solo_roster(),
        app=None,
        theme_controller=None,
    )
    calls = _stub_confirm_delete(monkeypatch, result=True)

    app_module._delete_crossing_for(context, 0)

    assert calls == []


def test_delete_crossing_for_given_a_miss_row_confirms_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: a miss row names no crossing to delete."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    context = _hotkey_context(engine, roster)
    calls = _stub_confirm_delete(monkeypatch, result=True)

    app_module._delete_crossing_for(context, 0)

    assert calls == []


def test_delete_crossing_for_given_a_stale_row_confirms_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-4 max + 1: a row outside the feed resolves to nothing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    context = _hotkey_context(engine, roster)
    calls = _stub_confirm_delete(monkeypatch, result=True)

    app_module._delete_crossing_for(context, 5)

    assert calls == []


def test_delete_crossing_for_given_a_crossing_row_runs_the_shared_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console's Delete key reuses Crossing Detail's own confirm."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    context = _hotkey_context(engine, roster)
    presenter = context.presenter
    calls = _stub_confirm_delete(monkeypatch, result=True)

    app_module._delete_crossing_for(context, 0)

    assert calls == [
        (
            (context.frame, engine.crossings[0], roster, engine),
            {"on_refusal": context.frame.SetStatusText},
        )
    ]
    assert presenter.refreshes == 1


def test_delete_crossing_for_given_a_declined_confirm_leaves_the_feed_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: a cancelled confirm refreshes nothing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    context = _hotkey_context(engine, roster)
    presenter = context.presenter
    _stub_confirm_delete(monkeypatch, result=False)

    app_module._delete_crossing_for(context, 0)

    assert presenter.refreshes == 0


# ---------------------------------------------------------- Ctrl+E


def test_edit_plate_crossing_for_given_no_presenter_prompts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: a console-less context has no ride to correct."""
    context = app_module._RouteContext(
        frame=_StubFrame(),
        resource=_RefusingResource(),
        roster=_solo_roster(),
        app=None,
        theme_controller=None,
    )
    calls = _stub_plate_prompt(monkeypatch, "34")

    app_module._edit_plate_crossing_for(context, 0)

    assert calls == []


def test_edit_plate_crossing_for_given_a_miss_row_prompts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: a miss row names no crossing to retype."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_miss(_dt(10, 2), reason="missed number")
    context = _hotkey_context(engine, roster)
    calls = _stub_plate_prompt(monkeypatch, "34")

    app_module._edit_plate_crossing_for(context, 0)

    assert calls == []


def test_edit_plate_crossing_for_given_a_stale_row_prompts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-4 max + 1: a row outside the feed resolves to nothing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    context = _hotkey_context(engine, roster)
    calls = _stub_plate_prompt(monkeypatch, "34")

    app_module._edit_plate_crossing_for(context, 5)

    assert calls == []


def test_edit_plate_crossing_for_given_a_crossing_row_prefills_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl+E prompts on the crossing's plate, then reassigns it."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("34", at=_dt(10, 2))
    context = _hotkey_context(engine, roster)
    presenter = context.presenter
    replayed = _stub_reassign_plate(monkeypatch, None)
    calls = _stub_plate_prompt(monkeypatch, "12")

    app_module._edit_plate_crossing_for(context, 0)

    assert calls == [{"opener": context.frame, "plate": "34"}]
    assert replayed == [{"engine": engine, "crossing": engine.crossings[0], "new_plate": "12"}]
    assert presenter.refreshes == 1
    assert context.frame.notices == []


def test_edit_plate_crossing_for_given_a_crossing_without_a_plate_uses_the_entry_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-4 nullable: a crossing with no plate falls back to its id."""
    roster = _two_solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("34", at=_dt(10, 2))
    context = _hotkey_context(engine, roster)
    monkeypatch.setattr(
        app_module,
        "_feed_row_target",
        lambda *_args: replace(engine.crossings[0], rider_plate=None),
    )
    _stub_reassign_plate(monkeypatch, None)
    calls = _stub_plate_prompt(monkeypatch, "12")

    app_module._edit_plate_crossing_for(context, 0)

    assert calls == [{"opener": context.frame, "plate": "34"}]


def test_edit_plate_crossing_for_given_a_cancelled_prompt_reassigns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: Cancel commits nothing and refreshes nothing."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    context = _hotkey_context(engine, roster)
    presenter = context.presenter
    replayed = _stub_reassign_plate(monkeypatch, None)
    _stub_plate_prompt(monkeypatch, None)

    app_module._edit_plate_crossing_for(context, 0)

    assert (replayed, presenter.refreshes) == ([], 0)


def test_edit_plate_crossing_for_given_a_refused_reassign_posts_it_on_the_status_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-5 negative: the helper's refusal reaches the status bar."""
    roster = _solo_roster()
    engine = _running_engine(roster)
    engine.record_crossing("12", at=_dt(10, 2))
    context = _hotkey_context(engine, roster)
    presenter = context.presenter
    _stub_reassign_plate(monkeypatch, "Could not reassign: unknown plate: 999")
    _stub_plate_prompt(monkeypatch, "999")

    app_module._edit_plate_crossing_for(context, 0)

    assert context.frame.notices == ["Could not reassign: unknown plate: 999"]
    assert presenter.refreshes == 0


# ------------------------------------------------------- the wiring


class _WiringConsole:
    """A console-view double recording the app's three feed seams."""

    def __init__(self) -> None:
        """Start with no registered callbacks."""
        self.open_crossing: Callable[[int], None] | None = None
        self.delete_crossing: Callable[[int], None] | None = None
        self.edit_plate_crossing: Callable[[int], None] | None = None

    def set_on_open_crossing(self, callback: Callable[[int], None]) -> None:
        """Record the open-crossing callback."""
        self.open_crossing = callback

    def set_on_delete_crossing(self, callback: Callable[[int], None]) -> None:
        """Record the delete-crossing callback."""
        self.delete_crossing = callback

    def set_on_edit_plate_crossing(self, callback: Callable[[int], None]) -> None:
        """Record the edit-plate callback."""
        self.edit_plate_crossing = callback


def test_wire_crossing_open_seam_wires_all_three_feed_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wire-up hands the console one callback per feed hotkey."""
    opened: list[tuple[object, int]] = []
    deleted: list[tuple[object, int]] = []
    edited: list[tuple[object, int]] = []
    monkeypatch.setattr(
        app_module, "_open_crossing_detail_for", lambda context, row: opened.append((context, row))
    )
    monkeypatch.setattr(
        app_module, "_delete_crossing_for", lambda context, row: deleted.append((context, row))
    )
    monkeypatch.setattr(
        app_module,
        "_edit_plate_crossing_for",
        lambda context, row: edited.append((context, row)),
    )
    console = _WiringConsole()
    context = app_module._RouteContext(
        frame=_StubFrame(),
        resource=_RefusingResource(),
        roster=_solo_roster(),
        app=None,
        theme_controller=None,
        console_view=console,
    )

    app_module._wire_crossing_open_seam(context)
    console.open_crossing(1)
    console.delete_crossing(2)
    console.edit_plate_crossing(3)

    assert (opened, deleted, edited) == ([(context, 1)], [(context, 2)], [(context, 3)])


def test_wire_crossing_open_seam_given_no_console_view_wires_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: a console-less context has nothing to wire."""
    called: list[str] = []
    monkeypatch.setattr(
        app_module, "_open_crossing_detail_for", lambda *_args: called.append("open")
    )
    monkeypatch.setattr(app_module, "_delete_crossing_for", lambda *_args: called.append("delete"))
    monkeypatch.setattr(
        app_module, "_edit_plate_crossing_for", lambda *_args: called.append("edit")
    )
    context = app_module._RouteContext(
        frame=_StubFrame(),
        resource=_RefusingResource(),
        roster=_solo_roster(),
        app=None,
        theme_controller=None,
    )

    app_module._wire_crossing_open_seam(context)

    assert called == []
