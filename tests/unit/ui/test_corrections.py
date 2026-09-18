# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the correction runners (``corrections.py``).

Three behaviours this workstream adds:

* ``run_void_card`` writes **two** labels -- ``card_lbl`` (the
  formatted card) and ``entry_lbl`` (the entry's own name) -- instead
  of one concatenated string, so ``void_card_confirm_dlg``'s new
  two-column top grid reads caption/data exactly like
  ``crossing_detail_dlg``'s.
* The dialog's "Void card" OK button starts disabled and enables only
  once the Reason box is non-blank (``_bind_reason_enable`` drives
  ``wx.EVT_TEXT``).
* ``run_edit_crossing`` gains ``title`` / ``read_only_plate`` /
  ``suppress_void`` so a caller can retitle it "Edit Time", lock the
  plate field and hide the void button; the defaults preserve the
  existing menu callers' behaviour.

The DNF dialog (``run_dnf``) is the fourth: its OK gate now needs a
*real* target, so a keystroke filter passes only digit keys
(``_plate_key_allowed``) and the gate refuses -- keeping the dialog
open, focusing the offending field and writing why onto ``entry_lbl``
-- a blank, non-digit or roster-unknown plate, or a blank reason.

Every runner is driven against recording widget doubles -- no window
is built and no desktop is taken. The toolkit boundaries the runners
call (``load_dialog`` and the ``find_control`` lookup) are
monkeypatched, and the modal seam is stubbed the way
``test_crossing_detail.py`` stubs ``run_edit_crossing`` itself. The
.xrc restructure that shares this change is pinned structurally, over
the authored XML, because a loader test cannot see sizer shape.
"""

from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest
import wx
from defusedxml.ElementTree import parse

from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui import ids
from rivercrossing.ui.views import corrections, dialogs

if TYPE_CHECKING:
    from collections.abc import Callable
    from xml.etree.ElementTree import Element


_DIALOGS_XRC = Path(corrections.__file__).resolve().parent.parent / "xrc" / "dialogs.xrc"

# The exact consequence line the restructured top carries (capital R, no
# leading em dash). Pinned as a literal: it is frozen wording, not data.
_VOID_DESCRIPTION = "Removes the card from the entry's scored hand. Audit-logged."

_VOID_CARD = "9H"
_VOID_ENTRY = "45 · J. Okafor"
_VOID_CARD_TEXT = "9♥"  # format_card("9H"), spelled out so a raw code fails

# The instant a 10:05:00 picker stamp lands on the fixed event day.
_EDIT_TIME = datetime(2026, 9, 20, 10, 5)  # noqa: DTZ001 -- naive by design


# ------------------------------------------------------------- doubles


class _StubDialog:
    """A loaded XRC dialog double: a name, a title, closable."""

    def __init__(self, name: str, *, being_deleted: bool = False) -> None:
        """Start untitled, undestroyed and not mid-delete."""
        self._name = name
        self._being_deleted = being_deleted
        self.titles: list[str] = []
        self.modal_ids: list[int] = []
        self.destroyed = False

    def GetName(self) -> str:  # noqa: N802 -- wx API name the SUT calls
        """Return the frozen XRC window name."""
        return self._name

    def SetTitle(self, title: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the title the runner applied."""
        self.titles.append(title)

    def EndModal(self, modal_id: int) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the id the committed OK handler ended on."""
        self.modal_ids.append(modal_id)

    def IsBeingDeleted(self) -> bool:  # noqa: N802 -- wx API name the SUT calls
        """Report the scripted mid-delete state."""
        return self._being_deleted

    def Destroy(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the destroy the runner's ``finally`` runs."""
        self.destroyed = True


class _RecordingLabel:
    """A ``wx.StaticText`` double recording the text it was given."""

    def __init__(self) -> None:
        """Start blank."""
        self.label = ""

    def SetLabel(self, text: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the rendered text."""
        self.label = text


class _RecordingText:
    """A ``wx.TextCtrl`` double recording value and focus."""

    def __init__(self, value: str = "") -> None:
        """Start holding *value*, with no enablement applied."""
        self._value = value
        self.enabled: bool | None = None
        self.focuses = 0

    def GetValue(self) -> str:  # noqa: N802 -- wx API name the SUT calls
        """Return the field's current text."""
        return self._value

    def SetValue(self, value: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Replace the field's text."""
        self._value = value

    def Enable(self, enabled: bool = True) -> None:  # noqa: N802, FBT001, FBT002 -- wx API shape
        """Record the enablement the runner applied."""
        self.enabled = enabled

    def SetFocus(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the focus a gate applied to the field it refused."""
        self.focuses += 1


class _KeyEvent:
    """A ``wx.KeyEvent`` double carrying one key code and its skip."""

    def __init__(self, key_code: int) -> None:
        """Start on *key_code*, before the handler ran."""
        self._key_code = key_code
        self.skipped = False

    def GetKeyCode(self) -> int:  # noqa: N802 -- wx API name the double mirrors
        """Return the typed key code."""
        return self._key_code

    def Skip(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record that the handler let the event continue."""
        self.skipped = True


class _RecordingPlateInput(_RecordingText):
    """A plate field double recording its ``EVT_CHAR`` bind."""

    def __init__(self, value: str = "") -> None:
        """Start holding *value*, with no key handler bound."""
        super().__init__(value)
        self.char_handlers: list[Callable[[object], None]] = []

    def Bind(self, _event_type: object, handler: Callable[[object], None]) -> None:  # noqa: N802
        """Record the handler the runner bound to ``EVT_CHAR``."""
        self.char_handlers.append(handler)

    def press(self, key_code: int) -> _KeyEvent:
        """Fire the bound handler with *key_code*, as typing would."""
        event = _KeyEvent(key_code)
        self.char_handlers[0](event)
        return event


class _RecordingReasonInput(_RecordingText):
    """A reason field double recording its ``EVT_TEXT`` bind."""

    def __init__(self, value: str = "") -> None:
        """Start holding *value*, with no text handler bound."""
        super().__init__(value)
        self.text_handlers: list[Callable[[object], None]] = []

    def Bind(self, _event_type: object, handler: Callable[[object], None]) -> None:  # noqa: N802
        """Record the handler the runner bound to ``EVT_TEXT``."""
        self.text_handlers.append(handler)

    def type_text(self, value: str) -> None:
        """Type *value* and fire the bound ``EVT_TEXT`` handler."""
        self.SetValue(value)
        self.text_handlers[0](None)


class _RecordingButton:
    """A ``wx.Button`` double recording enable, show and binds."""

    def __init__(self) -> None:
        """Start with no enablement, visibility or handler applied."""
        self.enabled: bool | None = None
        self.shown: bool | None = None
        self.handlers: list[Callable[[object], None]] = []

    def Enable(self, enabled: bool = True) -> None:  # noqa: N802, FBT001, FBT002 -- wx API shape
        """Record the enablement the runner applied."""
        self.enabled = enabled

    def Show(self, show: bool = True) -> None:  # noqa: N802, FBT001, FBT002 -- wx API shape
        """Record the visibility the runner applied."""
        self.shown = show

    def Bind(self, _event_type: object, handler: Callable[[object], None]) -> None:  # noqa: N802
        """Record the handler the runner bound to ``EVT_BUTTON``."""
        self.handlers.append(handler)

    def click(self) -> None:
        """Drive the bound ``EVT_BUTTON`` handler as a click would."""
        self.handlers[0](None)


class _RecordingTimePicker:
    """A ``wx.adv.TimePickerCtrl`` double recording its stamp."""

    def __init__(self) -> None:
        """Start with no stamp set."""
        self.value: object | None = None

    def SetValue(self, value: object) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the stamp the runner applied."""
        self.value = value

    def GetValue(self) -> object | None:  # noqa: N802 -- wx API name the SUT calls
        """Return the stamp the runner applied."""
        return self.value


class _FrozenClock:
    """A ``datetime`` stand-in pinned to the fixed event day."""

    @staticmethod
    def now(_tz: object = None) -> datetime:
        """Return midnight on the fixed event day."""
        return datetime(2026, 9, 20, tzinfo=UTC)

    @staticmethod
    def combine(day: date, at: time) -> datetime:
        """Combine *day* and *at* as ``datetime.combine`` does."""
        return datetime.combine(day, at)


class _VoidCardDoubles(NamedTuple):
    """The doubles one ``run_void_card`` run touches."""

    dialog: _StubDialog
    card_lbl: _RecordingLabel
    entry_lbl: _RecordingLabel
    reason_input: _RecordingReasonInput
    ok_btn: _RecordingButton


class _EditCrossingDoubles(NamedTuple):
    """The doubles one ``run_edit_crossing`` run touches."""

    dialog: _StubDialog
    plate_input: _RecordingText
    void_btn: _RecordingButton
    ok_btn: _RecordingButton


class _DnfDoubles(NamedTuple):
    """The doubles one ``run_dnf`` run touches."""

    dialog: _StubDialog
    plate_input: _RecordingPlateInput
    reason_input: _RecordingReasonInput
    entry_lbl: _RecordingLabel
    ok_btn: _RecordingButton


class _ManualDealDoubles(NamedTuple):
    """The doubles one ``run_manual_deal`` run touches."""

    dialog: _StubDialog
    plate_input: _RecordingPlateInput
    reason_input: _RecordingReasonInput
    ok_btn: _RecordingButton


# ------------------------------------------------------------- arrange
#
# logic-coverage-exempt: T-10 -- ``load_dialog`` and ``find_control``
# are the wx toolkit boundaries (``XmlResource.LoadDialog`` and the
# window child lookup), the same seam ``test_crossing_detail.py``'s
# ``run_plate_dialog`` tests stub; no engine or roster is doubled.


def _refuse_lookup(*_args: object, **_kwargs: object) -> object:
    """Fail the test if the runner looks a control up."""
    raise AssertionError("no control may be looked up")


def _patch_load(monkeypatch: pytest.MonkeyPatch, dialog: _StubDialog | None) -> None:
    """Point the ``load_dialog`` seam at *dialog*."""

    def _load(_resource: object, _name: object, **_kwargs: object) -> _StubDialog | None:
        return dialog

    monkeypatch.setattr(corrections, "load_dialog", _load)


def _patch_controls(monkeypatch: pytest.MonkeyPatch, controls: dict[str, object]) -> None:
    """Point the ``find_control`` seam at *controls* by name."""

    def _find(_dialog: object, name: str, _expected_type: object = None) -> object:
        if name not in controls:
            message = f"unexpected control lookup: {name!r}"
            raise AssertionError(message)
        return controls[name]

    monkeypatch.setattr(corrections, "find_control", _find)


def _patch_modal(
    monkeypatch: pytest.MonkeyPatch, result: int, on_show: Callable[[], None] | None = None
) -> None:
    """Stub the modal seam; optionally drive the dialog's own OK."""

    def _run(_dialog: object, opener: object) -> int:  # noqa: ARG001 -- run_dialog's keyword
        if on_show is not None:
            on_show()
        return result

    monkeypatch.setattr(dialogs, "run_dialog", _run)


# ------------------------------------------------------- run_void_card


def _void_card_harness(monkeypatch: pytest.MonkeyPatch, *, reason: str = "") -> _VoidCardDoubles:
    """Build the void-card confirm double wired into the runner."""
    dialog = _StubDialog(ids.VOID_CARD_CONFIRM_DLG)
    card_lbl = _RecordingLabel()
    entry_lbl = _RecordingLabel()
    reason_input = _RecordingReasonInput(reason)
    ok_btn = _RecordingButton()
    _patch_load(monkeypatch, dialog)
    _patch_controls(
        monkeypatch,
        {
            ids.CARD_LBL: card_lbl,
            ids.ENTRY_LBL: entry_lbl,
            ids.REASON_INPUT: reason_input,
            "wxID_OK": ok_btn,
        },
    )
    return _VoidCardDoubles(dialog, card_lbl, entry_lbl, reason_input, ok_btn)


def _open_void_card() -> corrections.CardVoid | None:
    """Open the void-card confirm through the runner under test."""
    return corrections.run_void_card(
        object(), frame=object(), entry_id="12", card=_VOID_CARD, entry=_VOID_ENTRY
    )


def test_run_void_card_given_a_confirmed_void_writes_the_two_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two labels: ``card_lbl`` formatted, ``entry_lbl`` the entry."""
    doubles = _void_card_harness(monkeypatch, reason="wrong card")
    _patch_modal(monkeypatch, wx.ID_OK, doubles.ok_btn.click)

    result = _open_void_card()

    assert (doubles.card_lbl.label, doubles.entry_lbl.label) == (_VOID_CARD_TEXT, _VOID_ENTRY)
    assert result == corrections.CardVoid(entry_id="12", card=_VOID_CARD, reason="wrong card")
    assert (doubles.dialog.modal_ids, doubles.dialog.destroyed) == ([wx.ID_OK], True)


def test_run_void_card_given_a_fresh_dialog_disables_the_void_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OK button starts disabled however the reason box arrived."""
    doubles = _void_card_harness(monkeypatch, reason="wrong card")
    _patch_modal(monkeypatch, wx.ID_CANCEL)

    _open_void_card()

    assert doubles.ok_btn.enabled is False


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("", False),  # T-4 boundary: present-but-empty
        ("   ", False),  # whitespace only is still blank
        ("\t\n", False),  # T-4 boundary: other whitespace
        ("x", True),  # T-4 min: one non-blank character
        ("wrong card", True),  # ordinary text
        ("  wrong card  ", True),  # surrounding whitespace is trimmed
    ],
    ids=["empty", "spaces", "control_whitespace", "one_char", "text", "padded_text"],
)
def test_run_void_card_given_a_typed_reason_sets_the_void_button_state(
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    expected: bool,  # noqa: FBT001 -- a parametrize row's value
) -> None:
    """``EVT_TEXT`` enables OK exactly when the reason is non-blank."""
    doubles = _void_card_harness(monkeypatch)
    _patch_modal(monkeypatch, wx.ID_CANCEL)
    _open_void_card()

    doubles.reason_input.type_text(reason)

    assert doubles.ok_btn.enabled is expected


def test_run_void_card_given_a_cancelled_dialog_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: Cancel commits nothing, window destroyed."""
    doubles = _void_card_harness(monkeypatch, reason="wrong card")
    _patch_modal(monkeypatch, wx.ID_CANCEL)

    result = _open_void_card()

    assert (result, doubles.dialog.modal_ids, doubles.dialog.destroyed) == (None, [], True)


def test_run_void_card_given_an_unauthored_dialog_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: a dialog no .xrc authors opens nothing."""
    _patch_load(monkeypatch, None)
    monkeypatch.setattr(corrections, "find_control", _refuse_lookup)

    result = _open_void_card()

    assert result is None


# ------------------------------------------- run_edit_crossing params


def _edit_crossing_harness(  # noqa: PLR0913 -- the scripted run's own shape
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: int = wx.ID_CANCEL,
    reason: str = "",
    plate: str = "12",
    void_click: bool = False,
) -> _EditCrossingDoubles:
    """Build the edit-crossing double wired into the runner.

    ``_run_dialog`` is stubbed (never ``dialogs.run_dialog``), so the
    recorded-defaults step never looks a control up in the double; it
    clicks the scripted button -- OK, or ``void_btn`` when *void_click*
    -- as the operator would, then answers *result*.
    """
    dialog = _StubDialog(ids.EDIT_CROSSING_DLG)
    plate_input = _RecordingText(plate)
    void_btn = _RecordingButton()
    ok_btn = _RecordingButton()
    _patch_load(monkeypatch, dialog)
    _patch_controls(
        monkeypatch,
        {
            ids.PLATE_INPUT: plate_input,
            ids.TIME_PICKER: _RecordingTimePicker(),
            ids.REASON_INPUT: _RecordingReasonInput(reason),
            ids.VOID_BTN: void_btn,
            "wxID_OK": ok_btn,
        },
    )

    def _run(_dialog: object, _frame: object) -> int:
        if result == wx.ID_OK:
            if void_click:
                void_btn.click()
            else:
                ok_btn.click()
        return result

    monkeypatch.setattr(corrections, "_run_dialog", _run)
    return _EditCrossingDoubles(dialog, plate_input, void_btn, ok_btn)


def _open_edit_crossing(**kwargs: object) -> corrections.CrossingEdit | None:
    """Open the edit dialog through the runner under test.

    *kwargs* are the runner's own dialog parameters; each test passes
    the ones it pins.
    """
    return corrections.run_edit_crossing(
        object(), frame=object(), plate="12", time="10:05:00", **kwargs
    )


def test_run_edit_crossing_given_the_edit_time_params_retitles_disables_and_suppresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-13: "Edit Time", read-only plate and no void button at once."""
    doubles = _edit_crossing_harness(monkeypatch)

    _open_edit_crossing(
        adding=False,
        seq=1,
        base_date=date(2026, 9, 20),
        title="Edit Time",
        read_only_plate=True,
        suppress_void=True,
    )

    assert doubles.dialog.titles == ["Edit Time"]
    assert doubles.plate_input.enabled is False
    assert (doubles.void_btn.shown, doubles.void_btn.enabled, doubles.void_btn.handlers) == (
        False,
        False,
        [],
    )


def test_run_edit_crossing_given_the_defaults_keeps_the_menu_behaviour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 false: defaults keep the plate editable and void shown."""
    doubles = _edit_crossing_harness(monkeypatch)

    _open_edit_crossing(adding=False)

    assert doubles.dialog.titles == ["Edit Crossing"]
    assert doubles.plate_input.enabled is True
    assert (doubles.void_btn.shown, doubles.void_btn.enabled) == (True, True)
    assert len(doubles.void_btn.handlers) == 1


def test_run_edit_crossing_given_adding_defaults_to_the_add_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``adding=True`` titles the dialog and never offers a void."""
    doubles = _edit_crossing_harness(monkeypatch)

    _open_edit_crossing(adding=True)

    assert doubles.dialog.titles == ["Add Crossing at Time"]
    assert (doubles.void_btn.shown, doubles.void_btn.enabled, doubles.void_btn.handlers) == (
        False,
        False,
        [],
    )


@pytest.mark.parametrize(
    ("read_only_plate", "expected"),
    [(False, True), (True, False)],
    ids=["editable", "read_only"],
)
def test_run_edit_crossing_given_a_plate_mode_sets_the_field_enablement(
    monkeypatch: pytest.MonkeyPatch,
    read_only_plate: bool,  # noqa: FBT001 -- a parametrize row's value
    expected: bool,  # noqa: FBT001 -- a parametrize row's value
) -> None:
    """T-13: the plate field's enablement inverts the flag."""
    doubles = _edit_crossing_harness(monkeypatch)

    _open_edit_crossing(adding=False, read_only_plate=read_only_plate)

    assert doubles.plate_input.enabled is expected


def test_run_edit_crossing_given_a_confirmed_edit_returns_the_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unchanged commit path still builds the ``CrossingEdit``."""
    _edit_crossing_harness(monkeypatch, result=wx.ID_OK, reason="wrong clock")

    result = _open_edit_crossing(adding=False, seq=1, base_date=date(2026, 9, 20))

    assert result == corrections.CrossingEdit(
        entry_id="12", seq=1, crossed_at=_EDIT_TIME, reason="wrong clock"
    )


def test_run_edit_crossing_given_a_chosen_void_returns_the_void_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The void button still voids instead of retiming."""
    _edit_crossing_harness(monkeypatch, result=wx.ID_OK, reason="never happened", void_click=True)

    result = _open_edit_crossing(adding=False, seq=1, base_date=date(2026, 9, 20))

    assert result == corrections.CrossingEdit(
        entry_id="12", seq=1, crossed_at=None, reason="never happened", void=True
    )


def test_run_edit_crossing_given_a_blank_void_reason_ends_no_modal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: the void click refuses without a reason."""
    doubles = _edit_crossing_harness(monkeypatch, result=wx.ID_OK, void_click=True)

    result = _open_edit_crossing(adding=False, seq=1, base_date=date(2026, 9, 20))

    assert (result, doubles.dialog.modal_ids) == (None, [])


def test_run_edit_crossing_given_no_base_date_uses_the_current_utc_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-4 nullable: no event date falls back to today's UTC day."""
    _edit_crossing_harness(monkeypatch, result=wx.ID_OK, reason="wrong clock")
    monkeypatch.setattr(corrections, "datetime", _FrozenClock)

    result = _open_edit_crossing(adding=False, seq=1)

    assert result == corrections.CrossingEdit(
        entry_id="12", seq=1, crossed_at=_EDIT_TIME, reason="wrong clock"
    )


def test_run_edit_crossing_given_a_cancelled_dialog_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: Cancel returns no submission."""
    doubles = _edit_crossing_harness(monkeypatch)

    result = _open_edit_crossing(adding=False)

    assert (result, doubles.dialog.destroyed) == (None, True)


def test_run_edit_crossing_given_an_unauthored_dialog_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: a dialog no .xrc authors loads nothing."""
    _patch_load(monkeypatch, None)
    monkeypatch.setattr(corrections, "find_control", _refuse_lookup)

    result = _open_edit_crossing(adding=False)

    assert result is None


@pytest.mark.parametrize(
    ("adding", "suppress_void", "expected"),
    [
        (False, False, (True, True, 1)),
        (False, True, (False, False, 0)),
        (True, False, (False, False, 0)),
        (True, True, (False, False, 0)),
    ],
    ids=["edit", "edit_suppressed", "add", "add_suppressed"],
)
def test_run_edit_crossing_given_the_two_void_flags_sets_the_buttons_state(  # noqa: PLR0913, PLR0917
    monkeypatch: pytest.MonkeyPatch,
    adding: bool,  # noqa: FBT001 -- a parametrize row's value
    suppress_void: bool,  # noqa: FBT001 -- a parametrize row's value
    expected: tuple[bool, bool, int],
) -> None:
    """T-13: shown, enabled and bound all follow the two flags."""
    doubles = _edit_crossing_harness(monkeypatch)

    _open_edit_crossing(adding=adding, suppress_void=suppress_void)

    assert (
        doubles.void_btn.shown,
        doubles.void_btn.enabled,
        len(doubles.void_btn.handlers),
    ) == expected


# --------------------------------------------------------- run_dnf
#
# The DNF dialog is the one correction form whose target is typed in,
# so its OK gate has to refuse a plate that is blank, not a number, or
# no entry on the roster, as well as a blank reason -- and say why on
# the dialog's own inline line (``entry_lbl``, the consequence line
# directly under the field) rather than let the engine's unknown-plate
# refusal be the first line of defence. The keystroke filter is the
# typing half of that rule; the gate is the other half, because a
# prefilled or pasted value never passes through ``wx.EVT_CHAR``.

_PLATE_REFUSED_EMPTY = "Enter the rider plate."
_PLATE_REFUSED_NOT_DIGITS = "The plate must be digits only."
_PLATE_REFUSED_UNKNOWN = "Unknown plate 404 — check the roster."
_REASON_REFUSED_EMPTY = "Enter a reason for the DNF."


def _dnf_roster() -> Roster:
    """Build a roster holding solo 12 and a pooled team (45, 9)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="J.", last_name="Okafor", plate="12")
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[
            Rider(first_name="Alex", last_name="Smith", plate="45"),
            Rider(first_name="Bo", last_name="Jones", plate="9"),
        ],
    )
    return roster


def _dnf_harness(  # noqa: PLR0913 -- the scripted run's own shape
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: int = wx.ID_CANCEL,
    plate: str = "12",
    reason: str = "",
) -> _DnfDoubles:
    """Build the DNF dialog's doubles, wired into the runner.

    ``_run_dialog`` is stubbed (never ``dialogs.run_dialog``), so the
    recorded-defaults step never looks a control up in the double; it
    clicks OK -- as the operator would -- when *result* is ``wx.ID_OK``
    and then answers *result*.
    """
    dialog = _StubDialog(ids.DNF_CONFIRM_DLG)
    plate_input = _RecordingPlateInput(plate)
    reason_input = _RecordingReasonInput(reason)
    entry_lbl = _RecordingLabel()
    ok_btn = _RecordingButton()
    _patch_load(monkeypatch, dialog)
    _patch_controls(
        monkeypatch,
        {
            ids.PLATE_INPUT: plate_input,
            ids.REASON_INPUT: reason_input,
            ids.ENTRY_LBL: entry_lbl,
            "wxID_OK": ok_btn,
        },
    )

    def _run(_dialog: object, _frame: object) -> int:
        if result == wx.ID_OK:
            ok_btn.click()
        return result

    monkeypatch.setattr(corrections, "_run_dialog", _run)
    return _DnfDoubles(dialog, plate_input, reason_input, entry_lbl, ok_btn)


def _open_dnf(*, plate: str = "12", entry: str = "") -> corrections.DnfMark | None:
    """Open the DNF dialog through the runner under test.

    The roster is a real (wx-free) :class:`Roster` -- the gate's own
    resolution seam -- so nothing here is mocked (T-10).
    """
    return corrections.run_dnf(
        object(), frame=object(), roster=_dnf_roster(), plate=plate, entry=entry
    )


def test_run_dnf_given_a_resolvable_plate_and_reason_returns_the_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A digit plate the roster answers plus a reason commits."""
    doubles = _dnf_harness(monkeypatch, result=wx.ID_OK, plate="12", reason="mechanical failure")

    result = _open_dnf()

    assert result == corrections.DnfMark(plate="12", reason="mechanical failure")
    assert (doubles.dialog.modal_ids, doubles.entry_lbl.label) == ([wx.ID_OK], "")


def test_run_dnf_given_a_pooled_riders_own_plate_accepts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A team member's own number resolves through the roster too."""
    _dnf_harness(monkeypatch, result=wx.ID_OK, plate="45", reason="mechanical failure")

    result = _open_dnf(plate="45")

    assert result == corrections.DnfMark(plate="45", reason="mechanical failure")


def test_run_dnf_given_a_blank_plate_keeps_the_dialog_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-4 empty: no plate, no close -- and the field says so."""
    doubles = _dnf_harness(monkeypatch, result=wx.ID_OK, plate="", reason="mechanical failure")

    result = _open_dnf(plate="")

    assert (result, doubles.dialog.modal_ids) == (None, [])
    assert doubles.entry_lbl.label == _PLATE_REFUSED_EMPTY
    assert doubles.plate_input.focuses == 1


def test_run_dnf_given_a_non_digit_plate_keeps_the_dialog_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prefilled non-numeric plate is refused, not sent on."""
    doubles = _dnf_harness(monkeypatch, result=wx.ID_OK, plate="K1", reason="mechanical failure")

    result = _open_dnf(plate="K1")

    assert (result, doubles.dialog.modal_ids) == (None, [])
    assert doubles.entry_lbl.label == _PLATE_REFUSED_NOT_DIGITS
    assert doubles.plate_input.focuses == 1


def test_run_dnf_given_an_unknown_plate_keeps_the_dialog_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: digits the roster does not answer are refused."""
    doubles = _dnf_harness(monkeypatch, result=wx.ID_OK, plate="404", reason="mechanical failure")

    result = _open_dnf(plate="404")

    assert (result, doubles.dialog.modal_ids) == (None, [])
    assert doubles.entry_lbl.label == _PLATE_REFUSED_UNKNOWN
    assert doubles.plate_input.focuses == 1


def test_run_dnf_given_a_blank_reason_keeps_the_dialog_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real target still needs a reason; the field takes focus."""
    doubles = _dnf_harness(monkeypatch, result=wx.ID_OK, plate="12")

    result = _open_dnf()

    assert (result, doubles.dialog.modal_ids) == (None, [])
    assert doubles.entry_lbl.label == _REASON_REFUSED_EMPTY
    assert (doubles.reason_input.focuses, doubles.plate_input.focuses) == (1, 0)


def test_run_dnf_given_an_entry_prefill_writes_the_entry_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A known target's naming sentence lands on ``entry_lbl``."""
    doubles = _dnf_harness(monkeypatch)

    _open_dnf(entry="9 · Dirt Dynamos")

    assert doubles.entry_lbl.label == "9 · Dirt Dynamos"


def test_run_dnf_given_a_plate_binds_its_keystroke_filter_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plate field filters keystrokes as the operator types."""
    doubles = _dnf_harness(monkeypatch)

    _open_dnf()

    assert len(doubles.plate_input.char_handlers) == 1


@pytest.mark.parametrize(
    ("key_code", "expected"),
    [(ord("5"), True), (ord("A"), False)],
    ids=["digit_allowed", "letter_swallowed"],
)
def test_run_dnf_given_a_keystroke_lets_the_bound_filter_decide(
    monkeypatch: pytest.MonkeyPatch, key_code: int, *, expected: bool
) -> None:
    """The bound handler skips a digit key, consumes the rest."""
    doubles = _dnf_harness(monkeypatch)
    _open_dnf()  # arrange: the runner binds the filter under test

    event = doubles.plate_input.press(key_code)

    assert event.skipped is expected


_PLATE_KEY_CASES = (
    (0, True),  # the lowest key code wx reports
    (8, True),  # Backspace: below the control limit
    (31, True),  # T-4: control limit - 1
    (32, False),  # T-4: control limit (space) -- not a plate character
    (47, False),  # "/": one under "0"
    (48, True),  # "0"
    (57, True),  # "9"
    (58, False),  # ":": one past "9"
    (65, False),  # "A"
    (wx.WXK_DELETE, True),  # Delete still edits the field
)
_PLATE_KEY_CASE_IDS = (
    "control_low",
    "backspace",
    "control_max",
    "space",
    "slash",
    "zero",
    "nine",
    "colon",
    "letter",
    "delete",
)


@pytest.mark.parametrize(("key_code", "expected"), _PLATE_KEY_CASES, ids=_PLATE_KEY_CASE_IDS)
def test_plate_key_allowed_given_a_key_code_accepts_digits_and_control_keys(
    key_code: int, *, expected: bool
) -> None:
    """T-4 boundaries: the plate's alphabet plus the editing keys."""
    assert corrections._plate_key_allowed(key_code) is expected


def test_run_dnf_given_a_cancelled_dialog_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: Cancel returns no submission."""
    doubles = _dnf_harness(monkeypatch, plate="12", reason="mechanical failure")

    result = _open_dnf()

    assert (result, doubles.dialog.destroyed) == (None, True)


def test_run_dnf_given_an_unauthored_dialog_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: a dialog no .xrc authors loads nothing."""
    _patch_load(monkeypatch, None)
    monkeypatch.setattr(corrections, "find_control", _refuse_lookup)

    result = _open_dnf()

    assert result is None


# ----------------------------------------------------- run_manual_deal
#
# Deal Bonus Card is the second form whose plate is typed in, so it
# takes the DNF dialog's typing half: ``_bind_digits_only`` keeps a
# stray letter out of the field. It takes no ``_bind_plate_gate`` --
# the dialog authors no ``entry_lbl`` to write a refusal onto, and an
# unknown-but-numeric plate stays the engine's own refusal.


def _manual_deal_harness(
    monkeypatch: pytest.MonkeyPatch, *, result: int = wx.ID_CANCEL, plate: str = ""
) -> _ManualDealDoubles:
    """Build the manual-deal dialog's doubles, wired into the runner.

    The control map carries exactly the names the runner looks up, so
    a lookup the dialog does not author (``entry_lbl``, the DNF gate's
    refusal line) fails the test rather than passing silently.
    ``_run_dialog`` is stubbed, so the recorded-defaults step never
    looks a control up in the double; it clicks OK -- as the operator
    would -- when *result* is ``wx.ID_OK`` and then answers *result*.
    """
    dialog = _StubDialog(ids.MANUAL_DEAL_DLG)
    plate_input = _RecordingPlateInput(plate)
    reason_input = _RecordingReasonInput()
    ok_btn = _RecordingButton()
    _patch_load(monkeypatch, dialog)
    _patch_controls(
        monkeypatch,
        {
            ids.PLATE_INPUT: plate_input,
            ids.REASON_INPUT: reason_input,
            "wxID_OK": ok_btn,
        },
    )

    def _run(_dialog: object, _frame: object) -> int:
        if result == wx.ID_OK:
            ok_btn.click()
        return result

    monkeypatch.setattr(corrections, "_run_dialog", _run)
    return _ManualDealDoubles(dialog, plate_input, reason_input, ok_btn)


def _open_manual_deal() -> corrections.ManualDeal | None:
    """Open the Deal Bonus Card dialog through the runner under test."""
    return corrections.run_manual_deal(object(), frame=object(), plate="")


def test_run_manual_deal_given_a_plate_binds_its_keystroke_filter_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plate field filters keystrokes as the operator types."""
    doubles = _manual_deal_harness(monkeypatch)

    _open_manual_deal()

    assert len(doubles.plate_input.char_handlers) == 1


@pytest.mark.parametrize(
    ("key_code", "expected"),
    [(ord("5"), True), (ord("A"), False)],
    ids=["digit_allowed", "letter_swallowed"],
)
def test_run_manual_deal_given_a_keystroke_lets_the_bound_filter_decide(
    monkeypatch: pytest.MonkeyPatch, key_code: int, *, expected: bool
) -> None:
    """The bound handler skips a digit key, consumes the rest."""
    doubles = _manual_deal_harness(monkeypatch)
    _open_manual_deal()  # arrange: the runner binds the filter under test

    event = doubles.plate_input.press(key_code)

    assert event.skipped is expected


# ------------------------------------- the restructured .xrc top


def _child(row: Element) -> Element:
    """Return a top-level sizeritem's own child object."""
    return row.find("object")  # type: ignore[return-value]


def _dialog_rows(dialog_name: str) -> list[Element]:
    """Return *dialog_name*'s top sizer's row elements."""
    for dialog in parse(_DIALOGS_XRC).iter("object"):
        if dialog.get("class") == "wxDialog" and dialog.get("name") == dialog_name:
            return list(dialog.find("object").findall("object"))  # type: ignore[union-attr]
    message = f"no wxDialog named {dialog_name!r}"
    raise AssertionError(message)


def _first_index(rows: list[Element], predicate: Callable[[Element], bool]) -> int:
    """Return the index of the first row matching *predicate*."""
    for index, row in enumerate(rows):
        if predicate(row):
            return index
    raise AssertionError("no matching row")


def _is_flex_grid(row: Element) -> bool:
    """Report whether *row*'s child is the label grid."""
    return _child(row).get("class") == "wxFlexGridSizer"


def _is_description(row: Element) -> bool:
    """Report whether *row*'s child is the consequence line."""
    child = _child(row)
    return child.get("class") == "wxStaticText" and child.findtext("label") == _VOID_DESCRIPTION


def _holds_reason_input(row: Element) -> bool:
    """Report whether *row* contains the Reason field."""
    return any(element.get("name") == ids.REASON_INPUT for element in _child(row).iter("object"))


def _void_grid() -> Element:
    """Return the void-card dialog's top two-column grid."""
    rows = _dialog_rows(ids.VOID_CARD_CONFIRM_DLG)
    return _child(rows[_first_index(rows, _is_flex_grid)])


def _void_row_order() -> tuple[int, int, int]:
    """Return the grid, description and Reason row indexes."""
    rows = _dialog_rows(ids.VOID_CARD_CONFIRM_DLG)
    return (
        _first_index(rows, _is_flex_grid),
        _first_index(rows, _is_description),
        _first_index(rows, _holds_reason_input),
    )


def _void_description_label() -> str | None:
    """Return the restructured top's consequence-line text."""
    rows = _dialog_rows(ids.VOID_CARD_CONFIRM_DLG)
    return _child(rows[_first_index(rows, _is_description)]).findtext("label")


def test_void_card_dialog_given_its_restructured_top_grids_card_and_entry() -> None:
    """The top is a two-column grid holding card_lbl + entry_lbl."""
    grid = _void_grid()
    cells = [_child(item) for item in grid.findall("object")]

    assert grid.get("class") == "wxFlexGridSizer"
    assert (grid.findtext("cols"), grid.findtext("vgap"), grid.findtext("hgap")) == (
        "2",
        "6",
        "8",
    )
    assert grid.findtext("growablecols") == "1"
    assert [cell.findtext("label") for cell in cells if cell.get("name") is None] == [
        "Card",
        "Entry",
    ]
    assert [cell.get("name") for cell in cells if cell.get("name") is not None] == [
        ids.CARD_LBL,
        ids.ENTRY_LBL,
    ]


def test_void_card_dialog_given_its_restructured_top_puts_copy_below_the_grid() -> None:
    """The description sits below the grid, above the Reason row."""
    grid_index, copy_index, reason_index = _void_row_order()

    assert grid_index < copy_index < reason_index
    assert _void_description_label() == (
        "Removes the card from the entry's scored hand. Audit-logged."
    )
