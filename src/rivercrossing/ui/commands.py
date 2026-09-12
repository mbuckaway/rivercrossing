# SPDX-License-Identifier: GPL-3.0-only
"""The menu route map and its state-enablement rules (E1.4.1, E1.4.2).

spec.md section 15 is one table with two jobs: which target each of
the 40 menu rows reaches ("Opens / does"), and when it is allowed to
fire ("Enabled when"). :data:`ROUTE_TABLE` is that table transcribed
once, so both jobs read off the same 40 :class:`MenuRoute` rows
instead of two tables that could drift apart. (Results lost its
mi_tiebreak_order row: the tie-break order now comes only from the
ride's stored config, set in Ride Setup.)

No wx import lands here (R-71 does not require it, since nothing
below touches a window, but the presenter-protocol pattern --
module-skeletons.md S1 -- is to keep UI *logic* headless-testable
regardless): :func:`route_for_id` and :func:`is_route_enabled` are
plain functions over plain data, unit-testable without a display.

No ride engine exists before EPIC 4 (module-skeletons.md), so
:class:`RideState` stands in for it: a plain, frozen fact sheet a
future engine will produce and this module's enablement rules will
keep consuming unchanged.
"""

from dataclasses import dataclass
from enum import Enum

from rivercrossing.ride import RideStatus
from rivercrossing.ui import ids

__all__ = [
    "ALWAYS",
    "ROUTE_TABLE",
    "Enablement",
    "MenuRoute",
    "RideState",
    "TargetKind",
    "UnroutedMenuItemError",
    "is_route_enabled",
    "route_for_id",
]


class TargetKind(Enum):
    """Which of the three target shapes a :class:`MenuRoute` has.

    ``WINDOW``/``DIALOG`` targets are one of ``ui/ids.py``'s frozen
    XRC names -- a screen worth its own persistent content (window)
    or a small transient confirm/edit (dialog). ``COMMAND`` targets
    have no XRC window at all: an OS-native picker, an external
    browser, or a direct in-place action -- spec.md's own wording
    ("Direct command", "no dialog", "OS-native ... dialog") is what
    decides which of the three a row gets, not a guess.
    """

    WINDOW = "window"
    DIALOG = "dialog"
    COMMAND = "command"


_DRAFT = frozenset({RideStatus.DRAFT})
_RUNNING = frozenset({RideStatus.RUNNING})
_RUNNING_REOPENED = frozenset({RideStatus.RUNNING, RideStatus.REOPENED})
_FINISHED = frozenset({RideStatus.FINISHED})
_DRAFT_RUNNING_REOPENED = frozenset({RideStatus.DRAFT, RideStatus.RUNNING, RideStatus.REOPENED})


@dataclass(frozen=True)
class Enablement:
    """One §15 "Enabled when" cell, decomposed into named conditions.

    ``allowed_states=None`` means the rule is not gated by
    ``RideStatus`` membership at all -- either the row says "always",
    or it is a condition-only rule such as Review Held Cards' "held
    cards > 0", which §15 never ties to a particular state. Every
    other field defaults to "no extra requirement", so a bare
    ``Enablement()`` -- :data:`ALWAYS` -- reads as exactly that.

    Attributes:
        allowed_states: The ``RideStatus`` values the row lists, or
            ``None`` if it does not gate on status.
        requires_ride_open: The row's bare "ride open" / "a ride is
            open" condition.
        requires_ride_stopped: Start Ride's "or stopped RUNNING"
            clause -- only consulted while ``status == RUNNING``.
        teams_allowed: The ride's entry_mode is mixed, so team
            records exist to edit (Phase 4's Teams Editor gate).
        min_crossings: The row's "≥1 crossing" condition, as a
            threshold so boundary tests can vary it.
        min_held_cards: Review Held Cards' "held cards > 0".
        min_audit_rows: Audit Trail's "≥1 audit row".
        requires_entry_has_cards: Void Card's "entry has cards".
        requires_export_exists: Preview in Browser's "an export
            exists".
    """

    allowed_states: frozenset[RideStatus] | None = None
    requires_ride_open: bool = False
    requires_ride_stopped: bool = False
    teams_allowed: bool = False
    min_crossings: int = 0
    min_held_cards: int = 0
    min_audit_rows: int = 0
    requires_entry_has_cards: bool = False
    requires_export_exists: bool = False


