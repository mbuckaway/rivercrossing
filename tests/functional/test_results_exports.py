# SPDX-License-Identifier: GPL-3.0-only
"""Functional: the Results ▸ export rows write real files (E6.4.2).

Extends the menu-coverage walk's reachability proof with a
side-effect proof: for each Results export row, patch the picker and
off-loop seams, fire the menu event at the real app frame, and assert
a real file landed with the expected content. Runs only in the Tart
VM (``pytestmark = functional``; AGENTS.md hard rule).

E7.3.2 (the stale-export flag) adds the end-to-end lifecycle proof:
export -> correct -> the results window's ``stale_infobar`` shows;
re-export clears it. The same live-app context ``test_corrections.py``
builds drives the real correction route (dialog included), so the
flag's trigger is the real engine event log, not a stub.
"""

import pathlib
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import harness
import pytest
import wx

from rivercrossing.cards import Shoe
from rivercrossing.ride import RideConfig, RideEngine
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids, theme
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EngineDataSource
from rivercrossing.ui.views import MainFrame
from rivercrossing.ui.views.results_win import STALE_INFOBAR

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.functional

EXPORT_ROWS = (
    ("mi_export_html", "results.html", "race-data"),
    ("mi_export_pdf", "results.pdf", None),
    ("mi_export_poster", "podium.pdf", None),
    ("mi_export_results_csv", "standings.csv", "place,plate,entry,laps,hand"),
)


@pytest.fixture(scope="module")
def firing_frame(wx_app: object) -> Any:  # noqa: ANN401 -- ordering only, see docstring
    """Build the one ``main_frame`` the export rows fire at.

    Same shape as ``test_app_open_target.firing_frame`` (which is
    module-local, not shared) -- a real ``build_main_window`` frame
    whose bound route handlers carry the live console engine.
    Module-scoped: six tests sharing one build/close keeps the
    main-frame churn (and its bus-error exposure under xdist load)
    at one close instead of six. The teardown sets ``really_quitting``
    first, the ``console_subprocess_scenarios`` pattern: a plain close
    on a fresh app would hit ``_on_main_frame_close``'s
    ``context.app.really_quitting`` guard before the quit flow ever
    set the attribute (measured crash, rerun-2 teardown).
    """
    frame = app_module.build_main_window(wx_app)
    try:
        yield frame
    finally:
        wx.GetApp().really_quitting = True
        harness.close_window(frame)


def _sync_offloop(  # noqa: PLR0913 -- the seam mirrors _run_export_offloop.s inputs
    context: object,
    target: str,
    path: object,
    *,
    config: object,
    placed: object,
    opts: object,
    watermark: int | None = None,
) -> None:
    """Run the export synchronously; record it as completion does.

    Mirrors ``_run_export_offloop``'s success-path side effects: the
    recorded path, the advanced context watermark, and the open
    results window's banner clear (E7.3.2) -- so the walk can assert
    the file AND the stale flag in one call.
    """
    app_module._write_export(config, placed, opts, target, path)  # type: ignore[arg-type]
    context.last_export_path = path  # type: ignore[attr-defined]
    context.export_watermark = watermark  # type: ignore[attr-defined]
    if watermark is not None:
        app_module._clear_results_stale(watermark)


@pytest.mark.parametrize(
    ("item_id", "name", "content"),
    EXPORT_ROWS,
    ids=[row[0] for row in EXPORT_ROWS],
)
def test_results_export_rows_write_real_files(  # noqa: PLR0913, PLR0917 -- parametrized row + shared fixtures
    firing_frame: object,
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
    item_id: str,
    name: str,
    content: str | None,
) -> None:
    """Each export row writes its file through the real handler."""
    out = pathlib.Path(str(tmp_path)) / name
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _suggested: out)
    monkeypatch.setattr(app_module, "_run_export_offloop", _sync_offloop)

    harness.fire_menu_event(firing_frame, item_id)

    assert out.exists(), f"{item_id} wrote no file at {out}"
    assert out.stat().st_size > 0
    if content is not None:
        text = out.read_text(encoding="utf-8")
        assert content in text, f"{item_id} file lacks {content!r}"


