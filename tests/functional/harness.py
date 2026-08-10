# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx driver for the functional smoke suite (E1.3.3).

The reusable harness later EPICs' UI tests build on: load a window
from the packaged ``.xrc`` resources by its frozen name, find a
control by name, drive it, screenshot it, and close it -- all
against the real wxWidgets toolkit, never a mock. plan.md section 4
names this the pytest + ``FindWindowByName`` + direct-injection
strategy; this module is that strategy's implementation.

Measured on wxPython 4.3.1 / wxWidgets 3.3.3 (macOS), all
reproduced with throwaway scripts before being encoded here:

* ``wx.Window.FindWindowByName``/``FindWindowById`` are exposed as
  *static* methods that default to searching every top-level window
  in the process when no ``parent`` is given. Calling them as
  ``some_window.FindWindowByName(name)`` silently drops
  ``some_window`` and can resolve a same-named control that belongs
  to a different, still-alive window (``plate_input`` exists in four
  windows; every stock button name exists in a dozen). Every lookup
  here passes the loaded window explicitly as ``parent`` to scope
  the search to it.
* ``wx.Dialog``'s default ``Close()`` only ``Hide()``s it -- unlike a
  frame, whose default close handler destroys it outright. Both
  cases are handled by checking ``IsBeingDeleted()`` before an
  explicit ``Destroy()``.
* Once a window is genuinely destroyed and the event loop has
  processed the pending deletion, the underlying C++ object is
  gone: calling *any* further method on that Python reference --
  even a harmless-looking query -- is undefined behaviour and
  reliably segfaults the interpreter (reproduced by calling
  ``IsBeingDeleted()`` a second time, after a pump, on a window
  already reaped by the first check). :func:`close_window` never
  touches its argument again after it returns, and callers must
  not either.
* ``wx.UIActionSimulator`` posts real OS-level input events. In a
  desktop session where this process is not the active, focused
  application -- measured true of the session this harness runs
  in -- ``MouseMove``/``MouseClick``/``Char`` all report ``True``
  while delivering nothing: no bound handler fires, no control
  value changes. ``Text()`` additionally raises ``TypeError`` on a
  plain ``str`` in this build. Direct event injection --
  ``control.SetValue()`` (fires ``EVT_TEXT``, confirmed) and a
  posted ``wx.CommandEvent`` (fires ``EVT_BUTTON``, confirmed) -- is
  used unconditionally here, not merely as a fallback, since it is
  the only mechanism measured to work. A real, interactive desktop
  CI session may differ; re-measure before relying on the simulator
  there (see ``tools/ci_gui_probe.py``'s ``simulator_ok`` line,
  which only checks the method exists, not that it delivers).
"""

from pathlib import Path
from typing import Any

import rivercrossing.ui as ui_package
from rivercrossing.ui import require_wx

wx = require_wx()

__all__ = [
    "ControlNotFoundError",
    "ScreenshotError",
    "WindowLoadError",
    "click",
    "close_window",
    "find_control",
    "load_menubar",
    "load_window",
    "load_xrc_resources",
    "pump",
    "run_modal",
    "screenshot",
    "select_choice",
    "select_radio",
    "select_row",
    "type_text",
    "xrc_directory",
]


class WindowLoadError(LookupError):
    """Raised when an XRC resource name has no matching window."""


class ControlNotFoundError(LookupError):
    """A frozen control name did not resolve in its window."""


class ScreenshotError(OSError):
    """Raised when a window's bitmap cannot be written to disk."""


def xrc_directory() -> Path:
    """Return the packaged ``ui/xrc/`` directory.

    Resolved relative to the installed ``rivercrossing.ui`` package
    -- the same pattern ``cards_imagelist.cards_dir`` uses -- so this
    also works from a built wheel, not just an editable checkout
    (pyproject.toml ships ``ui/xrc/*.xrc`` as package data).
    """
    return Path(ui_package.__file__).resolve().parent / "xrc"


