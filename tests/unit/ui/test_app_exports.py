# SPDX-License-Identifier: GPL-3.0-only
"""Headless unit tests for the E6.4.2 results-export route handlers.

Drives ``app._write_export`` and the ``_TARGET_ACTIONS`` dispatch with
stub engines/contexts (no wx app constructed): each export target
writes a real file with the expected content, the picker/off-loop/
browser seams are monkeypatched, and the no-engine and cancel paths
post notices instead of failing.
"""

from base64 import b64encode
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from pypdf import PdfReader

if TYPE_CHECKING:
    from collections.abc import Callable

from rivercrossing.cards import Card
from rivercrossing.hands import best_hand
from rivercrossing.htmlexport import ExportOptions
from rivercrossing.ride import RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.standings import EntryResult, Placed
from rivercrossing.ui import app as app_module
from rivercrossing.ui import std_dialogs
from rivercrossing.ui.cards_imagelist import SCALE_2X, asset_filename, asset_key, cards_dir
from rivercrossing.ui.presenters.settings import default_settings


class _StubConfig:
    """A ride-like config with the fields the export writers read."""

    name = "Test Poker Run"
    event_date = date(2026, 9, 20)
    venue = "Test Venue"
    lap_km = 8.0
    organizer = "Test Org"
    scorer = "T. Ester"
    tiebreak_order = ("laps", "total_time", "high_card")
    logo_path: Path | None = None
    entry_mode = EntryMode.SOLO


class _StubEngine:
    """The engine surface the export handlers and menu state read."""

    def __init__(  # noqa: PLR0913 -- a stub mirroring the engine's own inputs
        self,
        snapshot: tuple[EntryResult, ...],
        *,
        events: tuple = (),
        state: RideStatus = RideStatus.FINISHED,
        self_test_unverified: bool = False,
    ) -> None:
        """Store *snapshot* under a stub config and fixed event log.

        *state* is the live ride state the export-completion menu
        refresh reads; a REOPENED engine stages the reopened-mid-export
        case. *self_test_unverified* is E6.4.3's flag, which the export
        handler captures and hands to the writers.
        """
        self.config = _StubConfig()
        self._snapshot = snapshot
        self.events = events
        self.state = state
        self.stopped = False
        self.crossings: tuple = ()
        self.self_test_unverified = self_test_unverified

    def snapshot(self) -> tuple[EntryResult, ...]:
        """Return the stored results."""
        return self._snapshot

    def held_crossings(self) -> tuple:
        """Return no held crossings -- only the count is read."""
        return ()


class _StubFrame:
    """A minimal frame: status notices and an optional menubar."""

    def __init__(self, menubar: object = None) -> None:
        """Start with no notices and *menubar* (None = no menu bar)."""
        self.notices: list[str] = []
        self._menubar = menubar

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record *text* as the latest notice."""
        self.notices.append(text)

    def GetMenuBar(self) -> object:  # noqa: N802 -- wx API name
        """Return the staged menubar, or None."""
        return self._menubar


def _result(  # noqa: PLR0913 -- a fixture builder mirroring EntryResult's fields
    plate: str, codes: str, *, laps: int, total_time: float
) -> EntryResult:
    """Build one EntryResult whose hand is best_hand of *codes*."""
    cards = tuple(Card.parse(code) for code in codes.split())
    return EntryResult(
        entry_id=plate,
        plate=plate,
        name="Rider",
        kind="solo",
        laps=laps,
        total_time=total_time,
        best_lap=0.0,
        cards=cards,
        hand=best_hand(cards),
        dnf=False,
    )


def _placed(results: tuple[EntryResult, ...]) -> tuple[Placed, ...]:
    """Wrap *results* as placed rows, one place apart."""
    return tuple(
        Placed(place=i + 1, result=r, tie_note=None, draw_required=False)
        for i, r in enumerate(results)
    )


def _export_inputs(context: app_module._RouteContext) -> tuple[object, object, object]:
    """Capture (config, placed-groups, opts) as the handler now does.

    ``_placed_for_export`` returns Phase 3's ``(teams, solo)`` pair --
    each kind ranked from 1 -- which the tests unpack into the two
    ``_write_export`` arguments. G6: the options come from the route
    context's own settings (the Results menu's publish flags), so the
    context is the argument.
    """
    engine = context.presenter.engine
    return (
        engine.config,
        app_module._placed_for_export(context),
        app_module._export_options(context),
    )


def _unpack_groups(groups: object) -> tuple[object, object]:
    """Split the (teams, solo) export groups into two arguments."""
    teams, solo = groups  # type: ignore[misc]
    return teams, solo


def _context(*, engine: _StubEngine | None, menubar: object = None) -> app_module._RouteContext:
    """Build a route context with an optional engine and menubar.

    The roster is empty rather than None: the export handler's Phase 7
    rider tally walks it (``_unique_rider_count``), and a test that
    cares about the count assigns its own roster.
    """
    return app_module._RouteContext(
        frame=_StubFrame(menubar),
        resource=None,
        roster=Roster(),
        app=None,
        theme_controller=None,  # type: ignore[arg-type]
        presenter=None if engine is None else _presenter(engine),
    )


def _presenter(engine: _StubEngine) -> object:
    """Expose *engine* through a stub console presenter."""

    class _Presenter:
        def __init__(self, engine: _StubEngine) -> None:
            """Store the engine."""
            self.engine = engine

    return _Presenter(engine)


# --- G6: the publish options now come from the persisted settings ---

PUBLISH_SETTINGS_CASES = (
    (
        {
            "publish_show_times": False,
            "publish_laps_board": True,
            "publish_time_board": False,
            "publish_full_field": True,
            "publish_all_cards": True,
        },
        ExportOptions(),
    ),
    (
        {
            "publish_show_times": True,
            "publish_laps_board": False,
            "publish_time_board": True,
            "publish_full_field": False,
            "publish_all_cards": False,
        },
        ExportOptions(
            show_times=True, laps_board=False, time_board=True, full_field=False, all_cards=False
        ),
    ),
)
PUBLISH_SETTINGS_IDS = ("canvas_defaults", "all_flipped")


@pytest.mark.parametrize(("stored", "expected"), PUBLISH_SETTINGS_CASES, ids=PUBLISH_SETTINGS_IDS)
def test_export_options_given_stored_publish_flags_maps_them_onto_the_writer(
    stored: dict[str, bool], expected: ExportOptions
) -> None:
    """G6: each publish_* flag reaches its ExportOptions field."""
    context = _context(engine=_StubEngine(_snapshot()))
    context.settings = replace(default_settings(), **stored)

    options = app_module._export_options(context)

    assert options == expected


def test_export_options_given_no_stored_flags_returns_the_settings_defaults() -> None:
    """G6: a fresh context exports the persisted defaults, not zeros."""
    context = _context(engine=_StubEngine(_snapshot()))

    options = app_module._export_options(context)

    assert options == ExportOptions()


class _RecordingMenuItem:
    """A menu item that records the last ``Enable`` verdict."""

    def __init__(self) -> None:
        """Start with no recorded verdict."""
        self.enabled: bool | None = None

    def Enable(self, enabled: bool) -> None:  # noqa: N802, FBT001 -- wx API name
        """Record *enabled* as the item's verdict."""
        self.enabled = enabled


