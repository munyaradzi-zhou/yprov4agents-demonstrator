"""An archive's manifest must say truthfully whether its tree was dirty, and how to rebuild it.

`freeze` read `git status` after copying the run into ``experiments/<name>/`` — itself an
untracked change — so every one of the project's eleven archives reported a dirty tree,
including any taken from a clean one (docs/AUDIT.md, finding [4]). This module had no
tests, which is how that survived.

Each test runs in a throwaway git repository, so "clean" and "dirty" are real states of a
real tree rather than mocked return values.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from eval.archive import DIFF_NAME, MANIFEST_NAME, freeze, verify


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args),
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A committed repository with one source file and a run directory to archive."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "agent.py").write_text("THRESHOLD = 3\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("out/\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    run = tmp_path / "out"
    run.mkdir()
    (run / "record_prov.json").write_text('{"activity": {}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _manifest(archive: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads((archive / MANIFEST_NAME).read_text(encoding="utf-8"))
    return manifest


def test_an_archive_of_a_clean_tree_says_it_is_clean(repo: Path) -> None:
    archive = freeze(Path("out"), "clean")
    git = _manifest(archive)["git"]
    assert git["dirty"] is False, "the archive's own new directory counted as a change"
    assert git["changes"] == []
    assert not (archive / DIFF_NAME).exists()


def test_an_archive_of_a_modified_tree_says_what_was_modified(repo: Path) -> None:
    (repo / "agent.py").write_text("THRESHOLD = 4\n", encoding="utf-8")
    archive = freeze(Path("out"), "dirty")
    git = _manifest(archive)["git"]
    assert git["dirty"] is True
    assert any("agent.py" in str(change) for change in git["changes"])


def test_the_saved_patch_rebuilds_the_code_that_produced_the_run(repo: Path) -> None:
    """Commit plus patch: the archive is reconstructible even from a modified tree."""
    (repo / "agent.py").write_text("THRESHOLD = 4\n", encoding="utf-8")
    archive = freeze(Path("out"), "dirty")
    patch = archive / DIFF_NAME
    assert patch.is_file()

    _git(repo, "checkout", "--", "agent.py")
    assert (repo / "agent.py").read_text(encoding="utf-8") == "THRESHOLD = 3\n"
    _git(repo, "apply", str(patch))
    assert (repo / "agent.py").read_text(encoding="utf-8") == "THRESHOLD = 4\n"


def test_the_patch_is_covered_by_the_manifest(repo: Path) -> None:
    (repo / "agent.py").write_text("THRESHOLD = 4\n", encoding="utf-8")
    archive = freeze(Path("out"), "dirty")
    recorded = {entry["path"] for entry in _manifest(archive)["files"]}
    assert DIFF_NAME in recorded
    assert verify(archive) == (True, [])


def test_require_clean_refuses_a_modified_tree_and_writes_nothing(repo: Path) -> None:
    (repo / "agent.py").write_text("THRESHOLD = 4\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=re.escape("agent.py")):
        freeze(Path("out"), "refused", require_clean=True)
    assert not (repo / "experiments" / "refused").exists()


def test_require_clean_accepts_a_clean_tree(repo: Path) -> None:
    archive = freeze(Path("out"), "accepted", require_clean=True)
    assert _manifest(archive)["git"]["dirty"] is False


def test_an_archive_is_not_replaced_unless_asked(repo: Path) -> None:
    freeze(Path("out"), "once")
    with pytest.raises(FileExistsError):
        freeze(Path("out"), "once")


def test_verify_reports_modified_missing_and_untracked_files(repo: Path) -> None:
    archive = freeze(Path("out"), "checked")
    assert verify(archive) == (True, [])

    (archive / "record_prov.json").write_text('{"activity": {"x": {}}}', encoding="utf-8")
    (archive / "extra.json").write_text("{}", encoding="utf-8")
    ok, problems = verify(archive)
    assert not ok
    assert any(p.startswith("modified: record_prov.json") for p in problems)
    assert "untracked: extra.json" in problems

    (archive / "record_prov.json").unlink()
    _, problems = verify(archive)
    assert "missing: record_prov.json" in problems
