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
    """A ``wx.TextCtrl`` double recording value and enablement."""

    def __init__(self, value: str = "") -> None:
        """Start holding *value*, with no enablement applied."""
        self._value = value
        self.enabled: bool | None = None

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
        """No-op; the reason gate refocuses the field it is given."""
        return


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
