import pytest

from pipeline.checks.digits import copy_digits, main
from pipeline.paths import REPO_ROOT


def test_flags_digits_in_copy():
    assert copy_digits("<h2>Top 10 techniques</h2>\n") == [(1, "Top 10 techniques")]


def test_ignores_frontmatter_scripts_styles_tags_and_expressions():
    src = ('---\nconst n = 5;\n---\n<h2 class="x">Share {fmt(0.5, "pct")}</h2>\n<style>p{margin:2px}</style>\n'
           '<script>let a = 1;</script>\n<ul>{xs.map((x) => <li><a href={`/t/${x.id}/`}>{x.n} items</a></li>)}</ul>\n'
           '<meta charset="utf-8" />\n<!-- v2 -->\n')
    assert copy_digits(src) == []


def test_line_numbers_survive_stripping():
    assert copy_digits("---\na = 1\n---\n<p>ok</p>\n<p>page 2</p>\n") == [(5, "page 2")]


def test_repo_site_has_no_digit_copy():
    assert main([str(REPO_ROOT / "site" / "src")]) == 0


@pytest.mark.parametrize("src", [
    '<p title="Top 10">x</p>',
    '<Chart title="Top 10 things" />',
    "{ok && <p>Top 10 items</p>}",
    "{a ? <h2>Top 10</h2> : <p>x</p>}",
    '---\nconst s = "Top 10"\n---\n<p>{s}</p>',
    "<div set:html={x} />",
    "<p>fewer than <5 items</p>",
    '{"{"}\n<p>Top 10</p>',
])
def test_flags_reviewer_probes(src):
    assert copy_digits(src) != []


@pytest.mark.parametrize("src", [
    "{u.skills.length > 0 && <h2>Skills</h2>}",
    '<meta name="viewport" content="width=device-width, initial-scale=1" />',
    '---\nimport x from "../data/a1.json"\n---\n<p>x</p>',
    "<a href={`/t/${id}/`}>x</a>",
])
def test_ignores_code_and_exempt_values(src):
    assert copy_digits(src) == []


def test_main_fails_when_no_astro_files(tmp_path, capsys):
    assert main([str(tmp_path)]) == 1
    assert "no .astro files found under" in capsys.readouterr().out
