# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the wx-free WordPress REST client (wordpress.py).

``rivercrossing.wordpress`` publishes a rendered results page to a
WordPress site over its REST API. It uses the standard library only
(``urllib.request``, never a third-party HTTP client) and never
imports ``wx``, so the import-linter contract "wx stays inside
rivercrossing.ui" covers it once the module joins that contract's
source list.

The contract under test:

- **HTTPS only, on every request.** ``discover``,
  ``find_page_by_slug`` and ``publish_page`` all refuse a non-https
  ``base_url`` with ``InsecureURLError`` before an ``Authorization``
  header can exist -- the target site is auth-gated and every call
  carries the WordPress Application Password.
- ``BasicAuth.header_value`` is ``Basic <base64(user:password)>``
  with the display chunking's spaces removed, because wp-admin shows
  an Application Password as "abcd efgh ijkl".
- ``discover`` reads the REST index's ``namespaces`` and whether its
  ``authentication`` mapping offers ``application-passwords``.
- ``find_page_by_slug`` asks
  ``/wp/v2/pages?slug=<slug>&context=edit`` and returns the first
  match's ``id``, or None when nothing matches.
- ``publish_page`` POSTs a create body to ``/wp/v2/pages`` when no
  ``page_id`` is given, an update body to ``/wp/v2/pages/<id>`` when
  one is, and reports which happened through ``PublishedPage.created``.
- Every failure is converted: HTTP 401/403 to ``WordPressAuthError``;
  any other status carrying a WordPress error document to
  ``WordPressResponseError`` (which keeps that document's ``code``,
  ``message`` and status); a body that is not JSON at all -- a login
  wall, a disabled REST API -- to ``WordPressUnavailableError``.

The HTTP seam is ``urllib.request.urlopen`` itself, monkeypatched by
:func:`_install`: the module under test looks that attribute up when
it makes a request, so patching the stdlib module is the real
boundary, and :class:`_Recorder` records exactly what was sent.

Written FIRST: this file is red until ``wordpress.py`` lands.
"""

import base64
import io
import json
import re
import urllib.error
import urllib.request
from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING, NamedTuple

import pytest

import rivercrossing.wordpress
from rivercrossing.wordpress import (
    BasicAuth,
    InsecureURLError,
    PublishedPage,
    SiteInfo,
    WordPressAuthError,
    WordPressError,
    WordPressResponseError,
    WordPressUnavailableError,
    discover,
    find_page_by_slug,
    publish_page,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Self

_BASE = "https://example.test"
_USER = "mark"
_CHUNKED = "abcd efgh ijkl"
_SOLID = "abcdefghijkl"
# base64("mark:abcdefghijkl") -- the chunked form must give the same.
_AUTH_HEADER = "Basic bWFyazphYmNkZWZnaGlqa2w="
_AUTH = BasicAuth(username=_USER, password=_CHUNKED)

# logic-coverage-exempt: T-7 -- AGENTS.md's TDD section permits unit
# tests only; a property/functional suite needs expressed permission.
# BasicAuth.header_value, the module's one pure function, is pinned by
# exact base64 equality instead of by a Hypothesis invariant.

_INDEX_BODY = {
    "name": "Example Site",
    "namespaces": ["wp/v2", "wp-site-health/v1"],
    "authentication": {"application-passwords": {"endpoints": {"authorization": "..."}}},
}

_WORDPRESS_ERROR_BODY = {
    "code": "rest_forbidden",
    "message": "Sorry, you are not allowed to do that.",
    "data": {"status": 403},
}

_NON_JSON_BODIES = [
    b"<!DOCTYPE html><html><body>Please log in</body></html>",
    b"\xff\xfe\x00\x01not text at all",
]

_MALFORMED_ERROR_BODIES = [
    b'["rest_forbidden"]',
    b'{"code": 42, "message": "Sorry."}',
    b'{"code": "rest_forbidden", "message": 403}',
    b'{"code": "rest_forbidden"',
]


class _Response:
    """Stand-in for what urlopen yields as a context manager."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> Self:
        """Return self, exactly as an http.client response does."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close nothing: this fake holds bytes, not a socket."""

    def read(self) -> bytes:
        """Return the canned body."""
        return self._body


class _Call(NamedTuple):
    """One recorded urlopen call: what was sent, and how."""

    request: urllib.request.Request
    timeout: float


class _Recorder:
    """Stand-in for urllib.request.urlopen that records its calls.

    It answers with a canned body, or raises the canned error, so a
    test can drive any status/transport failure through the module
    without a network.
    """

    def __init__(self, body: bytes = b"{}", error: Exception | None = None) -> None:
        self.body = body
        self.error = error
        self.calls: list[_Call] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> _Response:
        """Record one call, then answer with the canned reply."""
        self.calls.append(_Call(request, timeout))
        if self.error is not None:
            raise self.error
        return _Response(self.body)


