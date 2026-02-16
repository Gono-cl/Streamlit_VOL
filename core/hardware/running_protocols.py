"""Persistence helpers for reusable running protocols.

Running protocols are user-facing saved presets that define:
- which process adapter to use
- adapter_config payload for that adapter

Backward compatibility:
- Legacy protocol files stored under ``process_profiles/`` are still discoverable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULT_RUNNING_PROTOCOL_DIR = "running_protocols"
LEGACY_PROCESS_PROFILE_DIR = "process_profiles"


def sanitize_protocol_name(name: str) -> str:
    if not isinstance(name, str):
        name = str(name)
    cleaned = name.strip().rstrip(".")
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        cleaned = cleaned.replace(ch, "_")
    cleaned = " ".join(cleaned.split())
    return cleaned or "running_protocol"


def _ensure_dir(base_dir: str = DEFAULT_RUNNING_PROTOCOL_DIR) -> None:
    os.makedirs(base_dir, exist_ok=True)


def _protocol_path(protocol_name: str, base_dir: str = DEFAULT_RUNNING_PROTOCOL_DIR) -> Path:
    safe_name = sanitize_protocol_name(protocol_name)
    return Path(base_dir) / f"{safe_name}.json"


def _list_names_from_dir(base_dir: str) -> list[str]:
    if not os.path.exists(base_dir):
        return []
    names = []
    for file_name in sorted(os.listdir(base_dir)):
        if file_name.lower().endswith(".json"):
            names.append(Path(file_name).stem)
    return names


def list_running_protocols(
    base_dir: str = DEFAULT_RUNNING_PROTOCOL_DIR,
    include_legacy: bool = True,
    legacy_dir: str = LEGACY_PROCESS_PROFILE_DIR,
) -> list[str]:
    _ensure_dir(base_dir)
    names = set(_list_names_from_dir(base_dir))
    if include_legacy and legacy_dir and legacy_dir != base_dir:
        names.update(_list_names_from_dir(legacy_dir))
    return sorted(names)


def load_running_protocol(
    protocol_name: str,
    base_dir: str = DEFAULT_RUNNING_PROTOCOL_DIR,
    legacy_dir: str = LEGACY_PROCESS_PROFILE_DIR,
):
    safe_name = sanitize_protocol_name(protocol_name)
    candidate_paths = [_protocol_path(safe_name, base_dir=base_dir)]
    if legacy_dir and legacy_dir != base_dir:
        candidate_paths.append(_protocol_path(safe_name, base_dir=legacy_dir))

    for path in candidate_paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if "adapter" not in payload:
            payload["adapter"] = payload.get("process_adapter")
        if "adapter_config" not in payload:
            payload["adapter_config"] = payload.get("config", {})
        payload["name"] = payload.get("name", safe_name)
        return payload
    return None


def save_running_protocol(
    protocol_name: str,
    adapter: str,
    adapter_config: dict,
    base_dir: str = DEFAULT_RUNNING_PROTOCOL_DIR,
) -> str:
    _ensure_dir(base_dir)
    safe_name = sanitize_protocol_name(protocol_name)
    payload = {
        "name": safe_name,
        "adapter": str(adapter),
        "adapter_config": dict(adapter_config or {}),
    }
    path = _protocol_path(safe_name, base_dir=base_dir)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return str(path)


def delete_running_protocol(
    protocol_name: str,
    base_dir: str = DEFAULT_RUNNING_PROTOCOL_DIR,
    allow_legacy: bool = True,
    legacy_dir: str = LEGACY_PROCESS_PROFILE_DIR,
) -> bool:
    safe_name = sanitize_protocol_name(protocol_name)
    deleted = False

    main_path = _protocol_path(safe_name, base_dir=base_dir)
    if main_path.exists():
        main_path.unlink()
        deleted = True

    if allow_legacy and legacy_dir and legacy_dir != base_dir:
        legacy_path = _protocol_path(safe_name, base_dir=legacy_dir)
        if legacy_path.exists():
            legacy_path.unlink()
            deleted = True

    return deleted
