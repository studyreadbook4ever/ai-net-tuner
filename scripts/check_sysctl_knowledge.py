from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def main() -> int:
    path = Path("data/sysctl_knowledge_en.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    keys = []
    entry_keys = []
    for entry in data["entries"]:
        entry_keys.append(entry["key"])
        keys.append(entry["key"])
        keys.extend(entry.get("keys", []))

    duplicate_entries = [key for key, count in Counter(entry_keys).items() if count > 1]
    duplicate_covered = [key for key, count in Counter(keys).items() if count > 1]

    print(f"entries={len(data['entries'])}")
    print(f"covered_keys={len(keys)}")
    print(f"duplicate_entry_keys={len(duplicate_entries)}")
    print(f"duplicate_covered_keys={len(duplicate_covered)}")
    for key in duplicate_covered:
        print(f"duplicate: {key}")

    return 1 if duplicate_entries or duplicate_covered else 0


if __name__ == "__main__":
    raise SystemExit(main())
