# SPDX-License-Identifier: GPL-3.0-only
"""wx-side runners for the E7 correction dialogs (section C).

The six correction dialogs -- ``edit_crossing_dlg`` (add + edit modes),
``reassign_dlg``, ``manual_deal_dlg``, ``dnf_confirm_dlg`` and
``void_card_confirm_dlg`` -- are shared by two entry points: the
Cards/Riders menu routes (app.py's handlers) and the entry-detail
dialog's action buttons (``EntryDetailDialog``'s ``DetailView``
implementation). Each ``run_*`` function is that shared wiring, in the
``_open_ride_confirm`` shape: load the dialog from the resource,
prefill / write its named labels (a blank label is a failed assertion,
never cosmetic -- UX-DESKTOP §4), show it through
:func:`~rivercrossing.ui.views.dialogs.run_dialog` -- the one seam
every dialog in this codebase shows through -- and return the
confirmed submission as a wx-free request dataclass (or ``None`` on
cancel). The caller (the presenter or the app handler) performs the
engine command, so this module never touches a ``RideEngine``.

Every form/confirm requires a non-empty ``reason``: the OK handler
keeps the dialog open and refocuses ``reason_input`` when it is blank
(``_bind_reason_gate``), so the engine's own empty-reason refusal is
never the first line of defence in the UI.

The move-rider "team picker" has no XRC dialog (spec §15b authors
none); :func:`run_move_rider` builds a small native picker in code.

The audit button and the Cards/Riders menu row both open ``audit_dlg``
through :func:`run_audit`, which E7.3.1 made real: it binds the
:class:`~rivercrossing.ui.views.audit.AuditDialog` view + presenter
(the viewer, R-38) before showing it. The entry-detail deep-link
passes its plate as *entry_filter* so the dialog opens pre-filtered to
that entry; the menu route passes the live engine source and roster
with no filter.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any

from rivercrossing.ui import ids, require_wx
from rivercrossing.ui.presenters.detail import (
    CardVoid,
    CrossingEdit,
    DnfMark,
    ManualDeal,
    RiderMove,
)
from rivercrossing.ui.views import dialogs

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.roster import Roster
    from rivercrossing.ui.presenters.data_source import DataSource

__all__ = [
    "ReassignRequest",
    "run_audit",
    "run_dnf",
    "run_edit_crossing",
    "run_manual_deal",
    "run_move_rider",
    "run_reassign",
    "run_void_card",
]

wx = require_wx()


@dataclass(frozen=True, slots=True)
class ReassignRequest:
    """One confirmed ``reassign_dlg`` submission (E7.2.1)."""

    new_plate: str
    reason: str


def _find(dialog: Any, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return the control named *name* inside *dialog*, or None."""
    return wx.Window.FindWindowByName(name, dialog)


def _parse_time_text(text: str) -> tuple[int, int, int]:
    """Parse ``HH:MM:SS`` into (hour, minute, second)."""
    hour, minute, second = (int(part) for part in text.split(":"))
    return hour, minute, second


def _set_time_picker(time_picker: Any, text: str) -> None:  # noqa: ANN401
    """Set *time_picker* to the ``HH:MM:SS`` *text* value."""
    hour, minute, second = _parse_time_text(text)
    stamp = wx.DateTime()
    stamp.SetHMS(hour, minute, second)
    time_picker.SetValue(stamp)


def _picked_time(time_picker: Any, base_date: date) -> datetime:  # noqa: ANN401
    """Return the picker's time-of-day combined onto *base_date*."""
    picked = time_picker.GetValue()
    return datetime.combine(
        base_date, time(picked.GetHour(), picked.GetMinute(), picked.GetSecond())
    )


