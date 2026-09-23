from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

__all__ = ["CODE_ROOT", "code_version"]

CODE_ROOT = Path(__file__).resolve().parents[2]
"""The folder holding the `oracle` package: a git checkout, or a packaged build."""

_SKIP = {"__pycache__"}


def code_version(root: Path = CODE_ROOT) -> str:
    """The checkout's short commit, or, with no git, a hash of the package's files."""
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=7", "HEAD"], capture_output=True,
                             text=True, timeout=5, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return _files_hash(root / "oracle")


def _files_hash(package: Path) -> str:
    digest = hashlib.sha1()
    for path in sorted(p for p in package.rglob("*") if p.is_file() and not _SKIP & set(p.parts)):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:7]
