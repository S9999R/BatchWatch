"""PreToolUse hook: refuse edits to secrets and to the frozen evaluation data.

The published accuracy numbers are only valid while the hand-labelled gold set and the snapshot it was
labelled on stay exactly as they are. Exit code 2 blocks the edit and shows the message to Claude.
"""
import json
import sys
from pathlib import PurePath

PROTECTED = {
    ".env": "holds the real NEBIUS_API_KEY and TAVILY_API_KEY",
    "eval/gold_batches.json": "is the hand-labelled gold set behind the published results",
    "eval/alerts_snapshot.jsonl": "is the frozen text the gold set was labelled on",
    "registry/seed_alerts.jsonl": "is the evaluated run U3 the demo serves without a live registry",
}

path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
parts = PurePath(path.replace("\\", "/")).parts
for rel, why in PROTECTED.items():
    rel_parts = PurePath(rel).parts
    if tuple(parts[-len(rel_parts):]) == rel_parts:
        print(f"Blocked: {rel} {why}. Ask the user to change it by hand if this is intended.", file=sys.stderr)
        sys.exit(2)
