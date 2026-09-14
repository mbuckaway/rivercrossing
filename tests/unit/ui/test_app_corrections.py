# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for app.py's ``_make_route_handler`` dispatch.

The four Cards/Riders correction rows dispatch through
``_make_route_handler`` by their own item id -- not by target, since
``mi_add_crossing_at`` and ``mi_edit_crossing`` share
``EDIT_CROSSING_DLG`` with different modes. The ux-polish wiring of
the two last-dead Ride menu rows (Stop Ride…, Set Start Time…)
added a second, target-keyed dispatch family for the same function
(``_LIVE_FLOW_HANDLERS``), pinned here too. W5 moved mi_stop_ride
out of that table into its own COMMAND branch (``target="stop_ride"``
-> the live presenter's native stop-confirm flow), so this file pins
the two remaining shapes: Set Start Time… still dispatches through
``_LIVE_FLOW_HANDLERS``, and Stop Ride… fires
``presenter.on_stop_requested``. This file is the only headless
``_make_route_handler`` suite; the handlers themselves need real wx
dialogs and real engine commands, so they are not exercised here.
"""

from datetime import datetime

import pytest
import wx

from conftest import gorba_config
from rivercrossing.cards import Shoe
from rivercrossing.ride import Crossing, RideEngine
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import commands, ids, std_dialogs
from rivercrossing.ui.presenters.data_source import EngineDataSource, format_duration
from rivercrossing.ui.views import corrections, dialogs
from rivercrossing.ui.views import crossing_detail as crossing_detail_module
from rivercrossing.ui.views.corrections import DnfMark

# Phase 2 retired the Reassign Plate… and Void Card… menu rows, so the
# correction dispatch family is four rows now.
_CORRECTION_ROUTES = (
    ids.MI_ADD_CROSSING_AT,
    ids.MI_EDIT_CROSSING,
    ids.MI_DEAL_MANUAL,
    ids.MI_MARK_DNF,
)


class _StubContext:
    """A minimal context the stub handlers record.

    ``presenter``/``frame`` default to ``None`` exactly like the real
    ``_RouteContext``, so the presenter-less route arms are drivable.
    """

    presenter: object | None = None
    frame: object | None = None


class _RecordingPresenter:
    """A presenter stand-in that records the stop-request call."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[str] = []

    def on_stop_requested(self) -> None:
        """Record the native stop-confirm flow request."""
        self.calls.append("on_stop_requested")


class _NoticeFrame:
    """A frame stand-in that records status-bar notices."""

    def __init__(self, notices: list[str]) -> None:
        """Share the caller's notice log."""
        self.notices = notices

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record one status-bar notice."""
        self.notices.append(text)


@pytest.mark.parametrize("item_id", _CORRECTION_ROUTES, ids=lambda value: value)
def test_make_route_handler_dispatches_each_correction_route_by_its_own_id(
    monkeypatch: pytest.MonkeyPatch, item_id: str
) -> None:
    """Each correction item id binds to its own handler."""
    route = commands.route_for_id(item_id)
    fired: list[object] = []

    def handler(context: object) -> None:
        fired.append(context)

    monkeypatch.setitem(app_module._CORRECTION_HANDLERS, item_id, handler)
    context = _StubContext()
    bound = app_module._make_route_handler(context, route)  # type: ignore[arg-type]

    bound(None)

    assert fired == [context]


def test_shared_edit_crossing_target_dispatch_differs_by_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Add and Edit share a target but bind different handlers."""
    assert commands.route_for_id(ids.MI_ADD_CROSSING_AT).target == ids.EDIT_CROSSING_DLG
    assert commands.route_for_id(ids.MI_EDIT_CROSSING).target == ids.EDIT_CROSSING_DLG
    fired: list[str] = []
    monkeypatch.setitem(
        app_module._CORRECTION_HANDLERS,
        ids.MI_ADD_CROSSING_AT,
        lambda _context: fired.append("add"),
    )
    monkeypatch.setitem(
        app_module._CORRECTION_HANDLERS,
        ids.MI_EDIT_CROSSING,
        lambda _context: fired.append("edit"),
    )
    context = _StubContext()

    app_module._make_route_handler(context, commands.route_for_id(ids.MI_ADD_CROSSING_AT))(None)  # type: ignore[arg-type]
    app_module._make_route_handler(context, commands.route_for_id(ids.MI_EDIT_CROSSING))(None)  # type: ignore[arg-type]

    assert fired == ["add", "edit"]


def test_make_route_handler_binds_the_deal_bonus_card_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 2: the renamed row still dispatches by mi_deal_manual."""
    route = commands.route_for_id(ids.MI_DEAL_MANUAL)
    fired: list[object] = []

    def handler(context: object) -> None:
        fired.append(context)

    monkeypatch.setitem(app_module._CORRECTION_HANDLERS, ids.MI_DEAL_MANUAL, handler)
    context = _StubContext()
    bound = app_module._make_route_handler(context, route)  # type: ignore[arg-type]

    bound(None)

    assert route.label == "Deal Bonus Card…"
    assert fired == [context]


def test_make_route_handler_leaves_non_correction_dialogs_to_open_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DIALOG row outside every wired dispatch opens generically."""
    route = commands.route_for_id("mi_selftest")
    opened: list[object] = []
    monkeypatch.setattr(app_module, "_open_target", lambda _context, _route: opened.append(_route))
    context = _StubContext()

    bound = app_module._make_route_handler(context, route)  # type: ignore[arg-type]
    bound(None)

    assert opened == [route]


# ux-polish: Ride ▸ Set Start Time… was the final dead Ride row --
# its DIALOG target opened through _open_target's generic path and a
# confirmed OK did nothing; it binds its own real handler through
# _LIVE_FLOW_HANDLERS (target-keyed, the same shape as the E5.4.1
# ride confirms). W5 gave Stop Ride… its own COMMAND branch instead
# (pinned below); only Set Start Time… dispatches through the table
# now.
_LIVE_FLOW_ROUTES = (ids.MI_SET_START_TIME,)


@pytest.mark.parametrize("item_id", _LIVE_FLOW_ROUTES, ids=lambda value: value)
def test_make_route_handler_dispatches_the_wired_ride_menu_flows_by_target(
    monkeypatch: pytest.MonkeyPatch,
    item_id: str,
) -> None:
    """Each wired Ride row binds its own handler, not a generic open."""
    route = commands.route_for_id(item_id)
    fired: list[object] = []

    def stub_handler(context: object) -> None:
        """Record the routed context, like the real handler would."""
        fired.append(context)

    # _open_target records any fall-through so a regression to the
    # generic open fails the assertion instead of silently no-op'ing.
    monkeypatch.setattr(app_module, "_open_target", lambda _context, _route: fired.append("open"))
    # setitem, not setattr: the dispatch table holds the handler
    # reference, so replacing the module attribute would not rebind it
    # (the correction-row pin above uses the same setitem).
    monkeypatch.setitem(app_module._LIVE_FLOW_HANDLERS, route.target, stub_handler)
    context = _StubContext()

    bound = app_module._make_route_handler(context, route)  # type: ignore[arg-type]
    bound(None)

    assert fired == [context]


def test_make_route_handler_dispatches_mi_stop_ride_to_the_live_presenter_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W5: mi_stop_ride fires the presenter's native stop-confirm flow.

    The stop row is now a COMMAND row (``target="stop_ride"``) with
    its own ``_make_route_handler`` branch, mirroring the start_ride
    branch -- the same handler the console Stop button fires, so the
    menu row and the button cannot drift. A fall-through to
    ``_open_target`` (or to the generic COMMAND stub) fails the
    assertion instead of silently no-op'ing.
    """
    route = commands.route_for_id(ids.MI_STOP_RIDE)
    presenter = _RecordingPresenter()
    fallbacks: list[str] = []
    monkeypatch.setattr(
        app_module, "_open_target", lambda _context, _route: fallbacks.append("open")
    )
    context = _StubContext()
    context.presenter = presenter  # type: ignore[attr-defined]

    bound = app_module._make_route_handler(context, route)  # type: ignore[arg-type]
    bound(None)

    assert presenter.calls == ["on_stop_requested"]
    assert fallbacks == []


def test_make_route_handler_given_mi_stop_ride_without_presenter_posts_the_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W5: a presenter-less stop route posts the generic stub."""
    route = commands.route_for_id(ids.MI_STOP_RIDE)
    notices: list[str] = []
    monkeypatch.setattr(
        app_module, "_open_target", lambda _context, _route: notices.append("open")
    )
    context = _StubContext()
    context.frame = _NoticeFrame(notices)  # type: ignore[attr-defined]

    bound = app_module._make_route_handler(context, route)  # type: ignore[arg-type]
    bound(None)

    assert notices == [f"{route.label} — not yet implemented"]


# ============================================================ W11 F2a
# F2a (dead-control wiring): the console's flagged list had no
# activation binding, so a scorer could never open anything from a
# flagged crossing. Plan §6 refines the seam: a flagged row is a short
# lap with a card *disposition* -- held (hold mode, R-34) or credited
# (always-deal) -- so the activation carries ``(plate, held)`` and the
# app routes it: held -> a Confirm/Void decision through
# ``engine.confirm_held`` / ``engine.void_held``, credited -> the
# crossing detail on that lap. A stale row (no crossing resolves) posts
# a status notice instead of opening anything.


class _OpenFlaggedConsole:
    """A console-view stand-in recording the flagged-open callback."""

    def __init__(self) -> None:
        """Start with no registered callback."""
        self._on_open_flagged: object | None = None

    def set_on_open_flagged(self, callback: object) -> None:
        """Record the callback the app wired."""
        self._on_open_flagged = callback


class _RouteStub:
    """The route-context surface the W11 seam helpers read."""

    def __init__(  # noqa: PLR0913 -- mirrors the live _RouteContext fields the seams touch
        self,
        *,
        frame: object = None,
        roster: object = None,
        presenter: object = None,
        console_view: object = None,
        resource: object = None,
    ) -> None:
        """Store the threaded surfaces."""
        self.frame = frame
        self.roster = roster
        self.presenter = presenter
        self.console_view = console_view
        self.resource = resource


def test_wire_flagged_open_seam_routes_the_flagged_row_to_the_review_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2a/§6: the wired seam passes the row's plate + held.

    ``_open_flagged_review_for`` is the app-side router; this pins the
    wiring half (the console fires ``callback(plate, held)``).
    """
    opened: list[tuple[object, str, bool]] = []
    monkeypatch.setattr(
        app_module,
        "_open_flagged_review_for",
        lambda context, plate, held: opened.append((context, plate, held)),
    )
    console = _OpenFlaggedConsole()
    context = _RouteStub(console_view=console)

    app_module._wire_flagged_open_seam(context)  # type: ignore[arg-type]
    console._on_open_flagged("77", True)  # type: ignore[attr-defined]  # noqa: FBT003 -- seam's flag

    assert opened == [(context, "77", True)]


def test_wire_flagged_open_seam_without_a_console_view_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A console-less route-level context has nothing to wire."""
    called: list[tuple[object, ...]] = []
    monkeypatch.setattr(app_module, "_open_flagged_review_for", lambda *args: called.append(args))

    app_module._wire_flagged_open_seam(_RouteStub(console_view=None))  # type: ignore[arg-type]

    assert called == []


# ============================================== plan §6 review routing
# The app-side half: resolve the (plate, held) pair back to the live
# crossing the feed row shows, then route by card disposition.


def _dt(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Build a naive datetime on the fixed event day."""
    return datetime(2026, 9, 20, hour, minute, second)  # noqa: DTZ001 -- naive by design


def _running_engine(*, hold_short_laps: bool, min_lap_s: int = 60) -> RideEngine:
    """Build a RUNNING engine over one solo entry (plate 12)."""
    config = gorba_config(min_lap_s=min_lap_s, hold_short_laps=hold_short_laps)
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider 12", last_name="", plate="12")
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    engine = RideEngine(config=config, shoe=shoe, clock=lambda: _dt(10, 0), roster=roster)
    engine.start()
    return engine


def _review_context(
    engine: RideEngine,
    *,
    frame: object = None,
    resource: object = None,
) -> _RouteStub:
    """Build the route context over the live *engine* and source."""
    source = EngineDataSource(engine, engine._roster)
    presenter = type("_Presenter", (), {"engine": engine, "source": source})()
    return _RouteStub(frame=frame, roster=engine._roster, presenter=presenter, resource=resource)


def _stub_show(
    monkeypatch: pytest.MonkeyPatch, name: str, result: int
) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    """Swap ``std_dialogs.<name>`` for a recorder returning *result*."""
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _show(*args: object, **kwargs: object) -> int:
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(std_dialogs, name, _show)
    return calls


class _FakeWindow:
    """A wx-window-shaped stub: closable, nothing else."""

    def IsBeingDeleted(self) -> bool:  # noqa: N802 -- wx API name
        """Report this stub is never mid-delete."""
        return False

    def Destroy(self) -> None:  # noqa: N802 -- wx API name
        """No-op: there is no real window to destroy."""


class _FakeResource:
    """A resource-shaped stub returning one window (or ``None``)."""

    def __init__(self, window: object) -> None:
        """Store the window every LoadDialog call returns."""
        self.window = window

    def LoadDialog(self, _parent: object, _name: object) -> object:  # noqa: N802 -- wx API name
        """Return the stored window for the requested target."""
        return self.window


def test_open_flagged_review_for_given_a_held_row_resolves_the_held_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held row resolves to the crossing in the hold queue."""
    engine = _running_engine(hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 0, 5))
    context = _review_context(engine, frame=_NoticeFrame([]))
    routed: list[object] = []
    monkeypatch.setattr(
        app_module,
        "_review_held_crossing",
        lambda _context, _engine, crossing: routed.append(crossing),
    )

    app_module._open_flagged_review_for(context, "12", True)  # noqa: FBT003 -- seam's flag

    assert routed == [engine.crossings[-1]]


def test_open_flagged_review_for_given_a_credited_row_resolves_the_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A credited short lap resolves to its recorded crossing."""
    engine = _running_engine(hold_short_laps=False)
    engine.record_crossing("12", at=_dt(10, 0, 5))
    context = _review_context(engine, frame=_NoticeFrame([]))
    routed: list[object] = []
    monkeypatch.setattr(
        app_module,
        "_show_crossing_detail_dialog",
        lambda _context, _engine, target: routed.append(target),
    )

    app_module._open_flagged_review_for(context, "12", False)  # noqa: FBT003 -- seam's flag

    assert routed == [engine.crossings[-1]]


def test_open_flagged_review_for_given_a_duplicate_row_routes_to_the_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 3: a duplicate row opens Crossing Detail on its own lap.

    The ride records 10:05, 10:06, then 10:05 again -- so the newest
    crossing is the duplicate (its twin is lap 1) and it is not itself
    a short-lap flag. Only the duplicate bit routes it here; a flagged
    row is never what this activation names.
    """
    engine = _running_engine(hold_short_laps=False)
    engine.record_crossing("12", at=_dt(10, 5))
    engine.record_crossing("12", at=_dt(10, 6))
    engine.record_crossing("12", at=_dt(10, 5))
    context = _review_context(engine, frame=_NoticeFrame([]))
    routed: list[object] = []
    monkeypatch.setattr(
        app_module,
        "_show_crossing_detail_dialog",
        lambda _context, _engine, target: routed.append(target),
    )

    app_module._open_flagged_review_for(context, "12", False)  # noqa: FBT003 -- seam's flag

    assert routed == [engine.crossings[-1]]


@pytest.mark.parametrize("held", [True, False], ids=["held", "credited"])
def test_open_flagged_review_for_given_an_unresolvable_plate_posts_a_notice(
    monkeypatch: pytest.MonkeyPatch, *, held: bool
) -> None:
    """A stale activation (plate absent from the feed) opens nothing."""
    engine = _running_engine(hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 0, 5))
    notices: list[str] = []
    context = _review_context(engine, frame=_NoticeFrame(notices))
    routed: list[str] = []
    monkeypatch.setattr(app_module, "_review_held_crossing", lambda *_args: routed.append("held"))
    monkeypatch.setattr(
        app_module, "_show_crossing_detail_dialog", lambda *_args: routed.append("detail")
    )

    app_module._open_flagged_review_for(context, "99", held)

    assert (notices, routed) == (["Review — no crossing found for plate 99"], [])


