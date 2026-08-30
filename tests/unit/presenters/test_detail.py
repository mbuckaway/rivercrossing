# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the E7.2.1 DetailPresenter (entry detail actions).

The entry-detail dialog's six action buttons (edit crossing / deal
card / void card / move rider / mark DNF / audit) are wired through
``DetailPresenter``: a wx-free coordinator holding ``(view,
data_source, plate, engine, roster)``. Each ``on_*_clicked`` handler
computes the parameters (plate, current time, selected lap, entry
label), asks the ``DetailView`` to open the matching correction dialog
(which returns the confirmed submission, or None on cancel), then
calls the matching ``RideEngine`` / ``Roster`` command and re-renders
the entry detail through ``show_entry``.

These tests drive the presenter against a recording ``FakeDetailView``
and a real ``RideEngine``/``Roster`` (never wx -- R-71): per-command
happy paths, cancel paths (the view returns None), the no-engine /
no-roster guard notices, the "select a lap first" guard, the
edit-vs-void split, the pooled team-move resolution, and the engine
refusal surfaces as a notice, never a crash (the same
``ConsolePresenter`` discipline).
"""

from datetime import date, datetime, timedelta

from rivercrossing.cards import Shoe
from rivercrossing.ride import RideConfig, RideEngine
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui.presenters.data_source import EntryDetail, EntryLapRow
from rivercrossing.ui.presenters.detail import (
    CardVoid,
    CrossingEdit,
    DetailPresenter,
    DnfMark,
    ManualDeal,
    RiderMove,
)

# -------------------------------------------------------------- helpers

_EVENT_DAY = date(2026, 9, 20)


def _dt(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Build a naive datetime on the fixed event day."""
    return datetime(2026, 9, 20, hour, minute, second)  # noqa: DTZ001 -- naive by design, as RideConfig.planned_start


class _FakeClock:
    """Wall-clock source the presenter's time prefill reads."""

    def __init__(self, start: datetime) -> None:
        """Freeze the fake clock at *start*."""
        self._now = start

    def __call__(self) -> datetime:
        """Return the current fake time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward by *seconds*."""
        self._now = self._now + timedelta(seconds=seconds)


def _config(*, min_lap_s: int = 1) -> RideConfig:
    """Build a valid, always-valid config with a tunable min-lap."""
    return RideConfig(
        name="GORBA EPIC 2026",
        event_date=_EVENT_DAY,
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=_dt(10, 0),
        planned_duration_s=21600,
        min_lap_s=min_lap_s,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )


