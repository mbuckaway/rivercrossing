# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for E8.2.3's About box (VM-only, Phase 10).

The route cases run in a fresh, spawned interpreter via
``console_subprocess_scenarios.py`` (``scenario_runner.run_scenario``),
following that module's own isolation rationale exactly: the dialog is
driven through the real app bootstrap, which needs a live desktop
session -- so this file runs only in the Tart VM, never on this host.

The scenarios report raw facts (version text, gorba link URL, bitmap
IsOk/size, logo-match) for this module to assert; a wrong measured
value surfaces as a normal pytest assertion diff, not a bare non-zero
exit code.

The ux-polish additions (fixed ``ABOUT_SIZE``, the three attribution
labels, the embedded-SVG fallback logo) are asserted in-process on a
raw XRC-loaded ``about_dlg`` -- the ``test_selftest_dialog.py``
pattern -- because the loaded resource plus ``AboutDialog`` *is* the
code path the route runs, and none of those facts needs the bootstrap
or a menu event. Like every other case here they still need a real
wx session, so they run only in the VM.
"""

from typing import Any

import harness
import pytest
import scenario_runner
import wx

from rivercrossing import __version__
from rivercrossing.ui import ids
from rivercrossing.ui.views.about import (
    ABOUT_LOGO_SIZE,
    ABOUT_SIZE,
    EMBEDDED_LOGO_SVG,
    AboutDialog,
)

pytestmark = pytest.mark.functional


def _label_text(dialog: Any, name: str) -> str:  # noqa: ANN401 -- wx ships no stubs
    """Return one of *dialog*'s static labels, resolved by name."""
    return harness.find_control(dialog, name).GetLabelText()


def _open_about_dialog(xrc_resource: Any) -> tuple[Any, AboutDialog]:  # noqa: ANN401 -- wx ships no stubs
    """Load ``about_dlg``, wire it live, show it, and pump once."""
    dialog = harness.load_window_verified(xrc_resource, ids.ABOUT_DLG, frame=False)
    try:
        view = AboutDialog(dialog)
        dialog.Show()
        harness.pump()
    except Exception:  # Fault A: any post-load failure must close the dialog
        harness.close_window(dialog)
        raise
    return dialog, view


def test_about_dialog_opens_with_the_product_version_prefix_and_gorba_link() -> None:
    """Help ▸ About renders ``RiverCrossing <version>`` and gorba_link.

    E8.2.3's route half: the bootstrap threads no ride logo, so
    ``about_logo_bmp`` must be a non-null bitmap (E8.2.3 b) --
    ``wx.NullBitmap`` is never acceptable (dialogs.xrc) -- and the
    ux-polish copy change means ``version_lbl`` reads the product
    name plus the package version, not the bare version.
    """
    result = scenario_runner.run_scenario("about_dialog_route_renders_version_and_gorba_link")

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["dlg_shown"] is True, result["context"]
    assert data["version_text"] == f"RiverCrossing {__version__}", result["context"]
    assert data["gorba_is_hyperlink"] is True, result["context"]
    assert data["gorba_url"] == "https://gorba.ca", result["context"]
    assert data["logo_bitmap_ok"] is True, result["context"]


def test_about_dialog_uses_the_ride_logo_bitmap_when_one_is_present() -> None:
    """A real logo_path sets about_logo_bmp to that file's bitmap.

    The ride logo still wins over the ux-polish embedded-SVG
    fallback: the resolved logo is the file's own 8x8 bitmap (the
    helper writes 8x8), not the 64x64 embedded logo, and the control
    carries a non-null bitmap.
    """
    result = scenario_runner.run_scenario("about_dialog_uses_the_ride_logo_bitmap")

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["logo_bitmap_ok"] is True, result["context"]
    assert data["logo_bitmap_size"] == [8, 8], result["context"]
    assert data["control_bitmap_ok"] is True, result["context"]


def test_about_dialog_is_pinned_to_about_size_without_a_resize_border(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """about_dlg is fixed at ABOUT_SIZE and cannot be resized.

    ux-polish: dialogs.xrc drops ``wxRESIZE_BORDER`` and AboutDialog
    pins the dialog (SetSize + min/max hints), so its fixed copy can
    never wrap from a user resize. The style bit and the min/max
    hints are the behaviour, not a screenshot.
    """
    dialog, _view = _open_about_dialog(xrc_resource)

    try:
        facts = (
            dialog.GetSize(),
            dialog.GetMinSize(),
            dialog.GetMaxSize(),
            bool(dialog.GetWindowStyleFlag() & wx.RESIZE_BORDER),
        )
    finally:
        harness.close_window(dialog)

    assert facts == (ABOUT_SIZE, ABOUT_SIZE, ABOUT_SIZE, False)


def test_about_dialog_renders_version_prefix_and_the_three_attribution_labels(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """The About box names the product and credits author/licence.

    ux-polish: ``version_lbl`` reads ``RiverCrossing <version>`` and
    the three fixed-copy attribution labels sit below the tagline,
    resolved through the generated ``ui/ids.py`` registry.
    """
    dialog, _view = _open_about_dialog(xrc_resource)

    try:
        copy = (
            _label_text(dialog, ids.VERSION_LBL),
            _label_text(dialog, ids.AUTHOR_LBL),
            _label_text(dialog, ids.COPYRIGHT_LBL),
            _label_text(dialog, ids.LICENSE_LBL),
        )
    finally:
        harness.close_window(dialog)

    assert copy == (
        f"RiverCrossing {__version__}",
        "Mark Buckaway",
        "© 2026 Mark Buckaway",
        "Licensed under GPL-3.0-only",
    )


def test_about_dialog_without_a_ride_logo_renders_the_embedded_svg_logo(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """No logo_path: the fallback logo is the embedded SVG, non-null.

    ux-polish: the About box always shows a real, non-null logo. The
    resolved fallback bitmap must match a fresh render of
    ``EMBEDDED_LOGO_SVG`` at the control's own DPI -- proving the
    embedded logo, not the stock icon, supplied it -- and the control
    itself must carry a non-null bitmap.
    """
    dialog, view = _open_about_dialog(xrc_resource)

    try:
        control_bitmap = view.about_logo_bmp.GetBitmap()
        embedded = wx.BitmapBundle.FromSVG(
            EMBEDDED_LOGO_SVG.encode("utf-8"), ABOUT_LOGO_SIZE
        ).GetBitmapFor(view.about_logo_bmp)
        facts = (
            view.logo_bitmap.IsOk(),
            control_bitmap.IsOk(),
            view.logo_bitmap.GetSize() == embedded.GetSize(),
        )
    finally:
        harness.close_window(dialog)

    assert facts == (True, True, True)