class _RecordingMenuBar:
    """A menubar carrying exactly the observed frozen item names."""

    def __init__(self, names: tuple[str, ...]) -> None:
        """Build one recording item per frozen *names* entry."""
        import wx.xrc  # noqa: PLC0415 -- the real id space apply_to_menubar walks

        self._names = {wx.xrc.XRCID(name): name for name in names}
        self.items = {name: _RecordingMenuItem() for name in names}

    def FindItem(  # noqa: N802 -- wx API name
        self, real_id: int
    ) -> tuple[_RecordingMenuItem | None, None]:
        """Return the item for *real_id*, or a miss."""
        name = self._names.get(real_id)
        return (None, None) if name is None else (self.items[name], None)


# Part D: the three per-format preview rows the export-completion menu
# refresh enables; the recording menubar walks the real xrcids.
PREVIEW_MENU_IDS = (
    "mi_preview_html_browser",
    "mi_preview_pdf_browser",
    "mi_preview_poster_html_browser",
)


def _gating_engine(*, state: RideStatus = RideStatus.FINISHED) -> _StubEngine:
    """Build an engine with the live state the menu refresh reads."""
    return _StubEngine(_snapshot(), state=state)


def _snapshot() -> tuple[EntryResult, ...]:
    """Build a two-entry field: a quad of nines and a royal flush."""
    return (
        _result("88", "9S 9D 9C 9H 2C", laps=4, total_time=1_000.0),
        _result("7", "AS KS QS JS 10S", laps=5, total_time=1_200.0),
    )


def test_ride_slug_slugifies_and_never_empty() -> None:
    """Slugs are lowercase with ``-`` for non-alnum, never blank."""
    slug = app_module._ride_slug("GORBA EPIC & MTB Festival 2026")
    assert slug == "gorba-epic-mtb-festival-2026"
    assert app_module._ride_slug("!!!") == "results"


@pytest.mark.parametrize(
    "target",
    [
        "export_html",
        "export_pdf",
        "export_poster",
        "export_poster_html",
        "export_results_csv",
        "preview_html_browser",
        "preview_pdf_browser",
        "preview_poster_html_browser",
    ],
)
def test_target_actions_cover_every_results_export_row(target: str) -> None:
    """Every export target + every preview resolves to a handler."""
    assert target in app_module._TARGET_ACTIONS


def test_target_actions_route_each_preview_to_its_own_handler() -> None:
    """Part D: each preview opens its own format's recorded path."""
    assert (
        app_module._TARGET_ACTIONS["preview_html_browser"]
        is app_module._handle_preview_html_browser
    )
    assert (
        app_module._TARGET_ACTIONS["preview_pdf_browser"] is app_module._handle_preview_pdf_browser
    )
    assert (
        app_module._TARGET_ACTIONS["preview_poster_html_browser"]
        is app_module._handle_preview_poster_html_browser
    )


# Part D/h: the poster-HTML export registers like its three siblings --
# a suggested filename, its own recorded preview path, and a target
# action built from the name table (no hand-written dispatch entry).
def test_export_suggested_names_given_the_poster_html_row_names_the_file() -> None:
    """The row's OS-native picker suggests ``{slug}-podium.html``."""
    assert app_module._EXPORT_SUGGESTED_NAMES["export_poster_html"] == "{slug}-podium.html"


def test_export_path_fields_given_the_poster_html_row_records_its_own_path() -> None:
    """The poster page owns a preview field, never the PDF's."""
    assert app_module._EXPORT_PATH_FIELDS["export_poster_html"] == "poster_html_export_path"


def test_target_actions_given_the_poster_html_row_writes_the_poster_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The poster-HTML target dispatches to the real export command."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "podium.html"
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: out)
    _inline_offloop(monkeypatch)

    app_module._TARGET_ACTIONS["export_poster_html"](context)

    assert "Best poker hands" in out.read_text(encoding="utf-8")
    assert context.poster_html_export_path == out


def test_write_export_html_writes_a_self_contained_page(tmp_path: Path) -> None:
    """The HTML export writes a page naming the ride."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "results.html"

    config, groups, opts = _export_inputs(context)
    teams, solo = _unpack_groups(groups)
    app_module._write_export(config, teams, solo, opts, "export_html", out)

    text = out.read_text(encoding="utf-8")
    assert "Test Poker Run" in text
    assert "race-data" in text


def test_write_export_pdf_writes_a_readable_pdf(tmp_path: Path) -> None:
    """The PDF export writes a readable multi-section report."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "results.pdf"

    config, groups, opts = _export_inputs(context)
    teams, solo = _unpack_groups(groups)
    app_module._write_export(config, teams, solo, opts, "export_pdf", out)

    assert len(PdfReader(str(out)).pages) >= 1


