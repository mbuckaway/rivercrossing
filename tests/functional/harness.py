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

from __future__ import annotations

import gc
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

import rivercrossing.ui as ui_package
from rivercrossing.ui import require_wx

wx = require_wx()

__all__ = [
    "ControlNotFoundError",
    "ScreenshotError",
    "WindowLoadError",
    "WindowNameConflictError",
    "WindowStillResolvingError",
    "click",
    "close_window",
    "dismiss_modal",
    "find_control",
    "fire_menu_event",
    "flush_deferred_deletions",
    "load_menubar",
    "load_window",
    "load_xrc_resources",
    "pump",
    "recent_wx_log",
    "release_main_window",
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


class WindowNameConflictError(LookupError):
    """A leaked top-level window already owns the requested frozen name.

    Raised at the entry of :func:`load_window_verified` when
    ``wx.GetTopLevelWindows()`` already contains a window whose name
    matches the requested one: a leaked same-named window would shadow
    every subsequent name-scoped assertion, so the load fails loudly
    instead of proceeding. The message names the requested name and the
    stale window's native handle.
    """


class ControlNotFoundError(LookupError):
    """A frozen control name did not resolve in its window."""


class WindowStillResolvingError(LookupError):
    """``close_window(strict=True)`` could not prove the window reaped.

    Raised when the closed window's own native handle is still present
    in ``wx.GetTopLevelWindows()`` after the close settle loop exhausts
    :data:`_CLOSE_SETTLE_ATTEMPTS` -- its deferred destruction never
    completed. The message names the closed window and its handle.
    """


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


# 5, not 25 (the _CLOSE_SETTLE_ATTEMPTS/FIND_SETTLE_ATTEMPTS mirror
# this used to be): measured on windows-latest CI, MSW's own
# UpdateUI idle chatter keeps ProcessIdle() reporting True almost
# every pass, so a large bound multiplies cost without improving the
# reap -- wxAppBase::ProcessIdle calls DeletePendingObjects
# unconditionally on the very first pass regardless of the bound.
_FLUSH_IDLE_ATTEMPTS = 5


def flush_deferred_deletions() -> None:
    """Flush wx's deferred-deletion queue by driving idle processing.

    wxWidgets frees a ``Destroy()``d *top-level* window (frame or
    dialog -- the windows this suite tears down) only during idle
    processing: ``wxTopLevelWindowBase::Destroy()`` appends the
    window to ``wxPendingDelete`` (the same list
    ``wxApp::ScheduleForDestruction`` manages) and
    ``wxAppConsoleBase::DeletePendingObjects()`` deletes it from
    ``ProcessIdle()``. Child windows are the exception: their
    ``Destroy()`` is a synchronous ``delete this`` (wxWidgets 3.3.3,
    ``wincmn.cpp``; there is no ``DestroyLater`` in that release).
    Idle processing only runs once the event queue drains. Measured
    (probe script, this task's own scratchpad, cross-checked against
    PR #8's CI runs 31390187217 / 31390190295): with no ``MainLoop``
    running, ``wx.EventLoopBase.GetActive()`` is ``None``, and a bare
    ``wx.SafeYield()`` reaps a deferred delete on an idle host but not
    reliably under a hosted runner's load, where the queue never
    drains far enough to reach idle. ``EventLoopBase.ProcessIdle()``
    drives the idle machinery directly, without waiting for the queue
    to drain first, and reaped the probe's own deferred delete on
    every trial regardless of load. wxPython's own test framework
    (``unittests/wtc.py``) documents the same requirement: without a
    running ``MainLoop``, a useful ``Yield`` needs a created and
    activated event loop first.

    Creates and activates a throwaway loop only when none is already
    active -- :func:`run_modal`'s own ``ShowModal`` call leaves one
    active, and this must not disturb it -- so this is safe to call
    from either context.
    """
    loop = wx.EventLoopBase.GetActive()
    if loop is not None:
        _drain_idle(loop)
        return
    loop = wx.GetApp().GetTraits().CreateEventLoop()
    activator = wx.EventLoopActivator(loop)
    try:
        _drain_idle(loop)
    finally:
        del activator


def _drain_idle(loop: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Yield *loop* once, then process its idle queue until it settles.

    Bounded by :data:`_FLUSH_IDLE_ATTEMPTS`: ``ProcessIdle()`` reports
    whether more idle work remains, and a source that never settles
    must not hang the caller.
    """
    loop.YieldFor(wx.EVT_CATEGORY_ALL)
    attempts = 0
    while loop.ProcessIdle() and attempts < _FLUSH_IDLE_ATTEMPTS:
        attempts += 1


def pump() -> None:
    """Process one round of the event queue.

    The only wait primitive this harness uses (project-plan.md
    section 4: event-driven waits, never a bare ``sleep``). A
    posted ``CommandEvent`` needs one of these to actually take
    effect.

    ``wx.SafeYield()`` drains the event queue -- posted
    ``CommandEvent``s among them -- the same call ``ui.views.
    _support.find_control``'s own settle retry relies on for the
    identical class of problem. It is not always enough on its own
    for a deferred ``Destroy()``, though: measured (PR #8's CI runs
    31390187217/31390190295), a hosted runner's event queue can
    starve idle processing under load, so a bare ``SafeYield`` never
    reaches the point wx actually frees a pending delete.

    :func:`flush_deferred_deletions` reaches it regardless of queue
    load, but this function no longer calls it: measured on
    windows-latest CI (run 31392502719), driving it from every
    single ``pump()`` call turned one functional job's normal ~90s
    runtime into 5h59m28s before the 6-hour cap killed it. MSW's own
    ``ProcessIdle()`` keeps reporting more idle work on almost every
    pass (:data:`_FLUSH_IDLE_ATTEMPTS`'s own comment), so the bounded
    drain was never cheap there, and multiplying it by every one of
    this suite's thousands of ``pump()`` calls multiplied that cost
    across the whole run. Only :func:`close_window`'s own settle
    loop calls :func:`flush_deferred_deletions` now, at teardown,
    where the call count is orders of magnitude smaller.
    """
    wx.SafeYield()


# --- Phase 3: wx log capture (Fault B class-2 diagnostic) ---
#
# XRC's silent error-and-skip path (Addendum 2, class 2): when a nested
# XRC node fails to create, wxWidgets logs one line -- "Creating %s
# failed" -- and omits that node AND its subtree while the rest of the
# tree loads. The suite's conftest redirects the active wx log target to
# wx.LogStderr() for the whole session (an exit-hang fix), so that line
# is effectively invisible mid-run and a missing subtree surfaces only
# as a later stochastic find_control LookupError. These helpers capture
# the lines a load emits into an in-memory buffer, queryable afterwards
# via :func:`recent_wx_log` -- capture-and-surface ONLY, no behavior
# change.

_WX_LOG_MAX_LINES = 100
"""Bound on :func:`recent_wx_log`'s lines (one load's output)."""


class _WxLogCapture:
    """Mutable capture state, kept off the module globals.

    The buffer, the pre-capture log target, the nesting depth and the
    captured lines are per-load state; a small state object (rather than
    module-level ``global`` rebinding) keeps :func:`_wx_log_capture`
    re-entrant without ``global`` statements.
    """

    def __init__(self) -> None:
        self.buffer: Any | None = None
        self.previous: Any | None = None
        self.depth = 0
        self.lines: tuple[str, ...] = ()


_wx_capture = _WxLogCapture()


@contextmanager
def _wx_log_capture() -> Iterator[None]:
    """Temporarily capture wx log output into an in-memory buffer.

    While this context is active the active wx log target is a
    ``wx.LogBuffer``, so a ``wxLogError`` a load emits (e.g. "Creating
    %s failed") is stored instead of going straight to the suite's
    ``wx.LogStderr()`` target; :func:`recent_wx_log` makes the stored
    lines queryable afterwards.

    Re-entrant: :func:`load_window_verified` opens a capture block and
    calls :func:`load_window`, which opens its own, so the inner block
    must not clear or restore anything until the outermost block ends --
    a depth counter guards that. On the outermost exit the buffer's
    lines are snapshotted BEFORE the target is reinstated: measured,
    ``wxLogBuffer`` clears itself when the target switch flushes it, so
    reading ``GetBuffer()`` after the restore would always be empty.
    Restoring the pre-capture target (the session's ``wx.LogStderr()``)
    keeps conftest's exit-hang fix intact and still delivers the
    captured lines to stderr via that flush -- coexist, don't replace.
    """
    if _wx_capture.depth == 0:
        if _wx_capture.buffer is None:
            _wx_capture.buffer = wx.LogBuffer()
        else:
            _wx_capture.buffer.Clear()
        _wx_capture.previous = wx.Log.GetActiveTarget()
        wx.Log.SetActiveTarget(_wx_capture.buffer)
    _wx_capture.depth += 1
    try:
        yield
    finally:
        _wx_capture.depth -= 1
        if _wx_capture.depth == 0:
            _wx_capture.lines = tuple(
                line for line in _wx_capture.buffer.GetBuffer().splitlines() if line
            )[-_WX_LOG_MAX_LINES:]
            wx.Log.SetActiveTarget(_wx_capture.previous)
            _wx_capture.previous = None


def recent_wx_log() -> tuple[str, ...]:
    """Return the wx log lines captured during the most recent load.

    Phase 3's class-2 diagnostic query (Addendum 2): after a window load
    or control lookup fails, callers can show the wx log lines XRC
    emitted -- e.g. ``wxLogError("Creating %s failed")`` -- so a missing
    subtree is diagnosable instead of surfacing only as a LookupError.
    The capture is bounded (last :data:`_WX_LOG_MAX_LINES` lines) and
    reset at the start of each :func:`load_window` /
    :func:`load_window_verified` call, so this reflects only the most
    recent load; ``()`` until the first load runs.
    """
    return _wx_capture.lines


def load_window(resource: Any, name: str, *, frame: bool) -> Any:  # noqa: ANN401
    """Load the top-level window called *name* from *resource*.

    Captures wx log output while the load runs, for
    :func:`recent_wx_log`.

    Args:
        resource: The ``wx.xrc.XmlResource`` returned by
            :func:`load_xrc_resources`.
        name: The frozen XRC name (``ui/ids.py``).
        frame: ``True`` for the one ``LoadFrame`` window
            (``main_frame``); ``False`` for every ``LoadDialog``
            window (``results_dlg`` included).

    Returns:
        The loaded, not-yet-shown window.

    Raises:
        WindowLoadError: If *resource* has no window named *name*
            (``LoadFrame``/``LoadDialog`` return ``None`` rather
            than raise -- measured -- which would otherwise surface
            as a confusing ``AttributeError`` on first use).
    """
    with _wx_log_capture():
        window = resource.LoadFrame(None, name) if frame else resource.LoadDialog(None, name)
        if window is None:
            kind = "LoadFrame" if frame else "LoadDialog"
            raise WindowLoadError(f"{kind}(None, {name!r}) found no matching XRC resource")
        return window


def load_window_verified(resource: Any, name: str, *, frame: bool) -> Any:  # noqa: ANN401
    """Load *name* and verify its spec'd controls (Fault B).

    Opt-in variant of :func:`load_window` for the sites that build a
    window ONCE per file (the module-scoped ``shared_*`` fixtures) or
    once per test (the ``_show`` helpers): CI has measured the
    process-global ``wx.xrc.XmlResource`` building an incomplete
    window under worker load -- a whole subtree skipped, different
    per load (``results_dlg`` with an empty staticbox,
    ``ride_setup_dlg`` missing its radio group, ``rider_editor_dlg``
    missing its whole action staticbox) -- and a degraded load errors
    the whole module or the single test with no rerun able to absorb
    it (the retry reloads from the same degraded singleton). The
    window is verified against ``pages.WINDOWS``' per-window control
    contract; only a genuinely incomplete build is rebuilt, once,
    from a fresh private resource (``test_bundle_smoke.py``'s
    ``bundled_xrc`` isolation pattern), built BEFORE the degraded
    window is torn down so its controls cannot land on the degraded
    window's just-freed addresses (the wrapper-cache corruption
    ``_support.find_control`` documents). No settle loop: yielding
    during the verification is exactly the event processing the
    degradation hides in. Healthy builds cost one extra name-walk and
    nothing else.

    Entry-only guard: before the first load, a top-level window that
    already owns *name* in ``wx.GetTopLevelWindows()`` raises
    :class:`WindowNameConflictError`. ``FindWindowByName`` with
    ``parent=None`` searches every top-level window in creation order,
    so a leaked same-named window would shadow every later name-scoped
    assertion in an order-dependent way -- a loud failure, never a
    silent contaminant. The guard is entry-only by design: the rebuild
    path's two same-named frames (degraded + fresh) coexist
    deliberately, and the rebuild calls ``fresh.LoadFrame`` directly,
    never re-entering this function.

    Raises:
        WindowNameConflictError: If a top-level window already owns
            *name* (a leaked same-named window); names the requested
            name and the stale window's handle.
        WindowLoadError: If no window named *name* can be loaded.
        ControlNotFoundError: If both the first load and the fresh
            rebuild are incomplete; the message carries the rebuilt
            window's first-level-child inventory.

    Captures wx log output across the whole verify -- the first load,
    the verification walk, and any fresh rebuild -- for
    :func:`recent_wx_log`, so a degraded build's "Creating %s failed"
    line is queryable even when the rebuild path runs.
    """
    stale = next(
        (w for w in wx.GetTopLevelWindows() if w.GetName() == name),
        None,
    )
    if stale is not None:
        raise WindowNameConflictError(
            f"load_window_verified({name!r}): a top-level window already owns "
            f"that name (stale handle {stale.GetHandle()!r}); a leaked same-named "
            f"window would shadow the new load"
        )
    with _wx_log_capture():
        window = load_window(resource, name, frame=frame)
        if _expected_controls_resolve(window, name):
            return window
        fresh = _fresh_resource()
        rebuilt = fresh.LoadFrame(None, name) if frame else fresh.LoadDialog(None, name)
        if rebuilt is None:
            close_window(window)
            kind = "LoadFrame" if frame else "LoadDialog"
            raise WindowLoadError(f"{kind}(None, {name!r}) found no matching XRC resource")
        if not _expected_controls_resolve(rebuilt, name):
            missing = _first_missing_control(rebuilt, name)
            children = [child.GetName() for child in rebuilt.GetChildren()]
            window_name = rebuilt.GetName()
            close_window(rebuilt)
            close_window(window)
            raise ControlNotFoundError(
                f"{window_name} has no control named {missing!r} "
                f"(first-level children: {len(children)} -- {children!r})"
            )
        close_window(window)
        return rebuilt


def _fresh_resource() -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return a private ``XmlResource`` loaded from every packaged .xrc.

    The isolation pattern ``test_bundle_smoke.py``'s ``bundled_xrc``
    fixture proves: a *new* ``XmlResource`` (never the process-wide
    ``XmlResource.Get()`` singleton, whose degraded builds under
    worker load are what Fault B works around), loaded from the same
    ``ui/xrc/*.xrc`` files :func:`load_xrc_resources` loads.
    """
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    resource = wx.xrc.XmlResource()
    for path in sorted(xrc_directory().glob("*.xrc")):
        resource.Load(str(path))
    return resource


def _expected_controls_for(name: str) -> tuple[str, ...]:
    """Return *name*'s spec'd control names, or ``()`` if none.

    Fault B's completeness contract: ``pages.WINDOWS``' per-window
    ``controls`` tuples are the frozen-name inventory the whole suite
    already asserts. Imported here (function scope), not at module
    scope: ``pages`` imports ``harness`` at module level, so a
    module-level import here would be a cycle.
    """
    import pages  # noqa: PLC0415 -- cycle, see above

    for spec in pages.WINDOWS:
        if spec.name == name:
            return spec.controls
    return ()


def _expected_control_class(name: str) -> type:
    """Return the concrete wx class *name* must resolve to.

    Falls back to ``wx.Window`` for every non-``main_frame`` control.
    The single source of truth is ``MainFrame``'s own
    ``REQUIRED_CONTROL_CLASSES`` (``src/rivercrossing/ui/views/
    main_frame.py``): the name->concrete-class contract the ctor
    demands and ``ui.app._load_frame_verified`` verifies. Imported here
    (function scope), not at module scope, mirroring the ``pages``
    cycle note below: this test-infrastructure module must not drag the
    view layer in at import time, before ``require_wx`` has run.
    ``pages.WINDOWS`` carries frozen *names* only -- no concrete
    classes -- so every other window's spec'd controls return
    ``wx.Window``: no code-side view class authors a concrete class
    for them in ``pages``, so the generic
    ``isinstance(control, wx.Window)`` check is the strongest available
    contract for those.
    """
    from rivercrossing.ui.views.main_frame import (  # noqa: PLC0415 -- mirror the pages cycle note above
        REQUIRED_CONTROL_CLASSES,
    )

    return REQUIRED_CONTROL_CLASSES.get(name, wx.Window)


def _control_resolves(window: Any, control_name: str) -> bool:  # noqa: ANN401 -- wx ships no stubs
    """Return whether *control_name* resolves to its concrete class.

    *window* is the parent scope. Mirrors the settle idiom
    ``ui.views._support.find_control`` uses
    for the identical wx/SIP wrapper-cache hazard: a lookup can answer
    a stale wrapper whose Python type is wrong for the live control
    (the address-reuse signature), and the remedy is reference hygiene
    -- ``del control; gc.collect()`` evicts the stale wrapper's SIP
    pointer->wrapper entry -- then re-query, bounded. Unlike
    :func:`find_control`'s settle, this does NOT ``SafeYield``:
    yielding during verification is exactly the event processing the
    Fault-B degradation hides in (``load_window_verified``'s own
    docstring). A wrong-typed wrapper that persists past the bound
    fails the walk, sending ``load_window_verified`` down the
    fresh-rebuild path.
    """
    expected = _expected_control_class(control_name)
    control = wx.Window.FindWindowByName(control_name, window)
    attempts = 0
    while not isinstance(control, expected) and attempts < _FIND_SETTLE_ATTEMPTS:
        del control
        gc.collect()
        control = wx.Window.FindWindowByName(control_name, window)
        attempts += 1
    return isinstance(control, expected)


def _expected_controls_resolve(window: Any, name: str) -> bool:  # noqa: ANN401 -- wx ships no stubs
    """Return whether every spec'd control of *name* resolves in it.

    Each spec'd control is checked against its CONCRETE expected class
    (``MainFrame.__init__``'s demands) rather than the generic
    ``isinstance(control, wx.Window)``: a stale wrapper whose Python
    class is a *different* ``wx.Window`` subclass passes the generic
    check yet fails the ctor's ``_find(name, ConcreteClass)`` after
    ``Show()`` -- the wrong-typed-wrapper leak (E7.2.2) this gate
    closes. A transiently-stale lookup retries (``del control;
    gc.collect()``, bounded); a wrong-typed wrapper that persists
    fails the walk and sends :func:`load_window_verified` down the
    fresh-rebuild path.
    """
    return all(
        _control_resolves(window, control_name) for control_name in _expected_controls_for(name)
    )


def _first_missing_control(window: Any, name: str) -> str:  # noqa: ANN401 -- wx ships no stubs
    """Return *name*'s first spec'd control missing from *window*.

    "Missing" applies the same concrete-class test
    :func:`_expected_controls_resolve` uses, so a stale wrong-typed
    wrapper is reported as the missing control -- the name the error
    message needs -- rather than falling through to the empty string
    when every name resolves but one is stale.
    """
    for control_name in _expected_controls_for(name):
        if not _control_resolves(window, control_name):
            return control_name
    return ""


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


# 25, mirroring ui.views._support.FIND_SETTLE_ATTEMPTS: the same
# wx/SIP wrapper-cache stale-lookup hazard _support.find_control
# settles applies to this harness's own name lookups too (its
# find_control docstring).
_FIND_SETTLE_ATTEMPTS = 25


def find_control(window: Any, name: str) -> Any:  # noqa: ANN401
    """Return the control called *name* inside *window*.

    The lookup always passes *window* as the explicit ``parent``
    argument (see the module docstring): omitting it lets the
    search default to every top-level window in the process, which
    can silently resolve a same-named control that belongs to a
    different window.

    Mirrors ``ui.views._support.find_control``'s settle retry and
    type check (measured on windows-latest CI): under the wx/SIP
    wrapper-cache corruption, ``FindWindowByName`` can return a
    stale-typed wrapper for a live control, and a lookup that
    accepts it reads the wrong control's state. A ``wx.SafeYield``
    between retries flushes the deferred deletion that causes it.

    Raises:
        ControlNotFoundError: If *name* does not resolve inside
            *window* -- or resolves only to a stale, non-``wx.Window``
            wrapper -- naming both so the failure is diagnosable
            without falling through to a bare ``AttributeError`` on
            a ``None`` result.
    """
    control = wx.Window.FindWindowByName(name, window)
    attempts = 0
    while not isinstance(control, wx.Window) and attempts < _FIND_SETTLE_ATTEMPTS:
        wx.SafeYield()
        control = wx.Window.FindWindowByName(name, window)
        attempts += 1
    if control is None:
        raise ControlNotFoundError(f"window {window.GetName()!r} has no control named {name!r}")
    if not isinstance(control, wx.Window):
        raise ControlNotFoundError(
            f"window {window.GetName()!r} resolved {name!r} to a stale "
            f"{type(control).__name__} wrapper, not a wx.Window"
        )
    return control


def _clear_last_exception() -> None:
    """Drop the parked traceback after a synthetic dispatch (retention).

    wx swallows a Python exception raised inside a handler, and
    PyErr_Print parks it on ``sys`` (``last_exc`` on 3.11+, carrying
    the traceback); the frame chain then holds the failing handler's
    view and its control wrappers alive for the rest of the process,
    keeping their SIP map entries forever -- the address-reuse poison
    :func:`~rivercrossing.ui.views._support.find_control`'s docstring
    documents. :func:`fire_menu_event` has cleared this in a
    ``finally`` since the 2026-08-19 retention probe; every other
    synthetic dispatch (click/type/select) must do the same, or one
    swallowed exception mid-suite poisons a recycled C++ address.
    """
    sys.last_type = sys.last_value = sys.last_traceback = None
    sys.last_exc = None


def click(window: Any, name: str, *, require_shown: bool = False) -> None:  # noqa: ANN401
    """Click the button named *name* in *window*.

    Direct event injection (see the module docstring): posts the
    ``wx.CommandEvent`` a real click would generate, rather than
    relying on ``wx.UIActionSimulator``, which does not deliver
    input in this harness's session.

    *require_shown* is an opt-in visibility guard, default ``False``.
    The harness injects via ``ProcessEvent`` synchronously, so a
    drive can silently succeed against a hidden control -- the event
    is delivered straight to the handler with no hit-testing to
    fail. With *require_shown* set, the target's ``IsShown()`` is
    checked before injection and a hidden target raises instead,
    turning that false-fast pass into a failure.

    Raises:
        ControlNotFoundError: If *name* does not resolve inside
            *window*.
        AssertionError: If *require_shown* is set and the target
            control is not shown; names the control and its window.
    """
    button = find_control(window, name)
    if require_shown and not button.IsShown():
        raise AssertionError(f"click target {name!r} in window {window.GetName()!r} is hidden")
    event = wx.CommandEvent(wx.EVT_BUTTON.typeId, button.GetId())
    event.SetEventObject(button)
    try:
        button.GetEventHandler().ProcessEvent(event)
        pump()
    finally:
        _clear_last_exception()


def type_text(  # noqa: PLR0913 -- require_shown is click()'s F5 visibility guard
    window: Any,  # noqa: ANN401 -- wx ships no stubs
    name: str,
    text: str,
    *,
    require_shown: bool = False,
) -> None:
    """Type *text* into the text control named *name* in *window*.

    ``SetValue`` fires ``wx.EVT_TEXT`` the same way real typing
    does (measured), which is what a bound presenter listens for.

    *require_shown* is an opt-in visibility guard, default ``False``;
    see :func:`click` for the rationale. The harness injects via
    ``ProcessEvent`` synchronously, so a drive can silently succeed
    against a hidden control. With *require_shown* set, the target's
    ``IsShown()`` is checked before injection and a hidden target
    raises instead, turning that false-fast pass into a failure.

    Raises:
        ControlNotFoundError: If *name* does not resolve inside
            *window*.
        AssertionError: If *require_shown* is set and the target
            control is not shown; names the control and its window.
    """
    control = find_control(window, name)
    if require_shown and not control.IsShown():
        raise AssertionError(f"type_text target {name!r} in window {window.GetName()!r} is hidden")
    try:
        control.SetValue(text)
        pump()
    finally:
        _clear_last_exception()


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
    try:
        control.GetEventHandler().ProcessEvent(event)
        pump()
    finally:
        _clear_last_exception()


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
    try:
        control.GetEventHandler().ProcessEvent(event)
        pump()
    finally:
        _clear_last_exception()


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
    try:
        control.GetEventHandler().ProcessEvent(event)
        pump()
    finally:
        _clear_last_exception()


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


# Bound for dismiss_modal's event-driven re-arm (the per-file modal
# dismissers this helper consolidates all used 100 attempts).
_DISMISS_ATTEMPTS = 100
_DISMISS_WAIT_MS = 25


def dismiss_modal(  # noqa: PLR0913 -- name/id/drive are the shape; attempts/wait_ms are the re-arm knobs
    dialog_name: str,
    *,
    dismiss_with: int,
    drive: Callable[[Any], None] | None = None,
    attempts: int = _DISMISS_ATTEMPTS,
    wait_ms: int = _DISMISS_WAIT_MS,
) -> None:
    """Schedule an event-driven dismissal of the modal *dialog_name*.

    The hardened form of :func:`run_modal` for a dialog that does not
    exist yet when the dismissal is armed: the caller schedules this
    right before firing a route whose handler opens the modal, and the
    dismissal then runs inside that modal's own event loop.

    Three failures the per-file copies each handled by hand, unified
    here (measured throughout, e.g. PR #8's runs and the 2026-09-07 /
    2026-09-08 flakes):

    * **Not yet shown.** A one-shot ``wx.CallAfter`` can be dispatched
      mid-decoration, before ``ShowModal`` shows the dialog. Re-arm on
      a timer so the synchronous decoration can unwind between attempts;
      a same-drain ``CallAfter`` re-queue would spin instead.
    * **A probe that raises.** ``harness.click``'s own find can trip the
      address-reuse poison (a stale wrapper raising ``LookupError``) or
      hit a dead C++ object (``RuntimeError``). The exception is caught
      here and re-armed -- an exception that escapes a ``CallAfter``
      callback is swallowed at the loop boundary
      (``wxApp::OnExceptionInMainLoop``), which would skip the
      dismissal and hang ``ShowModal`` with no user present.
    * **The leak-guard.** Once *attempts* exhausts, fire a final
      ``EndModal`` on a freshly re-found, still-shown dialog so the
      modal never stays open past the bound -- a leaked dialog hangs the
      caller's close path.

    Args:
        dialog_name: The frozen XRC name of the modal to dismiss.
        dismiss_with: The id ``EndModal`` is called with -- both for a
            default dismissal (when *drive* is ``None``) and for the
            leak-guard at the attempts bound.
        drive: Optional probe run once the dialog is shown. It should
            end the modal (typically ``harness.click`` on an OK/Cancel/
            Close button); a probe that raises is retried. ``None``
            ends the modal directly with *dismiss_with*.
        attempts: Bound on re-arms before the leak-guard fires.
        wait_ms: Delay between re-arm attempts, in milliseconds.

    Returns:
        ``None`` -- the dismissal runs inside the modal loop; the caller
        then blocks in ``ShowModal`` (or the route that opens the modal)
        and observes its return value.
    """

    def _dismiss(attempts_left: int) -> None:
        dialog = wx.Window.FindWindowByName(dialog_name)
        if dialog is None or not dialog.IsShown():
            if attempts_left <= 0:
                _end_modal_leak_guard(dialog_name, dismiss_with)
                return
            wx.CallLater(wait_ms, _dismiss, attempts_left - 1)
            return
        try:
            if drive is None:
                dialog.EndModal(dismiss_with)
            else:
                drive(dialog)
        except Exception:  # noqa: BLE001 -- probe failures re-arm
            if attempts_left <= 0:
                _end_modal_leak_guard(dialog_name, dismiss_with)
                return
            wx.CallLater(wait_ms, _dismiss, attempts_left - 1)

    wx.CallAfter(_dismiss, attempts)


def _end_modal_leak_guard(dialog_name: str, dismiss_with: int) -> None:
    """Fire the leak-guard ``EndModal`` on a freshly re-found dialog.

    Re-finds by name rather than touching the wrapper the probe last
    saw: ``EndModal`` on a destroyed wrapper is undefined behaviour (a
    segfault, measured -- :func:`close_window`'s own warning), so the
    guard must resolve a live wrapper. Checks ``IsShown()`` first so a
    never-shown or already-gone dialog is left alone.
    """
    fresh = wx.Window.FindWindowByName(dialog_name)
    if fresh is not None and fresh.IsShown():
        fresh.EndModal(dismiss_with)


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


# 25 attempts was measured enough for -n 2 in-process; per-file
# isolation runs two fresh wx processes at once and measured deferred
# deletions that outlast 25 drains, leaking frames into the sweep.
_CLOSE_SETTLE_ATTEMPTS = 100


def _closed_window_still_registered(handle: Any, name: str) -> bool:  # noqa: ANN401
    """Return whether the closed window is still an unreaped top-level.

    The reap signal is the native handle leaving
    ``wx.GetTopLevelWindows()``: a ``Destroy()``d *top-level* window
    stays in that registry -- appended to ``wxPendingDelete``, still
    findable by name -- until its destructor runs during idle
    processing, which is what actually removes it. Comparing handles
    (not names, not wrapper identity) is the only sound signal: on
    macOS ``IsBeingDeleted()`` stays ``False`` until the destructor
    runs, and a same-named sibling would otherwise be mistaken for the
    closed window.

    When *handle* is falsy (a never-created window whose ``GetHandle()``
    returned 0/``None`` before destruction) there is no handle to
    compare, so the settle falls back to the name-based signal -- the
    window is reaped when ``FindWindowByName(name)`` answers ``None``.
    Acceptable only for that null-handle edge case.
    """
    if not handle:
        return wx.Window.FindWindowByName(name) is not None
    return any(w.GetHandle() == handle for w in wx.GetTopLevelWindows())


def close_window(window: Any, *, strict: bool = False) -> bool:  # noqa: ANN401
    """Close and destroy *window*, then wait for its handle to reap.

    A dialog's default ``Close()`` only ``Hide()``s it -- unlike a
    frame, whose default handler destroys it outright (measured) --
    so both cases are covered by checking ``IsBeingDeleted()`` before
    an explicit ``Destroy()``.

    The reap signal is the native handle leaving
    ``wx.GetTopLevelWindows()``. A ``Destroy()``d *top-level* window
    is deferred: it stays in that registry -- still findable by name,
    ``IsBeingDeleted()`` still ``False`` on macOS -- until its
    destructor runs during idle processing, which is what actually
    removes it. Measured (hosted macOS CI, near-every run at this
    suite's full 761-test size on a 3-core runner -- the 4-CPU Tart VM
    this project's own local runs use stays green): a single pump right
    after ``Destroy()`` is not always enough idle time under load for
    the deferred deletion to complete, so the settle loop drives
    :func:`flush_deferred_deletions` -- proven to drain a hosted
    runner's idle queue -- bounded by :data:`_CLOSE_SETTLE_ATTEMPTS`,
    never a sleep, until the captured *handle* is no longer present.

    An earlier revision of this loop resolved the window by name
    (``wx.Window.FindWindowByName(name)``) and broke when the resolved
    handle no longer matched -- treating "a different, same-named
    window answers the name" as the reap and leaving the closed
    window's own reap unconfirmed (PR #45's defect). Same-named
    top-levels are real in this suite (``plate_input`` exists in four
    windows), so a stranger answering the name must not count as
    success. Keying on the handle makes a same-named sibling harmless:
    the loop exits only when *handle* itself is gone. The registry is
    enumerated fresh each iteration and only those live wrappers'
    ``GetHandle()`` is called; *window* is never touched again after
    ``Destroy()``.

    No new window is constructed while this loop runs, so no other
    window can acquire *handle* mid-settle -- strict mode's attribution
    (below) depends on that invariant.

    The caller must not touch *window* again after this returns: once
    its deletion completes, the underlying C++ object is gone, and any
    further method call on it -- even a harmless-looking query --
    segfaults the interpreter (measured). *name* and *handle* are
    captured before ``Destroy()`` runs and neither reads from *window*
    again afterwards; every later query in the loop is against freshly
    enumerated registry wrappers.

    Returns:
        ``Close()``'s return value. ``False`` would mean a bound
        handler vetoed the close; these raw XRC windows carry no
        such handler, but the value is surfaced for the caller to
        assert on rather than assumed.

    When the close succeeded, ends with a bounded ``gc.collect()``:
    the window's C++ object is gone by now, so any Python reference
    cycle still linking its control wrappers -- a view's bound-method
    handlers hold the view, which holds the controls (measured:
    ``RiderEditor``; the retention probe 2026-08-19 found stale
    wrappers held by the view objects) -- is unreachable and only a
    collection can dealloc it. Dealloc is what evicts the wrapper's
    entry from SIP's C++-pointer map, the one in-process remedy for
    the address-reuse corruption :func:`find_control` documents
    (Addendum 2: no repair exists; reference hygiene and process
    freshness are the only levers). wxPython's own test framework
    (``unittests/wtc.py``) ends its ``tearDown`` with the identical
    explicit ``gc.collect()`` for the same reason. A vetoed close
    (``Close()`` returning ``False``) skips the collection: the close
    was refused, so the destruction -- and this call's cleanup -- did
    not happen through the window's own close path.

    Raises:
        WindowStillResolvingError: Only when *strict* is set. Raised
            after the settle loop exhausts
            :data:`_CLOSE_SETTLE_ATTEMPTS` with the closed window's own
            handle still present in ``wx.GetTopLevelWindows()`` -- its
            deferred deletion never reaped. The message names the
            closed window and its handle.
    """
    name = window.GetName()
    handle = window.GetHandle()
    closed = window.Close()
    was_deleting = window.IsBeingDeleted()
    if not was_deleting:
        window.Destroy()
    attempts = 0
    while attempts < _CLOSE_SETTLE_ATTEMPTS:
        if not _closed_window_still_registered(handle, name):
            break
        flush_deferred_deletions()
        attempts += 1
    still_registered = _closed_window_still_registered(handle, name)
    if os.environ.get("RIVERCROSSING_CLOSE_DEBUG") and still_registered:
        survivors = [w.GetHandle() for w in wx.GetTopLevelWindows()]
        print(  # noqa: T201 -- env-gated diagnostic, off by default
            f"CLOSE-DEBUG: {name!r} still registered after {attempts} attempts "
            f"(closed={closed}, was_deleting={was_deleting}, handle={handle!r}, "
            f"top_level_handles={survivors!r})",
            file=sys.stderr,
            flush=True,
        )
    if strict and still_registered:
        if not handle:
            # Falsy handle: never-created window; the name is the only
            # signal, so the message names it rather than a handle.
            raise WindowStillResolvingError(
                f"close_window(strict=True): {name!r} still resolves after "
                f"{_CLOSE_SETTLE_ATTEMPTS} settle attempts -- the closed "
                f"window's name never stopped resolving"
            )
        raise WindowStillResolvingError(
            f"close_window(strict=True): {name!r} still registered after "
            f"{_CLOSE_SETTLE_ATTEMPTS} settle attempts -- the closed window's "
            f"native handle {handle!r} never left wx.GetTopLevelWindows()"
        )
    pump()
    if closed:
        gc.collect()
    return closed


def release_main_window(app: Any, frame: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Tear down a built ``main_frame`` at scenario cleanup.

    The single home of the cleanup sequence
    ``console_subprocess_scenarios._close_without_prompt`` encodes
    today; it is the contract every module that builds the window
    through :func:`~rivercrossing.ui.app.build_main_window` calls
    when the scenario is done with it. Sequence, in order:

    1. Sets ``app.really_quitting = True`` BEFORE :func:`close_window`
       runs: a plain, vetoable ``Close()`` -- what ``close_window``
       always does -- runs the very same ``_confirm_quit`` flow
       File ▸ Exit does on every platform but macOS, and nothing
       in a test session dismisses that modal (measured on
       windows-latest CI, run 31015653629: the child hung, empty
       stdout). The flag makes ``_on_main_frame_close``'s own
       ``not event.CanVeto() or context.app.really_quitting`` guard
       destroy *frame* immediately instead -- the same forced-close
       mechanism :func:`_handle_exit_route` relies on.
    2. Calls :func:`close_window` to destroy *frame* and wait for its
       native handle to leave ``wx.GetTopLevelWindows()``.
    3. If ``app.main_frame is frame``, clears it to ``None``.
       ``app.py``'s ``_bind_process_quit_paths`` hands the app the
       frame reference ``RiverCrossingApp.MacReopenApp`` restores;
       a stale reference would keep frame #1's wrappers alive across
       a same-process frame #2 build.
    4. ``app.Unbind(wx.EVT_QUERY_END_SESSION)`` -- drops the handler
       whose closure holds the route context, and through it *frame*
       and its console.

    Steps 3-4 are reference hygiene (F3): those two app-level Python
    references would otherwise outlive *frame*'s C++ object, pinning
    its wrapper -- and its SIP pointer->wrapper map entry -- for the
    rest of the process. A caller that builds a second ``main_frame``
    in the same process then finds a frame #2 control that lands on a
    recycled C++ address resolving to frame #1's stale wrapper: the
    address-reuse poison ``ui.views._support.find_control``'s
    docstring documents. Dropping the references lets
    :func:`close_window`'s final ``gc.collect()`` dealloc the
    released graph and evict the entry.

    The caller must not touch *frame* again after this returns
    (:func:`close_window`'s own warning). *app* is expected to be the
    live ``wx.App`` carrying the ``really_quitting`` flag and the
    ``main_frame`` attribute the build wiring set.

    Args:
        app: The live ``wx.App`` instance the frame was built under.
        frame: The ``main_frame`` instance to close and release.

    Restores ``really_quitting`` to its previous value after the close:
    the suite's session-scoped ``wx_app`` outlives every frame, and a
    leaked ``True`` would make every later in-process close skip its
    confirm dialog (the subprocess exemplar is safe only because its
    app dies with the process).
    """
    name = frame.GetName()
    previous = getattr(app, "really_quitting", False)
    app.really_quitting = True
    close_window(frame)
    if getattr(app, "main_frame", None) is frame:
        # getattr, not attribute access: the live-context mirror
        # builders never hand the app a frame reference, and a bare
        # session wx.App may not carry the attribute at all.
        app.main_frame = None
    app.Unbind(wx.EVT_QUERY_END_SESSION)
    app.really_quitting = previous
    # Reap any same-named pending-delete leftover (Fault B / PR #45):
    # app._load_frame_verified's rebuild path destroys the degraded
    # frame with a plain Destroy() and no reap, so in a no-MainLoop
    # test process that pending-delete frame lingers in the registry
    # under the frame's name and poisons the next load_window_verified
    # entry guard. close_window reaps by native handle -- the frame's
    # own -- so a same-named sibling pending-delete is left behind;
    # drive the flush until the name is gone. A genuinely-live leaked
    # frame is not pending-delete, so this bounded best-effort loop
    # leaves it for the session-end sweep to catch.
    attempts = 0
    while attempts < _CLOSE_SETTLE_ATTEMPTS:
        if not any(w.GetName() == name for w in wx.GetTopLevelWindows()):
            break
        flush_deferred_deletions()
        attempts += 1


_MENU_EVENT_SETTLE_ATTEMPTS = 10


def fire_menu_event(frame: Any, item_id: str) -> None:  # noqa: ANN401
    """Post a real ``EVT_MENU`` for *item_id* at *frame*, then settle.

    The shared home of the two per-file ``_fire_menu_event`` helpers
    the E3.2/E3.4 split left in ``test_app_bootstrap.py`` and
    ``test_app_open_target.py``. (``console_subprocess_scenarios.py``
    keeps its own simpler variant: it fires inside freshly spawned
    interpreters, where process freshness makes retention moot.)

    Measured (PR #8's CI, run 31344728049, this suite's own scattered
    residual churn): a route that opens *and* destroys a dialog inside
    this same synchronous call (``mi_import_csv``'s picker -> preview
    -> commit flow, say) can leave that deletion still pending when
    this returns, racing the very next call's own window construction.
    ``harness.close_window``'s deterministic reap does not cover that
    path: production's own ``dialogs.run_dialog``/``_open_target``
    destroy their windows directly, never through that test-only
    helper.

    The settle loop calls :func:`flush_deferred_deletions` directly
    rather than :func:`pump`: measured on windows-latest CI (run
    31392502719), driving that flush from every ``pump()`` call in the
    whole suite -- not just here -- turned one functional job's normal
    ~90s runtime into 5h59m28s before the 6-hour cap killed it, so
    ``pump`` no longer flushes on every call. This loop's own deletions
    still need the deterministic idle-processing drive only a bounded
    few calls per fired event, not one per pump call across the suite.

    Finally, clears ``sys.last_type``/``sys.last_value``/
    ``sys.last_traceback``/``sys.last_exc``. wx swallows a Python
    exception raised inside a handler, and PyErr_Print then parks the
    exception on ``sys`` (on Python 3.11+ under the ``last_exc`` name,
    which also carries the traceback) -- the frame chain holds the
    failing handler's view and every control wrapper it touches alive
    for the rest of the process -- the "Python frames" signature the
    2026-08-19 retention probe attributed to this exact path (Addendum
    2, inventory item 1) -- so their SIP map entries never evict. The
    clear runs in a ``finally`` whether or not a handler raised, and
    it covers the app's own route path too: ``app.py``'s ``EVT_MENU``
    bindings are the same events this posts at.
    """
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    real_id = wx.xrc.XRCID(item_id)
    event = wx.CommandEvent(wx.EVT_MENU.typeId, real_id)
    event.SetEventObject(frame)
    try:
        frame.GetEventHandler().ProcessEvent(event)
        pump()
        for _ in range(_MENU_EVENT_SETTLE_ATTEMPTS):
            flush_deferred_deletions()
    finally:
        _clear_last_exception()
