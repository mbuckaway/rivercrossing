# SPDX-License-Identifier: GPL-3.0-only
"""Window specs for the 23 XRC windows, keyed off ``ids.py`` (E1.3.3).

xrc-windows.md sections A-E, in that order. Each :class:`WindowSpec`
is one window's load recipe (which ``.xrc`` file, ``LoadFrame`` vs
``LoadDialog``) plus its complete frozen-name and button inventory,
built from ``ui/ids.py`` constants rather than ad hoc strings so a
test reads as intent (``harness.find_control(frame, ids.START_BTN)``)
instead of a bare string lookup. Stock button ids (``wxID_OK`` and
friends) are not in ``ui/ids.py`` -- ``tools/gen_ids.py`` deliberately
excludes them, since they are not part of the frozen custom-name
registry -- but they are still real XRC names that ``FindWindowByName``
resolves (measured), so they are named here as plain module constants
instead.

``main_menubar`` is a menu-bar resource, not a window: it loads via
``LoadMenuBar``, not ``LoadFrame``/``LoadDialog``, and its XRC handler
drops the name so it never resolves through ``FindWindowByName``
(measured). It is deliberately absent from :data:`WINDOWS`;
``test_screen_smoke.py`` asserts its absence explicitly rather than
silently omitting it.
"""

from dataclasses import dataclass

from rivercrossing.ui import ids

__all__ = ["WINDOWS", "WindowSpec"]

# Real XRC names and FindWindowByName targets (measured), but excluded
# from ui/ids.py by tools/gen_ids.py's STOCK_IDS set (spec.md 15b).
WX_ID_OK = "wxID_OK"
WX_ID_CANCEL = "wxID_CANCEL"
WX_ID_CLOSE = "wxID_CLOSE"
WX_ID_DELETE = "wxID_DELETE"
WX_ID_OPEN = "wxID_OPEN"


@dataclass(frozen=True)
class WindowSpec:
    """One window's load recipe and its frozen control names."""

    name: str
    xrc_file: str
    is_frame: bool
    controls: tuple[str, ...]
    buttons: tuple[str, ...]


# --- xrc-windows section A: the console -------------------------------

MAIN_FRAME = WindowSpec(
    name=ids.MAIN_FRAME,
    xrc_file="main.xrc",
    is_frame=True,
    controls=(
        # C1: the ride-identity block -- ride_logo_bmp carries the
        # ride's own logo, ride_details_lbl is the "date · start ·
        # type" fallback.
        ids.RIDE_LOGO_BMP,
        ids.RIDE_NAME_LBL,
        ids.RIDE_DETAILS_LBL,
        ids.RIDE_STATUS_LBL,
        # WS-D/WS-H: the code-side gauge slots and the review notebook
        # (elapsed_clock_panel/remaining_clock_panel/ride_status_panel
        # are XRC placeholder panels for RaceClock/StopLight, and the
        # notebook hosts flagged_list/review_btn/console_riders_list).
        ids.RIDE_STATUS_PANEL,
        ids.CLOCK_ELAPSED_LBL,
        ids.CLOCK_REMAINING_LBL,
        ids.ELAPSED_CLOCK_PANEL,
        ids.REMAINING_CLOCK_PANEL,
        ids.START_BTN,
        ids.STOP_BTN,
        ids.PLATE_INPUT,
        ids.RECORD_BTN,
        ids.LAST_CROSSING_LBL,
        ids.UNDO_BTN,
        ids.MAIN_SPLITTER,
        ids.CROSSINGS_LIST,
        ids.CROSSINGS_COUNT_LBL,
        ids.CARDS_COUNT_LBL,
        ids.ON_COURSE_LBL,
        ids.SHOE_LBL,
        # W12: the two registration chips (riders_count_lbl /
        # teams_count_lbl) sit beside the four live-timing chips.
        ids.RIDERS_COUNT_LBL,
        ids.TEAMS_COUNT_LBL,
        ids.REVIEW_NOTEBOOK,
        ids.FLAGGED_LIST,
        ids.REVIEW_BTN,
        ids.CONSOLE_RIDERS_LIST,
        ids.MAIN_STATUSBAR,
    ),
    buttons=(ids.START_BTN, ids.STOP_BTN, ids.RECORD_BTN, ids.UNDO_BTN, ids.REVIEW_BTN),
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
        ids.LOGO_PREVIEW_BMP,
        ids.LOGO_STATUS_LBL,
        ids.LOGO_BROWSE_BTN,
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

# W5/W15/native-confirms: finish_confirm_dlg, duplicate_ride_dlg,
# reopen_ride_dlg and exit_confirm_dlg were deleted from the .xrc
# files -- the finish/reopen/exit confirmations are now native
# ``ui.std_dialogs`` dialogs (show_danger / show_prompt /
# show_confirm), which have no XRC window and so no ``FindWindowByName``
# target. This registry therefore no longer lists them.

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
    controls=(ids.MESSAGE_LBL, WX_ID_CANCEL, ids.FINISH_FIRST_BTN, WX_ID_OK),
    buttons=(WX_ID_CANCEL, ids.FINISH_FIRST_BTN, WX_ID_OK),
)

