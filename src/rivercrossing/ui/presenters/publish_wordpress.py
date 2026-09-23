# SPDX-License-Identifier: GPL-3.0-only
"""Publish-to-WordPress dialog -- the publish dialog's UI logic.

Pure Python -- no ``wx`` import may ever land here (R-71), and no
I/O either: the dialog's view collects the text and forwards it, and
this module supplies everything else the dialog decides -- the values
it opens with, the label-to-value maps its status and publish-kind
choices render, and the rules the operator's input has to pass before
a publish is even attempted. The HTTP work (discovery, the slug
lookup, the create or update POST) belongs to
:mod:`rivercrossing.wordpress`; nothing here talks to a site.

No state is held between calls, so this module is functions plus one
frozen form snapshot (SIMPLECODE Rule 5). :class:`PublishForm` is that
snapshot: the eight field values at one instant, with
:meth:`~PublishForm.is_valid` and :meth:`~PublishForm.errors` reading
straight through to :func:`validate`.

**HTTPS is a front-door rule here too.** :func:`validate` refuses a
``base_url`` that is not ``https://`` before the operator can reach a
publish button, because the WordPress client refuses the same URL
later (``wordpress.InsecureURLError``) -- the credentials must never
ride a plain-http request. This check is textual only; whether the
site actually answers is the client's to discover.

**The slug algorithm is duplicated on purpose.** :func:`slugify` is the
same transform as ``ui.app._ride_slug``, which names every export file
(``<ride-slug>-results.html`` and friends). Importing it from app.py
would pull ``wx`` into a presenter and cycle at import time, so the
same transform lives here as well; the unit tests pin both to the
same cases. One ride therefore has one slug across the exported file
name and the published page URL.
"""

import re
from dataclasses import dataclass

__all__ = [
    "PUBLISH_KIND_CHOICES",
    "STATUS_CHOICES",
    "PublishForm",
    "default_slug",
    "default_title",
    "kind_value",
    "slugify",
    "status_value",
    "validate",
]

# The status_dlg choice's items, label -> value, in render order.
# WordPress's "future" is deliberately absent: it schedules a post
# against a date this dialog never collects, so the app cannot reach
# it (ui.presenters.settings whitelists the same four).
STATUS_CHOICES: tuple[tuple[str, str], ...] = (
    ("Draft", "draft"),
    ("Publish", "publish"),
    ("Pending", "pending"),
    ("Private", "private"),
)

# The status an unrecognised label resolves to: a blank or unexpected
# control value must never publish something by accident. Draft is also
# the dialog's own opening choice (settings.AppSettings.wp_status).
_DEFAULT_STATUS = "draft"

# The label -> value map status_value looks up, derived from the
# choice's own items so the two can never drift apart.
_STATUS_BY_LABEL: dict[str, str] = dict(STATUS_CHOICES)

# The publish_kind choice's items, label -> value, in render order: the
# published page either carries every entry ("full") or just the
# podium ("podium").
PUBLISH_KIND_CHOICES: tuple[tuple[str, str], ...] = (
    ("Full results", "full"),
    ("Podium results", "podium"),
)

# The kind an unrecognised label resolves to: the full results page is
# what this dialog has always published, so a blank or unexpected
# control value never narrows a page to the podium by accident. Full is
# also the dialog's own opening choice (settings.AppSettings.wp_kind).
_DEFAULT_KIND = "full"

# The label -> value map kind_value looks up, derived from the choice's
# own items so the two can never drift apart.
_KIND_BY_LABEL: dict[str, str] = dict(PUBLISH_KIND_CHOICES)

# The choice's values, for the value-or-label test _resolved_kind runs.
_KIND_VALUES: tuple[str, ...] = tuple(value for _label, value in PUBLISH_KIND_CHOICES)

# A slug is mandatory, so the transform's fallback is the same word the
# export filenames use when a ride has no name at all.
_FALLBACK_SLUG = "results"

_HTTPS_PREFIX = "https://"
_HTTPS_REQUIRED = (
    "site URL must start with https:// (WordPress credentials are only sent over HTTPS)"
)


def slugify(name: str) -> str:
    """Return *name* as a URL slug.

    Lowercased, every non-alphanumeric character replaced by ``-``,
    runs of ``-`` collapsed, and the edges stripped; a name with no
    alphanumeric character at all becomes :data:`_FALLBACK_SLUG`.
    Non-ASCII letters and digits are kept, since ``str.isalnum``
    accepts them and WordPress does too.

    Args:
        name: The ride name to slugify.

    Returns:
        The slug, never empty.
    """
    lowered = "".join(ch if ch.isalnum() else "-" for ch in name.lower())
    collapsed = re.sub(r"-+", "-", lowered).strip("-")
    return collapsed or _FALLBACK_SLUG


