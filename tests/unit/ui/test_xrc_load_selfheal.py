# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the shared XRC load self-heal (Fault-B).

``wx.xrc.XmlResource`` can silently skip a window during a load -- the
Fault-B degraded-load class. The reported symptom: ``File ▸
Simulation…`` posts "Simulation — no window authored yet" because
``simulation_dlg`` is missing from the process-global singleton, and
every other dialog/menubar load site carries the same failure class.
:func:`~rivercrossing.ui.views._support.load_dialog` and
:func:`~rivercrossing.ui.views._support.load_menubar` are the one
shared answer: a miss retries twice against
:func:`~rivercrossing.ui.views._support.fresh_resource` (a private
``XmlResource`` loaded from every packaged ``.xrc``), dropping the
memoized rebuild between attempts. One retry was not enough: it could
hit a *frozen degraded* rebuild and answer ``None`` for the rest of the
session, so the cache entry is evicted and the second attempt re-parses
from disk. A shipped window then comes back instead of ``None``.

Most tests build no wx object: the resource and the recovered window are
plain doubles, and ``fresh_resource`` is monkeypatched. The later groups
drive the real ``fresh_resource`` -- against a temporary xrc directory
(a real ``wx.App()`` and ``XmlResource`` there), and against the
packaged ``ui/xrc`` for the two simulator dialogs the reported
regression skipped -- so the Load-result check, its warning, the
module-scope memoization and the shipped window ids are pinned by
behaviour, not by a double.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, Any

import pytest
import wx

from rivercrossing.ui import ids
from rivercrossing.ui.views import _support

if TYPE_CHECKING:
    from pathlib import Path


class _Window:
    """A loaded-window double: identity is all these tests read."""


class _ResourceDouble:
    """An ``XmlResource`` double answering one scripted window.

    A ``None`` answer is the degraded load: the resource reports the
    window absent, exactly as the singleton does when a load skips a
    subtree. Every call is recorded as its own ``(parent, name)``
    pair, so a test can pin what the load forwarded -- the default
    ``parent`` is ``None``, and the two parented call sites pass a
    window.
    """

    def __init__(self, window: object | None = None) -> None:
        """Answer *window* from every load call on both seams."""
        self.window = window
        self.dialogs: list[tuple[object, str]] = []
        self.menubars: list[tuple[object, str]] = []

    def LoadDialog(self, parent: object, name: str) -> object | None:  # noqa: N802 -- wx API name
        """Record the ``(parent, name)`` lookup, return the window."""
        self.dialogs.append((parent, name))
        return self.window

    def LoadMenuBar(  # noqa: N802 -- wx API name
        self, parent: object, name: str
    ) -> object | None:
        """Record the ``(parent, name)`` lookup, return the menubar."""
        self.menubars.append((parent, name))
        return self.window


def _refuse_rebuild() -> object:
    """Fail the test: a load that found its window must not rebuild."""
    raise AssertionError("a load that found its window must not rebuild the resource")


def test_load_dialog_given_a_shipped_window_returns_it_without_a_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path costs one lookup and nothing else."""
    window = _Window()
    resource = _ResourceDouble(window)
    monkeypatch.setattr(_support, "fresh_resource", _refuse_rebuild)

    result = _support.load_dialog(resource, ids.SIMULATION_DLG)

    assert result is window
    assert resource.dialogs == [(None, ids.SIMULATION_DLG)]


def test_load_dialog_given_a_skipped_window_recovers_it_from_a_fresh_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fault-B: a dialog the singleton skipped comes back, not None."""
    recovered = _Window()
    resource = _ResourceDouble()
    fresh = _ResourceDouble(recovered)
    monkeypatch.setattr(_support, "fresh_resource", lambda: fresh)

    result = _support.load_dialog(resource, ids.SIMULATION_DLG)

    assert result is recovered
    assert resource.dialogs == [(None, ids.SIMULATION_DLG)]
    assert fresh.dialogs == [(None, ids.SIMULATION_DLG)]