# ux-polish W3: no_ride_dlg, the no-ride prompt that replaced
# continue_or_new_dlg at the store-backed bootstrap, was removed in
# W15 — the launch shows the native info dialog (app.py
# _show_no_ride_info) and the create/open choice lives in the File
# menus, so this registry no longer lists the window.

# --- xrc-windows section C: riders, corrections & cards ---------------

RIDER_EDITOR_DLG = WindowSpec(
    name=ids.RIDER_EDITOR_DLG,
    xrc_file="riders.xrc",
    is_frame=False,
    controls=(
        ids.RIDERS_LIST,
        ids.RIDER_SEARCH,
        ids.PLATE_INPUT,
        ids.FIRST_NAME_INPUT,
        ids.LAST_NAME_INPUT,
        ids.TEAM_CHOICE,
        # 1.0.12: the Rider box is display-only and the pane's add_btn /
        # edit_btn open add_rider_dlg (Add) or the same window in its
        # Edit mode; the editor's own save_btn was removed.
        ids.ADD_BTN,
        ids.EDIT_BTN,
        ids.DELETE_BTN,
        WX_ID_CLOSE,
    ),
    buttons=(
        ids.ADD_BTN,
        ids.EDIT_BTN,
        ids.DELETE_BTN,
        WX_ID_CLOSE,
    ),
)

# W7: the dedicated Add Rider dialog the editor's add_btn opens. Its
# input names reuse the editor's own (plate_input etc.); each
# top-level window is its own name namespace, so the ids constants
# stay shared (tools/gen_ids.py's own rule).
ADD_RIDER_DLG = WindowSpec(
    name=ids.ADD_RIDER_DLG,
    xrc_file="riders.xrc",
    is_frame=False,
    controls=(
        ids.PLATE_INPUT,
        ids.FIRST_NAME_INPUT,
        ids.LAST_NAME_INPUT,
        ids.TEAM_CHOICE,
        WX_ID_OK,
        WX_ID_CANCEL,
    ),
    buttons=(WX_ID_OK, WX_ID_CANCEL),
)

CSV_PREVIEW_DLG = WindowSpec(
    name=ids.CSV_PREVIEW_DLG,
    xrc_file="riders.xrc",
    is_frame=False,
    controls=(ids.SUMMARY_LBL, ids.CONFLICTS_LIST, WX_ID_OK, WX_ID_CANCEL),
    buttons=(WX_ID_OK, WX_ID_CANCEL),
)

RIDER_ISSUES_DLG = WindowSpec(
    name=ids.RIDER_ISSUES_DLG,
    xrc_file="riders.xrc",
    is_frame=False,
    controls=(
        ids.ISSUES_SUMMARY_LBL,
        ids.ISSUES_LIST,
        ids.OPEN_EDITOR_BTN,
        ids.CONVERT_SOLO_BTN,
        WX_ID_CLOSE,
    ),
    buttons=(ids.OPEN_EDITOR_BTN, ids.CONVERT_SOLO_BTN, WX_ID_CLOSE),
)

# --- xrc-windows section C: Phase 4 teams editor ---

# The reworked three-pane editor: Team | Riders | Members columns. Both
# the read-only record form and the pane's buttons are display/route
# only: add_btn and edit_btn open add_team_dlg (blank / on the
# selected team), remove_btn sits between them, and wxID_CLOSE closes
# the editor -- the old save_btn / image_btn / remove_logo_btn row was
# removed with the read-only rework. The logo preview and its
# pick_card_btn moved to add_team_dlg, where the record is written, so
# this window carries no logo controls of its own.
TEAM_EDITOR_DLG = WindowSpec(
    name=ids.TEAM_EDITOR_DLG,
    xrc_file="teams.xrc",
    is_frame=False,
    controls=(
        ids.TEAMS_LIST,
        ids.SINGLE_MEMBER_ONLY_CHK,
        ids.NAME_INPUT,
        ids.RELAY_PLATE_INPUT,
        ids.NOTES_INPUT,
        ids.MEMBERS_LIST,
        ids.EDIT_BTN,
        ids.REMOVE_BTN,
        ids.ADD_BTN,
        WX_ID_CLOSE,
    ),
    buttons=(
        ids.EDIT_BTN,
        ids.REMOVE_BTN,
        ids.ADD_BTN,
        WX_ID_CLOSE,
    ),
)

