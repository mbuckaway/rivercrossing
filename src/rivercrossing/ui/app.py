# SPDX-License-Identifier: GPL-3.0-only
"""Application bootstrap: the ``rivercrossing`` GUI entry point."""

from rivercrossing.ui import require_wx


def main() -> int:
    """Run the RiverCrossing GUI application.

    Returns:
        The process exit code; ``0`` on a clean shutdown.

    Raises:
        WxUnavailableError: If ``wx`` cannot be imported.
    """
    wx = require_wx()
    wx.App()
    return 0
