# SPDX-License-Identifier: GPL-3.0-only
"""Gate host (non-VM) functional-test runs on macOS.

Constructing wx windows on a host desktop can abort the interpreter
on a wxWidgets C++ assertion and, per noxfile.py's ``functional``
session, degrades after enough window construction even when it
doesn't -- both hazards a real desktop session is exposed to that a
disposable VM is not. scripts/run_functional_tests_vm.sh (test_vm_
scripts.py) runs the suite inside a Tart VM instead, so a bare host
run on darwin is refused unless CI already isolates it or an operator
explicitly opts in.
"""

from collections.abc import Mapping  # noqa: TC003 -- dev tooling, not a hot import path

_VM_SCRIPT = "scripts/run_functional_tests_vm.sh"
_OPT_OUT_VAR = "RIVERCROSSING_HOST_FUNCTIONAL"


def host_functional_run_allowed(platform: str, environ: Mapping[str, str]) -> tuple[bool, str]:
    """Decide whether a host (non-VM) functional run may proceed.

    Off darwin there is no wx-on-host-desktop risk, so every platform
    is allowed. On darwin, a non-empty ``CI`` means the run is
    already isolated (a CI runner, not a developer's desktop), and
    ``RIVERCROSSING_HOST_FUNCTIONAL=1`` records an operator's explicit
    opt-out; anything else is refused, naming the VM script instead.

    Returns:
        ``(True, "")`` when the run may proceed, or ``(False,
        <reason>)`` naming the VM script and the opt-out variable.
    """
    if platform != "darwin":
        return True, ""
    if environ.get("CI"):
        return True, ""
    if environ.get(_OPT_OUT_VAR) == "1":
        return True, ""
    return (
        False,
        (
            f"host functional runs are disabled on macOS outside CI -- "
            f"run {_VM_SCRIPT} instead, or set {_OPT_OUT_VAR}=1 to override"
        ),
    )
