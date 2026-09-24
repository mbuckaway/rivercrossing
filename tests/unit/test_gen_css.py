# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for tools/gen_css.py (E6.2.1).

The vendored CSS build step compiles the frozen Tailwind source
(theme.css + the .j2 templates) once, in CI, into three committed
artifacts under ``src/rivercrossing/htmlexport/templates/``:
``compiled_css`` (the minified Tailwind output with a provenance
header carrying theme.css's sha256 -- the TB-7 staleness gate),
``compiled_css_wp`` (that same stylesheet with every selector scoped
under ``.rc-results`` and its cascade layers flattened: the WordPress
content fragment's own CSS) and
``fonts_css`` (the five Barlow woff2 subsets as base64 ``@font-face``
blocks). These tests are that generator's specification, written
before ``tools/gen_css.py`` existed.

``tools/`` is a dev-script tree, not an installed package (it has no
``__init__.py`` and is excluded from ``[tool.setuptools.packages.
find]``), so the module under test is loaded from its file path --
the pattern test_ids_gen.py:28-39 established for tools/gen_ids.py.

The Tailwind CLI seam is monkeypatched in every unit test so none of
them need Node; one integration test calls the real CLI when it is
installed and skips with a reason otherwise.
"""

import base64
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import (  # noqa: TC003 -- used at runtime as return types here
    Callable,
    Sequence,
)
from pathlib import Path
from types import ModuleType  # noqa: TC003 -- used at runtime as a return type here
from unittest.mock import Mock, call

import pytest
from hypothesis import given
from hypothesis import strategies as st

_GEN_CSS_PATH = Path(__file__).resolve().parents[2] / "tools" / "gen_css.py"


def _load_gen_css(path: Path) -> ModuleType:
    """Load tools/gen_css.py by path -- it isn't a package."""
    spec = importlib.util.spec_from_file_location("gen_css", path)
    if spec is None or spec.loader is None:
        msg = f"could not build a module spec for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen_css = _load_gen_css(_GEN_CSS_PATH)

_ROOT = Path(__file__).resolve().parents[2]
_COMMITTED_TEMPLATES_DIR = _ROOT / "src" / "rivercrossing" / "htmlexport" / "templates"
_COMMITTED_COMPILED_CSS = _COMMITTED_TEMPLATES_DIR / "compiled_css"
_COMMITTED_COMPILED_CSS_WP = _COMMITTED_TEMPLATES_DIR / "compiled_css_wp"
_COMMITTED_FONTS_CSS = _COMMITTED_TEMPLATES_DIR / "fonts_css"
_COMMITTED_FONTS_DIR = _COMMITTED_TEMPLATES_DIR / "fonts"

# Canned Tailwind output for tests that only exercise the generator's
# own logic (writing, drift, idempotence) and never the CLI's content.
_FAKE_CLI_CSS = (
    b".bp{position:relative}.chip{display:inline-flex}"
    b"bg-paper{background-color:#f2f2f3}text-ink{color:#1d1f20}"
)


def _seam_writing(css: bytes) -> Callable[[Sequence[str]], bytes]:
    """Build a Tailwind seam that writes *css* to its ``-o`` file.

    The CLI resolves ``-i``/``-o`` relative to ``--cwd``, so the fake
    mirrors that: it locates the ``--cwd`` value and writes the output
    file inside it.
    """

    def _run(argv: Sequence[str]) -> bytes:
        cwd = Path(argv[argv.index("--cwd") + 1])
        out = cwd / argv[argv.index("-o") + 1]
        out.write_bytes(css)
        return css

    return _run


@pytest.fixture
def fake_tailwind_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Substitute the CLI seam so the unit tests never need Node."""
    monkeypatch.setattr(gen_css, "_run_tailwind_cli", _seam_writing(_FAKE_CLI_CSS))


def _fixture_templates_dir(fixture_root: Path) -> Path:
    """Copy the committed templates tree into *fixture_root*."""
    fixture = fixture_root / "templates"
    shutil.copytree(_COMMITTED_TEMPLATES_DIR, fixture)
    return fixture


def _point_cli_at_missing_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the CLI constant at a missing node_modules shim."""
    missing = tmp_path / "node_modules" / ".bin" / "tailwindcss"
    monkeypatch.setattr(gen_css, "_TAILWIND_CLI", missing)
    return missing


def _fake_npm_on_path(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make ``shutil.which`` resolve to a fake absolute npm path."""
    npm_path = "/usr/local/bin/npm"
    monkeypatch.setattr(gen_css.shutil, "which", lambda _name: npm_path)
    return npm_path


def _compiled_rule_declarations(selector: str) -> dict[str, str]:
    """Return one rule's declarations from the committed compiled CSS.

    The artifact is minified, so rules are matched textually: the
    selector must be followed directly by ``{``, which keeps ``.chip``
    from matching ``.chip.r`` or ``.chip.j``. A selector the artifact
    no longer carries yields ``{}`` -- the caller's assertion decides
    whether that is a failure.
    """
    content = _COMMITTED_COMPILED_CSS.read_text(encoding="utf-8")
    match = re.search(re.escape(selector) + r"\{([^{}]*)\}", content)
    if match is None:
        return {}
    return dict(
        declaration.split(":", 1)
        for declaration in match.group(1).split(";")
        if ":" in declaration
    )


# ------------------------------------------- the honest regeneration


def test_build_artifacts_compiled_css_matches_committed_artifact_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regenerating from the frozen templates reproduces compiled_css.

    The seam returns the CLI bytes embedded in the committed artifact,
    so what is actually under test is the header contract: the
    provenance line (theme.css sha256 + pinned version) and the
    pass-through of the CLI output must reproduce the committed file
    exactly. Mirrors test_gen_rank_vectors.py's honesty pattern.
    """
    committed = _COMMITTED_COMPILED_CSS.read_bytes()
    cli_css = committed.split(b"*/\n", 1)[1]
    monkeypatch.setattr(gen_css, "_run_tailwind_cli", _seam_writing(cli_css))

    gen_css.write_artifacts(_COMMITTED_TEMPLATES_DIR, tmp_path)

    assert (tmp_path / "compiled_css").read_bytes() == committed


def test_build_artifacts_derives_compiled_css_wp_from_the_committed_compiled_css(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It derives from compiled_css rather than compiling twice.

    The seam returns the committed artifact's own CLI bytes, so the
    derived file can only match if the transform is exactly the one
    that produced it -- the honesty pattern the compiled_css test above
    uses, applied to the derivation instead of the header.
    """
    committed = _COMMITTED_COMPILED_CSS.read_bytes()
    monkeypatch.setattr(
        gen_css, "_run_tailwind_cli", _seam_writing(committed.split(b"*/\n", 1)[1])
    )

    gen_css.write_artifacts(_COMMITTED_TEMPLATES_DIR, tmp_path)

    assert (tmp_path / "compiled_css_wp").read_bytes() == _COMMITTED_COMPILED_CSS_WP.read_bytes()


def test_render_fonts_css_matches_committed_fonts_css_byte_for_byte(tmp_path: Path) -> None:
    r"""The @font-face rendering reproduces fonts_css exactly.

    ``write_bytes`` is load-bearing: ``write_text`` translates ``\n``
    to the platform newline on Windows, which would rewrite the frozen
    LF artifact as CRLF and fail the byte compare (measured).
    """
    out_path = tmp_path / "fonts_css"

    out_path.write_bytes(gen_css.render_fonts_css(_COMMITTED_FONTS_DIR).encode("utf-8"))

    assert out_path.read_bytes() == _COMMITTED_FONTS_CSS.read_bytes()


def test_build_artifacts_with_real_tailwind_cli_reproduces_committed_compiled_css() -> None:
    """The real pinned CLI (when installed) reproduces compiled_css.

    The honest end-to-end check: Node is present and node_modules is
    populated, so the seam is not faked and the committed artifacts are
    the expectation -- both the compile (compiled_css) and the
    derivation (compiled_css_wp). Skipped with a reason when the CLI or
    Node itself is missing, so a machine without Node never fails the
    suite. The Node check is load-bearing on Windows: npm's ``.cmd``
    shim calls ``node`` by bare name, and a missing PATH entry fails
    with "'node' is not recognized" rather than a clean skip
    (measured).
    """
    if not gen_css._tailwind_executable().is_file() or shutil.which("node") is None:
        pytest.skip(
            "pinned Tailwind CLI or node not installed (run npm install "
            "and put node on PATH); skipping the real-CLI integration check"
        )
    work_dir = Path(tempfile.mkdtemp(prefix="gen_css-itest-", dir=_ROOT / "build"))
    try:
        compiled_css, compiled_css_wp, fonts_css = gen_css.build_artifacts(
            _COMMITTED_TEMPLATES_DIR, work_dir
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    assert compiled_css == _COMMITTED_COMPILED_CSS.read_bytes()
    assert compiled_css_wp == _COMMITTED_COMPILED_CSS_WP.read_bytes()
    assert fonts_css == _COMMITTED_FONTS_CSS.read_bytes()


# ----------------------------------------- write, idempotence, CLI


@pytest.mark.usefixtures("fake_tailwind_cli")
@pytest.mark.parametrize("name", ["compiled_css", "compiled_css_wp", "fonts_css"])
def test_write_artifacts_idempotent_two_runs_produce_byte_identical_files(
    tmp_path: Path, name: str
) -> None:
    """Regeneration is idempotent: two writes match byte-for-byte."""
    fixture = _fixture_templates_dir(tmp_path / "src")

    gen_css.write_artifacts(fixture, tmp_path / "first")
    gen_css.write_artifacts(fixture, tmp_path / "second")

    assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


@pytest.mark.usefixtures("fake_tailwind_cli")
def test_main_write_flag_with_path_overrides_writes_artifacts_to_out_dir(
    tmp_path: Path,
) -> None:
    """The path overrides point the generator at a fixture tree."""
    fixture = _fixture_templates_dir(tmp_path / "templates")
    out_dir = tmp_path / "out"

    exit_code = gen_css.main(
        ["--write", "--templates-dir", str(fixture), "--out-dir", str(out_dir)]
    )

    assert exit_code == 0
    assert (out_dir / "compiled_css").is_file()
    assert (out_dir / "compiled_css_wp").is_file()
    assert (out_dir / "fonts_css").is_file()


def test_main_write_flag_returns_two_when_theme_css_missing(tmp_path: Path) -> None:
    """``--write`` with a missing input fails loudly, not silently."""
    fixture = _fixture_templates_dir(tmp_path / "templates")
    (fixture / "theme.css").unlink()

    exit_code = gen_css.main(
        ["--write", "--templates-dir", str(fixture), "--out-dir", str(tmp_path / "out")]
    )

    assert exit_code == 2


# ----------------------------------------------------------- drift gate


@pytest.mark.usefixtures("fake_tailwind_cli")
def test_main_check_flag_returns_zero_when_artifacts_match(
    tmp_path: Path,
) -> None:
    """``--check`` matches the nox ``css_drift`` session when clean."""
    fixture = _fixture_templates_dir(tmp_path / "templates")
    out_dir = tmp_path / "out"
    gen_css.main(["--write", "--templates-dir", str(fixture), "--out-dir", str(out_dir)])

    exit_code = gen_css.main(
        ["--check", "--templates-dir", str(fixture), "--out-dir", str(out_dir)]
    )

    assert exit_code == 0


@pytest.mark.usefixtures("fake_tailwind_cli")
@pytest.mark.parametrize("name", ["compiled_css", "compiled_css_wp", "fonts_css"])
def test_main_check_flag_leaves_artifact_unchanged(tmp_path: Path, name: str) -> None:
    """``--check`` never rewrites the artifact.

    Only ``--write`` regenerates the committed files; a check that
    wrote (even byte-identical bytes) would bump the artifact's mtime.
    """
    fixture = _fixture_templates_dir(tmp_path / "templates")
    out_dir = tmp_path / "out"
    gen_css.main(["--write", "--templates-dir", str(fixture), "--out-dir", str(out_dir)])
    artifact = out_dir / name
    before = (artifact.read_bytes(), artifact.stat().st_mtime_ns)

    exit_code = gen_css.main(
        ["--check", "--templates-dir", str(fixture), "--out-dir", str(out_dir)]
    )

    assert exit_code == 0
    assert (artifact.read_bytes(), artifact.stat().st_mtime_ns) == before


@pytest.mark.usefixtures("fake_tailwind_cli")
def test_main_check_flag_returns_one_with_drift_line_when_compiled_css_modified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stale compiled_css fails the build with a ``drift:`` line."""
    fixture = _fixture_templates_dir(tmp_path / "templates")
    out_dir = tmp_path / "out"
    gen_css.main(["--write", "--templates-dir", str(fixture), "--out-dir", str(out_dir)])
    compiled_path = out_dir / "compiled_css"
    compiled_path.write_bytes(compiled_path.read_bytes() + b"\n")

    exit_code = gen_css.main(
        ["--check", "--templates-dir", str(fixture), "--out-dir", str(out_dir)]
    )

    assert exit_code == 1
    assert "drift: compiled_css" in capsys.readouterr().out


@pytest.mark.usefixtures("fake_tailwind_cli")
def test_main_check_flag_returns_one_with_drift_line_when_compiled_css_wp_modified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stale compiled_css_wp fails the build with a ``drift:`` line.

    The scoped artifact is derived from compiled_css in the same
    ``--write``, so drift here means it was edited by hand -- exactly
    what the gate exists to catch.
    """
    fixture = _fixture_templates_dir(tmp_path / "templates")
    out_dir = tmp_path / "out"
    gen_css.main(["--write", "--templates-dir", str(fixture), "--out-dir", str(out_dir)])
    scoped_path = out_dir / "compiled_css_wp"
    scoped_path.write_bytes(scoped_path.read_bytes() + b"\n")

    exit_code = gen_css.main(
        ["--check", "--templates-dir", str(fixture), "--out-dir", str(out_dir)]
    )

    assert exit_code == 1
    assert "drift: compiled_css_wp" in capsys.readouterr().out


@pytest.mark.usefixtures("fake_tailwind_cli")
def test_main_check_flag_returns_one_with_drift_line_when_fonts_css_modified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stale fonts_css fails the build with a ``drift:`` line."""
    fixture = _fixture_templates_dir(tmp_path / "templates")
    out_dir = tmp_path / "out"
    gen_css.main(["--write", "--templates-dir", str(fixture), "--out-dir", str(out_dir)])
    fonts_path = out_dir / "fonts_css"
    fonts_path.write_text(fonts_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    exit_code = gen_css.main(
        ["--check", "--templates-dir", str(fixture), "--out-dir", str(out_dir)]
    )

    assert exit_code == 1
    assert "drift: fonts_css" in capsys.readouterr().out


@pytest.mark.usefixtures("fake_tailwind_cli")
def test_main_check_flag_returns_one_when_artifact_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Before the artifacts exist at all, every one is drift."""
    fixture = _fixture_templates_dir(tmp_path / "templates")

    exit_code = gen_css.main(
        ["--check", "--templates-dir", str(fixture), "--out-dir", str(tmp_path / "empty")]
    )

    assert exit_code == 1
    assert "drift: compiled_css missing" in capsys.readouterr().out


def test_main_check_flag_returns_zero_when_templates_dir_absent(tmp_path: Path) -> None:
    """A missing templates dir passes vacuously, like ids_drift."""
    exit_code = gen_css.main(["--check", "--templates-dir", str(tmp_path / "no-such-templates")])

    assert exit_code == 0


def test_main_check_flag_returns_zero_when_theme_css_missing(tmp_path: Path) -> None:
    """A templates dir missing an input passes vacuously.

    Mirrors the ids_drift guard: no input files, nothing to check.
    """
    fixture = _fixture_templates_dir(tmp_path / "templates")
    (fixture / "theme.css").unlink()

    exit_code = gen_css.main(["--check", "--templates-dir", str(fixture)])

    assert exit_code == 0


# ---------------------------------------------------- hard CLI errors


def test_main_check_flag_returns_two_when_tailwind_cli_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing pinned CLI is a hard error naming the fix, not drift.

    The check must not confuse "tooling absent" with "artifact stale".
    ``shutil.which`` is forced to ``None`` so the self-install seam is
    never exercised -- this test must not shell out to a real npm.
    """
    fixture = _fixture_templates_dir(tmp_path / "templates")
    _point_cli_at_missing_path(monkeypatch, tmp_path)
    monkeypatch.setattr(gen_css.shutil, "which", lambda _name: None)

    exit_code = gen_css.main(
        ["--check", "--templates-dir", str(fixture), "--out-dir", str(tmp_path / "out")]
    )

    assert exit_code == 2
    assert "npm install" in capsys.readouterr().err


def test_main_check_flag_returns_two_when_tailwind_cli_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-zero CLI exit is a hard error, never a silent pass."""
    fixture = _fixture_templates_dir(tmp_path / "templates")
    cli = tmp_path / "fake-cli"
    cli.write_bytes(b"#!/bin/sh\nexit 1\n")
    monkeypatch.setattr(gen_css, "_TAILWIND_CLI", cli)

    def _run_fails(*_args: object, **_kwargs: object) -> bytes:
        raise subprocess.CalledProcessError(1, "tailwindcss", stderr=b"synthetic failure")

    monkeypatch.setattr(gen_css.subprocess, "run", _run_fails)

    exit_code = gen_css.main(
        ["--check", "--templates-dir", str(fixture), "--out-dir", str(tmp_path / "out")]
    )

    assert exit_code == 2
    assert "synthetic failure" in capsys.readouterr().err


def test_run_tailwind_cli_missing_cli_raises_tailwind_cli_missing_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam's own guard names the install fix (T-5 direct raise).

    ``shutil.which`` is forced to ``None`` so the self-install seam is
    never exercised -- this test must not shell out to a real npm.
    """
    _point_cli_at_missing_path(monkeypatch, tmp_path)
    monkeypatch.setattr(gen_css.shutil, "which", lambda _name: None)

    with pytest.raises(gen_css.TailwindCliMissingError, match=re.escape("npm install")):
        gen_css._run_tailwind_cli([])


# ----------------------------------- Tailwind CLI self-install (E6.2.1)


def test_run_tailwind_cli_present_cli_skips_the_npm_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An installed CLI skips the npm lookup and install entirely."""
    cli = tmp_path / "tailwindcss"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(gen_css, "_TAILWIND_CLI", cli)
    which = Mock(return_value="/usr/local/bin/npm")
    monkeypatch.setattr(gen_css.shutil, "which", which)
    mock_run = Mock(return_value=Mock(stdout=b"cli-out"))
    monkeypatch.setattr(gen_css.subprocess, "run", mock_run)

    result = gen_css._run_tailwind_cli([])

    assert result == b"cli-out"
    which.assert_not_called()
    mock_run.assert_called_once_with(
        [str(cli)], cwd=gen_css._ROOT, capture_output=True, check=True, timeout=120
    )


def test_run_tailwind_cli_missing_cli_with_npm_installs_then_runs_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing CLI is bootstrapped with ``npm ci``, then runs."""
    missing = _point_cli_at_missing_path(monkeypatch, tmp_path)
    npm = _fake_npm_on_path(monkeypatch)

    def _install(*_args: object, **_kwargs: object) -> Mock:
        missing.parent.mkdir(parents=True, exist_ok=True)
        missing.write_text("#!/bin/sh\n", encoding="utf-8")
        return Mock(stdout=b"")

    def _compile(*_args: object, **_kwargs: object) -> Mock:
        return Mock(stdout=b"compiled-out")

    responses = {npm: _install, str(missing): _compile}

    def _dispatch(argv: Sequence[str], **kwargs: object) -> Mock:
        return responses[argv[0]](*argv, **kwargs)

    mock_run = Mock(side_effect=_dispatch)
    monkeypatch.setattr(gen_css.subprocess, "run", mock_run)

    result = gen_css._run_tailwind_cli([])

    assert result == b"compiled-out"
    assert mock_run.call_args_list == [
        call([npm, "ci"], cwd=gen_css._ROOT, capture_output=True, check=True),
        call(
            [str(missing)],
            cwd=gen_css._ROOT,
            capture_output=True,
            check=True,
            timeout=120,
        ),
    ]


def test_run_tailwind_cli_missing_cli_and_missing_npm_raises_missing_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing npm on PATH fails before any subprocess."""
    _point_cli_at_missing_path(monkeypatch, tmp_path)
    monkeypatch.setattr(gen_css.shutil, "which", lambda _name: None)
    mock_run = Mock(return_value=Mock())
    monkeypatch.setattr(gen_css.subprocess, "run", mock_run)

    with pytest.raises(gen_css.TailwindCliMissingError, match=re.escape("npm install")):
        gen_css._run_tailwind_cli([])

    mock_run.assert_not_called()


def test_run_tailwind_cli_install_leaves_cli_absent_raises_missing_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An install that leaves no CLI is a hard error."""
    _point_cli_at_missing_path(monkeypatch, tmp_path)
    npm = _fake_npm_on_path(monkeypatch)
    mock_run = Mock(return_value=Mock(stdout=b""))
    monkeypatch.setattr(gen_css.subprocess, "run", mock_run)

    with pytest.raises(gen_css.TailwindCliMissingError, match=re.escape("npm install")):
        gen_css._run_tailwind_cli([])

    mock_run.assert_called_once_with(
        [npm, "ci"], cwd=gen_css._ROOT, capture_output=True, check=True
    )


def test_run_tailwind_cli_npm_ci_failure_raises_missing_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed ``npm ci`` surfaces as the CLI-missing error."""
    _point_cli_at_missing_path(monkeypatch, tmp_path)
    _fake_npm_on_path(monkeypatch)

    def _npm_fails(*_args: object, **_kwargs: object) -> Mock:
        raise subprocess.CalledProcessError(1, "npm ci", stderr=b"registry unreachable")

    monkeypatch.setattr(gen_css.subprocess, "run", _npm_fails)

    with pytest.raises(gen_css.TailwindCliMissingError, match=re.escape("npm install")):
        gen_css._run_tailwind_cli([])


def test_run_tailwind_cli_failing_cli_raises_tailwind_compile_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam surfaces a non-zero CLI exit with its stderr (T-5)."""
    cli = tmp_path / "fake-cli"
    cli.write_bytes(b"#!/bin/sh\nexit 1\n")
    monkeypatch.setattr(gen_css, "_TAILWIND_CLI", cli)

    def _run_fails(*_args: object, **_kwargs: object) -> bytes:
        raise subprocess.CalledProcessError(1, "tailwindcss", stderr=b"synthetic failure")

    monkeypatch.setattr(gen_css.subprocess, "run", _run_fails)

    with pytest.raises(gen_css.TailwindCompileError, match=re.escape("synthetic failure")):
        gen_css._run_tailwind_cli([])


def test_run_tailwind_cli_passes_timeout_120_to_subprocess_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pinned CLI cannot hang the build; run() gets timeout=120."""
    cli = tmp_path / "tailwindcss"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(gen_css, "_TAILWIND_CLI", cli)
    mock_run = Mock(return_value=Mock())
    monkeypatch.setattr(gen_css.subprocess, "run", mock_run)

    gen_css._run_tailwind_cli(["--cwd", str(tmp_path)])

    mock_run.assert_called_once_with(
        [str(cli), "--cwd", str(tmp_path)],
        cwd=gen_css._ROOT,
        capture_output=True,
        check=True,
        timeout=120,
    )


def test_run_tailwind_cli_timeout_raises_tailwind_timeout_error_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung CLI surfaces as TailwindTimeoutError, never silently."""
    cli = tmp_path / "tailwindcss"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(gen_css, "_TAILWIND_CLI", cli)

    def _run_times_out(*_args: object, **_kwargs: object) -> bytes:
        raise subprocess.TimeoutExpired("tailwindcss", timeout=120)

    monkeypatch.setattr(gen_css.subprocess, "run", _run_times_out)

    with pytest.raises(gen_css.TailwindTimeoutError, match=re.escape("120")):
        gen_css._run_tailwind_cli([])


def test_main_check_flag_returns_two_when_tailwind_cli_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A hung CLI fails the build loudly, never a silent pass."""
    fixture = _fixture_templates_dir(tmp_path / "templates")
    cli = tmp_path / "fake-cli"
    cli.write_bytes(b"#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(gen_css, "_TAILWIND_CLI", cli)

    def _run_times_out(*_args: object, **_kwargs: object) -> bytes:
        raise subprocess.TimeoutExpired("tailwindcss", timeout=120)

    monkeypatch.setattr(gen_css.subprocess, "run", _run_times_out)

    exit_code = gen_css.main(
        ["--check", "--templates-dir", str(fixture), "--out-dir", str(tmp_path / "out")]
    )

    assert exit_code == 2
    assert "120" in capsys.readouterr().err


# -------------------------------------- Windows CLI resolution (E6.2.1)


def test_tailwind_executable_resolves_cmd_shim_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows cannot execute the extensionless npm shim; use .cmd.

    npm installs ``tailwindcss`` (POSIX shell script), ``tailwindcss
    .cmd`` and ``tailwindcss.ps1`` in node_modules/.bin. CreateProcess
    raises WinError 193 on the plain script, so the resolver must pick
    the cmd.exe shim when one exists (measured on windows-latest CI).
    """
    cli = tmp_path / "tailwindcss"
    cmd = tmp_path / "tailwindcss.cmd"
    cmd.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(gen_css, "_TAILWIND_CLI", cli)
    monkeypatch.setattr(gen_css.os, "name", "nt")

    assert gen_css._tailwind_executable() == cmd


def test_tailwind_executable_falls_back_to_plain_shim_without_cmd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing .cmd sibling (odd npm state) falls back to the base."""
    cli = tmp_path / "tailwindcss"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(gen_css, "_TAILWIND_CLI", cli)
    monkeypatch.setattr(gen_css.os, "name", "nt")

    assert gen_css._tailwind_executable() == cli


def test_tailwind_executable_posix_uses_plain_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POSIX platforms run the shebang script directly, never .cmd."""
    cli = tmp_path / "tailwindcss"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(gen_css, "_TAILWIND_CLI", cli)
    monkeypatch.setattr(gen_css.os, "name", "posix")

    assert gen_css._tailwind_executable() == cli


@pytest.mark.skipif(os.name != "nt", reason=".cmd shims only exist on Windows")
def test_run_tailwind_cli_executes_cmd_shim_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam really executes the .cmd shim on Windows (regression).

    Without the resolver this subprocess.run targets the extensionless
    script and dies with WinError 193 / TailwindCliMissingError; with
    it, the cmd.exe shim runs and its stdout comes back.
    """
    cli = tmp_path / "tailwindcss"
    cmd = tmp_path / "tailwindcss.cmd"
    cmd.write_text("@echo fake-tailwind-output\r\n", encoding="utf-8")
    monkeypatch.setattr(gen_css, "_TAILWIND_CLI", cli)

    out = gen_css._run_tailwind_cli([])

    assert b"fake-tailwind-output" in out


# ----------------------------------------------------- artifact content


def test_compiled_css_contains_custom_rules_and_theme_utilities() -> None:
    """The vendored artifact carries .bp/.chip and the @theme utilities.

    These are the classes the results page actually renders with.
    """
    content = _COMMITTED_COMPILED_CSS.read_text(encoding="utf-8")

    assert ".bp" in content
    assert ".chip" in content
    assert "bg-paper" in content
    assert "text-ink" in content


@pytest.mark.parametrize(
    # theme.css tokens: #1d1f20 = --color-ink, #e9e9ea = --color-panel.
    ("selector", "declaration", "expected"),
    [(".chip", "color", "#1d1f20"), (".chip.j", "background", "#e9e9ea")],
)
def test_compiled_css_chip_declares_ink_color_and_joker_chip_own_background(
    selector: str, declaration: str, expected: str
) -> None:
    """A chip that inherits its colour disappears on the dark card.

    Every results card is a ``<span class="chip">``. ``.chip`` fills a
    near-white pill (#e9e9ea), so on the dark 1st-place card
    (``bg-steel-800 text-paper``) a chip without its own ``color``
    inherits near-white text onto that pill and the spade/club cards
    vanish; ``.chip.j`` used to declare ``background: transparent``,
    re-exposing the same vanish for the joker. The minifier rewrites
    ``transparent`` to ``0 0``, so a mere presence check would not have
    caught that bug; both values are therefore pinned to theme.css's
    ink and panel tokens, so dropping or re-widening either declaration
    fails here -- the artifact is frozen, not the bug.
    """
    declarations = _compiled_rule_declarations(selector)

    assert declarations.get(declaration) == expected


@pytest.mark.parametrize(
    ("selector", "expected"), [(".chip.r", "#c0392b"), (".chip.j", "#416180")]
)
def test_compiled_css_red_chip_declares_the_suit_red_and_the_joker_keeps_steel(
    selector: str, expected: str
) -> None:
    """Hearts/diamonds take the suit red; the joker's star stays steel.

    The chip classes each own their ``color`` (see the test above for
    why), so the tone is pinned per class: ``.chip.r`` is the one suit
    red (#c0392b -- the old mono-steel #416180 accent is gone), while
    ``.chip.j`` keeps the steel the joker had all along (it is not a
    suit). ``.chip`` (spades/clubs, ink) is pinned by the test above.
    """
    declarations = _compiled_rule_declarations(selector)

    assert declarations.get("color") == expected


def test_fonts_css_contains_both_families_and_all_five_weights() -> None:
    """Both Barlow families ship, each block with its frozen weight."""
    content = _COMMITTED_FONTS_CSS.read_text(encoding="utf-8")

    assert 'font-family: "Barlow";' in content
    assert 'font-family: "Barlow Condensed";' in content
    assert content.count("font-weight:") == 5
    for weight in ("400", "500", "700", "600"):
        assert f"font-weight: {weight};" in content


@pytest.mark.parametrize("name", ["compiled_css", "compiled_css_wp", "fonts_css"])
def test_committed_stylesheet_artifacts_have_no_url_http_references(name: str) -> None:
    """Zero external fetches: the page must work offline (R-61)."""
    content = (_COMMITTED_TEMPLATES_DIR / name).read_text(encoding="utf-8")

    assert "url(http" not in content


# ---------------------------------------------------------- properties


@given(data=st.binary(max_size=4096))
def test_data_font_uri_base64_payload_round_trips_to_original_bytes(data: bytes) -> None:
    """Any woff2 payload embeds losslessly in a data: font URI."""
    uri = gen_css.data_font_uri(data)

    payload = uri.split("base64,", 1)[1].split(") format", 1)[0]
    assert base64.b64decode(payload) == data


# ------------------------------------ the WordPress-scoped variant

# htmlexport.render_wordpress publishes this artifact -- not
# compiled_css -- into a WordPress page's content, beside the site's
# own theme. The transform is two things: a *selector* rewrite, never
# an ``@scope (.rc-results) {…}`` wrapper (that cannot nest the theme's
# ``:root,:host`` token block or the nine ``@property`` registrations
# the minified output ends with -- measured), and the flattening of
# the ``@layer theme``/``base``/``utilities`` wrappers, because an
# unlayered author declaration beats every layered one regardless of
# specificity, so a theme's plain ``table{width:60%}`` outranked the
# fragment's own layered ``.rc-results .w-full{width:100%}`` (measured
# in a real browser against a mock theme). These tests are the
# transform's contract.


@pytest.fixture(scope="module")
def committed_css() -> str:
    """Return the committed compiled_css artifact as text."""
    return _COMMITTED_COMPILED_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def scoped_css(committed_css: str) -> str:
    """Return the scoped transform of the committed artifact."""
    return gen_css.render_compiled_css_wp(committed_css).decode("utf-8")


def test_compiled_css_wp_name_constant_names_the_committed_artifact() -> None:
    """The manifest and package-data name this artifact too."""
    assert gen_css.COMPILED_CSS_WP_NAME == "compiled_css_wp"


def test_render_compiled_css_wp_keeps_every_property_registration_at_top_level(
    scoped_css: str, committed_css: str
) -> None:
    """``@property`` is global, so it has no selector to scope.

    The fragment's utilities read the ``--tw-*`` values these nine
    registrations establish, so all nine must survive verbatim at the
    stylesheet's top level -- and none may gain the wrapper as a
    prefix, which would leave the custom properties unregistered.
    """
    registrations = re.findall(r"@property [^{]+\{[^{}]*\}", committed_css)

    assert len(registrations) == 9
    assert [entry for entry in registrations if entry not in scoped_css] == []
    assert ".rc-results @property" not in scoped_css


def test_render_compiled_css_wp_moves_the_theme_tokens_onto_the_wrapper(scoped_css: str) -> None:
    """The theme's custom properties are declared on the wrapper itself.

    ``:root``/``:host`` can never match inside a page's content, so the
    token block hangs off ``.rc-results`` -- the one element of the
    fragment that inherits down to every element in it. It sits
    unlayered, like every other rule of the flattened fragment.
    """
    assert ".rc-results{--font-sans:" in scoped_css
    assert ":root" not in scoped_css


@pytest.mark.parametrize(
    ("layer", "rule"),
    [
        ("theme", ".rc-results{--font-sans:"),
        ("base", ".rc-results [hidden]:where(:not([hidden=until-found])){display:none!important}"),
        ("utilities", ".rc-results .static{position:static}"),
    ],
)
def test_render_compiled_css_wp_flattens_the_layer_wrapper_and_keeps_its_rules(
    scoped_css: str, layer: str, rule: str
) -> None:
    """No layer wrapper survives; the rules it held still do.

    An unlayered author declaration beats a layered one whatever their
    specificities. With the Tailwind rules still inside ``@layer
    base``/``@layer utilities``, a publishing theme's plain unlayered
    ``table{border:6px dashed lime;width:60%}`` therefore beat the
    fragment's scoped ``.rc-results .w-full{width:100%}`` and the
    fragment rendered theme-mangled (measured in a real browser against
    a mock theme). Dropping the wrapper puts the fragment's rules back
    on specificity -- and flattening must not drop the rules with it.
    """
    assert f"@layer {layer}" not in scoped_css
    assert rule in scoped_css


def test_render_compiled_css_wp_keeps_theme_then_base_then_utilities_in_order(
    scoped_css: str,
) -> None:
    """Flattening preserves the source order the layers encoded.

    Unlayered rules cascade by source position instead of by layer
    order, so the utilities have to keep coming after preflight:
    reorder them and preflight's own reset on every element would beat
    ``.rc-results .mt-1{margin-top:var(--spacing)}``, emptying every
    spacing utility in the fragment. The theme tokens stay in front of
    both.
    """
    theme = scoped_css.index(".rc-results{--font-sans:")
    base = scoped_css.index(".rc-results [hidden]:where(:not([hidden=until-found])){")
    utilities = scoped_css.index(".rc-results .static{")

    assert theme < base < utilities


def test_render_compiled_css_wp_leaves_only_the_layers_the_flattening_spares(
    scoped_css: str,
) -> None:
    """Only the two layer at-rules no flattening touches survive.

    ``@layer properties`` holds the ``--tw-*`` initial values the
    fragment's utilities read -- it is the first layer by design, so
    everything else already outranked it, flattened or not -- and the
    bare ``@layer components;`` statement carries no rules at all.
    Neither mentions a selector, so neither can restyle a host site.
    ``theme``, ``base`` and ``utilities`` are the three the transform
    flattens, and none of them may come back.
    """
    assert re.findall(r"@layer\s+[-\w]+", scoped_css) == [
        "@layer properties",
        "@layer components",
    ]


@pytest.mark.parametrize(
    "selector",
    [".mt-1", ".mt-12", ".tabular-nums", ".text-ink", ".font-body", r".sm\:p-8"],
)
def test_render_compiled_css_wp_prefixes_every_utility_with_the_wrapper(
    scoped_css: str, selector: str
) -> None:
    """A utility that stayed unscoped would restyle the whole site.

    Both halves matter: the scoped form must exist (or the fragment
    renders unstyled) and no rule boundary may introduce the bare
    selector, which is the leak into the publishing site.
    """
    assert f".rc-results {selector}{{" in scoped_css
    assert re.search(rf"[{{}};]{re.escape(selector)}\{{", scoped_css) is None


def test_render_compiled_css_wp_scopes_the_custom_rules_and_the_print_block(
    scoped_css: str,
) -> None:
    """theme.css's own classes and the print rule are scoped too.

    ``.bp``/``.chip`` are the page's own widgets and ``.no-print`` is
    the print rule it relies on: a site that happens to use those class
    names inherits nothing. The print block's ``body`` background
    becomes the wrapper's, so a printed fragment whitens as the
    standalone page does.
    """
    assert ".rc-results .bp{" in scoped_css
    assert ".rc-results .bp>i.c:before,.rc-results .bp>i.c:after{" in scoped_css
    assert ".rc-results .chip{" in scoped_css
    assert ".rc-results .chip.j{" in scoped_css
    assert (
        "@media print{.rc-results .no-print{display:none!important}"
        ".rc-results{background:#fff!important}}" in scoped_css
    )


def test_render_compiled_css_wp_neutralizes_the_document_level_selectors(
    scoped_css: str,
) -> None:
    """``html``/``body``/``:root``/``:host`` cannot match in a page.

    Prefixing them would leave dead selectors; dropping the rules would
    lose preflight's defaults for the fragment (line-height 1.5, the
    print background). Each is therefore rewritten to the wrapper:
    scoped, still in force for the fragment, invisible to the site.
    """
    assert re.search(r"\.rc-results\{[^{}]*line-height:1\.5", scoped_css) is not None
    assert re.search(r"\.rc-results (?:html|body|:root|:host)\b", scoped_css) is None
    assert ".rc-results .rc-results" not in scoped_css


@pytest.mark.parametrize(
    "pattern",
    [
        r"[{};]\.[a-z-]",  # a class selector left unscoped
        r"[{};][a-z][-\w]*(?=[{,])",  # an element selector left unscoped
        r"[{};](?:html|body|:root|:host)\b",  # a document-level selector left alone
        r"(?m)^\s*\.[a-z-]",  # a class selector opening a line, with no rule boundary
        r"(?m)^\s*(?:html|body|:root|:host)\b",  # a document-level selector opening a line
    ],
)
def test_render_compiled_css_wp_leaves_no_rule_outside_the_wrapper(
    scoped_css: str, pattern: str
) -> None:
    """The whole-sheet audit: nothing survives the scoping unscoped.

    The wrapper class is masked out first, so a match can only be a
    *second*, unscoped selector sitting at a rule boundary -- and one
    rule like that is a stylesheet that restyles the publishing site's
    theme. The last two patterns drop the boundary requirement: the
    flattened layers put their bodies' rules straight after each
    other's closing braces, and the first rule of a body that lost its
    wrapper would otherwise open a line with nothing in front of it.
    """
    masked = scoped_css.replace(gen_css._WP_SCOPE, "@scope@")

    assert re.findall(pattern, masked) == []


def test_render_compiled_css_wp_prepends_the_scoped_provenance_header(
    scoped_css: str, committed_css: str
) -> None:
    """The artifact names its variant; the source hash is the same.

    The provenance line is what the TB-7 staleness gate reads, so the
    derived artifact carries theme.css's own sha256 and the pinned
    Tailwind version -- plus which variant it is, since two artifacts
    now share that source.
    """
    header = scoped_css.split("\n", 1)[0]

    assert header.startswith("/* rivercrossing compiled_css_wp — generated by tools/gen_css.py; ")
    assert "the WordPress-scoped variant of compiled_css" in header
    assert re.search(r"sha256:[0-9a-f]{64}; tailwindcss \d+\.\d+\.\d+ \*/$", header) is not None
    assert re.search(r"sha256:[0-9a-f]{64}", committed_css).group(0) in header
    assert scoped_css.count("rivercrossing compiled_css_wp") == 1


def test_render_compiled_css_wp_raw_cli_output_without_the_header_raises_value_error() -> None:
    """T-5 negative: raw CLI output is named, never guessed at."""
    with pytest.raises(ValueError, match=re.escape("provenance header")):
        gen_css.render_compiled_css_wp(".bp{position:relative}\n")
