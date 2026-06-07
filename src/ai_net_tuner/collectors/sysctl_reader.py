from __future__ import annotations

import subprocess
from pathlib import Path


def key_to_proc_path(key: str) -> Path:
    return Path("/proc/sys") / Path(key.replace(".", "/"))


def proc_path_to_key(path: Path) -> str:
    return str(path.relative_to("/proc/sys")).replace("/", ".")


def list_net_sysctl_keys() -> list[str]:
    root = Path("/proc/sys/net")
    if not root.exists():
        return []
    return sorted(proc_path_to_key(path) for path in root.rglob("*") if path.is_file())


def read_sysctl_value(key: str) -> str:
    proc_path = key_to_proc_path(key)
    if proc_path.exists():
        try:
            return proc_path.read_text(encoding="utf-8").strip()
        except OSError:
            return "unknown"

    try:
        completed = subprocess.run(
            ["sysctl", "-n", key],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return "unknown"

    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip()


def read_many(keys: list[str]) -> dict[str, str]:
    return {key: read_sysctl_value(key) for key in keys}
