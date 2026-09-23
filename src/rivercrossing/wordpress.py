# SPDX-License-Identifier: GPL-3.0-only
"""Publish a rendered results page to a WordPress site (REST client).

This module is the app's only WordPress surface. It speaks the
WordPress REST API through the standard library alone -- ``
urllib.request`` for the transport, ``base64`` for the credential
token, ``json`` for both directions -- so a published page costs no
third-party HTTP dependency, and it imports no ``wx``, which keeps it
inside the import-linter contract "wx stays inside rivercrossing.ui".

**HTTPS only, on every request (security).** The target site is
auth-gated, so every call -- :func:`discover`,
:func:`find_page_by_slug`, :func:`publish_page` -- carries the
operator's credentials in an ``Authorization`` header. A request
against a non-https URL is refused with :class:`InsecureURLError`
before that header is built (:func:`_require_https` runs first inside
:func:`_request_json`), so a plain-``http`` site never sees an
Application Password. The same header rides the request's
*unredirected* headers, so a redirect cannot carry the credentials to
another host either.

**Credentials.** :class:`BasicAuth` is a WordPress user's login name
plus an Application Password; :attr:`BasicAuth.header_value` is the
``Basic <base64(user:password)>`` token WordPress expects. wp-admin
displays an Application Password in space-separated chunks ("abcd
efgh ijkl"), so the chunking spaces are removed before encoding --
WordPress compares the password itself, not its display form.

**The three calls.** :func:`discover` reads ``{base}/wp-json/`` and
reports the advertised ``namespaces`` plus whether the index's
``authentication`` mapping offers the ``application-passwords``
handler, which is what makes an Application Password usable at all.
:func:`find_page_by_slug` asks
``{base}/wp-json/wp/v2/pages?slug=<slug>&context=edit`` for the
existing page (``context=edit`` is what an authenticated query needs
to see a draft) and returns its id, or None when the site has no such
page. :func:`publish_page` then either creates the page -- a POST to
``{base}/wp-json/wp/v2/pages`` -- or updates it -- a POST to
``{base}/wp-json/wp/v2/pages/<page_id>`` -- with the same JSON body,
whose ``content`` is the page's bare HTML string. The caller passes
the page id it got from :func:`find_page_by_slug`, which is why the
two share the create/update decision: ``page_id=None`` means "create",
and :class:`PublishedPage.created` reports which one happened.

**Failures are converted, never leaked.** A raised status becomes
:class:`WordPressAuthError` for 401/403 (the Application Password was
refused) or :class:`WordPressResponseError` for any other status,
carrying the WordPress error document's own ``code`` and ``message``.
Anything that means "this site is not answering the REST API as
documented" -- a body that is not JSON (the login wall an auth-gated
site redirects to, an HTML error page), a disabled REST API, an
unreachable host, a JSON body of the wrong shape -- becomes
:class:`WordPressUnavailableError`. No ``urllib`` exception escapes
this module.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "BasicAuth",
    "InsecureURLError",
    "PublishedPage",
    "SiteInfo",
    "WordPressAuthError",
    "WordPressError",
    "WordPressResponseError",
    "WordPressUnavailableError",
    "discover",
    "find_page_by_slug",
    "publish_page",
]

_INDEX_PATH = "wp-json/"
_PAGES_PATH = "wp-json/wp/v2/pages"
_APPLICATION_PASSWORDS = "application-passwords"
_AUTH_STATUSES = frozenset({401, 403})
# A publishing client must not hang on a silent host: urllib waits
# forever without one.
_TIMEOUT_S = 30


@dataclass(frozen=True, slots=True)
class BasicAuth:
    """WordPress Application Password credentials for one site.

    ``username`` is the WordPress user's login name and ``password``
    the Application Password generated for it in wp-admin.
    """

    username: str
    password: str

    @property
    def header_value(self) -> str:
        """Return the HTTP ``Authorization`` header value.

        The Application Password's display chunking spaces are
        removed first: wp-admin shows "abcd efgh ijkl" for the
        password WordPress stores as "abcdefghijkl".
        """
        token = f"{self.username}:{self.password.replace(' ', '')}"
        return f"Basic {base64.b64encode(token.encode('utf-8')).decode('ascii')}"


@dataclass(frozen=True, slots=True)
class SiteInfo:
    """What a site's REST index advertises about itself.

    ``namespaces`` is the index's advertised namespace list;
    ``application_passwords`` is True when the index's
    ``authentication`` mapping offers the ``application-passwords``
    handler, which is the site-side fact that makes a
    :class:`BasicAuth` usable for publishing.
    """

    namespaces: tuple[str, ...]
    application_passwords: bool


@dataclass(frozen=True, slots=True)
class PublishedPage:
    """The page a :func:`publish_page` call created or updated.

    ``id``, ``link`` and ``status`` are the site's own values for the
    saved page -- ``status`` is what WordPress actually applied, which
    a future-dated publish may not be what was asked for. ``created``
    distinguishes the create POST from the update POST, which the site
    answers with the same page shape.
    """

    id: int
    link: str
    status: str
    created: bool


class WordPressError(Exception):
    """Base for every WordPress REST failure this module raises."""


class InsecureURLError(WordPressError):
    """A request was refused because its URL is not https.

    Raised before any ``Authorization`` header is built, so a plain
    ``http`` site never receives the Application Password.
    """


class WordPressAuthError(WordPressError):
    """WordPress refused the credentials (HTTP 401 or 403)."""


class WordPressResponseError(WordPressError):
    """WordPress answered with an error document.

    ``code`` and ``message`` are the error document's own fields --
    WordPress REST errors are ``{code, message, data:{status}}`` --
    and ``status`` is the HTTP status that carried them.
    """

    def __init__(self, code: str, message: str, status: int) -> None:
        """Record the site's error code/message and the HTTP status."""
        super().__init__(f"WordPress error {code} (HTTP {status}): {message}")
        self.code = code
        self.message = message
        self.status = status


