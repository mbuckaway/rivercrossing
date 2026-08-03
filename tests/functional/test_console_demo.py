# SPDX-License-Identifier: GPL-3.0-only
"""Real-toolkit tests for the console's demo display (E1.5.1).

``MainFrame`` decorates ``main_frame`` (already loaded from
``main.xrc`` the same way every other window in this suite is,
``harness.load_window``) with the code-side bindings xrc-windows.md
section A's footnote assigns to it: the crossings feed's DataView
columns, rows and bold flagged-row attribute, the card imagelist,
the three ``wxInfoBar`` shells, ``main_splitter``'s sash restore, and
the hide-times column toggle (R-37). project-plan.md §7 names this
window's ``wxDataViewListCtrl``-family control the riskiest widget
in EPIC 1; the bold-flagged-row assertions below are the test that
retires that risk.

Everything here needs a live ``wx.App`` and the packaged card
bitmaps, so it lives in ``tests/functional/`` rather than
``tests/unit/`` (``cards_imagelist``'s own split is the precedent).

Read-only assertions share one module-scoped ``shared_console``
(see its own docstring): building a ``MainFrame`` decodes the 53-card
imagelist and appends 7 DataView columns, and reconstructing one per
test measurably raises this wxPython 4.3.1 / wxWidgets 3.3.3 build's
own address-reuse hazard (``MainFrame._find``'s docstring) at whole
-suite scale, where every functional module's own window churn adds
to the same process's tally.

The three tests that mutate ``main_frame`` state instead run their
whole scenario in a fresh, *spawned* interpreter each --
``console_subprocess_scenarios.py``, this module's own docstring --
never forked: forking a process that may already have an initialised
``NSApplication`` is unsafe on macOS, and this session's own
``wx_app`` fixture usually already has one. Measured (a throwaway
sampling script, per this repo's convention): even fully isolated,
that hazard still shows up at a real per-*spawn* rate for one of the
three, so :func:`_run_scenario` also retries the spawn itself, not
only relying on the child's own in-process retry.
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import harness
import pytest
import wx.dataview

from rivercrossing.ui import feed_model, ids
from rivercrossing.ui.views import MainFrame
from rivercrossing.ui.views.main_frame import (
    FINISHED_INFOBAR,
    MIN_SIZE,
    REOPENED_INFOBAR,
    RESUME_INFOBAR,
)

if TYPE_CHECKING:
    from rivercrossing.ui.presenters.data_source import FeedRow

pytestmark = pytest.mark.functional

# xrc-windows.md section A's feed table, newest first, transcribed
# independently of demo.py so a transcription mistake in either place
# is caught by the other disagreeing, not by this test checking
# demo.py against itself.
CANVAS_FEED_PLATES = ("123", "77", "45", "212", "8")
CANVAS_COUNTERS = ("1 124", "1 092", "42", "41/108")
INFOBAR_NAMES = (RESUME_INFOBAR, REOPENED_INFOBAR, FINISHED_INFOBAR)

SCENARIOS_SCRIPT = Path(__file__).resolve().parent / "console_subprocess_scenarios.py"
SCENARIO_TIMEOUT_SECONDS = 30
SCENARIO_SPAWN_ATTEMPTS = 3


@pytest.fixture(scope="module")
def shared_console(xrc_resource: object) -> MainFrame:
    """One ``MainFrame``, reused by every read-only assertion below.

    Nothing in this module's read-only tests mutates the feed,
    counters, InfoBars, columns or min size a fresh construction
    already sets once -- so one instance safely serves all of them
    (see the module docstring for why sharing matters here).
    """
    window = harness.load_window(xrc_resource, ids.MAIN_FRAME, frame=True)
    window.Show()
    window.Layout()
    harness.pump()
    console = MainFrame(window)
    try:
        yield console
    finally:
        harness.close_window(window)


def _spawn_scenario(name: str) -> subprocess.CompletedProcess[str]:
    """Spawn one fresh interpreter running scenario *name*.

    Always ``subprocess`` (spawn), never ``os.fork``: forking a
    process that may already have an initialised ``NSApplication``
    is unsafe on macOS, and this session's own ``wx_app`` fixture
    usually already has one.
    """
    return subprocess.run(  # noqa: S603 -- sys.executable + a fixed repo-local script path
        [sys.executable, str(SCENARIOS_SCRIPT), name],
        capture_output=True,
        text=True,
        timeout=SCENARIO_TIMEOUT_SECONDS,
        check=False,
    )


def _decode_scenario_output(
    name: str, completed: subprocess.CompletedProcess[str]
) -> dict[str, Any]:
    """Decode *completed*'s stdout into the scenario's JSON envelope.

    Always returns a dict carrying "ok"/"error"/"data"/"context" --
    even when stdout holds no parseable JSON at all -- so a failure
    message never needs a second code path for "the child produced
    nothing useful".
    """
    context = (
        f"scenario={name!r} returncode={completed.returncode}\n"
        f"--- child stdout ---\n{completed.stdout}\n"
        f"--- child stderr ---\n{completed.stderr}"
    )
    last_line = next(
        (line for line in reversed(completed.stdout.splitlines()) if line.strip()), ""
    )
    try:
        result = json.loads(last_line)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"no parseable JSON on stdout: {exc}",
            "data": None,
            "context": context,
        }
    result["context"] = context
    return result


def _run_scenario(name: str) -> dict[str, Any]:
    """Run scenario *name* in a fresh interpreter; decode its result.

    Retries the *spawn* itself, on top of
    ``console_subprocess_scenarios.py``'s own in-process retry of the
    sash sequence: measured, a whole process launch can rarely land
    on a memory layout where every one of its in-process attempts
    fails, and a fresh spawn gets an independent layout. Returns the
    first successful attempt's envelope, or the last attempt's
    (failing) one if every spawn failed.
    """
    result: dict[str, Any] = {"ok": False, "error": "no attempt ran", "data": None, "context": ""}
    for _attempt in range(SCENARIO_SPAWN_ATTEMPTS):
        try:
            completed = _spawn_scenario(name)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            result = {
                "ok": False,
                "error": f"child timed out after {SCENARIO_TIMEOUT_SECONDS}s",
                "data": None,
                "context": f"scenario={name!r}\nstdout={stdout}\nstderr={stderr}",
            }
            continue
        result = _decode_scenario_output(name, completed)
        if result["ok"]:
            return result
    return result


def _feed_plates(model: Any) -> tuple[str, ...]:  # noqa: ANN401 -- wx ships no stubs
    """Return every row's Plate cell, in model row order."""
    return tuple(model.GetValueByRow(row, feed_model.COL_PLATE) for row in range(model.GetCount()))


