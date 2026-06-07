from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ai_net_tuner.collectors.sysctl_reader import read_sysctl_value
from ai_net_tuner.models import Proposal
from ai_net_tuner.paths import state_dir
from ai_net_tuner.policy.rules import PolicyEngine


def _load_applied_state() -> dict[str, str]:
    path = state_dir() / "applied_state.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_applied_state(values: dict[str, str]) -> None:
    path = state_dir() / "applied_state.json"
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_sysctl_file(values: dict[str, str]) -> str:
    lines = [
        "# Managed by ai-net-tuner. Do not edit manually.",
        "# Source: human-approved AI networking optimization proposal.",
        "",
    ]
    for key in sorted(values):
        lines.append(f"{key} = {values[key]}")
    lines.append("")
    return "\n".join(lines)


def apply_proposal_file(proposal_file: Path, *, real: bool) -> int:
    payload: dict[str, Any] = json.loads(proposal_file.read_text(encoding="utf-8"))
    engine = PolicyEngine()
    proposal = Proposal(**payload["proposal"])
    proposal.current = read_sysctl_value(proposal.key)
    decision = engine.evaluate(proposal)
    if not decision.allowed:
        print(f"refusing to apply proposal after re-check: {decision.reason}")
        return 2

    managed_file = Path(engine.managed_file())
    key = proposal.key
    proposed = proposal.proposed

    if not real:
        print(f"dry-run apply: {key} = {proposed}")
        print(f"target file: {managed_file}")
        return 0

    if os.geteuid() != 0:
        print("real apply requires root. Run through sudo.")
        return 1

    rollback_dir = state_dir() / "rollback"
    rollback_dir.mkdir(parents=True, exist_ok=True)
    rollback_path = rollback_dir / f"{proposal.proposal_id}.json"
    rollback_path.write_text(
        json.dumps(
            {
                "proposal_id": proposal.proposal_id,
                "previous": {
                    key: read_sysctl_value(key),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    applied_state = _load_applied_state()
    applied_state[key] = proposed
    content = _render_sysctl_file(applied_state)

    tmp_path = managed_file.with_suffix(managed_file.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(managed_file)

    completed = subprocess.run(
        ["sysctl", "-p", str(managed_file)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        print(completed.stderr.strip() or completed.stdout.strip())
        return completed.returncode

    _save_applied_state(applied_state)
    print(completed.stdout.strip())
    return 0
