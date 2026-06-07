from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_net_tuner.paths import state_dir


def proposals_dir(base_dir: Path | None = None) -> Path:
    path = (base_dir or state_dir()) / "proposals"
    path.mkdir(parents=True, exist_ok=True)
    return path


def prompts_dir(base_dir: Path | None = None) -> Path:
    path = (base_dir or state_dir()) / "prompts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json_artifact(name: str, payload: dict[str, Any], *, base_dir: Path | None = None) -> Path:
    path = proposals_dir(base_dir) / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_prompt(name: str, prompt: str, *, base_dir: Path | None = None) -> Path:
    path = prompts_dir(base_dir) / name
    path.write_text(prompt, encoding="utf-8")
    return path
