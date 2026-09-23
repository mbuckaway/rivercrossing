# SPDX-License-Identifier: GPL-3.0-only
"""Vendor the results-page CSS and Barlow font subsets (E6.2.1).

The results page (``base.html.j2``) is a self-contained offline file:
its styles are compiled once, at package build, by the pinned
Tailwind v4 CLI against the frozen templates (``theme.css`` + the
``.j2`` files' classes), and its Barlow typefaces ship as base64
``@font-face`` blocks. This generator produces three committed
artifacts under ``src/rivercrossing/htmlexport/templates/``:

* ``compiled_css`` -- the minified Tailwind output with a provenance
  header recording theme.css's sha256 (the TB-7 staleness gate: CI
  fails if the committed CSS was built from different source).
* ``compiled_css_wp`` -- that stylesheet with every selector scoped
  under ``.rc-results`` and its ``@layer theme``/``base``/
  ``utilities`` wrappers flattened: the CSS
  ``htmlexport.render_wordpress`` publishes inside a WordPress page's
  content, where it has to beat the host theme's own unlayered rules.
  Derived from ``compiled_css``, never a second compile.
* ``fonts_css`` -- ``@font-face`` blocks embedding the five Barlow
  woff2 subsets, deterministically ordered, no timestamps.

    python tools/gen_css.py --write   # regenerate all three artifacts
    python tools/gen_css.py --check   # fail the build on drift

Both flags accept ``--templates-dir``/``--out-dir`` overrides so
tests can point the generator at fixture files instead of the real
tree. ``--check`` passes vacuously when the templates are absent --
the same guard ``nox -s ids_drift`` applies to the .xrc tree.
"""

import argparse
import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence  # noqa: TC003 -- dev CLI, no perf-sensitive import path
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATES_DIR = _ROOT / "src" / "rivercrossing" / "htmlexport" / "templates"
DEFAULT_OUT_DIR = DEFAULT_TEMPLATES_DIR

COMPILED_CSS_NAME = "compiled_css"
COMPILED_CSS_WP_NAME = "compiled_css_wp"
FONTS_CSS_NAME = "fonts_css"

# The three artifacts, in the order build_artifacts returns them.
_ARTIFACT_NAMES: tuple[str, ...] = (COMPILED_CSS_NAME, COMPILED_CSS_WP_NAME, FONTS_CSS_NAME)

# The three scanned template files; the wrapper below needs theme.css
# (it holds the @theme tokens and custom rules), and the .j2 files are
# scanned by the CLI's automatic content detection for utility classes.
# poster.html.j2 and wordpress.html.j2 are deliberately absent: the
# podium poster reuses the results page's widget tokens (its card
# markup duplicates macros' classes), and the WordPress fragment
# renders the same macros into a wrapper div -- so a new utility in
# either must be added here, and the artifacts regenerated, or the
# class renders unstyled.
INPUT_TEMPLATE_FILES: tuple[str, ...] = ("base.html.j2", "macros.html.j2", "theme.css")

# Pinned CLI, matching package.json's devDependencies (@tailwindcss/cli
# 4.3.3). The provenance header below names this version so a pin bump
# shows up in the regenerated artifact.
_TAILWIND_CLI = _ROOT / "node_modules" / ".bin" / "tailwindcss"
_TAILWIND_VERSION = "4.3.3"


def _tailwind_executable() -> Path:
    """Return the npm shim this platform can actually execute.

    npm installs three shims per bin: ``tailwindcss`` (POSIX shell
    script), ``tailwindcss.cmd`` (cmd.exe shim) and ``tailwindcss.ps1``.
    Windows cannot execute the extensionless script -- CreateProcess
    raises WinError 193 -- so resolve the ``.cmd`` sibling there and
    fall back to the plain shim when npm left no sibling behind.
    """
    if os.name == "nt":
        cmd_shim = _TAILWIND_CLI.with_suffix(".cmd")
        if cmd_shim.is_file():
            return cmd_shim
    return _TAILWIND_CLI


