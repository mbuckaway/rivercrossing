# SPDX-License-Identifier: GPL-3.0-only
"""The accelerator table: single source of truth (E1.4.1).

xrc-windows.md section E is explicit that ``shortcuts_dlg``'s rows
are "filled in code from the accelerator table -- cannot drift"
(E8.2.1). That only holds if this table is the *only* place the four
accelerators are spelled out, so it is kept separate from
:mod:`rivercrossing.ui.commands`'s much larger, faster-changing route
table rather than folded into one more field there: E8.2.1 imports
only this, never the full command table, to build its dialog rows.

Three of the four are declared in ``main.xrc``'s ``<accel>``
elements (``Ctrl+Z``, ``F5``, ``F1``); ``Enter`` is the console's
own default action -- typing a plate and pressing it records a
crossing -- and is not a menu accelerator at all, so its
``menu_item_id`` is ``None``.
"""

from dataclasses import dataclass

__all__ = ["ACCELERATOR_TABLE", "Accelerator"]


@dataclass(frozen=True)
class Accelerator:
    """One row of the keyboard-shortcuts table (xrc-windows.md §E).

    Attributes:
        key: The shortcut as wx renders it (``wx.MenuItem.GetAccel()
            .ToString()`` for the three menu accelerators, measured
            to match this spelling exactly).
        action: The shortcuts dialog's own action text.
        menu_item_id: The ``mi_*`` name whose XRC ``<accel>`` this
            row cross-checks, or ``None`` for ``Enter``, which is not
            a menu accelerator.
    """

    key: str
    action: str
    menu_item_id: str | None


ACCELERATOR_TABLE: tuple[Accelerator, ...] = (
    Accelerator(key="Enter", action="Record crossing for typed plate", menu_item_id=None),
    Accelerator(key="Ctrl+Z", action="Undo last crossing", menu_item_id="mi_undo_crossing"),
    Accelerator(key="F5", action="Standings (Results window)", menu_item_id="mi_standings"),
    Accelerator(key="F1", action="User guide", menu_item_id="mi_user_guide"),
)