def _bold_flags_by_plate(model: Any, rows: list[FeedRow]) -> dict[str, bool]:  # noqa: ANN401
    """Return whether ``GetAttrByRow`` bolds each row, by plate."""
    flags = {}
    for row, feed_row in enumerate(rows):
        attr = wx.dataview.DataViewItemAttr()
        attr_set = model.GetAttrByRow(row, feed_model.COL_TIME, attr)
        flags[feed_row.plate] = bool(attr_set and attr.GetBold())
    return flags


def _expected_bold_flags(rows: list[FeedRow]) -> dict[str, bool]:
    """Return which plate the fixture flags -- not a hard-coded row."""
    flagged_plate = next(row.plate for row in rows if row.flagged)
    return {row.plate: row.plate == flagged_plate for row in rows}


# --- feed rows: count, order (T-3/T-9) ------------------------------


def test_main_frame_given_demo_data_source_shows_five_feed_rows_newest_first(
    shared_console: MainFrame,
) -> None:
    """R-32: the feed is the demo fixture, in its newest-first order."""
    model = shared_console.crossings_list.GetModel()

    plates = _feed_plates(model)

    assert plates == CANVAS_FEED_PLATES


# --- the riskiest widget: the bold flagged row (project-plan.md §7) --


def test_main_frame_crossings_model_bolds_only_the_row_the_fixture_flags(
    shared_console: MainFrame,
) -> None:
    """R-34: bold is read through ``GetAttrByRow``, tied to plate 45.

    Never hard-codes row 2 -- the flagged row is found by asking the
    fixture which plate it flags, the same source the model itself
    renders from.
    """
    rows = shared_console.data_source.feed_rows()
    model = shared_console.crossings_list.GetModel()

    bold_flags = _bold_flags_by_plate(model, rows)

    assert bold_flags == _expected_bold_flags(rows)


# --- counters (T-9) --------------------------------------------------


