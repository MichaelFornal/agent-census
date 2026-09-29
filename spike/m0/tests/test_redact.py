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


def test_url_credentials_redacted():
    out, counts = redact('"args": ["postgresql://neondb_owner:npg_AbC123xyz@ep-cool.neon.tech/db"]')
    assert "npg_AbC123xyz" not in out
    assert "postgresql://neondb_owner:[REDACTED:url_credentials]@ep-cool.neon.tech/db" in out


def test_jwt_redacted():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.c2lnbmF0dXJlX3ZhbHVlX2hlcmU"
    out, counts = redact(f'"SUPABASE_SERVICE_ROLE": "{jwt}"')
    assert jwt not in out and "eyJ" not in out


def test_stripe_and_huggingface_tokens():
    out, _ = redact("sk_live_" + "a1B2" * 6 + " and hf_" + "Zz9" * 12)
    assert "sk_live_a1B2" not in out and "hf_Zz9" not in out


def test_key_names_without_api_prefix_and_camel_case():
    text = '{"apiKey": "abcd1234efgh5678", "X-API-Key": "zyxw9876vuts5432", "STRIPE_KEY": "qwer1234asdf5678"}'
    out, _ = redact(text)
    for secret in ("abcd1234efgh5678", "zyxw9876vuts5432", "qwer1234asdf5678"):
        assert secret not in out


def test_cli_flag_secrets_in_permission_strings():
    text = ('"Bash(curl -u admin:hunter2secret https://x.io)", "Bash(mysql -pS3cretPass99 db)", '
            '["--api-key", "k-9f8e7d6c5b4a"], "Bash(tool --token=tok_55aa66bb77cc)"')
    out, _ = redact(text)
    for secret in ("hunter2secret", "S3cretPass99", "k-9f8e7d6c5b4a", "tok_55aa66bb77cc"):
        assert secret not in out
