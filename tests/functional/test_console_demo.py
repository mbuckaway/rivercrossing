# SPDX-License-Identifier: GPL-3.0-only
"""Real-toolkit tests for the console's display (E1.5.1, E5.4.2).

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

E5.4.2 retired the demo seam from the app path: the app's console is
the live ``EngineDataSource`` over a fresh engine (empty feed, zero
counters). The empty-state facts are pinned through the production
``app._build_console_engine`` twice: the ``empty_console`` fixture
below (zero counters, full shoe) and the fresh-engine drive inside
``test_main_frame_given_a_fresh_engine_shows_an_empty_feed`` (empty
feed at rest). The demo ``shared_console`` fixture stays for the
view-capability assertions (bold mapping, card bitmap, the held
row's own chip)
-- ``rivercrossing.demo`` remains importable from tests.

Everything here needs a live ``wx.App`` and the packaged card
bitmaps, so it lives in ``tests/functional/`` rather than
``tests/unit/`` (``cards_imagelist``'s own split is the precedent).

Read-only assertions share two module-scoped fixtures
(``shared_console`` and ``empty_console``, see their own docstrings):
building a ``MainFrame`` decodes the 53-card imagelist and appends 7
DataView columns, and reconstructing one per test measurably raises
this wxPython 4.3.1 / wxWidgets 3.3.3 build's own address-reuse
hazard (``MainFrame._find``'s docstring) at whole-suite scale, where
every functional module's own window churn adds to the same process's
tally.

The tests that mutate ``main_frame`` state instead run their whole
scenario in a fresh, *spawned* interpreter each --
``console_subprocess_scenarios.py``, this module's own docstring --
never forked: forking a process that may already have an initialised
``NSApplication`` is unsafe on macOS, and this session's own
``wx_app`` fixture usually already has one. Measured (a throwaway
sampling script, per this repo's convention): even fully isolated,
that hazard still shows up at a real per-*spawn* rate for one of the
three original scenarios, so :func:`scenario_runner.run_scenario` also
retries the spawn itself, not only relying on the child's own
in-process retry.

One deliberate exception: the single-window drive inside
``test_main_frame_given_a_fresh_engine_shows_an_empty_feed`` below
runs in this process. It builds exactly one wired ``MainFrame`` --
the ``test_mini_acceptance.py`` precedent, whose own docstring
records the measured distinction: the address-reuse hazard the
subprocess scenarios isolate scales with *many* in-process
constructions, and a lone build adds one window to the same tally
every other module's per-test loads already contribute. Mutating the
shared, read-only fixtures instead would make later tests in this
module order-dependent, which is the flake class this whole branch
exists to remove.
"""

import re
from typing import TYPE_CHECKING, Any

import harness
import pytest
import scenario_runner
import wx.dataview

from rivercrossing.demo import DemoDataSource
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import feed_model, ids
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.views import MainFrame, _support
from rivercrossing.ui.views.main_frame import (
    DEFAULT_SASH,
    FINISHED_INFOBAR,
    MIN_SIZE,
    REOPENED_INFOBAR,
    RESUME_INFOBAR,
)

if TYPE_CHECKING:
    from rivercrossing.ui.presenters.data_source import FeedRow

pytestmark = pytest.mark.functional

INFOBAR_NAMES = (RESUME_INFOBAR, REOPENED_INFOBAR, FINISHED_INFOBAR)


@pytest.fixture(scope="module")
def shared_console(xrc_resource: object) -> MainFrame:
    """One demo-fed ``MainFrame`` for the view-capability assertions.

    ``rivercrossing.demo`` is the test-only fixture source since
    E5.4.2: the bold-mapping, card-bitmap and held-no-chip assertions
    below need populated rows to exercise the view's own rendering
    paths, and the app path itself is pinned demo-free by the lint
    contract plus the ``empty_console``/subprocess pins.

    Nothing in this module's read-only tests mutates the feed,
    counters, InfoBars, columns or min size a fresh construction
    already sets once -- so one instance safely serves all of them
    (see the module docstring for why sharing matters here).
    """
    window = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        console = MainFrame(window, data_source=DemoDataSource())
        yield console
    finally:
        # Drop the view before the window dies (Phase 2 reference
        # hygiene): the view holds every control wrapper, so leaving
        # it alive past close_window would keep their SIP map entries
        # from evicting (Addendum 2; pinned by
        # test_harness.py's cycle test).
        del console
        harness.close_window(window)


