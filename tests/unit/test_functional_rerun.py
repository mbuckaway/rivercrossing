# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for tools/functional_rerun.py (fresh-process rerun).

RED phase for Phase 4's process-level functional-suite rerun:
``tools/functional_rerun.py`` does not exist yet. The wrapper re-runs
the FAILED/ERROR *files* from a pytest ``-ra`` summary in freshly
spawned pytest processes, because the wx/SIP wrapper-cache corruption
the functional suite hits is process-granular and pytest's own
``--reruns`` re-runs inside the same poisoned worker
(docs/EPIC3-SESSION-SUMMARY.md, Addendum 2).

``tools/`` carries no ``__init__.py`` (module-skeletons.md: it is dev
tooling), so it is only importable as an implicit PEP 420 namespace
package once the repo root is on ``sys.path`` -- the same insertion
test_functional_gate.py makes, and for the same reason the import is
deferred into a fixture: with the import at module level, a missing
tools/functional_rerun.py would abort *collection* for the whole
tests/unit session. Deferring it confines this module's RED state to
its own tests.

Orchestration is unit-tested through a seam: ``rerun_failed_files``
takes a ``runner`` callable that returns a ``CompletedProcess``, so
the exit-code decisions (green -> 0, crash propagated, failed-then-
green, failed-then-failed, whole-suite fallback) are pinned without
ever spawning pytest.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The exact functional invocation the wrapper wraps (mirrors
# scripts/run_functional_tests_vm.sh, noxfile.py and ci.yml).
_CMD = [
    "pytest",
    "tests/functional",
    "-v",
    "--no-cov",
    "-n",
    "auto",
    "--dist",
    "loadfile",
    "--reruns",
    "2",
]
# The wrapper normalises a leading ``pytest`` token to the interpreter
# that launched it, so a fresh process never depends on bare ``pytest``
# being on PATH (the guest VM's ssh session has no .venv/bin on PATH).
_PYTEST = [sys.executable, "-m", "pytest"]
_PASS1 = _PYTEST + _CMD[1:]
_FLAGS = _CMD[2:]


@pytest.fixture(scope="module")
def rerun_module() -> ModuleType:
    """Return tools.functional_rerun, imported lazily."""
    from tools import functional_rerun  # type: ignore[import-not-found]  # noqa: PLC0415

    return functional_rerun


# ------------------------------------------------- parse_summary


def test_parse_summary_when_clean_returns_empty_set(rerun_module: ModuleType) -> None:
    """A clean summary maps to no files."""
    text = "800 passed in 812.34s\n"

    result = rerun_module.parse_summary(text)

    assert result == set()


def test_parse_summary_when_failed_lines_returns_distinct_files(
    rerun_module: ModuleType,
) -> None:
    """FAILED node ids map to their files, deduplicated."""
    text = (
        "FAILED tests/functional/test_roster_editor.py::test_save"
        " - AssertionError: boom\n"
        "FAILED tests/functional/test_roster_editor.py::test_load"
        " - KeyError: 'plates'\n"
        "FAILED tests/functional/test_csvio.py::test_import - ValueError\n"
    )

    result = rerun_module.parse_summary(text)

    assert result == {
        "tests/functional/test_roster_editor.py",
        "tests/functional/test_csvio.py",
    }


def test_parse_summary_when_error_lines_returns_distinct_files(
    rerun_module: ModuleType,
) -> None:
    """Fixture-setup failures surface as ERROR lines."""
    text = (
        "ERROR tests/functional/test_roster_editor.py::test_save - LookupError\n"
        "ERROR tests/functional/test_csvio.py::test_import - LookupError\n"
    )

    result = rerun_module.parse_summary(text)

    assert result == {
        "tests/functional/test_roster_editor.py",
        "tests/functional/test_csvio.py",
    }


def test_parse_summary_when_mixed_statuses_returns_union_of_files(
    rerun_module: ModuleType,
) -> None:
    """FAILED and ERROR lines share one file set."""
    text = (
        "FAILED tests/functional/test_roster_editor.py::test_save"
        " - AssertionError\n"
        "ERROR tests/functional/test_csvio.py::test_import - LookupError\n"
    )

    result = rerun_module.parse_summary(text)

    assert result == {
        "tests/functional/test_roster_editor.py",
        "tests/functional/test_csvio.py",
    }


def test_parse_summary_when_ansi_codes_present_returns_same_files(
    rerun_module: ModuleType,
) -> None:
    """The guest runs with colour; ANSI codes must not break parsing."""
    text = (
        "\x1b[31mFAILED\x1b[0m \x1b[1mtests/functional/test_csvio.py"
        "::test_import\x1b[0m - ValueError\n"
        "\x1b[31mERROR\x1b[0m tests/functional/test_roster_editor.py"
        "::test_save - LookupError\n"
    )

    result = rerun_module.parse_summary(text)

    assert result == {
        "tests/functional/test_csvio.py",
        "tests/functional/test_roster_editor.py",
    }


def test_parse_summary_when_unparseable_lines_ignores_them(
    rerun_module: ModuleType,
) -> None:
    """Header/status lines never produce phantom files."""
    text = (
        "=============================== test session starts"
        " ================================\n"
        "platform darwin -- Python 3.14.0\n"
        "======================= short test summary info"
        " =======================\n"
        "PASSED tests/functional/test_csvio.py::test_import\n"
        "SKIPPED [2] tests/functional/test_roster_editor.py: slow\n"
        "FAILED tests/functional/test_csvio.py::test_import - ValueError\n"
        "WARNINGS\n"
        "==================== 1 failed, 799 passed in 812.34s"
        " ====================\n"
    )

    result = rerun_module.parse_summary(text)

    assert result == {"tests/functional/test_csvio.py"}


def test_parse_summary_when_parametrized_node_id_returns_file(
    rerun_module: ModuleType,
) -> None:
    """Parametrized ids (``::test_y[case]``) map to the owning file."""
    text = "FAILED tests/functional/test_csvio.py::test_import[utf-8] - ValueError\n"

    result = rerun_module.parse_summary(text)

    assert result == {"tests/functional/test_csvio.py"}


def test_parse_summary_when_class_scoped_node_id_returns_file(
    rerun_module: ModuleType,
) -> None:
    """Class-scoped ids (``::TestX::test_y``) map to the owning file."""
    text = "FAILED tests/functional/test_csvio.py::TestImport::test_roundtrip - ValueError\n"

    result = rerun_module.parse_summary(text)

    assert result == {"tests/functional/test_csvio.py"}


def test_parse_summary_when_plain_file_node_id_returns_file(
    rerun_module: ModuleType,
) -> None:
    """A collection error names the bare file, not a node id."""
    text = "ERROR tests/functional/test_csvio.py - collection error\n"

    result = rerun_module.parse_summary(text)

    assert result == {"tests/functional/test_csvio.py"}


def test_parse_summary_when_duplicate_files_returns_deduplicated_set(
    rerun_module: ModuleType,
) -> None:
    """Two failing tests in one file yield one file entry."""
    text = (
        "FAILED tests/functional/test_csvio.py::test_import - ValueError\n"
        "ERROR tests/functional/test_csvio.py::test_export - LookupError\n"
    )

    result = rerun_module.parse_summary(text)

    assert result == {"tests/functional/test_csvio.py"}


@given(
    st.lists(
        st.text(
            alphabet=st.characters(
                blacklist_categories=("Zs", "Zl", "Zp", "Cc"),
                blacklist_characters=":-",
            ),
            min_size=1,
        ),
        max_size=8,
    )
)
def test_parse_summary_property_maps_node_ids_to_file_prefixes(
    rerun_module: ModuleType, node_ids: list[str]
) -> None:
    """Node ids always map to their ``::``-truncated file prefix."""
    text = "\n".join(f"FAILED {node_id}::test_case - boom" for node_id in node_ids)

    result = rerun_module.parse_summary(text)

    assert result == set(node_ids)


# ------------------------------------------------ orchestration seam


def _completed(
    returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    """Return a canned CompletedProcess for the fake runner."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _failed_summary(file: str) -> str:
    """Build a minimal -ra summary naming one failed file."""
    return f"1 failed\nFAILED {file}::test_x - AssertionError\n"


def _recording_runner(
    results: list[subprocess.CompletedProcess[str]],
) -> tuple[Callable[[list[str]], subprocess.CompletedProcess[str]], list[list[str]]]:
    """Return (fake runner, recorded calls), popping canned results."""
    calls: list[list[str]] = []

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return results.pop(0)

    return run, calls


def test_spawn_runs_command_and_returns_exit_code(
    rerun_module: ModuleType,
) -> None:
    """The real runner spawns subprocesses and captures output."""
    completed = rerun_module._spawn([sys.executable, "-c", "import sys; sys.exit(0)"])
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_spawn_when_timeout_none_waits_forever_and_returns_exit_code(
    rerun_module: ModuleType,
) -> None:
    """An explicit None timeout disables the bound.

    A fast child still returns its exit code; None means wait forever.
    """
    completed = rerun_module._spawn(
        [sys.executable, "-c", "import sys; sys.exit(0)"], timeout=None
    )

    assert completed.returncode == 0
    assert completed.stdout == ""


def test_spawn_when_child_hangs_times_out_terminates_and_returns_124(
    rerun_module: ModuleType,
) -> None:
    """A hung child is killed after the timeout and maps to exit 124.

    Returning at all -- within ~1s of a 3600s sleep -- proves the
    timeout fired and the child was terminated, not left running.
    """
    completed = rerun_module._spawn(
        [sys.executable, "-c", "import time; time.sleep(3600)"], timeout=0.3
    )

    assert completed.returncode == 124
    assert completed.stdout == ""


def test_spawn_streams_output_live_and_preserves_partial_output_on_timeout(
    rerun_module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Output is streamed live to stdout and preserved on timeout.

    The marker is flushed by the child immediately, then the child
    sleeps: capture_output-style buffering would show nothing in
    capsys, so the marker in ``out`` proves live streaming, and the
    marker in ``completed.stdout`` proves the partial output survives
    for parse_summary on a timed-out pass.
    """
    completed = rerun_module._spawn(
        [
            sys.executable,
            "-c",
            "import time; print('STREAMED_MARKER', flush=True); time.sleep(3600)",
        ],
        timeout=0.5,
    )

    captured = capsys.readouterr()
    assert completed.returncode == 124
    assert "STREAMED_MARKER" in captured.out
    assert "STREAMED_MARKER" in completed.stdout


def test_spawn_timeout_diagnostic_reports_elapsed_and_termination(
    rerun_module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """A timeout leaves a named stderr diagnostic, not silence."""
    completed = rerun_module._spawn(
        [sys.executable, "-c", "import time; time.sleep(3600)"], timeout=0.3
    )

    captured = capsys.readouterr()
    assert completed.returncode == 124
    assert "pass exceeded" in captured.err
    assert "child terminated" in captured.err


def test_run_pass_when_real_spawn_streams_live_and_does_not_double_echo(
    rerun_module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Real _spawn streams live; _run_pass must not echo it again."""
    result = rerun_module._run_pass(
        [sys.executable, "-c", "print('REAL_PASS_MARKER')"],
        rerun_module._spawn,
        "solo",
    )

    captured = capsys.readouterr()
    assert result == (0, set())
    assert captured.out.count("REAL_PASS_MARKER") == 1
    assert "solo: exit 0" in captured.err


def test_rerun_failed_files_when_command_empty_runs_once(
    rerun_module: ModuleType,
) -> None:
    """An empty argv still runs exactly one (degenerate) pass."""
    runner, calls = _recording_runner([_completed(0, "")])

    result = rerun_module.rerun_failed_files([], runner)

    assert result == 0
    assert calls == [[]]


def test_rerun_failed_files_when_first_token_not_pytest_runs_verbatim(
    rerun_module: ModuleType,
) -> None:
    """A non-pytest first token is not normalised; it runs as given."""
    command = ["python", "-c", "pass"]
    runner, calls = _recording_runner([_completed(0, "")])

    result = rerun_module.rerun_failed_files(command, runner)

    assert result == 0
    assert calls == [command]


def test_rerun_failed_files_when_initial_green_returns_zero_and_runs_once(
    rerun_module: ModuleType,
) -> None:
    """A clean first pass needs no rerun."""
    runner, calls = _recording_runner([_completed(0, "800 passed\n")])

    result = rerun_module.rerun_failed_files(_CMD, runner)

    assert result == 0
    assert calls == [_PASS1]


@pytest.mark.parametrize("crash_code", [2, 3])
def test_rerun_failed_files_when_initial_crash_propagates_code_without_rerun(
    rerun_module: ModuleType, crash_code: int
) -> None:
    """Exit codes outside {0, 1} propagate without rerun.

    The bounded pass's timeout (124) is deliberately NOT in this set:
    a hang is not a pytest crash, and the fresh-process whole-suite
    fallback (test_rerun_failed_files_when_initial_pass_times_out_...)
    is the one measured remedy for the corruption it signals.
    """
    runner, calls = _recording_runner([_completed(crash_code, "")])

    result = rerun_module.rerun_failed_files(_CMD, runner)

    assert result == crash_code
    assert calls == [_PASS1]


def test_rerun_failed_files_when_initial_pass_times_out_falls_back_to_whole_suite(
    rerun_module: ModuleType,
) -> None:
    """A timed-out pass (124, no summary) reruns the whole suite.

    A hang produces no ``-ra`` summary to map to files (measured on
    windows-latest CI, PR #9: the suite reached 97% then stalled in the
    last window-heavy files until the pass bound killed it). The
    fresh-process remedy is the only measured cure for the wx/SIP
    corruption, so a 124 pass falls back to a whole-suite run in a
    fresh process instead of failing bare.
    """
    runner, calls = _recording_runner([_completed(124, ""), _completed(0, "800 passed\n")])

    result = rerun_module.rerun_failed_files(_CMD, runner)

    assert result == 0
    assert calls == [_PASS1, _PASS1]


def test_rerun_failed_files_when_first_rerun_green_returns_zero(
    rerun_module: ModuleType,
) -> None:
    """A green rerun of only the failed file returns 0."""
    failed = _failed_summary("tests/functional/test_rider_editor.py")
    runner, calls = _recording_runner([_completed(1, failed), _completed(0, "800 passed\n")])

    result = rerun_module.rerun_failed_files(_CMD, runner)

    assert result == 0
    assert calls == [
        _PASS1,
        [*_PYTEST, "tests/functional/test_rider_editor.py", *_FLAGS],
    ]


def test_rerun_failed_files_when_second_rerun_green_returns_zero(
    rerun_module: ModuleType,
) -> None:
    """A green second rerun of the latest failed file returns 0."""
    a = _failed_summary("tests/functional/test_rider_editor.py")
    b = _failed_summary("tests/functional/test_ride_setup.py")
    runner, calls = _recording_runner(
        [_completed(1, a), _completed(1, b), _completed(0, "800 passed\n")]
    )

    result = rerun_module.rerun_failed_files(_CMD, runner)

    assert result == 0
    assert calls == [
        _PASS1,
        [*_PYTEST, "tests/functional/test_rider_editor.py", *_FLAGS],
        [*_PYTEST, "tests/functional/test_ride_setup.py", *_FLAGS],
    ]


def test_rerun_failed_files_when_all_reruns_fail_returns_last_exit_code(
    rerun_module: ModuleType,
) -> None:
    """Exhausted rerun budget -> the last pass's pytest exit code."""
    failed = _failed_summary("tests/functional/test_rider_editor.py")
    runner, calls = _recording_runner(
        [_completed(1, failed), _completed(1, failed), _completed(1, failed)]
    )

    result = rerun_module.rerun_failed_files(_CMD, runner)

    assert result == 1
    assert len(calls) == 3


def test_rerun_failed_files_when_rerun_crashes_propagates_code(
    rerun_module: ModuleType,
) -> None:
    """A rerun crash stops immediately, code propagated."""
    failed = _failed_summary("tests/functional/test_rider_editor.py")
    runner, calls = _recording_runner([_completed(1, failed), _completed(2, "")])

    result = rerun_module.rerun_failed_files(_CMD, runner)

    assert result == 2
    assert len(calls) == 2


def test_rerun_failed_files_when_summary_maps_to_no_file_falls_back_to_whole_suite(
    rerun_module: ModuleType,
) -> None:
    """An unmappable summary reruns the whole suite once."""
    sweep = "1 failed\nERROR tests/functional/conftest.py - no per-test node id\n"
    runner, calls = _recording_runner([_completed(1, sweep), _completed(0, "800 passed\n")])

    result = rerun_module.rerun_failed_files(_CMD, runner)

    assert result == 0
    assert calls == [_PASS1, _PASS1]


def test_rerun_failed_files_when_initial_summary_empty_falls_back_to_whole_suite(
    rerun_module: ModuleType,
) -> None:
    """Pass 1 parsed no files: rerun the whole suite once."""
    runner, calls = _recording_runner([_completed(1, "1 failed\n"), _completed(0, "800 passed\n")])

    result = rerun_module.rerun_failed_files(_CMD, runner)

    assert result == 0
    assert calls == [_PASS1, _PASS1]


def test_rerun_failed_files_when_rerun_summary_unmappable_falls_back_to_whole_suite(
    rerun_module: ModuleType,
) -> None:
    """A rerun whose own summary cannot map to files falls back once."""
    failed = _failed_summary("tests/functional/test_rider_editor.py")
    sweep = "ERROR tests/functional/conftest.py - session sweep\n"
    runner, calls = _recording_runner(
        [_completed(1, failed), _completed(1, sweep), _completed(0, "800 passed\n")]
    )

    result = rerun_module.rerun_failed_files(_CMD, runner)

    assert result == 0
    assert calls == [
        _PASS1,
        [*_PYTEST, "tests/functional/test_rider_editor.py", *_FLAGS],
        _PASS1,
    ]


def test_rerun_failed_files_when_summary_names_nonexistent_file_falls_back(
    rerun_module: ModuleType,
) -> None:
    """A parsed file that does not exist on disk is not rerunnable."""
    ghost = _failed_summary("tests/functional/test_never_created.py")
    runner, calls = _recording_runner([_completed(1, ghost), _completed(0, "800 passed\n")])

    result = rerun_module.rerun_failed_files(_CMD, runner)

    assert result == 0
    assert calls == [_PASS1, _PASS1]


def test_rerun_failed_files_preserves_trailing_posargs_in_rerun(
    rerun_module: ModuleType,
) -> None:
    """Noxfile posargs are kept after the flags in the rerun."""
    command = [*_CMD, "-k", "smoke"]
    failed = _failed_summary("tests/functional/test_rider_editor.py")
    runner, calls = _recording_runner([_completed(1, failed), _completed(0, "800 passed\n")])

    result = rerun_module.rerun_failed_files(command, runner)

    assert result == 0
    assert calls[1] == [*_PYTEST, "tests/functional/test_rider_editor.py", *command[2:]]


def test_rerun_failed_files_prints_progress_lines_to_stderr(
    rerun_module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each pass reports a progress line and echoes the pytest text."""
    failed = _failed_summary("tests/functional/test_rider_editor.py")
    runner, _ = _recording_runner([_completed(1, failed), _completed(0, "800 passed\n")])

    result = rerun_module.rerun_failed_files(_CMD, runner)

    captured = capsys.readouterr()
    assert result == 0
    assert "initial" in captured.err
    assert "rerun 1" in captured.err
    assert "tests/functional/test_rider_editor.py" in captured.err
    assert "1 failed" in captured.out
    assert "800 passed" in captured.out


def test_rerun_failed_files_echoes_stderr_to_stderr(
    rerun_module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pass's stderr is echoed to the wrapper's stderr."""
    runner, _ = _recording_runner([_completed(0, "", "warn: fixture cleanup\n")])

    result = rerun_module.rerun_failed_files(_CMD, runner)

    captured = capsys.readouterr()
    assert result == 0
    assert "warn: fixture cleanup" in captured.err
