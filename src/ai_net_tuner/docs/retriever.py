from __future__ import annotations

import json
import fnmatch
from functools import cached_property
from typing import Any

from ai_net_tuner.paths import data_path


class SysctlKnowledgeBase:
    def __init__(self) -> None:
        self.path = data_path("sysctl_knowledge_en.json")

    @cached_property
    def document(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    @cached_property
    def entries(self) -> list[dict[str, Any]]:
        return list(self.document.get("entries", []))

    def by_key(self, key: str) -> dict[str, Any] | None:
        for entry in self.entries:
            if entry.get("key") == key:
                return entry
            if key in entry.get("keys", []):
                return entry
            pattern = str(entry.get("key", ""))
            if "*" in pattern and fnmatch.fnmatch(key, pattern):
                return entry
            for alias_pattern in entry.get("keys", []):
                if "*" in alias_pattern and fnmatch.fnmatch(key, alias_pattern):
                    return entry
        return None

    def retrieve_for_keys(self, keys: list[str]) -> list[dict[str, Any]]:
        found = []
        seen = set()
        for key in keys:
            entry = self.by_key(key)
            if entry and entry.get("key") not in seen:
                found.append(entry)
                seen.add(entry.get("key"))
        return found

    def proposal_entries(self) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self.entries
            if entry.get("auto_tuning_role") != "block_by_default"
        ]

    def proposal_keys_from_host(self, host_keys: list[str]) -> list[str]:
        proposal_keys: set[str] = set()
        for entry in self.proposal_entries():
            key = str(entry.get("key", ""))
            patterns = [key, *(str(alias) for alias in entry.get("keys", []))]
            for pattern in patterns:
                if "*" in pattern:
                    proposal_keys.update(
                        host_key for host_key in host_keys if fnmatch.fnmatch(host_key, pattern)
                    )
                elif pattern in host_keys:
                    proposal_keys.add(pattern)
        return sorted(proposal_keys)

    def all_keys(self) -> list[str]:
        keys = []
        for entry in self.entries:
            if "key" in entry:
                keys.append(str(entry["key"]))
            keys.extend(str(key) for key in entry.get("keys", []))
        return keys
