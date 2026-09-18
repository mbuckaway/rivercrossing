# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for tools/gen_userguide.py.

The user guide ships as one self-contained HTML page, regenerated from
``docs/user-guide.md`` + ``docs/user-guide.css`` by a generator whose
output is byte-deterministic -- no timestamps, no generated-on comment,
no file paths -- so CI can gate the committed page on drift. These
tests are that generator's specification, written before
``tools/gen_userguide.py`` existed.

``tools/`` is a dev-script tree, not an installed package (it has no
``__init__.py`` and is excluded from ``[tool.setuptools.packages.
find]``), so the module under test is loaded from its file path --
the pattern test_ids_gen.py established for tools/gen_ids.py.

Every CLI test passes ``--markdown``/``--css``/``--out``, so no test
touches the real ``docs/`` tree and the suite is independent of the
markdown phase that owns ``docs/user-guide.md``.
"""

import importlib.util
import re
from pathlib import Path
from types import ModuleType  # noqa: TC003 -- used at runtime as a return type here

import pytest

from rivercrossing.hands import HandClass
from rivercrossing.ui import help as help_module

_GEN_USERGUIDE_PATH = Path(__file__).resolve().parents[2] / "tools" / "gen_userguide.py"
_DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"

DEFAULT_TITLE = "RiverCrossing — User Guide"
TITLE = "Snowflake Classic Guide"

GUIDE_MARKDOWN = (
    "---\n"
    f"title: {TITLE}\n"
    "lang: fr\n"
    "---\n"
    "\n"
    "# RiverCrossing — User Guide\n"
    "\n"
    '<nav class="toc">\n'
    "  <ol>\n"
    '    <li><a href="#settings">Settings</a></li>\n'
    "  </ol>\n"
    "</nav>\n"
    "\n"
    "## Settings {: #settings }\n"
    "\n"
    "Set your name.\n"
)

GUIDE_CSS = "body { color: #1d1f20; }"

# The markdown-rendered body GUIDE_MARKDOWN must produce:
# python-markdown passes the raw block HTML through verbatim and
# gives the explicit heading anchor its id.
_EXPECTED_BODY = (
    "<h1>RiverCrossing — User Guide</h1>\n"
    '<nav class="toc">\n'
    "  <ol>\n"
    '    <li><a href="#settings">Settings</a></li>\n'
    "  </ol>\n"
    "</nav>\n"
    "\n"
    '<h2 id="settings">Settings</h2>\n'
    "<p>Set your name.</p>"
)

# The frozen shell, copied into this test on purpose: it is the contract
# the generator must satisfy, not a restatement of its own template.
_EXPECTED_SHELL = """<!DOCTYPE html>
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
</body>
</html>
"""


def _load_gen_userguide(path: Path) -> ModuleType:
    """Load tools/gen_userguide.py by path -- it isn't a package."""
    spec = importlib.util.spec_from_file_location("gen_userguide", path)
    if spec is None or spec.loader is None:
        msg = f"could not build a module spec for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen_userguide = _load_gen_userguide(_GEN_USERGUIDE_PATH)


def _expected_document() -> str:
    """Render the frozen shell with the guide fixture's values."""
    return _EXPECTED_SHELL.format(title=TITLE, lang="fr", css=GUIDE_CSS, body=_EXPECTED_BODY)


def _cli_args(mode: str, paths: tuple[Path, Path, Path]) -> list[str]:
    """Build a ``--markdown``/``--css``/``--out`` CLI argument list."""
    markdown_path, css_path, out_path = paths
    return [
        mode,
        "--markdown",
        str(markdown_path),
        "--css",
        str(css_path),
        "--out",
        str(out_path),
    ]


@pytest.fixture
def guide_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write the fixture markdown + CSS to tmp and name the out path."""
    markdown_path = tmp_path / "user-guide.md"
    css_path = tmp_path / "user-guide.css"
    markdown_path.write_text(GUIDE_MARKDOWN, encoding="utf-8")
    css_path.write_text(GUIDE_CSS, encoding="utf-8")
    return markdown_path, css_path, tmp_path / "user-guide.html"


# ------------------------------------------------------------- render


def test_render_called_twice_returns_identical_output() -> None:
    """Regeneration is deterministic for the CI drift check."""
    first = gen_userguide.render(GUIDE_MARKDOWN, GUIDE_CSS)

    second = gen_userguide.render(GUIDE_MARKDOWN, GUIDE_CSS)

    assert first == second


def test_render_matches_the_frozen_html_shell_for_the_guide_fixture() -> None:
    """The page is exactly the shell -- title, CSS, body in main."""
    expected = _expected_document()

    html = gen_userguide.render(GUIDE_MARKDOWN, GUIDE_CSS)

    assert html == expected


def test_render_front_matter_title_lands_in_the_title_element() -> None:
    """The front matter's ``title`` becomes the document title."""
    html = gen_userguide.render(GUIDE_MARKDOWN, GUIDE_CSS)

    assert f"<title>{TITLE}</title>" in html


