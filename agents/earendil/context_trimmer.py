from __future__ import annotations

import json
import sys
from pathlib import Path

WARNING_THRESHOLD = 0.80
CRITICAL_THRESHOLD = 0.85
TARGET_USAGE = 0.50


def get_session_context_file() -> Path | None:
    workspace = Path.home() / ".openclaw" / "workspace"
    sessions_file = workspace / "sessions.json"
    if sessions_file.exists():
        return sessions_file

    agents_file = Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
    if agents_file.exists():
        return agents_file

    return None


def calculate_usage(current_tokens: int, max_tokens: int) -> float:
    if max_tokens == 0:
        return 0.0
    return current_tokens / max_tokens


def trim_session_history(sessions_file: Path, target_usage: float) -> bool:
    try:
        with open(sessions_file) as f:
            sessions = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("Cannot read sessions file")
        return False

    if not sessions or "messages" not in sessions:
        print("No messages to trim")
        return False

    messages = sessions.get("messages", [])
    original_count = len(messages)

    target_message_count = max(10, int(original_count * target_usage))

    if len(messages) <= target_message_count:
        print(f"Already at target ({len(messages)} messages)")
        return True

    trimmed_messages = messages[-target_message_count:]
    trimmed_count = original_count - len(trimmed_messages)

    sessions["messages"] = trimmed_messages

    try:
        with open(sessions_file, "w") as f:
            json.dump(sessions, f, indent=2)
        print(f"Trimmed {trimmed_count} old messages")
        print(f"  Kept: {len(trimmed_messages)} recent messages")
        return True
    except Exception as e:
        print(f"Failed to write sessions: {e}")
        return False


def check_and_trim(current_tokens: int, max_tokens: int) -> tuple[str, float]:
    usage = calculate_usage(current_tokens, max_tokens)
    usage_percent = usage * 100

    if usage >= CRITICAL_THRESHOLD:
        print(f"CRITICAL: {usage_percent:.1f}% (threshold: {CRITICAL_THRESHOLD*100}%)")
        sessions_file = get_session_context_file()
        if sessions_file:
            trim_session_history(sessions_file, TARGET_USAGE)
        return "CRITICAL", usage_percent

    if usage >= WARNING_THRESHOLD:
        print(f"WARNING: {usage_percent:.1f}% (threshold: {WARNING_THRESHOLD*100}%)")
        return "WARNING", usage_percent

    print(f"OK: {usage_percent:.1f}%")
    return "OK", usage_percent


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: context_trimmer.py <current_tokens> <max_tokens>")
        sys.exit(1)
    try:
        current = int(sys.argv[1])
        max_tokens = int(sys.argv[2])
        status, _ = check_and_trim(current, max_tokens)
        sys.exit({"OK": 0, "WARNING": 1, "CRITICAL": 2}[status])
    except ValueError:
        print("Usage: context_trimmer.py <current_tokens> <max_tokens>")
        sys.exit(1)
