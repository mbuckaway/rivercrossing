# SPDX-License-Identifier: GPL-3.0-only
"""Functional: the Results ▸ export rows write real files (E6.4.2).

Extends the menu-coverage walk's reachability proof with a
side-effect proof: for each Results export row, patch the picker and
off-loop seams, fire the menu event at the real app frame, and assert
a real file landed with the expected content. Runs only in the Tart
VM (``pytestmark = functional``; AGENTS.md hard rule).
"""

import pathlib
from typing import Any

import harness
import pytest
import wx

from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids

pytestmark = pytest.mark.functional

EXPORT_ROWS = (
    ("mi_export_html", "results.html", "race-data"),
    ("mi_export_pdf", "results.pdf", None),
    ("mi_export_poster", "podium.pdf", None),
    ("mi_export_results_csv", "standings.csv", "place,plate,entry,laps,hand"),
)


@pytest.fixture
def firing_frame(wx_app: object) -> Any:  # noqa: ANN401 -- ordering only, see docstring
    """Build the one ``main_frame`` the export rows fire at.

    Same shape as ``test_app_open_target.firing_frame`` (which is
    module-local, not shared) -- a real ``build_main_window`` frame
    whose bound route handlers carry the live console engine. The
    teardown sets ``really_quitting`` first, the
    ``console_subprocess_scenarios`` pattern: a plain close on a
    fresh app would hit ``_on_main_frame_close``'s
    ``context.app.really_quitting`` guard before the quit flow ever
    set the attribute (measured crash, rerun-2 teardown).
    """
    frame = app_module.build_main_window(wx_app)
    try:
        yield frame
    finally:
        wx.GetApp().really_quitting = True
        harness.close_window(frame)


def _sync_offloop(context: object, target: str, path: object) -> None:
    """Run the export synchronously so the walk can assert the file."""
    app_module._write_export(context, target, path)  # type: ignore[arg-type]
    context.last_export_path = path  # type: ignore[attr-defined]


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