def test_load_dialog_given_a_parent_forwards_it_on_the_primary_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parented site keeps its parent on the healthy path."""
    parent = _Window()
    window = _Window()
    resource = _ResourceDouble(window)
    monkeypatch.setattr(_support, "fresh_resource", _refuse_rebuild)

    result = _support.load_dialog(resource, ids.SIMULATION_DLG, parent=parent)

    assert result is window
    assert resource.dialogs == [(parent, ids.SIMULATION_DLG)]


def test_load_dialog_given_a_parent_forwards_it_on_the_fresh_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rebuilt load keeps the parent too, not just the name.

    ``_load_running_window`` and ``RideLibrary._on_delete_clicked``
    parent their dialogs, so a self-healed rebuild that dropped the
    parent would silently change the window's stacking.
    """
    parent = _Window()
    recovered = _Window()
    resource = _ResourceDouble()
    fresh = _ResourceDouble(recovered)
    monkeypatch.setattr(_support, "fresh_resource", lambda: fresh)

    result = _support.load_dialog(resource, ids.SIMULATION_DLG, parent=parent)

    assert result is recovered
    assert resource.dialogs == [(parent, ids.SIMULATION_DLG)]
    assert fresh.dialogs == [(parent, ids.SIMULATION_DLG)]


def test_load_dialog_given_a_window_no_resource_carries_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target no .xrc authors reads as absent after both attempts."""
    resource = _ResourceDouble()
    missing = _ResourceDouble()
    monkeypatch.setattr(_support, "fresh_resource", lambda: missing)

    result = _support.load_dialog(resource, "no_such_dlg")

    assert result is None
    assert resource.dialogs == [(None, "no_such_dlg")]
    # Both self-heal attempts miss: the failed first attempt evicts the
    # memoized rebuild, the second asks the rebuilt resource again.
    assert missing.dialogs == [(None, "no_such_dlg")] * 2


def test_load_menubar_given_a_shipped_menubar_returns_it_without_a_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path costs one lookup and nothing else."""
    menubar = _Window()
    resource = _ResourceDouble(menubar)
    monkeypatch.setattr(_support, "fresh_resource", _refuse_rebuild)

    result = _support.load_menubar(resource, ids.MAIN_MENUBAR)

    assert result is menubar
    assert resource.menubars == [(None, ids.MAIN_MENUBAR)]


def test_load_menubar_given_a_skipped_menubar_recovers_it_from_a_fresh_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fault-B: the same self-heal covers the menubar seam."""
    recovered = _Window()
    resource = _ResourceDouble()
    fresh = _ResourceDouble(recovered)
    monkeypatch.setattr(_support, "fresh_resource", lambda: fresh)

    result = _support.load_menubar(resource, ids.MAIN_MENUBAR)

    assert result is recovered
    assert resource.menubars == [(None, ids.MAIN_MENUBAR)]
    assert fresh.menubars == [(None, ids.MAIN_MENUBAR)]


def test_load_menubar_given_a_menubar_no_resource_carries_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A menubar no resource carries stays absent on both attempts."""
    resource = _ResourceDouble()
    missing = _ResourceDouble()
    monkeypatch.setattr(_support, "fresh_resource", lambda: missing)

    result = _support.load_menubar(resource, "no_such_menubar")

    assert result is None
    assert resource.menubars == [(None, "no_such_menubar")]
    assert missing.menubars == [(None, "no_such_menubar")] * 2


# ------------------------- a rebuild that fails must not reach wx
#
# ``fresh_resource`` re-parses every packaged .xrc, so it can raise
# (an OSError, a parse error). These loaders are called straight from
# wx event handlers, where an escaping exception is a crash: a failed
# retry answers ``None`` -- the same "no window authored" reading the
# caller already handles.


def test_load_dialog_given_a_rebuild_that_raises_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: a failed rebuild answers None, never raises."""
    resource = _ResourceDouble()
    warnings: list[str] = []
    monkeypatch.setattr(wx, "LogWarning", warnings.append)

    def _explode() -> object:
        raise OSError("cannot read the xrc dir")

    monkeypatch.setattr(_support, "fresh_resource", _explode)

    result = _support.load_dialog(resource, ids.SIMULATION_DLG)

    assert warnings == ["XRC rebuild failed: OSError: cannot read the xrc dir"]
    assert result is None
    assert resource.dialogs == [(None, ids.SIMULATION_DLG)]


def test_load_menubar_given_a_rebuild_that_raises_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3 negative: the menubar seam guards the rebuild too."""
    resource = _ResourceDouble()
    warnings: list[str] = []
    monkeypatch.setattr(wx, "LogWarning", warnings.append)

    def _explode() -> object:
        raise OSError("cannot read the xrc dir")

    monkeypatch.setattr(_support, "fresh_resource", _explode)

    result = _support.load_menubar(resource, ids.MAIN_MENUBAR)

    assert warnings == ["XRC rebuild failed: OSError: cannot read the xrc dir"]
    assert result is None
    assert resource.menubars == [(None, ids.MAIN_MENUBAR)]