def _roster_with_entries(*plates: str) -> Roster:
    """Build a MIXED rider_pooled roster of one solo entry per plate."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    for plate in plates:
        roster.create_solo_entry(name=f"Rider {plate}", plate=plate)
    return roster


def _running_engine(*, roster: Roster | None = None) -> tuple[RideEngine, _FakeClock]:
    """Build a started engine over a valid config, shoe and roster."""
    roster = roster if roster is not None else _roster_with_entries("12", "34")
    config = _config()
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    clock = _FakeClock(config.planned_start)
    engine = RideEngine(config=config, shoe=shoe, clock=clock, roster=roster)
    engine.start()
    return engine, clock


def _record(  # noqa: PLR0913 -- (engine, clock, plate, lap_time_s)
    engine: RideEngine, clock: _FakeClock, plate: str, *, lap_time_s: float
) -> None:
    """Record one crossing, clock advanced by *lap_time_s*."""
    clock.advance(lap_time_s)
    result = engine.record_crossing(plate)
    assert result.accepted is True


class _EngineSource:
    """A minimal ``DataSource`` over one engine/roster."""

    def __init__(self, engine: RideEngine, roster: Roster) -> None:
        """Wrap the engine and roster this presenter corrects."""
        self._engine = engine
        self._roster = roster

    def entry_detail(self, plate: str) -> EntryDetail:
        """Return a fixed header/members/laps for *plate*."""
        laps = tuple(
            EntryLapRow(
                lap=crossing.seq,
                time=crossing.crossed_at.strftime("%H:%M:%S"),
                lap_time="0:00",
                rider=plate,
                card=self._engine.card_for(crossing).code(),
            )
            for crossing in self._engine.crossings
            if crossing.entry_id == plate
        )
        return EntryDetail(header=f"Entry {plate}", members="Rider", cards_held=(), laps=laps)


class _EmptySource:
    """DataSource-shaped stub for no-engine presenter constructions."""

    def entry_detail(self, plate: str) -> EntryDetail:  # noqa: ARG002 -- DataSource's signature, unused
        """Return an empty detail view-model for any *plate*."""
        return EntryDetail(header="", members="", cards_held=(), laps=())


# ----------------------------------------------------------------- view


class FakeDetailView:
    """A recording ``DetailView`` spy for headless presenter tests.

    Each dialog channel records the parameters it was called with and
    returns the canned result the test stored; ``show_notice`` records
    the notice text. ``selected`` is the lap the view reports when the
    presenter asks which laps_list row the operator selected.
    """

    def __init__(self) -> None:
        """Start every channel empty."""
        self.shown: list[EntryDetail] = []
        self.move_enabled: bool | None = None
        self.selected: EntryLapRow | None = None
        self.edit_result: CrossingEdit | None = None
        self.manual_result: ManualDeal | None = None
        self.void_result: CardVoid | None = None
        self.dnf_result: DnfMark | None = None
        self.move_result: RiderMove | None = None
        self.notices: list[str] = []
        self.last_edit: tuple[bool, str, str] | None = None
        self.last_manual: str | None = None
        self.last_void: tuple[str, str] | None = None
        self.last_dnf: str | None = None
        self.last_move: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self.audit_count = 0

    def show_entry(self, detail: EntryDetail) -> None:
        """Record the rendered detail."""
        self.shown.append(detail)

    def set_move_rider_enabled(self, *, enabled: bool) -> None:
        """Record the move_rider_btn enablement."""
        self.move_enabled = enabled

    def selected_lap(self) -> EntryLapRow | None:
        """Return the canned selected lap."""
        return self.selected

    def show_edit_crossing(self, *, adding: bool, plate: str, time: str) -> CrossingEdit | None:
        """Record the edit-dialog parameters; return the result."""
        self.last_edit = (adding, plate, time)
        return self.edit_result

    def open_manual_deal(self, *, plate: str) -> ManualDeal | None:
        """Record the manual-deal prefill; return the canned result."""
        self.last_manual = plate
        return self.manual_result

    def open_void_card(self, *, card: str, entry: str) -> CardVoid | None:
        """Record the void-card label inputs; return the result."""
        self.last_void = (card, entry)
        return self.void_result

    def open_dnf(self, *, entry: str) -> DnfMark | None:
        """Record the DNF label input; return the canned result."""
        self.last_dnf = entry
        return self.dnf_result

    def open_move_rider(
        self, *, riders: tuple[str, ...], teams: tuple[str, ...]
    ) -> RiderMove | None:
        """Record the picker inputs; return the canned result."""
        self.last_move = (riders, teams)
        return self.move_result

    def open_audit(self) -> None:
        """Record one audit open."""
        self.audit_count += 1

    def show_notice(self, text: str) -> None:
        """Record the notice text."""
        self.notices.append(text)


def _make_presenter(  # noqa: PLR0913 -- (engine, view, roster, plate)
    engine: RideEngine,
    view: FakeDetailView,
    roster: Roster,
    *,
    plate: str = "12",
) -> DetailPresenter:
    """Build the presenter over a real engine source and a fake view."""
    source = _EngineSource(engine, roster)
    clock = _FakeClock(_dt(10, 45))
    return DetailPresenter(view, source, plate=plate, engine=engine, roster=roster, clock=clock)


# ------------------------------------------------------- constructor


def test_detail_presenter_holds_view_data_source_plate_and_engine() -> None:
    """E7.2.1: the presenter keeps its five collaborators."""
    engine, _clock = _running_engine()
    roster = engine._roster
    view = FakeDetailView()
    source = _EngineSource(engine, roster)

    presenter = DetailPresenter(view, source, plate="12", engine=engine, roster=roster)

    assert presenter.view is view
    assert presenter.data_source is source
    assert presenter.plate == "12"
    assert presenter.engine is engine
    assert presenter.roster is roster


# ------------------------------------------------------------ refresh


def test_refresh_renders_the_entry_and_enables_move_for_a_pooled_team() -> None:
    """refresh() re-renders and enables move for a pooled team."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[Rider(name="A. Roy", plate="77"), Rider(name="K. Singh", plate="78")],
    )
    engine, _clock = _running_engine(roster=roster)
    view = FakeDetailView()
    presenter = _make_presenter(engine, view, roster, plate="77")

    presenter.refresh()

    assert view.shown[-1].header == "Entry 77"
    assert view.move_enabled is True


