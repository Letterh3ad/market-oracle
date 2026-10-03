from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["CODE_ROOT", "CommitDecisions", "code_version", "commit_decisions"]

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


@dataclass(frozen=True)
class CommitDecisions:
    full: str
    committed: datetime
    """Committer time, UTC."""
    on_head: bool
    """Reachable from the checkout's HEAD."""
    decisions: str
    parent_decisions: str
    """Its first parent's DECISIONS.md; empty for a root commit or one without the file."""


def commit_decisions(commit: str, root: Path = CODE_ROOT) -> CommitDecisions:
    """What the checkout at `root` knows about `commit`'s DECISIONS.md. Raises LookupError on
    anything git cannot answer (no checkout, unsafe directory, no git, a timeout, no such commit)."""
    def git(*args: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=10)
        except subprocess.TimeoutExpired as exc:
            raise LookupError("git timed out checking the commit") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise LookupError(f"git could not run to check the commit: {exc}") from exc

    if (out := git("rev-parse", "--git-dir")).returncode:
        raise LookupError(f"{root} is not a git checkout git can read: {out.stderr.strip()}")
    full = git("rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
    if full.returncode:
        raise LookupError(f"no commit {commit} in this checkout")
    sha = full.stdout.strip()
    ancestor = git("merge-base", "--is-ancestor", sha, "HEAD")
    if ancestor.returncode not in (0, 1):
        raise LookupError(f"git could not tell whether {commit} is on HEAD: {ancestor.stderr.strip()}")
    if (when := git("show", "-s", "--format=%ct", sha)).returncode:
        raise LookupError(f"git could not read {commit}'s date: {when.stderr.strip()}")
    if (text := git("show", f"{sha}:DECISIONS.md")).returncode:
        raise LookupError(f"commit {commit} has no DECISIONS.md")
    parent = git("rev-parse", "--verify", "--quiet", f"{sha}^1")
    before = git("show", f"{parent.stdout.strip()}:DECISIONS.md") if not parent.returncode else None
    return CommitDecisions(sha, datetime.fromtimestamp(int(when.stdout.strip()), timezone.utc),
                           ancestor.returncode == 0, text.stdout,
                           before.stdout if before is not None and not before.returncode else "")


def _files_hash(package: Path) -> str:
    digest = hashlib.sha1()
    for path in sorted(p for p in package.rglob("*") if p.is_file() and not _SKIP & set(p.parts)):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:7]
