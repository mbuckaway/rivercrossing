# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for E8.2.2's user-guide opening logic (``ui.help``).

``ui.help`` is wx-free: the window->anchor map, ``anchor_for``, the
guide path and ``open_guide``'s URL all run without a display, so the
mapping, the defaulting, the missing-guide error and the guide-file
consistency are exactly the logic R-71's >=90% branch-coverage gate
covers in the headless suite. The wx half -- resolving the active
top-level window and firing the ``mi_user_guide`` route -- is proven
by the spawned-subprocess scenarios in ``tests/functional/
test_user_guide.py`` instead (the same split ``test_zoom.py``/
``test_theme.py`` draw).
"""

import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui import help as help_module

# The repo's committed guide, resolved independently of the module
# under test so ``guide_path()`` cannot tautologically pass against
# itself (the same layout ``help.guide_path`` must find).
_GUIDE_FILE = Path(__file__).resolve().parents[3] / "docs" / "user-guide.html"

KNOWN_ANCHORS = frozenset({help_module.DEFAULT_ANCHOR, *help_module.ANCHOR_BY_WINDOW.values()})


# --- anchor_for: every mapped window, then the defaulting -----------


@pytest.mark.parametrize(
    ("window_name", "expected_anchor"),
    tuple(help_module.ANCHOR_BY_WINDOW.items()),
)
def test_anchor_for_given_each_mapped_window_returns_its_anchor(
    window_name: str, expected_anchor: str
) -> None:
    """Every mapped window/deep-link target names its own chapter."""
    assert help_module.anchor_for(window_name) == expected_anchor


@pytest.mark.parametrize("window_name", [None, "", "main_frame", "no_such_dialog"])
def test_anchor_for_given_an_unmapped_window_returns_the_default_anchor(
    window_name: str | None,
) -> None:
    """None, empty and unknown names all land on the default chapter."""
    assert help_module.anchor_for(window_name) == help_module.DEFAULT_ANCHOR


@given(st.none() | st.text())
def test_anchor_for_given_any_window_name_returns_a_known_anchor(
    window_name: str | None,
) -> None:
    """Property (T-7): the result is always a real guide anchor.

    ``anchor_for`` never invents an anchor string -- every input,
    known or not, maps onto the closed set of anchors the guide
    actually declares.
    """
    assert help_module.anchor_for(window_name) in KNOWN_ANCHORS


# --- guide anchors exist in the authored guide file ------------------


def _missing_guide_anchors(guide_html: str, anchors: set[str]) -> list[str]:
    """Return *anchors* with no ``id="..."`` match in *guide_html*."""
    return sorted(anchor for anchor in anchors if f'id="{anchor}"' not in guide_html)


def test_every_mapped_anchor_exists_as_an_id_in_the_user_guide() -> None:
    """Each anchor the dialogs deep-link to is a real guide section."""
    guide_html = _GUIDE_FILE.read_text(encoding="utf-8")

    assert _missing_guide_anchors(guide_html, set(help_module.ANCHOR_BY_WINDOW.values())) == []


# --- guide_path ------------------------------------------------------


def test_guide_path_resolves_to_the_existing_user_guide_file() -> None:
    """guide_path() names the repo's committed user-guide file."""
    assert help_module.guide_path() == _GUIDE_FILE
    assert _GUIDE_FILE.is_file()


def test_guide_path_raises_when_the_guide_file_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout with no guide raises GuideMissingError, naming it."""
    monkeypatch.setattr(help_module, "__file__", str(tmp_path / "help.py"))

    with pytest.raises(help_module.GuideMissingError, match=re.escape("user guide not found")):
        help_module.guide_path()


# --- open_guide: the URL-building seam --------------------------------


def test_open_guide_opens_the_guide_at_the_given_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """open_guide() opens the guide at *anchor* via webbrowser."""
    opened: list[str] = []
    monkeypatch.setattr(help_module.webbrowser, "open", lambda url: opened.append(url) or True)

    url = help_module.open_guide("settings")

    assert url == f"{help_module.guide_path().as_uri()}#settings"
    assert opened == [url]


def test_open_guide_defaults_to_the_opening_anchor_when_none_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No anchor opens the guide at its default opening chapter."""
    opened: list[str] = []
    monkeypatch.setattr(help_module.webbrowser, "open", lambda url: opened.append(url) or True)

    url = help_module.open_guide()

    assert url == f"{help_module.guide_path().as_uri()}#{help_module.DEFAULT_ANCHOR}"
    assert opened == [url]
