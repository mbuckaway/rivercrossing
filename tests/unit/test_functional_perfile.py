# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for tools/functional_perfile.py (fresh-process-per-file).

The wx functional suite's wx/SIP wrapper-cache corruption is process-
granular and load-amplified: whole-suite xdist passes at ``-n 2``
accumulate degradation until late files fail with ``_support.py:106
LookupError`` -- a control exists in the XRC yet does not resolve --
while every file run alone in a fresh pytest process passes
(docs/FUNCTIONAL-SUITE-INSTABILITY.md section 2.1, measured this
session). The wrapper under test therefore runs one fresh pytest
process per file, two at a time, with one fresh-process retry round
for failed files as its backstop (this tool is self-contained).

``tools/`` carries no ``__init__.py`` (module-skeletons.md: it is dev
tooling), so it is only importable as an implicit PEP 420 namespace
package once the repo root is on ``sys.path``. The import is deferred
into a fixture so a broken import stays confined to the tests that
use the module instead of aborting collection for the whole suite.

Orchestration is unit-tested through a runner seam: ``run_file`` (and
``main``, which drives it) take an optional
``runner`` callable that returns a ``CompletedProcess``, so the argv
shape, the exit-code decisions (all-green -> 0, failed-then-retried
-> 0, still-failing -> 1, empty dir -> 0, usage error -> 2) and the
single retry round are pinned without ever spawning pytest. The
timeout path is pinned for real -- a sleeping child killed after the
bound reports exit code 124, mirroring functional_rerun's tests --
and one bounded smoke test spawns a real fresh process against
``tests/functional/test_menu_state.py`` (known-green alone).
"""

from __future__ import annotations

import importlib
import runpy
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_JOBS_ENV = "RIVERCROSSING_FUNCTIONAL_PERFILE_JOBS"
_MENU_STATE_FILE = _REPO_ROOT / "tests" / "functional" / "test_menu_state.py"


@pytest.fixture(scope="module")
def perfile_module() -> ModuleType:
    """Return tools.functional_perfile, imported lazily."""
    from tools import functional_perfile  # type: ignore[import-not-found]  # noqa: PLC0415

    return functional_perfile


def _command(python: str, file: Path, *flags: str) -> list[str]:
    """Build the exact argv run_file must spawn for *file*."""
    default_flags = ("-o", "faulthandler_timeout=600")
    return [
        python,
        "-m",
        "pytest",
        str(file),
        "-v",
        "--no-cov",
        "--reruns",
        "2",
        *default_flags,
        *flags,
    ]


def _completed(returncode: int) -> subprocess.CompletedProcess[str]:
    """Return a canned CompletedProcess for the fake runner."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr="")


def _recording_runner(
    plan: dict[str, list[int]],
) -> tuple[Callable[[list[str]], subprocess.CompletedProcess[str]], list[list[str]]]:
    """Return (fake runner, recorded calls) popping canned exit codes.

    *plan* maps a file name to the exit codes its runs must report, in
    order, so a retry round's second run of the same file pops the
    next code. Any run not planned for -- or past its plan -- fails
    loudly instead of guessing.
    """
    calls: list[list[str]] = []

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        codes = plan.get(Path(command[3]).name)
        if not codes:
            raise AssertionError(f"unplanned run of {command[3]}: {command}")
        return _completed(codes.pop(0))

    return run, calls


def _make_files(root: Path, *names: str) -> list[Path]:
    """Create empty files under *root*, returning their sorted paths."""
    for name in names:
        (root / name).touch()
    return sorted(root / name for name in names)


