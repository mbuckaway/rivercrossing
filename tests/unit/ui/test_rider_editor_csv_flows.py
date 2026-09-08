# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for ``rider_editor.run_csv_export_flow`` (E3.4, R-21).

The flow is the one place ``mi_export_csv`` (``ui.app``'s
``_handle_export_csv``) and ``rider_editor_dlg``'s own ``export_btn``
run the picker -> write flow through. Its write is the CSV export
boundary the functional suite drives through real windows
(``tests/functional/test_rider_editor.py``); what stays headless here
is the failure contract itself: a failed ``csvio.export`` (``OSError``
from an unwritable target) is caught inside the flow and surfaced
through the caller's ``on_error`` seam -- ``Export failed: {exc}`` --
instead of an unguarded raise into a wx handler that swallows it
(measured: ``docs/EPIC3-SESSION-SUMMARY.md``), which would leave the
operator believing the export succeeded. ``_pick_export_path`` is the
module-level picker seam (the native ``wx.FileDialog`` is never
drivable headless); no wx window is constructed in this file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rivercrossing.roster import Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui.views import rider_editor

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_run_csv_export_flow_given_a_writable_path_writes_and_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful export writes the file and reports no failure."""
    export_path = tmp_path / "export.csv"
    monkeypatch.setattr(rider_editor, "_pick_export_path", lambda _parent: export_path)
    failures: list[str] = []

    written = rider_editor.run_csv_export_flow(None, Roster(), on_error=failures.append)  # type: ignore[arg-type]

    assert written == export_path
    assert export_path.exists()
    assert failures == []


def test_run_csv_export_flow_given_a_cancelled_picker_reports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled save picker stays a silent no-op (E3.4)."""
    monkeypatch.setattr(rider_editor, "_pick_export_path", lambda _parent: None)
    failures: list[str] = []

    written = rider_editor.run_csv_export_flow(None, Roster(), on_error=failures.append)  # type: ignore[arg-type]

    assert written is None
    assert failures == []


def test_run_csv_export_flow_given_an_unwritable_path_posts_export_failed_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed write surfaces through on_error and returns None.

    The picker cannot veto a target that becomes unwritable after the
    dialog closes (permissions, a full disk, a path that is a
    directory), so ``csvio.export``'s ``OSError`` must reach the
    operator -- the caller posts it -- rather than escaping into a wx
    handler that swallows it.
    """
    export_path = tmp_path / "export.csv"
    monkeypatch.setattr(rider_editor, "_pick_export_path", lambda _parent: export_path)
    failures: list[str] = []

    def _export_that_fails(_ride: object, _path: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(rider_editor.csvio, "export", _export_that_fails)

    written = rider_editor.run_csv_export_flow(None, Roster(), on_error=failures.append)  # type: ignore[arg-type]

    assert written is None
    assert failures == ["Export failed: disk full"]
    assert export_path.exists() is False


# -------------------- ui.app's route handler threads its notice seam


class _StubFrame:
    """A minimal frame: status notices are captured, nothing else."""

    def __init__(self) -> None:
        """Start with no notices."""
        self.notices: list[str] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record *text* as the latest notice."""
        self.notices.append(text)


def test_handle_export_csv_surfaces_a_failed_write_on_the_status_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mi_export_csv posts the flow's ``Export failed`` text."""
    frame = _StubFrame()
    context = app_module._RouteContext(
        frame=frame,
        resource=None,
        roster=Roster(),
        app=None,
        theme_controller=None,
    )

    def _failing_flow(_parent: object, _roster: object, *, on_error: object) -> None:
        on_error("Export failed: disk full")  # type: ignore[operator]

    monkeypatch.setattr(rider_editor, "run_csv_export_flow", _failing_flow)

    app_module._handle_export_csv(context)

    assert frame.notices == ["Export failed: disk full"]


def test_handle_export_csv_posts_success_only_for_a_written_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful write posts ``Exported {name}`` only."""
    frame = _StubFrame()
    context = app_module._RouteContext(
        frame=frame,
        resource=None,
        roster=Roster(),
        app=None,
        theme_controller=None,
    )
    written = tmp_path / "riders.csv"

    def _ok_flow(_parent: object, _roster: object, *, on_error: object) -> object:  # noqa: ARG001 -- success path never reports a failure
        return written

    monkeypatch.setattr(rider_editor, "run_csv_export_flow", _ok_flow)

    app_module._handle_export_csv(context)

    assert frame.notices == ["Exported riders.csv"]
