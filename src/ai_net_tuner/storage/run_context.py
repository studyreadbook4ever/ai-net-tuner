from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ai_net_tuner.models import utc_like_now
from ai_net_tuner.paths import state_dir


@dataclass
class RunContext:
    run_id: str
    run_dir: Path
    decisions_csv: Path
    initial_sysctls: dict[str, str]


def make_run_id() -> str:
    return utc_like_now().replace(":", "").replace("+", "_").replace("-", "").replace("T", "_")


def create_run_context(initial_sysctls: dict[str, str]) -> RunContext:
    run_id = make_run_id()
    run_dir = state_dir() / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    context = {
        "run_id": run_id,
        "started_at": utc_like_now(),
        "initial_sysctl_count": len(initial_sysctls),
    }
    (run_dir / "run_context.json").write_text(
        json.dumps(context, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "initial_sysctls.json").write_text(
        json.dumps(initial_sysctls, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    return RunContext(
        run_id=run_id,
        run_dir=run_dir,
        decisions_csv=run_dir / "decisions.csv",
        initial_sysctls=initial_sysctls,
    )
