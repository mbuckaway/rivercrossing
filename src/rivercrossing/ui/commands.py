# SPDX-License-Identifier: GPL-3.0-only
"""The menu route map and its state-enablement rules (E1.4.1, E1.4.2).

spec.md section 15 is one table with two jobs: which target each of
the 38 menu rows reaches ("Opens / does"), and when it is allowed to
fire ("Enabled when"). :data:`ROUTE_TABLE` is that table transcribed
once, so both jobs read off the same 38 :class:`MenuRoute` rows
instead of two tables that could drift apart.

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
    "is_stop_button_enabled",
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


# spec.md section 15 names a dialog for these three rows -- Duplicate
# Ride, Reopen Ride, Void Card -- that xrc-windows.md's 23 frozen
# windows do not implement (all three rows' own "Opens / does" text
# says "(3d pattern)" or "Duplicate-ride dialog", never a name that
# matches an authored window). This sentinel cannot be mistaken for a
# real ``ui/ids.py`` constant; see the E1.4.1 report for the gap.
_UNAUTHORED_DIALOG = "UNAUTHORED-DIALOG: no frozen XRC window covers this §15 target yet"

_RUNNING = frozenset({RideStatus.RUNNING})
_RUNNING_REOPENED = frozenset({RideStatus.RUNNING, RideStatus.REOPENED})
_FINISHED = frozenset({RideStatus.FINISHED})
_DRAFT_OR_RUNNING = frozenset({RideStatus.DRAFT, RideStatus.RUNNING})


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
            row; the View row's single "Theme · Hide Times · Zoom"
            entry covers all eleven of its radio/check items, since
            §15 itself groups them into one row.
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
    MenuRoute(
        menu="File",
        label="New Ride…",
        ids=("mi_new_ride",),
        kind=TargetKind.WINDOW,
        target=ids.RIDE_SETUP_DLG,
        enabled_when=ALWAYS,  # "always"
    ),
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
        kind=TargetKind.DIALOG,
        target=_UNAUTHORED_DIALOG,
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
        kind=TargetKind.COMMAND,  # OS-native save dialog -- no app window
        target="backup_database",
        enabled_when=ALWAYS,  # "always"
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
        # Branches on ride state (RUNNING -> exit_running_dlg; otherwise
        # quits directly) rather than reaching one fixed target, so this
        # is the flow itself, not a single window/dialog.
        kind=TargetKind.COMMAND,
        target="exit_or_quit",
        enabled_when=ALWAYS,  # "always"
    ),
    # --- Ride: 7 rows ---
    MenuRoute(
        menu="Ride",
        label="Start Ride",
        ids=("mi_start_ride",),
        # Branches on existing data (-> continue_or_new_dlg) rather than
        # reaching one fixed target; the common case starts directly.
        kind=TargetKind.COMMAND,
        target="start_ride",
        enabled_when=Enablement(
            allowed_states=_DRAFT_OR_RUNNING, requires_ride_stopped=True
        ),  # "DRAFT, or stopped RUNNING"
    ),
    MenuRoute(
        menu="Ride",
        label="Stop Ride…",
        ids=("mi_stop_ride",),
        kind=TargetKind.DIALOG,
        target=ids.STOP_CONFIRM_DLG,
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
        kind=TargetKind.DIALOG,
        target=ids.FINISH_CONFIRM_DLG,
        enabled_when=Enablement(allowed_states=_RUNNING_REOPENED),  # "RUNNING · REOPENED"
    ),
    MenuRoute(
        menu="Ride",
        label="Reopen Ride",
        ids=("mi_reopen_ride",),
        kind=TargetKind.DIALOG,
        target=_UNAUTHORED_DIALOG,
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
        label="Ride Setup…",
        ids=("mi_ride_setup",),
        kind=TargetKind.WINDOW,
        target=ids.RIDE_SETUP_DLG,
        # spec.md §15: ride open (locks tighten after start)
        enabled_when=Enablement(requires_ride_open=True),
    ),
    # --- Riders: 4 rows ---
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
        label="Add Rider/Entry…",
        ids=("mi_add_entry",),
        kind=TargetKind.WINDOW,
        target=ids.RIDER_EDITOR_DLG,
        enabled_when=Enablement(requires_ride_open=True),  # "ride open (new plates any time)"
    ),
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
        target=_UNAUTHORED_DIALOG,
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
    # --- Results: 7 rows ---
    MenuRoute(
        menu="Results",
        label="Standings",
        ids=("mi_standings",),
        kind=TargetKind.WINDOW,
        target=ids.RESULTS_FRAME,
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
        enabled_when=Enablement(requires_export_exists=True),  # "an export exists"
    ),
    MenuRoute(
        menu="Results",
        label="Tie-break Order…",
        ids=("mi_tiebreak_order",),
        # "Focuses the tie-break control in Results" -- no new window.
        kind=TargetKind.COMMAND,
        target="focus_tiebreak_control",
        enabled_when=Enablement(requires_ride_open=True),  # "ride open"
    ),
    # --- View: 1 row, 11 ids ---
    MenuRoute(
        menu="View",
        label="Theme · Hide Times · Zoom",
        ids=(
            "mi_theme_system",
            "mi_theme_light",
            "mi_theme_dark",
            "mi_hide_times",
            "mi_zoom_90",
            "mi_zoom_100",
            "mi_zoom_110",
            "mi_zoom_120",
            "mi_zoom_130",
            "mi_zoom_140",
            "mi_zoom_150",
        ),
        kind=TargetKind.COMMAND,  # "Direct commands ... mirrored in Settings"
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
    """

    status: RideStatus
    ride_open: bool = True
    ride_stopped: bool = False
    crossings: int = 0
    held_cards: int = 0
    audit_rows: int = 0
    entry_has_cards: bool = False
    export_exists: bool = False


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
        and (not rule.requires_entry_has_cards or state.entry_has_cards)
        and (not rule.requires_export_exists or state.export_exists)
        and state.crossings >= rule.min_crossings
        and state.held_cards >= rule.min_held_cards
        and state.audit_rows >= rule.min_audit_rows
    )


def is_stop_button_enabled(*, armed: bool) -> bool:
    """Return whether the console Stop button is enabled (R-35).

    ``stop_btn`` is a button, not a menu item, and R-35 gates it on
    nothing but the ``arm_stop_chk`` checkbox beside it -- a separate
    rule from any :class:`MenuRoute`, kept here because it is the
    same enablement-binder concern this module owns.
    """
    return armed
