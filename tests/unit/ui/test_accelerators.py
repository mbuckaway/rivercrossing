# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the accelerator table (E1.4.1).

Kept separate from ``test_commands.py``, mirroring the production
split: ``accelerators.py`` is deliberately its own module (E8.2.1
imports only this, not the larger route table), so its tests stay
scoped to it too.

The one thing only a real ``wx.MenuBar`` can prove -- that the three
XRC-backed entries agree with ``main.xrc``'s live ``<accel>``
declarations -- needs a live toolkit and is not pinned here.
"""

import pytest

from rivercrossing.ui import accelerators


def test_accelerator_table_declares_exactly_eight_entries() -> None:
    """Enter, Ctrl+Z, F5, F1, F2, Delete, Ctrl+D, Ctrl+E."""
    assert len(accelerators.ACCELERATOR_TABLE) == 8


def test_accelerator_table_declares_each_key_exactly_once() -> None:
    """No duplicate shortcut keys in the single source of truth."""
    keys = [accelerator.key for accelerator in accelerators.ACCELERATOR_TABLE]

    assert len(keys) == len(set(keys))


def test_enter_accelerator_is_not_a_menu_item() -> None:
    """Enter is the console's default action, never an XRC <accel>."""
    enter_row = next(row for row in accelerators.ACCELERATOR_TABLE if row.key == "Enter")

    assert enter_row.menu_item_id is None


def test_f2_accelerator_is_a_code_side_frame_accelerator() -> None:
    """Phase 6: F2 opens the selected feed row's Crossing Detail.

    It carries no ``menu_item_id`` -- the opposite of the three XRC
    ``<accel>`` rows -- because main.xrc has no menu item for it.
    """
    f2_row = next(row for row in accelerators.ACCELERATOR_TABLE if row.key == "F2")

    assert (f2_row.action, f2_row.menu_item_id) == ("Edit crossing (open detail)", None)


@pytest.mark.parametrize(
    ("key", "action"),
    [
        ("Delete", "Delete selected crossing"),
        ("Ctrl+D", "Delete selected crossing"),
        ("Ctrl+E", "Edit crossing plate"),
    ],
    ids=["delete", "ctrl_d", "ctrl_e"],
)
def test_accelerator_table_declares_the_crossings_panel_hotkeys(key: str, action: str) -> None:
    """The console feed's Delete/Ctrl+D/Ctrl+E rows are code-side.

    Like F2 they carry no ``menu_item_id``: main.xrc declares no menu
    item for either command, so ``MainFrame`` binds them as frame
    accelerators and ``MainFrame.accelerator_entries`` owns the rows.
    """
    row = next(row for row in accelerators.ACCELERATOR_TABLE if row.key == key)

    assert (row.action, row.menu_item_id) == (action, None)