class WordPressUnavailableError(WordPressError):
    """The site is not answering the REST API as documented.

    A body that is not JSON (the login wall an auth-gated site
    redirects to, an HTML error page), a disabled REST API, an
    unreachable host, or a JSON body of an unexpected shape.
    """


def discover(base_url: str, auth: BasicAuth) -> SiteInfo:
    """Return what ``{base_url}/wp-json/`` advertises about the site.

    Args:
        base_url: The site's base URL, e.g.
            ``"https://example.test"``; must be https.
        auth: Credentials for that site's own WordPress user.

    Returns:
        The site's :class:`SiteInfo`.

    Raises:
        InsecureURLError: *base_url* is not https.
        WordPressAuthError: WordPress refused the credentials.
        WordPressResponseError: WordPress returned an error document.
        WordPressUnavailableError: The site did not return its REST
            index as a JSON object.
    """
    url = _rest_url(base_url, _INDEX_PATH)
    index = _json_object(_request_json(url, auth), url)
    authentication = index.get("authentication")
    return SiteInfo(
        namespaces=_string_tuple(index.get("namespaces")),
        application_passwords=isinstance(authentication, dict)
        and _APPLICATION_PASSWORDS in authentication,
    )


def find_page_by_slug(base_url: str, auth: BasicAuth, slug: str) -> int | None:
    """Return the id of the site's page at *slug*.

    The query uses ``context=edit`` so an authenticated caller sees
    the page whatever its status -- a draft has to be found before it
    can be updated.

    Args:
        base_url: The site's base URL; must be https.
        auth: Credentials for that site's own WordPress user.
        slug: The page's URL slug, e.g. ``"results"``.

    Returns:
        The first matching page's id, or None when no page has that
        slug.

    Raises:
        InsecureURLError: *base_url* is not https.
        WordPressAuthError: WordPress refused the credentials.
        WordPressResponseError: WordPress returned an error document.
        WordPressUnavailableError: The query did not answer with a
            pages array naming an integer id.
    """
    url = _rest_url(base_url, _PAGES_PATH, {"slug": slug, "context": "edit"})
    pages = _json_array(_request_json(url, auth), url)
    if not pages:
        return None
    return _integer_field(pages[0], "id", url)