def test_refresh_disables_move_button_for_a_solo_entry() -> None:
    """A solo entry has no rider to move: the button stays off."""
    engine, _clock = _running_engine()
    view = FakeDetailView()
    presenter = _make_presenter(engine, view, engine._roster, plate="12")

    presenter.refresh()

    assert view.move_enabled is False


# ---------------------------------------------------- edit crossing


def test_on_edit_crossing_without_a_live_engine_notices() -> None:
    """No live ride: the button explains itself, opens nothing."""
    view = FakeDetailView()
    presenter = DetailPresenter(view, _EmptySource(), plate="12")

    presenter.on_edit_crossing_clicked()

    assert view.notices == ["No live ride to correct"]
    assert view.last_edit is None


def test_on_edit_crossing_without_a_selected_lap_notices() -> None:
    """Edit needs a concrete crossing: select a lap first."""
    engine, _clock = _running_engine()
    view = FakeDetailView()
    view.selected = None
    presenter = _make_presenter(engine, view, engine._roster)

    presenter.on_edit_crossing_clicked()

    assert view.notices == ["Select a lap to edit"]
    assert view.last_edit is None


def test_on_edit_crossing_opens_edit_mode_prefilled_and_applies_the_edit() -> None:
    """Edit mode prefills; confirm re-times the selected lap."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=600)
    view = FakeDetailView()
    view.selected = EntryLapRow(
        lap=1, time="10:10:00", lap_time="10:00", rider="Rider 12", card="9H"
    )
    view.edit_result = CrossingEdit(
        entry_id="12", seq=1, crossed_at=_dt(10, 11), reason="mis-keyed time"
    )
    presenter = _make_presenter(engine, view, engine._roster)

    presenter.on_edit_crossing_clicked()

    assert view.last_edit == (False, "12", "10:45:00")
    assert engine.crossings[0].crossed_at == _dt(10, 11)
    assert engine.events[-1].action == "edit_crossing"
    assert view.notices == ["Crossing edited"]
    assert view.shown[-1].header == "Entry 12"


def test_on_edit_crossing_cancel_leaves_the_engine_untouched() -> None:
    """A cancelled dialog changes nothing."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=600)
    view = FakeDetailView()
    view.selected = EntryLapRow(
        lap=1, time="10:10:00", lap_time="10:00", rider="Rider 12", card="9H"
    )
    view.edit_result = None
    presenter = _make_presenter(engine, view, engine._roster)
    before = len(engine.events)

    presenter.on_edit_crossing_clicked()

    assert len(engine.events) == before
    assert view.notices == []


def test_on_edit_crossing_void_choice_voids_the_crossing() -> None:
    """edit_crossing_dlg's void_btn routes to void_crossing."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=600)
    view = FakeDetailView()
    view.selected = EntryLapRow(
        lap=1, time="10:10:00", lap_time="10:00", rider="Rider 12", card="9H"
    )
    view.edit_result = CrossingEdit(
        entry_id="12", seq=1, crossed_at=None, reason="double entry", void=True
    )
    presenter = _make_presenter(engine, view, engine._roster)

    presenter.on_edit_crossing_clicked()

    assert engine.crossings == ()
    assert engine.events[-1].action == "void_crossing"
    assert view.notices == ["Crossing voided"]


def test_on_edit_crossing_void_engine_refusal_surfaces_as_a_notice() -> None:
    """A FINISHED ride refuses the void choice; notice, no crash."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=600)
    engine.finish()
    view = FakeDetailView()
    view.selected = EntryLapRow(
        lap=1, time="10:10:00", lap_time="10:00", rider="Rider 12", card="9H"
    )
    view.edit_result = CrossingEdit(
        entry_id="12", seq=1, crossed_at=None, reason="double entry", void=True
    )
    presenter = _make_presenter(engine, view, engine._roster)

    presenter.on_edit_crossing_clicked()

    assert view.notices == ["Cannot void crossing: cannot void crossing from finished"]


