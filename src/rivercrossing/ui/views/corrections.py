# SPDX-License-Identifier: GPL-3.0-only
"""wx-side runners for the E7 correction dialogs (section C).

The four correction dialogs -- ``edit_crossing_dlg`` (add + edit
modes), ``manual_deal_dlg``, ``dnf_confirm_dlg`` and
``void_card_confirm_dlg`` -- are shared by two entry points: the
Cards/Riders menu routes (app.py's handlers) and Crossing Detail's own
``edit_time_btn``/``void_card_btn`` corrections. Each ``run_*``
function is that shared wiring, in the ``_open_ride_confirm`` shape:
load the dialog from the resource, prefill / write its named labels (a
blank label is a failed assertion, never cosmetic -- UX-DESKTOP §4),
show it through :func:`~rivercrossing.ui.views.dialogs.run_dialog` --
the one seam every dialog in this codebase shows through -- and return
the confirmed submission as a wx-free request dataclass (or ``None`` on
cancel). The request dataclasses themselves live here, beside the
runners that build them; the caller performs the engine command, so
this module never touches a ``RideEngine``.
ux-polish's Ride ▸ Set Start Time… row runs through the same runner
shape here (:func:`run_set_start_time`, section B's ``set_start_dlg``
-- a form dialog whose route handler applies the confirmed instant to
the engine afterwards, exactly like the section-C rows).

Every form/confirm requires a non-empty ``reason``: the OK handler
keeps the dialog open and refocuses ``reason_input`` when it is blank
(``_bind_reason_gate``, or ``_bind_plate_gate`` for the DNF form,
which also writes the refusal line), so the engine's own empty-reason
refusal is never the first line of defence in the UI.
``void_card_confirm_dlg`` also shows that gate up front -- its OK
starts disabled and enables on the first non-blank ``wx.EVT_TEXT``
(``_bind_reason_enable``).
``dnf_confirm_dlg`` gates on its ``plate_input`` as well
(``_bind_plate_gate``) -- its target is typed in, and the engine's own
unknown-plate refusal must never be the first line of defence either.
That gate needs a *real* target, not merely a non-blank one: the plate
has to be digits (``_is_plate``; ``_bind_digits_only`` filters the
keystrokes that get there) and resolve through the caller's roster
(``Roster.resolve_plate``). Every refusal keeps the dialog open, focuses
the offending field and says why on the dialog's own inline
``entry_lbl`` -- the consequence line directly under the plate field.

The move-rider "team picker" has no XRC dialog (spec §15b authors
none); :func:`run_move_rider` builds a small native picker in code.

The Cards/Riders menu row opens ``audit_dlg`` through
:func:`run_audit`, which E7.3.1 made real: it binds the
:class:`~rivercrossing.ui.views.audit.AuditDialog` view + presenter
(the viewer, R-38) before showing it, over the live engine source and
roster with no filter.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any

import wx.adv as _wx_adv  # submodule, not loaded by plain `import wx`

from rivercrossing.ui import ids, require_wx
from rivercrossing.ui.card_text import format_card
from rivercrossing.ui.views import dialogs
from rivercrossing.ui.views._support import find_control, load_dialog

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.roster import Roster
    from rivercrossing.ui.presenters.data_source import DataSource

__all__ = [
    "CardVoid",
    "CrossingEdit",
    "DnfMark",
    "ManualDeal",
    "RiderMove",
    "run_audit",
    "run_dnf",
    "run_edit_crossing",
    "run_manual_deal",
    "run_move_rider",
    "run_set_start_time",
    "run_void_card",
]

wx = require_wx()


@dataclass(frozen=True, slots=True)
class CrossingEdit:
    """One confirmed ``edit_crossing_dlg`` submission (E7.2.1).

    ``entry_id`` is the confirmed plate (the operator may have changed
    the prefill), ``seq`` the crossing's 1-based lap number within the
    entry (or ``None`` when the caller resolves it from the engine's
    latest crossing -- the menu flow), ``crossed_at`` the confirmed
    instant, ``reason`` the audit reason, and ``void`` True when the
    operator chose the dialog's ``void_btn`` instead of Save.
    """

    entry_id: str
    seq: int | None
    crossed_at: datetime | None
    reason: str
    void: bool = False


@dataclass(frozen=True, slots=True)
class ManualDeal:
    """One confirmed ``manual_deal_dlg`` submission (E7.2.1)."""

    plate: str
    reason: str


@dataclass(frozen=True, slots=True)
class CardVoid:
    """One confirmed ``void_card_confirm_dlg`` submission (E7.2.1).

    ``card`` is the dealt card's code (``Card.code()``); the caller
    still names the engine's own dealt object when it calls
    ``void_card``, never a parsed copy of the code -- the registry and
    the hold guard key by identity.
    """

    entry_id: str
    card: str
    reason: str


@dataclass(frozen=True, slots=True)
class DnfMark:
    """One confirmed ``dnf_confirm_dlg`` submission (E7.2.1).

    ``plate`` is the digits-only plate the operator typed into the
    dialog's ``plate_input`` -- a pooled rider's own plate, or a whole
    entry's plate, resolved through the roster before OK closes (the
    engine's ``mark_dnf`` re-resolves the scope from that plate; the
    dialog never decides which is which). ``reason`` is the confirmed
    reason.
    """

    plate: str
    reason: str


@dataclass(frozen=True, slots=True)
class RiderMove:
    """One confirmed move-rider picker submission (E7.2.1).

    ``rider_plate`` names the rider being moved (a ``Rider.plate`` on
    a rider_pooled team); ``to_team`` names the destination entry by
    ``display_name``. The caller resolves both through the roster
    before calling :meth:`Roster.move_rider`.
    """

    rider_plate: str
    to_team: str


# The address-reuse poison (views/_support.find_control's docstring)
# wraps an XRC control in the wrong Python type (a generic
# ``wx.Control`` whose ``SetValue`` does not exist); the settle-retry
# only catches it with the right expected_type, so every lookup here
# names its wx class -- the same convention RideSetup and every other
# view use. A raw ``wx.Window.FindWindowByName`` skips the check.
_CONTROL_EXPECTED_TYPES: dict[str, type] = {
    ids.PLATE_INPUT: wx.TextCtrl,
    ids.REASON_INPUT: wx.TextCtrl,
    ids.TIME_PICKER: _wx_adv.TimePickerCtrl,
    ids.VOID_BTN: wx.Button,
    ids.CARD_LBL: wx.StaticText,
    ids.ENTRY_LBL: wx.StaticText,
    ids.START_DATE_PICKER: _wx_adv.DatePickerCtrl,
    ids.START_TIME_PICKER: _wx_adv.TimePickerCtrl,
}


def _find(dialog: Any, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return the control named *name* inside *dialog*.

    Forwards to :func:`views._support.find_control` so the
    address-reuse settle-retry runs against the control's expected wx
    type; a missing control raises ``LookupError`` naming the window
    and its first-level children, never a silent ``None``.
    """
    return find_control(dialog, name, _CONTROL_EXPECTED_TYPES.get(name, wx.Window))


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


