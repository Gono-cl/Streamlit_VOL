"""Backward-compatible wrappers for running protocol persistence."""

from __future__ import annotations
import os
from pathlib import Path

from .running_protocols import (
    DEFAULT_RUNNING_PROTOCOL_DIR,
    LEGACY_PROCESS_PROFILE_DIR,
    delete_running_protocol,
    list_running_protocols,
    load_running_protocol,
    save_running_protocol,
    sanitize_protocol_name,
)


DEFAULT_PROCESS_PROFILE_DIR = DEFAULT_RUNNING_PROTOCOL_DIR


def sanitize_profile_name(name: str) -> str:
    return sanitize_protocol_name(name)


def _ensure_dir(base_dir: str = DEFAULT_PROCESS_PROFILE_DIR) -> None:
    os.makedirs(base_dir, exist_ok=True)


def _profile_path(profile_name: str, base_dir: str = DEFAULT_PROCESS_PROFILE_DIR) -> Path:
    safe_name = sanitize_profile_name(profile_name)
    return Path(base_dir) / f"{safe_name}.json"


def list_process_profiles(base_dir: str = DEFAULT_PROCESS_PROFILE_DIR) -> list[str]:
    return list_running_protocols(
        base_dir=base_dir,
        include_legacy=True,
        legacy_dir=LEGACY_PROCESS_PROFILE_DIR,
    )


def load_process_profile(profile_name: str, base_dir: str = DEFAULT_PROCESS_PROFILE_DIR):
    return load_running_protocol(
        profile_name,
        base_dir=base_dir,
        legacy_dir=LEGACY_PROCESS_PROFILE_DIR,
    )


def save_process_profile(profile_name: str, adapter: str, adapter_config: dict, base_dir: str = DEFAULT_PROCESS_PROFILE_DIR) -> str:
    return save_running_protocol(
        profile_name,
        adapter=adapter,
        adapter_config=adapter_config,
        base_dir=base_dir,
    )


def delete_process_profile(profile_name: str, base_dir: str = DEFAULT_PROCESS_PROFILE_DIR) -> bool:
    return delete_running_protocol(
        profile_name,
        base_dir=base_dir,
        allow_legacy=True,
        legacy_dir=LEGACY_PROCESS_PROFILE_DIR,
    )
