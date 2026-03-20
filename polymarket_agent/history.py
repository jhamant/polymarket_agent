from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class HistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_recent(self, limit: int) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        lines = self.path.read_text(encoding="utf-8").splitlines()
        recent = lines[-limit:]
        return [json.loads(line) for line in recent if line.strip()]

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")


def summarize_history(records: list[dict[str, Any]]) -> str:
    if not records:
        return "No prior decisions recorded yet."

    lines = []
    for record in records:
        decision = record.get("decision", {})
        market = record.get("market", {})
        lines.append(
            "- "
            f"{record.get('timestamp', 'unknown')} | "
            f"{market.get('slug', 'unknown-market')} | "
            f"{decision.get('action', 'unknown-action')} | "
            f"{decision.get('rationale', 'no rationale')}"
        )
    return "\n".join(lines)


def write_history_snapshot(
    *,
    store: HistoryStore,
    limit: int,
    output_path: Path,
) -> str:
    summary = summarize_history(store.load_recent(limit=limit))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(summary + "\n", encoding="utf-8")
    return summary
