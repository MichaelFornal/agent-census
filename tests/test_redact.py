import json
import time

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


@pytest.mark.parametrize("text", ["a" * 20000, "A1b2" * 5000, "a-b_" * 5000], ids=["a", "A1b2", "a-b_"])
def test_a_long_identifier_run_is_not_quadratic(text):
    start = time.perf_counter()
    assert redact(text) == (text, {})
    assert time.perf_counter() - start < 2.0


# Outputs computed with the rules as they were before the identifier run of assigned_secret was anchored.
SAMPLE_OUTPUTS = [
    ("token [REDACTED:github_token]", {"github_token": 1}),
    ("curl -u admin:[REDACTED:curl_user_password] https://x.example/api", {"curl_user_password": 1}),
    ("postgres://app:[REDACTED:url_credentials]@db.internal:5432/app", {"url_credentials": 1}),
    ('claude --api-key "[REDACTED:anthropic_key]"', {"anthropic_key": 1}),
    ("mysql -u root -p[REDACTED:mysql_password] app", {"mysql_password": 1}),
    ("Authorization: Bearer [REDACTED:bearer]", {"bearer": 1}),
    ('{"env": {"CLOUD_API_KEY": "[REDACTED:assigned_secret]"}}', {"assigned_secret": 1}),
    ("curl -u admin:[REDACTED:github_token] https://x.example", {"github_token": 1}),
]
EQUALITY_CASES = [
    (json.dumps({"permissions": {"allow": [f"Bash(GH_TOKEN={GHP} gh pr list)"]}}),
     '{"permissions": {"allow": ["Bash(GH_TOKEN=[REDACTED:github_token] gh pr list)"]}}', {"github_token": 1}),
    (json.dumps({"mcpServers": {"brave": {"env": {"BRAVE_API_KEY": "BSAabc123def456ghi",
                                                  "GITHUB_TOKEN": "${GITHUB_TOKEN}"}}}}),
     '{"mcpServers": {"brave": {"env": {"BRAVE_API_KEY": "[REDACTED:assigned_secret]", '
     '"GITHUB_TOKEN": "${GITHUB_TOKEN}"}}}}', {"assigned_secret": 1}),
    ("export ANTHROPIC=sk-ant-api03-" + "x" * 40, "export ANTHROPIC=[REDACTED:anthropic_key]", {"anthropic_key": 1}),
    ("AKIAABCDEFGHIJKLMNOP\ncurl -H \"Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123\"\n"
     "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----\n",
     '[REDACTED:aws_access_key]\ncurl -H "Authorization: Bearer [REDACTED:bearer]"\n[REDACTED:private_key]\n',
     {"private_key": 1, "aws_access_key": 1, "bearer": 1}),
    ('{"apiKey": "abcd1234efgh5678", "X-API-Key": "zyxw9876vuts5432", "STRIPE_KEY": "qwer1234asdf5678"}',
     '{"apiKey": "[REDACTED:assigned_secret]", "X-API-Key": "[REDACTED:assigned_secret]", '
     '"STRIPE_KEY": "[REDACTED:assigned_secret]"}', {"assigned_secret": 3}),
    ('"Bash(curl -u admin:hunter2secret https://x.io)", "Bash(mysql -pS3cretPass99 db)", '
     '["--api-key", "k-9f8e7d6c5b4a"], "Bash(tool --token=tok_55aa66bb77cc)"',
     '"Bash(curl -u admin:[REDACTED:curl_user_password] https://x.io)", "Bash(mysql -p[REDACTED:mysql_password] db)", '
     '["--api-key", "[REDACTED:cli_flag_secret]"], "Bash(tool --token=[REDACTED:cli_flag_secret])"',
     {"cli_flag_secret": 2, "curl_user_password": 1, "mysql_password": 1}),
    ("tokens = count_tokens(text)\nAPI_KEY = process.env.API_KEY\nmax_tokens = 4096\n",
     "tokens = count_tokens(text)\nAPI_KEY = process.env.API_KEY\nmax_tokens = 4096\n", {}),
    ('export OPENAI_API_KEY="a8f3Kq92Lm0Zx7Rt"', 'export OPENAI_API_KEY="[REDACTED:assigned_secret]"',
     {"assigned_secret": 1}),
    ("curl -u admin:[REDACTED:x]hunter2secret https://x", "curl -u admin:[REDACTED:curl_user_password] https://x",
     {"curl_user_password": 1}),
    ('abc"TOKEN": "Zq8Xv2Lm9Pw4Rt7Ky3Nb" and foo.SECRET=Ab3dE5gH7jK9 and x-my_password: Zq8Xv2Lm9Pw4Rt7Ky3Nb',
     'abc"TOKEN": "[REDACTED:assigned_secret]" and foo.SECRET=[REDACTED:assigned_secret] '
     "and x-my_password: [REDACTED:assigned_secret]", {"assigned_secret": 3}),
    ("prefix" * 30 + "_API_KEY=Zq8Xv2Lm9Pw4Rt7Ky3Nb\n_KEY: 'Zq8Xv2Lm9Pw4Rt7Ky3Nb'\nDB_PASSWORD=Zq8Xv2Lm9Pw4Rt7Ky3Nb",
     "prefix" * 30 + "_API_KEY=[REDACTED:assigned_secret]\n_KEY: [REDACTED:assigned_secret]\n"
     "DB_PASSWORD=[REDACTED:assigned_secret]", {"assigned_secret": 3}),
]


@pytest.mark.parametrize("text, out, counts", [(t, *e) for t, e in zip(SAMPLES, SAMPLE_OUTPUTS)] + EQUALITY_CASES)
def test_redact_output_equals_the_snapshot_from_before_the_anchoring(text, out, counts):
    assert redact(text) == (out, counts)


PINNED = (1, "ecfad3866b9a20a6")


def test_rule_changes_require_a_version_bump():
    assert (REDACT_VERSION, rules_fingerprint()) == PINNED, (
        "the redaction rules changed: bump REDACT_VERSION in pipeline/redact.py and pin the new fingerprint here")