def test_write_export_poster_writes_one_page(tmp_path: Path) -> None:
    """The podium poster is a single celebratory page."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "podium.pdf"

    config, groups, opts = _export_inputs(context)
    teams, solo = _unpack_groups(groups)
    app_module._write_export(config, teams, solo, opts, "export_poster", out)

    assert len(PdfReader(str(out)).pages) == 1


def test_write_export_poster_html_writes_the_podium_page(tmp_path: Path) -> None:
    """The poster-HTML export writes the shared model's podium page."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "podium.html"

    config, groups, opts = _export_inputs(context)
    teams, solo = _unpack_groups(groups)
    app_module._write_export(config, teams, solo, opts, "export_poster_html", out)

    text = out.read_text(encoding="utf-8")
    assert "Best poker hands" in text
    assert ">#88 Rider</div>" in text
    assert "Four of a Kind — Nines" in text


def _pdf_text(path: Path) -> str:
    """Extract a PDF's every-page text, joined with newlines."""
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def test_write_export_poster_given_an_unverified_ride_writes_the_note(
    tmp_path: Path,
) -> None:
    """E6.4.3: the handler's flag reaches the PDF poster's note seam."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "podium.pdf"

    config, groups, opts = _export_inputs(context)
    teams, solo = _unpack_groups(groups)
    app_module._write_export(
        config, teams, solo, opts, "export_poster", out, self_test_unverified=True
    )

    assert "Self-test unverified" in _pdf_text(out)


def test_write_export_poster_given_a_verified_ride_writes_no_note(tmp_path: Path) -> None:
    """T-3 negative: the default poster export carries no such note."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "podium.pdf"

    config, groups, opts = _export_inputs(context)
    teams, solo = _unpack_groups(groups)
    app_module._write_export(config, teams, solo, opts, "export_poster", out)

    assert "Self-test unverified" not in _pdf_text(out)


def test_write_export_poster_html_given_an_unverified_ride_writes_the_note(
    tmp_path: Path,
) -> None:
    """E6.4.3: the flag reaches the poster page's note seam."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "podium.html"

    config, groups, opts = _export_inputs(context)
    teams, solo = _unpack_groups(groups)
    app_module._write_export(
        config, teams, solo, opts, "export_poster_html", out, self_test_unverified=True
    )

    assert "Self-test unverified" in out.read_text(encoding="utf-8")


def test_write_export_poster_html_given_a_verified_ride_writes_no_note(
    tmp_path: Path,
) -> None:
    """T-3 negative: the default poster page carries no such note."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "podium.html"

    config, groups, opts = _export_inputs(context)
    teams, solo = _unpack_groups(groups)
    app_module._write_export(config, teams, solo, opts, "export_poster_html", out)

    assert "Self-test unverified" not in out.read_text(encoding="utf-8")


def test_write_export_csv_writes_the_s15_header(tmp_path: Path) -> None:
    """The standings CSV carries the spec §15 header (type and sex)."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "standings.csv"

    config, groups, opts = _export_inputs(context)
    teams, solo = _unpack_groups(groups)
    app_module._write_export(config, teams, solo, opts, "export_results_csv", out)

    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "place,plate,entry,type,sex,laps,hand,draw"
    assert len(lines) == 3  # header + two rows


def test_write_export_html_given_an_unverified_ride_writes_the_note(
    tmp_path: Path,
) -> None:
    """E6.4.3: the handler's flag reaches the rendered page."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "results.html"

    config, groups, opts = _export_inputs(context)
    teams, solo = _unpack_groups(groups)
    app_module._write_export(
        config, teams, solo, opts, "export_html", out, self_test_unverified=True
    )

    assert "Self-test unverified" in out.read_text(encoding="utf-8")


def test_write_export_html_given_a_verified_ride_writes_no_note(tmp_path: Path) -> None:
    """T-3 negative: the default export carries no such note."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "results.html"

    config, groups, opts = _export_inputs(context)
    teams, solo = _unpack_groups(groups)
    app_module._write_export(config, teams, solo, opts, "export_html", out)

    assert "Self-test unverified" not in out.read_text(encoding="utf-8")


def test_write_export_html_writes_an_empty_field_page(tmp_path: Path) -> None:
    """A 0-entry field still renders a valid, self-contained page."""
    context = _context(engine=_StubEngine(()))
    out = tmp_path / "results.html"

    config, groups, opts = _export_inputs(context)
    teams, solo = _unpack_groups(groups)
    app_module._write_export(config, teams, solo, opts, "export_html", out)

    assert "race-data" in out.read_text(encoding="utf-8")


def test_handle_export_command_picks_writes_and_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The handler picks a path, writes off-loop, and records it."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "results.html"
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: out)

    def sync_offloop(  # noqa: PLR0913 -- mirrors _run_export_offloop's inputs
        ctx: app_module._RouteContext,
        target: str,
        path: Path,
        *,
        config: object,
        teams: object,
        solo: object,
        opts: object,
        watermark: int,
        riders: int = 0,
        team_logos: object = None,
        self_test_unverified: bool = False,
    ) -> None:
        app_module._write_export(  # type: ignore[arg-type]
            config,
            teams,
            solo,
            opts,
            target,
            path,
            riders,
            team_logos=team_logos,
            self_test_unverified=self_test_unverified,
        )
        app_module._record_export_completion(ctx, target, path, watermark)

    monkeypatch.setattr(app_module, "_run_export_offloop", sync_offloop)

    app_module._handle_export_command(context, "export_html")

    assert out.exists()
    assert (context.html_export_path, context.pdf_export_path) == (out, None)
    assert context.export_watermark == 0
    # The off-loop notice is async; the sync seam posts nothing
    assert context.frame.notices == []


def test_handle_export_command_captures_the_self_test_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E6.4.3: the handler reads the engine's flag on the main thread.

    The off-loop writer never touches the live context (R-02), so the
    unverified flag is captured here alongside the config, groups and
    options, exactly like one of them.
    """
    context = _context(engine=_StubEngine(_snapshot(), self_test_unverified=True))
    out = tmp_path / "results.html"
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: out)
    captured: list[bool] = []

    def sync_offloop(  # noqa: PLR0913 -- mirrors _run_export_offloop's inputs
        _ctx: app_module._RouteContext,
        _target: str,
        _path: Path,
        *,
        config: object,  # noqa: ARG001 -- mirrors the frozen signature
        teams: object,  # noqa: ARG001 -- mirrors the frozen signature
        solo: object,  # noqa: ARG001 -- mirrors the frozen signature
        opts: object,  # noqa: ARG001 -- mirrors the frozen signature
        watermark: int,  # noqa: ARG001 -- mirrors the frozen signature
        riders: int = 0,  # noqa: ARG001 -- mirrors the frozen signature
        team_logos: object = None,  # noqa: ARG001 -- mirrors the frozen signature
        self_test_unverified: bool = False,
    ) -> None:
        captured.append(self_test_unverified)

    monkeypatch.setattr(app_module, "_run_export_offloop", sync_offloop)

    app_module._handle_export_command(context, "export_html")

    assert captured == [True]