# A plate's alphabet: ASCII digits only. ``str.isdigit`` would also take
# "²" and the Arabic-Indic digits -- characters no roster lookup could
# answer -- so the check is pinned to what a plate is made of.
def _is_plate(text: str) -> bool:
    """Return whether *text* is a usable plate: digits, nothing else."""
    return text.isascii() and text.isdigit()


# wx reports the editing keys -- Backspace, Tab, Escape, the arrows --
# as key codes below the space character, and Delete above it; both must
# still reach the field. Digits arrive as their own ASCII key codes
# ("0".."9"), compared as codes so a platform reporting something
# outside the Unicode range can never raise inside a handler.
_CONTROL_KEY_LIMIT = 32
_FIRST_DIGIT_KEY = ord("0")
_LAST_DIGIT_KEY = ord("9")


def _plate_key_allowed(key_code: int) -> bool:
    """Return whether ``plate_input`` may accept the typed *key_code*.

    Digits are the plate's whole alphabet; the control keys that edit
    and leave the field (and Delete, which wx reports above the space
    character) pass too, so the operator is never trapped in it.
    """
    return (
        key_code < _CONTROL_KEY_LIMIT
        or key_code == wx.WXK_DELETE
        or _FIRST_DIGIT_KEY <= key_code <= _LAST_DIGIT_KEY
    )