def _bind_reason_gate(reason_input: Any) -> Callable[[], bool]:  # noqa: ANN401 -- wx ships no stubs
    """Return a gate: True when reason is non-empty, else refocus.

    The returned callable decides whether an OK/Void click may close
    the dialog: an empty or whitespace-only ``reason_input`` keeps the
    dialog open (the handler returns without ``event.Skip()``, so
    wx's stock-OK auto-close never fires -- the measured contract
    ``RideSetup._on_ok`` relies on) and refocuses the reason field so
    the operator sees why nothing happened.
    """

    def _reason_present() -> bool:
        if reason_input.GetValue().strip():
            return True
        reason_input.SetFocus()
        return False

    return _reason_present


def _bind_ok(dialog: Any, gate: Callable[[], bool], on_ok: Callable[[], None]) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Bind ``wxID_OK`` so a gated OK commits and closes, else stays."""
    ok_button = _find(dialog, "wxID_OK")

    def _handler(_event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        if not gate():
            return
        on_ok()
        dialog.EndModal(wx.ID_OK)

    ok_button.Bind(wx.EVT_BUTTON, _handler)


def _apply_recorded_defaults(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Apply *dialog*'s own E1.5.3 default-button/first-field.

    The generic ``_open_target`` path applies these through
    ``app._apply_dialog_defaults``; the correction runners are its
    second entry point (menu routes + entry-detail buttons), so they
    apply the same recorded decision. A dialog with no entry in either
    table is a no-op (its XRC already declares its ``<default>``).
    """
    dialog_name = dialog.GetName()
    default_button = dialogs.default_button_for(dialog_name)
    if default_button is not None:
        dialogs.set_default_button(dialog, default_button)
    first_field = dialogs.first_field_for(dialog_name)
    if first_field is not None:
        dialogs.set_initial_focus(dialog, first_field)


def _run_dialog(dialog: Any, frame: Any) -> int:  # noqa: ANN401 -- wx ships no stubs
    """Show *dialog* through the one seam, recorded defaults applied."""
    _apply_recorded_defaults(dialog)
    return dialogs.run_dialog(dialog, opener=frame)


