# SPDX-License-Identifier: GPL-3.0-only
"""Page objects for the 23 XRC windows, keyed off ``ids.py`` (E1.3.3).

xrc-windows.md sections A-E, in that order. Each :class:`WindowSpec`
is one window's load recipe (which ``.xrc`` file, ``LoadFrame`` vs
``LoadDialog``) plus its complete frozen-name and button inventory,
built from ``ui/ids.py`` constants rather than ad hoc strings so a
test reads as intent (``page.find(ids.START_BTN)``) instead of a bare
string lookup. Stock button ids (``wxID_OK`` and friends) are not in
``ui/ids.py`` -- ``tools/gen_ids.py`` deliberately excludes them, since
they are not part of the frozen custom-name registry -- but they are
still real XRC names that ``FindWindowByName`` resolves (measured),
so they are named here as plain module constants instead.

``main_menubar`` is a menu-bar resource, not a window: it loads via
``LoadMenuBar``, not ``LoadFrame``/``LoadDialog``, and its XRC handler
drops the name so it never resolves through ``FindWindowByName``
(measured). It is deliberately absent from :data:`WINDOWS`;
``test_screen_smoke.py`` asserts its absence explicitly rather than
silently omitting it.
"""

from dataclasses import dataclass
from typing import Any

import harness

from rivercrossing.ui import ids

__all__ = ["WINDOWS", "Page", "WindowSpec"]

# Real XRC names and FindWindowByName targets (measured), but excluded
# from ui/ids.py by tools/gen_ids.py's STOCK_IDS set (spec.md 15b).
WX_ID_OK = "wxID_OK"
WX_ID_CANCEL = "wxID_CANCEL"
WX_ID_CLOSE = "wxID_CLOSE"
WX_ID_DELETE = "wxID_DELETE"
WX_ID_OPEN = "wxID_OPEN"
WX_ID_NEW = "wxID_NEW"


@dataclass(frozen=True)
class WindowSpec:
    """One window's load recipe and its frozen control names."""

    name: str
    xrc_file: str
    is_frame: bool
    controls: tuple[str, ...]
    buttons: tuple[str, ...]


