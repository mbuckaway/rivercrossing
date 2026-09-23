# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the Publish-to-WordPress dialog presenter (headless).

``ui.presenters.publish_wordpress`` carries the publish dialog's whole
UI logic as module-level functions and one frozen form dataclass --
wx-free (R-71, the MVP/passive-view rule) and I/O-free, so it runs on
the host without a window. It supplies the defaults the dialog opens
with, the label -> value map its status choice renders, and the
pre-flight validation the operator sees inline; the HTTP work itself
belongs to :mod:`rivercrossing.wordpress`.

``slugify`` is the app's one slug algorithm and must stay identical to
``ui.app._ride_slug`` -- the export filenames already use that
transform -- so the parity cases below pin exactly what app.py does.
Nothing here imports ``ui.app``: app.py pulls in ``wx``, and a
presenter must not.

Written FIRST: this file is red until ``publish_wordpress.py`` lands.
"""

from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.presenters.publish_wordpress import (
    STATUS_CHOICES,
    PublishForm,
    default_slug,
    default_title,
    slugify,
    status_value,
    validate,
)

# A complete, valid dialog: a site that answers over https, its own
# WordPress user, and the title/slug the dialog derives from a ride.
_VALID: dict[str, str] = {
    "base_url": "https://example.test",
    "username": "mark",
    "password": "abcd efgh ijkl",
    "title": "GORBA Test Ride #1 — Results",
    "slug": "gorba-test-ride-1-results",
}

# The two form fields validate() never inspects: the parent page text
# the operator may have typed, and the status choice. Blank parent
# means "top level", which is WordPress's own 0.
_PARENT_AND_STATUS: dict[str, str] = {"parent": "", "status": "draft"}


def _form(**overrides: str) -> PublishForm:
    """Return the valid form with *overrides* applied."""
    return PublishForm(**{**_VALID, **_PARENT_AND_STATUS, **overrides})


# ------------------------------------------ STATUS_CHOICES: the choice


def test_status_choices_carry_the_four_wordpress_statuses_in_order() -> None:
    """The dialog's choice lists exactly these four label/value pairs.

    ``future`` is deliberately absent: it schedules a post against a
    date this dialog never collects.
    """
    assert STATUS_CHOICES == (
        ("Draft", "draft"),
        ("Publish", "publish"),
        ("Pending", "pending"),
        ("Private", "private"),
    )


@pytest.mark.parametrize(("label", "expected"), STATUS_CHOICES)
def test_status_value_maps_every_choice_label_to_its_value(label: str, expected: str) -> None:
    """Every rendered label resolves to the value WordPress is sent."""
    assert status_value(label) == expected


@pytest.mark.parametrize("label", ["Scheduled", "Future", "Trash", "", "   "])
def test_status_value_falls_back_to_draft_for_any_other_label(label: str) -> None:
    """An unknown or blank label never publishes; it lands as draft."""
    assert status_value(label) == "draft"


# ---------------------------- slugify: the same algorithm as _ride_slug


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("GORBA Test Ride #1", "gorba-test-ride-1", id="ride-name"),
        pytest.param("", "results", id="empty"),
        pytest.param("!!!", "results", id="punctuation-only"),
        pytest.param("   ", "results", id="spaces-only"),
        pytest.param("---", "results", id="dashes-only"),
        pytest.param("Sea to Sky", "sea-to-sky", id="spaces-to-dashes"),
        pytest.param("a--b___c", "a-b-c", id="runs-collapse"),
        pytest.param("  padded name  ", "padded-name", id="padded"),
        pytest.param("--leading-and-trailing--", "leading-and-trailing", id="edge-dashes"),
        pytest.param("5.5 km loop", "5-5-km-loop", id="digits-kept"),
        pytest.param("2026", "2026", id="digits-only"),
        pytest.param("Étape—Ünïcode", "étape-ünïcode", id="unicode-lowered-kept"),
        pytest.param("Ünïcode", "ünïcode", id="unicode-only"),
    ],
)
def test_slugify_applies_the_ride_slug_transform(name: str, expected: str) -> None:
    """Lowercase, non-alphanumerics to "-", runs collapsed."""
    assert slugify(name) == expected


@given(name=st.text())
def test_slugify_output_is_slug_shaped_and_idempotent(name: str) -> None:
    """T-7 invariant: any input slugifies to a non-empty slug run.

    The output holds only alphanumerics and single dashes, never starts
    or ends on one, and slugifies to itself -- so re-slugifying a slug
    is a no-op.
    """
    slug = slugify(name)

    assert slug != ""
    assert all(character.isalnum() or character == "-" for character in slug)
    assert "--" not in slug
    assert slug == slug.strip("-")
    assert slugify(slug) == slug


# --------------------------- default_title / default_slug: the prefill


def test_default_title_appends_the_results_suffix_to_the_ride_name() -> None:
    """The dialog opens on "<ride name> — Results"."""
    assert default_title("GORBA Test Ride #1") == "GORBA Test Ride #1 — Results"


def test_default_slug_slugifies_the_ride_name_with_the_results_suffix() -> None:
    """The dialog opens with a slug derived from the same ride name."""
    assert default_slug("GORBA Test Ride #1") == "gorba-test-ride-1-results"


def test_default_slug_of_a_nameless_ride_is_results() -> None:
    """A ride with no name still prefills a usable slug."""
    assert default_slug("") == "results"


# ------------------------------------------------- validate: the rules


def test_validate_accepts_a_complete_https_form() -> None:
    """A filled-in https form has nothing to report."""
    assert validate(**_VALID) == ()


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        pytest.param("base_url", ("site URL is required",), id="base-url"),
        pytest.param("username", ("username is required",), id="username"),
        pytest.param("password", ("application password is required",), id="password"),
        pytest.param("title", ("page title is required",), id="title"),
        pytest.param("slug", ("slug is required",), id="slug"),
    ],
)
def test_validate_reports_the_one_blank_field(field: str, expected: tuple[str, ...]) -> None:
    """Each blank field refuses on its own, naming itself."""
    assert validate(**{**_VALID, field: ""}) == expected


@pytest.mark.parametrize(
    "base_url",
    ["http://example.test", "example.test", "//example.test", "ftp://example.test"],
)
def test_validate_rejects_a_site_url_that_is_not_https(base_url: str) -> None:
    """A non-https site is refused, naming the HTTPS requirement.

    WordPress credentials are only ever sent over HTTPS, so the dialog
    refuses a plain-http site before the client could.
    """
    assert validate(**{**_VALID, "base_url": base_url}) == (
        "site URL must start with https:// (WordPress credentials are only sent over HTTPS)",
    )


def test_validate_accepts_an_https_url_with_a_path() -> None:
    """A site under a path is still https, so it is usable."""
    assert validate(**{**_VALID, "base_url": "https://example.test/blog"}) == ()


def test_validate_reports_every_blank_field_top_of_form_first() -> None:
    """All five blanks report in the dialog's own field order.

    A blank URL yields its "required" message alone -- never that plus
    the https message -- because there is nothing to check a scheme on.
    """
    assert validate(base_url="", username="", password="", title="", slug="") == (
        "site URL is required",
        "username is required",
        "application password is required",
        "page title is required",
        "slug is required",
    )


def test_validate_keeps_its_field_order_when_the_url_is_http() -> None:
    """The URL's https refusal stays ahead of every other field's."""
    assert validate(
        base_url="http://example.test",
        username="",
        password="",
        title="",
        slug="",
    ) == (
        "site URL must start with https:// (WordPress credentials are only sent over HTTPS)",
        "username is required",
        "application password is required",
        "page title is required",
        "slug is required",
    )


# ------------------------------------ PublishForm: the dialog's form


def test_publish_form_is_valid_when_every_field_passes() -> None:
    """A form built from the dialog's own values validates."""
    assert _form().is_valid()


def test_publish_form_is_invalid_when_a_field_is_blank() -> None:
    """One blank field makes the form invalid."""
    assert _form(username="").is_valid() is False


def test_publish_form_errors_are_validates_own_messages() -> None:
    """``errors()`` reports the form's own messages, in order."""
    assert _form(username="", slug="").errors() == (
        "username is required",
        "slug is required",
    )


@pytest.mark.parametrize("parent", ["", "12", "results", "grand-parent"])
def test_publish_form_accepts_any_parent_page_text(parent: str) -> None:
    """The typed parent page never fails validation.

    Blank means top level; an id or a slug is passed through to the
    publish path as typed, so neither can block the publish.
    """
    assert _form(parent=parent).is_valid()


def test_publish_form_cannot_be_mutated_in_place() -> None:
    """The form is a snapshot of the dialog at one instant."""
    form = _form()

    with pytest.raises(FrozenInstanceError, match="cannot assign to field"):
        form.title = "Other"
