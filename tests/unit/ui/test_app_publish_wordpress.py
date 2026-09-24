# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the publish worker's Retry/Cancel path (Part B).

``app._run_publish_offloop`` renders and publishes on a background
thread. A failure now asks Retry/Cancel on the main thread instead of
showing an OK-only alert, and Retry starts a fresh worker thread
running the same ``publish`` closure with the already-captured publish
inputs. The worker thread, ``wx.CallAfter`` and the native dialog are
all GUI/thread boundaries (T-10), so this module swaps each for an
inline or recording double -- the ``test_app_exports`` seam -- and
never constructs a wx window or a real thread.

``_ask_retry`` compares the dialog's result against
``require_wx().ID_OK``, so the fake wx seam carries both stock ids as
well as running ``CallAfter`` inline.

Part C adds the publish kind's two ends: ``_publish_page``'s branch
(the podium kind renders the poster fragment, the full kind the results
page) and ``_publish_wordpress``'s persistence of ``wp_kind`` beside
the other site fields -- driven through the real function over a real
settings file, with only the thread boundary stubbed.
"""

from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from rivercrossing.htmlexport import ExportOptions
from rivercrossing.ride import RideStatus
from rivercrossing.roster import Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import std_dialogs
from rivercrossing.ui.presenters import settings as settings_store
from rivercrossing.ui.presenters.publish_wordpress import PublishForm

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_PAGE_LINK = "https://example.test/results/"
_FAILURE_MESSAGE = "boom"
_FAILURE_TITLE = "Publish Failed"
_SAVE_FAILURE = "settings file is read-only"


class _InlineThread:
    """Run a worker target inline, for deterministic off-loop tests."""

    def __init__(self, *, target: Callable[[], None], **_wx_kwargs: object) -> None:
        """Hold *target*; wx's extra kwargs (daemon) are accepted."""
        self._target = target

    def start(self) -> None:
        """Run the worker body now, on the calling thread."""
        self._target()


class _ImmediateWx:
    """A wx seam that runs ``CallAfter`` inline and carries both ids.

    ``_ask_retry`` reads ``require_wx().ID_OK`` to decide whether the
    operator chose Retry, so the seam exposes the two stock ids the
    dialog's result is compared against.
    """

    ID_OK = 5100
    ID_CANCEL = 5101

    def CallAfter(  # noqa: N802 -- wx API name
        self, callable_: Callable[..., None], *args: object
    ) -> None:
        """Run one deferred call immediately, on the calling thread."""
        callable_(*args)


class _FakeFrame:
    """Record status-bar notices; no wx window ever exists."""

    def __init__(self) -> None:
        """Start with an empty notice log."""
        self.notices: list[str] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record one status-bar notice."""
        self.notices.append(text)


class _StubConfig:
    """A ride-like config; the publish worker only forwards it."""

    name = "Test Poker Run"
    # The render inputs _publish_page reads off the config: the export
    # ranking's tie-break order and the ride's own logo.
    tiebreak_order = ("laps", "total_time", "high_card")
    logo_path: Path | None = None


class _StubEngine:
    """The engine surface a publish reads on the main thread."""

    def __init__(self) -> None:
        """Start FINISHED with no crossings and a clean self-test."""
        self.config = _StubConfig()
        self.state = RideStatus.FINISHED
        self.self_test_unverified = False
        self.stopped = False

    def snapshot(self) -> tuple[object, ...]:
        """Return no results: this fixture publishes an empty field."""
        return ()


def _context() -> app_module._RouteContext:
    """Build a route context with a fake frame and no app log."""
    return app_module._RouteContext(
        frame=_FakeFrame(),
        resource=None,
        roster=None,  # type: ignore[arg-type] -- this path never touches it
        app=None,
        theme_controller=None,
    )


def _form() -> PublishForm:
    """Return the valid publish form the worker publishes from."""
    return PublishForm(
        base_url="https://example.test",
        username="operator",
        password="example-value",  # noqa: S106 -- a fixture value, not a credential
        title="GORBA EPIC 2026 — Results",
        slug="gorba-epic-2026-results",
        parent="",
        status="draft",
    )


def _drive_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the real worker inline (thread + wx seams)."""
    monkeypatch.setattr(app_module, "require_wx", _ImmediateWx)
    monkeypatch.setattr(app_module, "threading", SimpleNamespace(Thread=_InlineThread))


