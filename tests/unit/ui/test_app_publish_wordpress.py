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

Phase 4b adds the modal progress window: ``_publish_wordpress`` now
schedules the publish (through ``wx.CallAfter``) instead of starting it,
so the publish form's own ``EndModal`` runs first and the progress
dialog never stacks over a form that is still on screen. The progress
window itself is stubbed -- its own behaviour is
``test_publish_wordpress_wx.py``'s subject -- and the completion paths
are pinned here: success shows the native OK/Open question
(``std_dialogs.show_ok_open``, R-86) -- OK returns to the console and
Open hands the page's own link to ``webbrowser`` -- failure keeps the
Retry/Cancel flow (Retry re-shows the progress dialog over a fresh
attempt), and a cancel ends the modal, records it and shows no result
dialog at all.
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
from rivercrossing.ui.views import publish_wordpress as publish_wordpress_view

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_PAGE_LINK = "https://example.test/results/"
_FAILURE_MESSAGE = "boom"
_FAILURE_TITLE = "Publish Failed"
_SAVE_FAILURE = "settings file is read-only"
# The success confirmation's own caption, and the cancel record the
# progress dialog's Cancel path writes.
_PUBLISHED_TITLE = "Published"
_CANCELLED_MESSAGE = "Publish cancelled"


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
    monkeypatch: pytest.MonkeyPatch, *, fail_times: int | None, cancel_latest: bool = False
) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    """Swap ``_publish_page`` for a recorder that fails, then publishes.

    The first *fail_times* calls raise ``OSError(_FAILURE_MESSAGE)``;
    ``None`` fails every call. Any later call returns a page whose link
    is :data:`_PAGE_LINK`. *cancel_latest* marks the newest progress
    dialog cancelled as the request is entered, which is how a cancel
    that lands while the blocking HTTP call is in flight is modelled.
    """
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _publish(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        if cancel_latest:
            _RUNNINGS[-1].cancelled = True
        if fail_times is None or len(calls) <= fail_times:
            raise OSError(_FAILURE_MESSAGE)
        return SimpleNamespace(link=_PAGE_LINK)

    # logic-coverage-exempt: T-10 -- _publish_page is this worker's own
    # network boundary (the only frame that talks HTTP), so stubbing it
    # is the I/O seam, exactly like test_app_exports' _write_export.
    monkeypatch.setattr(app_module, "_publish_page", _publish)
    return calls


# ---------------------------------- the progress dialog (Phase 4b)

# The flow's thread and wx boundaries are already swapped by
# ``_drive_inline``; these add the progress window's own two seams.


class _StubWindow:
    """The loaded ``publish_running_dlg`` the app hands the view."""

    def __init__(self, parent: object) -> None:
        """Record the window the loader was parented to."""
        self.parent = parent


class _StubRunningDialog:
    """A ``PublishRunningDialog`` stand-in driven headlessly.

    ``run`` calls the view's own start seam -- the app wires that to the
    worker start -- which is exactly what the real ``run`` does inside
    the modal loop it opens; ``finish`` records the modal end.
    ``cancel_before_start`` marks the attempt abandoned before the
    worker's first checkpoint.
    """

    def __init__(
        self,
        dialog: object,
        *,
        on_start: Callable[[], None],
        cancel_before_start: bool = False,
    ) -> None:
        """Record the seams, starting un-cancelled and unfinished."""
        self.dialog = dialog
        self.on_start = on_start
        self.cancel_before_start = cancel_before_start
        self.cancelled = False
        self.finished = False
        self.statuses: list[str] = []
        self.pulses = 0
        _RUNNINGS.append(self)

    def is_cancelled(self) -> bool:
        """Return whether the operator has abandoned this attempt."""
        return self.cancelled

    def set_status(self, text: str) -> None:
        """Record one live status-line update."""
        self.statuses.append(text)

    def pulse(self) -> None:
        """Record one gauge pulse."""
        self.pulses += 1

    def run(self) -> None:
        """Run the start seam, as the real modal loop does."""
        if self.cancel_before_start:
            self.cancelled = True
        self.on_start()

    def finish(self) -> None:
        """Record the modal end."""
        self.finished = True


# Every progress-dialog double built since the last installer call.
_RUNNINGS: list[_StubRunningDialog] = []


def _stub_running_dialog(
    monkeypatch: pytest.MonkeyPatch, *, cancel_before_start: bool = False
) -> list[_StubRunningDialog]:
    """Swap the progress view and its window loader for doubles.

    logic-coverage-exempt: T-10 -- loading the XRC window and showing a
    real modal are the GUI boundary; the view's own behaviour is
    ``test_publish_wordpress_wx.py``'s subject.
    """
    _RUNNINGS.clear()

    def _build(dialog: object, *, on_start: Callable[[], None]) -> _StubRunningDialog:
        return _StubRunningDialog(
            dialog, on_start=on_start, cancel_before_start=cancel_before_start
        )

    monkeypatch.setattr(publish_wordpress_view, "load_publish_running_window", _StubWindow)
    monkeypatch.setattr(publish_wordpress_view, "PublishRunningDialog", _build)
    return _RUNNINGS


def _stub_show_ok_open(
    monkeypatch: pytest.MonkeyPatch, *, result: int = _ImmediateWx.ID_CANCEL
) -> list[tuple[object, ...]]:
    """Swap ``std_dialogs.show_ok_open`` for a recorder of its calls.

    *result* is the operator's answer: ``wx.ID_OK`` is Open, anything
    else is the OK/Escape dismiss. It defaults to the dismiss so no
    test launches a browser it did not ask for.

    logic-coverage-exempt: T-10 -- the native modal is the GUI
    boundary; the helper's own flags and labels are
    ``test_std_dialogs.py``'s subject.
    """
    calls: list[tuple[object, ...]] = []

    def _show(*args: object) -> int:
        calls.append(args)
        return result

    monkeypatch.setattr(std_dialogs, "show_ok_open", _show)
    return calls


def _stub_browser(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the URLs the publish completion hands to the browser.

    logic-coverage-exempt: T-10 -- ``webbrowser`` is the OS process
    boundary (it launches the operator's own browser), so it is the
    stub, not the SUT (the ``test_help.py`` seam).
    """
    opened: list[str] = []

    def _open(url: str, *_args: object, **_kwargs: object) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(app_module.webbrowser, "open", _open)
    return opened


def _stub_log_warn(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the always-on log warnings the publish path writes."""
    recorded: list[str] = []
    monkeypatch.setattr(
        app_module, "_log_warn", lambda _context, message: recorded.append(message)
    )
    return recorded


def _defer_wx(monkeypatch: pytest.MonkeyPatch) -> list[Callable[[], None]]:
    """Point ``require_wx`` at a seam that queues ``CallAfter`` calls.

    The publish form's OK calls ``on_publish`` and *then* ``EndModal``,
    so ``_publish_wordpress`` must schedule the progress dialog and the
    worker rather than start them: this seam records what was scheduled
    so a test can assert nothing ran while the form was still up.
    """
    pending: list[Callable[[], None]] = []

    class _DeferredWx:
        ID_OK = _ImmediateWx.ID_OK
        ID_CANCEL = _ImmediateWx.ID_CANCEL

        def CallAfter(  # noqa: N802 -- wx API name
            self, callable_: Callable[[], None]
        ) -> None:
            """Queue one deferred call instead of running it."""
            pending.append(callable_)

    monkeypatch.setattr(app_module, "require_wx", _DeferredWx)
    return pending


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


def test_run_publish_offloop_given_a_successful_publish_posts_the_link_and_confirms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-86: the success arm offers OK/Open and never retries."""
    context = _context()
    attempts = _stub_publish_page(monkeypatch, fail_times=0)
    retries = _stub_show_retry(monkeypatch, result=_ImmediateWx.ID_OK)
    confirmations = _stub_show_ok_open(monkeypatch)
    runnings = _stub_running_dialog(monkeypatch)
    _drive_inline(monkeypatch)

    _run(context)

    assert context.frame.notices == [f"Published to {_PAGE_LINK}"]
    assert confirmations == [(context.frame, _PUBLISHED_TITLE, f"Published to {_PAGE_LINK}")]
    assert (len(attempts), retries, runnings[0].finished) == (1, [], True)


def test_run_publish_offloop_given_the_open_choice_opens_the_published_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-86: Open hands the page's own link to the browser, verbatim."""
    context = _context()
    _stub_publish_page(monkeypatch, fail_times=0)
    confirmations = _stub_show_ok_open(monkeypatch, result=_ImmediateWx.ID_OK)
    opened = _stub_browser(monkeypatch)
    _stub_running_dialog(monkeypatch)
    _drive_inline(monkeypatch)

    _run(context)

    assert confirmations == [(context.frame, _PUBLISHED_TITLE, f"Published to {_PAGE_LINK}")]
    assert opened == [_PAGE_LINK]


def test_run_publish_offloop_given_the_ok_choice_leaves_the_browser_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-86: the OK dismiss returns to the console and opens nothing."""
    context = _context()
    _stub_publish_page(monkeypatch, fail_times=0)
    confirmations = _stub_show_ok_open(monkeypatch, result=_ImmediateWx.ID_CANCEL)
    opened = _stub_browser(monkeypatch)
    _stub_running_dialog(monkeypatch)
    _drive_inline(monkeypatch)

    _run(context)

    assert confirmations == [(context.frame, _PUBLISHED_TITLE, f"Published to {_PAGE_LINK}")]
    assert opened == []


def test_run_publish_offloop_given_an_attempt_updates_the_status_line_and_pulses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 4b: the worker feeds the live status line and the gauge."""
    context = _context()
    _stub_publish_page(monkeypatch, fail_times=0)
    _stub_show_ok_open(monkeypatch)
    runnings = _stub_running_dialog(monkeypatch)
    _drive_inline(monkeypatch)

    _run(context)

    assert (runnings[0].statuses, runnings[0].pulses) == ([app_module._PUBLISHING_STATUS], 1)


def test_run_publish_offloop_given_captured_inputs_forwards_them_to_the_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 4b: the worker renders with the captured publish inputs."""
    context = _context()
    form = _form()
    config = _StubConfig()
    opts = ExportOptions()
    logos = {"45": "data:image/png;base64,AA"}
    calls = _stub_publish_page(monkeypatch, fail_times=0)
    _stub_show_ok_open(monkeypatch)
    _stub_running_dialog(monkeypatch)
    _drive_inline(monkeypatch)

    app_module._run_publish_offloop(
        context,
        form,
        config=config,  # type: ignore[arg-type] -- the stubbed render ignores it
        teams=(),
        solo=(),
        opts=opts,
        riders=7,
        team_logos=logos,
        self_test_unverified=True,
    )

    assert calls == [
        (
            (form, config, (), opts),
            {"riders": 7, "team_logos": logos, "self_test_unverified": True},
        )
    ]


def test_run_publish_offloop_given_a_failure_and_retry_republishes_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Part B: Retry runs the same publish closure on a fresh worker."""
    context = _context()
    attempts = _stub_publish_page(monkeypatch, fail_times=1)
    retries = _stub_show_retry(monkeypatch, result=_ImmediateWx.ID_OK)
    _stub_show_ok_open(monkeypatch)
    _stub_running_dialog(monkeypatch)
    _drive_inline(monkeypatch)

    _run(context)

    assert len(attempts) == 2
    assert retries == [((context.frame, _FAILURE_TITLE, _FAILURE_MESSAGE), {})]
    assert context.frame.notices == [f"Published to {_PAGE_LINK}"]


def test_run_publish_offloop_given_a_failure_and_retry_reshows_the_progress_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 4b: Retry re-shows the progress window."""
    context = _context()
    _stub_publish_page(monkeypatch, fail_times=1)
    _stub_show_retry(monkeypatch, result=_ImmediateWx.ID_OK)
    confirmations = _stub_show_ok_open(monkeypatch)
    runnings = _stub_running_dialog(monkeypatch)
    _drive_inline(monkeypatch)

    _run(context)

    assert (len(runnings), runnings[0].finished, runnings[1].finished) == (2, True, True)
    assert confirmations == [(context.frame, _PUBLISHED_TITLE, f"Published to {_PAGE_LINK}")]
    assert runnings[0].dialog is not runnings[1].dialog


def test_run_publish_offloop_given_a_failure_and_cancel_leaves_the_page_unpublished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-3's other arm: Cancel never re-runs the publish."""
    context = _context()
    attempts = _stub_publish_page(monkeypatch, fail_times=None)
    retries = _stub_show_retry(monkeypatch, result=_ImmediateWx.ID_CANCEL)
    confirmations = _stub_show_ok_open(monkeypatch)
    runnings = _stub_running_dialog(monkeypatch)
    _drive_inline(monkeypatch)

    _run(context)

    assert len(attempts) == 1
    assert retries == [((context.frame, _FAILURE_TITLE, _FAILURE_MESSAGE), {})]
    assert (context.frame.notices, confirmations) == ([], [])
    assert runnings[0].finished is True


def test_run_publish_offloop_given_a_cancel_before_the_request_shows_no_result_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel before the first HTTP call: no result dialog at all."""
    context = _context()
    attempts = _stub_publish_page(monkeypatch, fail_times=0)
    retries = _stub_show_retry(monkeypatch, result=_ImmediateWx.ID_OK)
    confirmations = _stub_show_ok_open(monkeypatch)
    warnings = _stub_log_warn(monkeypatch)
    runnings = _stub_running_dialog(monkeypatch, cancel_before_start=True)
    _drive_inline(monkeypatch)

    _run(context)

    assert (attempts, retries, confirmations) == ([], [], [])
    assert (runnings[0].finished, warnings) == (True, [_CANCELLED_MESSAGE])


def test_run_publish_offloop_given_a_cancel_during_a_successful_request_suppresses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel that lands mid-request wins over the late success."""
    context = _context()
    attempts = _stub_publish_page(monkeypatch, fail_times=0, cancel_latest=True)
    retries = _stub_show_retry(monkeypatch, result=_ImmediateWx.ID_OK)
    confirmations = _stub_show_ok_open(monkeypatch)
    warnings = _stub_log_warn(monkeypatch)
    runnings = _stub_running_dialog(monkeypatch)
    _drive_inline(monkeypatch)

    _run(context)

    assert (len(attempts), retries, confirmations, context.frame.notices) == (1, [], [], [])
    assert (runnings[0].finished, warnings) == (True, [_CANCELLED_MESSAGE])


def test_run_publish_offloop_given_a_cancel_during_a_failing_request_suppresses_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel that lands mid-request wins over the failure's Retry."""
    context = _context()
    attempts = _stub_publish_page(monkeypatch, fail_times=None, cancel_latest=True)
    retries = _stub_show_retry(monkeypatch, result=_ImmediateWx.ID_OK)
    confirmations = _stub_show_ok_open(monkeypatch)
    warnings = _stub_log_warn(monkeypatch)
    runnings = _stub_running_dialog(monkeypatch)
    _drive_inline(monkeypatch)

    _run(context)

    assert (len(attempts), retries, confirmations) == (1, [], [])
    assert (runnings[0].finished, warnings) == (True, [_CANCELLED_MESSAGE])


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
    _drive_inline(monkeypatch)

    app_module._publish_wordpress(context, replace(_form(), kind="podium"))

    assert context.settings.wp_kind == "podium"
    assert settings_store.load_settings(settings_path).wp_kind == "podium"


def test_publish_wordpress_given_no_engine_notices_and_starts_no_publish(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T-3's other arm: with no ride threaded nothing is published."""
    context = _publish_context(tmp_path / "settings.json", engine=None)
    calls = _stub_offloop(monkeypatch)
    _drive_inline(monkeypatch)

    app_module._publish_wordpress(context, _form())

    assert calls == []
    assert context.frame.notices == ["No ride to publish"]


def test_publish_wordpress_given_a_refused_settings_write_still_publishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A refused write costs the persistence, never the publish."""
    context = _publish_context(tmp_path / "settings.json", _StubEngine())
    calls = _stub_offloop(monkeypatch)
    _drive_inline(monkeypatch)

    def _refuse(_settings: object, _path: object = None) -> None:
        raise OSError(_SAVE_FAILURE)

    # logic-coverage-exempt: T-10 -- settings_store.save_settings is the
    # settings-file I/O boundary; a refused write is this arm's subject.
    monkeypatch.setattr(settings_store, "save_settings", _refuse)

    app_module._publish_wordpress(context, replace(_form(), kind="podium"))

    assert context.frame.notices == [f"Could not save settings: {_SAVE_FAILURE}"]
    assert context.settings.wp_kind == "podium"
    assert len(calls) == 1


def test_publish_wordpress_given_a_ride_schedules_the_publish_after_the_form_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Phase 4b: nothing starts while the publish form is still up.

    The form's OK calls ``on_publish`` and then ``EndModal``, so the
    progress dialog and the worker must be scheduled: only running the
    deferred call starts them, which is what happens after the form is
    gone.
    """
    context = _publish_context(tmp_path / "settings.json", _StubEngine())
    form = replace(_form(), kind="podium")
    calls = _stub_offloop(monkeypatch)
    pending = _defer_wx(monkeypatch)

    app_module._publish_wordpress(context, form)

    assert (calls, len(pending)) == ([], 1)
    pending[0]()
    assert len(calls) == 1
    assert (calls[0][0], calls[0][1]) == (context, form)


def test_publish_wordpress_given_a_ride_parents_the_progress_dialog_to_the_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The progress window stacks over the console, not over nothing."""
    context = _publish_context(tmp_path / "settings.json", _StubEngine())
    _stub_publish_page(monkeypatch, fail_times=0)
    _stub_show_ok_open(monkeypatch)
    runnings = _stub_running_dialog(monkeypatch)
    _drive_inline(monkeypatch)

    app_module._publish_wordpress(context, _form())

    assert len(runnings) == 1
    assert runnings[0].dialog.parent is context.frame
