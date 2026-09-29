import json

from m0.redact import redact

GHP = "ghp_" + "A1b2C3d4E5" * 4  # 40 chars after prefix


def test_github_token_in_settings_local_permission():
    text = json.dumps({"permissions": {"allow": [f"Bash(GH_TOKEN={GHP} gh pr list)"]}})
    out, counts = redact(text)
    assert GHP not in out
    assert "[REDACTED:github_token]" in out
    assert counts == {"github_token": 1}


def test_mcp_env_secret_redacted_but_env_reference_kept():
    text = json.dumps({"mcpServers": {"brave": {"env": {
        "BRAVE_API_KEY": "BSAabc123def456ghi", "GITHUB_TOKEN": "${GITHUB_TOKEN}"}}}})
    out, counts = redact(text)
    assert '"BRAVE_API_KEY": "[REDACTED:assigned_secret]"' in out
    assert '"GITHUB_TOKEN": "${GITHUB_TOKEN}"' in out
    assert counts == {"assigned_secret": 1}


def test_anthropic_key_is_not_double_counted_as_openai():
    out, counts = redact("export ANTHROPIC=sk-ant-api03-" + "x" * 40)
    assert counts == {"anthropic_key": 1}
    assert "sk-ant" not in out


def test_aws_and_bearer_and_private_key():
    text = (
        "AKIAABCDEFGHIJKLMNOP\n"
        'curl -H "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123"\n'
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----\n"
    )
    out, counts = redact(text)
    assert counts == {"aws_access_key": 1, "bearer": 1, "private_key": 1}
    assert "Bearer [REDACTED:bearer]" in out
    assert "MIIEow" not in out


def test_ordinary_claude_md_prose_is_untouched():
    text = "# Project\n\nRun `pytest -q` before finishing. Never commit the .env file.\n"
    assert redact(text) == (text, {})
