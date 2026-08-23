# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for the File ▸ Import/Export Riders CSV routes (E3.4).

Split out of ``test_app_open_target.py`` (2026-08-23) so the heavy
CSV-preview / rider-editor E2E flows run in their own low-volume
worker process. Measured on macOS CI (PR #9, run 32554309607): the
combined file loaded ~15 dialogs in one process, and the rider-editor
build degraded (the documented class-2 XRC missing-subtree failure --
only ``riders_list``/``import_btn``/``export_btn``/``wxID_CLOSE``
built) after the csv-preview loads, so the editor route's handler
raised a swallowed ``LookupError`` and the monkeypatched ``run_dialog``
never ran. Keeping this file's per-process dialog-load volume low
(below the measured ~10-load degradation threshold) is the same
remedy the E3 close-out applied by splitting the heavy files across
``--dist loadfile`` workers: fewer builds per process, fewer chances
for the SIP wrapper-cache reuse that Addendum 2 documents.
"""

from pathlib import Path
from typing import Any

import harness
import pytest
import scenario_runner
import wx

from rivercrossing.ui import ids
from rivercrossing.ui.views import dialogs, rider_editor

pytestmark = pytest.mark.functional

# test_csvio.py's own fixture home (its module docstring) -- reused
# here rather than a tmp_path, so the E3.4 picker-seam tests below
# stay within pytest's own 3-argument budget (CODINGSTANDARDS-
# SIMPLECODE.md:154).
_CLEAN_POOLED_FIXTURE = (
    Path(__file__).resolve().parents[1] / "unit" / "fixtures" / "csv" / "clean_pooled.csv"
)


@pytest.fixture(scope="module")
def firing_frame(wx_app: object) -> Any:  # noqa: ANN401 -- ordering only, see docstring
    """Build the one ``main_frame`` every route-firing test shares."""
    from rivercrossing.ui import app as app_module  # noqa: PLC0415

    frame = app_module.build_main_window(wx_app)
    try:
        yield frame
    finally:
        harness.close_window(frame)


def _fire_menu_event(frame: Any, item_id: str) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Post a real ``EVT_MENU`` for *item_id* at *frame*, then settle.

    Delegates to :func:`harness.fire_menu_event`, the shared home that
    also clears ``sys.last_*`` after a swallowed handler exception
    (Phase 2 retention pin; ``harness.fire_menu_event``'s docstring).
    """
    harness.fire_menu_event(frame, item_id)


def test_mi_import_csv_given_a_cancelled_picker_opens_no_window(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """task-briefs.md's own "cancelled picker = no dialog" (E3.4)."""
    monkeypatch.setattr(rider_editor, "_pick_import_path", lambda _parent: None)
    before = len(wx.GetTopLevelWindows())

    _fire_menu_event(firing_frame, "mi_import_csv")

    assert len(wx.GetTopLevelWindows()) == before


def test_mi_import_csv_given_a_picked_path_shows_it_decorated(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Menu -> picker -> csv_preview_dlg opens decorated (E3.4)."""
    monkeypatch.setattr(rider_editor, "_pick_import_path", lambda _parent: _CLEAN_POOLED_FIXTURE)
    captured: dict[str, str] = {}

    def _capture_summary(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        captured["summary"] = harness.find_control(dialog, ids.SUMMARY_LBL).GetLabelText()
        return wx.ID_CANCEL

    monkeypatch.setattr(dialogs, "run_dialog", _capture_summary)

    _fire_menu_event(firing_frame, "mi_import_csv")

    assert captured["summary"] == "clean_pooled.csv → 9 riders · 2 teams · 0 conflicts"


def test_mi_import_csv_import_click_commits_into_the_shared_roster() -> None:
    """The committed roster is the same one rider_editor_dlg reads.

    Proven end to end through the app's own two routes, never a
    direct handle on ``_RouteContext.roster``: import clean_pooled.
    csv via ``mi_import_csv`` (clicking wxID_OK for real inside the
    monkeypatched ``run_dialog``), then open ``rider_editor_dlg`` via
    ``mi_rider_editor`` and read its own ``riders_list``.

    Runs as a subprocess scenario (``csv_import_commit_reads_editor``)
    because the two riders.xrc dialog loads in sequence trigger the
    documented SIP wrapper-cache degradation in a long-lived worker:
    measured on macOS CI (PR #9, runs 32554309607 / 32650668444) the
    second load built with the action staticbox missing, the editor
    route's handler raised a swallowed ``LookupError``, and the
    monkeypatched ``run_dialog`` never ran (``KeyError: 'plates'``) --
    failing all three file-level reruns. A fresh interpreter per
    attempt gets an independent memory layout (scenario_runner's own
    rationale), the one measured remedy for that corruption.
    """
    result = scenario_runner.run_scenario("csv_import_commit_reads_editor")

    assert {"1", "2", "3", "4", "10", "11", "12", "20", "21"} <= set(
        result["data"]["plates"]
    ), result["context"]


def test_mi_export_csv_given_a_cancelled_picker_is_a_silent_no_op(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled save picker changes nothing, silently (E3.4)."""
    monkeypatch.setattr(rider_editor, "_pick_export_path", lambda _parent: None)
    before = firing_frame.GetStatusBar().GetStatusText()

    _fire_menu_event(firing_frame, "mi_export_csv")

    assert firing_frame.GetStatusBar().GetStatusText() == before


def test_mi_export_csv_given_a_picked_path_writes_the_rosters_own_header(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Menu -> save picker -> csvio.export writes the real file."""
    export_path = tmp_path / "export.csv"
    monkeypatch.setattr(rider_editor, "_pick_export_path", lambda _parent: export_path)

    _fire_menu_event(firing_frame, "mi_export_csv")

    assert export_path.read_text(encoding="utf-8").splitlines()[0] == "plate,name,team_name,notes"