def publish_page(  # noqa: PLR0913 -- one argument per field the page itself has
    base_url: str,
    auth: BasicAuth,
    *,
    title: str,
    slug: str,
    content: str,
    status: str,
    parent: int = 0,
    page_id: int | None = None,
) -> PublishedPage:
    """Create a page, or update *page_id*'s page when it is given.

    Args:
        base_url: The site's base URL; must be https.
        auth: Credentials for that site's own WordPress user.
        title: The page title, as the site should store it.
        slug: The page's URL slug.
        content: The page body, a bare HTML string -- the site stores
            it as given, so it is neither escaped nor JSON-encoded
            here.
        status: The page status to ask for, e.g. ``"publish"`` or
            ``"draft"``.
        parent: The parent page's id; 0, the default, means the page
            has no parent.
        page_id: The existing page to update. None, the default,
            creates a new page.

    Returns:
        The :class:`PublishedPage` the site saved, whose ``created``
        reports which of the two calls this was.

    Raises:
        InsecureURLError: *base_url* is not https.
        WordPressAuthError: WordPress refused the credentials.
        WordPressResponseError: WordPress returned an error document.
        WordPressUnavailableError: The site did not answer with the
            saved page.
    """
    created = page_id is None
    url = _rest_url(base_url, _PAGES_PATH if created else f"{_PAGES_PATH}/{page_id}")
    page = {
        "title": title,
        "slug": slug,
        "status": status,
        "parent": parent,
        "content": content,
    }
    payload = _request_json(url, auth, body=json.dumps(page).encode("utf-8"))
    saved = _json_object(payload, url)
    return PublishedPage(
        id=_integer_field(saved, "id", url),
        link=_text_field(saved, "link", url),
        status=_text_field(saved, "status", url),
        created=created,
    )


def _request_json(url: str, auth: BasicAuth, *, body: bytes | None = None) -> object:
    """Return the JSON document *url* answers with.

    Args:
        url: The absolute REST URL to call.
        auth: Credentials to attach as HTTP Basic authentication.
        body: A JSON request body. A body makes the call a POST; None,
            the default, makes it a GET.

    Returns:
        The parsed JSON body, unvalidated: each caller checks the
        shape its own endpoint documents.

    Raises:
        InsecureURLError: *url* is not https.
        WordPressAuthError: WordPress refused the credentials.
        WordPressResponseError: WordPress returned an error document.
        WordPressUnavailableError: The request failed, or the body
            was not JSON.
    """
    _require_https(url)
    request = _build_request(url, auth, body)
    # _require_https has already rejected every non-https URL (S310).
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise _http_error(url, exc) from exc
    except urllib.error.URLError as exc:
        msg = f"WordPress site {url} is unavailable: {exc.reason}"
        raise WordPressUnavailableError(msg) from exc
    return _decode_json(raw, url)


def _build_request(url: str, auth: BasicAuth, body: bytes | None) -> urllib.request.Request:
    """Return the request for *url*, with credentials attached.

    ``Authorization`` rides the *unredirected* headers, so a redirect
    cannot forward the Application Password to another host.
    """
    # _require_https has already rejected every non-https URL (S310).
    method = "POST" if body is not None else "GET"
    request = urllib.request.Request(url, data=body, method=method)  # noqa: S310
    request.add_unredirected_header("Authorization", auth.header_value)
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json; charset=utf-8")
    return request


def _require_https(url: str) -> None:
    """Refuse *url* unless it uses https.

    Raises:
        InsecureURLError: *url*'s scheme is not ``https``.
    """
    if urllib.parse.urlsplit(url).scheme != "https":
        msg = f"refusing to send WordPress credentials to a non-https URL: {url}"
        raise InsecureURLError(msg)