def test_handle_export_command_advances_the_export_watermark_to_the_event_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The watermark a fresh export records is the engine's event count.

    E7.3.2: ``_handle_export_command`` captures ``len(engine.events)``
    at snapshot time and the off-loop completion stores it on the route
    context, so a later correction (a new event past that count) makes
    the results window render the stale banner.
    """
    events = (object(), object(), object())  # three recorded events
    context = _context(engine=_StubEngine(_snapshot(), events=events))
    out = tmp_path / "results.html"
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: out)
    captured: list[int] = []

    def sync_offloop(  # noqa: PLR0913 -- mirrors _run_export_offloop's inputs
        ctx: app_module._RouteContext,
        target: str,
        path: Path,
        *,
        config: object,
        teams: object,
        solo: object,
        opts: object,
        watermark: int,
        riders: int = 0,
        team_logos: object = None,
        self_test_unverified: bool = False,
    ) -> None:
        app_module._write_export(  # type: ignore[arg-type]
            config,
            teams,
            solo,
            opts,
            target,
            path,
            riders,
            team_logos=team_logos,
            self_test_unverified=self_test_unverified,
        )
        app_module._record_export_completion(ctx, target, path, watermark)
        captured.append(watermark)

    monkeypatch.setattr(app_module, "_run_export_offloop", sync_offloop)

    app_module._handle_export_command(context, "export_html")

    assert captured == [3]
    assert (context.html_export_path, context.export_watermark) == (out, 3)


def test_handle_export_command_cancel_is_a_silent_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled picker writes nothing and posts no notice."""
    context = _context(engine=_StubEngine(_snapshot()))
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: None)

    app_module._handle_export_command(context, "export_html")

    assert not (tmp_path / "results.html").exists()
    assert context.frame.notices == []
    assert (context.html_export_path, context.pdf_export_path) == (None, None)


def test_handle_export_command_without_engine_notices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ride threaded posts a notice instead of opening a picker."""
    context = _context(engine=None)
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: Path("/x.html"))

    app_module._handle_export_command(context, "export_html")

    assert context.frame.notices == ["No ride to export"]


def test_handle_export_command_given_the_csv_target_records_no_preview_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part D: a standings CSV export records neither preview path.

    Deliberate behavior change: the old single path could open a .csv
    from the Preview row, so CSV completions now advance the watermark
    only and both Preview items stay disabled.
    """
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "standings.csv"
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: out)
    _inline_offloop(monkeypatch)

    app_module._handle_export_command(context, "export_results_csv")

    assert (context.html_export_path, context.pdf_export_path) == (None, None)
    assert context.export_watermark == 0


# --- the unique-rider count and the DNS display on the export path ---


def _roster_of(*plates: str) -> Roster:
    """Build a MIXED solo roster keyed by each entry's own plate.

    The stub snapshot names every result by its plate (``_result``'s
    ``entry_id``), so the roster's stable keys are aligned with it --
    exactly what the real engine does when it files a crossing under
    the entry's key.
    """
    roster = Roster(entry_mode=EntryMode.SOLO, plate_model=PlateModel.RIDER_POOLED)
    for plate in plates:
        entry = roster.create_solo_entry(first_name="Rider", last_name=plate, plate=plate)
        entry.key = plate
    return roster


def _team_roster(*, key: str) -> Roster:
    """Build one two-rider team whose key is the snapshot's own."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    entry = roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[Rider(first_name="A.", plate="77"), Rider(first_name="K.", plate="78")],
    )
    entry.key = key
    return roster


def _finished_engine_with_a_zero_lap_entry() -> _StubEngine:
    """Build a FINISHED stub engine: a 4-lap entry and a 0-lap one."""
    return _StubEngine(
        (
            _result("88", "9S 9D 9C 9H 2C", laps=4, total_time=1_000.0),
            _result("7", "AS KS QS JS 10S", laps=0, total_time=1_200.0),
        )
    )


def test_handle_export_command_counts_only_the_riders_who_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The header's unique-rider count excludes the DNS entries' riders.

    ``riders`` counts individual people, so it comes from the roster --
    never from the placed rows, whose entry count is a count of entries
    (a two-rider team is one entry and two riders) -- and only the
    riders on an entry with at least one recorded lap.
    """
    context = _context(engine=_StubEngine(_snapshot()))
    context.roster = _roster_of("88", "7", "12")
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: tmp_path / "results.html")
    captured: list[int] = []
    _capture_offloop(monkeypatch, captured)

    app_module._handle_export_command(context, "export_html")

    assert captured == [2]


