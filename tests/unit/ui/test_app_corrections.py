# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for app.py's ``_make_route_handler`` dispatch.

The six Cards/Riders correction rows dispatch through
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
``_make_route_handler`` suite; the handlers themselves (real wx
dialogs + real engine commands) are functionally covered by
``tests/functional/test_corrections.py``,
``test_void_card_confirm.py`` and ``test_menu_coverage.py``.
"""

import pytest

from rivercrossing.ui import app as app_module
from rivercrossing.ui import commands, ids

_CORRECTION_ROUTES = (
    ids.MI_ADD_CROSSING_AT,
    ids.MI_EDIT_CROSSING,
    ids.MI_REASSIGN_PLATE,
    ids.MI_DEAL_MANUAL,
    ids.MI_MARK_DNF,
    ids.MI_VOID_CARD,
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


# ============================================================ W11
# F2a (dead-control wiring): the console's flagged list had no
# activation binding, so a scorer could never open Entry Detail from a
# flagged crossing -- and ``_RouteContext.detail_plate`` was only ever
# self-assigned, so the menu correction routes refused. The app now
# wires a ``set_on_open_flagged`` seam (mirroring the Riders tab's
# ``set_on_open_rider``) whose handler opens the live entry detail at
# the flagged plate and records it as the current entry.


class _OpenFlaggedConsole:
    """A console-view stand-in recording the flagged-open callback."""

    def __init__(self) -> None:
        """Start with no registered callback."""
        self._on_open_flagged: object | None = None

    def set_on_open_flagged(self, callback: object) -> None:
        """Record the callback the app wired."""
        self._on_open_flagged = callback


class _EntryDialogSpy:
    """EntryDetailDialog stand-in recording its constructor inputs."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Store the constructor inputs for later assertions."""
        self.args = args
        self.kwargs = kwargs


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
        self.detail_plate: str | None = None


def test_wire_flagged_open_seam_opens_the_entry_detail_at_the_flagged_plate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2a: the wired seam opens the live detail for the activated row.

    ``_open_entry_detail_for`` is the app-side opener; this pins the
    wiring half (the console only fires ``callback(plate)``).
    """
    opened: list[tuple[object, str]] = []
    monkeypatch.setattr(
        app_module,
        "_open_entry_detail_for",
        lambda context, plate: opened.append((context, plate)),
    )
    console = _OpenFlaggedConsole()
    context = _RouteStub(console_view=console)

    app_module._wire_flagged_open_seam(context)  # type: ignore[arg-type]
    console._on_open_flagged("77")  # type: ignore[attr-defined]

    assert opened == [(context, "77")]


def test_wire_flagged_open_seam_without_a_console_view_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A console-less route-level context has nothing to wire."""
    called: list[object] = []
    monkeypatch.setattr(
        app_module, "_open_entry_detail_for", lambda _context, _plate: called.append(1)
    )

    app_module._wire_flagged_open_seam(_RouteStub(console_view=None))  # type: ignore[arg-type]

    assert called == []


def test_open_entry_detail_dialog_live_branch_records_and_builds_the_live_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2a: the live branch records ``detail_plate`` and builds live.

    With a live presenter and a concrete plate the dialog opens over
    the console's engine/source/roster/resource -- the six action
    buttons act on real data -- and the plate becomes the current
    entry the menu correction routes target.
    """
    import rivercrossing.ui.views.entry_detail as entry_detail_module  # noqa: PLC0415 -- deferred-import spy target

    engine = object()
    source = object()
    roster = object()
    built: list[_EntryDialogSpy] = []
    monkeypatch.setattr(
        entry_detail_module,
        "EntryDetailDialog",
        lambda *args, **kwargs: built.append(_EntryDialogSpy(*args, **kwargs)) or built[-1],
    )
    presenter = type("_Presenter", (), {"engine": engine, "source": source})()
    context = _RouteStub(frame=_NoticeFrame([]), roster=roster, presenter=presenter)

    app_module._open_entry_detail_dialog(context, object(), "77")  # type: ignore[arg-type]

    assert context.detail_plate == "77"
    assert len(built) == 1
    assert built[0].kwargs["engine"] is engine
    assert built[0].kwargs["data_source"] is source
    assert built[0].kwargs["roster"] is roster
    # W11 F2b: the picker's app seam writes the current-entry context.
    on_plate_picked = built[0].kwargs["on_plate_picked"]
    on_plate_picked("34")
    assert context.detail_plate == "34"


def test_open_entry_detail_dialog_empty_branch_opens_the_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2a: no live selection keeps the E5.4.2 empty state.

    The empty branch must not record a plate: a correction menu route
    after this dialog closes still refuses, which is the honest
    reading of "open an entry first".
    """
    import rivercrossing.ui.views.entry_detail as entry_detail_module  # noqa: PLC0415 -- deferred-import spy target

    built: list[_EntryDialogSpy] = []
    monkeypatch.setattr(
        entry_detail_module,
        "EntryDetailDialog",
        lambda *args, **kwargs: built.append(_EntryDialogSpy(*args, **kwargs)) or built[-1],
    )
    context = _RouteStub(presenter=None)

    app_module._open_entry_detail_dialog(context, object(), "")  # type: ignore[arg-type]

    assert context.detail_plate is None
    assert len(built) == 1
    assert built[0].args[1] == app_module._ENTRY_DETAIL_DEFAULT_PLATE
    assert built[0].kwargs["data_source"] is app_module._EMPTY_SOURCE
