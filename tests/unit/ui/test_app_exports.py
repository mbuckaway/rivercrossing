# SPDX-License-Identifier: GPL-3.0-only
"""Headless unit tests for the E6.4.2 results-export route handlers.

Drives ``app._write_export`` and the ``_TARGET_ACTIONS`` dispatch with
stub engines/contexts (no wx app constructed): each export target
writes a real file with the expected content, the picker/off-loop/
browser seams are monkeypatched, and the no-engine and cancel paths
post notices instead of failing.
"""

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from pypdf import PdfReader

if TYPE_CHECKING:
    import pytest

from rivercrossing.cards import Card
from rivercrossing.hands import best_hand
from rivercrossing.standings import EntryResult, Placed
from rivercrossing.ui import app as app_module


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


class _StubEngine:
    """The engine surface the export handlers read."""

    def __init__(
        self,
        snapshot: tuple[EntryResult, ...],
        *,
        events: tuple = (),
    ) -> None:
        """Store *snapshot* under a stub config and fixed event log."""
        self.config = _StubConfig()
        self._snapshot = snapshot
        self.events = events

    def snapshot(self) -> tuple[EntryResult, ...]:
        """Return the stored results."""
        return self._snapshot


class _StubFrame:
    """A minimal frame: status notices are captured, nothing else."""

    def __init__(self) -> None:
        """Start with no notices."""
        self.notices: list[str] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record *text* as the latest notice."""
        self.notices.append(text)


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


def _export_inputs(context: app_module._RouteContext) -> tuple[object, tuple, object]:
    """Capture (config, placed, opts) the way the handler now does."""
    engine = context.presenter.engine
    return (
        engine.config,
        app_module._placed_for_export(context),
        app_module._export_options(),
    )


def _context(*, engine: _StubEngine | None) -> app_module._RouteContext:
    """Build a route context with an optional live engine."""
    return app_module._RouteContext(
        frame=_StubFrame(),
        resource=None,
        roster=None,  # type: ignore[arg-type]
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


def _snapshot() -> tuple[EntryResult, ...]:
    """Build a two-entry field: a quad of nines and a royal flush."""
    return (
        _result("88", "9S 9D 9C 9H 2C", laps=4, total_time=1_000.0),
        _result("7", "AS KS QS JS TS", laps=5, total_time=1_200.0),
    )


def test_ride_slug_slugifies_and_never_empty() -> None:
    """Slugs are lowercase with ``-`` for non-alnum, never blank."""
    slug = app_module._ride_slug("GORBA EPIC & MTB Festival 2026")
    assert slug == "gorba-epic-mtb-festival-2026"
    assert app_module._ride_slug("!!!") == "results"


def test_target_actions_cover_every_results_export_row() -> None:
    """Every export target + preview + focus resolves to a handler."""
    for target in ("export_html", "export_pdf", "export_poster", "export_results_csv"):
        assert target in app_module._TARGET_ACTIONS
    assert "preview_in_browser" in app_module._TARGET_ACTIONS
    assert "focus_tiebreak_control" in app_module._TARGET_ACTIONS


def test_write_export_html_writes_a_self_contained_page(tmp_path: Path) -> None:
    """The HTML export writes a page naming the ride."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "results.html"

    config, placed, opts = _export_inputs(context)
    app_module._write_export(config, placed, opts, "export_html", out)

    text = out.read_text(encoding="utf-8")
    assert "Test Poker Run" in text
    assert "race-data" in text


def test_write_export_pdf_writes_a_readable_pdf(tmp_path: Path) -> None:
    """The PDF export writes a readable multi-section report."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "results.pdf"

    config, placed, opts = _export_inputs(context)
    app_module._write_export(config, placed, opts, "export_pdf", out)

    assert len(PdfReader(str(out)).pages) >= 1


def test_write_export_poster_writes_one_page(tmp_path: Path) -> None:
    """The podium poster is a single celebratory page."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "podium.pdf"

    config, placed, opts = _export_inputs(context)
    app_module._write_export(config, placed, opts, "export_poster", out)

    assert len(PdfReader(str(out)).pages) == 1