def run_edit_crossing(  # noqa: PLR0913 -- (resource, frame, adding, plate, time, seq, base_date)
    resource: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    frame: Any,  # noqa: ANN401 -- wx ships no stubs
    adding: bool,
    plate: str,
    time: str,
    seq: int | None = None,
    base_date: date | None = None,
) -> CrossingEdit | None:
    """Open ``edit_crossing_dlg``; return the confirmed edit, or None.

    ``adding`` False titles it "Edit Crossing" and shows ``void_btn``
    (a confirmed void returns ``CrossingEdit(void=True)``); True titles
    it "Add Crossing at Time" and hides ``void_btn``. *plate* prefills
    ``plate_input``, *time* (``HH:MM:SS``) prefills ``time_picker``,
    and the operator supplies the required reason. *seq* is the
    crossing identity the caller already knows (the selected lap);
    ``None`` leaves it to the caller to resolve (the menu flow).

    Returns:
        The confirmed submission, or ``None`` on cancel.
    """
    dialog = resource.LoadDialog(None, ids.EDIT_CROSSING_DLG)
    if dialog is None:
        return None
    try:
        dialog.SetTitle("Add Crossing at Time" if adding else "Edit Crossing")
        plate_input = _find(dialog, ids.PLATE_INPUT)
        time_picker = _find(dialog, ids.TIME_PICKER)
        reason_input = _find(dialog, ids.REASON_INPUT)
        void_btn = _find(dialog, ids.VOID_BTN)
        plate_input.SetValue(plate)
        _set_time_picker(time_picker, time)
        void_btn.Show(not adding)
        void_btn.Enable(not adding)
        base = base_date if base_date is not None else datetime.now(UTC).date()
        confirmed: CrossingEdit | None = None

        def _commit_edit() -> None:
            nonlocal confirmed
            confirmed = CrossingEdit(
                entry_id=plate_input.GetValue().strip(),
                seq=seq,
                crossed_at=_picked_time(time_picker, base),
                reason=reason_input.GetValue().strip(),
            )

        def _commit_void() -> None:
            nonlocal confirmed
            confirmed = CrossingEdit(
                entry_id=plate_input.GetValue().strip(),
                seq=seq,
                crossed_at=None,
                reason=reason_input.GetValue().strip(),
                void=True,
            )

        gate = _bind_reason_gate(reason_input)
        _bind_ok(dialog, gate, _commit_edit)
        if not adding:

            def _on_void(_event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
                if gate():
                    _commit_void()
                    dialog.EndModal(wx.ID_OK)

            void_btn.Bind(wx.EVT_BUTTON, _on_void)
        result = _run_dialog(dialog, frame)
        return confirmed if result == wx.ID_OK else None
    finally:
        if not dialog.IsBeingDeleted():
            dialog.Destroy()


def run_manual_deal(
    resource: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    frame: Any,  # noqa: ANN401 -- wx ships no stubs
    plate: str,
) -> ManualDeal | None:
    """Open ``manual_deal_dlg``; return the confirmed deal, or None."""
    dialog = resource.LoadDialog(None, ids.MANUAL_DEAL_DLG)
    if dialog is None:
        return None
    try:
        plate_input = _find(dialog, ids.PLATE_INPUT)
        reason_input = _find(dialog, ids.REASON_INPUT)
        plate_input.SetValue(plate)
        confirmed: ManualDeal | None = None

        def _commit() -> None:
            nonlocal confirmed
            confirmed = ManualDeal(
                plate=plate_input.GetValue().strip(),
                reason=reason_input.GetValue().strip(),
            )

        _bind_ok(dialog, _bind_reason_gate(reason_input), _commit)
        result = _run_dialog(dialog, frame)
        return confirmed if result == wx.ID_OK else None
    finally:
        if not dialog.IsBeingDeleted():
            dialog.Destroy()


def run_void_card(  # noqa: PLR0913 -- (resource, frame, entry_id, card, entry)
    resource: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    frame: Any,  # noqa: ANN401 -- wx ships no stubs
    entry_id: str,
    card: str,
    entry: str,
) -> CardVoid | None:
    """Open ``void_card_confirm_dlg``; return the confirmed void.

    Writes ``card_lbl`` naming the card + entry (never blank --
    ``dialogs.void_card_message``), so the operator sees exactly which
    dealt card they are about to void.
    """
    dialog = resource.LoadDialog(None, ids.VOID_CARD_CONFIRM_DLG)
    if dialog is None:
        return None
    try:
        card_lbl = _find(dialog, ids.CARD_LBL)
        reason_input = _find(dialog, ids.REASON_INPUT)
        card_lbl.SetLabel(dialogs.void_card_message(card, entry))
        confirmed: CardVoid | None = None

        def _commit() -> None:
            nonlocal confirmed
            confirmed = CardVoid(
                entry_id=entry_id,
                card=card,
                reason=reason_input.GetValue().strip(),
            )

        _bind_ok(dialog, _bind_reason_gate(reason_input), _commit)
        result = _run_dialog(dialog, frame)
        return confirmed if result == wx.ID_OK else None
    finally:
        if not dialog.IsBeingDeleted():
            dialog.Destroy()


def run_dnf(  # noqa: PLR0913 -- (resource, frame, entry_id, entry)
    resource: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    frame: Any,  # noqa: ANN401 -- wx ships no stubs
    entry_id: str,
    entry: str,
) -> DnfMark | None:
    """Open ``dnf_confirm_dlg``; return the confirmed DNF mark.

    Writes ``entry_lbl`` naming the entry (never blank --
    ``dialogs.dnf_message``).
    """
    dialog = resource.LoadDialog(None, ids.DNF_CONFIRM_DLG)
    if dialog is None:
        return None
    try:
        entry_lbl = _find(dialog, ids.ENTRY_LBL)
        reason_input = _find(dialog, ids.REASON_INPUT)
        entry_lbl.SetLabel(entry)
        confirmed: DnfMark | None = None

        def _commit() -> None:
            nonlocal confirmed
            confirmed = DnfMark(entry_id=entry_id, reason=reason_input.GetValue().strip())

        _bind_ok(dialog, _bind_reason_gate(reason_input), _commit)
        result = _run_dialog(dialog, frame)
        return confirmed if result == wx.ID_OK else None
    finally:
        if not dialog.IsBeingDeleted():
            dialog.Destroy()


def run_reassign(
    resource: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    frame: Any,  # noqa: ANN401 -- wx ships no stubs
    crossing_label: str,
) -> ReassignRequest | None:
    """Open ``reassign_dlg``; return the confirmed reassign, or None.

    Writes ``crossing_lbl`` naming the crossing being reassigned
    (never blank -- ``dialogs.reassign_message``).
    """
    dialog = resource.LoadDialog(None, ids.REASSIGN_DLG)
    if dialog is None:
        return None
    try:
        crossing_lbl = _find(dialog, ids.CROSSING_LBL)
        new_plate_input = _find(dialog, ids.NEW_PLATE_INPUT)
        reason_input = _find(dialog, ids.REASON_INPUT)
        crossing_lbl.SetLabel(crossing_label)
        confirmed: ReassignRequest | None = None

        def _commit() -> None:
            nonlocal confirmed
            confirmed = ReassignRequest(
                new_plate=new_plate_input.GetValue().strip(),
                reason=reason_input.GetValue().strip(),
            )

        _bind_ok(dialog, _bind_reason_gate(reason_input), _commit)
        result = _run_dialog(dialog, frame)
        return confirmed if result == wx.ID_OK else None
    finally:
        if not dialog.IsBeingDeleted():
            dialog.Destroy()


def run_move_rider(
    frame: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    riders: tuple[str, ...],
    teams: tuple[str, ...],
) -> RiderMove | None:
    """Open the code-built team picker; return the confirmed move.

    Two native single-choice dialogs (which rider, then which
    destination team); a cancel at either step is a silent no-op.
    """
    if not riders or not teams:
        return None
    rider_choice = wx.SingleChoiceDialog(frame, "Move which rider?", "Move Rider", list(riders))
    try:
        if rider_choice.ShowModal() != wx.ID_OK:
            return None
        rider_plate = rider_choice.GetStringSelection()
    finally:
        rider_choice.Destroy()
    team_choice = wx.SingleChoiceDialog(frame, "Move to which team?", "Move Rider", list(teams))
    try:
        if team_choice.ShowModal() != wx.ID_OK:
            return None
        to_team = team_choice.GetStringSelection()
    finally:
        team_choice.Destroy()
    return RiderMove(rider_plate=rider_plate, to_team=to_team)


def run_audit(  # noqa: PLR0913 -- (resource, frame) + the viewer's data seams
    resource: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    frame: Any,  # noqa: ANN401 -- wx ships no stubs
    data_source: DataSource,
    roster: Roster | None = None,
    entry_filter: str = "",
) -> None:
    """Open ``audit_dlg`` bound to the E7.3.1 viewer (R-38).

    Builds the :class:`~rivercrossing.ui.views.audit.AuditDialog`
    view + presenter -- which fills ``audit_list`` newest-first, wires
    ``audit_search``/``action_choice`` to the two filters, and
    pre-fills the search when deep-linked from entry detail
    (*entry_filter* = the entry's plate) -- then shows it through the
    one dialog seam. The caller supplies the live display source and
    roster; the entry-detail dialog passes its own, the app menu route
    passes the console's.
    """
    from rivercrossing.ui.views.audit import AuditDialog  # noqa: PLC0415

    dialog = resource.LoadDialog(None, ids.AUDIT_DLG)
    if dialog is None:
        return
    try:
        AuditDialog(
            dialog,
            data_source=data_source,
            roster=roster,
            entry_filter=entry_filter,
        )
        dialogs.run_dialog(dialog, opener=frame)
    finally:
        if not dialog.IsBeingDeleted():
            dialog.Destroy()
