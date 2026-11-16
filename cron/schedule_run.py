#!/usr/bin/env python3
"""Nightly Encore schedule job (no agent): Lighthouse API -> iCloud calendar -> outbox.txt.
Example no-agent cron wrapper: runs run.py and prints its message (exit code 1 if the run failed).
Set LIGHTHOUSE_DIR to the checkout (default: the parent of this folder)."""
import os, subprocess, sys
from pathlib import Path
P = Path(os.environ.get("LIGHTHOUSE_DIR") or Path(__file__).resolve().parent.parent)
r = subprocess.run([os.environ.get("LIGHTHOUSE_PY", str(P / "venv/bin/python")), "-W", "ignore", str(P / "run.py")], capture_output=True, text=True, timeout=600, cwd=P)
print(r.stdout.strip() or r.stderr.strip()[-500:] or "Encore schedule run produced no output.")
sys.exit(r.returncode)
