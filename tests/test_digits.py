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
