"""Freeze a run's output directory into a versioned, hash-verified experiment archive.

A result that backs a published claim must be reproducible from a known input at a known
commit, and must be detectably unchanged thereafter. ``out/`` is scratch: every run
overwrites it. This module copies a run into ``experiments/<id>/`` alongside a manifest
recording the commit, the environment, and a SHA-256 of every artifact, so a later reader
can prove the files are the ones the numbers came from.

The manifest is deliberately the same shape of evidence the demonstrator itself emits about
provenance records: content hashes plus the context needed to interpret them.

    python -m eval.archive freeze --name baseline-pre-review --note "before any fixes"
    python -m eval.archive verify --name baseline-pre-review
    python -m eval.archive list
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

MANIFEST_NAME = "MANIFEST.json"
MANIFEST_SCHEMA = "yprov4agents/experiment-manifest/1"
DEFAULT_ARCHIVE_ROOT = Path("experiments")

# Artifacts that describe the archive rather than belonging to the run.
_EXCLUDED_NAMES = frozenset({MANIFEST_NAME})


def _utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _sha256(path: Path) -> str:
    """Hash a file in chunks, so archive size is not bounded by memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _git(*args: str) -> str | None:
    """Run a git command, returning None when git or the repository is unavailable."""
    try:
        result = subprocess.run(
            ("git", *args), capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def git_provenance() -> dict[str, object]:
    """Describe the working tree the archive was taken from.

    ``dirty`` is the honest part: an archive taken from a modified tree cannot be
    reconstructed from its commit alone, and the manifest must say so.
    """
    status = _git("status", "--porcelain")
    return {
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "describe": _git("describe", "--tags", "--always", "--dirty"),
        "dirty": bool(status) if status is not None else None,
    }


def environment() -> dict[str, object]:
    """Capture the machine and interpreter. Absolute timings are meaningless without it."""
    try:
        import psutil

        cpu_logical = psutil.cpu_count(logical=True)
        cpu_physical = psutil.cpu_count(logical=False)
        memory_bytes: int | None = psutil.virtual_memory().total
    except ImportError:  # psutil lives in the `eval` extra; archiving must not require it
        import os

        cpu_logical, cpu_physical, memory_bytes = os.cpu_count(), None, None

    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_logical": cpu_logical,
        "cpu_physical": cpu_physical,
        "memory_bytes": memory_bytes,
    }


def _inventory(source: Path) -> tuple[list[dict[str, object]], int]:
    """Hash every file under `source`, returning (sorted records, total bytes)."""
    files: list[dict[str, object]] = []
    total = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.name in _EXCLUDED_NAMES:
            continue
        size = path.stat().st_size
        total += size
        files.append(
            {
                "path": path.relative_to(source).as_posix(),
                "size_bytes": size,
                "content_hash": _sha256(path),
            }
        )
    return files, total