@pytest.fixture(scope="module")
def empty_console(xrc_resource: object) -> MainFrame:
    """One fresh-engine ``MainFrame`` for the E5.4.2 empty-state pins.

    Wired exactly as the app bootstrap wires it: the production
    :func:`rivercrossing.ui.app._build_console_engine` over an empty
    mixed roster -- an unstarted (DRAFT) engine with zero entries and
    zero crossings, so the feed is empty and the counters read
    zero/full-shoe.
    """
    # load_window, not load_window_verified: this second module-scoped
    # console coexists with the shared_console fixture's same-named
    # ``main_frame`` (set up earlier in the module), so the verified
    # loader's entry guard would refuse the coexistence (Fault B /
    # PR #45); access is reference-scoped, never name-based.
    window = harness.load_window(xrc_resource, ids.MAIN_FRAME, frame=True)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
        _engine, source = app_module._build_console_engine(roster)
        console = MainFrame(window, data_source=source)
        yield console
    finally:
        del console
        harness.close_window(window)


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


def test_main_frame_given_a_fresh_engine_shows_an_empty_feed(
    xrc_resource: object,
) -> None:
    """E5.4.2 empty state, then Start + a typed plate render (R-31/32).

    The app's console reads a fresh live engine (no store-backed ride
    open), so its feed is empty -- the demo rows are gone from the app
    path. ``test_app_bootstrap``'s ``wires_the_console_to_the_live_
    engine_feed`` pin is the same fact through ``build_main_window``;
    this one drives the production ``_build_console_engine`` wiring.

    The read-only assertion would still pass if the console's start
    and entry bindings were dead: ``wire_entry``/``wire_console``
    install them, never the constructor. So the same fresh
    construction is driven through the real controls: Start Ride
    flips the state label DRAFT -> RUNNING, then a plate typed into
    ``plate_input`` and submitted with Record lands in the crossings
    model and the crossings counter, and the field clears. Runs on
    its own window, never the module-scoped fixtures (module
    docstring): mutating those would make the read-only tests
    order-dependent.
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider", last_name="12", plate="12")
    engine, source = app_module._build_console_engine(roster)
    window = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    console = None
    presenter = None
    try:
        window.Show()
        window.Layout()
        harness.pump()
        console = MainFrame(window, data_source=source)
        presenter = ConsolePresenter(console, engine=engine, source=source)
        console.wire_entry(presenter.on_plate_entered)
        console.wire_console(presenter)
        console.set_state(source.ride_status())
        status_lbl = harness.find_control(window, ids.RIDE_STATUS_LBL)

        # E5.4.2: an unstarted fresh engine renders an empty feed and
        # the DRAFT state label.
        plates = _feed_plates(console.crossings_list.GetModel())

        assert plates == ()
        assert status_lbl.GetLabelText() == "DRAFT"

        # Drive 1 -- Start Ride through the wired start_btn: the state
        # label renders RUNNING and the status bar carries the notice.
        harness.click(window, ids.START_BTN, require_shown=True)

        assert status_lbl.GetLabelText() == "RUNNING"
        assert window.GetStatusBar().GetStatusText(0) == "Ride started"

        # Drive 2 -- type a plate and submit with Record: the
        # crossing lands in the feed model (R-32), the crossings
        # counter reads 1, the field clears for the next plate.
        harness.type_text(window, ids.PLATE_INPUT, "12", require_shown=True)
        harness.click(window, ids.RECORD_BTN, require_shown=True)

        assert _feed_plates(console.crossings_list.GetModel()) == ("12",)
        assert harness.find_control(window, ids.CROSSINGS_COUNT_LBL).GetLabelText() == "1"
        assert harness.find_control(window, ids.PLATE_INPUT).GetValue() == ""
        assert status_lbl.GetLabelText() == "RUNNING"
    finally:
        # Phase-2 reference hygiene (the shared-fixture precedent):
        # drop the view and presenter before the window dies so
        # close_window's final gc.collect() can evict their wrappers.
        del console, presenter
        harness.close_window(window)


# --- the riskiest widget: the bold flagged row (project-plan.md §7) --


def test_main_frame_crossings_model_bolds_only_the_row_the_fixture_flags(
    shared_console: MainFrame,
) -> None:
    """R-34: bold is read through ``GetAttrByRow``, tied to plate 45.

    Never hard-codes row 2 -- the flagged row is found by asking the
    fixture which plate it flags, the same source the model itself
    renders from. Demo is the test-only fixture supplying a flagged
    row here (E5.4.2); the live-engine path is pinned by
    ``test_console_live``'s ``live_flagged_crossing_row_is_bold``.
    """
    rows = shared_console.data_source.feed_rows()
    model = shared_console.crossings_list.GetModel()

    bold_flags = _bold_flags_by_plate(model, rows)

    assert bold_flags == _expected_bold_flags(rows)


# --- counters (T-9) --------------------------------------------------


def test_main_frame_given_a_fresh_engine_shows_zero_counters_and_full_shoe(
    empty_console: MainFrame,
) -> None:
    """E5.4.2: a fresh ride counts 0 crossings/cards/course; full shoe.

    The bootstrap engine is a fresh empty DRAFT ride (R-32's counters
    read from the engine, never a display-data source). W12: the two
    registration chips (riders/teams) also read 0 -- the roster is
    empty.
    """
    labels = (
        empty_console.crossings_count_lbl.GetLabelText(),
        empty_console.cards_count_lbl.GetLabelText(),
        empty_console.on_course_lbl.GetLabelText(),
        empty_console.shoe_lbl.GetLabelText(),
        empty_console.riders_count_lbl.GetLabelText(),
        empty_console.teams_count_lbl.GetLabelText(),
    )

    assert labels == ("0", "0", "0", "432/432", "0", "0")


# --- InfoBars (R-73) ---------------------------------------------


@pytest.mark.parametrize("name", INFOBAR_NAMES)
def test_main_frame_infobar_resolves_by_name_and_starts_hidden(
    shared_console: MainFrame, name: str
) -> None:
    """Each code-side InfoBar resolves by name, hidden by default."""
    bar = harness.find_control(shared_console.frame, name)

    assert (bar.GetName(), bar.IsShown()) == (name, False)


@pytest.mark.parametrize("name", INFOBAR_NAMES)
def test_main_frame_infobar_disables_show_hide_effects(
    shared_console: MainFrame, name: str
) -> None:
    """Every code-side InfoBar disables its default slide effect.

    Measured (wxPython 4.3.1 / wxWidgets 3.3.3, macOS, a throwaway
    probe script per this repo's convention, first reproduced while
    wiring ``rider_editor_dlg``'s ``roster_infobar``, E3.2):
    ``Dismiss()``/``ShowMessage()`` on a ``wx.InfoBar`` with its
    default slide effect never returns, dialog or frame shown or
    not. Every code-side InfoBar this app builds must disable both
    effects at construction, or its first real message hangs the
    process with no user present to recover it -- this is the pin
    that keeps ``main_frame.py``'s ``_build_infobar`` fix applied;
    ``test_rider_editor.py``'s sibling pin covers ``roster_infobar``.
    """
    bar = harness.find_control(shared_console.frame, name)

    effects = (bar.GetShowEffect(), bar.GetHideEffect())

    assert effects == (wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)


# --- splitter sash persistence (CODINGSTANDARDS-UX-DESKTOP.md §6) -----


def test_main_frame_sash_position_round_trips_across_a_simulated_relaunch() -> None:
    """Persist, close, rebuild fresh, restore -- the sash survives.

    Runs the whole build/persist/rebuild/restore sequence in its own
    spawned interpreter (module docstring): this is the one scenario
    of the three that measurably needs it.
    """
    result = scenario_runner.run_scenario("sash_round_trip")

    assert result["ok"], result["context"]
    assert result["data"]["restored_sash"] == 300, result["context"]


# --- hide-times (R-37) -------------------------------------------


def test_main_frame_hide_times_removes_lap_time_and_total_columns_both_ways() -> None:
    """On: Lap time/Total vanish. Off: the full seven columns return.

    Runs in its own spawned interpreter (module docstring), like the
    other two state-mutating scenarios.
    """
    result = scenario_runner.run_scenario("hide_times_columns_round_trip")

    assert result["ok"], result["context"]
    assert result["data"]["before"] == list(feed_model.COLUMN_LABELS), result["context"]
    assert result["data"]["during"] == ["Time", "Plate", "Name", "Card", "Lap"], result["context"]
    assert result["data"]["after"] == list(feed_model.COLUMN_LABELS), result["context"]


def test_main_frame_hide_times_leaves_the_clock_labels_shown() -> None:
    """R-37: "the closing-window clock stays" through the toggle.

    Runs in its own spawned interpreter (module docstring), like the
    other two state-mutating scenarios.
    """
    result = scenario_runner.run_scenario("hide_times_leaves_clock_shown")

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


def test_main_frame_card_images_defaults_to_the_shared_support_cache(
    shared_console: MainFrame,
) -> None:
    """The extracted ``default_card_images`` cache backs this deck."""
    assert shared_console.card_images is _support.default_card_images()


# --- min size --------------------------------------------------------


def test_main_frame_applies_the_canvas_minimum_size(shared_console: MainFrame) -> None:
    """W9 min 1100x780: raised for the 30-row feed, still 1366x768."""
    min_size = shared_console.frame.GetMinSize()

    assert (min_size.width, min_size.height) == MIN_SIZE


def test_main_frame_splitter_defaults_to_the_feed_pane_sash(
    shared_console: MainFrame,
) -> None:
    """W9: a fresh launch splits feed/sidebar at the pinned default.

    No persisted sash (E8.1.1's ``initial_sash=None``) falls back to
    ``DEFAULT_SASH``, so the sidebar keeps its drawn ~250 px and the
    feed pane gets the rest; the round-trip scenario pins that a
    persisted position still wins over this default.
    """
    assert shared_console.main_splitter.GetSashPosition() == DEFAULT_SASH


# --- positive case: a held crossing's real card draws its chip (W9) ---


def test_main_frame_crossings_model_held_card_row_renders_the_held_cards_bitmap(
    shared_console: MainFrame,
) -> None:
    """W9: the flagged row's card cell is the held card's own bitmap.

    The old "held" placeholder mapped to no asset and drew no chip;
    W9 removed the placeholder, so the always-visible card rule pins
    the held row to the exact imagelist bitmap of its real code --
    never a lookalike and never a blank.
    """
    rows = shared_console.data_source.feed_rows()
    held_row = next(index for index, row in enumerate(rows) if row.flagged)
    model = shared_console.crossings_list.GetModel()

    rendered = model.GetValueByRow(held_row, feed_model.COL_CARD)

    assert rendered is shared_console.card_images.bitmap("2s")  # plate 45 -> held "2S"


# --- record-crossing row: record_btn, plate font, A4 wiring -----------


def test_main_frame_record_btn_resolves_with_the_canvas_label(shared_console: MainFrame) -> None:
    """``record_btn`` (P8-D3) resolves inside ``main_frame`` by name."""
    record_btn = harness.find_control(shared_console.frame, ids.RECORD_BTN)

    assert record_btn.GetLabelText() == "Record (Enter)"


def test_main_frame_plate_input_font_is_about_one_and_a_half_times_the_system_default(
    shared_console: MainFrame,
) -> None:
    """P8-D3: the XRC relative-size font renders ~1.5x default."""
    default_pt = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT).GetPointSize()
    plate_pt = shared_console.plate_input.GetFont().GetPointSize()

    assert abs(plate_pt - round(default_pt * 1.5)) <= 1


def test_main_frame_set_state_enables_or_disables_plate_input_and_record_btn_together() -> None:
    """A4: record_btn tracks plate_input, enabled only in RUNNING.

    Runs in its own spawned interpreter (module docstring): ``set_
    state`` mutates controls the shared, read-only ``shared_console``
    fixture forbids mutating.
    """
    result = scenario_runner.run_scenario("state_enablement_round_trip")

    assert result["ok"], result["context"]
    assert result["data"]["running"] == [True, True], result["context"]
    assert result["data"]["draft"] == [False, False], result["context"]


def test_main_frame_plate_entry_given_no_ride_open_rejects_and_keeps_the_field() -> None:
    """R-31/E5.4.2: with no ride open, a typed plate is refused.

    Runs in its own spawned interpreter (module docstring): a real
    app bootstrap and a real ``EVT_TEXT_ENTER`` cannot run against
    the shared fixtures. E5.4.2 leaves the bootstrap console DRAFT --
    no store-backed ride is open, and a fresh launch starts no ride --
    so plate 123 is refused because the ride is not running: the
    notice says so, the field is kept, focus returns, and no
    crossing is recorded -- the console's correct empty state.
    """
    result = scenario_runner.run_scenario("plate_entry_round_trip")

    assert result["ok"], result["context"]
    assert result["data"]["feed_plates"] == [], result["context"]
    assert result["data"]["field_value"] == "123", result["context"]
    assert result["data"]["focused"] is True, result["context"]
    assert result["data"]["crossings_label"] == "0", result["context"]
    assert result["data"]["status_text"] == "The ride is not running", result["context"]


def test_main_frame_record_btn_given_no_ride_open_rejects_and_keeps_the_field() -> None:
    """R-31/E5.4.2: Record with no ride open refuses the plate too."""
    result = scenario_runner.run_scenario("record_btn_click_records_once")

    assert result["ok"], result["context"]
    assert result["data"]["feed_plates"] == [], result["context"]
    assert result["data"]["field_value"] == "77", result["context"]
    assert result["data"]["focused"] is True, result["context"]
    assert result["data"]["crossings_label"] == "0", result["context"]
    assert result["data"]["status_text"] == "The ride is not running", result["context"]


def test_build_main_window_starts_the_console_in_the_draft_state() -> None:
    """The bootstrap runs ``set_state(data_source.ride_status())`` (A4).

    Runs in its own spawned interpreter (module docstring): drives
    the real ``build_main_window`` bootstrap, not a bare ``MainFrame``.
    A fresh launch starts no ride (E5.4.2, R-31), so the console
    opens DRAFT with plate entry and Record disabled.
    """
    result = scenario_runner.run_scenario("console_starts_in_draft_state")

    assert result["ok"], result["context"]
    assert result["data"]["plate_enabled"] is False, result["context"]
    assert result["data"]["record_enabled"] is False, result["context"]
    assert result["data"]["status_label"] == "DRAFT", result["context"]


# --- negative path: MainFrame._find (T-5) -----------------------------


def test_main_frame_find_given_an_unknown_control_name_raises_naming_it(
    shared_console: MainFrame,
) -> None:
    """T-5: the one ``raise`` in ``views/main_frame.py``."""
    with pytest.raises(LookupError, match=re.escape("no control named 'no_such_control'")):
        shared_console._find("no_such_control")
