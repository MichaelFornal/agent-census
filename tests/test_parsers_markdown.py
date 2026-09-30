import pytest
from helpers import fixture_text

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