class Page:
    """A loaded window plus its :class:`WindowSpec` contract.

    Wraps :func:`harness.find_control` so call sites read as intent
    rather than a bare string lookup against the raw wx window.
    """

    def __init__(self, window: Any, spec: WindowSpec) -> None:  # noqa: ANN401
        """Wrap an already-loaded *window* with its *spec* contract."""
        self.window = window
        self.spec = spec

    def find(self, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Resolve one of this page's own frozen control names."""
        return harness.find_control(self.window, name)


# --- xrc-windows section A: the console -------------------------------

MAIN_FRAME = WindowSpec(
    name=ids.MAIN_FRAME,
    xrc_file="main.xrc",
    is_frame=True,
    controls=(
        ids.RIDE_NAME_LBL,
        ids.RIDE_STATUS_LBL,
        ids.CLOCK_ELAPSED_LBL,
        ids.CLOCK_REMAINING_LBL,
        ids.START_BTN,
        ids.ARM_STOP_CHK,
        ids.STOP_BTN,
        ids.PLATE_INPUT,
        ids.LAST_CROSSING_LBL,
        ids.UNDO_BTN,
        ids.MAIN_SPLITTER,
        ids.CROSSINGS_LIST,
        ids.CROSSINGS_COUNT_LBL,
        ids.CARDS_COUNT_LBL,
        ids.ON_COURSE_LBL,
        ids.SHOE_LBL,
        ids.FLAGGED_LIST,
        ids.REVIEW_BTN,
        ids.MAIN_STATUSBAR,
    ),
    buttons=(ids.START_BTN, ids.STOP_BTN, ids.UNDO_BTN, ids.REVIEW_BTN),
)

# --- xrc-windows section B: ride setup & lifecycle dialogs ------------

RIDE_SETUP_DLG = WindowSpec(
    name=ids.RIDE_SETUP_DLG,
    xrc_file="setup.xrc",
    is_frame=False,
    controls=(
        ids.NAME_INPUT,
        ids.DATE_PICKER,
        ids.START_TIME_PICKER,
        ids.VENUE_INPUT,
        ids.LAP_KM_SPIN,
        ids.ORGANIZER_INPUT,
        ids.SCORER_INPUT,
        ids.DURATION_INPUT,
        ids.MIN_LAP_INPUT,
        ids.LOGO_PICKER,
        ids.SOLO_RADIO,
        ids.MIXED_RADIO,
        ids.TEAM_SIZE_SPIN,
        ids.POOLED_RADIO,
        ids.RELAY_RADIO,
        ids.DECKS_SPIN,
        ids.JOKERS_0_RADIO,
        ids.JOKERS_2_RADIO,
        ids.JOKERS_4_RADIO,
        ids.CAP_CHK,
        ids.CAP_SPIN,
        ids.TIEBREAK_LIST,
        WX_ID_OK,
        WX_ID_CANCEL,
    ),
    buttons=(WX_ID_OK, WX_ID_CANCEL),
)

SET_START_DLG = WindowSpec(
    name=ids.SET_START_DLG,
    xrc_file="dialogs.xrc",
    is_frame=False,
    controls=(ids.START_DATE_PICKER, ids.START_TIME_PICKER, WX_ID_OK, WX_ID_CANCEL),
    buttons=(WX_ID_OK, WX_ID_CANCEL),
)

STOP_CONFIRM_DLG = WindowSpec(
    name=ids.STOP_CONFIRM_DLG,
    xrc_file="dialogs.xrc",
    is_frame=False,
    controls=(WX_ID_OK, WX_ID_CANCEL),
    buttons=(WX_ID_OK, WX_ID_CANCEL),
)

FINISH_CONFIRM_DLG = WindowSpec(
    name=ids.FINISH_CONFIRM_DLG,
    xrc_file="dialogs.xrc",
    is_frame=False,
    controls=(WX_ID_OK, WX_ID_CANCEL),
    buttons=(WX_ID_OK, WX_ID_CANCEL),
)

CONTINUE_OR_NEW_DLG = WindowSpec(
    name=ids.CONTINUE_OR_NEW_DLG,
    xrc_file="dialogs.xrc",
    is_frame=False,
    controls=(ids.MESSAGE_LBL, WX_ID_CANCEL, ids.ARCHIVE_NEW_BTN, ids.CONTINUE_BTN),
    buttons=(WX_ID_CANCEL, ids.ARCHIVE_NEW_BTN, ids.CONTINUE_BTN),
)

RESUME_DLG = WindowSpec(
    name=ids.RESUME_DLG,
    xrc_file="dialogs.xrc",
    is_frame=False,
    controls=(ids.MESSAGE_LBL, ids.LIBRARY_BTN, ids.CONTINUE_BTN),
    buttons=(ids.LIBRARY_BTN, ids.CONTINUE_BTN),
)

EXIT_RUNNING_DLG = WindowSpec(
    name=ids.EXIT_RUNNING_DLG,
    xrc_file="dialogs.xrc",
    is_frame=False,
    controls=(WX_ID_CANCEL, ids.FINISH_FIRST_BTN, WX_ID_OK),
    buttons=(WX_ID_CANCEL, ids.FINISH_FIRST_BTN, WX_ID_OK),
)

# --- xrc-windows section C: riders, corrections & cards ---------------

RIDER_EDITOR_DLG = WindowSpec(
    name=ids.RIDER_EDITOR_DLG,
    xrc_file="riders.xrc",
    is_frame=False,
    controls=(
        ids.RIDERS_LIST,
        ids.PLATE_INPUT,
        ids.NAME_INPUT,
        ids.TEAM_CHOICE,
        ids.ADD_BTN,
        ids.SAVE_BTN,
        ids.DELETE_BTN,
        ids.IMPORT_BTN,
        ids.EXPORT_BTN,
        WX_ID_CLOSE,
    ),
    buttons=(
        ids.ADD_BTN,
        ids.SAVE_BTN,
        ids.DELETE_BTN,
        ids.IMPORT_BTN,
        ids.EXPORT_BTN,
        WX_ID_CLOSE,
    ),
)

CSV_PREVIEW_DLG = WindowSpec(
    name=ids.CSV_PREVIEW_DLG,
    xrc_file="riders.xrc",
    is_frame=False,
    controls=(ids.SUMMARY_LBL, ids.CONFLICTS_LIST, WX_ID_OK, WX_ID_CANCEL),
    buttons=(WX_ID_OK, WX_ID_CANCEL),
)

ENTRY_DETAIL_DLG = WindowSpec(
    name=ids.ENTRY_DETAIL_DLG,
    xrc_file="detail.xrc",
    is_frame=False,
    controls=(
        ids.ENTRY_HEADER_LBL,
        ids.MEMBERS_LBL,
        ids.CARDS_LIST,
        ids.LAPS_LIST,
        ids.EDIT_CROSSING_BTN,
        ids.DEAL_CARD_BTN,
        ids.VOID_CARD_BTN,
        ids.MOVE_RIDER_BTN,
        ids.DNF_BTN,
        ids.AUDIT_BTN,
        WX_ID_CLOSE,
    ),
    buttons=(
        ids.EDIT_CROSSING_BTN,
        ids.DEAL_CARD_BTN,
        ids.VOID_CARD_BTN,
        ids.MOVE_RIDER_BTN,
        ids.DNF_BTN,
        ids.AUDIT_BTN,
        WX_ID_CLOSE,
    ),
)

EDIT_CROSSING_DLG = WindowSpec(
    name=ids.EDIT_CROSSING_DLG,
    xrc_file="dialogs.xrc",
    is_frame=False,
    controls=(
        ids.PLATE_INPUT,
        ids.TIME_PICKER,
        ids.REASON_INPUT,
        ids.VOID_BTN,
        WX_ID_OK,
        WX_ID_CANCEL,
    ),
    buttons=(ids.VOID_BTN, WX_ID_OK, WX_ID_CANCEL),
)

REASSIGN_DLG = WindowSpec(
    name=ids.REASSIGN_DLG,
    xrc_file="dialogs.xrc",
    is_frame=False,
    controls=(ids.CROSSING_LBL, ids.NEW_PLATE_INPUT, ids.REASON_INPUT, WX_ID_OK, WX_ID_CANCEL),
    buttons=(WX_ID_OK, WX_ID_CANCEL),
)

MANUAL_DEAL_DLG = WindowSpec(
    name=ids.MANUAL_DEAL_DLG,
    xrc_file="dialogs.xrc",
    is_frame=False,
    controls=(ids.PLATE_INPUT, ids.REASON_INPUT, WX_ID_OK, WX_ID_CANCEL),
    buttons=(WX_ID_OK, WX_ID_CANCEL),
)

DNF_CONFIRM_DLG = WindowSpec(
    name=ids.DNF_CONFIRM_DLG,
    xrc_file="dialogs.xrc",
    is_frame=False,
    controls=(ids.ENTRY_LBL, ids.REASON_INPUT, WX_ID_OK, WX_ID_CANCEL),
    buttons=(WX_ID_OK, WX_ID_CANCEL),
)

# --- xrc-windows section D: results, library, audit -------------------

RESULTS_FRAME = WindowSpec(
    name=ids.RESULTS_FRAME,
    xrc_file="results.xrc",
    is_frame=True,
    controls=(
        ids.TIEBREAK_LIST,
        ids.REOPEN_BTN,
        ids.STANDINGS_LIST,
        ids.SHOW_TIMES_CHK,
        ids.LAPS_BOARD_CHK,
        ids.TIME_BOARD_CHK,
        ids.FULL_FIELD_CHK,
        ids.ALL_CARDS_CHK,
        ids.EXPORT_HTML_BTN,
        ids.EXPORT_PDF_BTN,
        ids.POSTER_BTN,
        ids.EXPORT_CSV_BTN,
    ),
    buttons=(
        ids.REOPEN_BTN,
        ids.EXPORT_HTML_BTN,
        ids.EXPORT_PDF_BTN,
        ids.POSTER_BTN,
        ids.EXPORT_CSV_BTN,
    ),
)

RIDE_LIBRARY_DLG = WindowSpec(
    name=ids.RIDE_LIBRARY_DLG,
    xrc_file="library.xrc",
    is_frame=False,
    controls=(ids.RIDES_LIST, WX_ID_OPEN, WX_ID_NEW, ids.DUPLICATE_BTN, WX_ID_DELETE, WX_ID_CLOSE),
    buttons=(WX_ID_OPEN, WX_ID_NEW, ids.DUPLICATE_BTN, WX_ID_DELETE, WX_ID_CLOSE),
)

DELETE_RIDE_DLG = WindowSpec(
    name=ids.DELETE_RIDE_DLG,
    xrc_file="library.xrc",
    is_frame=False,
    controls=(ids.MESSAGE_LBL, ids.CONFIRM_NAME_INPUT, WX_ID_DELETE, WX_ID_CANCEL),
    buttons=(WX_ID_DELETE, WX_ID_CANCEL),
)

AUDIT_DLG = WindowSpec(
    name=ids.AUDIT_DLG,
    xrc_file="audit.xrc",
    is_frame=False,
    controls=(ids.AUDIT_SEARCH, ids.ACTION_CHOICE, ids.AUDIT_LIST, WX_ID_CLOSE),
    buttons=(WX_ID_CLOSE,),
)

# --- xrc-windows section E: system & help -----------------------------

SETTINGS_DLG = WindowSpec(
    name=ids.SETTINGS_DLG,
    xrc_file="settings.xrc",
    is_frame=False,
    controls=(
        ids.APPEARANCE_SYSTEM_RADIO,
        ids.APPEARANCE_LIGHT_RADIO,
        ids.APPEARANCE_DARK_RADIO,
        ids.SOUND_CHK,
        ids.HIDE_TIMES_CHK,
        ids.ZOOM_CHOICE,
        ids.BACKUP_NOW_BTN,
        WX_ID_OK,
        WX_ID_CANCEL,
    ),
    buttons=(ids.BACKUP_NOW_BTN, WX_ID_OK, WX_ID_CANCEL),
)

ABOUT_DLG = WindowSpec(
    name=ids.ABOUT_DLG,
    xrc_file="dialogs.xrc",
    is_frame=False,
    controls=(ids.ABOUT_LOGO_BMP, ids.VERSION_LBL, ids.GORBA_LINK, WX_ID_CLOSE),
    buttons=(WX_ID_CLOSE,),
)

SHORTCUTS_DLG = WindowSpec(
    name=ids.SHORTCUTS_DLG,
    xrc_file="dialogs.xrc",
    is_frame=False,
    controls=(ids.SHORTCUTS_LIST, WX_ID_CLOSE),
    buttons=(WX_ID_CLOSE,),
)

SELFTEST_DLG = WindowSpec(
    name=ids.SELFTEST_DLG,
    xrc_file="dialogs.xrc",
    is_frame=False,
    controls=(ids.SELFTEST_OUTPUT, ids.RERUN_BTN, WX_ID_CLOSE),
    buttons=(ids.RERUN_BTN, WX_ID_CLOSE),
)

# xrc-windows's own A-E order: 1 console + 7 setup/lifecycle dialogs +
# 7 rider/card dialogs + 4 results/library/audit + 4 system/help = 23.
WINDOWS: tuple[WindowSpec, ...] = (
    MAIN_FRAME,
    RIDE_SETUP_DLG,
    SET_START_DLG,
    STOP_CONFIRM_DLG,
    FINISH_CONFIRM_DLG,
    CONTINUE_OR_NEW_DLG,
    RESUME_DLG,
    EXIT_RUNNING_DLG,
    RIDER_EDITOR_DLG,
    CSV_PREVIEW_DLG,
    ENTRY_DETAIL_DLG,
    EDIT_CROSSING_DLG,
    REASSIGN_DLG,
    MANUAL_DEAL_DLG,
    DNF_CONFIRM_DLG,
    RESULTS_FRAME,
    RIDE_LIBRARY_DLG,
    DELETE_RIDE_DLG,
    AUDIT_DLG,
    SETTINGS_DLG,
    ABOUT_DLG,
    SHORTCUTS_DLG,
    SELFTEST_DLG,
)