def test_on_edit_crossing_engine_refusal_surfaces_as_a_notice() -> None:
    """A FINISHED ride refuses the edit; the presenter notices."""
    engine, _clock = _running_engine()
    engine.finish()
    view = FakeDetailView()
    view.selected = EntryLapRow(
        lap=1, time="10:10:00", lap_time="10:00", rider="Rider 12", card="9H"
    )
    view.edit_result = CrossingEdit(
        entry_id="12", seq=1, crossed_at=_dt(10, 11), reason="mis-keyed time"
    )
    presenter = _make_presenter(engine, view, engine._roster)

    presenter.on_edit_crossing_clicked()

    assert view.notices == ["Cannot edit crossing: cannot edit crossing from finished"]


# -------------------------------------------------------- deal card


def test_on_deal_card_opens_the_manual_deal_dialog_and_deals() -> None:
    """Deal card: prefill the plate; confirm credits one shoe card."""
    engine, _clock = _running_engine()
    view = FakeDetailView()
    view.manual_result = ManualDeal(plate="12", reason="flag confirmed")
    presenter = _make_presenter(engine, view, engine._roster)

    presenter.on_deal_card_clicked()

    assert view.last_manual == "12"
    assert engine.events[-1].action == "deal_manual"
    assert engine.events[-1].payload["reason"] == "flag confirmed"
    assert view.notices == ["Card dealt"]


def test_on_deal_card_without_an_engine_notices() -> None:
    """No live ride: the button explains itself, opens nothing."""
    view = FakeDetailView()
    presenter = DetailPresenter(view, _EmptySource(), plate="12")

    presenter.on_deal_card_clicked()

    assert view.notices == ["No live ride to correct"]
    assert view.last_manual is None


def test_on_deal_card_cancel_is_a_silent_noop() -> None:
    """A cancelled manual-deal dialog changes nothing."""
    engine, _clock = _running_engine()
    view = FakeDetailView()
    view.manual_result = None
    presenter = _make_presenter(engine, view, engine._roster)
    before = len(engine.events)

    presenter.on_deal_card_clicked()

    assert len(engine.events) == before
    assert view.notices == []


# ------------------------------------------------------- void card


def test_on_void_card_opens_confirm_naming_the_card_and_entry_then_voids() -> None:
    """Void card: confirm names card + entry; OK voids it."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=600)
    dealt = engine.card_for(engine.crossings[0])
    view = FakeDetailView()
    view.selected = EntryLapRow(
        lap=1, time="10:10:00", lap_time="10:00", rider="Rider 12", card=dealt.code()
    )
    view.void_result = CardVoid(entry_id="12", card=dealt.code(), reason="wrong card dealt")
    presenter = _make_presenter(engine, view, engine._roster)

    presenter.on_void_card_clicked()

    assert view.last_void == (dealt.code(), "12 · Rider 12")
    assert engine.events[-1].action == "void_card"
    assert engine.events[-1].payload["card"] == dealt.code()
    assert view.notices == ["Card voided"]


def test_on_void_card_without_a_selected_lap_notices() -> None:
    """Void needs a concrete dealt card: select a laps row first."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=600)
    view = FakeDetailView()
    view.selected = None
    presenter = _make_presenter(engine, view, engine._roster)

    presenter.on_void_card_clicked()

    assert view.notices == ["Select a lap to void"]
    assert view.last_void is None


