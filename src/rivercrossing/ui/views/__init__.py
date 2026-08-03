# SPDX-License-Identifier: GPL-3.0-only
"""wx-backed views: thin windows binding presenters (S1).

Each module wraps one already-XRC-loaded window with the code-side
behaviour xrc-windows.md's footnotes assign to it -- DataView
columns/rows, InfoBar construction, splitter persistence and the
like -- and implements that window's ``*View`` Protocol from
``ui.presenters``. Loading the XRC itself stays the caller's job
(the app bootstrap in production, ``harness.load_window`` in tests);
nothing here duplicates that.
"""

from rivercrossing.ui.views.main_frame import MainFrame

__all__ = ["MainFrame"]
