# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the self-test presenter (E2.4.1) -- tests first, R-70.

``SelfTestPresenter`` drives ``selftest_dlg`` (3f); unlike the other
presenters in ``ui.presenters`` it takes no ``DataSource`` -- its
whole job is to run an injected ``self_test`` callable and render its
``SelfTestReport``, so ``tests/unit/presenters/test_protocols.py``'s
shared ``(view, data_source)`` harness does not fit it (it is still
covered by that suite's "no wx" import probe, see
``_PRESENTER_MODULES`` there).
"""

import pytest

from rivercrossing.hands import SelfTestCheck, SelfTestReport
from rivercrossing.ui.presenters.selftest import (
    SelfTestPresenter,
    SelfTestView,
    format_check_line,
    format_report,
)


class FakeSelfTestView:
    """A complete SelfTestView fake, recording every call."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def show_lines(self, lines: tuple[str, ...]) -> None:
        """Record the rendered lines."""
        self.calls.append(("show_lines", (lines,)))

    def set_rerun_busy(self, *, busy: bool) -> None:
        """Record the rerun busy/enabled state."""
        self.calls.append(("set_rerun_busy", (busy,)))


def _report(*, passed: bool, name: str = "check") -> SelfTestReport:
    """Build a one-check report, all-pass or all-fail."""
    check = SelfTestCheck(name=name, passed=passed, duration_seconds=0.0, detail="")
    return SelfTestReport(checks=(check,))


# ------------------------------------------- protocol conformance


def test_fake_self_test_view_satisfies_the_self_test_view_protocol() -> None:
    """A complete FakeSelfTestView satisfies SelfTestView."""
    assert isinstance(FakeSelfTestView(), SelfTestView)


# ---------------------------------------------- format_check_line


@pytest.mark.parametrize(
    ("name", "detail", "expected"),
    [
        ("7,462 distinct ranks", "", "7,462 distinct ranks ........ PASS"),
        ("Joker vector table (28)", "", "Joker vector table (28) ..... PASS"),
        ("Five-of-a-kind ordering", "", "Five-of-a-kind ordering ..... PASS"),
        (
            "Whole-field 180×12 timing",  # noqa: RUF001 -- xrc-windows.md's frozen text
            "0.31 s",
            "Whole-field 180×12 timing ... 0.31 s PASS",  # noqa: RUF001
        ),
    ],
    ids=["rank_sweep", "joker_vectors", "five_of_a_kind", "field_timing"],
)
def test_format_check_line_given_each_canvas_check_matches_its_frozen_line(
    name: str, detail: str, expected: str
) -> None:
    """Every canvas line renders character-for-character."""
    check = SelfTestCheck(name=name, passed=True, duration_seconds=0.0, detail=detail)

    assert format_check_line(check) == expected


def test_format_check_line_when_failed_renders_the_fail_suffix() -> None:
    """A failed check's line ends FAIL, not PASS."""
    check = SelfTestCheck(
        name="7,462 distinct ranks", passed=False, duration_seconds=0.0, detail=""
    )

    assert format_check_line(check) == "7,462 distinct ranks ........ FAIL"


def test_format_report_renders_one_line_per_check_in_order() -> None:
    """format_report renders every check, in the report's own order."""
    report = SelfTestReport(
        checks=(
            SelfTestCheck(name="a", passed=True, duration_seconds=0.0, detail=""),
            SelfTestCheck(name="b", passed=False, duration_seconds=0.0, detail=""),
        )
    )

    assert format_report(report) == (
        format_check_line(report.checks[0]),
        format_check_line(report.checks[1]),
    )


# ------------------------------------------------- SelfTestPresenter


def test_self_test_presenter_holds_the_view_and_self_test_callable_given() -> None:
    """The presenter stores the exact view/self_test callable given."""
    view = FakeSelfTestView()

    def fake_self_test() -> SelfTestReport:
        return _report(passed=True)

    presenter = SelfTestPresenter(view, fake_self_test)

    assert presenter.view is view
    assert presenter.self_test is fake_self_test


def test_self_test_presenter_init_runs_once_and_renders_a_passing_report() -> None:
    """Construction runs the suite once and shows its lines."""
    view = FakeSelfTestView()
    report = _report(passed=True)

    presenter = SelfTestPresenter(view, lambda: report)

    assert presenter.report is report
    assert view.calls == [
        ("set_rerun_busy", (True,)),
        ("show_lines", (format_report(report),)),
        ("set_rerun_busy", (False,)),
    ]


def test_self_test_presenter_init_with_a_failing_report_renders_fail_lines() -> None:
    """A red report renders FAIL lines as faithfully as a green one."""
    view = FakeSelfTestView()
    failing_report = _report(passed=False)

    presenter = SelfTestPresenter(view, lambda: failing_report)

    assert presenter.report.passed is False
    assert view.calls[1] == ("show_lines", (format_report(failing_report),))


def test_self_test_presenter_on_rerun_runs_again_and_re_renders() -> None:
    """rerun_btn's handler runs the suite again, re-rendering."""
    view = FakeSelfTestView()
    reports = iter([_report(passed=False), _report(passed=True)])
    presenter = SelfTestPresenter(view, lambda: next(reports))
    view.calls.clear()

    presenter.on_rerun()

    assert presenter.report.passed is True
    assert view.calls == [
        ("set_rerun_busy", (True,)),
        ("show_lines", (format_report(_report(passed=True)),)),
        ("set_rerun_busy", (False,)),
    ]