def _apply_jobs_env(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    """Set the jobs env to *value*, or unset it when *value* is None."""
    if value is None:
        monkeypatch.delenv(_JOBS_ENV, raising=False)
    else:
        monkeypatch.setenv(_JOBS_ENV, value)


# ---------------------------------------------------- module constants


def test_pass_timeout_s_default_is_at_least_1200_seconds(perfile_module: ModuleType) -> None:
    """The per-file bound must clear the worst single-file crawl.

    The default is 1200 s: one file in its own process, however slow,
    has the whole former per-pass budget to itself, while a hung modal
    (FUNCTIONAL-SUITE-INSTABILITY.md section 2.4) still costs one
    file's bound instead of a whole 20-40 minute pass.
    """
    assert perfile_module.PASS_TIMEOUT_S >= 1200


def test_pass_timeout_s_obeys_the_env_override(
    perfile_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env override wins over the default bound."""
    monkeypatch.setenv("RIVERCROSSING_FUNCTIONAL_PERFILE_TIMEOUT_S", "1500")
    importlib.reload(perfile_module)

    assert perfile_module.PASS_TIMEOUT_S == 1500


# ------------------------------------------------------- worker count


@pytest.mark.parametrize(
    "env_row",
    [
        ("1", 1),
        ("2", 2),
        ("4", 4),
        ("0", 1),  # clamped: a pool needs at least one worker
        ("auto", 1),  # legacy xdist value is not a perfile concurrency
        ("garbage", 1),
        (None, 1),  # unset -> one file at a time (measured convergent)
    ],
    ids=["one", "default", "four", "zero-clamped", "auto-fallback", "garbage-fallback", "unset"],
)
def test_worker_count_when_env_value_returns_expected(
    perfile_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    env_row: tuple[str | None, int],
) -> None:
    """RIVERCROSSING_FUNCTIONAL_PERFILE_JOBS sets the concurrency."""
    env_value, expected = env_row
    _apply_jobs_env(monkeypatch, env_value)

    assert perfile_module._worker_count() == expected


# ---------------------------------------------------------- test_files


def test_test_files_when_mixed_tree_returns_only_sorted_test_files(
    perfile_module: ModuleType, tmp_path: Path
) -> None:
    """Non-test helpers are excluded; test files come back sorted."""
    _make_files(
        tmp_path,
        "test_zebra.py",
        "test_alpha.py",
        "conftest.py",
        "harness.py",
        "pages.py",
        "scenario_runner.py",
    )

    result = perfile_module.test_files(tmp_path)

    assert result == [tmp_path / "test_alpha.py", tmp_path / "test_zebra.py"]


def test_test_files_when_nested_helper_module_exists_returns_only_top_level(
    perfile_module: ModuleType, tmp_path: Path
) -> None:
    """Files under a subdirectory are not top-level runs of this suite.

    tests/functional keeps its test files flat; a nested helper module
    (or future per-area subdir) is not something perfile should pick
    up as one of its fresh-process units.
    """
    nested = tmp_path / "area"
    nested.mkdir()
    (nested / "test_nested.py").touch()
    _make_files(tmp_path, "test_flat.py")

    result = perfile_module.test_files(tmp_path)

    assert result == [tmp_path / "test_flat.py"]


def test_test_files_when_directory_empty_returns_empty_list(
    perfile_module: ModuleType, tmp_path: Path
) -> None:
    """An empty directory scans to no files at all."""
    assert perfile_module.test_files(tmp_path) == []


# ------------------------------------------------------------ run_file


def test_run_file_when_flags_default_builds_exact_argv(
    perfile_module: ModuleType,
) -> None:
    """Default flags produce the exact fresh-process argv."""
    runner, calls = _recording_runner({"test_menu_state.py": [0]})
    file = _REPO_ROOT / "tests" / "functional" / "test_menu_state.py"

    completed = perfile_module.run_file("/opt/python", file, runner=runner)

    assert completed.returncode == 0
    assert calls == [_command("/opt/python", file)]


def test_run_file_when_flags_given_appends_them_after_reruns(
    perfile_module: ModuleType,
) -> None:
    """Extra flags trail the fixed argv like functional_rerun."""
    runner, calls = _recording_runner({"test_menu_state.py": [0]})
    file = _REPO_ROOT / "tests" / "functional" / "test_menu_state.py"

    completed = perfile_module.run_file("/opt/python", file, flags=("-k", "smoke"), runner=runner)

    assert completed.returncode == 0
    # Explicit flags REPLACE the default faulthandler flags: the
    # caller takes over the per-file pytest invocation entirely.
    assert calls == [
        [
            "/opt/python",
            "-m",
            "pytest",
            str(file),
            "-v",
            "--no-cov",
            "--reruns",
            "2",
            "-k",
            "smoke",
        ]
    ]


def test_run_file_when_runner_reports_failure_propagates_exit_code(
    perfile_module: ModuleType,
) -> None:
    """A failing file's pytest exit code (1) reaches the caller."""
    runner, _ = _recording_runner({"test_menu_state.py": [1]})
    file = _REPO_ROOT / "tests" / "functional" / "test_menu_state.py"

    completed = perfile_module.run_file("/opt/python", file, runner=runner)

    assert completed.returncode == 1


def test_run_file_when_runner_reports_timeout_returns_124(
    perfile_module: ModuleType,
) -> None:
    """A killed file reports the 124-style CompletedProcess.

    The real _spawn produces this shape when its timeout fires (pinned
    by test_spawn_when_child_hangs_times_out_and_returns_124); run_file
    must propagate a fake runner's 124 unchanged, because main counts
    any non-zero exit as a failed file for the retry round.
    """
    runner, calls = _recording_runner({"test_menu_state.py": [124]})
    file = _REPO_ROOT / "tests" / "functional" / "test_menu_state.py"

    completed = perfile_module.run_file("/opt/python", file, runner=runner)

    assert completed.returncode == 124
    assert calls == [_command("/opt/python", file)]


def test_spawn_when_child_exits_zero_returns_zero(perfile_module: ModuleType) -> None:
    """The real runner spawns and reports the child exit code."""
    completed = perfile_module._spawn([sys.executable, "-c", "import sys; sys.exit(0)"])

    assert completed.returncode == 0


def test_spawn_when_child_hangs_times_out_and_returns_124(
    perfile_module: ModuleType,
) -> None:
    """A hung child is killed after the bound and maps to exit 124.

    Returning at all -- within ~1s of a 3600s sleep -- proves the
    timeout fired and the child was terminated, not left running.
    """
    completed = perfile_module._spawn(
        [sys.executable, "-c", "import time; time.sleep(3600)"], timeout=0.3
    )

    assert completed.returncode == 124
    assert completed.stdout == ""


def test_spawn_streams_output_live_and_preserves_partial_output_on_timeout(
    perfile_module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Streamed lines reach stdout live and survive into the result.

    The marker is flushed by the child immediately, then the child
    sleeps: capture_output-style buffering would show nothing in
    capsys, so the marker in ``out`` proves live streaming, and the
    marker in ``completed.stdout`` proves the partial output survives
    on a timed-out run.
    """
    completed = perfile_module._spawn(
        [
            sys.executable,
            "-c",
            "import time; print('STREAMED_MARKER', flush=True); time.sleep(3600)",
        ],
        timeout=0.3,
    )

    captured = capsys.readouterr()
    assert completed.returncode == 124
    assert "STREAMED_MARKER" in captured.out
    assert "STREAMED_MARKER" in completed.stdout


# --------------------------------------------------------------- main


def test_main_when_all_files_green_returns_zero_and_runs_each_once(
    perfile_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean round needs no retry; every file runs exactly once."""
    monkeypatch.setenv(_JOBS_ENV, "1")
    files = _make_files(tmp_path, "test_b.py", "test_a.py")
    runner, calls = _recording_runner({"test_a.py": [0], "test_b.py": [0]})

    result = perfile_module.main([str(tmp_path)], runner=runner)

    assert result == 0
    assert calls == [_command(sys.executable, file) for file in files]


def test_main_when_argv_is_a_single_test_file_runs_that_file(
    perfile_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single test file is a valid target: exactly that file runs."""
    monkeypatch.setenv(_JOBS_ENV, "1")
    file = _make_files(tmp_path, "test_solo.py")[0]
    runner, calls = _recording_runner({"test_solo.py": [0]})

    result = perfile_module.main([str(file)], runner=runner)

    assert result == 0
    assert calls == [_command(sys.executable, file)]


def test_main_when_one_file_fails_retries_only_that_file_and_returns_zero_on_green(
    perfile_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry round re-runs only the failed file, once, fresh."""
    monkeypatch.setenv(_JOBS_ENV, "1")
    files = _make_files(tmp_path, "test_b.py", "test_a.py")
    runner, calls = _recording_runner({"test_a.py": [0], "test_b.py": [1, 0]})

    result = perfile_module.main([str(tmp_path)], runner=runner)

    assert result == 0
    assert calls == [
        _command(sys.executable, files[0]),
        _command(sys.executable, files[1]),
        _command(sys.executable, files[1]),
    ]


def test_main_when_file_fails_both_rounds_returns_one(
    perfile_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file failing every round fails the whole run (exit 1)."""
    monkeypatch.setenv(_JOBS_ENV, "1")
    _make_files(tmp_path, "test_a.py", "test_b.py")
    runner, calls = _recording_runner({"test_a.py": [0], "test_b.py": [1, 1, 1, 1]})

    result = perfile_module.main([str(tmp_path)], runner=runner)

    assert result == 1
    # initial a+b, then up to three fresh-process retries of b only.
    assert len(calls) == 5


def test_main_when_directory_has_no_test_files_returns_zero_without_running(
    perfile_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A helper-only directory is a clean no-op, not an error."""
    monkeypatch.setenv(_JOBS_ENV, "1")
    (tmp_path / "conftest.py").touch()
    runner, calls = _recording_runner({})

    result = perfile_module.main([str(tmp_path)], runner=runner)

    assert result == 0
    assert calls == []


@pytest.mark.parametrize(
    "argv",
    [
        [],  # missing the directory argument
        ["tests/functional", "extra"],  # more than one positional
        ["--help"],  # no option CLI exists (exit 2, not 0)
        ["no/such/dir"],  # bogus path
    ],
    ids=["no-args", "two-positionals", "help-option", "bogus-path"],
)
def test_main_when_argv_invalid_returns_two(
    perfile_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    """Any argv other than one existing directory is a usage error."""
    monkeypatch.setenv(_JOBS_ENV, "1")
    runner, calls = _recording_runner({})

    result = perfile_module.main(argv, runner=runner)

    assert result == 2
    assert calls == []


def test_main_when_jobs_env_two_runs_files_concurrently_and_all_green(
    perfile_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default concurrency of 2 still runs every file to green.

    Recording order is scheduling-dependent at two workers, so the
    calls are compared as an unordered set; the retry decision is
    per-file and order-independent.
    """
    monkeypatch.setenv(_JOBS_ENV, "2")
    files = _make_files(tmp_path, "test_b.py", "test_a.py")
    runner, calls = _recording_runner({"test_a.py": [0], "test_b.py": [0]})

    result = perfile_module.main([str(tmp_path)], runner=runner)

    assert result == 0
    assert sorted(calls) == sorted(_command(sys.executable, file) for file in files)


def test_main_prints_round_summaries_to_stderr(
    perfile_module: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each round reports ``N files, M failed: ...`` on stderr.

    The summary counts hold at any worker count, so this pins the
    default (unset) concurrency rather than forcing jobs=1.
    """
    _make_files(tmp_path, "test_b.py", "test_a.py")
    runner, _ = _recording_runner({"test_a.py": [0], "test_b.py": [1, 1, 1, 1]})

    result = perfile_module.main([str(tmp_path)], runner=runner)

    captured = capsys.readouterr()
    assert result == 1
    assert "2 files, 1 failed:" in captured.err
    assert "test_b.py" in captured.err
    assert "retry" in captured.err


# -------------------------------------------------------- real spawn


@pytest.mark.functional
def test_run_file_smoke_on_known_green_file_returns_zero(perfile_module: ModuleType) -> None:
    """A real fresh process on a known-green file exits 0.

    test_menu_state.py builds one real main_frame and passes alone in
    ~1s on a desktop session (measured this session), so it is a
    stable canary for the whole spawn path: python -m pytest, the
    --no-cov/--reruns flags, live streaming, and the exit-code
    plumbing. Bounded at 30 s via run_file's timeout -- a hang returns
    124 and fails this test instead of hanging the unit suite.
    """
    completed = perfile_module.run_file(sys.executable, _MENU_STATE_FILE, timeout=30)

    assert completed.returncode == 0


def test_main_when_entry_point_run_as_script_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Executing the tool file as __main__ exits 0 on a green dir.

    ``runpy.run_path(..., run_name="__main__")`` evaluates the file
    exactly as ``python tools/functional_perfile.py <dir>`` would --
    including the ``if __name__ == "__main__"`` guard -- inside this
    process, so the guard's exit path is exercised under coverage. The
    SystemExit it raises carries main's return code; the green file
    spawns one real fresh pytest process (~1s, jobs=1).
    """
    (tmp_path / "test_one.py").write_text(
        "def test_a():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    monkeypatch.setenv(_JOBS_ENV, "1")
    monkeypatch.setattr(sys, "argv", ["tools/functional_perfile.py", str(tmp_path)])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(_REPO_ROOT / "tools" / "functional_perfile.py"), run_name="__main__")

    assert exc_info.value.code == 0
