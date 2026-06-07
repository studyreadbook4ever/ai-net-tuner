from __future__ import annotations

import select
import sys
import time
from datetime import datetime, timedelta

from ai_net_tuner.models import ReviewedProposal


class C:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"


def color(text: str, code: str) -> str:
    return f"{code}{text}{C.RESET}"


def _risk_color(risk_level: str) -> str:
    if risk_level in {"blocked", "high"}:
        return C.RED + C.BOLD
    if risk_level == "medium":
        return C.YELLOW
    return C.GREEN


def render_reviewed_proposal(reviewed: ReviewedProposal, timeout_seconds: int) -> None:
    proposal = reviewed.proposal
    policy = reviewed.policy
    width = 58
    line = "─" * width
    risk_code = _risk_color(policy.risk_level)

    print(color(line, C.DIM))
    print(
        f"{color('sysctl proposal', C.BOLD)}"
        f"  {proposal.created_at[-14:-6]}"
        f"  timeout={timeout_seconds}s"
    )
    print(color(line, C.DIM))
    print(color(proposal.key, C.WHITE + C.BOLD))
    print()
    print(f"현재  {proposal.current}")
    print(f"제안  {proposal.proposed}")
    print()
    print(f"{color('효과', C.GREEN)}  {policy.short_ko}")
    print(f"{color('근거', C.CYAN)}  {policy.evidence_ko}")
    for warning in policy.warnings_ko:
        label = "위험" if policy.risk_level in {"high", "blocked"} else "주의"
        print(f"{color(label, risk_code)}  {warning}")
    if policy.result != "allowed":
        print()
        print(f"{color('차단', C.RED + C.BOLD)}  {policy.reason}")
    print(color(line, C.DIM))


def ask_yes_no(timeout_seconds: int) -> tuple[str, str]:
    if not sys.stdin.isatty():
        print("비대화형 입력 감지: 자동 n")
        return "n", "noninteractive_n"

    deadline = time.monotonic() + timeout_seconds
    timeout_at = (datetime.now().astimezone() + timedelta(seconds=timeout_seconds)).strftime("%H:%M:%S")
    while True:
        remaining = max(0, int(deadline - time.monotonic()))
        if remaining <= 0:
            print("\ntimeout -> n")
            return "n", "timeout_n"

        prompt = f"적용할까요? [y/n] ({timeout_at}에 timeout됩니다): "
        print(prompt, end="", flush=True)
        ready, _, _ = select.select([sys.stdin], [], [], remaining)
        if not ready:
            print("\ntimeout -> n")
            return "n", "timeout_n"

        answer = sys.stdin.readline().strip().lower()
        if answer in {"y", "n"}:
            return answer, "user"
        print("y 또는 n만 입력 가능합니다.")