def test_results_preview_browser_opens_the_last_export(
    firing_frame: object, tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preview in Browser opens the export the handler recorded."""
    out = pathlib.Path(str(tmp_path)) / "results.html"
    opened: list[object] = []
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _suggested: out)
    monkeypatch.setattr(app_module, "_run_export_offloop", _sync_offloop)
    monkeypatch.setattr(app_module, "_open_in_browser", opened.append)

    harness.fire_menu_event(firing_frame, ids.MI_EXPORT_HTML)
    harness.fire_menu_event(firing_frame, ids.MI_PREVIEW_BROWSER)

    assert opened == [out]


# ------------------------------------------- E7.3.2 stale-export flag

_TIMEOUT_SENTINEL = -999


def _build_live_context(
    xrc_resource: object, wx_app: object
) -> tuple[app_module._RouteContext, RideEngine, Roster]:
    """Build a live app context whose engine has two entries.

    Mirrors ``test_corrections.py``'s own builder (same shape: a real
    ``main_frame`` with its menubar, a RUNNING engine, a live console +
    presenter, and a ``_RouteContext`` threaded the way
    ``build_main_window`` does), except the clock is a fixed naive
    instant -- ``test_corrections.py``'s aware-UTC clock TypeErrors
    against the naive crossing instants (the latent bug this task's
    report records). The Results menu row, the export rows and the
    correction routes all act on real data, and tests can set
    ``context.detail_plate`` for the correction routes that target
    "the current entry".
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider", last_name="12", plate="12")
    roster.create_solo_entry(first_name="Rider", last_name="34", plate="34")
    config = RideConfig(
        name="GORBA EPIC 2026",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive, RideConfig's own contract
        planned_duration_s=21600,
        min_lap_s=1,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    engine = RideEngine(
        config=config,
        shoe=shoe,
        clock=lambda: datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive clock, matching the naive crossing instants
        roster=roster,
    )
    engine.start()
    engine.record_crossing("12", at=datetime(2026, 9, 20, 10, 30))  # noqa: DTZ001
    source = EngineDataSource(engine, roster)
    frame = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    menubar = harness.load_menubar(xrc_resource, ids.MAIN_MENUBAR)
    frame.SetMenuBar(menubar)
    frame.Show()
    frame.Layout()
    harness.pump()
    console = MainFrame(frame, data_source=source, resource=xrc_resource)
    presenter = ConsolePresenter(console, engine=engine, source=source)
    console.wire_entry(presenter.on_plate_entered)
    console.wire_console(presenter)
    console.set_state(source.ride_status())
    context = app_module._RouteContext(
        frame=frame,
        resource=xrc_resource,
        roster=roster,
        app=wx_app,
        theme_controller=theme.ThemeController(wx_app),
        presenter=presenter,
        console_view=console,
        detail_plate="12",
    )
    app_module._bind_routes(context)
    app_module._apply_menu_state(context, engine.state)
    return context, engine, roster


@pytest.fixture
def live_context(
    xrc_resource: object, wx_app: object
) -> Iterator[tuple[app_module._RouteContext, RideEngine, Roster]]:
    """One live app context the E7.3.2 stale-export test fires at."""
    context, engine, roster = _build_live_context(xrc_resource, wx_app)
    try:
        yield context, engine, roster
    finally:
        harness.close_window(context.frame)


def _schedule_drive(dialog_name: str, drive: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Schedule *drive* on the modal *dialog_name* once it opens."""

    def _run() -> None:
        dialog = wx.Window.FindWindowByName(dialog_name)
        if dialog is not None:
            drive(dialog)

    wx.CallAfter(_run)


def _schedule_end_modal_if_undecided(dialog_name: str) -> None:
    """Schedule the safety-net EndModal for the modal *dialog_name*."""

    def _run() -> None:
        dialog = wx.Window.FindWindowByName(dialog_name)
        if dialog is not None and dialog.IsModal() and dialog.GetReturnCode() == 0:
            dialog.EndModal(_TIMEOUT_SENTINEL)

    wx.CallAfter(_run)


def _open_results(app_frame: object) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Fire the Results menu row and return the frame it opened.

    Raises:
        AssertionError: The row opened no ``results_frame`` -- the
            route failed, which is exactly what the caller is checking
            for before touching the stale banner.
    """
    harness.fire_menu_event(app_frame, ids.MI_STANDINGS)
    frame = wx.FindWindowByName(ids.RESULTS_FRAME)
    if frame is None:
        raise AssertionError("mi_standings opened no results_frame")
    return frame


def test_results_stale_banner_appears_after_a_post_export_correction_and_reexport_clears(
    live_context: tuple[app_module._RouteContext, RideEngine, Roster],
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E7.3.2: export -> correct -> banner shown; re-export clears.

    The real menu rows drive the whole lifecycle at the live app: the
    Results row opens the window (no export yet -> banner hidden), the
    export row records the event-count watermark (banner stays hidden),
    the Edit Crossing route lands a correction event past that
    watermark (reopening Results shows the banner), and a fresh export
    clears it without reopening.
    """
    context, engine, _roster = live_context
    frame = context.frame
    out = pathlib.Path(str(tmp_path)) / "results.html"
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _suggested: out)
    monkeypatch.setattr(app_module, "_run_export_offloop", _sync_offloop)

    results_frame = _open_results(frame)
    bar = harness.find_control(results_frame, STALE_INFOBAR)
    assert bar.IsShown() is False
    assert context.export_watermark is None

    harness.fire_menu_event(frame, ids.MI_EXPORT_HTML)
    assert context.export_watermark == len(engine.events)
    assert bar.IsShown() is False

    harness.close_window(results_frame)
    context.detail_plate = "12"

    def _drive_edit(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        harness.type_text(dialog, ids.REASON_INPUT, "mis-keyed time")
        harness.click(dialog, "wxID_OK")

    _schedule_drive(ids.EDIT_CROSSING_DLG, _drive_edit)
    _schedule_end_modal_if_undecided(ids.EDIT_CROSSING_DLG)
    harness.fire_menu_event(frame, ids.MI_EDIT_CROSSING)
    assert engine.events[-1].action == "edit_crossing"

    results_frame = _open_results(frame)
    try:
        bar = harness.find_control(results_frame, STALE_INFOBAR)
        assert bar.IsShown() is True

        harness.fire_menu_event(frame, ids.MI_EXPORT_HTML)
        assert context.export_watermark == len(engine.events)
        assert bar.IsShown() is False
    finally:
        harness.close_window(results_frame)
