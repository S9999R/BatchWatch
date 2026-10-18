"""PostToolUse hook: after a .py file is edited, run the offline test suite (no API key needed).

Uses the first Python that has the project's dependencies: the macOS/Linux venv, the Windows venv, then
the system Python. In a git worktree (which has no .venv of its own) it also tries the main checkout's venv.
If none has them, it stays silent rather than failing every edit.
Exit code 2 sends the failing test output back to Claude.
"""
import json
import os
import shutil
import subprocess
import sys

path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
if not path.endswith(".py"):
    sys.exit(0)

root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
try:  # the main checkout is the parent of the shared .git directory
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            cwd=root, capture_output=True, text=True).stdout.strip()
except OSError:
    common = ""
roots = [root] + ([os.path.dirname(common)] if common else [])
candidates = [os.path.join(r, ".venv", sub) for r in roots for sub in ("bin/python", "Scripts/python.exe")]
candidates += [shutil.which("python3"), shutil.which("python")]


def usable(p: str | None) -> bool:
    try:  # a venv made on another OS exists but cannot run here (Exec format error)
        return bool(p) and os.path.exists(p) and subprocess.run(
            [p, "-c", "import pytest, fastapi, httpx, openpyxl"], capture_output=True).returncode == 0
    except OSError:
        return False


python = next((p for p in candidates if usable(p)), None)
if not python:
    sys.exit(0)

run = subprocess.run([python, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
                     cwd=root, capture_output=True, text=True, timeout=170)
if run.returncode not in (0, 5):  # 5 = no tests collected
    print("Tests failed after this edit:\n" + (run.stdout + run.stderr)[-3000:], file=sys.stderr)
    sys.exit(2)
