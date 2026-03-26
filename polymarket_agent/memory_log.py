"""
Persistent memory store for cross-run lessons.

After each reconciliation stage, the runner extracts the `memory_entry`
object from 12-reconciliation.json and appends it here. Future runs
include family-relevant memories in the 00-history.md snapshot so agents
can recall patterns across many runs, not just recent decisions.

Schema of each line in memory_log.jsonl:
{
  "appended_at": "<ISO UTC>",
  "market_slug": str,
  "market_family": str,
  "decision": str,
  "key_lesson": str,
  "reuse_pattern": str | null,
  "avoid_pattern": str | null
}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def append_memory_entry(path: Path, entry: dict[str, Any]) -> None:
    """Stamp entry with appended_at (ISO UTC) and append one JSON line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = dict(entry)
    stamped["appended_at"] = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(stamped, sort_keys=True))
        handle.write("\n")


def load_family_memories(path: Path, family_key: str, limit: int = 10) -> list[dict[str, Any]]:
    """Read all lines, filter by market_family == family_key, return last limit entries."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("market_family") == family_key:
            entries.append(entry)
    return entries[-limit:]


def format_memory_summary(memories: list[dict[str, Any]]) -> str:
    """Render memory entries as markdown bullets."""
    if not memories:
        return "No family memory entries recorded yet."
    lines = []
    for entry in memories:
        appended_at = entry.get("appended_at", "unknown")
        market_slug = entry.get("market_slug", "unknown")
        decision = entry.get("decision", "unknown")
        key_lesson = entry.get("key_lesson", "")
        lines.append(f"- {appended_at} | {market_slug} | {decision} | {key_lesson}")
        reuse_pattern = entry.get("reuse_pattern")
        if reuse_pattern:
            lines.append(f"  - reuse: {reuse_pattern}")
        avoid_pattern = entry.get("avoid_pattern")
        if avoid_pattern:
            lines.append(f"  - avoid: {avoid_pattern}")
    return "\n".join(lines)
