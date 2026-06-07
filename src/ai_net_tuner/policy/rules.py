from __future__ import annotations

import json
import fnmatch
import os
import re
from functools import cached_property
from typing import Any

from ai_net_tuner.collectors.sysctl_reader import list_net_sysctl_keys, read_sysctl_value
from ai_net_tuner.docs.retriever import SysctlKnowledgeBase
from ai_net_tuner.models import PolicyDecision, Proposal
from ai_net_tuner.paths import config_path


class PolicyEngine:
    def __init__(self) -> None:
        self.allowlist_path = config_path("policy_allowlist.json")
        self.ko_templates_path = config_path("ko_templates.json")

    @cached_property
    def allowlist(self) -> dict[str, Any]:
        return json.loads(self.allowlist_path.read_text(encoding="utf-8"))

    @cached_property
    def ko_templates(self) -> dict[str, Any]:
        return json.loads(self.ko_templates_path.read_text(encoding="utf-8"))

    def allowed_keys(self) -> list[str]:
        keys = set(self.allowlist["allowed"].keys())
        if self.allowlist.get("allow_documented_existing_net_sysctls", False):
            knowledge = SysctlKnowledgeBase()
            for key in knowledge.proposal_keys_from_host(list_net_sysctl_keys()):
                if not self._blocked_reason(key):
                    keys.add(key)
        return sorted(keys)

    def managed_file(self) -> str:
        return str(self.allowlist["managed_file"])

    def max_proposals_per_cycle(self) -> int:
        raw = os.environ.get("AI_NET_TUNER_MAX_PROPOSALS_PER_CYCLE")
        if raw:
            try:
                return max(1, int(raw))
            except ValueError:
                pass
        return int(self.allowlist.get("max_proposals_per_cycle", 1))

    def evaluate(self, proposal: Proposal) -> PolicyDecision:
        blocked = self._blocked_reason(proposal.key)
        if blocked:
            return self._decision(
                proposal,
                allowed=False,
                result="blocked",
                risk_level="blocked",
                reason=blocked,
            )

        rule = self.allowlist["allowed"].get(proposal.key)
        if not rule:
            if not self._permissive_candidate(proposal.key):
                return self._decision(
                    proposal,
                    allowed=False,
                    result="blocked",
                    risk_level="blocked",
                    reason="key is not documented as a permissive network sysctl candidate",
                )
            rule = {
                "type": "same_shape",
                "risk": self._knowledge_risk(proposal.key),
            }

        if proposal.current == "unknown":
            return self._decision(
                proposal,
                allowed=False,
                result="blocked",
                risk_level="blocked",
                reason="current value is unknown or unavailable on this host",
                value_hint=self._allowed_value_hint(rule),
            )

        valid, reason = self._validate_value(proposal.proposed, rule)
        if not valid:
            return self._decision(
                proposal,
                allowed=False,
                result="invalid",
                risk_level="blocked",
                reason=reason,
                value_hint=self._allowed_value_hint(rule),
            )

        current_normalized = " ".join(proposal.current.split())
        proposed_normalized = " ".join(proposal.proposed.split())
        if current_normalized == proposed_normalized:
            return self._decision(
                proposal,
                allowed=False,
                result="no_change",
                risk_level=str(rule.get("risk", proposal.risk_level)),
                reason="proposed value is identical to current value",
                value_hint=self._allowed_value_hint(rule),
            )

        return self._decision(
            proposal,
            allowed=True,
            result="allowed",
            risk_level=str(rule.get("risk", proposal.risk_level)),
            reason="proposal passed allowlist and value guardrails",
            value_hint=self._allowed_value_hint(rule),
        )

    def _blocked_reason(self, key: str) -> str | None:
        if key in self.allowlist.get("blocked_exact", []):
            return "key is explicitly blocked by policy"
        for pattern in self.allowlist.get("blocked_globs", []):
            if fnmatch.fnmatch(key, pattern):
                return f"key pattern is blocked by policy: {pattern}"
        for prefix in self.allowlist.get("blocked_prefixes", []):
            if key.startswith(prefix):
                return f"key prefix is blocked by policy: {prefix}"
        return None

    def _permissive_candidate(self, key: str) -> bool:
        if not self.allowlist.get("allow_documented_existing_net_sysctls", False):
            return False
        if not key.startswith("net."):
            return False
        entry = SysctlKnowledgeBase().by_key(key)
        if not entry:
            return False
        return entry.get("auto_tuning_role") != "block_by_default"

    def _knowledge_risk(self, key: str) -> str:
        entry = SysctlKnowledgeBase().by_key(key)
        if not entry:
            return str(self.allowlist.get("permissive_default_risk", "high"))
        if entry.get("auto_tuning_role") == "allow_with_strong_warning":
            return "high"
        return str(entry.get("risk", self.allowlist.get("permissive_default_risk", "high")))

    def _validate_value(self, value: str, rule: dict[str, Any]) -> tuple[bool, str]:
        value_type = rule["type"]
        if value_type == "int":
            try:
                parsed = int(value)
            except ValueError:
                return False, "value must be an integer"
            if parsed < int(rule["min"]) or parsed > int(rule["max"]):
                return False, f"value must be between {rule['min']} and {rule['max']}"
            return True, "ok"

        if value_type == "enum":
            if value not in set(rule["values"]):
                return False, f"value must be one of {rule['values']}"
            return True, "ok"

        if value_type == "port_range":
            try:
                low_raw, high_raw = value.split()
                low = int(low_raw)
                high = int(high_raw)
            except ValueError:
                return False, "port range must be two integers"
            if low >= high:
                return False, "port range low must be lower than high"
            min_low = int(rule["min_low"])
            min_low_sysctl = rule.get("min_low_sysctl")
            if min_low_sysctl:
                reference_value = read_sysctl_value(str(min_low_sysctl))
                try:
                    min_low = max(min_low, int(reference_value))
                except ValueError:
                    return False, f"could not read integer guardrail from {min_low_sysctl}"
            if low < min_low or low > int(rule["max_low"]):
                return False, f"low port must be between {min_low} and {rule['max_low']}"
            if high < int(rule["min_high"]) or high > int(rule["max_high"]):
                return False, f"high port must be between {rule['min_high']} and {rule['max_high']}"
            return True, "ok"

        if value_type == "int_triplet":
            try:
                parts = [int(part) for part in value.split()]
            except ValueError:
                return False, "triplet must contain integers"
            if len(parts) != 3:
                return False, "triplet must contain exactly three integers"
            if not (parts[0] <= parts[1] <= parts[2]):
                return False, "triplet must be nondecreasing"
            lower = int(rule["min_each"])
            upper = int(rule["max_each"])
            if any(part < lower or part > upper for part in parts):
                return False, f"each value must be between {lower} and {upper}"
            return True, "ok"

        if value_type == "same_shape":
            return self._validate_same_shape(value)

        return False, f"unsupported policy value type: {value_type}"

    def _validate_same_shape(self, value: str) -> tuple[bool, str]:
        if "\n" in value or "\r" in value or "\x00" in value:
            return False, "value must be a single-line sysctl value"
        if "=" in value:
            return False, "value must not contain '='"
        if len(value) > 256:
            return False, "value is too long for permissive sysctl mode"
        if not re.fullmatch(r"[A-Za-z0-9_./:+,\-\t ]*", value):
            return False, "value contains unsupported characters for permissive sysctl mode"
        parts = value.split()
        if not parts:
            return True, "ok"
        for part in parts:
            if re.fullmatch(r"-?\d+", part):
                parsed = int(part)
                if parsed < -2147483648 or parsed > 2147483647:
                    return False, "integer value is outside int32 guardrail"
        return True, "ok"

    def _allowed_value_hint(self, rule: dict[str, Any]) -> str:
        value_type = rule["type"]
        if value_type == "int":
            return f"{rule['min']} 이상 {rule['max']} 이하 정수"
        if value_type == "enum":
            return " | ".join(str(value) for value in rule["values"])
        if value_type == "port_range":
            low_hint = f"{rule['min_low']}-{rule['max_low']}"
            min_low_sysctl = rule.get("min_low_sysctl")
            if min_low_sysctl:
                reference_value = read_sysctl_value(str(min_low_sysctl))
                try:
                    effective_min_low = max(int(rule["min_low"]), int(reference_value))
                    low_hint = (
                        f"{effective_min_low}-{rule['max_low']} "
                        f"(현재 {min_low_sysctl}={reference_value})"
                    )
                except ValueError:
                    low_hint = (
                        f"{rule['min_low']}-{rule['max_low']} "
                        f"(단 {min_low_sysctl} 이상 필요)"
                    )
            parity_hint = ", low/high parity 다르게 권장" if rule.get("prefer_different_parity") else ""
            return (
                f"두 정수, low {low_hint}, "
                f"high {rule['min_high']}-{rule['max_high']}, low < high{parity_hint}"
            )
        if value_type == "int_triplet":
            return f"오름차순 정수 3개, 각 {rule['min_each']}-{rule['max_each']}"
        if value_type == "same_shape":
            return "한 줄 sysctl 값, 줄바꿈과 '=' 금지"
        return "정의되지 않은 값 형식"

    def _decision(
        self,
        proposal: Proposal,
        *,
        allowed: bool,
        result: str,
        risk_level: str,
        reason: str,
        value_hint: str | None = None,
    ) -> PolicyDecision:
        key_template = self.ko_templates.get("keys", {}).get(proposal.key)
        default_template = self.ko_templates["default"]
        blocked_warnings = self.ko_templates.get("blocked", {}).get(proposal.key)

        if blocked_warnings:
            warnings = blocked_warnings
        elif key_template:
            warnings = list(key_template.get("warnings", []))
        else:
            warnings = list(default_template.get("warnings", []))

        if value_hint:
            warnings.append(f"허용값: {value_hint}")

        return PolicyDecision(
            allowed=allowed,
            result=result,
            risk_level=risk_level,
            short_ko=(key_template or default_template).get("effect", default_template["effect"]),
            evidence_ko=(key_template or default_template).get("evidence", default_template["evidence"]),
            warnings_ko=warnings,
            reason=reason,
        )