def test_open_flagged_review_for_given_a_credited_row_asked_as_held_posts_a_notice() -> None:
    """The held search covers the hold queue only: the pair is stale."""
    engine = _running_engine(hold_short_laps=False)
    engine.record_crossing("12", at=_dt(10, 0, 5))
    notices: list[str] = []
    context = _review_context(engine, frame=_NoticeFrame(notices))

    app_module._open_flagged_review_for(context, "12", True)  # noqa: FBT003 -- seam's flag

    assert notices == ["Review — no crossing found for plate 12"]


def test_open_flagged_review_for_given_a_miss_row_posts_a_notice() -> None:
    """A miss row is never a flagged row, so it resolves to nothing."""
    engine = _running_engine(hold_short_laps=False)
    engine.record_miss(_dt(10, 0, 5), reason="missed number")
    notices: list[str] = []
    context = _review_context(engine, frame=_NoticeFrame(notices))

    app_module._open_flagged_review_for(context, "-", False)  # noqa: FBT003 -- seam's flag

    assert notices == ["Review — no crossing found for plate -"]


def test_open_flagged_review_for_given_no_presenter_posts_nothing() -> None:
    """A context with no live console has nothing to review."""
    notices: list[str] = []
    context = _RouteStub(frame=_NoticeFrame(notices), presenter=None)

    app_module._open_flagged_review_for(context, "12", True)  # noqa: FBT003 -- seam's flag

    assert notices == []