# --- xrc-windows section C: W8 Add Team dialog ---

# W8: the dedicated Add Team dialog the teams editor's add_btn opens.
# Its input names reuse the editor's own (name_input etc.); each
# top-level window is its own name namespace, so the ids constants
# stay shared (tools/gen_ids.py's own rule).
ADD_TEAM_DLG = WindowSpec(
    name=ids.ADD_TEAM_DLG,
    xrc_file="teams.xrc",
    is_frame=False,
    controls=(
        ids.NAME_INPUT,
        ids.RELAY_PLATE_INPUT,
        ids.NOTES_INPUT,
        ids.LOGO_BMP,
        ids.PICK_CARD_BTN,
        WX_ID_OK,
        WX_ID_CANCEL,
    ),
    buttons=(
        WX_ID_OK,
        WX_ID_CANCEL,
        ids.PICK_CARD_BTN,
    ),
)

ENTRY_DETAIL_DLG = WindowSpec(
    name=ids.ENTRY_DETAIL_DLG,
    xrc_file="detail.xrc",
    is_frame=False,
    controls=(
        ids.PLATE_CHOICE,
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

RESULTS_DLG = WindowSpec(
    name=ids.RESULTS_DLG,
    xrc_file="results.xrc",
    is_frame=False,
    controls=(
        ids.STANDINGS_LIST,
        ids.TEAMS_STANDINGS_LIST,
        ids.SOLO_STANDINGS_LIST,
        ids.RESULTS_NOTEBOOK,
        ids.SHOW_TIMES_CHK,
        ids.LAPS_BOARD_CHK,
        ids.TIME_BOARD_CHK,
        ids.FULL_FIELD_CHK,
        ids.ALL_CARDS_CHK,
        ids.EXPORT_HTML_BTN,
        ids.EXPORT_PDF_BTN,
        ids.POSTER_BTN,
        ids.EXPORT_CSV_BTN,
        WX_ID_CLOSE,
    ),
    buttons=(
        ids.EXPORT_HTML_BTN,
        ids.EXPORT_PDF_BTN,
        ids.POSTER_BTN,
        ids.EXPORT_CSV_BTN,
        WX_ID_CLOSE,
    ),
)

RIDE_LIBRARY_DLG = WindowSpec(
    name=ids.RIDE_LIBRARY_DLG,
    xrc_file="library.xrc",
    is_frame=False,
    controls=(ids.RIDES_LIST, WX_ID_OPEN, ids.DUPLICATE_BTN, WX_ID_DELETE, WX_ID_CLOSE),
    buttons=(WX_ID_OPEN, ids.DUPLICATE_BTN, WX_ID_DELETE, WX_ID_CLOSE),
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
        # S7: verbose logging writes the support diagnostic log.
        ids.VERBOSE_LOG_CHK,
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

# xrc-windows's own A-E order: 1 console + 4 setup/lifecycle dialogs
# (finish_confirm_dlg, duplicate_ride_dlg, reopen_ride_dlg and
# exit_confirm_dlg became native std_dialogs confirms; stop_confirm_dlg
# retired W5, no_ride_dlg retired W15) + 11 rider/card dialogs
# (add_rider_dlg, add_team_dlg and team_editor_dlg are W7/Phase 4
# section-C members) + 4 results/library/audit + 4 system/help = 24.
WINDOWS: tuple[WindowSpec, ...] = (
    MAIN_FRAME,
    RIDE_SETUP_DLG,
    SET_START_DLG,
    RESUME_DLG,
    EXIT_RUNNING_DLG,
    RIDER_EDITOR_DLG,
    ADD_RIDER_DLG,
    CSV_PREVIEW_DLG,
    RIDER_ISSUES_DLG,
    TEAM_EDITOR_DLG,
    ADD_TEAM_DLG,
    ENTRY_DETAIL_DLG,
    EDIT_CROSSING_DLG,
    REASSIGN_DLG,
    MANUAL_DEAL_DLG,
    DNF_CONFIRM_DLG,
    RESULTS_DLG,
    RIDE_LIBRARY_DLG,
    DELETE_RIDE_DLG,
    AUDIT_DLG,
    SETTINGS_DLG,
    ABOUT_DLG,
    SHORTCUTS_DLG,
    SELFTEST_DLG,
)
