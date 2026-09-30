import json

import pytest

from pipeline.redact import REDACT_VERSION, redact, rules_fingerprint

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


def test_assigned_secret_leaves_code_alone():
    text = "tokens = count_tokens(text)\nAPI_KEY = process.env.API_KEY\nmax_tokens = 4096\n"
    assert redact(text) == (text, {})


def test_assigned_secret_redacts_high_entropy_value():
    out, counts = redact('export OPENAI_API_KEY="a8f3Kq92Lm0Zx7Rt"')
    assert "a8f3Kq92Lm0Zx7Rt" not in out
    assert counts == {"assigned_secret": 1}


def test_redaction_is_idempotent():
    once, _ = redact('{"CLOUD_API_KEY": "Zq8Xv2Lm9Pw4Rt7Ky3Nb"}')
    assert redact(once) == (once, {})


SAMPLES = [
    "token " + GHP,
    "curl -u admin:hunter2secret https://x.example/api",
    "postgres://app:S3cr3tPassw0rd@db.internal:5432/app",
    'claude --api-key "sk-ant-' + "a1B2" * 8 + '"',
    "mysql -u root -pS3cr3tPassw0rd app",
    "Authorization: Bearer " + "abcDEF123456" * 3,
    '{"env": {"CLOUD_API_KEY": "Zq8Xv2Lm9Pw4Rt7Ky3Nb"}}',
    "curl -u admin:" + GHP + " https://x.example",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_redact_is_idempotent_for_every_rule_shape(text):
    once, counts = redact(text)
    assert counts, text  # every sample holds something to redact
    assert redact(once) == (once, {})


def test_a_later_rule_does_not_relabel_an_earlier_placeholder():
    out, counts = redact("curl -u admin:" + GHP + " https://x.example")
    assert out == "curl -u admin:[REDACTED:github_token] https://x.example"
    assert counts == {"github_token": 1}


@pytest.mark.parametrize("text", [
    "curl -u admin:[REDACTED:x]hunter2secret https://x",
    "--password [REDACTED:note]hunter2secret",
])
def test_a_placeholder_followed_by_secret_text_is_still_redacted(text):
    out, counts = redact(text)
    assert "hunter2secret" not in out and counts


PINNED =(1, "c1aa50aa9fac1150")


def test_rule_changes_require_a_version_bump():
    assert (REDACT_VERSION, rules_fingerprint()) == PINNED, (
        "the redaction rules changed: bump REDACT_VERSION in pipeline/redact.py and pin the new fingerprint here")
