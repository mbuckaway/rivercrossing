# SPDX-License-Identifier: GPL-3.0-only
"""The accelerator table: single source of truth (E1.4.1).

xrc-windows.md section E is explicit that ``shortcuts_dlg``'s rows
are "filled in code from the accelerator table -- cannot drift"
(E8.2.1). That only holds if this table is the *only* place the four
accelerators are spelled out, so it is kept separate from
:mod:`rivercrossing.ui.commands`'s much larger, faster-changing route
table rather than folded into one more field there: E8.2.1 imports
only this, never the full command table, to build its dialog rows.

Three of the eight are declared in ``main.xrc``'s ``<accel>``
elements (``Ctrl+Z``, ``F5``, ``F1``); ``Enter`` is the console's
own default action -- typing a plate and pressing it records a
crossing -- and is not a menu accelerator at all, so its
``menu_item_id`` is ``None``. Phase 6's ``F2`` is the same kind of
code-side row: main.xrc has no menu item for "edit crossing", so the
console binds it as a frame accelerator
(``views.main_frame.MainFrame``) and its ``menu_item_id`` is ``None``
too. The crossings feed's ``Ctrl+D`` and ``Ctrl+E`` rows are the same
shape again: no menu item exists for a Delete or an Edit Plate
command, so ``MainFrame`` binds those two frame-side and this table
only documents them. ``Delete`` is the odd one out -- it carries no
``menu_item_id`` either, but it is not a frame accelerator: the frame
remaps it away from ``plate_input``, so ``MainFrame`` binds it on
``crossings_list`` itself (feed-scoped) and ``Ctrl+D`` is the frame's
own route to the same command.
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
            row cross-checks, or ``None`` for the five code-side rows:
            ``Enter`` (the console's default action), the frame
            accelerators ``F2``/``Ctrl+D``/``Ctrl+E``, and the
            feed-scoped ``Delete`` (bound on ``crossings_list``).
    """

    key: str
    action: str
    menu_item_id: str | None


ACCELERATOR_TABLE: tuple[Accelerator, ...] = (
    Accelerator(key="Enter", action="Record crossing for typed plate", menu_item_id=None),
    Accelerator(key="Ctrl+Z", action="Undo last crossing", menu_item_id="mi_undo_crossing"),
    Accelerator(key="F5", action="Standings window", menu_item_id="mi_standings"),
    Accelerator(key="F1", action="User guide", menu_item_id="mi_user_guide"),
    Accelerator(key="F2", action="Edit crossing (open detail)", menu_item_id=None),
    Accelerator(key="Delete", action="Delete selected crossing", menu_item_id=None),
    Accelerator(key="Ctrl+D", action="Delete selected crossing", menu_item_id=None),
    Accelerator(key="Ctrl+E", action="Edit crossing plate", menu_item_id=None),
)