def _install(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes = b"{}",
    error: Exception | None = None,
) -> _Recorder:
    """Install the recording urlopen stand-in and return it."""
    recorder = _Recorder(body, error)
    monkeypatch.setattr(urllib.request, "urlopen", recorder)
    return recorder


def _install_index(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """Install the stand-in answering with the canned REST index."""
    return _install(monkeypatch, json.dumps(_INDEX_BODY).encode("utf-8"))


def _only_call(recorder: _Recorder) -> _Call:
    """Return the one recorded call, asserting there was exactly one."""
    assert len(recorder.calls) == 1
    return recorder.calls[0]


def _json_body(request: urllib.request.Request) -> dict[str, object]:
    """Return the request's JSON body, asserting it is an object."""
    assert isinstance(request.data, bytes)
    body = json.loads(request.data.decode("utf-8"))
    assert isinstance(body, dict)
    return body


def _http_error(status: int, body: bytes) -> urllib.error.HTTPError:
    """Build the HTTPError a site answers *status* with."""
    return urllib.error.HTTPError(
        f"{_BASE}/wp-json/",
        status,
        "WordPress error",
        {"Content-Type": "application/json"},
        io.BytesIO(body),
    )


@pytest.mark.parametrize(
    ("username", "password", "expected"),
    [
        (_USER, _SOLID, _AUTH_HEADER),
        (_USER, _CHUNKED, _AUTH_HEADER),
        (_USER, "abcd  efgh   ijkl", _AUTH_HEADER),
        ("", "", "Basic Og=="),
    ],
)
def test_basic_auth_header_value_encodes_credentials_without_chunk_spaces(
    username: str, password: str, expected: str
) -> None:
    """basic64(user:password); the wp-admin chunking spaces go first."""
    auth = BasicAuth(username=username, password=password)

    header = auth.header_value

    assert header == expected


def test_basic_auth_header_value_matches_an_independent_base64_encoding() -> None:
    """The token is plain standard base64 of "user:password"."""
    auth = BasicAuth(username=_USER, password=_SOLID)

    header = auth.header_value

    assert header == f"Basic {base64.b64encode(b'mark:abcdefghijkl').decode('ascii')}"


def test_basic_auth_is_immutable() -> None:
    """Credentials are a value object: a field cannot be reassigned."""
    auth = BasicAuth(username=_USER, password=_CHUNKED)

    with pytest.raises(FrozenInstanceError, match=re.escape("cannot assign to field 'username'")):
        auth.username = "someone-else"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InsecureURLError, WordPressError),
        (WordPressAuthError, WordPressError),
        (WordPressResponseError, WordPressError),
        (WordPressUnavailableError, WordPressError),
    ],
)
def test_every_public_error_derives_from_wordpress_error(
    error: type[Exception], expected: type[Exception]
) -> None:
    """One catchable base covers the whole module's failures."""
    assert issubclass(error, expected)


def test_wordpress_response_error_carries_the_sites_code_message_and_status() -> None:
    """A response error keeps the site's own error document."""
    error = WordPressResponseError("rest_forbidden", "Sorry.", 403)

    assert (error.code, error.message, error.status, str(error)) == (
        "rest_forbidden",
        "Sorry.",
        403,
        "WordPress error rest_forbidden (HTTP 403): Sorry.",
    )


def test_discover_parses_namespaces_and_application_password_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The index's namespaces and auth mapping reach SiteInfo."""
    _install_index(monkeypatch)

    info = discover(_BASE, _AUTH)

    assert info == SiteInfo(
        namespaces=("wp/v2", "wp-site-health/v1"),
        application_passwords=True,
    )