def test_on_void_card_without_an_engine_notices() -> None:
    """No live ride: the button explains itself, opens nothing."""
    view = FakeDetailView()
    presenter = DetailPresenter(view, _EmptySource(), plate="12")

    presenter.on_void_card_clicked()

    assert view.notices == ["No live ride to correct"]
    assert view.last_void is None


def test_on_void_card_engine_refusal_surfaces_as_a_notice() -> None:
    """A FINISHED ride refuses the void; notice, no crash."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=600)
    dealt = engine.card_for(engine.crossings[0])
    engine.finish()
    view = FakeDetailView()
    view.selected = EntryLapRow(
        lap=1, time="10:10:00", lap_time="10:00", rider="Rider 12", card=dealt.code()
    )
    view.void_result = CardVoid(entry_id="12", card=dealt.code(), reason="wrong card dealt")
    presenter = _make_presenter(engine, view, engine._roster)

    presenter.on_void_card_clicked()

    assert view.notices == ["Cannot void card: cannot void card from finished"]


def test_on_void_card_cancel_is_a_silent_noop() -> None:
    """A cancelled void-card confirm changes nothing."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=600)
    view = FakeDetailView()
    view.selected = EntryLapRow(
        lap=1, time="10:10:00", lap_time="10:00", rider="Rider 12", card="9H"
    )
    view.void_result = None
    presenter = _make_presenter(engine, view, engine._roster)
    before = len(engine.events)

    presenter.on_void_card_clicked()

    assert len(engine.events) == before
    assert view.notices == []


# ------------------------------------------------------------ DNF


def test_on_dnf_opens_confirm_naming_the_entry_then_marks() -> None:
    """Mark DNF: the confirm names the entry; OK flips the status."""
    engine, _clock = _running_engine()
    view = FakeDetailView()
    view.dnf_result = DnfMark(entry_id="12", reason="mechanical failure")
    presenter = _make_presenter(engine, view, engine._roster)

    presenter.on_dnf_clicked()

    assert view.last_dnf == "12 · Rider 12"
    assert engine.events[-1].action == "dnf"
    assert view.notices == ["Entry marked DNF"]


def test_on_dnf_cancel_is_a_silent_noop() -> None:
    """A cancelled DNF confirm changes nothing."""
    engine, _clock = _running_engine()
    view = FakeDetailView()
    view.dnf_result = None
    presenter = _make_presenter(engine, view, engine._roster)
    before = len(engine.events)

    presenter.on_dnf_clicked()

    assert len(engine.events) == before
    assert view.notices == []


def test_on_dnf_without_an_engine_notices() -> None:
    """No live ride: the button explains itself, opens nothing."""
    view = FakeDetailView()
    presenter = DetailPresenter(view, _EmptySource(), plate="12")

    presenter.on_dnf_clicked()

    assert view.notices == ["No live ride to correct"]
    assert view.last_dnf is None


# ------------------------------------------------------ move rider


def test_on_move_rider_opens_picker_and_calls_the_pooled_move() -> None:
    """Move rider: pick rider + team; the roster performs the move."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[Rider(name="A. Roy", plate="77"), Rider(name="K. Singh", plate="78")],
    )
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(name="S. Okafor", plate="9"), Rider(name="P. Chen", plate="45")],
    )
    engine, _clock = _running_engine(roster=roster)
    view = FakeDetailView()
    view.move_result = RiderMove(rider_plate="78", to_team="Dirt Dynamos")
    presenter = _make_presenter(engine, view, roster, plate="77")

    presenter.on_move_rider_clicked()

    assert view.last_move == (("77", "78"), ("Dirt Dynamos",))
    teams = {
        entry.display_name: {rider.plate for rider in entry.riders} for entry in roster.entries
    }
    assert teams["Dirt Dynamos"] == {"9", "45", "78"}
    assert view.notices == ["Rider moved"]


def test_on_move_rider_without_a_roster_notices() -> None:
    """No live roster: the button explains itself, opens nothing."""
    view = FakeDetailView()
    presenter = DetailPresenter(view, _EmptySource(), plate="77")

    presenter.on_move_rider_clicked()

    assert view.notices == ["No live ride to move riders in"]
    assert view.last_move is None


def test_on_move_rider_locked_move_surfaces_as_a_notice() -> None:
    """A finished ride refuses the move; the presenter notices."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[Rider(name="A. Roy", plate="77"), Rider(name="K. Singh", plate="78")],
    )
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(name="S. Okafor", plate="9"), Rider(name="P. Chen", plate="45")],
    )
    engine, _clock = _running_engine(roster=roster)
    engine.finish()
    view = FakeDetailView()
    view.move_result = RiderMove(rider_plate="78", to_team="Dirt Dynamos")
    presenter = _make_presenter(engine, view, roster, plate="77")

    presenter.on_move_rider_clicked()

    assert len(view.notices) == 1
    assert view.notices[0].startswith("Cannot move rider:")
    assert "locked" in view.notices[0]


