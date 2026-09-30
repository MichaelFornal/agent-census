from helpers import fixture_text

from pipeline.parsers import parse

SETTINGS = ".claude/settings.json"


def test_settings_hooks_permissions_env():
    d, err = parse("settings", fixture_text("acme/webapp", SETTINGS), SETTINGS, [])
    assert err is None
    assert d["hooks"] == [
        {"event": "Stop", "matcher": None, "type": "command", "command": "npm test --silent", "line": 7},
        {"event": "PostToolUse", "matcher": "Edit|Write", "type": "command",
         "command": 'npx prettier --write "$CLAUDE_FILE_PATHS"', "line": 8},
    ]
    assert d["permissions"] == {"allow": ["Bash(npm test:*)", "Bash(npm run lint)", "Read"],
                                "deny": ["Bash(rm -rf:*)", "Read(./.env)"], "ask": [], "default_mode": None}
    assert d["permissions_line"] == 2
    assert d["env_keys"] == ["NODE_ENV"] and d["model"] == "sonnet" and d["sandbox"] is None


def test_settings_sandbox_ask_and_events():
    d, _ = parse("settings", fixture_text("epsilon/infra", SETTINGS), SETTINGS, [])
    assert d["sandbox"] is True
    assert d["permissions"]["ask"] == ["Bash(terraform apply:*)"]
    assert [h["event"] for h in d["hooks"]] == ["PreToolUse", "SessionStart"]


def test_bypass_mode_in_settings_local():
    path = ".claude/settings.local.json"
    d, _ = parse("settings_local", fixture_text("beta/data-pipeline", path), path, [])
    assert d["permissions"]["default_mode"] == "bypassPermissions"
    assert d["permissions"]["allow"] == ["Bash", "WebFetch"]


def test_invalid_json_settings_keeps_error_class():
    text = fixture_text("eta/broken", SETTINGS)
    assert parse("settings", text, SETTINGS, []) == ({"bytes": len(text.encode())}, "json_invalid")


def test_json_array_is_not_object():
    assert parse("settings", "[1, 2]", SETTINGS, [])[1] == "not_object"


def test_mcp_servers():
    d, err = parse("mcp", fixture_text("acme/webapp", ".mcp.json"), ".mcp.json", [])
    assert err is None
    assert d["servers"][0] == {"name": "github", "transport": "stdio", "command": "npx",
                               "package": "@modelcontextprotocol/server-github", "url_host": None,
                               "env_keys": ["GITHUB_PERSONAL_ACCESS_TOKEN"], "line": 2}
    assert [s["name"] for s in d["servers"]] == ["github", "postgres"]


def test_http_mcp_server_and_missing_servers_key():
    d, _ = parse("mcp", '{"mcpServers": {"docs": {"type": "http", "url": "https://mcp.example.com/sse"}}}', ".mcp.json", [])
    assert d["servers"][0]["transport"] == "http" and d["servers"][0]["url_host"] == "mcp.example.com"
    assert parse("mcp", '{"servers": {}}', ".mcp.json", [])[1] == "no_mcp_servers"


def test_manifest():
    path = ".claude-plugin/plugin.json"
    d, _ = parse("plugin", fixture_text("zeta/release-plugin", path), path, [])
    assert (d["name"], d["version"], d["description"]) == ("release-helper", "0.3.0", "Release helpers")


def test_empty_file():
    assert parse("claude_md", "  \n", "CLAUDE.md", []) == ({"bytes": 3}, "empty")


def test_malformed_shapes_raise_only_parse_error_classes():
    deep = "[" * 100000 + "]" * 100000
    assert parse("settings", deep, SETTINGS, [])[1] == "json_invalid"
    odd = '{"hooks": {"Stop": [{"hooks": "x"}, 5]}, "permissions": [], "env": 3, "sandbox": {"enabled": false}}'
    d, err = parse("settings", odd, SETTINGS, [])
    assert err is None and d["hooks"] == [] and d["sandbox"] is False
    bad_url = '{"mcpServers": {"x": {"url": "http://[bad"}, "y": 5}}'
    d, err = parse("mcp", bad_url, ".mcp.json", [])
    assert err is None and d["servers"][0]["url_host"] is None and len(d["servers"]) == 1
    assert parse("settings", '{"a": ' + "1" * 5000 + "}", SETTINGS, [])[1] == "json_invalid"


def test_non_ascii_hook_command_line_number():
    text = '{\n  "hooks": {"Stop": [{"hooks": [\n    {"type": "command", "command": "echo \\"héllo\\""}\n  ]}]}\n}\n'
    d, err = parse("settings", text, SETTINGS, [])
    assert err is None and d["hooks"][0]["command"] == 'echo "héllo"' and d["hooks"][0]["line"] == 3