def load_xrc_resources() -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Load every packaged ``.xrc`` file into the global resource.

    ``wx.xrc.XmlResource.Get()`` is a process-wide singleton and
    ``Load`` is idempotent, so calling this more than once in a
    session is harmless; a session-scoped fixture calls it exactly
    once.
    """
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    resource = wx.xrc.XmlResource.Get()
    for path in sorted(xrc_directory().glob("*.xrc")):
        resource.Load(str(path))
    return resource


def pump() -> None:
    """Process one round of the event queue.

    The only wait primitive this harness uses (project-plan.md
    section 4: event-driven waits, never a bare ``sleep``). A
    deferred ``Destroy()`` and a posted ``CommandEvent`` both need
    one of these to actually take effect.
    """
    wx.Yield()


def load_window(resource: Any, name: str, *, frame: bool) -> Any:  # noqa: ANN401
    """Load the top-level window called *name* from *resource*.

    Args:
        resource: The ``wx.xrc.XmlResource`` returned by
            :func:`load_xrc_resources`.
        name: The frozen XRC name (``ui/ids.py``).
        frame: ``True`` for the two ``LoadFrame`` windows
            (``main_frame``, ``results_frame``); ``False`` for every
            ``LoadDialog`` window.

    Returns:
        The loaded, not-yet-shown window.

    Raises:
        WindowLoadError: If *resource* has no window named *name*
            (``LoadFrame``/``LoadDialog`` return ``None`` rather
            than raise -- measured -- which would otherwise surface
            as a confusing ``AttributeError`` on first use).
    """
    window = resource.LoadFrame(None, name) if frame else resource.LoadDialog(None, name)
    if window is None:
        kind = "LoadFrame" if frame else "LoadDialog"
        raise WindowLoadError(f"{kind}(None, {name!r}) found no matching XRC resource")
    return window


def load_menubar(resource: Any, name: str) -> Any:  # noqa: ANN401
    """Load the menu bar called *name* from *resource*.

    ``LoadMenuBar`` is a separate load path from
    ``LoadFrame``/``LoadDialog``: a menu bar is not a window, and its
    name never resolves through ``FindWindowByName`` once attached
    to a frame -- its XRC handler drops the name (measured). Callers
    that need to walk its items (E1.4.1's menu-coverage suite) use
    the returned ``wx.MenuBar`` object directly instead.

    Raises:
        WindowLoadError: If *resource* has no menu bar named *name*.
    """
    menubar = resource.LoadMenuBar(None, name)
    if menubar is None:
        raise WindowLoadError(f"LoadMenuBar(None, {name!r}) found no matching XRC resource")
    return menubar


def find_control(window: Any, name: str) -> Any:  # noqa: ANN401
    """Return the control called *name* inside *window*.

    The lookup always passes *window* as the explicit ``parent``
    argument (see the module docstring): omitting it lets the
    search default to every top-level window in the process, which
    can silently resolve a same-named control that belongs to a
    different window.

    Raises:
        ControlNotFoundError: If *name* does not resolve inside
            *window*, naming both so the failure is diagnosable
            without falling through to a bare ``AttributeError`` on
            a ``None`` result.
    """
    control = wx.Window.FindWindowByName(name, window)
    if control is None:
        raise ControlNotFoundError(f"window {window.GetName()!r} has no control named {name!r}")
    return control


def click(window: Any, name: str) -> None:  # noqa: ANN401
    """Click the button named *name* in *window*.

    Direct event injection (see the module docstring): posts the
    ``wx.CommandEvent`` a real click would generate, rather than
    relying on ``wx.UIActionSimulator``, which does not deliver
    input in this harness's session.
    """
    button = find_control(window, name)
    event = wx.CommandEvent(wx.EVT_BUTTON.typeId, button.GetId())
    event.SetEventObject(button)
    button.GetEventHandler().ProcessEvent(event)
    pump()


def type_text(window: Any, name: str, text: str) -> None:  # noqa: ANN401
    """Type *text* into the text control named *name* in *window*.

    ``SetValue`` fires ``wx.EVT_TEXT`` the same way real typing
    does (measured), which is what a bound presenter listens for.
    """
    control = find_control(window, name)
    control.SetValue(text)
    pump()


def select_choice(window: Any, name: str, item_label: str) -> None:  # noqa: ANN401
    """Select *item_label* in the ``wx.Choice`` named *name*.

    ``wx.Choice.SetSelection`` does not itself generate a
    ``wx.EVT_CHOICE`` (documented wx behaviour, the same silence
    :func:`click`'s own docstring notes for a plain ``SetValue``
    on a button) -- the event a real selection would generate is
    posted directly instead, this module's one working mechanism
    (module docstring).

    Raises:
        ControlNotFoundError: If *name* does not resolve inside
            *window*.
        ValueError: If *item_label* is not one of the choice's
            current items.
    """
    control = find_control(window, name)
    index = control.FindString(item_label)
    if index == wx.NOT_FOUND:
        raise ValueError(f"choice {name!r} has no item labelled {item_label!r}")
    control.SetSelection(index)
    event = wx.CommandEvent(wx.EVT_CHOICE.typeId, control.GetId())
    event.SetEventObject(control)
    control.GetEventHandler().ProcessEvent(event)
    pump()


def select_radio(window: Any, name: str) -> None:  # noqa: ANN401
    """Select the ``wx.RadioButton`` named *name*, firing its event.

    ``wx.RadioButton.SetValue(True)`` clears every other member of
    its own XRC-declared group (documented wx behaviour: setting one
    radio's value clears its siblings), but -- the same silence
    :func:`select_choice`'s own docstring notes for ``wx.Choice``
    -- it does not itself generate a ``wx.EVT_RADIOBUTTON`` (measured).
    The event a real click would generate is posted directly instead,
    this module's one working mechanism (module docstring).

    Raises:
        ControlNotFoundError: If *name* does not resolve inside
            *window*.
    """
    control = find_control(window, name)
    control.SetValue(True)  # noqa: FBT003 -- wx API takes a positional bool
    event = wx.CommandEvent(wx.EVT_RADIOBUTTON.typeId, control.GetId())
    event.SetEventObject(control)
    control.GetEventHandler().ProcessEvent(event)
    pump()


def select_row(window: Any, name: str, row: int) -> None:  # noqa: ANN401
    """Select *row* in the ``wx.dataview.DataViewCtrl`` named *name*.

    Measured cross-platform (PR #8's CI, run 31344728049): on macOS,
    ``DataViewCtrl.Select`` fires ``wx.dataview.
    EVT_DATAVIEW_SELECTION_CHANGED`` on this wx build by itself, so an
    earlier revision of this function posted nothing further. That
    measurement turned out to be generic-control behaviour, not
    universal: MSW's *native* ``DataViewCtrl`` follows wx's own
    documented convention that a programmatic selection change emits
    no event at all, so on windows-latest CI the presenter never saw
    the selection and every save/delete-dependent test silently
    no-op'd. The event is now posted unconditionally after ``Select``
    -- the same ``wx.dataview.DataViewEvent(type, control, item)``
    3-arg constructor E3.2's own probe already verified
    (``test_rider_editor.py``'s stale-selection pin uses the
    identical call) -- which double-fires the handler on macOS;
    ``RidersPresenter.on_row_selected`` is idempotent by contract, and
    the full VM suite stayed green with this change (this fix's own
    gauntlet).

    Raises:
        ControlNotFoundError: If *name* does not resolve inside
            *window*.
    """
    import wx.dataview  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    control = find_control(window, name)
    item = control.GetModel().GetItem(row)
    control.Select(item)
    event = wx.dataview.DataViewEvent(wx.dataview.wxEVT_DATAVIEW_SELECTION_CHANGED, control, item)
    control.GetEventHandler().ProcessEvent(event)
    pump()


def run_modal(dialog: Any, *, dismiss_with: int) -> int:  # noqa: ANN401
    """Show *dialog* modally, auto-dismissing it with *dismiss_with*.

    ``ShowModal`` blocks the caller until the dialog ends, so the
    dismissal is scheduled first: the ``wx.CallAfter`` runs once wx
    starts pumping events inside the modal loop, and the call
    returns instead of hanging forever with no user present to
    click anything.

    Args:
        dialog: A loaded, not-yet-shown dialog.
        dismiss_with: The id ``EndModal`` is called with, e.g.
            ``wx.ID_OK``.

    Returns:
        ``ShowModal``'s return value (equal to *dismiss_with*).
    """
    wx.CallAfter(dialog.EndModal, dismiss_with)
    return dialog.ShowModal()


def screenshot(window: Any, destination: Path) -> Path:  # noqa: ANN401
    """Save a PNG of *window*'s client area to *destination*.

    Uses the same ``MemoryDC``-blit-from-``ClientDC`` technique as
    ``tools/ci_gui_probe.py``, the CI job that first proved this
    desktop session can render at all.

    Raises:
        ScreenshotError: If the bitmap cannot be written.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = window.GetClientSize()
    bitmap = wx.Bitmap(size.width, size.height)
    memory_dc = wx.MemoryDC(bitmap)
    memory_dc.Blit(0, 0, size.width, size.height, wx.ClientDC(window), 0, 0)
    del memory_dc
    if not bitmap.SaveFile(str(destination), wx.BITMAP_TYPE_PNG):
        raise ScreenshotError(f"could not save screenshot to {destination}")
    return destination


def close_window(window: Any) -> bool:  # noqa: ANN401
    """Close and destroy *window*, then let the deletion complete.

    A dialog's default ``Close()`` only ``Hide()``s it -- unlike a
    frame, whose default handler destroys it outright (measured) --
    so both cases are covered by checking ``IsBeingDeleted()``
    before an explicit ``Destroy()``. The event loop is pumped
    exactly once more after that.

    The caller must not touch *window* again after this returns:
    once the pump completes a pending deletion, the underlying C++
    object is gone, and any further method call on it -- even a
    harmless-looking query -- segfaults the interpreter (measured).

    Returns:
        ``Close()``'s return value. ``False`` would mean a bound
        handler vetoed the close; these raw XRC windows carry no
        such handler, but the value is surfaced for the caller to
        assert on rather than assumed.
    """
    closed = window.Close()
    if not window.IsBeingDeleted():
        window.Destroy()
    pump()
    return closed
