from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    env_root = os.environ.get("AI_NET_TUNER_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "data").exists():
            return candidate
    return cwd


def data_path(*parts: str) -> Path:
    return project_root() / "data" / Path(*parts)


def config_path(*parts: str) -> Path:
    return project_root() / "config" / Path(*parts)


def state_dir() -> Path:
    path = project_root() / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def datasets_dir() -> Path:
    path = project_root() / "datasets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = project_root() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path