def test_held_card_facts_given_a_held_crossing_names_entry_plate_lap_and_card() -> None:
    """UX-DESKTOP §4: the confirm names the object it is about."""
    engine = _running_engine(hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 0, 5))
    crossing = engine.crossings[-1]

    facts = app_module._held_card_facts(engine, crossing, engine._roster)

    assert facts == (
        f"Rider 12 · plate 12 · Lap 1 · {format_duration(5.0)} · "
        f"{engine.card_for(crossing).code()}"
    )


def test_held_card_facts_given_a_lap_past_the_recorded_times_renders_a_zero_time() -> None:
    """A stale crossing renders a zero duration and no card."""
    engine = _running_engine(hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 0, 5))
    stale = Crossing(entry_id="12", seq=99, crossed_at=_dt(10, 0, 5))

    facts = app_module._held_card_facts(engine, stale, engine._roster)

    assert facts == f"Rider 12 · plate 12 · Lap 99 · {format_duration(0.0)} · no card"


def test_held_card_facts_given_an_unresolvable_entry_falls_back_to_the_entry_id() -> None:
    """An unresolvable entry falls back to the crossing's entry id."""
    engine = _running_engine(hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 0, 5))
    crossing = engine.crossings[-1]

    facts = app_module._held_card_facts(engine, crossing, Roster())

    assert facts.startswith("12 · plate 12 · Lap 1 · ")