def _resolved_kind(kind: str) -> str:
    """Return *kind* as one of :data:`PUBLISH_KIND_CHOICES`' values.

    Accepts a choice's value (``"full"``/``"podium"``) or a rendered
    label; anything else -- a blank or unexpected control value --
    lands on the full kind through :func:`kind_value`, so a page is
    never narrowed to the podium by accident.
    """
    if kind in _KIND_VALUES:
        return kind
    return kind_value(kind)


def default_title(name: str, kind: str = "full") -> str:
    """Return the page title the dialog opens with.

    Args:
        name: The live ride's name.
        kind: The publish kind, one of :data:`PUBLISH_KIND_CHOICES`'
            values; any other kind opens on the full form.

    Returns:
        ``"<name> — Full Results"``, or ``"<name> — Podium Results"``
        for the podium kind.
    """
    suffix = "Podium Results" if _resolved_kind(kind) == "podium" else "Full Results"
    return f"{name} — {suffix}"


def default_slug(name: str, kind: str = "full") -> str:
    """Return the page slug the dialog opens with.

    Args:
        name: The live ride's name.
        kind: The publish kind, one of :data:`PUBLISH_KIND_CHOICES`'
            values; any other kind opens on the full form.

    Returns:
        :func:`slugify` of ``"<name> full results"``, or of ``"<name>
        podium results"`` for the podium kind -- a nameless ride still
        yields a non-empty slug, e.g. ``"full-results"``.
    """
    tail = "podium results" if _resolved_kind(kind) == "podium" else "full results"
    return slugify(f"{name} {tail}")


def kind_value(label: str) -> str:
    """Return the publish kind a choice label carries.

    The labels are :data:`PUBLISH_KIND_CHOICES`' own items, so the
    dialog's choice and this map can never drift apart.

    Args:
        label: One of the choice's rendered labels.

    Returns:
        The label's value, or ``"full"`` for any other label -- an
        unknown or blank control value must never narrow a page to the
        podium by accident.
    """
    return _KIND_BY_LABEL.get(label, _DEFAULT_KIND)


def status_value(label: str) -> str:
    """Return the WordPress status :data:`STATUS_CHOICES` label carries.

    Args:
        label: One of the choice's rendered labels.

    Returns:
        The label's value, or ``"draft"`` for any other label -- a
        blank or unexpected control value must never publish.
    """
    return _STATUS_BY_LABEL.get(label, _DEFAULT_STATUS)


def validate(  # noqa: PLR0913 -- one argument per field the form collects
    *,
    base_url: str,
    username: str,
    password: str,
    title: str,
    slug: str,
) -> tuple[str, ...]:
    """Return why the dialog's fields cannot be published.

    Messages are ordered top-of-form first and each names the field it
    is about. No site is contacted: whether *base_url* answers, and
    whether the credentials are accepted, is the WordPress client's to
    report. The parent page and status choice are never errors -- a
    blank parent means "top level" and the status is one of the four
    choice values.

    Args:
        base_url: The site's base URL, e.g. ``"https://example.test"``.
        username: The site's own WordPress login name.
        password: The Application Password generated for that user.
        title: The page title to publish.
        slug: The page's URL slug.

    Returns:
        One message per failing field, empty when every field passes.
    """
    errors: list[str] = []
    if not base_url:
        errors.append("site URL is required")
    elif not base_url.startswith(_HTTPS_PREFIX):
        errors.append(_HTTPS_REQUIRED)
    if not username:
        errors.append("username is required")
    if not password:
        errors.append("application password is required")
    if not title:
        errors.append("page title is required")
    if not slug:
        errors.append("slug is required")
    return tuple(errors)


@dataclass(frozen=True, slots=True)
class PublishForm:
    """The publish dialog's field values at one instant.

    Built by the view when the operator asks to publish and read by
    the publish path, so the dialog cannot change out from under a
    publish already in flight.

    Attributes:
        base_url: The site's base URL, e.g.
            ``"https://example.test"``.
        username: The site's own WordPress login name.
        password: The Application Password generated for that user.
        title: The page title.
        slug: The page's URL slug.
        parent: The parent page's id-or-slug as typed; ``""`` is top
            level, which the publish path turns into WordPress's 0.
        status: The WordPress status to ask for, one of
            :data:`STATUS_CHOICES`' values.
        kind: The page the results carry, one of
            :data:`PUBLISH_KIND_CHOICES`' values. It is the last
            field, so the frozen dataclass can default it.
    """

    base_url: str
    username: str
    password: str
    title: str
    slug: str
    parent: str
    status: str
    kind: str = "full"

    def is_valid(self) -> bool:
        """Return whether every field passes :func:`validate`."""
        return not self.errors()

    def errors(self) -> tuple[str, ...]:
        """Return the form's validation messages, empty when valid."""
        return validate(
            base_url=self.base_url,
            username=self.username,
            password=self.password,
            title=self.title,
            slug=self.slug,
        )