def _stub_publish_page(
    monkeypatch: pytest.MonkeyPatch, *, fail_times: int | None
) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    """Swap ``_publish_page`` for a recorder that fails, then publishes.

    The first *fail_times* calls raise ``OSError(_FAILURE_MESSAGE)``;
    ``None`` fails every call. Any later call returns a page whose link
    is :data:`_PAGE_LINK`.
    """
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _publish(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        if fail_times is None or len(calls) <= fail_times:
            raise OSError(_FAILURE_MESSAGE)
        return SimpleNamespace(link=_PAGE_LINK)

    # logic-coverage-exempt: T-10 -- _publish_page is this worker's own
    # network boundary (the only frame that talks HTTP), so stubbing it
    # is the I/O seam, exactly like test_app_exports' _write_export.
    monkeypatch.setattr(app_module, "_publish_page", _publish)
    return calls


def _stub_show_retry(
    monkeypatch: pytest.MonkeyPatch, *, result: int
) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    """Swap ``std_dialogs.show_retry`` for a recording double.

    Every ask is recorded; *result* is what the operator's click
    answers.
    """
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _show(*args: object, **kwargs: object) -> int:
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(std_dialogs, "show_retry", _show)
    return calls


def _run(context: app_module._RouteContext) -> None:
    """Call the publish worker with the stub publish inputs."""
    app_module._run_publish_offloop(
        context,
        _form(),
        config=_StubConfig(),  # type: ignore[arg-type] -- the stubbed worker never reads it
        teams=(),
        solo=(),
        opts=ExportOptions(),
    )


def test_run_publish_offloop_given_a_successful_publish_posts_the_page_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The success arm notices the link and never asks to retry."""
    context = _context()
    attempts = _stub_publish_page(monkeypatch, fail_times=0)
    retries = _stub_show_retry(monkeypatch, result=_ImmediateWx.ID_OK)
    _drive_inline(monkeypatch)

    _run(context)

    assert context.frame.notices == [f"Published to {_PAGE_LINK}"]
    assert len(attempts) == 1
    assert retries == []


def test_run_publish_offloop_given_a_failure_and_retry_republishes_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Part B: Retry runs the same publish closure on a fresh worker."""
    context = _context()
    attempts = _stub_publish_page(monkeypatch, fail_times=1)
    retries = _stub_show_retry(monkeypatch, result=_ImmediateWx.ID_OK)
    _drive_inline(monkeypatch)

    _run(context)

    assert len(attempts) == 2
    assert retries == [((context.frame, _FAILURE_TITLE, _FAILURE_MESSAGE), {})]
    assert context.frame.notices == [f"Published to {_PAGE_LINK}"]


def test_run_publish_offloop_given_a_failure_and_cancel_leaves_the_page_unpublished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3's other arm: Cancel never re-runs the publish."""
    context = _context()
    attempts = _stub_publish_page(monkeypatch, fail_times=None)
    retries = _stub_show_retry(monkeypatch, result=_ImmediateWx.ID_CANCEL)
    _drive_inline(monkeypatch)

    _run(context)

    assert len(attempts) == 1
    assert retries == [((context.frame, _FAILURE_TITLE, _FAILURE_MESSAGE), {})]
    assert context.frame.notices == []


# ------------------------------------- the publish kind's render branch

# The two renderers _publish_page may reach, as the kind selects them.
_RENDERER_NAMES = ("render_wordpress", "render_poster_wordpress")

# The full kind's own renderer call: the results page, team logos
# included. The podium kind's poster renderer takes no team_logos (the
# poster draws no per-team marks), which is why the two kinds' kwarg
# sets differ -- pinned below.
_FULL_RENDER_KWARGS: dict[str, object] = {
    "logo_path": None,
    "team_logos": None,
    "self_test_unverified": False,
    "riders": 0,
}
_PODIUM_RENDER_KWARGS: dict[str, object] = {
    "logo_path": None,
    "self_test_unverified": False,
    "riders": 0,
}

# (kind, the renderer it must reach, that renderer's own kwargs). The
# third row is the boundary: the branch is the podium kind alone, so
# any other value keeps publishing the full results page.
RENDER_CASES = (
    ("full", "render_wordpress", _FULL_RENDER_KWARGS),
    ("podium", "render_poster_wordpress", _PODIUM_RENDER_KWARGS),
    ("mystery", "render_wordpress", _FULL_RENDER_KWARGS),
)


def _stub_publish_renderers(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, object]]]:
    """Swap both publish renderers for recorders of their calls.

    Each records ``(its own name, its keyword arguments)`` and returns a
    marker string, so the test can see which renderer the kind reached
    and with what.

    logic-coverage-exempt: T-10 -- the tested branch *is* which renderer
    runs, and each renderer's own output has its own test module
    (test_htmlexport_wordpress), so rendering a real fragment here would
    only hide the branch.
    """
    calls: list[tuple[str, dict[str, object]]] = []

    def _recorder(name: str) -> Callable[..., str]:
        def _render(*_args: object, **kwargs: object) -> str:
            calls.append((name, kwargs))
            return f'<div class="rc-results">{name}</div>'

        return _render

    for name in _RENDERER_NAMES:
        monkeypatch.setattr(app_module.htmlexport, name, _recorder(name))
    return calls


