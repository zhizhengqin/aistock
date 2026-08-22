#!/usr/bin/env python3
"""Validate or create the production LLM encryption keyring in a dotenv file."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Any


KEY_ID = "LLM_CONFIG_ENCRYPTION_KEY_ID"
KEYS = "LLM_CONFIG_ENCRYPTION_KEYS"
DATAHUB_KEY = "DATAHUB_CONFIG_ENCRYPTION_KEY"
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)(?:\r?\n)?$")


class KeyringError(ValueError):
    """A safe, user-facing keyring validation error."""


def _read_env(path: Path) -> tuple[str, dict[str, str], dict[str, int]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise KeyringError("无法读取生产配置文件") from exc

    values: dict[str, str] = {}
    occurrences: dict[str, int] = {}
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if not match:
            continue
        name, value = match.groups()
        if name in {KEY_ID, KEYS, DATAHUB_KEY}:
            occurrences[name] = occurrences.get(name, 0) + 1
            if occurrences[name] > 1:
                raise KeyringError("LLM 密钥环配置存在重复声明")
            values[name] = _unquote(value.strip())
    return text, values, occurrences


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_json_object(value: str) -> dict[str, Any]:
    if not value:
        raise KeyringError("LLM 密钥环必须同时配置写入 ID 和密钥对象")

    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise KeyringError("LLM 密钥环 JSON 存在重复键")
            result[key] = item
        return result

    try:
        parsed = json.loads(value, object_pairs_hook=reject_duplicate_pairs)
    except KeyringError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise KeyringError("LLM 密钥环 JSON 无效") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise KeyringError("LLM 密钥环必须是非空 JSON 对象")
    return parsed


def _validate_existing(values: dict[str, str]) -> None:
    write_id = values.get(KEY_ID, "")
    keyring_text = values.get(KEYS, "")
    if not write_id or not keyring_text:
        raise KeyringError("LLM 密钥环必须同时配置写入 ID 和密钥对象")
    if not write_id.strip():
        raise KeyringError("LLM 写入密钥 ID 不能为空")

    keyring = _parse_json_object(keyring_text)
    for key_id, encoded in keyring.items():
        if not isinstance(key_id, str) or not key_id.strip():
            raise KeyringError("LLM 密钥 ID 不能为空")
        if not isinstance(encoded, str):
            raise KeyringError("LLM 密钥必须是标准 Base64 且解码为 32 字节")
        try:
            decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError, base64.binascii.Error) as exc:
            raise KeyringError("LLM 密钥必须是标准 Base64 且解码为 32 字节") from exc
        if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != encoded:
            raise KeyringError("LLM 密钥必须是标准 Base64 且解码为 32 字节")
    if write_id not in keyring:
        raise KeyringError("LLM 写入密钥 ID 不存在于密钥环")


def _validate_datahub(values: dict[str, str]) -> None:
    value = values.get(DATAHUB_KEY, "")
    if not value:
        return
    try:
        decoded = bytes.fromhex(value)
    except ValueError as exc:
        raise KeyringError("DataHub 加密主密钥必须是 32 字节十六进制") from exc
    if len(decoded) != 32 or value.lower() != value:
        raise KeyringError("DataHub 加密主密钥必须是 32 字节十六进制")


def _new_values() -> tuple[str, str]:
    key_id = f"llm-key-{secrets.token_hex(12)}"
    encoded = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    return key_id, encoded


def _replace_or_append(
    text: str,
    key_id: str,
    encoded: str,
    datahub_key: str | None = None,
    keyring: dict[str, Any] | None = None,
) -> str:
    keyring = keyring or {key_id: encoded}
    replacements = {
        KEY_ID: key_id,
        KEYS: json.dumps(keyring, ensure_ascii=True, separators=(",", ":")),
    }
    if datahub_key is not None:
        replacements[DATAHUB_KEY] = datahub_key
    seen: set[str] = set()
    lines = text.splitlines(keepends=True)
    rendered: list[str] = []
    for line in lines:
        match = _ASSIGNMENT.match(line)
        if match and match.group(1) in replacements:
            name = match.group(1)
            newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            prefix = line[: line.index("=") + 1]
            rendered.append(f"{prefix}{replacements[name]}{newline}")
            seen.add(name)
        else:
            rendered.append(line)
    if rendered and not rendered[-1].endswith(("\n", "\r")):
        rendered.append("\n")
    if KEY_ID not in seen:
        rendered.append(f"{KEY_ID}={replacements[KEY_ID]}\n")
    if KEYS not in seen:
        rendered.append(f"{KEYS}={replacements[KEYS]}\n")
    if datahub_key is not None and DATAHUB_KEY not in seen:
        rendered.append(f"{DATAHUB_KEY}={datahub_key}\n")
    return "".join(rendered)


def _atomic_write(path: Path, content: str) -> None:
    parent = path.parent
    temp_path: str | None = None
    try:
        descriptor, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
        os.chmod(path, 0o600)
        try:
            directory_fd = os.open(parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError as exc:
        raise KeyringError("无法安全写入生产配置文件") from exc
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def ensure_keyring(path: Path) -> bool:
    """Ensure a valid keyring exists; return True when a new one was written."""

    text, values, occurrences = _read_env(path)
    _validate_datahub(values)
    datahub_key = values.get(DATAHUB_KEY) or secrets.token_hex(32)
    datahub_missing = not values.get(DATAHUB_KEY)
    if occurrences.get(KEY_ID, 0) == 0 and occurrences.get(KEYS, 0) == 0:
        key_id, encoded = _new_values()
        _atomic_write(path, _replace_or_append(text, key_id, encoded, datahub_key))
        return True
    if not values.get(KEY_ID, "") and not values.get(KEYS, ""):
        key_id, encoded = _new_values()
        _atomic_write(path, _replace_or_append(text, key_id, encoded, datahub_key))
        return True
    _validate_existing(values)
    if datahub_missing:
        keyring = _parse_json_object(values[KEYS])
        _atomic_write(
            path,
            _replace_or_append(
                text,
                values[KEY_ID],
                str(keyring[values[KEY_ID]]),
                datahub_key,
                keyring=keyring,
            ),
        )
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("错误：请提供生产配置文件路径", file=sys.stderr)
        return 2
    try:
        generated = ensure_keyring(Path(args[0]))
    except KeyringError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    if generated:
        print("已生成并保存 LLM 密钥环（文件权限已限制为 0600）")
    else:
        print("LLM 密钥环校验通过，未修改配置文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
