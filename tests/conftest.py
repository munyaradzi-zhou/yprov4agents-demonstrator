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
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest

AgentT = TypeVar("AgentT")

REPO_ROOT = Path(__file__).resolve().parent.parent

# `--prov-dir` is declared in the root conftest.py, not here: pytest only honours
# pytest_addoption in a conftest at the rootdir.


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


@pytest.fixture
def headless_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[..., Any]:
    """Build a real agent with no bus and a temporary output volume.

    Constructed through its own ``__init__`` rather than ``__new__``: an agent assembled
    attribute-by-attribute in a fixture silently stops matching the constructor the moment
    the base class gains a field, and then every test using it fails for a reason that has
    nothing to do with what it was testing. That happened once already, when
    ``StreamingAgent`` gained an energy tracker.

    ``OUT_DIR`` is patched both in ``runtime.util`` and in the agent's own module, because
    agents bind it at import time.
    """

    def build(agent_class: type[AgentT], **kwargs: Any) -> AgentT:
        import runtime.util

        monkeypatch.setattr(runtime.util, "OUT_DIR", tmp_path)
        monkeypatch.setattr(
            sys.modules[agent_class.__module__], "OUT_DIR", tmp_path, raising=False
        )
        # No key and no requirement: the deterministic path, unless a test says otherwise.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("LLM_REQUIRED", raising=False)
        return agent_class(None, **kwargs)  # type: ignore[call-arg]

    return build