def _stub_wordpress_publish(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Swap the site's two lookups/POSTs for headless recorders.

    ``publish_page`` records the content it is handed; the slug lookup
    answers "no page yet", so the publish takes the create arm.

    logic-coverage-exempt: T-10 -- ``wordpress.publish_page`` and
    ``wordpress.find_page_by_slug`` are the module's own HTTP boundary;
    no test here speaks HTTP.
    """
    published: list[str] = []

    def _publish(_base_url: str, _auth: object, **kwargs: object) -> object:
        published.append(str(kwargs["content"]))
        return SimpleNamespace(link=_PAGE_LINK)

    monkeypatch.setattr(app_module.wordpress, "publish_page", _publish)
    monkeypatch.setattr(app_module.wordpress, "find_page_by_slug", lambda *_args, **_kwargs: None)
    return published


@pytest.mark.parametrize(("kind", "renderer", "render_kwargs"), RENDER_CASES)
def test_publish_page_given_a_kind_renders_through_that_kinds_renderer(  # noqa: PLR0913, PLR0917
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    renderer: str,
    render_kwargs: dict[str, object],
) -> None:
    """Part C: only the podium kind takes the poster fragment path."""
    renders = _stub_publish_renderers(monkeypatch)
    published = _stub_wordpress_publish(monkeypatch)

    app_module._publish_page(replace(_form(), kind=kind), _StubConfig(), (), ExportOptions())

    assert renders == [(renderer, render_kwargs)]
    assert published == [f'<div class="rc-results">{renderer}</div>']


# --------------------------- the publish kind's persistence (Part C)


def _publish_context(settings_path: Path, engine: object) -> app_module._RouteContext:
    """Build a route context whose presenter carries *engine*."""
    return app_module._RouteContext(
        frame=_FakeFrame(),
        resource=None,
        roster=Roster(),
        app=None,
        theme_controller=None,
        presenter=SimpleNamespace(engine=engine),  # type: ignore[arg-type] -- a stub presenter
        settings_path=settings_path,
    )


def _stub_offloop(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[object, object, dict[str, object]]]:
    """Record the captured publish inputs instead of starting a thread.

    logic-coverage-exempt: T-10 -- ``_run_publish_offloop`` is the
    thread boundary (a real ``Thread`` in a headless test), and its own
    behaviour is ``test_run_publish_offloop_*``'s subject above.
    """
    calls: list[tuple[object, object, dict[str, object]]] = []

    def _run(context: object, form: object, **kwargs: object) -> None:
        calls.append((context, form, kwargs))

    monkeypatch.setattr(app_module, "_run_publish_offloop", _run)
    return calls


def test_publish_wordpress_given_a_podium_form_persists_the_kind_with_the_site_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Part C: the chosen kind persists beside the site fields."""
    settings_path = tmp_path / "settings.json"
    context = _publish_context(settings_path, _StubEngine())
    _stub_offloop(monkeypatch)

    app_module._publish_wordpress(context, replace(_form(), kind="podium"))

    assert context.settings.wp_kind == "podium"
    assert settings_store.load_settings(settings_path).wp_kind == "podium"


def test_publish_wordpress_given_no_engine_notices_and_starts_no_publish(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T-3's other arm: with no ride threaded nothing is published."""
    context = _publish_context(tmp_path / "settings.json", engine=None)
    calls = _stub_offloop(monkeypatch)

    app_module._publish_wordpress(context, _form())

    assert calls == []
    assert context.frame.notices == ["No ride to publish"]


def test_publish_wordpress_given_a_refused_settings_write_still_publishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A refused write costs the persistence, never the publish."""
    context = _publish_context(tmp_path / "settings.json", _StubEngine())
    calls = _stub_offloop(monkeypatch)

    def _refuse(_settings: object, _path: object = None) -> None:
        raise OSError(_SAVE_FAILURE)

    # logic-coverage-exempt: T-10 -- settings_store.save_settings is the
    # settings-file I/O boundary; a refused write is this arm's subject.
    monkeypatch.setattr(settings_store, "save_settings", _refuse)

    app_module._publish_wordpress(context, replace(_form(), kind="podium"))

    assert context.frame.notices == [f"Could not save settings: {_SAVE_FAILURE}"]
    assert context.settings.wp_kind == "podium"
    assert len(calls) == 1