# ------------------- the real rebuild (a temporary xrc directory)
#
# Every test above doubles the resource. These drive the real
# ``fresh_resource`` with ``xrc_dir=`` pointed at a temporary directory,
# so what it reads, what it records when a load fails, and that it
# parses once per directory are pinned by behaviour.

_PROBE_DIALOG = "xrc_probe_dlg"
_PROBE_XRC = """<?xml version="1.0" ?>
<resource>
  <object class="wxDialog" name="xrc_probe_dlg">
    <title>Probe</title>
  </object>
</resource>
"""


def _write_probe_xrc(xrc_dir: Path) -> None:
    """Write one minimal, valid dialog .xrc into *xrc_dir*."""
    (xrc_dir / "probe.xrc").write_text(_PROBE_XRC, encoding="utf-8")


@cache
def _process_wx_app() -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Return the module's one wx.App, creating it on first use.

    Reuses an app another unit module already built (``wx.GetApp()``
    answers it then). The app is deliberately never destroyed: this
    module is not the only unit module that needs one, and destroying
    the process's app makes every later test that needs one raise
    ``wx._core.PyNoAppError`` (measured -- ``wx.GetApp()`` answers
    ``None`` afterwards, but a later ``wx.App()`` does not restore what
    the wx layer needs). ``test_cards_imagelist_wx.py`` keeps the same
    module-scope reference for the same reason.
    """
    return wx.GetApp() or wx.App(False)  # noqa: FBT003 -- wx's own signature: redirect=False


@pytest.fixture(scope="module")
def wx_app() -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Guarantee a live wx.App before any real XRC object is built."""
    return _process_wx_app()


def test_fresh_resource_given_a_temp_xrc_dir_loads_the_authored_window(
    wx_app: Any,  # noqa: ANN401, ARG001 -- taken for the real XRC build, not read
    tmp_path: Path,
) -> None:
    """The rebuild reads the given dir: the authored dialog loads."""
    _write_probe_xrc(tmp_path)

    resource = _support.fresh_resource(xrc_dir=tmp_path)
    window = resource.LoadDialog(None, _PROBE_DIALOG)

    assert isinstance(window, wx.Dialog)
    assert window.GetName() == _PROBE_DIALOG
    window.Destroy()


def test_fresh_resource_given_an_empty_xrc_dir_holds_no_window(
    wx_app: Any,  # noqa: ANN401, ARG001 -- taken for the real XRC build, not read
    tmp_path: Path,
) -> None:
    """T-4 empty boundary: a dir with no .xrc holds no window.

    The absence is read through ``GetResourceNode``: ``LoadDialog``
    drives wx's GUI error log on a miss, which blocks with no event
    loop, so it cannot answer "is this authored?" headlessly.
    """
    resource = _support.fresh_resource(xrc_dir=tmp_path)

    assert resource.GetResourceNode(_PROBE_DIALOG) is None