def test_review_held_crossing_given_a_confirmed_prompt_releases_the_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirm releases the held card into the entry's credited hand."""
    engine = _running_engine(hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 0, 5))
    crossing = engine.crossings[-1]
    notices: list[str] = []
    context = _review_context(engine, frame=_NoticeFrame(notices))
    calls = _stub_show(monkeypatch, "show_prompt", wx.ID_OK)

    app_module._review_held_crossing(context, engine, crossing)

    parent, title, message, ok_label, cancel_label = calls[0][0]
    assert (parent, title, ok_label, cancel_label) == (
        context.frame,
        "Review Held Card",
        "Confirm card",
        "Cancel",
    )
    assert "Rider 12" in message
    assert "Lap 1" in message
    assert engine.held_crossings() == ()
    assert engine.credited_cards("12") == (engine.card_for(crossing),)
    assert notices == ["Card confirmed for plate 12"]


def test_review_held_crossing_given_confirmed_void_discards_the_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Declining the confirm, then confirming the void discards it."""
    engine = _running_engine(hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 0, 5))
    crossing = engine.crossings[-1]
    notices: list[str] = []
    context = _review_context(engine, frame=_NoticeFrame(notices))
    _stub_show(monkeypatch, "show_prompt", wx.ID_CANCEL)
    calls = _stub_show(monkeypatch, "show_danger", wx.ID_OK)

    app_module._review_held_crossing(context, engine, crossing)

    _parent, title, _message, ok_label, cancel_label = calls[0][0]
    assert (title, ok_label, cancel_label) == ("Void Held Card?", "Void card", "Keep held")
    assert engine.held_crossings() == ()
    assert engine.credited_cards("12") == ()
    assert notices == ["Card voided for plate 12"]


def test_review_held_crossing_given_both_declined_keeps_the_card_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both declines keep the card in the hold queue."""
    engine = _running_engine(hold_short_laps=True)
    engine.record_crossing("12", at=_dt(10, 0, 5))
    crossing = engine.crossings[-1]
    notices: list[str] = []
    context = _review_context(engine, frame=_NoticeFrame(notices))
    _stub_show(monkeypatch, "show_prompt", wx.ID_CANCEL)
    _stub_show(monkeypatch, "show_danger", wx.ID_CANCEL)

    app_module._review_held_crossing(context, engine, crossing)

    assert [held.crossing for held in engine.held_crossings()] == [crossing]
    assert engine.credited_cards("12") == ()
    assert notices == []