# ------------------------------------------------------------ audit


def test_on_audit_opens_the_audit_dialog() -> None:
    """Audit trail opens plain (the pre-filter is E7.3.1)."""
    view = FakeDetailView()
    presenter = DetailPresenter(view, _EmptySource(), plate="12")

    presenter.on_audit_clicked()

    assert view.audit_count == 1


# ------------------------------------------- refusal + fallback paths


class _DissolvingSource:
    """A source whose entry_detail raises for one plate (dissolved)."""

    def __init__(self, raise_for: str) -> None:
        """Store the plate that no longer resolves."""
        self._raise_for = raise_for

    def entry_detail(self, plate: str) -> EntryDetail:
        """Raise for the dissolved plate; return a detail otherwise."""
        if plate == self._raise_for:
            raise LookupError(f"no entry detail for plate {plate!r}")
        return EntryDetail(header=f"Entry {plate}", members="Rider", cards_held=(), laps=())


def test_refresh_after_the_entry_dissolved_shows_the_empty_detail() -> None:
    """A move that dissolves the entry falls back to the empty view."""
    engine, _clock = _running_engine()
    view = FakeDetailView()
    presenter = DetailPresenter(
        view, _DissolvingSource("12"), plate="12", engine=engine, roster=engine._roster
    )

    presenter.refresh()

    assert view.shown[-1] == EntryDetail(header="", members="", cards_held=(), laps=())
    assert view.move_enabled is False


def test_on_corrected_hook_fires_after_a_successful_correction() -> None:
    """The app-level hook (menu binder) runs once per applied edit."""
    engine, clock = _running_engine()
    _record(engine, clock, "12", lap_time_s=600)
    view = FakeDetailView()
    view.selected = EntryLapRow(
        lap=1, time="10:10:00", lap_time="10:00", rider="Rider 12", card="9H"
    )
    view.edit_result = CrossingEdit(
        entry_id="12", seq=1, crossed_at=_dt(10, 11), reason="mis-keyed time"
    )
    corrected: list[bool] = []
    source = _EngineSource(engine, engine._roster)
    clock = _FakeClock(_dt(10, 45))
    presenter = DetailPresenter(
        view,
        source,
        plate="12",
        engine=engine,
        roster=engine._roster,
        clock=clock,
        on_corrected=lambda: corrected.append(True),
    )

    presenter.on_edit_crossing_clicked()

    assert corrected == [True]


def test_on_deal_card_from_reopened_ride_deals_and_notices() -> None:
    """REOPENED re-opens the shoe: deal card deals and notices (§15).

    The old E4.3 pin (a REOPENED ride's closed shoe refusing the deal)
    is superseded: ``reopen()`` re-opens the shoe, so the manual deal
    credits in REOPENED exactly as it does while RUNNING.
    """
    engine, _clock = _running_engine()
    engine.finish()
    engine.reopen()
    view = FakeDetailView()
    view.manual_result = ManualDeal(plate="12", reason="flag confirmed")
    presenter = _make_presenter(engine, view, engine._roster)

    presenter.on_deal_card_clicked()

    assert view.notices == ["Card dealt"]
    assert engine.events[-1].action == "deal_manual"
    assert engine._shoe.is_closed is False


