import subprocess

from oracle.system.version import code_version


def package(root):
    (root / "oracle").mkdir()
    (root / "oracle" / "a.py").write_text("x = 1\n", encoding="utf-8")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def test_a_git_folder_is_known_by_its_commit(tmp_path):
    package(tmp_path)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one")
    assert code_version(tmp_path) == git(tmp_path, "rev-parse", "--short=7", "HEAD")


def test_without_git_the_package_files_are_hashed_so_edits_show_and_caches_do_not(tmp_path):
    package(tmp_path)
    first = code_version(tmp_path)
    assert first == code_version(tmp_path) and len(first) == 7
    (tmp_path / "oracle" / "__pycache__").mkdir()
    (tmp_path / "oracle" / "__pycache__" / "a.pyc").write_bytes(b"compiled")
    assert code_version(tmp_path) == first
    (tmp_path / "oracle" / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert code_version(tmp_path) != first
