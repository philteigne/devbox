from __future__ import annotations

from pathlib import Path


def devbox_home() -> Path:
    return Path(__file__).resolve().parents[2]


def app_dir() -> Path:
    return devbox_home() / "app"


def secrets_dir() -> Path:
    return devbox_home() / "secrets"


def runtime_dir() -> Path:
    return devbox_home() / "runtime"


def config_dir() -> Path:
    return devbox_home() / "config"


def run_dir() -> Path:
    return devbox_home() / ".run"


def ensure_runtime_dirs() -> None:
    app_dir().mkdir(parents=True, exist_ok=True)
    secrets_dir().mkdir(parents=True, exist_ok=True)
    config_dir().mkdir(parents=True, exist_ok=True)
    run_dir().mkdir(parents=True, exist_ok=True)

