from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import paths
from .errors import DevboxError


_TOP_LEVEL_KEYS = {"version", "tools", "apt", "env", "ports", "command"}
_BOOLEAN_TOOL_KEYS = ("codex", "opencode", "claude", "agy", "fvm")
_TOOL_KEYS = {*_BOOLEAN_TOOL_KEYS, "node", "bun"}
_PACKAGE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9+_.-]*$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NODE_VERSION = re.compile(r"^[1-9][0-9]*(?:\.[0-9]+\.[0-9]+)?$")
_BUN_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_DEFAULT_COMMAND = ("sleep", "infinity")


ToolValue = bool | str | tuple[str, ...]


@dataclass(frozen=True)
class LaunchConfig:
    version: int
    tools: dict[str, ToolValue]
    apt: tuple[str, ...]
    env: dict[str, str]
    ports: tuple[int, ...]
    command: tuple[str, ...]
    path: Path | None
    source_hash: str

    def normalized_hash(self) -> str:
        payload = {
            "version": self.version,
            "tools": self.tools,
            "apt": self.apt,
            "env": self.env,
            "ports": self.ports,
            "command": self.command,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def path_for(owner: str, repo: str) -> Path:
    return paths.config_dir() / owner / repo / "launch.yml"


def default_config() -> LaunchConfig:
    return LaunchConfig(
        version=1,
        tools={key: False for key in _BOOLEAN_TOOL_KEYS},
        apt=(),
        env={},
        ports=(),
        command=_DEFAULT_COMMAND,
        path=None,
        source_hash="",
    )


def load(owner: str, repo: str) -> LaunchConfig:
    path = path_for(owner, repo)
    if not path.exists():
        return default_config()

    try:
        source = path.read_bytes()
        data = yaml.safe_load(source)
    except (OSError, yaml.YAMLError) as exc:
        raise _invalid(path, f"could not read YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise _invalid(path, "top level must be an object")

    unknown = [key for key in data if key not in _TOP_LEVEL_KEYS]
    if unknown:
        raise _invalid(path, f"unknown key `{unknown[0]}`")

    version = data.get("version")
    if type(version) is not int:
        raise _invalid(path, "version must be an integer")
    if version != 1:
        raise _invalid(path, f"unsupported version `{version}`")

    tools = _validate_tools(path, data.get("tools", {}))
    apt = _validate_apt(path, data.get("apt", []))
    env = _validate_env(path, data.get("env", {}))
    ports = _validate_ports(path, data.get("ports", []))
    command = _validate_command(path, data.get("command", list(_DEFAULT_COMMAND)))

    return LaunchConfig(
        version=version,
        tools=tools,
        apt=apt,
        env=env,
        ports=ports,
        command=command,
        path=path,
        source_hash=hashlib.sha256(source).hexdigest(),
    )


def _validate_tools(path: Path, value: Any) -> dict[str, ToolValue]:
    if not isinstance(value, dict):
        raise _invalid(path, "tools must be an object")
    unknown = [key for key in value if key not in _TOOL_KEYS]
    if unknown:
        raise _invalid(path, f"unknown tools key `{unknown[0]}`")
    tools: dict[str, ToolValue] = {}
    for key in _BOOLEAN_TOOL_KEYS:
        enabled = value.get(key, False)
        if type(enabled) is not bool:
            raise _invalid(path, f"tools.{key} must be a boolean")
        tools[key] = enabled
    node = value.get("node")
    if node is not None:
        tools["node"] = _validate_node(path, node)
    bun = value.get("bun")
    if bun is not None:
        if not isinstance(bun, str) or not _BUN_VERSION.fullmatch(bun):
            raise _invalid(
                path,
                "tools.bun must be an exact semantic version string",
            )
        tools["bun"] = bun
    return tools


def _validate_node(path: Path, value: Any) -> str | tuple[str, ...]:
    is_list = isinstance(value, list)
    raw_versions = value if is_list else [value]
    if not raw_versions:
        raise _invalid(
            path,
            "tools.node must be a positive version or a non-empty list of versions",
        )

    versions: list[str] = []
    for index, raw_version in enumerate(raw_versions):
        version = str(raw_version) if type(raw_version) is int else raw_version
        if not isinstance(version, str) or not _NODE_VERSION.fullmatch(version):
            field = f"tools.node[{index}]" if is_list else "tools.node"
            raise _invalid(
                path,
                f"{field} must be a positive major version or semantic version string",
            )
        if version not in versions:
            versions.append(version)
    return tuple(versions) if is_list else versions[0]


def _validate_apt(path: Path, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _invalid(path, "apt must be a list of package names")
    packages: list[str] = []
    seen: set[str] = set()
    for index, package in enumerate(value):
        if not isinstance(package, str):
            raise _invalid(path, f"apt[{index}] must be a string")
        if not _PACKAGE_NAME.fullmatch(package):
            raise _invalid(path, f"apt[{index}] contains invalid package name `{package}`")
        if package not in seen:
            packages.append(package)
            seen.add(package)
    return tuple(packages)


def _validate_env(path: Path, value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise _invalid(path, "env must be an object")
    env: dict[str, str] = {}
    for key, raw_value in value.items():
        if not isinstance(key, str) or not _ENV_NAME.fullmatch(key):
            raise _invalid(path, f"env contains invalid variable name `{key}`")
        if raw_value is None:
            continue
        if not isinstance(raw_value, (str, int, float, bool)):
            raise _invalid(path, f"env.{key} must be a string, number, boolean, or null")
        env[key] = str(raw_value)
    return env


def _validate_ports(path: Path, value: Any) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise _invalid(path, "ports must be a list of integers")
    ports: list[int] = []
    seen: set[int] = set()
    for index, port in enumerate(value):
        if type(port) is not int or not 1 <= port <= 65535:
            raise _invalid(path, f"ports[{index}] must be an integer from 1 to 65535")
        if port not in seen:
            ports.append(port)
            seen.add(port)
    return tuple(ports)


def _validate_command(path: Path, value: Any) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(argument, str) or not argument for argument in value)
    ):
        raise _invalid(path, "command must be a non-empty list of non-empty strings")
    return tuple(value)


def _invalid(path: Path, detail: str) -> DevboxError:
    return DevboxError(f"invalid launch config `{path}`: {detail}")