def _bind_reason_enable(dialog: Any, reason_input: Any) -> None:  # noqa: ANN401
    """Start OK disabled and enable it when the reason is non-blank.

    ``void_card_confirm_dlg`` is a scoring change: the operator must not
    be able to click "Void card" before saying why. The button starts
    disabled and ``wx.EVT_TEXT`` re-enables it the moment the reason box
    holds non-blank text -- the visual half of
    :func:`_bind_reason_gate`'s refusal, which still guards the click.
    """
    ok_button = _find(dialog, "wxID_OK")
    ok_button.Enable(False)  # noqa: FBT003 -- wx API takes a positional bool

    def _on_text(_event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        ok_button.Enable(bool(reason_input.GetValue().strip()))

    reason_input.Bind(wx.EVT_TEXT, _on_text)


# wx ships no stubs
def _bind_reason_gate(reason_input: Any) -> Callable[[], bool]:  # noqa: ANN401
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


def _bind_plate_gate(  # noqa: PLR0913, PLR0917 -- (plate, reason, label, resolve): the form's own parts
    plate_input: Any,  # noqa: ANN401 -- wx ships no stubs
    reason_input: Any,  # noqa: ANN401 -- wx ships no stubs
    entry_lbl: Any,  # noqa: ANN401 -- wx ships no stubs
    resolve: Callable[[str], object | None],
) -> Callable[[], bool]:
    """Return a gate requiring a real target AND a reason.

    ``dnf_confirm_dlg`` is the one correction form whose target is
    typed in, so its OK gate covers three things: the plate must be
    digits (:func:`_is_plate` -- the ``wx.EVT_CHAR`` filter cannot see
    a prefilled or pasted value), it must resolve through *resolve*
    (``Roster.resolve_plate``), and the reason must be non-blank -- the
    same rule the other forms get from :func:`_bind_reason_gate`. Every
    refusal keeps the dialog open (the handler returns without
    ``event.Skip()``, so wx's stock-OK auto-close never fires), lands
    focus on the offending field, and writes why onto ``entry_lbl`` --
    the inline line directly under the plate field -- so neither a
    mistyped plate nor a blank reason can reach the engine, and the
    operator is told which field to fix (UX-DESKTOP §9).
    """

    def _refuse(message: str, focus: Any) -> bool:  # noqa: ANN401 -- wx ships no stubs
        entry_lbl.SetLabel(message)
        focus.SetFocus()
        return False

    def _target_present() -> bool:
        plate = plate_input.GetValue().strip()
        if not plate:
            return _refuse("Enter the rider plate.", plate_input)
        if not _is_plate(plate):
            return _refuse("The plate must be digits only.", plate_input)
        if resolve(plate) is None:
            return _refuse(f"Unknown plate {plate} — check the roster.", plate_input)
        # The reason rule, plus the refusal line ``_bind_reason_gate``
        # cannot write (it has no label): the operator is told which
        # field to fix, not merely where focus went.
        if not reason_input.GetValue().strip():
            return _refuse("Enter a reason for the DNF.", reason_input)
        return True

    return _target_present


def _bind_digits_only(plate_input: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Let only digit keystrokes reach ``plate_input`` (``EVT_CHAR``).

    The typed half of the plate rule: a key the field's alphabet does
    not carry is consumed before the control sees it (no
    ``event.Skip()``, wx's own way to drop one keystroke), so a stray
    letter never lands in the field. :func:`_bind_plate_gate` remains
    the other half -- a prefilled or pasted value never passes through
    this event.
    """

    def _on_char(event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        if _plate_key_allowed(event.GetKeyCode()):
            event.Skip()

    plate_input.Bind(wx.EVT_CHAR, _on_char)


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


def run_edit_crossing(  # noqa: PLR0913 -- resource, frame + the dialog's own fields
    resource: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    frame: Any,  # noqa: ANN401 -- wx ships no stubs
    adding: bool,
    plate: str,
    time: str,
    seq: int | None = None,
    base_date: date | None = None,
    title: str | None = None,
    read_only_plate: bool = False,
    suppress_void: bool = False,
) -> CrossingEdit | None:
    """Open ``edit_crossing_dlg``; return the confirmed edit, or None.

    ``adding`` False titles it "Edit Crossing" and shows ``void_btn``
    (a confirmed void returns ``CrossingEdit(void=True)``); True titles
    it "Add Crossing at Time" and hides ``void_btn``. *plate* prefills
    ``plate_input``, *time* (``HH:MM:SS``) prefills ``time_picker``,
    and the operator supplies the required reason. *seq* is the
    crossing identity the caller already knows (the selected lap);
    ``None`` leaves it to the caller to resolve (the menu flow).

    The three optional overrides let a caller re-purpose the same
    window: *title* replaces the ``adding``-derived one (the crossing
    detail's "Edit Time"), *read_only_plate* shows *plate* without
    offering it for editing, and *suppress_void* hides ``void_btn``
    (and unbinds it) where voiding the crossing is not on offer. Their
    defaults preserve the Cards menu routes' behaviour.

    Returns:
        The confirmed submission, or ``None`` on cancel.
    """
    dialog = load_dialog(resource, ids.EDIT_CROSSING_DLG)
    if dialog is None:
        return None
    try:
        dialog.SetTitle(
            title if title is not None else ("Add Crossing at Time" if adding else "Edit Crossing")
        )
        plate_input = _find(dialog, ids.PLATE_INPUT)
        time_picker = _find(dialog, ids.TIME_PICKER)
        reason_input = _find(dialog, ids.REASON_INPUT)
        void_btn = _find(dialog, ids.VOID_BTN)
        plate_input.SetValue(plate)
        plate_input.Enable(not read_only_plate)
        _set_time_picker(time_picker, time)
        void_btn.Show((not adding) and (not suppress_void))
        void_btn.Enable((not adding) and (not suppress_void))
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
        if not adding and not suppress_void:

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
    dialog = load_dialog(resource, ids.MANUAL_DEAL_DLG)
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

    Writes the dialog's two data labels -- ``card_lbl`` with the
    formatted card and ``entry_lbl`` with the entry's own name, never
    blank -- so the operator sees exactly which dealt card they are
    about to void. OK starts disabled and enables once the reason box
    holds non-blank text (:func:`_bind_reason_enable`); the reason gate
    still guards the click.
    """
    dialog = load_dialog(resource, ids.VOID_CARD_CONFIRM_DLG)
    if dialog is None:
        return None
    try:
        card_lbl = _find(dialog, ids.CARD_LBL)
        entry_lbl = _find(dialog, ids.ENTRY_LBL)
        reason_input = _find(dialog, ids.REASON_INPUT)
        card_lbl.SetLabel(format_card(card))
        entry_lbl.SetLabel(entry)
        confirmed: CardVoid | None = None

        def _commit() -> None:
            nonlocal confirmed
            confirmed = CardVoid(
                entry_id=entry_id,
                card=card,
                reason=reason_input.GetValue().strip(),
            )

        _bind_reason_enable(dialog, reason_input)
        _bind_ok(dialog, _bind_reason_gate(reason_input), _commit)
        result = _run_dialog(dialog, frame)
        return confirmed if result == wx.ID_OK else None
    finally:
        if not dialog.IsBeingDeleted():
            dialog.Destroy()


def run_dnf(  # noqa: PLR0913 -- (resource, frame, roster, plate, entry): the runner's fixed shape
    resource: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    frame: Any,  # noqa: ANN401 -- wx ships no stubs
    roster: Roster,
    plate: str = "",
    entry: str = "",
) -> DnfMark | None:
    """Open ``dnf_confirm_dlg``; return the confirmed DNF mark.

    The dialog is a form: ``plate_input`` takes the rider plate (or a
    whole entry's plate) and ``reason_input`` the reason. Its OK gate
    is the strictest of the four correction forms -- the plate must be
    digits *and* resolve through *roster* (``Roster.resolve_plate``)
    before the reason is even considered, and any refusal keeps the
    dialog open with the reason shown on its ``entry_lbl``
    (:func:`_bind_plate_gate`); the typed half of the plate rule is the
    ``wx.EVT_CHAR`` digit filter (:func:`_bind_digits_only`). *plate*
    prefills the input (the entry-detail button passes its own entry
    plate; the menu route passes nothing and the operator types the
    plate). *entry* writes the ``entry_lbl`` naming sentence
    (``dialogs.dnf_message``) when a target is already known; with
    none, XRC's own standing copy stays until a refusal replaces it.
    """
    dialog = load_dialog(resource, ids.DNF_CONFIRM_DLG)
    if dialog is None:
        return None
    try:
        plate_input = _find(dialog, ids.PLATE_INPUT)
        reason_input = _find(dialog, ids.REASON_INPUT)
        entry_lbl = _find(dialog, ids.ENTRY_LBL)
        plate_input.SetValue(plate)
        if entry:
            entry_lbl.SetLabel(entry)
        _bind_digits_only(plate_input)
        confirmed: DnfMark | None = None

        def _commit() -> None:
            nonlocal confirmed
            confirmed = DnfMark(
                plate=plate_input.GetValue().strip(),
                reason=reason_input.GetValue().strip(),
            )

        gate = _bind_plate_gate(plate_input, reason_input, entry_lbl, roster.resolve_plate)
        _bind_ok(dialog, gate, _commit)
        result = _run_dialog(dialog, frame)
        return confirmed if result == wx.ID_OK else None
    finally:
        if not dialog.IsBeingDeleted():
            dialog.Destroy()


def _wx_datetime_for_date(value: date) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return a ``wx.DateTime`` for *value* (wx months are 0-based)."""
    stamp = wx.DateTime()
    stamp.Set(value.day, value.month - 1, value.year)
    return stamp


def _wx_datetime_for_time(value: time) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return a ``wx.DateTime`` carrying *value*'s time-of-day."""
    stamp = wx.DateTime()
    stamp.SetHMS(value.hour, value.minute, value.second)
    return stamp


def run_set_start_time(
    resource: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    frame: Any,  # noqa: ANN401 -- wx ships no stubs
    prefill: datetime,
) -> datetime | None:
    """Open ``set_start_dlg``; return the confirmed instant, or None.

    ux-polish's Ride ▸ Set Start Time… runner -- section B's sibling
    of the section-C runners above: loads ``set_start_dlg``,
    prefills ``start_date_picker``/``start_time_picker`` from
    *prefill* (the app passes the ride's planned start, the value the
    operator is correcting; the pickers' bare defaults would show
    today's date and now, wrong for a back-dated event), shows it
    through the one dialog seam with the recorded E1.5.3 defaults
    applied, and on a confirmed ``wx.ID_OK`` combines the two pickers
    into the naive local ``datetime`` the engine stores -- the same
    reading ``views/ride_setup.py`` performs for ``planned_start``.
    No reason field, so no reason gate binds (unlike the section-C
    runners): the stock ``wxID_OK`` ends the modal itself.

    Returns:
        The confirmed start instant, or ``None`` on cancel.
    """
    dialog = load_dialog(resource, ids.SET_START_DLG)
    if dialog is None:
        return None
    try:
        date_picker = _find(dialog, ids.START_DATE_PICKER)
        time_picker = _find(dialog, ids.START_TIME_PICKER)
        date_picker.SetValue(_wx_datetime_for_date(prefill.date()))
        time_picker.SetValue(_wx_datetime_for_time(prefill.time()))
        if _run_dialog(dialog, frame) != wx.ID_OK:
            return None
        picked_date = date_picker.GetValue()
        picked_time = time_picker.GetValue()
        return datetime.combine(
            date(picked_date.GetYear(), picked_date.GetMonth() + 1, picked_date.GetDay()),
            time(picked_time.GetHour(), picked_time.GetMinute(), picked_time.GetSecond()),
        )
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

    dialog = load_dialog(resource, ids.AUDIT_DLG)
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