def _missing_tailwind_cli_message() -> str:
    """Return the CLI-missing error text that names the install fix."""
    return (
        f"pinned Tailwind CLI not found at {_tailwind_executable()} -- "
        f"run `npm install` first (pins @tailwindcss/cli {_TAILWIND_VERSION})"
    )


def _ensure_tailwind_cli() -> None:
    """Install the pinned Tailwind CLI when it is missing.

    ``node_modules`` is gitignored, so a fresh clone or ``git
    worktree`` has no CLI and the CSS build dies with a bare "run npm
    install first". Install it from the lockfile with ``npm ci`` at the
    repo root so the tool self-heals; a no-op once the CLI exists.

    Raises:
        TailwindCliMissingError: npm is not on ``PATH``, ``npm ci``
            exited non-zero, or the CLI is still absent afterwards.
    """
    if _tailwind_executable().is_file():
        return
    npm = shutil.which("npm")
    if npm is None:
        raise TailwindCliMissingError(_missing_tailwind_cli_message())
    print(f"Tailwind CLI missing at {_tailwind_executable()} -- running `npm ci`")
    try:
        subprocess.run(  # noqa: S603 -- absolute path from which(), fixed argv list, no shell
            [npm, "ci"],
            cwd=_ROOT,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise TailwindCliMissingError(_missing_tailwind_cli_message()) from exc
    if not _tailwind_executable().is_file():
        raise TailwindCliMissingError(_missing_tailwind_cli_message())


# The wrapper is what makes theme.css a valid Tailwind v4 input: a
# bare @theme file has no `@import "tailwindcss";`, so Tailwind would
# not treat it as a stylesheet entry point.
_WRAPPER_CSS = '@import "tailwindcss";\n@import "./theme.css";\n'

# (filename, font family, font weight) -- the frozen typeface set, in
# stable output order (Spec section 8: Barlow 400/500/700 and Barlow
# Condensed 400/600, always upright).
FONT_FILES: tuple[tuple[str, str, int], ...] = (
    ("barlow-400.woff2", "Barlow", 400),
    ("barlow-500.woff2", "Barlow", 500),
    ("barlow-700.woff2", "Barlow", 700),
    ("barlow-condensed-400.woff2", "Barlow Condensed", 400),
    ("barlow-condensed-600.woff2", "Barlow Condensed", 600),
)

_COMPILED_CSS_HEADER = (
    "/* rivercrossing compiled_css — generated by tools/gen_css.py; "
    "source theme.css sha256:{sha256}; tailwindcss {version} */"
)


class TailwindCliMissingError(RuntimeError):
    """Raised when the pinned Tailwind CLI is not installed."""


class TailwindCompileError(RuntimeError):
    """Raised when the Tailwind CLI exits non-zero."""


class TailwindTimeoutError(RuntimeError):
    """Raised when the pinned Tailwind CLI does not finish in time."""


def _sha256_hex(path: Path) -> str:
    """Return the lowercase hex sha256 of *path*."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def data_font_uri(data: bytes) -> str:
    """Return the ``src`` value for one woff2 ``@font-face``."""
    encoded = base64.b64encode(data).decode("ascii")
    return f'url(data:font/woff2;base64,{encoded}) format("woff2")'


def render_font_face(family: str, weight: int, src: str) -> str:
    """Render one ``@font-face`` block for *family*/*weight*/*src*."""
    return (
        "@font-face {\n"
        f'  font-family: "{family}";\n'
        "  font-style: normal;\n"
        f"  font-weight: {weight};\n"
        f"  src: {src};\n"
        "}"
    )


def _fonts_provenance(fonts_dir: Path) -> str:
    """Return the provenance comment naming every font file's sha256."""
    parts = " ".join(
        f"{name} sha256:{_sha256_hex(fonts_dir / name)}" for name, _family, _weight in FONT_FILES
    )
    return f"/* rivercrossing fonts_css — generated by tools/gen_css.py; {parts} */"


def render_fonts_css(fonts_dir: Path) -> str:
    """Render ``@font-face`` blocks for the woff2 files in *fonts_dir*.

    Deterministic: files are read in :data:`FONT_FILES` order, the
    provenance comment records each file's sha256, and no timestamps
    or paths leak into the output.
    """
    blocks = [_fonts_provenance(fonts_dir)]
    for name, family, weight in FONT_FILES:
        uri = data_font_uri((fonts_dir / name).read_bytes())
        blocks.append(render_font_face(family, weight, uri))
    return "\n".join(blocks) + "\n"


def render_compiled_css(compiled: bytes, theme_sha256: str) -> bytes:
    """Prefix the Tailwind output with the TB-7 provenance header."""
    header = _COMPILED_CSS_HEADER.format(sha256=theme_sha256, version=_TAILWIND_VERSION).encode(
        "utf-8"
    )
    return header + b"\n" + compiled


_COMPILED_CSS_WP_HEADER = (
    "/* rivercrossing compiled_css_wp — generated by tools/gen_css.py; the "
    "WordPress-scoped variant of compiled_css; source theme.css sha256:{sha256}; "
    "tailwindcss {version} */"
)

# Reads the sha256 and pinned version back out of _COMPILED_CSS_HEADER:
# the two artifacts record the same compile, and a reworded header has
# to fail loudly here rather than ship a provenance line nobody can
# check.
_PROVENANCE_RE = re.compile(
    r"/\* rivercrossing compiled_css — generated by tools/gen_css\.py; "
    r"source theme\.css sha256:(?P<sha256>[0-9a-f]{64}); tailwindcss (?P<version>[^ ]+) \*/"
)

# The wrapper class the WordPress fragment carries: the unscoped
# artifact restyles a publishing site's theme, so every rule has to
# match only inside this element.
_WP_SCOPE = ".rc-results"

# Selectors that can never match inside a page's content. They are
# rewritten to the wrapper itself rather than dropped: dropping
# preflight's html rule would take line-height 1.5 and the default font
# stack off the fragment with it, and dropping the print block's body
# would leave the fragment paper-coloured on paper where the standalone
# page whitens.
_UNREACHABLE_SELECTORS = frozenset({"body", "html", ":root", ":host"})

# At-rules whose bodies hold further rules, so the walk recurses into
# them. Everything else -- @property, @font-face, @keyframes -- is
# emitted verbatim: @property registers a custom property globally and
# has no selector to scope, and keyframe preludes are percentages or
# from/to, never elements.
_NESTED_AT_RULES = frozenset({"@container", "@layer", "@media", "@scope", "@supports"})

# Layers whose wrapper the WordPress variant drops, emitting the body
# unlayered in its original position. Tailwind emits its stylesheet
# inside these three, and an unlayered author declaration beats a
# layered one whatever the specificity -- so with them intact a
# publishing theme's plain ``table{border:6px dashed lime;width:60%}``
# outranked the fragment's own scoped ``.rc-results
# .w-full{width:100%}`` and the published page rendered theme-mangled
# (measured in a real browser against a mock theme). Flattened, the
# fragment's rules compete on specificity again, which is the cascade
# they were written for. ``@layer properties`` is deliberately left
# alone: it holds only the ``--tw-*`` initial values, it was the
# stylesheet's first layer so everything else already outranked it,
# and it carries no selector a host theme's CSS could collide with.
_FLATTENED_LAYERS = frozenset({"theme", "base", "utilities"})

_AT_KEYWORD_RE = re.compile(r"@[-\w]+")
_LAYER_NAME_RE = re.compile(r"@layer\s+(?P<name>[-\w]+)")
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _after_comment(css: str, start: int) -> int:
    """Return the index just past the comment starting at *start*."""
    end = css.find("*/", start + 2)
    return len(css) if end < 0 else end + 2


def _after_string(css: str, start: int) -> int:
    """Return the index just past the string starting at *start*."""
    quote = css[start]
    i = start + 1
    while i < len(css) and css[i] != quote:
        i += 2 if css[i] == "\\" else 1
    return min(i + 1, len(css))


def _scan_prelude(css: str, start: int) -> tuple[int, str]:
    """Return the index of the prelude's terminator, and the terminator.

    A prelude runs from *start* to the first ``{`` or ``;`` outside
    parentheses, brackets and quoted strings, so a selector such as
    ``:where(select:is([multiple],[size]))`` or an ``@supports``
    condition never ends it early. The terminator is ``""`` at the end
    of the text.
    """
    depth = 0
    i = start
    while i < len(css):
        if css.startswith("/*", i):
            i = _after_comment(css, i)
        elif css[i] in "\"'":
            i = _after_string(css, i)
        elif css[i] in "([":
            depth += 1
            i += 1
        elif css[i] in ")]":
            depth -= 1
            i += 1
        elif depth == 0 and css[i] in "{;":
            return i, css[i]
        else:
            i += 1
    return len(css), ""


def _block_end(css: str, open_index: int) -> int:
    """Return the index of the ``}`` closing *open_index*'s ``{``."""
    depth = 0
    i = open_index
    while i < len(css):
        if css.startswith("/*", i):
            i = _after_comment(css, i)
            continue
        if css[i] in "\"'":
            i = _after_string(css, i)
            continue
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(css)


def _split_selector_list(prelude: str) -> list[str]:
    """Split one selector list on its top-level commas.

    Commas inside ``:is(...)``/``:where(...)`` or a quoted attribute
    value belong to the selector they sit in, so the split tracks
    parentheses, brackets and strings.
    """
    selectors: list[str] = []
    start = 0
    depth = 0
    i = 0
    while i < len(prelude):
        if prelude.startswith("/*", i):
            i = _after_comment(prelude, i)
            continue
        if prelude[i] in "\"'":
            i = _after_string(prelude, i)
            continue
        if prelude[i] in "([":
            depth += 1
        elif prelude[i] in ")]":
            depth -= 1
        elif prelude[i] == "," and depth == 0:
            selectors.append(prelude[start:i])
            start = i + 1
        i += 1
    selectors.append(prelude[start:])
    return selectors


def _scope_selector_list(prelude: str) -> str:
    """Return one selector list with every selector scoped.

    Identical rewrites collapse: ``:root,:host`` and ``html,:host`` both
    become the wrapper alone, so no rule carries the same selector
    twice.
    """
    scoped: list[str] = []
    for selector in _split_selector_list(prelude):
        stripped = selector.strip()
        if not stripped:
            continue
        rewritten = (
            _WP_SCOPE if stripped.lower() in _UNREACHABLE_SELECTORS else f"{_WP_SCOPE} {stripped}"
        )
        if rewritten not in scoped:
            scoped.append(rewritten)
    return ",".join(scoped)


def _at_rule_keyword(prelude: str) -> str:
    """Return the at-rule keyword *prelude* opens with, "" for a rule.

    Comments are stripped first: the Tailwind banner shares its prelude
    with the first ``@layer``, and a prelude of a style rule is
    otherwise a plain selector list.
    """
    cleaned = _COMMENT_RE.sub(" ", prelude).lstrip()
    if not cleaned.startswith("@"):
        return ""
    match = _AT_KEYWORD_RE.match(cleaned)
    return match.group(0) if match else cleaned


def _layer_name(prelude: str) -> str:
    """Return the first layer name a ``@layer`` prelude declares.

    "" for a prelude that declares none. Only ever asked about a
    prelude that opens a block, so the name is the block's layer;
    comments are stripped the way :func:`_at_rule_keyword` strips them.
    """
    match = _LAYER_NAME_RE.match(_COMMENT_RE.sub(" ", prelude).lstrip())
    return match.group("name") if match else ""


def _rewrite_rule(prelude: str, body: str, *, nested: bool) -> str:
    """Return one rule, its selector list scoped unless *nested*.

    A nested rule needs no prefix: its parent's selector already
    carries the wrapper, and a prelude like ``&:hover`` or ``> i`` is
    relative to that parent. A layer named in
    :data:`_FLATTENED_LAYERS` loses its wrapper and keeps its scoped
    body, so its rules sit unlayered at the wrapper's own level.
    """
    keyword = _at_rule_keyword(prelude)
    if keyword == "@layer" and _layer_name(prelude) in _FLATTENED_LAYERS:
        return _scope_block(body, nested=nested)
    if keyword in _NESTED_AT_RULES:
        return f"{prelude}{{{_scope_block(body, nested=nested)}}}"
    if keyword:
        return f"{prelude}{{{body}}}"
    selector = prelude if nested else _scope_selector_list(prelude)
    return f"{selector}{{{_scope_block(body, nested=True)}}}"


def _scope_block(css: str, *, nested: bool) -> str:
    """Return one rule-list body with every selector scoped.

    The walk is textual and lossless: comments, whitespace and every
    declaration come back byte for byte, and only the selector list of
    a rule that introduces a block is rewritten.
    """
    out: list[str] = []
    i = 0
    while i < len(css):
        if css.startswith("/*", i):
            end = _after_comment(css, i)
            out.append(css[i:end])
            i = end
            continue
        if css[i].isspace():
            out.append(css[i])
            i += 1
            continue
        end, delimiter = _scan_prelude(css, i)
        prelude = css[i:end]
        if delimiter == "{":
            close = _block_end(css, end)
            out.append(_rewrite_rule(prelude, css[end + 1 : close], nested=nested))
            i = close + 1
            continue
        # A declaration terminator (as in `@layer components;`) or the
        # end of the text: nothing here has a selector list to rewrite.
        out.append(f"{prelude}{delimiter}")
        i = end + len(delimiter)
    return "".join(out)


def render_compiled_css_wp(compiled: str) -> bytes:
    """Return the WordPress-scoped variant of the compiled stylesheet.

    The fragment :func:`rivercrossing.htmlexport.render_wordpress`
    publishes lands in a WordPress page's ``content``, beside that
    site's own theme, so every rule it carries must match only inside
    the wrapper the fragment is built from -- and must still win there.
    Wrapping the whole stylesheet in ``@scope (.rc-results) {…}``
    cannot express that: the theme tokens sit in a rule whose selector
    is ``:root,:host`` -- reachable only by rewriting the selector --
    and the nine ``@property`` registrations that follow are at-rules
    with no selector at all, which no wrapper can scope. The transform
    is a selector rewrite plus a layer flattening:

    * the ``@property`` registrations stay at the stylesheet's top
      level -- they are global by definition, and the fragment's own
      ``--tw-*`` fallbacks need them in force;
    * the ``@layer theme``/``@layer base``/``@layer utilities``
      wrappers are dropped, their contents emitted unlayered in that
      source order. An unlayered author declaration beats a layered one
      whatever the specificity, so a host theme's plain
      ``table{width:60%}`` used to override the fragment's scoped
      ``.rc-results .w-full{width:100%}``; unlayered, the fragment's
      rules compete on specificity instead (measured in a real browser
      against a mock theme). Flattening preserves the order -- theme,
      base, utilities -- so the utilities still win over preflight;
    * the theme tokens move from ``:root,:host`` onto ``.rc-results``
      itself, declared once for the wrapper and inherited by every
      element inside it;
    * every other selector list gains a ``.rc-results `` prefix --
      preflight, the utilities and the custom ``.bp``/``.chip`` rules
      alike -- while ``html``/``body``/``:root``/``:host``, which can
      never match inside a page's content, become the wrapper, so
      preflight's line-height and the print block's white background
      still reach the fragment.

    Args:
        compiled: The committed ``compiled_css`` artifact's text: the
            provenance header, the Tailwind banner and the minified CSS.

    Returns:
        The scoped stylesheet bytes, carrying their own provenance
        header.

    Raises:
        ValueError: *compiled* does not open with the compiled_css
            provenance header, so it is not the artifact this function
            derives from.
    """
    match = _PROVENANCE_RE.match(compiled)
    if match is None:
        msg = (
            "compiled CSS does not open with the gen_css provenance header; "
            "pass the compiled_css artifact, not raw CLI output"
        )
        raise ValueError(msg)
    header = _COMPILED_CSS_WP_HEADER.format(
        sha256=match.group("sha256"), version=match.group("version")
    )
    scoped = _scope_block(compiled[match.end() :], nested=False)
    return (header + "\n" + scoped).encode("utf-8")


def _run_tailwind_cli(argv: Sequence[str]) -> bytes:
    """Run the pinned Tailwind CLI, returning its stdout bytes.

    The CLI writes the compiled CSS to the ``-o`` output file; the
    caller reads that file for the artifact bytes. The return value
    (stdout) is empty in that mode -- the seam exists so tests can
    substitute the binary without Node being installed.

    Raises:
        TailwindCliMissingError: The pinned CLI is not installed and
            could not be bootstrapped with ``npm ci``.
        TailwindCompileError: The CLI exited non-zero.
        TailwindTimeoutError: The CLI did not finish within 120s.
    """
    _ensure_tailwind_cli()
    try:
        proc = subprocess.run(  # noqa: S603 -- absolute path, fixed argv list, no shell
            [str(_tailwind_executable()), *argv],
            cwd=_ROOT,
            capture_output=True,
            check=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"tailwindcss did not finish within {exc.timeout}s"
        raise TailwindTimeoutError(msg) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        msg = f"tailwindcss exited {exc.returncode}: {stderr}"
        raise TailwindCompileError(msg) from exc
    return proc.stdout


def _make_work_dir() -> Path:
    """Create a scratch dir under build/ for the wrapper + output.

    The wrapper does ``@import "tailwindcss";``, which Node resolves
    by walking up from the importing file, so the scratch dir must sit
    inside the repo (under the gitignored build/ tree) for the pinned
    CLI to find its own package.
    """
    build_dir = _ROOT / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="gen_css-", dir=build_dir))


