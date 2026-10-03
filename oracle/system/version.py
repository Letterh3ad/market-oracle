from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

__all__ = ["CODE_ROOT", "code_version", "decisions_at"]

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


def decisions_at(commit: str, root: Path = CODE_ROOT) -> tuple[str, str] | None:
    """`commit`'s full hash and its DECISIONS.md; None without a git checkout at `root`. Raises
    LookupError when the checkout has no such commit or the commit has no DECISIONS.md."""
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=10)
    try:
        if git("rev-parse", "--git-dir").returncode:
            return None
        full = git("rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
        if full.returncode:
            raise LookupError(f"no commit {commit} in this checkout")
        text = git("show", f"{full.stdout.strip()}:DECISIONS.md")
    except (OSError, subprocess.SubprocessError):
        return None
    if text.returncode:
        raise LookupError(f"commit {commit} has no DECISIONS.md")
    return full.stdout.strip(), text.stdout


def _files_hash(package: Path) -> str:
    digest = hashlib.sha1()
    for path in sorted(p for p in package.rglob("*") if p.is_file() and not _SKIP & set(p.parts)):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:7]