ALWAYS = Enablement()


@dataclass(frozen=True)
class MenuRoute:
    """One spec.md section 15 row: what a menu item does, and when.

    Attributes:
        menu: The owning menu's plain name (``"File"``, ``"Ride"``,
            ...), matching spec.md section 15's row groups.
        label: The row's own text, transcribed from the "Menu item"
            column (the part after "▸").
        ids: The XRC names this row covers -- one for almost every
            row; the View row's single "Hide Times · Zoom"
            entry covers all eight of its radio/check items, since
            §15 itself groups them into one row (W13 removed the three
            theme radios -- Settings owns appearance now).
        kind: Which :class:`TargetKind` *target* is.
        target: A ``ui/ids.py`` frozen name for ``WINDOW``/``DIALOG``
            kinds, or a short symbolic action name for ``COMMAND``.
        enabled_when: The row's "Enabled when" cell, structured.
    """

    menu: str
    label: str
    ids: tuple[str, ...]
    kind: TargetKind
    target: str
    enabled_when: Enablement


ROUTE_TABLE: tuple[MenuRoute, ...] = (
    # --- File: 8 rows ---
    # D1: New Ride… left the File menu for the Ride menu (the ride
    # lifecycle lives on one surface); the remaining seven rows keep
    # spec.md 15's own order. The rider simulator adds the eighth,
    # after Back Up Database.
    MenuRoute(
        menu="File",
        label="Ride Library",
        ids=("mi_open_library",),
        kind=TargetKind.WINDOW,
        target=ids.RIDE_LIBRARY_DLG,
        enabled_when=ALWAYS,  # "always"
    ),
    MenuRoute(
        menu="File",
        label="Duplicate Ride…",
        ids=("mi_duplicate_ride",),
        # E5.4.1 mock-first; Phase 11 H2 replaced the authored dialog
        # with the native std_dialogs.show_prompt, so the row is now a
        # COMMAND (the confirm -> action flow lives in app.py's
        # "duplicate_ride" route target).
        kind=TargetKind.COMMAND,
        target="duplicate_ride",
        enabled_when=Enablement(requires_ride_open=True),  # "a ride is open"
    ),
    MenuRoute(
        menu="File",
        label="Import Riders CSV…",
        ids=("mi_import_csv",),
        kind=TargetKind.DIALOG,
        target=ids.CSV_PREVIEW_DLG,
        # "ride open (structure edits: DRAFT only)" -- the parenthetical
        # is a note about what the import flow permits once inside, not
        # a second gate on the menu item itself (see the E1.4.1 report).
        enabled_when=Enablement(requires_ride_open=True),
    ),
    MenuRoute(
        menu="File",
        label="Export Riders CSV…",
        ids=("mi_export_csv",),
        kind=TargetKind.COMMAND,  # OS-native save dialog -- no app window
        target="export_riders_csv",
        enabled_when=Enablement(requires_ride_open=True),  # "ride open"
    ),
    MenuRoute(
        menu="File",
        label="Back Up Database…",
        ids=("mi_backup_now",),
        # R-54 manual backup: writes one timestamped copy into the
        # <db>.backups/ sibling directory and posts the path -- no
        # OS-native save dialog, no app window.
        kind=TargetKind.COMMAND,
        target="backup_database",
        enabled_when=ALWAYS,  # "always"
    ),
    MenuRoute(
        menu="File",
        label="Simulation…",
        ids=("mi_simulation",),
        # The Rider Simulator generates a placeholder field, so it only
        # makes sense on a ride whose roster is still open for edits.
        kind=TargetKind.DIALOG,
        target=ids.SIMULATION_DLG,
        enabled_when=Enablement(requires_ride_open=True, allowed_states=_DRAFT),
    ),
    MenuRoute(
        menu="File",
        label="Settings…",
        ids=("wxID_PREFERENCES",),
        kind=TargetKind.WINDOW,
        target=ids.SETTINGS_DLG,
        enabled_when=ALWAYS,  # "always"
    ),
    MenuRoute(
        menu="File",
        label="Exit",
        ids=("wxID_EXIT",),
        # Branches on ride state via quit_flow.dialog_for_status
        # (RUNNING -> exit_running_dlg; otherwise -> None, the native
        # std_dialogs.show_confirm -- Phase 11 H2) rather than reaching
        # one fixed target, so this is the flow itself, not a single
        # window/dialog. Phase 8 (P8-D1/R-51):
        # the app never exits without confirmation -- app.py's own
        # "exit_or_quit" special-case in _make_route_handler is what
        # runs that flow; this table only records that the row has
        # one.
        kind=TargetKind.COMMAND,
        target="exit_or_quit",
        enabled_when=ALWAYS,  # "always"
    ),
    # --- Ride: 9 rows ---
    # W14 left this menu at six rows because the "edit this ride"
    # semantics behind mi_ride_setup were never built. D1/D2 build
    # them: New Ride… moves here, Edit Ride… opens the same setup
    # window PRELOADED with the live config, and Clear Ride… resets
    # the open ride to a fresh DRAFT.
    MenuRoute(
        menu="Ride",
        label="New Ride…",
        ids=("mi_new_ride",),
        kind=TargetKind.WINDOW,
        target=ids.RIDE_SETUP_DLG,
        enabled_when=ALWAYS,  # "always"
    ),
    MenuRoute(
        menu="Ride",
        label="Edit Ride…",
        ids=("mi_edit_ride",),
        # The same dialog as New Ride… in its preload mode; app.py's
        # _decorate distinguishes them by the row's own id (the
        # mi_add_crossing_at/mi_edit_crossing precedent). Only the
        # ride-open gate applies -- the structural fields are locked
        # inside the dialog for any ride past DRAFT (D2).
        kind=TargetKind.WINDOW,
        target=ids.RIDE_SETUP_DLG,
        enabled_when=Enablement(requires_ride_open=True),  # "a ride is open"
    ),
    MenuRoute(
        menu="Ride",
        label="Start Ride",
        ids=("mi_start_ride",),
        # ux-polish: the app.py route handler fires the live
        # presenter's on_start; the engine's own start gate refuses an
        # empty roster / incomplete setup (StartBlockedError). The
        # former continue_or_new_dlg branch retired with that dialog.
        kind=TargetKind.COMMAND,
        target="start_ride",
        # C2: REOPENED joins the startable set (Start continues riding
        # out of the corrections state); the stopped clause still only
        # gates a RUNNING ride, so DRAFT/REOPENED need no stop state.
        # W1: an open ride is also required -- the bootstrap console
        # holds no ride at all, so Start must be off there.
        enabled_when=Enablement(
            allowed_states=_DRAFT_RUNNING_REOPENED,
            requires_ride_stopped=True,
            requires_ride_open=True,
        ),  # "a ride is open; DRAFT, or stopped RUNNING, or REOPENED"
    ),
    MenuRoute(
        menu="Ride",
        label="Stop Ride…",
        ids=("mi_stop_ride",),
        # W5: the row dispatches to the live presenter's native
        # stop-confirm flow (kind=COMMAND target "stop_ride") -- the
        # XRC stop_confirm_dlg retired with it; a riderless roster
        # gets a native warning instead of any dialog.
        kind=TargetKind.COMMAND,
        target="stop_ride",
        enabled_when=Enablement(allowed_states=_RUNNING),  # "RUNNING"
    ),
    MenuRoute(
        menu="Ride",
        label="Set Start Time…",
        ids=("mi_set_start_time",),
        kind=TargetKind.DIALOG,
        target=ids.SET_START_DLG,
        enabled_when=Enablement(allowed_states=_RUNNING_REOPENED),  # "RUNNING · REOPENED"
    ),
    MenuRoute(
        menu="Ride",
        label="Finish Ride…",
        ids=("mi_finish_ride",),
        # Phase 11 H2: finish_confirm_dlg retired for the native
        # std_dialogs.show_danger confirm, so the row is a COMMAND
        # dispatched to app.py's "finish_ride" route target.
        kind=TargetKind.COMMAND,
        target="finish_ride",
        enabled_when=Enablement(allowed_states=_RUNNING_REOPENED),  # "RUNNING · REOPENED"
    ),
    MenuRoute(
        menu="Ride",
        label="Reopen Ride",
        ids=("mi_reopen_ride",),
        # E5.4.1 mock-first; Phase 11 H2 replaced the authored dialog
        # with the native std_dialogs.show_prompt, so the row is now a
        # COMMAND (app.py's "reopen_ride" route target).
        kind=TargetKind.COMMAND,
        target="reopen_ride",
        enabled_when=Enablement(allowed_states=_FINISHED),  # "FINISHED"
    ),
    MenuRoute(
        menu="Ride",
        label="Audit Trail…",
        ids=("mi_audit_trail",),
        kind=TargetKind.WINDOW,
        target=ids.AUDIT_DLG,
        enabled_when=Enablement(
            requires_ride_open=True, min_audit_rows=1
        ),  # "ride open, ≥1 audit row"
    ),
    MenuRoute(
        menu="Ride",
        label="Clear Ride…",
        ids=("mi_clear_ride",),
        # D3: a confirmed native danger dialog, then an in-place reset
        # -- no XRC window, so a COMMAND target like Stop Ride…'s.
        kind=TargetKind.COMMAND,
        target="clear_ride",
        # DRAFT, or stopped RUNNING, or FINISHED -- REOPENED is not
        # clearable (finish it first) and a live RUNNING ride must
        # stop first; the stopped clause only gates a RUNNING ride.
        # W1: an open ride is also required -- there is nothing to
        # clear on the no-ride bootstrap console.
        enabled_when=Enablement(
            allowed_states=frozenset({RideStatus.DRAFT, RideStatus.RUNNING, RideStatus.FINISHED}),
            requires_ride_stopped=True,
            requires_ride_open=True,
        ),
    ),
    # --- Riders: 5 rows ---
    MenuRoute(
        menu="Riders",
        label="Rider Editor",
        ids=("mi_rider_editor",),
        kind=TargetKind.WINDOW,
        target=ids.RIDER_EDITOR_DLG,
        enabled_when=Enablement(requires_ride_open=True),  # "ride open"
    ),
    MenuRoute(
        menu="Riders",
        label="Teams Editor",
        ids=("mi_team_editor",),
        kind=TargetKind.WINDOW,
        target=ids.TEAM_EDITOR_DLG,
        # Phase 4: team records only exist on a mixed ride -- a
        # solo-only ride has no teams to edit (the menu's own
        # teams_allowed gate mirrors the roster's entry_mode).
        enabled_when=Enablement(requires_ride_open=True, teams_allowed=True),
    ),
    MenuRoute(
        menu="Riders",
        label="Check for Rider Issues…",
        ids=("mi_check_rider_issues",),
        kind=TargetKind.WINDOW,
        target=ids.RIDER_ISSUES_DLG,
        enabled_when=Enablement(requires_ride_open=True),  # "ride open"
    ),
    # D4: Add Rider/Entry… retired -- the Rider Editor row above is
    # the single entry point for adding riders.
    MenuRoute(
        menu="Riders",
        label="Mark DNF…",
        ids=("mi_mark_dnf",),
        kind=TargetKind.DIALOG,
        target=ids.DNF_CONFIRM_DLG,
        enabled_when=Enablement(allowed_states=_RUNNING_REOPENED),  # "RUNNING · REOPENED"
    ),
    MenuRoute(
        menu="Riders",
        label="Entry Detail…",
        ids=("mi_entry_detail",),
        kind=TargetKind.WINDOW,
        target=ids.ENTRY_DETAIL_DLG,
        enabled_when=Enablement(requires_ride_open=True),  # "ride open"
    ),
    # --- Cards: 7 rows ---
    MenuRoute(
        menu="Cards",
        label="Undo Last Crossing",
        ids=("mi_undo_crossing",),
        kind=TargetKind.COMMAND,  # "Direct command + status-bar notice (no dialog)"
        target="undo_last_crossing",
        enabled_when=Enablement(
            allowed_states=_RUNNING, min_crossings=1
        ),  # "RUNNING, ≥1 crossing"
    ),
    MenuRoute(
        menu="Cards",
        label="Add Crossing at Time…",
        ids=("mi_add_crossing_at",),
        kind=TargetKind.DIALOG,
        target=ids.EDIT_CROSSING_DLG,
        enabled_when=Enablement(allowed_states=_RUNNING_REOPENED),  # "RUNNING · REOPENED"
    ),
    MenuRoute(
        menu="Cards",
        label="Edit Crossing…",
        ids=("mi_edit_crossing",),
        kind=TargetKind.DIALOG,
        target=ids.EDIT_CROSSING_DLG,
        enabled_when=Enablement(
            allowed_states=_RUNNING_REOPENED, min_crossings=1
        ),  # "RUNNING · REOPENED, ≥1 crossing"
    ),
    MenuRoute(
        menu="Cards",
        label="Reassign Plate…",
        ids=("mi_reassign_plate",),
        kind=TargetKind.DIALOG,
        target=ids.REASSIGN_DLG,
        enabled_when=Enablement(
            allowed_states=_RUNNING_REOPENED, min_crossings=1
        ),  # "RUNNING · REOPENED, ≥1 crossing"
    ),
    MenuRoute(
        menu="Cards",
        label="Deal Manual Card…",
        ids=("mi_deal_manual",),
        kind=TargetKind.DIALOG,
        target=ids.MANUAL_DEAL_DLG,
        enabled_when=Enablement(allowed_states=_RUNNING_REOPENED),  # "RUNNING · REOPENED"
    ),
    MenuRoute(
        menu="Cards",
        label="Void Card…",
        ids=("mi_void_card",),
        kind=TargetKind.DIALOG,
        target=ids.VOID_CARD_CONFIRM_DLG,
        enabled_when=Enablement(
            allowed_states=_RUNNING_REOPENED, requires_entry_has_cards=True
        ),  # "RUNNING · REOPENED, entry has cards"
    ),
    MenuRoute(
        menu="Cards",
        label="Review Held Cards",
        ids=("mi_review_held",),
        # "Focuses console review panel" -- no window/dialog opens.
        kind=TargetKind.COMMAND,
        target="focus_review_panel",
        enabled_when=Enablement(min_held_cards=1),  # "held cards > 0 (shows count)"
    ),
    # --- Results: 6 rows ---
    MenuRoute(
        menu="Results",
        label="Standings",
        ids=("mi_standings",),
        kind=TargetKind.DIALOG,
        target=ids.RESULTS_DLG,
        enabled_when=Enablement(requires_ride_open=True),  # "ride open (live while running)"
    ),
    MenuRoute(
        menu="Results",
        label="Generate HTML…",
        ids=("mi_export_html",),
        kind=TargetKind.COMMAND,  # OS-native save dialog -- no app window
        target="export_html",
        enabled_when=Enablement(allowed_states=_FINISHED),  # "FINISHED"
    ),
    MenuRoute(
        menu="Results",
        label="Export PDF…",
        ids=("mi_export_pdf",),
        kind=TargetKind.COMMAND,  # OS-native save dialog -- no app window
        target="export_pdf",
        enabled_when=Enablement(allowed_states=_FINISHED),  # "FINISHED"
    ),
    MenuRoute(
        menu="Results",
        label="Podium Poster PDF…",
        ids=("mi_export_poster",),
        kind=TargetKind.COMMAND,  # OS-native save dialog -- no app window
        target="export_poster",
        enabled_when=Enablement(allowed_states=_FINISHED),  # "FINISHED"
    ),
    MenuRoute(
        menu="Results",
        label="Export Standings CSV…",
        ids=("mi_export_results_csv",),
        kind=TargetKind.COMMAND,  # OS-native save dialog -- no app window
        target="export_results_csv",
        enabled_when=Enablement(allowed_states=_FINISHED),  # "FINISHED"
    ),
    MenuRoute(
        menu="Results",
        label="Preview in Browser",
        ids=("mi_preview_browser",),
        kind=TargetKind.COMMAND,  # opens the external, OS-default browser
        target="preview_in_browser",
        # "an export exists", and only for a FINISHED ride -- Part D
        # gates every Results export on FINISHED.
        enabled_when=Enablement(allowed_states=_FINISHED, requires_export_exists=True),
    ),
    # --- View: 1 row, 8 ids (W13: the theme trio left the View menu;
    # the Settings appearance radios are the single theme surface) ---
    MenuRoute(
        menu="View",
        label="Hide Times · Zoom",
        ids=(
            "mi_hide_times",
            "mi_zoom_90",
            "mi_zoom_100",
            "mi_zoom_110",
            "mi_zoom_120",
            "mi_zoom_130",
            "mi_zoom_140",
            "mi_zoom_150",
        ),
        kind=TargetKind.COMMAND,  # "Direct commands"
        target="view_setting",
        enabled_when=ALWAYS,  # "always"
    ),
    # --- Help: 4 rows ---
    MenuRoute(
        menu="Help",
        label="User Guide",
        ids=("mi_user_guide",),
        kind=TargetKind.COMMAND,  # opens the external, OS-default browser
        target="open_user_guide",
        enabled_when=ALWAYS,  # "always"
    ),
    MenuRoute(
        menu="Help",
        label="Keyboard Shortcuts",
        ids=("mi_shortcuts",),
        kind=TargetKind.DIALOG,
        target=ids.SHORTCUTS_DLG,
        enabled_when=ALWAYS,  # "always"
    ),
    MenuRoute(
        menu="Help",
        label="Run Evaluator Self-test",
        ids=("mi_selftest",),
        kind=TargetKind.DIALOG,
        target=ids.SELFTEST_DLG,
        enabled_when=ALWAYS,  # "always"
    ),
    MenuRoute(
        menu="Help",
        label="About RiverCrossing",
        ids=("wxID_ABOUT",),
        kind=TargetKind.DIALOG,
        target=ids.ABOUT_DLG,
        enabled_when=ALWAYS,  # "always"
    ),
)

