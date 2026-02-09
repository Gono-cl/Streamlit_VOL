"""Persistence helpers for reusable process adapter profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULT_PROCESS_PROFILE_DIR = "process_profiles"


def sanitize_profile_name(name: str) -> str:
    if not isinstance(name, str):
        name = str(name)
    cleaned = name.strip().rstrip(".")
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        cleaned = cleaned.replace(ch, "_")
    cleaned = " ".join(cleaned.split())
    return cleaned or "process_profile"


def _ensure_dir(base_dir: str = DEFAULT_PROCESS_PROFILE_DIR) -> None:
    os.makedirs(base_dir, exist_ok=True)


def _profile_path(profile_name: str, base_dir: str = DEFAULT_PROCESS_PROFILE_DIR) -> Path:
    safe_name = sanitize_profile_name(profile_name)
    return Path(base_dir) / f"{safe_name}.json"


def list_process_profiles(base_dir: str = DEFAULT_PROCESS_PROFILE_DIR) -> list[str]:
    _ensure_dir(base_dir)
    profiles = []
    for file_name in sorted(os.listdir(base_dir)):
        if file_name.lower().endswith(".json"):
            profiles.append(Path(file_name).stem)
    return profiles


def load_process_profile(profile_name: str, base_dir: str = DEFAULT_PROCESS_PROFILE_DIR):
    path = _profile_path(profile_name, base_dir=base_dir)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if "adapter" not in payload:
        payload["adapter"] = payload.get("process_adapter")
    if "adapter_config" not in payload:
        payload["adapter_config"] = payload.get("config", {})
    payload["name"] = payload.get("name", sanitize_profile_name(profile_name))
    return payload


def save_process_profile(profile_name: str, adapter: str, adapter_config: dict, base_dir: str = DEFAULT_PROCESS_PROFILE_DIR) -> str:
    _ensure_dir(base_dir)
    safe_name = sanitize_profile_name(profile_name)
    payload = {
        "name": safe_name,
        "adapter": str(adapter),
        "adapter_config": dict(adapter_config or {}),
    }
    path = _profile_path(safe_name, base_dir=base_dir)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return str(path)


def delete_process_profile(profile_name: str, base_dir: str = DEFAULT_PROCESS_PROFILE_DIR) -> bool:
    path = _profile_path(profile_name, base_dir=base_dir)
    if path.exists():
        path.unlink()
        return True
    return False