def test_write_export_csv_writes_the_s15_header(tmp_path: Path) -> None:
    """The standings CSV carries the spec §15 header."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "standings.csv"

    config, placed, opts = _export_inputs(context)
    app_module._write_export(config, placed, opts, "export_results_csv", out)

    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "place,plate,entry,laps,hand"
    assert len(lines) == 3  # header + two rows


def test_write_export_html_writes_an_empty_field_page(tmp_path: Path) -> None:
    """A 0-entry field still renders a valid, self-contained page."""
    context = _context(engine=_StubEngine(()))
    out = tmp_path / "results.html"

    config, placed, opts = _export_inputs(context)
    app_module._write_export(config, placed, opts, "export_html", out)

    assert "race-data" in out.read_text(encoding="utf-8")


def test_handle_export_command_picks_writes_and_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The handler picks a path, writes off-loop, and records it."""
    context = _context(engine=_StubEngine(_snapshot()))
    out = tmp_path / "results.html"
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: out)

    def sync_offloop(  # noqa: PLR0913 -- mirrors _run_export_offloop's inputs
        ctx: object,
        target: str,
        path: Path,
        *,
        config: object,
        placed: object,
        opts: object,
        watermark: int | None = None,
    ) -> None:
        app_module._write_export(config, placed, opts, target, path)  # type: ignore[arg-type]
        ctx.last_export_path = path  # type: ignore[attr-defined]
        ctx.export_watermark = watermark  # type: ignore[attr-defined]

    monkeypatch.setattr(app_module, "_run_export_offloop", sync_offloop)

    app_module._handle_export_command(context, "export_html")

    assert out.exists()
    assert context.last_export_path == out
    assert context.export_watermark == 0
    # the off-loop notice is async; the sync seam posts nothing
    assert context.frame.notices == []


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
    captured: list[object] = []

    def sync_offloop(  # noqa: PLR0913 -- mirrors _run_export_offloop's inputs
        ctx: object,
        _target: str,
        _path: Path,
        *,
        config: object,
        placed: object,
        opts: object,
        watermark: int | None = None,
    ) -> None:
        app_module._write_export(config, placed, opts, "export_html", out)  # type: ignore[arg-type]
        ctx.last_export_path = out  # type: ignore[attr-defined]
        ctx.export_watermark = watermark  # type: ignore[attr-defined]
        captured.append(watermark)

    monkeypatch.setattr(app_module, "_run_export_offloop", sync_offloop)

    app_module._handle_export_command(context, "export_html")

    assert captured == [3]
    assert context.export_watermark == 3


def test_handle_export_command_cancel_is_a_silent_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled picker writes nothing and posts no notice."""
    context = _context(engine=_StubEngine(_snapshot()))
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: None)

    app_module._handle_export_command(context, "export_html")

    assert not (tmp_path / "results.html").exists()
    assert context.frame.notices == []
    assert context.last_export_path is None


def test_handle_export_command_without_engine_notices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ride threaded posts a notice instead of opening a picker."""
    context = _context(engine=None)
    monkeypatch.setattr(app_module, "_pick_export_path", lambda _name: Path("/x.html"))

    app_module._handle_export_command(context, "export_html")

    assert context.frame.notices == ["No ride to export"]


def test_handle_preview_browser_opens_the_last_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preview opens the recorded export path via the seam."""
    context = _context(engine=None)
    context.last_export_path = tmp_path / "results.html"
    opened: list[Path] = []
    monkeypatch.setattr(app_module, "_open_in_browser", opened.append)

    app_module._handle_preview_browser(context)

    assert opened == [tmp_path / "results.html"]


def test_handle_preview_browser_without_export_notices() -> None:
    """No export yet posts a notice and opens nothing."""
    context = _context(engine=None)

    app_module._handle_preview_browser(context)

    assert context.frame.notices == ["No export yet — generate one first"]
