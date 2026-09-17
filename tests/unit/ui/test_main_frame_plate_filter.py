# SPDX-License-Identifier: GPL-3.0-only
"""T7: the console's plate-entry character filter (``MainFrame``).

``plate_input`` takes a plate number or one of ``console``'s miss
symbols -- the scorer's shorthand for a crossing whose number was
missed -- and nothing else. ``MainFrame.wire_entry`` binds
``wx.EVT_CHAR`` on the field to ``MainFrame._on_plate_char``:

- an **ASCII** digit or a miss symbol ``Skip()``s on to the control,
  so wx shows the character;
- every control key ``Skip()``s too: ``GetUnicodeKey()`` reports
  ``wx.WXK_NONE`` for the arrows and function keys, while Backspace (8)
  and Enter (13) arrive as non-printable characters -- the field's own
  ``wxTE_PROCESS_ENTER`` submit depends on Enter passing through;
- any other printable character is consumed -- no ``Skip()``, so it
  never reaches the control and wx never beeps at a mistyped key.

One filter for every plate model: a rider-pooled ride and a relay one
accept the same alphabet, with no relay off-switch.

The ASCII pin is deliberate. ``str.isdigit()`` alone admits the
non-ASCII digits ``٣`` and ``²``, which no scorer types into a plate
field; those rows below fail if the digit arm ever drops its
``isascii()`` half.

Constructing a real console needs a desktop, so this module drives the
real ``wire_entry`` against a shell plus a stand-in for the slice of
``wx`` it binds with -- ``test_automatic_backup.py``'s own headless
pattern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from rivercrossing.ui.views import main_frame

if TYPE_CHECKING:
    from collections.abc import Callable

# ``console.MISS_SYMBOLS``, spelled out rather than imported: the
# filter's symbol arm must fail if that alphabet ever changes.
_MISS_SYMBOLS = ("=", "/", "+", "-", ".")

# Every printable character the filter refuses: letters, punctuation
# that is no miss symbol, a space, one past the digit range, and the
# non-ASCII characters a bare ``str.isdigit()`` would wrongly admit.
_DISALLOWED = (
    pytest.param("A", id="upper_letter"),
    pytest.param("z", id="lower_letter"),
    pytest.param("#", id="hash"),
    pytest.param(" ", id="space"),
    pytest.param(":", id="one_past_the_digit_range"),
    pytest.param("\u00e9", id="accented_letter"),
    pytest.param("\u0663", id="arabic_indic_digit"),
    pytest.param("\u00b2", id="superscript_digit"),
)


class _FakeWx:
    """The slice of ``wx`` ``wire_entry`` binds with."""

    EVT_BUTTON = "evt:button"
    EVT_CHAR = "evt:char"
    EVT_TEXT_ENTER = "evt:text-enter"
    # wxPython 4.3.1 / wxWidgets 3.3.3: the value ``GetUnicodeKey()``
    # reports for a key that carries no character at all -- the arrows
    # and the function keys. Mirrored because the real handler reads it.
    WXK_NONE = 0


class _BindRecorder:
    """A wx control double recording every ``Bind`` and its value."""

    def __init__(self, value: str = "") -> None:
        """Start with no bindings, showing *value*."""
        self.binds: list[tuple[object, Callable[[Any], None]]] = []
        self._value = value

    def Bind(  # noqa: N802 -- wx API name
        self, event: object, handler: Callable[[Any], None]
    ) -> None:
        """Record one binding."""
        self.binds.append((event, handler))

    def GetValue(self) -> str:  # noqa: N802 -- wx API name
        """Return the text this double shows."""
        return self._value

    def handler_for(self, event: object) -> Callable[[Any], None]:
        """Return the handler bound to *event*.

        Raises ``StopIteration`` when nothing is bound to it.
        """
        return next(handler for bound, handler in self.binds if bound is event)


class _CharEvent:
    """A ``wx.KeyEvent`` double: its key and whether it was skipped."""

    def __init__(self, unicode_key: int) -> None:
        """Report *unicode_key* from ``GetUnicodeKey()``."""
        self._unicode_key = unicode_key
        self.skipped = False

    def GetUnicodeKey(self) -> int:  # noqa: N802 -- wx API name
        """Return the unicode value (``wx.WXK_NONE`` for controls)."""
        return self._unicode_key

    def Skip(self) -> None:  # noqa: N802 -- wx API name
        """Record that the key was let through to the control."""
        self.skipped = True


class _ConsoleShell:
    """A ``MainFrame`` double owning what ``wire_entry`` binds."""

    # The real handler, so the pins below drive the production filter.
    _on_plate_char = main_frame.MainFrame._on_plate_char

    def __init__(self) -> None:
        """Seed the controls ``wire_entry`` binds and its callback."""
        self.plate_input = _BindRecorder()
        self.record_btn = _BindRecorder()
        self._on_submit: Callable[[str], None] | None = None


def _wire_entry(monkeypatch: pytest.MonkeyPatch) -> tuple[_ConsoleShell, _FakeWx]:
    """Run the real ``wire_entry`` with a stand-in for ``wx``."""
    fake_wx = _FakeWx()
    monkeypatch.setattr(main_frame, "wx", fake_wx)
    console = _ConsoleShell()
    main_frame.MainFrame.wire_entry(console, lambda _text: None)
    return console, fake_wx


def _filter(console: _ConsoleShell, fake_wx: _FakeWx) -> Callable[[Any], None]:
    """Return the ``EVT_CHAR`` handler the wired plate field carries."""
    return console.plate_input.handler_for(fake_wx.EVT_CHAR)


# ------------------------------------------------------- the binding


def test_wire_entry_binds_the_plate_filter_to_the_char_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The filter rides the plate field's own ``EVT_CHAR`` binding."""
    console, fake_wx = _wire_entry(monkeypatch)

    bound = [event for event, _handler in console.plate_input.binds]

    assert bound == [fake_wx.EVT_TEXT_ENTER, fake_wx.EVT_CHAR]


