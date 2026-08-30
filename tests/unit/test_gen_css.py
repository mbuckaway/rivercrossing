# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for tools/gen_css.py (E6.2.1).

The vendored CSS build step compiles the frozen Tailwind source
(theme.css + the .j2 templates) once, in CI, into two committed
artifacts under ``src/rivercrossing/htmlexport/templates/``:
``compiled_css`` (the minified Tailwind output with a provenance
header carrying theme.css's sha256 -- the TB-7 staleness gate) and
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
    populated, so the seam is not faked and the committed artifact is
    the expectation. Skipped with a reason when the CLI or Node itself
    is missing, so a machine without Node never fails the suite. The
    Node check is load-bearing on Windows: npm's ``.cmd`` shim calls
    ``node`` by bare name, and a missing PATH entry fails with "'node'
    is not recognized" rather than a clean skip (measured).
    """
    if not gen_css._tailwind_executable().is_file() or shutil.which("node") is None:
        pytest.skip(
            "pinned Tailwind CLI or node not installed (run npm install "
            "and put node on PATH); skipping the real-CLI integration check"
        )
    work_dir = Path(tempfile.mkdtemp(prefix="gen_css-itest-", dir=_ROOT / "build"))
    try:
        compiled_css, fonts_css = gen_css.build_artifacts(_COMMITTED_TEMPLATES_DIR, work_dir)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    assert compiled_css == _COMMITTED_COMPILED_CSS.read_bytes()
    assert fonts_css == _COMMITTED_FONTS_CSS.read_bytes()


# ----------------------------------------- write, idempotence, CLI


@pytest.mark.usefixtures("fake_tailwind_cli")
def test_write_artifacts_idempotent_two_runs_produce_byte_identical_files(
    tmp_path: Path,
) -> None:
    """Regeneration is idempotent: two writes match byte-for-byte."""
    fixture = _fixture_templates_dir(tmp_path / "src")

    gen_css.write_artifacts(fixture, tmp_path / "first")
    gen_css.write_artifacts(fixture, tmp_path / "second")

    assert (tmp_path / "first" / "compiled_css").read_bytes() == (
        tmp_path / "second" / "compiled_css"
    ).read_bytes()
    assert (tmp_path / "first" / "fonts_css").read_bytes() == (
        tmp_path / "second" / "fonts_css"
    ).read_bytes()


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
    """
    fixture = _fixture_templates_dir(tmp_path / "templates")
    monkeypatch.setattr(
        gen_css, "_TAILWIND_CLI", tmp_path / "node_modules" / ".bin" / "tailwindcss"
    )

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
    """The seam's own guard names the install fix (T-5 direct raise)."""
    monkeypatch.setattr(
        gen_css, "_TAILWIND_CLI", tmp_path / "node_modules" / ".bin" / "tailwindcss"
    )

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


def test_fonts_css_contains_both_families_and_all_five_weights() -> None:
    """Both Barlow families ship, each block with its frozen weight."""
    content = _COMMITTED_FONTS_CSS.read_text(encoding="utf-8")

    assert 'font-family: "Barlow";' in content
    assert 'font-family: "Barlow Condensed";' in content
    assert content.count("font-weight:") == 5
    for weight in ("400", "500", "700", "600"):
        assert f"font-weight: {weight};" in content


@pytest.mark.parametrize("name", ["compiled_css", "fonts_css"])
def test_compiled_css_and_fonts_css_have_no_url_http_references(name: str) -> None:
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