def test_fresh_resource_given_an_unreadable_xrc_records_the_path(
    wx_app: Any,  # noqa: ANN401, ARG001 -- taken for the real XRC build, not read
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-5: a failed Load names the file it could not read.

    ``wx.LogNull`` swallows wx's own GUI error log for the malformed
    file -- headless, that log queues a message box and crashes the
    process at exit -- and the reference keeps the null target
    installed for the length of the test.
    """
    broken = tmp_path / "broken.xrc"
    broken.write_text("", encoding="utf-8")
    warnings: list[str] = []
    monkeypatch.setattr(wx, "LogWarning", warnings.append)
    _log_null = wx.LogNull()

    resource = _support.fresh_resource(xrc_dir=tmp_path)

    assert warnings == [f"XRC load failed for {broken}"]
    assert resource.GetResourceNode(_PROBE_DIALOG) is None


def test_fresh_resource_given_an_unreadable_xrc_rebuilds_on_the_next_call(
    wx_app: Any,  # noqa: ANN401, ARG001 -- taken for the real XRC build, not read
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed rebuild is never cached, so the next call retries it."""
    broken = tmp_path / "broken.xrc"
    broken.write_text("", encoding="utf-8")
    warnings: list[str] = []
    monkeypatch.setattr(wx, "LogWarning", warnings.append)
    _log_null = wx.LogNull()

    first = _support.fresh_resource(xrc_dir=tmp_path)
    second = _support.fresh_resource(xrc_dir=tmp_path)

    assert warnings == [f"XRC load failed for {broken}"] * 2
    assert first is not second


def test_fresh_resource_given_no_dir_reads_the_packaged_xrc_dir(
    wx_app: Any,  # noqa: ANN401, ARG001 -- taken for the real XRC build, not read
) -> None:
    """T-4 default: omitting *xrc_dir* reads the packaged ``ui/xrc``."""
    resource = _support.fresh_resource()
    menubar = resource.LoadMenuBar(None, ids.MAIN_MENUBAR)

    assert isinstance(menubar, wx.MenuBar)
    menubar.Destroy()


def test_fresh_resource_given_the_same_dir_returns_the_cached_resource(
    wx_app: Any,  # noqa: ANN401, ARG001 -- taken for the real XRC build, not read
    tmp_path: Path,
) -> None:
    """A healthy rebuild is memoized: one parse per directory."""
    _write_probe_xrc(tmp_path)

    first = _support.fresh_resource(xrc_dir=tmp_path)
    second = _support.fresh_resource(xrc_dir=tmp_path)

    assert first is second


# -------------- the frozen degraded rebuild (eviction between attempts)
#
# The single retry could land on the memoized *degraded* rebuild and
# answer ``None`` for the rest of the session. The two-attempt loop
# evicts that cache entry, so the next attempt re-parses from disk.


def test_load_dialog_given_a_frozen_degraded_rebuild_evicts_it_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degraded rebuild is evicted so the retry recovers."""
    degraded = _ResourceDouble()
    recovered = _ResourceDouble(_Window())
    replies = iter([degraded, recovered])
    calls: list[object] = []

    def _fresh() -> object:
        resource = next(replies)
        calls.append(resource)
        return resource

    monkeypatch.setattr(_support, "fresh_resource", _fresh)
    monkeypatch.setitem(_support._rebuilt_resources, _support._XRC_DIR, degraded)

    result = _support.load_dialog(_ResourceDouble(), ids.SIMULATION_DLG)

    assert result is recovered.window
    assert calls == [degraded, recovered]
    assert _support._XRC_DIR not in _support._rebuilt_resources


def test_load_menubar_given_a_frozen_degraded_rebuild_evicts_it_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The menubar seam evicts the degraded rebuild on the same loop."""
    degraded = _ResourceDouble()
    recovered = _ResourceDouble(_Window())
    replies = iter([degraded, recovered])
    calls: list[object] = []

    def _fresh() -> object:
        resource = next(replies)
        calls.append(resource)
        return resource

    monkeypatch.setattr(_support, "fresh_resource", _fresh)
    monkeypatch.setitem(_support._rebuilt_resources, _support._XRC_DIR, degraded)

    result = _support.load_menubar(_ResourceDouble(), ids.MAIN_MENUBAR)

    assert result is recovered.window
    assert calls == [degraded, recovered]
    assert _support._XRC_DIR not in _support._rebuilt_resources


# -------------------- the packaged simulator dialogs (the regression)
#
# The reported bug: ``File ▸ Simulation…`` posted "no window authored
# yet" because the singleton had skipped ``simulation_dlg``'s subtree.
# Pinning the shipped ids against the real packaged ``ui/xrc`` is the
# regression's own test.


@pytest.mark.parametrize("name", [ids.SIMULATION_DLG, ids.SIM_RUNNING_DLG])
def test_fresh_resource_given_the_packaged_dir_resolves_each_simulator_dialog(
    wx_app: Any,  # noqa: ANN401, ARG001 -- taken for the real XRC build, not read
    name: str,
) -> None:
    """Both shipped simulator dialogs load from the packaged dir."""
    resource = _support.fresh_resource()

    window = resource.LoadDialog(None, name)

    assert isinstance(window, wx.Dialog)
    assert window.GetName() == name
    window.Destroy()
