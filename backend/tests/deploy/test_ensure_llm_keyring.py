import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "ensure_llm_keyring.py"
WORKFLOW = SCRIPT.parents[1] / ".github" / "workflows" / "deploy.yml"


def _run(env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(env_file)],
        text=True,
        capture_output=True,
        check=False,
    )


def _valid_keyring() -> tuple[str, str]:
    key_id = "release-2026-08"
    encoded = base64.b64encode(b"x" * 32).decode("ascii")
    return key_id, encoded


def test_deploy_bootstraps_datahub_key_before_first_compose_call() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pull_position = workflow.index("git pull origin main")
    compose_position = workflow.index("docker compose", pull_position)
    bootstrap_position = workflow.index(
        "python3 deploy/ensure_llm_keyring.py deploy/.env", pull_position
    )

    assert bootstrap_position < compose_position


def test_missing_keyring_is_generated_atomically_and_restricted(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_PASSWORD=placeholder\n", encoding="utf-8")
    env_file.chmod(0o640)

    result = _run(env_file)

    assert result.returncode == 0, result.stderr
    assert "已生成并保存" in result.stdout
    assert "密钥" in result.stdout
    assert env_file.stat().st_mode & 0o777 == 0o600
    values = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            values[name] = value
    assert values["LLM_CONFIG_ENCRYPTION_KEY_ID"]
    keyring = json.loads(values["LLM_CONFIG_ENCRYPTION_KEYS"])
    assert list(keyring) == [values["LLM_CONFIG_ENCRYPTION_KEY_ID"]]
    assert len(base64.b64decode(next(iter(keyring.values())), validate=True)) == 32


def test_existing_valid_keyring_is_verified_without_changing_file(tmp_path: Path) -> None:
    key_id, encoded = _valid_keyring()
    env_file = tmp_path / ".env"
    original = (
        "POSTGRES_PASSWORD=placeholder\n"
        f"LLM_CONFIG_ENCRYPTION_KEY_ID={key_id}\n"
        f"LLM_CONFIG_ENCRYPTION_KEYS={json.dumps({key_id: encoded}, separators=(',', ':'))}\n"
        "DATAHUB_CONFIG_ENCRYPTION_KEY=" + "a" * 64 + "\n"
    )
    env_file.write_text(original, encoding="utf-8")
    env_file.chmod(0o600)

    result = _run(env_file)

    assert result.returncode == 0, result.stderr
    assert "校验通过" in result.stdout
    assert env_file.read_text(encoding="utf-8") == original
    assert env_file.stat().st_mode & 0o777 == 0o600


def test_missing_datahub_key_preserves_all_existing_llm_key_ids(tmp_path: Path) -> None:
    first_id, first_key = _valid_keyring()
    second_id = "previous-2026-07"
    second_key = base64.b64encode(b"y" * 32).decode("ascii")
    env_file = tmp_path / ".env"
    original = (
        f"LLM_CONFIG_ENCRYPTION_KEY_ID={first_id}\n"
        f"LLM_CONFIG_ENCRYPTION_KEYS={json.dumps({first_id: first_key, second_id: second_key}, separators=(',', ':'))}\n"
    )
    env_file.write_text(original, encoding="utf-8")
    env_file.chmod(0o600)

    result = _run(env_file)
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert set(json.loads(values["LLM_CONFIG_ENCRYPTION_KEYS"])) == {first_id, second_id}
    assert len(values["DATAHUB_CONFIG_ENCRYPTION_KEY"]) == 64


@pytest.mark.parametrize(
    "content",
    [
        "LLM_CONFIG_ENCRYPTION_KEY_ID=only-id\n",
        "LLM_CONFIG_ENCRYPTION_KEYS={}\n",
        "LLM_CONFIG_ENCRYPTION_KEY_ID=one\nLLM_CONFIG_ENCRYPTION_KEY_ID=two\n",
        "LLM_CONFIG_ENCRYPTION_KEYS={}\nLLM_CONFIG_ENCRYPTION_KEYS={}\n",
        "LLM_CONFIG_ENCRYPTION_KEY_ID=one\nLLM_CONFIG_ENCRYPTION_KEYS=[]\n",
        "LLM_CONFIG_ENCRYPTION_KEY_ID=one\nLLM_CONFIG_ENCRYPTION_KEYS={\"one\":}\n",
        "LLM_CONFIG_ENCRYPTION_KEY_ID=one\nLLM_CONFIG_ENCRYPTION_KEYS={\"one\":\"eA==\",\"one\":\"eA==\"}\n",
        "LLM_CONFIG_ENCRYPTION_KEY_ID=one\nLLM_CONFIG_ENCRYPTION_KEYS={\"one\":\"not-base64\"}\n",
        "LLM_CONFIG_ENCRYPTION_KEY_ID=one\nLLM_CONFIG_ENCRYPTION_KEYS={\"one\":\"eA==\"}\n",
    ],
)
def test_partial_or_invalid_keyring_fails_without_rewriting(
    tmp_path: Path, content: str
) -> None:
    env_file = tmp_path / ".env"
    original = "POSTGRES_PASSWORD=placeholder\n" + content
    env_file.write_text(original, encoding="utf-8")
    env_file.chmod(0o600)

    result = _run(env_file)

    assert result.returncode != 0
    assert env_file.read_text(encoding="utf-8") == original
    assert "not-base64" not in result.stdout + result.stderr
    assert "密钥" in result.stdout + result.stderr