def test_main_frame_given_demo_data_source_shows_canvas_exact_counters(
    shared_console: MainFrame,
) -> None:
    """R-32: crossings/cards/on-course/shoe read as the canvas draws."""
    labels = (
        shared_console.crossings_count_lbl.GetLabelText(),
        shared_console.cards_count_lbl.GetLabelText(),
        shared_console.on_course_lbl.GetLabelText(),
        shared_console.shoe_lbl.GetLabelText(),
    )

    assert labels == CANVAS_COUNTERS


# --- InfoBars (R-73) ---------------------------------------------


@pytest.mark.parametrize("name", INFOBAR_NAMES)
def test_main_frame_infobar_resolves_by_name_and_starts_hidden(
    shared_console: MainFrame, name: str
) -> None:
    """Each code-side InfoBar resolves by name, hidden by default."""
    bar = harness.find_control(shared_console.frame, name)

    assert (bar.GetName(), bar.IsShown()) == (name, False)


# --- splitter sash persistence (CODINGSTANDARDS-UX-DESKTOP.md §6) -----


def test_main_frame_sash_position_round_trips_across_a_simulated_relaunch() -> None:
    """Persist, close, rebuild fresh, restore -- the sash survives.

    Runs the whole build/persist/rebuild/restore sequence in its own
    spawned interpreter (module docstring): this is the one scenario
    of the three that measurably needs it.
    """
    result = _run_scenario("sash_round_trip")

    assert result["ok"], result["context"]
    assert result["data"]["restored_sash"] == 300, result["context"]


# --- hide-times (R-37) -------------------------------------------


def test_main_frame_hide_times_removes_lap_time_and_total_columns_both_ways() -> None:
    """On: Lap time/Total vanish. Off: the full seven columns return.

    Runs in its own spawned interpreter (module docstring), like the
    other two state-mutating scenarios.
    """
    result = _run_scenario("hide_times_columns_round_trip")

    assert result["ok"], result["context"]
    assert result["data"]["before"] == list(feed_model.COLUMN_LABELS), result["context"]
    assert result["data"]["during"] == ["Time", "Plate", "Entry", "Lap", "Card"], result["context"]
    assert result["data"]["after"] == list(feed_model.COLUMN_LABELS), result["context"]


def test_main_frame_hide_times_leaves_the_clock_labels_shown() -> None:
    """R-37: "the closing-window clock stays" through the toggle.

    Runs in its own spawned interpreter (module docstring), like the
    other two state-mutating scenarios.
    """
    result = _run_scenario("hide_times_leaves_clock_shown")

    assert result["ok"], result["context"]
    assert result["data"]["clock_elapsed_shown"] is True, result["context"]
    assert result["data"]["clock_remaining_shown"] is True, result["context"]


# --- the card imagelist (project-plan.md §7) ------------------------


def test_main_frame_crossings_model_card_column_renders_the_dealt_bitmap(
    shared_console: MainFrame,
) -> None:
    """The Card cell is the exact imagelist bitmap, not a lookalike."""
    model = shared_console.crossings_list.GetModel()

    rendered = model.GetValueByRow(0, feed_model.COL_CARD)  # plate 123 -> "9H"

    assert rendered is shared_console.card_images.bitmap("9h")


# --- min size --------------------------------------------------------


def test_main_frame_applies_the_canvas_minimum_size(shared_console: MainFrame) -> None:
    """xrc-windows.md A: "Min frame 1100x700, fits 1366x768."."""
    min_size = shared_console.frame.GetMinSize()

    assert (min_size.width, min_size.height) == MIN_SIZE


# --- negative case: a held crossing must not silently draw a card ----


def test_main_frame_crossings_model_held_card_row_renders_no_bitmap(
    shared_console: MainFrame,
) -> None:
    """A binding mapping "held" onto some default card would still pass.

    Every other assertion here about R-34's held cards would keep
    passing even if this specific mapping silently did nothing.
    """
    rows = shared_console.data_source.feed_rows()
    held_row = next(index for index, row in enumerate(rows) if row.card == "held")
    model = shared_console.crossings_list.GetModel()

    rendered = model.GetValueByRow(held_row, feed_model.COL_CARD)

    assert rendered.IsOk() is False


# --- negative path: MainFrame._find (T-5) -----------------------------


def test_main_frame_find_given_an_unknown_control_name_raises_naming_it(
    shared_console: MainFrame,
) -> None:
    """T-5: the one ``raise`` in ``views/main_frame.py``."""
    with pytest.raises(LookupError, match=re.escape("no control named 'no_such_control'")):
        shared_console._find("no_such_control")