def test_render_front_matter_lang_lands_in_the_html_lang_attribute() -> None:
    """The front matter's ``lang`` becomes the ``<html lang=...>``."""
    html = gen_userguide.render(GUIDE_MARKDOWN, GUIDE_CSS)

    assert '<html lang="fr">' in html


def test_render_body_lands_inside_the_main_element() -> None:
    """The markdown body, ``<h1>`` included, sits inside ``<main>``."""
    html = gen_userguide.render(GUIDE_MARKDOWN, GUIDE_CSS)

    assert f"<main>\n{_EXPECTED_BODY}\n</main>" in html


def test_render_explicit_heading_anchor_becomes_the_id_attribute() -> None:
    """``## Settings {: #settings }`` yields ``id="settings"``."""
    html = gen_userguide.render("## Settings {: #settings }\n", GUIDE_CSS)

    assert '<h2 id="settings">Settings</h2>' in html


@pytest.mark.parametrize(
    "css",
    [
        "",
        "body { color: #1d1f20; }",
        "body { color: #1d1f20; }\n",
        "\n  a { color: red; }\n\n",
    ],
)
def test_render_css_text_is_inlined_verbatim_inside_the_style_element(css: str) -> None:
    """The CSS is pasted in unchanged: no reformatting, no strip."""
    html = gen_userguide.render("# Guide\n", css)

    assert f"<style>\n{css}\n</style>" in html


# -------------------------------------------------------- extract_meta


def test_extract_meta_front_matter_returns_single_string_values() -> None:
    """``Meta`` holds lists; the caller needs the plain strings."""
    meta = gen_userguide.extract_meta(GUIDE_MARKDOWN)

    assert meta == {"title": TITLE, "lang": "fr"}


def test_extract_meta_title_present_without_lang_defaults_the_lang() -> None:
    """A front matter with no ``lang`` keeps the default language."""
    meta = gen_userguide.extract_meta("---\ntitle: Snowflake\n---\n\n# Guide\n")

    assert meta == {"title": "Snowflake", "lang": "en"}


def test_extract_meta_lang_present_without_title_defaults_the_title() -> None:
    """A front matter with no ``title`` keeps the default title."""
    meta = gen_userguide.extract_meta("---\nlang: fr\n---\n\n# Guide\n")

    assert meta == {"title": DEFAULT_TITLE, "lang": "fr"}


def test_extract_meta_repeated_key_takes_the_first_value() -> None:
    """``title`` set twice yields one string, not a list of two."""
    meta = gen_userguide.extract_meta("---\ntitle: A\ntitle: B\n---\n\n# Guide\n")

    assert meta == {"title": "A", "lang": "en"}


@pytest.mark.parametrize(
    "markdown_text",
    [
        "",
        "# No front matter\n",
        "---\n---\n\n# Empty front matter\n",
    ],
)
def test_extract_meta_absent_or_empty_front_matter_returns_the_defaults(
    markdown_text: str,
) -> None:
    """No front matter, or an empty one, is not an error."""
    meta = gen_userguide.extract_meta(markdown_text)

    assert meta == {"title": DEFAULT_TITLE, "lang": "en"}


# ------------------------------------------------------- default paths


def test_module_defaults_point_at_the_docs_tree_it_regenerates() -> None:
    """The defaults are the page's checked-in source and output."""
    assert Path(__file__).resolve().parents[2] == gen_userguide.ROOT
    assert gen_userguide.MD == gen_userguide.ROOT / "docs" / "user-guide.md"
    assert gen_userguide.CSS == gen_userguide.ROOT / "docs" / "user-guide.css"
    assert gen_userguide.HTML == gen_userguide.ROOT / "docs" / "user-guide.html"


# ---------------------------------------------------------------- CLI


def test_main_write_flag_writes_the_rendered_document_to_out(
    guide_paths: tuple[Path, Path, Path],
) -> None:
    """``--write`` regenerates the page from markdown + CSS."""
    _markdown_path, _css_path, out_path = guide_paths

    exit_code = gen_userguide.main(_cli_args("--write", guide_paths))

    assert exit_code == 0
    assert out_path.read_text(encoding="utf-8") == _expected_document()


