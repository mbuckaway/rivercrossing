# SPDX-License-Identifier: GPL-3.0-only
"""Self-test presenter -- selftest_dlg (3f), the evaluator self-test.

Pure Python -- no ``wx`` import may ever land here (R-71). Unlike
every other presenter in this package, this one takes no
``DataSource``: ``rivercrossing.hands.self_test`` (the frozen R-44
hook, module-skeletons.md S4) is injected directly as the collaborator
it drives, since the whole point of E2.4.1's dialog is to run that
real suite, not to read display data through the demo/live seam.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.hands import SelfTestCheck, SelfTestReport

__all__ = ["SelfTestPresenter", "SelfTestView", "format_check_line", "format_report"]

# The selftest_dlg canvas's own dotted-line column (xrc-windows.md
# section E): every check's name, space-padded with dots, lines up
# its PASS/FAIL in the same column regardless of the name's length.
_DOT_COLUMN = 29


@runtime_checkable
class SelfTestView(Protocol):
    """View surface for the self-test dialog (selftest_dlg, 3f)."""

    def show_lines(self, lines: tuple[str, ...]) -> None:
        """Replace ``selftest_output`` with *lines*, one per check."""
        ...

    def set_rerun_busy(self, *, busy: bool) -> None:
        """Disable ``rerun_btn`` mid-run; re-enable it once done."""
        ...


def format_check_line(check: SelfTestCheck) -> str:
    """Render one check as the canvas's dotted PASS/FAIL line.

    The name is padded with dots to :data:`_DOT_COLUMN`, then a
    space, then the check's own detail (only the whole-field timing
    check has one) and PASS/FAIL -- the exact shape xrc-windows.md's
    four selftest_dlg lines show, verified character-for-character in
    the presenter's own unit tests.
    """
    label = f"{check.name} ".ljust(_DOT_COLUMN, ".")
    detail = f"{check.detail} " if check.detail else ""
    status = "PASS" if check.passed else "FAIL"
    return f"{label} {detail}{status}"


def format_report(report: SelfTestReport) -> tuple[str, ...]:
    """Render every check in *report* as its own dotted canvas line."""
    return tuple(format_check_line(check) for check in report.checks)


class SelfTestPresenter:
    """Drives selftest_dlg: runs the real suite, renders it, reruns it.

    Runs *self_test* once at construction (so opening the dialog
    always shows a fresh result) and again on :meth:`on_rerun`
    (``rerun_btn``) -- both times through the same :meth:`run`, so the
    view is driven identically either way.
    """

    def __init__(self, view: SelfTestView, self_test: Callable[[], SelfTestReport]) -> None:
        """Store the view and the injected ``self_test`` callable; run.

        Args:
            view: The passive :class:`SelfTestView` this presenter
                drives -- a real wx dialog, or a fake in tests.
            self_test: The evaluator self-test hook to run --
                ``rivercrossing.hands.self_test`` in production, a
                stand-in report factory in tests.
        """
        self.view = view
        self.self_test = self_test
        self.report = self.run()

    def run(self) -> SelfTestReport:
        """Run the suite once, render it, and return the report.

        ``rerun_btn`` stays disabled until the freshly-rendered lines
        are already on screen, not merely once the suite has
        returned -- re-enabling it first would let a fast double-click
        fire a second run against the still-stale output.
        """
        self.view.set_rerun_busy(busy=True)
        try:
            report = self.self_test()
            self.view.show_lines(format_report(report))
        finally:
            self.view.set_rerun_busy(busy=False)
        return report

    def on_rerun(self) -> None:
        """Handle ``rerun_btn``: run the suite again and re-render."""
        self.report = self.run()