def test_handle_export_command_counts_every_member_of_a_started_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A team member counts individually: riders, not entries."""
    context = _context(engine=_StubEngine(_snapshot()))
    context.roster = _team_roster(key="88")
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: tmp_path / "results.html")
    captured: list[int] = []
    _capture_offloop(monkeypatch, captured)

    app_module._handle_export_command(context, "export_html")

    assert captured == [2]


def test_handle_export_command_given_only_dns_entries_counts_no_riders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-4 boundary: an all-DNS field contributes zero unique riders."""
    context = _context(
        engine=_StubEngine((_result("12", "9S 9D 9C 9H 2C", laps=0, total_time=0.0),))
    )
    context.roster = _roster_of("12")
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: tmp_path / "results.html")
    captured: list[int] = []
    _capture_offloop(monkeypatch, captured)

    app_module._handle_export_command(context, "export_html")

    assert captured == [0]


def _capture_offloop(monkeypatch: pytest.MonkeyPatch, captured: list[int]) -> None:
    """Patch ``_run_export_offloop`` with a rider-count recorder."""

    def record(  # noqa: PLR0913 -- mirrors _run_export_offloop's signature
        context: object,  # noqa: ARG001 -- mirrors the frozen signature
        target: object,  # noqa: ARG001 -- mirrors the frozen signature
        path: object,  # noqa: ARG001 -- mirrors the frozen signature
        *,
        config: object,  # noqa: ARG001 -- mirrors the frozen signature
        teams: object,  # noqa: ARG001 -- mirrors the frozen signature
        solo: object,  # noqa: ARG001 -- mirrors the frozen signature
        opts: object,  # noqa: ARG001 -- mirrors the frozen signature
        watermark: object,  # noqa: ARG001 -- mirrors the frozen signature
        riders: int,
        team_logos: object = None,  # noqa: ARG001 -- mirrors the frozen signature
        self_test_unverified: bool = False,  # noqa: ARG001 -- mirrors it
    ) -> None:
        captured.append(riders)

    monkeypatch.setattr(app_module, "_run_export_offloop", record)


def test_write_export_given_a_rider_count_puts_it_on_the_html_header(
    tmp_path: Path,
) -> None:
    """The count threads through to the HTML writer."""
    teams, solo = _unpack_groups(app_module._placed_for_export(_context(engine=None)))

    app_module._write_export(
        _StubConfig(),
        teams,
        solo,
        ExportOptions(),
        "export_html",
        tmp_path / "results.html",
        riders=207,
    )

    assert "· 207" in (tmp_path / "results.html").read_text(encoding="utf-8")


def test_placed_for_export_given_dns_riders_on_keeps_the_dns_entry() -> None:
    """Checked: the 0-lap entry stays, unplaced and marked DNS."""
    context = _context(engine=_finished_engine_with_a_zero_lap_entry())

    teams, solo = app_module._placed_for_export(context)

    assert [(p.result.plate, p.place, p.dns) for p in (*teams, *solo)] == [
        ("88", 1, False),
        ("7", 0, True),
    ]


def test_placed_for_export_given_dns_riders_off_drops_the_dns_entry() -> None:
    """Unchecked: the same export hides the 0-lap entry entirely."""
    context = _context(engine=_finished_engine_with_a_zero_lap_entry())
    context.settings = replace(default_settings(), show_dns_riders=False)

    teams, solo = app_module._placed_for_export(context)

    assert [(p.result.plate, p.dns) for p in (*teams, *solo)] == [("88", False)]


def test_write_export_given_a_dns_entry_renders_the_dns_cells(
    tmp_path: Path,
) -> None:
    """The Results-menu toggle's own reading reaches the page.

    Window and exports read the same ``AppSettings.show_dns_riders``
    through ``_placed_for_export``, so the two can never disagree: the
    checked default renders the DNS row's Laps cell as the word "DNS".
    """
    context = _context(engine=_finished_engine_with_a_zero_lap_entry())

    teams, solo = _unpack_groups(app_module._placed_for_export(context))
    app_module._write_export(
        _StubConfig(), teams, solo, ExportOptions(), "export_html", tmp_path / "results.html"
    )

    assert ">DNS</td>" in (tmp_path / "results.html").read_text(encoding="utf-8")


def test_write_export_given_dns_riders_off_omits_the_dns_entry(
    tmp_path: Path,
) -> None:
    """Unchecked: the dropped 0-lap entry never reaches the page.

    The embedded record is read rather than a raw plate search: the
    page's inlined CSS carries ``#7...`` colour literals, so the plate
    as a bare substring would match those.
    """
    context = _context(engine=_finished_engine_with_a_zero_lap_entry())
    context.settings = replace(default_settings(), show_dns_riders=False)

    teams, solo = _unpack_groups(app_module._placed_for_export(context))
    app_module._write_export(
        _StubConfig(), teams, solo, ExportOptions(), "export_html", tmp_path / "results.html"
    )

    html = (tmp_path / "results.html").read_text(encoding="utf-8")
    assert '"plate": 88,' in html
    assert '"plate": 7,' not in html
    assert ">DNS</td>" not in html


@pytest.mark.parametrize(
    "state",
    [RideStatus.DRAFT, RideStatus.RUNNING, RideStatus.REOPENED],
    ids=lambda state: state.value,
)
def test_placed_for_export_given_a_live_engine_keeps_zero_lap_entries_ranked(
    state: RideStatus,
) -> None:
    """Phase 7: the DNS filter is FINISHED-only.

    The Results menu only offers its exports once the ride is FINISHED,
    but this helper is the ranking seam itself: a route-level or future
    direct caller must never turn a live ride's 0-lap entry into an
    unplaced DNS row (``EngineDataSource.standings`` gates the same
    way, so window and export can never disagree).
    """
    context = _context(engine=_finished_engine_with_a_zero_lap_entry())
    context.presenter.engine.state = state

    teams, solo = app_module._placed_for_export(context)

    assert [(p.result.plate, p.place, p.dns) for p in (*teams, *solo)] == [
        ("7", 1, False),
        ("88", 2, False),
    ]