def test_main_write_then_check_returns_zero_when_nothing_changed(
    guide_paths: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """A freshly written page satisfies the drift gate."""
    gen_userguide.main(_cli_args("--write", guide_paths))

    exit_code = gen_userguide.main(_cli_args("--check", guide_paths))

    assert exit_code == 0
    assert "drift" not in capsys.readouterr().out


def test_main_check_returns_one_after_the_markdown_changes(
    guide_paths: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """An edited guide that was not regenerated fails the build."""
    markdown_path, _css_path, _out_path = guide_paths
    gen_userguide.main(_cli_args("--write", guide_paths))
    markdown_path.write_text(f"{GUIDE_MARKDOWN}\nAn added paragraph.\n", encoding="utf-8")

    exit_code = gen_userguide.main(_cli_args("--check", guide_paths))

    assert exit_code == 1
    assert "drift:" in capsys.readouterr().out


def test_main_check_returns_one_when_the_page_does_not_exist_yet(
    guide_paths: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing committed page is drift, never a silent pass."""
    exit_code = gen_userguide.main(_cli_args("--check", guide_paths))

    assert exit_code == 1
    assert "drift:" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["--write", "--check"])
def test_main_returns_two_when_the_markdown_is_missing(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing input is a tool error (2), not drift (1)."""
    css_path = tmp_path / "user-guide.css"
    css_path.write_text(GUIDE_CSS, encoding="utf-8")
    paths = (tmp_path / "no-such-guide.md", css_path, tmp_path / "user-guide.html")

    exit_code = gen_userguide.main(_cli_args(mode, paths))

    assert exit_code == 2
    assert "error:" in capsys.readouterr().err


def test_main_returns_two_when_the_css_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing stylesheet is a tool error (2), not drift (1)."""
    markdown_path = tmp_path / "user-guide.md"
    markdown_path.write_text(GUIDE_MARKDOWN, encoding="utf-8")
    paths = (markdown_path, tmp_path / "no-such-guide.css", tmp_path / "user-guide.html")

    exit_code = gen_userguide.main(_cli_args("--check", paths))

    assert exit_code == 2
    assert "error:" in capsys.readouterr().err


def test_main_returns_two_when_the_markdown_is_not_utf8(
    guide_paths: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """An undecodable input fails loudly, not with a traceback."""
    markdown_path, _css_path, _out_path = guide_paths
    markdown_path.write_bytes(b"---\ntitle: \xff\xfe\n---\n")

    exit_code = gen_userguide.main(_cli_args("--check", guide_paths))

    assert exit_code == 2
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["--write", "--check"]])
def test_main_without_exactly_one_mode_flag_exits_with_the_usage_error(argv: list[str]) -> None:
    """``--write``/``--check`` are mutually exclusive and required."""
    with pytest.raises(SystemExit, match=r"^2$") as exc_info:
        gen_userguide.main(argv)

    assert exc_info.value.code == 2


def test_main_check_error_message_names_the_file_it_could_not_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exit-2 message names the path it could not read."""
    css_path = tmp_path / "user-guide.css"
    css_path.write_text(GUIDE_CSS, encoding="utf-8")
    missing_markdown = tmp_path / "no-such-guide.md"

    gen_userguide.main(
        _cli_args("--check", (missing_markdown, css_path, tmp_path / "user-guide.html"))
    )

    assert str(missing_markdown) in capsys.readouterr().err


# ------------------------------------------------- content assertions
# These tests read the committed guide and markdown, so they gate the
# content phase, not the generator's own mechanics.

_RANK_PHRASE_TO_CLASS: dict[str, HandClass] = {
    "Five of a kind": HandClass.FIVE_OF_A_KIND,
    "Royal flush": HandClass.ROYAL_FLUSH,
    "Straight flush": HandClass.STRAIGHT_FLUSH,
    "Four of a kind": HandClass.QUADS,
    "Full house": HandClass.FULL_HOUSE,
    "Flush": HandClass.FLUSH,
    "Straight": HandClass.STRAIGHT,
    "Three of a kind": HandClass.TRIPS,
    "Two pair": HandClass.TWO_PAIR,
    "One pair": HandClass.PAIR,
    "High card": HandClass.HIGH_CARD,
}


def test_committed_guide_contains_every_mapped_anchor_and_the_default() -> None:
    """Every deep-link anchor, plus the default, exists in the guide."""
    guide_html = (_DOCS_DIR / "user-guide.html").read_text(encoding="utf-8")
    anchors = {help_module.DEFAULT_ANCHOR, *help_module.ANCHOR_BY_WINDOW.values()}
    missing = sorted(anchor for anchor in anchors if f'id="{anchor}"' not in guide_html)
    assert missing == []


def test_guide_hand_rankings_list_every_class_strongest_first() -> None:
    """The guide's ranking list matches hands.HandClass in order."""
    markdown = (_DOCS_DIR / "user-guide.md").read_text(encoding="utf-8")
    section = markdown.split("## Hand rankings {: #hand-rankings }")[1]
    section = section.split("## Scoring references")[0]
    phrases = re.findall(r"^\d+\. \*\*(.+?)\*\*", section, flags=re.MULTILINE)
    assert [_RANK_PHRASE_TO_CLASS[phrase] for phrase in phrases] == list(reversed(list(HandClass)))