def test_open_flagged_review_for_given_a_credited_short_lap_builds_the_crossing_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The credited path opens ``crossing_detail_dlg`` on the lap."""
    engine = _running_engine(hold_short_laps=False)
    engine.record_crossing("12", at=_dt(10, 0, 5))
    context = _review_context(
        engine, frame=_NoticeFrame([]), resource=_FakeResource(_FakeWindow())
    )
    built: list[dict[str, object]] = []
    monkeypatch.setattr(
        crossing_detail_module,
        "CrossingDetailView",
        lambda *_args, **kwargs: built.append(kwargs),
    )
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(
        dialogs,
        "run_dialog",
        lambda _dialog, opener: 0,  # noqa: ARG005 -- the SUT calls opener=; the stub ignores it
    )

    app_module._open_flagged_review_for(context, "12", False)  # noqa: FBT003 -- seam's flag

    assert built == [
        {"crossing": engine.crossings[-1], "roster": engine._roster, "engine": engine}
    ]


def test_show_crossing_detail_dialog_given_no_window_posts_the_notice() -> None:
    """An unauthored crossing_detail_dlg posts the app's own notice."""
    engine = _running_engine(hold_short_laps=False)
    engine.record_crossing("12", at=_dt(10, 0, 5))
    notices: list[str] = []
    context = _review_context(engine, frame=_NoticeFrame(notices), resource=_FakeResource(None))

    app_module._show_crossing_detail_dialog(context, engine, engine.crossings[-1])

    assert notices == ["Crossing Detail — no window authored yet"]


