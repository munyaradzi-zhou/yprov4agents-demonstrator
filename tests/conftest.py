"""Shared fixtures.

Tests fall into two groups, and the distinction matters when reading failures:

*Unit* tests drive the instrumentation library directly and need no pipeline run. They are
the authoritative tests — fast, deterministic, and independent of whatever happens to be
sitting in ``out/``.

*Artifact* tests inspect the provenance an actual run emitted. They require a run to exist
and will skip, loudly, when one does not. They are what catches an integration defect that
every unit test passes through.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--prov-dir",
        default="out",
        help="directory holding emitted *_prov.json artifacts (default: out)",
    )


@pytest.fixture(scope="session")
def prov_dir(pytestconfig: pytest.Config) -> Path:
    """Directory holding a run's emitted provenance. Skips the module when absent."""
    path = Path(pytestconfig.getoption("--prov-dir"))
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.is_dir():
        pytest.skip(
            f"no run directory at {path} — generate one with `python run_local.py` "
            f"or point --prov-dir at an archive under experiments/"
        )
    return path


@pytest.fixture(scope="session")
def prov_documents(prov_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Every emitted PROV-JSON document in the run directory, as (path, parsed)."""
    paths = sorted(prov_dir.glob("*_prov.json"))
    if not paths:
        pytest.skip(f"no *_prov.json in {prov_dir} — the run produced no provenance")
    return [(p, json.loads(p.read_text(encoding="utf-8"))) for p in paths]


@pytest.fixture(scope="session")
def store_manifest(prov_dir: Path) -> dict[str, Any]:
    """The content-addressed store's manifest for the run."""
    path = prov_dir / "store" / "_manifest.json"
    if not path.is_file():
        pytest.skip(f"no store manifest at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def cited_pids(documents: list[tuple[Path, dict[str, Any]]]) -> dict[str, set[str]]:
    """Map each ``yp:pid`` appearing in a provenance graph to the files citing it.

    A PID in a published graph is a promise that the payload can be fetched. Collecting
    them by citing file makes a broken promise traceable to the agent that made it.
    """
    pids: dict[str, set[str]] = {}
    for path, doc in documents:
        for entity in (doc.get("entity") or {}).values():
            pid = entity.get("yp:pid")
            if pid:
                pids.setdefault(str(pid), set()).add(path.name)
    return pids