def build_artifacts(templates_dir: Path, work_dir: Path) -> tuple[bytes, bytes, bytes]:
    """Compile and render all three artifacts from *templates_dir*.

    *work_dir* is a caller-owned scratch directory that receives the
    Tailwind wrapper, copies of the three input templates, and the
    CLI's output file. ``--cwd`` pins the CLI to *work_dir*, so the
    automatic content detection scans exactly those three files --
    nothing else in the repo. Measured: the repo-wide fallback scan
    feeds the committed artifacts (and any stray non-gitignored file)
    back into the candidate set, so a fresh build never reproduces the
    committed ``compiled_css`` and the drift gate can never pass.
    Returns ``(compiled_css, compiled_css_wp, fonts_css)`` bytes,
    deterministic for a given tree; the scoped artifact is derived from
    the compiled one here rather than compiled a second time, so the
    two cannot disagree about what the CLI produced.

    Raises:
        TailwindCliMissingError: The pinned CLI is not installed.
        TailwindCompileError: The CLI exited non-zero.
        TailwindTimeoutError: The CLI did not finish within 120s.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "wrapper.css").write_text(_WRAPPER_CSS, encoding="utf-8")
    for name in INPUT_TEMPLATE_FILES:
        shutil.copyfile(templates_dir / name, work_dir / name)
    _run_tailwind_cli(
        ["--cwd", str(work_dir), "-i", "wrapper.css", "-o", "compiled.css", "--minify"]
    )
    compiled_css = render_compiled_css(
        (work_dir / "compiled.css").read_bytes(), _sha256_hex(templates_dir / "theme.css")
    )
    compiled_css_wp = render_compiled_css_wp(compiled_css.decode("utf-8"))
    fonts_css = render_fonts_css(templates_dir / "fonts").encode("utf-8")
    return compiled_css, compiled_css_wp, fonts_css


def write_artifacts(templates_dir: Path, out_dir: Path) -> tuple[Path, Path, Path]:
    """Regenerate all three artifacts from *templates_dir*.

    They are written into *out_dir*; the return value is the three
    paths, in artifact order. Byte-identical regeneration is the
    contract: the same tree always produces the same bytes.
    """
    work_dir = _make_work_dir()
    try:
        compiled_css, compiled_css_wp, fonts_css = build_artifacts(templates_dir, work_dir)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    compiled_path = out_dir / COMPILED_CSS_NAME
    scoped_path = out_dir / COMPILED_CSS_WP_NAME
    fonts_path = out_dir / FONTS_CSS_NAME
    compiled_path.write_bytes(compiled_css)
    scoped_path.write_bytes(compiled_css_wp)
    fonts_path.write_bytes(fonts_css)
    return compiled_path, scoped_path, fonts_path


def _templates_present(templates_dir: Path) -> bool:
    """Return True when every generator input template exists."""
    return all((templates_dir / name).is_file() for name in INPUT_TEMPLATE_FILES)


def drift_lines(committed: Path, regenerated: bytes, name: str) -> list[str]:
    """Return drift lines for one artifact, [] when identical."""
    if not committed.exists():
        return [f"{name} missing from {committed.parent}"]
    if committed.read_bytes() != regenerated:
        return [f"{name} drifted from {committed}"]
    return []


def check_artifacts(templates_dir: Path, out_dir: Path) -> list[str]:
    """Regenerate from *templates_dir* and diff against *out_dir*.

    Runs the real Tailwind CLI via the seam; this single call is both
    the CI compile and the TB-7 staleness gate -- for the compiled
    artifact and for the scoped variant derived from it in the same
    run. Returns drift lines, [] when all three committed artifacts
    match a fresh build.
    """
    work_dir = _make_work_dir()
    try:
        compiled_css, compiled_css_wp, fonts_css = build_artifacts(templates_dir, work_dir)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return [
        *drift_lines(out_dir / COMPILED_CSS_NAME, compiled_css, COMPILED_CSS_NAME),
        *drift_lines(out_dir / COMPILED_CSS_WP_NAME, compiled_css_wp, COMPILED_CSS_WP_NAME),
        *drift_lines(out_dir / FONTS_CSS_NAME, fonts_css, FONTS_CSS_NAME),
    ]


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``--write``/``--check`` argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the vendored CSS")
    mode.add_argument("--check", action="store_true", help="fail the build on drift")
    parser.add_argument("--templates-dir", type=Path, default=DEFAULT_TEMPLATES_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser


def _run_write(templates_dir: Path, out_dir: Path) -> int:
    """Regenerate all three artifacts and report what was written."""
    paths = write_artifacts(templates_dir, out_dir)
    written = ", ".join(str(path) for path in paths)
    print(f"wrote {written} from {templates_dir}")
    return 0


def _run_check(templates_dir: Path, out_dir: Path) -> int:
    """Report drift between a fresh build and the committed artifacts.

    Passes vacuously when the templates do not exist yet, mirroring
    the ``ids_drift`` session's guard.
    """
    if not templates_dir.is_dir() or not _templates_present(templates_dir):
        print(f"{templates_dir} has no templates yet - nothing to check")
        return 0
    diffs = check_artifacts(templates_dir, out_dir)
    if not diffs:
        matched = ", ".join(str(out_dir / name) for name in _ARTIFACT_NAMES)
        print(f"{matched} match {templates_dir}")
        return 0
    for line in diffs:
        print(f"drift: {line}")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI: dispatch to ``--write`` or ``--check``."""
    args = _build_parser().parse_args(argv)
    try:
        if args.write:
            return _run_write(args.templates_dir, args.out_dir)
        return _run_check(args.templates_dir, args.out_dir)
    except (TailwindCliMissingError, TailwindCompileError, TailwindTimeoutError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


# logic-coverage-exempt: T-3 unreachable when loaded by
# spec_from_file_location -- the repo's established test pattern never
# executes this module as __main__, so the guard's True branch is
# never taken under coverage.
if __name__ == "__main__":
    sys.exit(main())