def test_discover_requests_the_rest_index_as_a_get_with_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET {base}/wp-json/, Basic auth attached, still one request."""
    opener = _install_index(monkeypatch)

    discover(_BASE, _AUTH)

    call = _only_call(opener)
    request = call.request
    assert request.full_url == "https://example.test/wp-json/"
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == _AUTH_HEADER
    assert request.get_header("Accept") == "application/json"
    assert request.data is None
    assert call.timeout == rivercrossing.wordpress._TIMEOUT_S


def test_discover_keeps_credentials_off_a_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authorization rides the unredirected headers only."""
    opener = _install_index(monkeypatch)

    discover(_BASE, _AUTH)

    request = _only_call(opener).request
    assert (list(request.unredirected_hdrs), list(request.headers)) == (
        ["Authorization"],
        ["Accept"],
    )


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://example.test", "https://example.test/wp-json/"),
        ("https://example.test/", "https://example.test/wp-json/"),
        ("https://example.test/blog/", "https://example.test/blog/wp-json/"),
    ],
)
def test_discover_appends_the_index_path_to_the_base_url(
    monkeypatch: pytest.MonkeyPatch, base_url: str, expected: str
) -> None:
    """A trailing slash is never doubled; a subdirectory survives."""
    opener = _install_index(monkeypatch)

    discover(base_url, _AUTH)

    assert _only_call(opener).request.full_url == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            {"namespaces": ["wp/v2"], "authentication": {"application-passwords": {}}},
            SiteInfo(namespaces=("wp/v2",), application_passwords=True),
        ),
        (
            {"namespaces": ["wp/v2"], "authentication": {}},
            SiteInfo(namespaces=("wp/v2",), application_passwords=False),
        ),
        (
            {"namespaces": ["wp/v2"]},
            SiteInfo(namespaces=("wp/v2",), application_passwords=False),
        ),
        (
            {"authentication": {"application-passwords": {}}},
            SiteInfo(namespaces=(), application_passwords=True),
        ),
        ({"namespaces": []}, SiteInfo(namespaces=(), application_passwords=False)),
        (
            {"namespaces": [1, "wp/v2"]},
            SiteInfo(namespaces=("wp/v2",), application_passwords=False),
        ),
    ],
)
def test_discover_reads_support_from_the_index_authentication_mapping(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, object], expected: SiteInfo
) -> None:
    """Only the exact application-passwords key counts as support."""
    _install(monkeypatch, json.dumps(body).encode("utf-8"))

    info = discover(_BASE, _AUTH)

    assert info == expected