def test_show_crossing_detail_dialog_given_a_pending_miss_builds_the_miss_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """K2: a miss target keeps the same dialog in its miss mode."""
    engine = _running_engine(hold_short_laps=False)
    engine.record_miss(_dt(10, 0, 5), reason="missed number")
    miss = engine.pending_misses()[0]
    context = _review_context(
        engine, frame=_NoticeFrame([]), resource=_FakeResource(_FakeWindow())
    )
    built: list[dict[str, object]] = []
    monkeypatch.setattr(
        crossing_detail_module, "MissDetailView", lambda *_args, **kwargs: built.append(kwargs)
    )
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(
        dialogs,
        "run_dialog",
        lambda _dialog, opener: 0,  # noqa: ARG005 -- the SUT calls opener=; the stub ignores it
    )

    app_module._show_crossing_detail_dialog(context, engine, miss)

    assert built == [{"miss": miss, "engine": engine}]


# ============================================================ W11 F3
# The FINISHED banner: ``finished_infobar`` was constructed but never
# shown (xrc-windows.md A's frozen-but-unimplemented state variant).
# MainFrame.set_state now shows it with the app-wired Reopen/Results
# actions; these pins keep the app-side wiring on the right flows (the
# same ``_handle_reopen_ride_route`` mi_reopen_ride runs, and the
# mi_standings results open).


class _FinishedActionsConsole:
    """A console-view stand-in recording the finished-actions wiring."""

    def __init__(self) -> None:
        """Start with no registered actions."""
        self._actions: dict[str, object] = {}

    def set_finished_actions(self, **actions: object) -> None:
        """Record the action callbacks the app wired."""
        self._actions = actions