def test_handle_preview_html_browser_opens_the_html_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part D: Preview HTML opens the recorded HTML path."""
    context = _context(engine=None)
    context.html_export_path = tmp_path / "results.html"
    opened: list[Path] = []
    monkeypatch.setattr(app_module, "_open_in_browser", opened.append)

    app_module._handle_preview_html_browser(context)

    assert opened == [tmp_path / "results.html"]
    assert context.frame.notices == ["Opened results.html"]


def test_handle_preview_pdf_browser_opens_the_pdf_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part D: Preview PDF opens the recorded PDF path via the seam."""
    context = _context(engine=None)
    context.pdf_export_path = tmp_path / "results.pdf"
    opened: list[Path] = []
    monkeypatch.setattr(app_module, "_open_in_browser", opened.append)

    app_module._handle_preview_pdf_browser(context)

    assert opened == [tmp_path / "results.pdf"]
    assert context.frame.notices == ["Opened results.pdf"]


def test_handle_preview_poster_html_browser_opens_the_poster_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The poster preview opens the poster page's recorded path."""
    context = _context(engine=None)
    context.poster_html_export_path = tmp_path / "podium.html"
    opened: list[Path] = []
    monkeypatch.setattr(app_module, "_open_in_browser", opened.append)

    app_module._handle_preview_poster_html_browser(context)

    assert opened == [tmp_path / "podium.html"]
    assert context.frame.notices == ["Opened podium.html"]


def test_handle_preview_poster_html_browser_without_export_notices() -> None:
    """No poster export yet posts a notice and opens nothing."""
    context = _context(engine=None)

    app_module._handle_preview_poster_html_browser(context)

    assert context.frame.notices == ["No export yet — generate one first"]


def test_handle_preview_poster_html_browser_ignores_the_other_formats_exports(
    tmp_path: Path,
) -> None:
    """The HTML and PDF exports never satisfy the poster preview row."""
    context = _context(engine=None)
    context.html_export_path = tmp_path / "results.html"
    context.pdf_export_path = tmp_path / "results.pdf"

    app_module._handle_preview_poster_html_browser(context)

    assert context.frame.notices == ["No export yet — generate one first"]


def test_handle_preview_html_browser_without_export_notices() -> None:
    """No HTML export yet posts a notice and opens nothing."""
    context = _context(engine=None)

    app_module._handle_preview_html_browser(context)

    assert context.frame.notices == ["No export yet — generate one first"]


def test_handle_preview_pdf_browser_without_export_notices() -> None:
    """No PDF export yet posts a notice and opens nothing."""
    context = _context(engine=None)

    app_module._handle_preview_pdf_browser(context)

    assert context.frame.notices == ["No export yet — generate one first"]


# ============================================================ W8
# Team logos in HTML exports: _write_export forwards the plate -> logo
# data-URI map into htmlexport.render, and _team_logo_srcs builds that
# map from the roster's TEAM entries (card logos only -- the stored
# image column went with A7's team-logo rework).


def _team_logo_uri() -> str:
    """Return a tiny deterministic data URI for the export pins."""
    return "data:image/png;base64,TEAMLOGO"


def test_write_export_html_embeds_team_logos_when_supplied(tmp_path: Path) -> None:
    """W8: the html writer passes team_logos into the renderer."""
    out = tmp_path / "results.html"
    config = _StubConfig()
    (entry, _) = _snapshot()
    team_result = EntryResult(
        entry_id="88",
        plate="88",
        name="Moss Ridge Riders",
        kind="team",
        laps=entry.laps,
        total_time=entry.total_time,
        best_lap=entry.best_lap,
        cards=entry.cards,
        hand=entry.hand,
        dnf=False,
    )
    placed = _placed((team_result,))
    opts = app_module.ExportOptions()

    app_module._write_export(
        config,
        placed,
        (),
        opts,
        "export_html",
        out,
        team_logos={"88": _team_logo_uri()},
    )

    html = out.read_text(encoding="utf-8")
    # Prefix match: the compact team rows carry Tailwind utilities in
    # the class attribute, so the exact-value form would pin markup
    # rather than the two logo images W8 owns.
    assert html.count('class="team-logo') == 2
    assert html.count('<img src="data:image/png;base64,TEAMLOGO"') == 2


def test_write_export_html_without_team_logos_renders_no_logo_images(tmp_path: Path) -> None:
    """W8: no map, no row images -- the plain page."""
    out = tmp_path / "results.html"
    config = _StubConfig()
    app_module._write_export(config, (), (), app_module.ExportOptions(), "export_html", out)

    assert 'class="team-logo"' not in out.read_text(encoding="utf-8")


def test_team_logo_srcs_maps_card_logo_teams_to_their_asset_data_uri() -> None:
    """W8/A7: a card-code team resolves to its packaged 2x asset."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    card_team = roster.create_empty_team(display_name="Card Team", logo_card="AS")
    asset_png = (cards_dir() / asset_filename(asset_key("AS"), SCALE_2X)).read_bytes()

    srcs = app_module._team_logo_srcs(roster)

    expected = {card_team.plate: "data:image/png;base64," + b64encode(asset_png).decode("ascii")}
    assert srcs == expected


def test_team_logo_srcs_omits_logo_less_and_solo_entries() -> None:
    """W8: rows without a logo, and non-team entries, never map."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_empty_team(display_name="Plain Team")
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="2")

    srcs = app_module._team_logo_srcs(roster)

    assert srcs == {}


def test_team_logo_srcs_without_a_roster_maps_no_logos() -> None:
    """W8: no ride threaded -- the map is empty, never an error."""
    srcs = app_module._team_logo_srcs(None)

    assert srcs == {}