_ROUTES_BY_ID: dict[str, MenuRoute] = {
    item_id: route for route in ROUTE_TABLE for item_id in route.ids
}


class UnroutedMenuItemError(LookupError):
    """Raised when a menu item id has no :data:`ROUTE_TABLE` entry."""


def route_for_id(item_id: str) -> MenuRoute:
    """Return the :class:`MenuRoute` that *item_id* fires.

    Raises:
        UnroutedMenuItemError: If *item_id* has no entry in
            :data:`ROUTE_TABLE` -- the failure R-73's coverage walk
            exists to catch as EPICs 2-9 add routes.
    """
    try:
        return _ROUTES_BY_ID[item_id]
    except KeyError as exc:
        raise UnroutedMenuItemError(f"no route registered for menu item id {item_id!r}") from exc


@dataclass(frozen=True)
class RideState:
    """The ride-lifecycle facts an :class:`Enablement` rule reads.

    Stands in for the real ride engine, which does not exist before
    EPIC 4 (module-skeletons.md's stub/hand-off table): every field
    here is a condition spec.md section 15's "Enabled when" column
    names explicitly, never collapsed into ``status`` alone.

    Attributes:
        status: The ride's ``RideStatus``.
        ride_open: Whether a ride is loaded at all -- true for every
            member of ``RideStatus``; false only when none is open.
        ride_stopped: Start Ride's "stopped RUNNING" condition -- the
            console Stop button's confirm has locked entry without
            finishing the ride.
        crossings: How many crossings the open ride has recorded.
        held_cards: How many cards are held (short-lap, unconfirmed).
        audit_rows: How many audit rows the open ride has.
        entry_has_cards: Whether the entry Void Card targets holds
            any cards.
        export_exists: Whether a results export has been written.
        teams_allowed: Whether the open ride's entry_mode is mixed
            (teams exist to edit).
    """

    status: RideStatus
    ride_open: bool = True
    ride_stopped: bool = False
    crossings: int = 0
    held_cards: int = 0
    audit_rows: int = 0
    entry_has_cards: bool = False
    export_exists: bool = False
    teams_allowed: bool = False


def is_route_enabled(route: MenuRoute, state: RideState) -> bool:
    """Return whether *route* is enabled for *state* (§15, R-36)."""
    rule = route.enabled_when
    if rule.allowed_states is not None and state.status not in rule.allowed_states:
        return False
    stop_ok = not (
        rule.requires_ride_stopped
        and state.status == RideStatus.RUNNING
        and not state.ride_stopped
    )
    return (
        stop_ok
        and (not rule.requires_ride_open or state.ride_open)
        and (not rule.teams_allowed or state.teams_allowed)
        and (not rule.requires_entry_has_cards or state.entry_has_cards)
        and (not rule.requires_export_exists or state.export_exists)
        and state.crossings >= rule.min_crossings
        and state.held_cards >= rule.min_held_cards
        and state.audit_rows >= rule.min_audit_rows
    )