@pytest.mark.parametrize("body", _NON_JSON_BODIES)
def test_discover_raises_unavailable_error_when_index_is_not_json(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    """A login wall is not the REST index."""
    _install(monkeypatch, body)

    with pytest.raises(WordPressUnavailableError, match=re.escape("did not return JSON")):
        discover(_BASE, _AUTH)


def test_discover_raises_unavailable_error_when_index_is_not_a_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JSON body of the wrong shape is not an index either."""
    _install(monkeypatch, b'["wp/v2"]')

    with pytest.raises(WordPressUnavailableError, match=re.escape("did not return a JSON object")):
        discover(_BASE, _AUTH)


def test_discover_maps_http_401_with_wordpress_body_to_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid Application Password names the site's own error."""
    _install(
        monkeypatch,
        error=_http_error(401, json.dumps(_WORDPRESS_ERROR_BODY).encode("utf-8")),
    )

    with pytest.raises(WordPressAuthError, match=re.escape("(HTTP 401)")) as excinfo:
        discover(_BASE, _AUTH)

    assert str(excinfo.value) == (
        "WordPress rejected the credentials for https://example.test/wp-json/ "
        "(HTTP 401): rest_forbidden: Sorry, you are not allowed to do that."
    )


def test_discover_maps_http_403_without_json_body_to_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The status alone decides: 403 is always a credentials problem."""
    _install(monkeypatch, error=_http_error(403, b"<html>Forbidden</html>"))

    with pytest.raises(WordPressAuthError, match=re.escape("(HTTP 403)")) as excinfo:
        discover(_BASE, _AUTH)

    assert str(excinfo.value) == (
        "WordPress rejected the credentials for https://example.test/wp-json/ (HTTP 403)"
    )


def test_discover_maps_http_500_with_wordpress_body_to_response_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any other status keeps the site's code, message and status."""
    body = json.dumps(
        {
            "code": "internal_server_error",
            "message": "Something went wrong.",
            "data": {"status": 500},
        }
    ).encode("utf-8")
    _install(monkeypatch, error=_http_error(500, body))

    with pytest.raises(
        WordPressResponseError, match=re.escape("internal_server_error")
    ) as excinfo:
        discover(_BASE, _AUTH)

    assert (excinfo.value.code, excinfo.value.message, excinfo.value.status) == (
        "internal_server_error",
        "Something went wrong.",
        500,
    )


def test_discover_maps_http_404_without_json_body_to_unavailable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled REST API answers 404 with an HTML page."""
    _install(monkeypatch, error=_http_error(404, b"<html>REST API disabled</html>"))

    with pytest.raises(
        WordPressUnavailableError,
        match=re.escape("returned HTTP 404 without a JSON error body"),
    ):
        discover(_BASE, _AUTH)


@pytest.mark.parametrize("body", _MALFORMED_ERROR_BODIES)
def test_discover_maps_malformed_error_body_to_unavailable_error(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    """A body with no code/message pair is not a WordPress error."""
    _install(monkeypatch, error=_http_error(500, body))

    with pytest.raises(
        WordPressUnavailableError,
        match=re.escape("returned HTTP 500 without a JSON error body"),
    ):
        discover(_BASE, _AUTH)


def test_discover_maps_a_connection_failure_to_unavailable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable host is an unavailable site, not a crash."""
    _install(monkeypatch, error=urllib.error.URLError(OSError("Name or service not known")))

    with pytest.raises(
        WordPressUnavailableError, match=re.escape("Name or service not known")
    ) as excinfo:
        discover(_BASE, _AUTH)

    assert str(excinfo.value) == (
        "WordPress site https://example.test/wp-json/ is unavailable: Name or service not known"
    )


@pytest.mark.parametrize(
    "call",
    [
        lambda: discover("http://example.test", _AUTH),
        lambda: find_page_by_slug("http://example.test", _AUTH, "about"),
        lambda: publish_page(
            "http://example.test",
            _AUTH,
            title="Results",
            slug="results",
            content="<p>hi</p>",
            status="publish",
        ),
    ],
    ids=["discover", "find_page_by_slug", "publish_page"],
)
def test_every_request_refuses_a_non_https_base_url(
    monkeypatch: pytest.MonkeyPatch, call: Callable[[], object]
) -> None:
    """No call attaches credentials over plain http; none connects."""
    opener = _install_index(monkeypatch)

    with pytest.raises(InsecureURLError, match=re.escape("non-https URL")) as excinfo:
        call()

    assert excinfo.value.args[0].startswith(
        "refusing to send WordPress credentials to a non-https URL: http://example.test/"
    )
    assert opener.calls == []


def test_find_page_by_slug_returns_the_first_matching_page_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The id of the first page the slug query returns."""
    body = json.dumps([{"id": 42, "slug": "about", "title": {"rendered": "About"}}]).encode()
    opener = _install(monkeypatch, body)

    page_id = find_page_by_slug(_BASE, _AUTH, "about")

    request = _only_call(opener).request
    assert page_id == 42
    assert request.full_url == ("https://example.test/wp-json/wp/v2/pages?slug=about&context=edit")
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == _AUTH_HEADER


@pytest.mark.parametrize(
    ("slug", "expected_url"),
    [
        ("about", "https://example.test/wp-json/wp/v2/pages?slug=about&context=edit"),
        ("", "https://example.test/wp-json/wp/v2/pages?slug=&context=edit"),
        (
            "a team page",
            "https://example.test/wp-json/wp/v2/pages?slug=a+team+page&context=edit",
        ),
    ],
)
def test_find_page_by_slug_queries_on_the_slug_in_edit_context(
    monkeypatch: pytest.MonkeyPatch, slug: str, expected_url: str
) -> None:
    """The slug is URL-encoded onto an edit-context query."""
    opener = _install(monkeypatch, b"[]")

    find_page_by_slug(_BASE, _AUTH, slug)

    assert _only_call(opener).request.full_url == expected_url


def test_find_page_by_slug_returns_none_when_no_page_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty pages list means the page does not exist yet."""
    _install(monkeypatch, b"[]")

    page_id = find_page_by_slug(_BASE, _AUTH, "missing")

    assert page_id is None


def test_find_page_by_slug_raises_unavailable_error_when_body_is_not_an_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single object is not a pages list."""
    _install(monkeypatch, json.dumps({"id": 42}).encode("utf-8"))

    with pytest.raises(WordPressUnavailableError, match=re.escape("did not return a JSON array")):
        find_page_by_slug(_BASE, _AUTH, "about")


@pytest.mark.parametrize("page", [{"slug": "about"}, {"id": "42"}, ["about"]])
def test_find_page_by_slug_raises_unavailable_error_when_page_has_no_integer_id(
    monkeypatch: pytest.MonkeyPatch, page: object
) -> None:
    """A page whose id is missing or not a number cannot be updated."""
    _install(monkeypatch, json.dumps([page]).encode("utf-8"))

    with pytest.raises(WordPressUnavailableError, match=re.escape("without an integer id")):
        find_page_by_slug(_BASE, _AUTH, "about")


def test_publish_page_creates_a_page_when_no_page_id_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No page_id POSTs the full page body to the pages collection."""
    opener = _install(
        monkeypatch,
        json.dumps({"id": 7, "link": "https://example.test/results/", "status": "publish"}).encode(
            "utf-8"
        ),
    )

    page = publish_page(
        _BASE,
        _AUTH,
        title="Results",
        slug="results",
        content="<p>hi</p>",
        status="publish",
    )

    request = _only_call(opener).request
    assert page == PublishedPage(
        id=7, link="https://example.test/results/", status="publish", created=True
    )
    assert request.full_url == "https://example.test/wp-json/wp/v2/pages"
    assert request.get_method() == "POST"
    # urllib capitalizes only the first letter of a header name.
    assert request.get_header("Content-type") == "application/json; charset=utf-8"
    assert request.get_header("Authorization") == _AUTH_HEADER
    assert _json_body(request) == {
        "title": "Results",
        "slug": "results",
        "status": "publish",
        "parent": 0,
        "content": "<p>hi</p>",
    }


def test_publish_page_updates_the_page_when_page_id_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page_id POSTs the same body to that page, and says so."""
    opener = _install(
        monkeypatch,
        json.dumps({"id": 42, "link": "https://example.test/results/", "status": "draft"}).encode(
            "utf-8"
        ),
    )

    page = publish_page(
        _BASE,
        _AUTH,
        title="Results",
        slug="results",
        content="<p>hi</p>",
        status="draft",
        parent=9,
        page_id=42,
    )

    request = _only_call(opener).request
    assert page == PublishedPage(
        id=42, link="https://example.test/results/", status="draft", created=False
    )
    assert request.full_url == "https://example.test/wp-json/wp/v2/pages/42"
    assert request.get_method() == "POST"
    assert _json_body(request) == {
        "title": "Results",
        "slug": "results",
        "status": "draft",
        "parent": 9,
        "content": "<p>hi</p>",
    }


def test_publish_page_sends_the_content_as_a_bare_html_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content is HTML, never a JSON-encoded or escaped copy of it."""
    opener = _install(
        monkeypatch,
        json.dumps({"id": 5, "link": "https://example.test/results/", "status": "publish"}).encode(
            "utf-8"
        ),
    )
    content = '<div class="results"><p>1 &mdash; Ada</p></div>'

    publish_page(_BASE, _AUTH, title="Results", slug="results", content=content, status="publish")

    assert _json_body(_only_call(opener).request)["content"] == content


def test_publish_page_reports_the_status_the_site_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future-dated publish comes back as the site's own status."""
    _install(
        monkeypatch,
        json.dumps({"id": 5, "link": "https://example.test/results/", "status": "future"}).encode(
            "utf-8"
        ),
    )

    page = publish_page(
        _BASE,
        _AUTH,
        title="Results",
        slug="results",
        content="<p>hi</p>",
        status="publish",
    )

    assert page.status == "future"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"link": "https://example.test/x/", "status": "publish"}, "without an integer id"),
        ({"id": 1, "status": "publish"}, "without a 'link' field"),
        ({"id": 1, "link": "https://example.test/x/"}, "without a 'status' field"),
        ({"id": 1, "link": None, "status": "publish"}, "without a 'link' field"),
        ({"id": 1, "link": "https://example.test/x/", "status": 200}, "without a 'status' field"),
    ],
)
def test_publish_page_raises_unavailable_error_for_an_incomplete_response(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, object], expected: str
) -> None:
    """The saved page must name its id, link and status."""
    _install(monkeypatch, json.dumps(body).encode("utf-8"))

    with pytest.raises(WordPressUnavailableError, match=re.escape(expected)):
        publish_page(
            _BASE,
            _AUTH,
            title="Results",
            slug="results",
            content="<p>hi</p>",
            status="publish",
        )


def test_publish_page_raises_unavailable_error_when_response_is_not_a_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pages payload that is not an object names no saved page."""
    _install(monkeypatch, b'["nope"]')

    with pytest.raises(WordPressUnavailableError, match=re.escape("did not return a JSON object")):
        publish_page(
            _BASE,
            _AUTH,
            title="Results",
            slug="results",
            content="<p>hi</p>",
            status="publish",
        )


def test_publish_page_maps_http_403_to_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The update path converts failures the same way as every other."""
    _install(
        monkeypatch,
        error=_http_error(403, json.dumps(_WORDPRESS_ERROR_BODY).encode("utf-8")),
    )

    with pytest.raises(WordPressAuthError, match=re.escape("(HTTP 403)")) as excinfo:
        publish_page(
            _BASE,
            _AUTH,
            title="Results",
            slug="results",
            content="<p>hi</p>",
            status="publish",
            page_id=42,
        )

    assert str(excinfo.value) == (
        "WordPress rejected the credentials for "
        "https://example.test/wp-json/wp/v2/pages/42 (HTTP 403): "
        "rest_forbidden: Sorry, you are not allowed to do that."
    )
