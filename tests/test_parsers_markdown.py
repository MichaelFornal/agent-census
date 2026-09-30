import json

import pytest
from helpers import FIXTURES, fixture_text

from pipeline.fixtures import fixture_files
from pipeline.parsers.errors import ParseError
from pipeline.parsers.markdown import (parse_agent, parse_claude_md, parse_command, parse_hook_script,
                                       parse_skill, scan_markdown)

SKILL = ".claude/skills/csv-cleaner/SKILL.md"


def test_claude_md_sections_imports_code_blocks():
    d = parse_claude_md(fixture_text("acme/webapp", "CLAUDE.md"), "CLAUDE.md", [])
    assert [(h["level"], h["text"], h["line"]) for h in d["headings"]] == [
        (1, "Webapp", 1), (2, "Commands", 7), (2, "Rules", 14)]
    assert d["imports"] == [{"target": "docs/architecture.md", "line": 5}]
    assert d["code_blocks"] == [{"lang": "bash", "start_line": 19, "end_line": 22}]
    assert d["nested"] is False and d["lines"] == 22


def test_import_regex_ignores_emails_code_spans_and_fences():
    text = "Mail a@b.com\n`@x/y`\n```\n@docs/in-code.md\n```\n@./notes.md\nsee @Michael today\n"
    assert scan_markdown(text.splitlines())["imports"] == [{"target": "./notes.md", "line": 6}]


def test_nested_claude_md_flag():
    text = fixture_text("acme/webapp", "packages/api/CLAUDE.md")
    assert parse_claude_md(text, "packages/api/CLAUDE.md", [])["nested"] is True


def test_skill_frontmatter_and_resources():
    siblings = [SKILL, ".claude/skills/csv-cleaner/scripts/clean.py", ".claude/skills/csv-cleaner/references/rules.md",
                ".claude/skills/other/SKILL.md"]
    d = parse_skill(fixture_text("beta/data-pipeline", SKILL), SKILL, siblings)
    assert d["frontmatter"]["name"] == "csv-cleaner"
    assert d["frontmatter_end_line"] == 4
    assert d["resources"] == ["references", "scripts"]
    assert [h["text"] for h in d["headings"]] == ["CSV cleaner"]


def test_bad_frontmatter_is_recorded_not_dropped():
    path = ".claude/skills/notes/SKILL.md"
    text = fixture_text("eta/broken", path)
    with pytest.raises(ParseError) as e:
        parse_skill(text, path, [path])
    assert e.value.error_class == "frontmatter_invalid"
    assert e.value.partial["frontmatter_end_line"] == 4
    assert e.value.partial["bytes"] == len(text.encode())
    assert e.value.partial["resources"] == []


def test_unclosed_frontmatter():
    with pytest.raises(ParseError) as e:
        parse_command("---\ndescription: x\n\nbody\n", ".claude/commands/x.md", [])
    assert e.value.error_class == "frontmatter_unclosed"


def test_agent_tools_string_becomes_list():
    d = parse_agent(fixture_text("acme/webapp", ".claude/agents/reviewer.md"), ".claude/agents/reviewer.md", [])
    assert d["tools"] == ["Read", "Grep", "Glob"]
    assert d["model"] == "opus"
    assert d["frontmatter_end_line"] == 6


def test_command_arguments_line():
    d = parse_command(fixture_text("acme/webapp", ".claude/commands/fix-issue.md"), ".claude/commands/fix-issue.md", [])
    assert d["has_arguments"] is True and d["arguments_line"] == 8
    assert d["allowed_tools"] == ["Bash(gh issue view:*)"]


def test_hook_script_language():
    assert parse_hook_script(fixture_text("epsilon/infra", ".claude/hooks/guard.sh"), ".claude/hooks/guard.sh",
                             [])["language"] == "shell"
    assert parse_hook_script("#!/usr/bin/env python3\nprint(1)\n", ".claude/hooks/check", [])["language"] == "python"
    assert parse_hook_script("echo hi\n", ".claude/hooks/check", [])["language"] == "other"


def test_date_key_frontmatter_is_stringified():
    d = parse_command("---\n2024-01-01: x\n---\nbody\n", ".claude/commands/x.md", [])
    assert d["frontmatter"] == {"2024-01-01": "x"}


def test_deeply_nested_frontmatter_is_invalid_not_recursion_error():
    with pytest.raises(ParseError) as e:
        parse_command("---\na: " + "[" * 5000 + "\n---\nbody\n", ".claude/commands/x.md", [])
    assert e.value.error_class == "frontmatter_invalid"


def test_non_dict_frontmatter_is_invalid_with_partial():
    with pytest.raises(ParseError) as e:
        parse_command("---\n- a\n- b\n---\nbody\n", ".claude/commands/x.md", [])
    assert e.value.error_class == "frontmatter_invalid"
    assert e.value.partial["frontmatter_end_line"] == 4
    assert e.value.partial["lines"] == 5


def test_bom_frontmatter_is_found():
    text = "\ufeff---\nname: x\n---\n# T\n"
    d = parse_skill(text, ".claude/skills/x/SKILL.md", [])
    assert d["frontmatter"]["name"] == "x"
    assert d["frontmatter_end_line"] == 3
    assert d["bytes"] == len(text.encode())
    assert [h["line"] for h in d["headings"]] == [4]


def test_scan_markdown_offset_shifts_lines():
    lines = ["# H", "@a/b.md", "```sh", "x", "```"]
    d = scan_markdown(lines, offset=10)
    assert d["headings"][0]["line"] == 11
    assert d["imports"] == [{"target": "a/b.md", "line": 12}]
    assert d["code_blocks"] == [{"lang": "sh", "start_line": 13, "end_line": 15}]


def test_unclosed_fence_runs_to_last_line_and_hides_headings():
    d = scan_markdown(["# A", "```py", "# not a heading", "code"])
    assert d["code_blocks"] == [{"lang": "py", "start_line": 2, "end_line": 4}]
    assert [h["text"] for h in d["headings"]] == ["A"]


def test_shorter_fence_does_not_close_longer_one():
    d = scan_markdown(["````", "```", "# inside", "````", "# out"])
    assert d["code_blocks"] == [{"lang": None, "start_line": 1, "end_line": 4}]
    assert [h["text"] for h in d["headings"]] == ["out"]


def test_import_trailing_period_stripped():
    assert scan_markdown(["read @docs/a.md."])["imports"] == [{"target": "docs/a.md", "line": 1}]


def test_node_shebang_is_javascript():
    assert parse_hook_script("#!/usr/bin/env node\n", ".claude/hooks/check", [])["language"] == "javascript"
    assert parse_hook_script("#!/bin/bash\n", ".claude/hooks/check", [])["language"] == "shell"


PARSERS = {"CLAUDE.md": parse_claude_md, "SKILL.md": parse_skill, "agents": parse_agent,
           "commands": parse_command, "hooks": parse_hook_script}


def _parser_for(path):
    name = path.rsplit("/", 1)[-1]
    if name in ("CLAUDE.md", "SKILL.md"):
        return PARSERS[name]
    for key in ("agents", "commands", "hooks"):
        if f"/{key}/" in path:
            return PARSERS[key]
    return None


def _cases():
    out = []
    for f in fixture_files(FIXTURES):
        if _parser_for(f.path):
            out.append((f.repo, f.path))
    return out


@pytest.mark.parametrize("repo,path", _cases())
def test_every_fixture_output_is_json_serialisable(repo, path):
    text = fixture_text(repo, path)
    try:
        d = _parser_for(path)(text, path, [path])
    except ParseError as e:
        d = e.partial
    assert d
    json.dumps(d)
