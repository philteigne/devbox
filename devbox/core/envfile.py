from __future__ import annotations

from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _strip_inline_comment(value.strip())
    return values


def write_env(path: Path, values: dict[str, str], order: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(order or [])
    keys.extend(k for k in values if k not in keys)
    body = "".join(f"{key}={values.get(key, '')}\n" for key in keys)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)


def _strip_inline_comment(value: str) -> str:
    if not value:
        return value
    if value[0] in {"'", '"'} and value[-1:] == value[0]:
        return value[1:-1]
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value