def test_team_logo_srcs_omits_a_card_code_with_no_asset_behind_it() -> None:
    """W8: an unknown card code is skipped, never raised."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_empty_team(display_name="Odd Team", logo_card="ZZ")

    srcs = app_module._team_logo_srcs(roster)

    assert srcs == {}


# ============================================================ E2/F
# Ride ▸ Finish Ride… (and the quit flow's Finish First) publishes
# nothing: the finish-time auto-export into a per-user ``exports``
# directory was removed, so every results file comes from the
# operator's own Results-menu request.


class _FinishPresenter:
    """A presenter double whose on_finish leaves the staged state."""

    def __init__(self, engine: object) -> None:
        """Hold the engine the finish route reads and finishes."""
        self.engine = engine
        self.finish_calls = 0

    def on_finish(self, _report: object = None) -> None:
        """Record the finish request; the test stages the state."""
        self.finish_calls += 1


def _export_roster() -> Roster:
    """Build the one-entry roster the export inputs read."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="12")
    return roster


def _finish_context(*, engine: object) -> app_module._RouteContext:
    """Build a route context whose presenter exposes *engine*."""
    return app_module._RouteContext(
        frame=_StubFrame(),
        resource=None,
        roster=_export_roster(),
        app=None,
        theme_controller=None,
        presenter=_FinishPresenter(engine),  # type: ignore[arg-type]
    )


def _stub_confirmed_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the native finish confirm with OK (T-10: wx boundary)."""
    import wx  # noqa: PLC0415 -- only the confirm's id needs it

    monkeypatch.setattr(std_dialogs, "show_danger", lambda *_a, **_k: wx.ID_OK)


def _sync_offloop(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Path]]:
    """Run the off-loop export inline; record each (target, path)."""
    written: list[tuple[str, Path]] = []

    def _write(  # noqa: PLR0913 -- mirrors _run_export_offloop's inputs
        context: app_module._RouteContext,
        target: str,
        path: Path,
        *,
        config: object,
        teams: object,
        solo: object,
        opts: object,
        watermark: int,
        team_logos: object = None,
    ) -> None:
        app_module._write_export(  # type: ignore[arg-type]
            config, teams, solo, opts, target, path, team_logos=team_logos
        )
        written.append((target, path))
        app_module._record_export_completion(context, target, path, watermark)

    monkeypatch.setattr(app_module, "_run_export_offloop", _write)
    return written


def test_handle_finish_route_given_a_finished_engine_exports_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F: a finish leaves every export to the operator's Results menu.

    Finishing no longer publishes anything on its own: no results file
    is written, no per-user ``exports`` directory is created, and both
    recorded export paths stay empty until the operator asks for an
    export from the Results menu.
    """
    engine, _source = app_module._build_console_engine(_export_roster())
    engine.start()
    engine.finish()
    context = _finish_context(engine=engine)
    _stub_confirmed_finish(monkeypatch)
    data_dir = tmp_path / "data"
    # raising=False: the finish path no longer reads user_data_dir at
    # all (the auto-export is gone), so this stub only keeps a failing
    # run hermetic instead of writing into the real per-user data dir.
    monkeypatch.setattr(app_module, "user_data_dir", lambda _appname: str(data_dir), raising=False)
    written = _sync_offloop(monkeypatch)

    app_module._handle_finish_route(context)

    assert engine.state is RideStatus.FINISHED
    assert written == []
    assert not (data_dir / "exports").exists()
    assert (context.html_export_path, context.pdf_export_path) == (None, None)


def test_handle_finish_route_given_a_running_engine_exports_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2: a refused finish leaves the engine RUNNING, writing nothing.

    No FINISHED transition means no results state either -- and with
    the finish-time auto-export removed, no finish writes a results
    file or creates the per-user exports directory.
    """
    engine, _source = app_module._build_console_engine(_export_roster())
    engine.start()
    context = _finish_context(engine=engine)
    _stub_confirmed_finish(monkeypatch)
    written = _sync_offloop(monkeypatch)

    app_module._handle_finish_route(context)

    assert engine.state is RideStatus.RUNNING
    assert written == []
    assert (context.html_export_path, context.pdf_export_path) == (None, None)
    assert not (tmp_path / "data" / "exports").exists()


class _InlineThread:
    """Run a worker target inline, for deterministic off-loop tests."""

    def __init__(self, *, target: Callable[[], None], **_wx_kwargs: object) -> None:
        """Hold *target*; wx's extra kwargs (daemon) are accepted."""
        self._target = target

    def start(self) -> None:
        """Run the worker body now, on the calling thread."""
        self._target()


class _ImmediateWx:
    """A wx seam that runs ``CallAfter`` inline and finds no window."""

    def CallAfter(  # noqa: N802 -- wx API name
        self, callable_: Callable[..., None], *args: object
    ) -> None:
        """Run one deferred call immediately."""
        callable_(*args)

    def GetApp(self) -> None:  # noqa: N802 -- wx API name
        """Report no running app, so the export options default."""

    def FindWindowByName(self, _name: str) -> None:  # noqa: N802 -- wx API name
        """Report no results window open."""


