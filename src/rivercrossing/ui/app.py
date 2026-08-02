# SPDX-License-Identifier: GPL-3.0-only
"""Application bootstrap: the ``rivercrossing`` GUI entry point."""

import wx


def main() -> int:
    """Run the RiverCrossing GUI application.

    Returns:
        The process exit code; ``0`` on a clean shutdown.
    """
    wx.App()
    return 0
