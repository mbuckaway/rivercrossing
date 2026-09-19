# SPDX-License-Identifier: GPL-3.0-only
"""Regenerate ``docs/user-guide.html`` from the guide's markdown + CSS.

The in-app user guide (``Help > User Guide``, F1) is one
self-contained HTML page: its prose lives in ``docs/user-guide.md``,
its styles in ``docs/user-guide.css``, and this tool renders the two
into the committed ``docs/user-guide.html`` -- inlined CSS, no
external fetches, so the page opens offline.

The output is byte-deterministic: no timestamps, no "generated on"
comment and no file paths leak into it, so the same markdown and CSS
always produce the same bytes and a CI drift check stays green.

    python tools/gen_userguide.py --write   # regenerate the page
    python tools/gen_userguide.py --check   # fail the build on drift

Both flags accept ``--markdown``/``--css``/``--out`` overrides so
tests can point the generator at fixtures instead of the real tree.
"""

import argparse
import sys
from collections.abc import Sequence  # noqa: TC003 -- dev CLI, no perf-sensitive import path
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "docs" / "user-guide.md"
CSS = ROOT / "docs" / "user-guide.css"
HTML = ROOT / "docs" / "user-guide.html"

DEFAULT_TITLE = "RiverCrossing — User Guide"
DEFAULT_LANG = "en"

_BODY_EXTENSIONS = ("meta", "attr_list", "tables", "fenced_code")

# The guide's shortcut table writes its modifier as
# ``<span class="mod">Ctrl</span>``: correct for Windows, wrong for the
# Macs and iOS devices the app also ships to. The markdown cannot know
# the reader's platform, so the shell carries a tiny inline script that
# rewrites every ``span.mod`` to ⌘ when the page opens on an Apple
# platform and leaves the markdown's ``Ctrl`` alone otherwise. Inline
# and dependency-free, because the page must open offline; defensive,
# because a guide that throws is worse than a small wrong modifier.
# Every literal brace in that script is doubled: ``_SHELL`` goes
# through ``str.format``, so a single ``{`` would raise at render time.
_SHELL = """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{css}
</style>
</head>
<body>
<main>
{body}
</main>
<script>
try {{
  var data = navigator.userAgentData;
  var platform = (data && data.platform) || navigator.platform || navigator.userAgent || "";
  if (/Mac|iPhone|iPad|iPod|iOS/i.test(platform)) {{
    var mods = document.querySelectorAll("span.mod");
    for (var i = 0; i < mods.length; i++) {{ mods[i].textContent = "⌘"; }}
  }}
}} catch (error) {{
  /* Unknown platform: the markdown's Ctrl stays the reader's default. */
}}
</script>
</body>
</html>
"""


def _first_value(meta: dict[str, list[str]], key: str, default: str) -> str:
    """Return *key*'s first value, or *default* when absent."""
    values = meta.get(key)
    return values[0] if values else default


def extract_meta(markdown_text: str) -> dict[str, str]:
    """Return the guide's YAML front matter as plain strings.

    python-markdown's ``meta`` extension parses the ``---`` front
    matter into a ``{key: [values]}`` mapping; every value here is
    the single string the shell needs, defaulting when a key is
    absent.
    """
    parser = markdown.Markdown(extensions=["meta"])
    parser.convert(markdown_text)
    meta: dict[str, list[str]] = parser.Meta
    return {
        "title": _first_value(meta, "title", DEFAULT_TITLE),
        "lang": _first_value(meta, "lang", DEFAULT_LANG),
    }


def render(markdown_text: str, css_text: str) -> str:
    """Render the complete self-contained user-guide page.

    The body is rendered with the ``meta`` (front matter),
    ``attr_list`` (explicit heading anchors), ``tables`` and
    ``fenced_code`` extensions; raw block HTML such as the contents
    ``<nav class="toc">`` passes through verbatim. The result is
    byte-deterministic for a given markdown/CSS pair.
    """
    meta = extract_meta(markdown_text)
    body = markdown.markdown(markdown_text, extensions=_BODY_EXTENSIONS)
    return _SHELL.format(lang=meta["lang"], title=meta["title"], css=css_text, body=body)


def _render_from_paths(markdown_path: Path, css_path: Path) -> str:
    """Render the page from the markdown and CSS files at *paths*."""
    return render(
        markdown_path.read_text(encoding="utf-8"),
        css_path.read_text(encoding="utf-8"),
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``--write``/``--check`` argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate user-guide.html")
    mode.add_argument("--check", action="store_true", help="fail the build on drift")
    parser.add_argument("--markdown", type=Path, default=MD)
    parser.add_argument("--css", type=Path, default=CSS)
    parser.add_argument("--out", type=Path, default=HTML)
    return parser


def _run_write(markdown_path: Path, css_path: Path, out_path: Path) -> int:
    """Regenerate *out_path* and report what was written.

    The page is written with LF line endings on every platform, so
    the committed artifact and the drift check agree byte for byte.
    """
    html = _render_from_paths(markdown_path, css_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8", newline="\n")
    print(f"wrote {out_path} from {markdown_path} and {css_path}")
    return 0


def _run_check(markdown_path: Path, css_path: Path, out_path: Path) -> int:
    """Report drift between a fresh render and *out_path*."""
    html = _render_from_paths(markdown_path, css_path)
    if not out_path.exists():
        print(f"drift: {out_path} is missing")
        return 1
    if out_path.read_bytes() != html.encode("utf-8"):
        print(f"drift: {out_path} does not match a fresh render of {markdown_path}")
        return 1
    print(f"{out_path} matches {markdown_path} and {css_path}")
    return 0


def _error_message(exc: OSError | UnicodeDecodeError) -> str:
    """Render *exc* as one line naming the path it could not read.

    ``str(OSError)`` embeds ``repr(exc.filename)``, which doubles
    every backslash, so on Windows the raw path is *not* a substring
    of the text printed. ``OSError.filename`` holds the path
    un-repr'd, so lead with that whenever there is one.
    ``UnicodeDecodeError`` has no ``filename`` and keeps the
    exception's own text.
    """
    if isinstance(exc, OSError) and exc.filename:
        return f"{exc.filename}: {exc}"
    return str(exc)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI: dispatch to ``--write`` or ``--check``."""
    args = _build_parser().parse_args(argv)
    try:
        if args.write:
            return _run_write(args.markdown, args.css, args.out)
        return _run_check(args.markdown, args.css, args.out)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: {_error_message(exc)}", file=sys.stderr)
        return 2


# logic-coverage-exempt: T-3 unreachable when loaded by
# spec_from_file_location -- the repo's established test pattern never
# executes this module as __main__, so the guard's True branch is
# never taken under coverage.
if __name__ == "__main__":
    raise SystemExit(main())