def _inline_offloop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the real off-loop writer inline (thread + wx seams)."""
    monkeypatch.setattr(app_module, "require_wx", _ImmediateWx)
    monkeypatch.setattr(app_module, "threading", SimpleNamespace(Thread=_InlineThread))


@pytest.mark.parametrize(
    ("target", "expected_html", "expected_pdf", "expected_poster_html"),
    [
        ("export_html", True, False, False),
        ("export_pdf", False, True, False),
        ("export_poster", False, True, False),
        ("export_poster_html", False, False, True),
        ("export_results_csv", False, False, False),
    ],
    ids=["html", "pdf", "poster", "poster_html", "csv"],
)
def test_run_export_offloop_completion_records_and_refreshes_per_format(  # noqa: PLR0913, PLR0917
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    expected_html: bool,  # noqa: FBT001 -- a parametrize row value, not a call-site flag
    expected_pdf: bool,  # noqa: FBT001 -- a parametrize row value, not a call-site flag
    expected_poster_html: bool,  # noqa: FBT001 -- a parametrize row value, not a call-site flag
) -> None:
    """Part D: each export records its path and refreshes the menu.

    The completion callback records the per-format path (the CSV target
    records none) and re-reads the menu state on the main thread, so
    the matching Preview row enables immediately and the others stay
    disabled. The full status x flag decision tables live in
    test_commands.py.
    """
    menubar = _RecordingMenuBar(PREVIEW_MENU_IDS)
    context = _context(engine=_gating_engine(), menubar=menubar)
    out = tmp_path / f"export-{target}.out"
    monkeypatch.setattr(app_module, "_write_export", lambda *_a, **_k: None)
    _inline_offloop(monkeypatch)

    app_module._run_export_offloop(
        context,
        target,
        out,
        config=_StubConfig(),
        teams=(),
        solo=(),
        opts=app_module.ExportOptions(),
        watermark=7,
    )

    assert (
        context.html_export_path,
        context.pdf_export_path,
        context.poster_html_export_path,
    ) == (
        out if expected_html else None,
        out if expected_pdf else None,
        out if expected_poster_html else None,
    )
    assert context.export_watermark == 7
    assert menubar.items[PREVIEW_MENU_IDS[0]].enabled is expected_html
    assert menubar.items[PREVIEW_MENU_IDS[1]].enabled is expected_pdf
    assert menubar.items[PREVIEW_MENU_IDS[2]].enabled is expected_poster_html
    assert context.frame.notices == [f"Exported {out.name}"]


def test_run_export_offloop_completion_refreshes_a_stale_menubar_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part D: the completion callback re-applies the live menu state.

    The HTML preview item starts enabled (stale), so only a real
    refresh can flip it after a PDF export -- and that refresh reads
    the path the same callback recorded.
    """
    menubar = _RecordingMenuBar(PREVIEW_MENU_IDS)
    menubar.items[PREVIEW_MENU_IDS[0]].enabled = True
    context = _context(engine=_gating_engine(), menubar=menubar)
    out = tmp_path / "results.pdf"
    monkeypatch.setattr(app_module, "_write_export", lambda *_a, **_k: None)
    _inline_offloop(monkeypatch)

    app_module._run_export_offloop(
        context,
        "export_pdf",
        out,
        config=_StubConfig(),
        teams=(),
        solo=(),
        opts=app_module.ExportOptions(),
        watermark=7,
    )

    assert menubar.items[PREVIEW_MENU_IDS[0]].enabled is False
    assert menubar.items[PREVIEW_MENU_IDS[1]].enabled is True


def test_run_export_offloop_given_a_failed_write_posts_the_failure_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2: a failed export notices; it records no path or watermark."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "results.html"

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(app_module, "_write_export", _boom)
    _inline_offloop(monkeypatch)

    app_module._run_export_offloop(
        context,
        "export_html",
        out,
        config=_StubConfig(),
        teams=(),
        solo=(),
        opts=app_module.ExportOptions(),
        watermark=7,
    )

    assert context.frame.notices == ["Export failed: disk full"]
    assert (context.html_export_path, context.pdf_export_path) == (None, None)
    assert context.export_watermark is None


def test_handle_export_command_records_the_html_path_for_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part D: the standalone HTML export records html_export_path."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "results.html"
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: out)
    _inline_offloop(monkeypatch)

    app_module._handle_export_command(context, "export_html")

    assert (context.html_export_path, context.pdf_export_path) == (out, None)
    assert context.export_watermark == 0


# ============================== Part D: menu gating on export
# The export-completion refresh must read the LIVE ride state on the
# main thread: a ride can be reopened (or replaced) while a worker is
# still rendering, and a status captured at dispatch time would wrongly
# re-enable the FINISHED-gated rows.


def test_apply_menu_state_given_a_fresh_context_after_restart_disables_preview() -> None:
    """Part D: the paths are in-memory, so a restart disables all."""
    menubar = _RecordingMenuBar(PREVIEW_MENU_IDS)
    context = _context(engine=_gating_engine(), menubar=menubar)

    app_module._apply_menu_state(context, RideStatus.FINISHED)

    assert menubar.items[PREVIEW_MENU_IDS[0]].enabled is False
    assert menubar.items[PREVIEW_MENU_IDS[1]].enabled is False
    assert menubar.items[PREVIEW_MENU_IDS[2]].enabled is False


def test_record_export_completion_given_a_reopened_ride_keeps_finished_rows_disabled() -> None:
    """Part D: a reopened ride records its path, enables no rows.

    The completion callback reads the engine's live state (REOPENED),
    so the FINISHED-gated rows stay disabled even though the export
    landed; the recorded path survives for the next finish.
    """
    menubar = _RecordingMenuBar(PREVIEW_MENU_IDS)
    context = _context(engine=_gating_engine(state=RideStatus.REOPENED), menubar=menubar)
    out = Path("/finished/results.html")

    app_module._record_export_completion(context, "export_html", out, 4)

    assert context.html_export_path == out
    assert menubar.items[PREVIEW_MENU_IDS[0]].enabled is False
    assert menubar.items[PREVIEW_MENU_IDS[1]].enabled is False
    assert menubar.items[PREVIEW_MENU_IDS[2]].enabled is False


def test_record_export_completion_given_the_poster_html_target_records_its_own_path() -> None:
    """Part D: the poster page owns the poster preview path alone."""
    context = _context(engine=_gating_engine())
    out = Path("/finished/podium.html")

    app_module._record_export_completion(context, "export_poster_html", out, 2)

    assert (context.poster_html_export_path, context.pdf_export_path) == (out, None)


def test_record_export_completion_without_a_presenter_applies_the_no_ride_state() -> None:
    """Part D: no presenter threaded -- the refresh uses DRAFT, no ride.

    The path still records; the no-ride state keeps the FINISHED-gated
    preview rows off (the console-less construction shape).
    """
    menubar = _RecordingMenuBar(PREVIEW_MENU_IDS)
    context = _context(engine=None, menubar=menubar)
    out = Path("/finished/results.html")

    app_module._record_export_completion(context, "export_html", out, 0)

    assert context.html_export_path == out
    assert menubar.items[PREVIEW_MENU_IDS[0]].enabled is False
    assert menubar.items[PREVIEW_MENU_IDS[2]].enabled is False
