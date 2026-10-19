"""The running code's version: the git short SHA (shown by /healthz)."""

from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@functools.lru_cache(maxsize=1)
def version() -> str:
    """HEY_CLAUDE_VERSION or RENDER_GIT_COMMIT (a cloud image has no .git),
    else `git rev-parse --short HEAD`, else "unknown". Read once: it is the
    version this process started with, even if the checkout moves."""
    for var in ("HEY_CLAUDE_VERSION", "RENDER_GIT_COMMIT"):
        if os.environ.get(var):
            return os.environ[var].strip()[:7]
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            # exactly 7 (--short may give more), as the host compares sha[:7]
            return out.stdout.strip()[:7]
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"