# ------------------------------------------- refused printable keys


@pytest.mark.parametrize("character", _DISALLOWED)
def test_on_plate_char_given_a_disallowed_printable_character_consumes_the_key(
    monkeypatch: pytest.MonkeyPatch, character: str
) -> None:
    """Consumed: the field never sees the key, so wx is silent."""
    console, fake_wx = _wire_entry(monkeypatch)
    event = _CharEvent(unicode_key=ord(character))

    _filter(console, fake_wx)(event)

    assert event.skipped is False


# -------------------------------------------- allowed printable keys


@pytest.mark.parametrize("character", tuple("0123456789"))
def test_on_plate_char_given_an_ascii_digit_skips_the_key(
    monkeypatch: pytest.MonkeyPatch, character: str
) -> None:
    """0-9 are plate characters: wx gets the key and shows the digit."""
    console, fake_wx = _wire_entry(monkeypatch)
    event = _CharEvent(unicode_key=ord(character))

    _filter(console, fake_wx)(event)

    assert event.skipped is True


@pytest.mark.parametrize("character", _MISS_SYMBOLS)
def test_on_plate_char_given_a_miss_symbol_skips_the_key(
    monkeypatch: pytest.MonkeyPatch, character: str
) -> None:
    """``= / + - .`` are the scorer's miss shorthand: wx gets it."""
    console, fake_wx = _wire_entry(monkeypatch)
    event = _CharEvent(unicode_key=ord(character))

    _filter(console, fake_wx)(event)

    assert event.skipped is True


# ------------------------------------------------ control keys


def test_on_plate_char_given_a_control_key_skips_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``WXK_NONE`` key (an arrow, a function key) passes through."""
    console, fake_wx = _wire_entry(monkeypatch)
    event = _CharEvent(unicode_key=fake_wx.WXK_NONE)

    _filter(console, fake_wx)(event)

    assert event.skipped is True


@pytest.mark.parametrize(
    "unicode_key",
    [pytest.param(8, id="backspace"), pytest.param(13, id="enter")],
)
def test_on_plate_char_given_a_non_printable_character_skips_the_key(
    monkeypatch: pytest.MonkeyPatch, unicode_key: int
) -> None:
    """Backspace/Enter are non-printable, so they reach wx untouched.

    Both report their own unicode value -- 8 and 13 -- not
    ``WXK_NONE``, so the WXK_NONE guard alone would consume them and
    break text editing and the field's ``wxTE_PROCESS_ENTER`` submit.
    """
    console, fake_wx = _wire_entry(monkeypatch)
    event = _CharEvent(unicode_key=unicode_key)

    _filter(console, fake_wx)(event)

    assert event.skipped is True