def test_wire_finished_banner_actions_wires_reopen_and_view_results_flows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F3: the banner's two buttons fire the menu's own two flows.

    Reopen runs the same ``_handle_reopen_ride_route`` (with its
    confirm) that ``mi_reopen_ride`` runs; View results opens the
    results frame through the same ``mi_standings`` target the Results
    menu row opens.
    """
    reopened: list[object] = []
    opened: list[object] = []
    monkeypatch.setattr(app_module, "_handle_reopen_ride_route", reopened.append)
    monkeypatch.setattr(app_module, "_open_target", lambda _context, route: opened.append(route))
    console = _FinishedActionsConsole()
    context = _RouteStub(console_view=console)

    app_module._wire_finished_banner_actions(context)  # type: ignore[arg-type]
    console._actions["on_reopen"]()  # type: ignore[operator]
    console._actions["on_view_results"]()  # type: ignore[operator]

    assert reopened == [context]
    assert opened == [commands.route_for_id("mi_standings")]


def test_wire_finished_banner_actions_without_a_console_view_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A console-less route-level context has nothing to wire."""
    called: list[object] = []
    monkeypatch.setattr(
        app_module, "_handle_reopen_ride_route", lambda _context: called.append("reopen")
    )
    monkeypatch.setattr(app_module, "_open_target", lambda _context, _route: called.append("open"))

    app_module._wire_finished_banner_actions(_RouteStub(console_view=None))  # type: ignore[arg-type]

    assert called == []


# ================================== Phase 3: Riders ▸ Mark DNF…
#
# The DNF row is the one correction whose target is typed into the
# dialog itself: it works with nothing open (the operator types the
# rider number) and marks exactly the plate that comes back. Phase 2
# dropped the entry-detail deep-link, so the route always opens the
# dialog unprefilled. ``corrections.run_dnf`` is the wx dialog boundary,
# so it is swapped for a recorder here -- the same seam
# test_app_ride_menu.py's ``_patch_ride_setup`` uses for the setup
# dialog.


class _DnfFrame:
    """Record the status-bar notices the DNF route posts."""

    def __init__(self) -> None:
        """Start with an empty notice log."""
        self.notices: list[str] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record one status-bar notice."""
        self.notices.append(text)


class _DnfPresenter:
    """Expose the engine and record the post-correction tick."""

    def __init__(self, engine: RideEngine) -> None:
        """Store the engine and start with a zero tick count."""
        self.engine = engine
        self.ticks = 0

    def tick(self) -> None:
        """Record the console refresh ``_apply_correction`` fires."""
        self.ticks += 1


class _DnfContext:
    """The route-context surface ``_handle_mark_dnf_route`` reads."""

    def __init__(self, engine: RideEngine) -> None:
        """Wire the engine, its roster and the recording surfaces."""
        self.resource = object()
        self.roster = engine._roster
        self.frame = _DnfFrame()
        self.presenter = _DnfPresenter(engine)


def _patch_run_dnf(monkeypatch: pytest.MonkeyPatch, result: DnfMark | None) -> dict[str, object]:
    """Swap the dialog runner for a recorder returning *result*."""
    captured: dict[str, object] = {}

    def _fake_run_dnf(_resource: object, **kwargs: object) -> DnfMark | None:
        captured.update(kwargs)
        return result

    monkeypatch.setattr(corrections, "run_dnf", _fake_run_dnf)
    return captured


def test_handle_mark_dnf_route_opens_the_dialog_unprefilled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 2: nothing has to be open -- the dialog asks the number."""
    engine = _running_engine(hold_short_laps=False)
    context = _DnfContext(engine)
    captured = _patch_run_dnf(monkeypatch, DnfMark(plate="12", reason="mechanical failure"))

    app_module._handle_mark_dnf_route(context)  # type: ignore[arg-type]

    assert (captured["plate"], captured["entry"]) == ("", "")
    assert [event.action for event in engine.events[-1:]] == ["dnf"]
    assert context.frame.notices == ["DNF marked"]
    assert context.presenter.ticks == 1


def test_handle_mark_dnf_route_given_a_cancelled_dialog_marks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel is a silent no-op: no event, no notice, no refresh."""
    engine = _running_engine(hold_short_laps=False)
    context = _DnfContext(engine)
    before = len(engine.events)
    _patch_run_dnf(monkeypatch, None)

    app_module._handle_mark_dnf_route(context)  # type: ignore[arg-type]

    assert len(engine.events) == before
    assert context.frame.notices == []
    assert context.presenter.ticks == 0


def test_handle_mark_dnf_route_without_a_ride_posts_the_no_ride_notice() -> None:
    """With no engine the row says so; no dialog is opened."""
    notices: list[str] = []
    context = _StubContext()
    context.frame = _NoticeFrame(notices)

    app_module._handle_mark_dnf_route(context)  # type: ignore[arg-type]

    assert notices == ["Mark DNF… — no ride open"]