def _rest_url(base_url: str, path: str, params: Mapping[str, str] | None = None) -> str:
    """Return the absolute REST URL for *path* under *base_url*.

    A trailing slash on *base_url* is not doubled; *params*, when
    given, are URL-encoded onto the result.
    """
    url = f"{base_url.rstrip('/')}/{path}"
    if params is None:
        return url
    return f"{url}?{urllib.parse.urlencode(params)}"


def _decode_json(raw: bytes, url: str) -> object:
    """Return the JSON document in *raw*.

    Raises:
        WordPressUnavailableError: *raw* is not UTF-8 JSON -- the
            login wall or HTML error page this client meets whenever
            the REST API is not reachable as JSON.
    """
    try:
        text = raw.decode("utf-8")
        payload: object = json.loads(text)
    except (UnicodeDecodeError, ValueError) as exc:
        msg = f"WordPress site {url} did not return JSON: {exc}"
        raise WordPressUnavailableError(msg) from exc
    return payload


def _http_error(url: str, exc: urllib.error.HTTPError) -> WordPressError:
    """Map one HTTP status error onto this module's error hierarchy.

    Returns the error rather than raising it, so the caller can chain
    the original ``HTTPError`` as its cause.
    """
    code, message = _error_body(exc)
    if exc.code in _AUTH_STATUSES:
        detail = f": {code}: {message}" if code is not None else ""
        return WordPressAuthError(
            f"WordPress rejected the credentials for {url} (HTTP {exc.code}){detail}"
        )
    if code is None:
        return WordPressUnavailableError(
            f"WordPress site {url} returned HTTP {exc.code} without a JSON error body"
        )
    return WordPressResponseError(code, message, exc.code)


def _error_body(exc: urllib.error.HTTPError) -> tuple[str | None, str]:
    """Return the ``code``/``message`` pair *exc*'s body carries.

    ``(None, "")`` means the body carried no WordPress error document
    at all -- an HTML login wall, say, rather than
    ``{code, message, data:{status}}``.
    """
    try:
        detail: object = json.loads(exc.read().decode("utf-8"))
    except UnicodeDecodeError, ValueError:
        return None, ""
    if not isinstance(detail, dict):
        return None, ""
    code = detail.get("code")
    message = detail.get("message")
    if not isinstance(code, str) or not isinstance(message, str):
        return None, ""
    return code, message


def _json_array(payload: object, url: str) -> list[object]:
    """Return *payload* as a JSON array.

    Raises:
        WordPressUnavailableError: *payload* is any other shape.
    """
    if isinstance(payload, list):
        return payload
    msg = f"WordPress site {url} did not return a JSON array"
    raise WordPressUnavailableError(msg)


def _json_object(payload: object, url: str) -> dict[str, object]:
    """Return *payload* as a JSON object.

    Raises:
        WordPressUnavailableError: *payload* is any other shape.
    """
    if isinstance(payload, dict):
        return payload
    msg = f"WordPress site {url} did not return a JSON object"
    raise WordPressUnavailableError(msg)


def _integer_field(mapping: object, name: str, url: str) -> int:
    """Return *mapping*'s integer *name* field.

    Raises:
        WordPressUnavailableError: *mapping* has no such integer.
    """
    value = mapping.get(name) if isinstance(mapping, dict) else None
    if not isinstance(value, int):
        msg = f"WordPress site {url} returned a response without an integer {name}"
        raise WordPressUnavailableError(msg)
    return value


def _text_field(mapping: dict[str, object], name: str, url: str) -> str:
    """Return *mapping*'s string *name* field.

    Raises:
        WordPressUnavailableError: *mapping* has no such string.
    """
    value = mapping.get(name)
    if not isinstance(value, str):
        msg = f"WordPress site {url} returned a response without a {name!r} field"
        raise WordPressUnavailableError(msg)
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    """Return *value*'s string items, ignoring any others.

    The REST index's ``namespaces`` is an array of strings; a missing
    or differently shaped value yields an empty tuple rather than
    failing the whole discovery.
    """
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