def freeze(
    source: Path,
    name: str,
    *,
    archive_root: Path = DEFAULT_ARCHIVE_ROOT,
    note: str = "",
    overwrite: bool = False,
) -> Path:
    """Copy `source` into `archive_root/name/` and write its manifest.

    Raises
    ------
    FileNotFoundError
        `source` does not exist or holds no files.
    FileExistsError
        The destination exists and `overwrite` is False. Archives are immutable by
        default: silently replacing one would destroy the evidence it exists to preserve.
    """
    if not source.is_dir():
        raise FileNotFoundError(f"nothing to archive: {source} is not a directory")

    destination = archive_root / name
    if destination.exists():
        if not overwrite:
            raise FileExistsError(
                f"{destination} already exists; pass --overwrite to replace it"
            )
        shutil.rmtree(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)

    files, total = _inventory(destination)
    if not files:
        shutil.rmtree(destination)
        raise FileNotFoundError(f"nothing to archive: {source} holds no files")

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "name": name,
        "note": note,
        "created_at": _utc_now(),
        "source": source.as_posix(),
        "git": git_provenance(),
        "environment": environment(),
        "file_count": len(files),
        "total_bytes": total,
        "files": files,
    }
    (destination / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return destination


def verify(archive: Path) -> tuple[bool, list[str]]:
    """Re-hash an archive against its manifest.

    Returns (ok, problems). Detects modified, missing, and untracked files, so a reviewer
    can establish that an archive still holds exactly what was measured.
    """
    manifest_path = archive / MANIFEST_NAME
    if not manifest_path.is_file():
        return False, [f"no {MANIFEST_NAME} in {archive}"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    recorded = {str(entry["path"]): entry for entry in manifest["files"]}

    for relative, entry in sorted(recorded.items()):
        path = archive / relative
        if not path.is_file():
            problems.append(f"missing: {relative}")
            continue
        actual = _sha256(path)
        if actual != entry["content_hash"]:
            problems.append(
                f"modified: {relative} ({entry['content_hash'][:19]}… -> {actual[:19]}…)"
            )

    present = {
        path.relative_to(archive).as_posix()
        for path in archive.rglob("*")
        if path.is_file() and path.name not in _EXCLUDED_NAMES
    }
    problems.extend(f"untracked: {extra}" for extra in sorted(present - set(recorded)))

    return not problems, problems


def _cmd_freeze(args: argparse.Namespace) -> int:
    destination = freeze(
        Path(args.source),
        args.name,
        archive_root=Path(args.archive_root),
        note=args.note,
        overwrite=args.overwrite,
    )
    manifest = json.loads((destination / MANIFEST_NAME).read_text(encoding="utf-8"))
    print(
        f"[archive] froze {manifest['file_count']} file(s), "
        f"{manifest['total_bytes']:,} bytes -> {destination.as_posix()}"
    )
    git = manifest["git"]
    print(
        f"[archive] commit {git['describe'] or 'n/a'}"
        f"{' (DIRTY working tree)' if git['dirty'] else ''}"
    )
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    archive = Path(args.archive_root) / args.name
    ok, problems = verify(archive)
    if ok:
        print(f"[archive] {args.name}: OK — every file matches its recorded hash")
        return 0
    print(f"[archive] {args.name}: {len(problems)} problem(s)")
    for problem in problems:
        print(f"  ! {problem}")
    return 1


def _cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.archive_root)
    if not root.is_dir():
        print(f"[archive] no archives yet ({root.as_posix()} does not exist)")
        return 0
    rows = sorted(path for path in root.iterdir() if (path / MANIFEST_NAME).is_file())
    if not rows:
        print(f"[archive] no archives yet under {root.as_posix()}")
        return 0
    print(f"{'name':<34} {'created (UTC)':<28} {'files':>6} {'bytes':>12}  note")
    for path in rows:
        manifest = json.loads((path / MANIFEST_NAME).read_text(encoding="utf-8"))
        print(
            f"{manifest['name']:<34} {manifest['created_at']:<28} "
            f"{manifest['file_count']:>6} {manifest['total_bytes']:>12,}  "
            f"{manifest.get('note', '')}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.archive",
        description="Freeze and verify versioned experiment archives.",
    )
    parser.add_argument(
        "--archive-root",
        default=str(DEFAULT_ARCHIVE_ROOT),
        help=f"where archives live (default: {DEFAULT_ARCHIVE_ROOT})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze_parser = subparsers.add_parser("freeze", help="archive a run directory")
    freeze_parser.add_argument("--name", required=True, help="archive identifier")
    freeze_parser.add_argument("--source", default="out", help="directory to archive")
    freeze_parser.add_argument("--note", default="", help="why this archive exists")
    freeze_parser.add_argument(
        "--overwrite", action="store_true", help="replace an existing archive"
    )
    freeze_parser.set_defaults(func=_cmd_freeze)

    verify_parser = subparsers.add_parser("verify", help="re-hash an archive")
    verify_parser.add_argument("--name", required=True, help="archive identifier")
    verify_parser.set_defaults(func=_cmd_verify)

    list_parser = subparsers.add_parser("list", help="list archives")
    list_parser.set_defaults(func=_cmd_list)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