def test_on_dnf_engine_refusal_surfaces_as_a_notice() -> None:
    """A FINISHED ride refuses the DNF mark; notice, no crash."""
    engine, _clock = _running_engine()
    engine.finish()
    view = FakeDetailView()
    view.dnf_result = DnfMark(entry_id="12", reason="mechanical failure")
    presenter = _make_presenter(engine, view, engine._roster)

    presenter.on_dnf_clicked()

    assert view.notices == ["Cannot mark DNF: cannot mark DNF from finished"]


def test_on_move_rider_for_a_solo_entry_notices() -> None:
    """A solo entry has no rider to move; the button path says so."""
    engine, _clock = _running_engine()
    view = FakeDetailView()
    presenter = _make_presenter(engine, view, engine._roster, plate="12")

    presenter.on_move_rider_clicked()

    assert view.notices == ["Move rider is for pooled team entries"]
    assert view.last_move is None


def test_on_move_rider_unresolved_rider_or_team_notices() -> None:
    """A picker result naming no rider/team is surfaced, not crash."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[Rider(name="A. Roy", plate="77"), Rider(name="K. Singh", plate="78")],
    )
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(name="S. Okafor", plate="9"), Rider(name="P. Chen", plate="45")],
    )
    engine, _clock = _running_engine(roster=roster)
    view = FakeDetailView()
    view.move_result = RiderMove(rider_plate="999", to_team="No Such Team")
    presenter = _make_presenter(engine, view, roster, plate="77")

    presenter.on_move_rider_clicked()

    assert view.notices == ["No rider with plate 999"]


def test_on_move_rider_unresolved_destination_team_notices() -> None:
    """A picker naming no destination team is surfaced, not crash."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[Rider(name="A. Roy", plate="77"), Rider(name="K. Singh", plate="78")],
    )
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(name="S. Okafor", plate="9"), Rider(name="P. Chen", plate="45")],
    )
    engine, _clock = _running_engine(roster=roster)
    view = FakeDetailView()
    view.move_result = RiderMove(rider_plate="78", to_team="No Such Team")
    presenter = _make_presenter(engine, view, roster, plate="77")

    presenter.on_move_rider_clicked()

    assert view.notices == ["No team named No Such Team"]


def test_on_move_rider_cancel_is_a_silent_noop() -> None:
    """A cancelled move picker changes nothing."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[Rider(name="A. Roy", plate="77"), Rider(name="K. Singh", plate="78")],
    )
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(name="S. Okafor", plate="9"), Rider(name="P. Chen", plate="45")],
    )
    engine, _clock = _running_engine(roster=roster)
    view = FakeDetailView()
    view.move_result = None
    presenter = _make_presenter(engine, view, roster, plate="77")
    mover_before = roster.resolve_plate("78")

    presenter.on_move_rider_clicked()

    assert view.notices == []
    assert roster.resolve_plate("78") is mover_before


def test_move_rider_enabled_false_without_a_roster() -> None:
    """No live roster: the pooled-only rule cannot enable the button."""
    view = FakeDetailView()
    presenter = DetailPresenter(view, _EmptySource(), plate="77")

    assert presenter.move_rider_enabled() is False


def test_move_rider_enabled_false_for_an_unknown_plate() -> None:
    """A plate no entry owns never enables the move button."""
    engine, _clock = _running_engine()
    view = FakeDetailView()
    presenter = _make_presenter(engine, view, engine._roster, plate="999")

    assert presenter.move_rider_enabled() is False


# --------------------------------------------------- T-7 invariants


def test_current_time_prefill_formats_hms_with_leading_zeros() -> None:
    """The time prefill is zero-padded HH:MM:SS from the clock seam."""
    engine, _clock = _running_engine()
    view = FakeDetailView()
    source = _EngineSource(engine, engine._roster)
    clock = _FakeClock(_dt(9, 5, 3))
    presenter = DetailPresenter(
        view, source, plate="12", engine=engine, roster=engine._roster, clock=clock
    )

    formatted = presenter._current_time()

    assert formatted == "09:05:03"
