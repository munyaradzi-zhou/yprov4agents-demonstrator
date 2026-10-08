"""Integrity: can a PID in a published graph actually be dereferenced?

The lightweight-prov design puts a pointer in the provenance graph and the payload in a
content-addressed store, so the graph grows with the number of steps rather than the size
of the data. That trade is only sound if the pointer resolves. A ``yp:pid`` nobody can
follow is strictly worse than an inline value: it reads as evidence while supplying none.

These tests ask the only question that matters about the design — *can a third party fetch
what the graph promises?* — from two angles: within a process, and from a cold start, which
is the situation any later reader is actually in.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import cited_pids
from yprov4agenticai import LightweightStore

PAYLOAD = "a full tool output that does not belong inline in a provenance graph"


# ──────────────────────────────────────────────────────────────────────────────────
# Library behaviour
# ──────────────────────────────────────────────────────────────────────────────────
def test_put_returns_a_pointer_not_a_payload() -> None:
    store = LightweightStore()
    pointer = store.put(PAYLOAD, kind="tool-result")

    assert pointer["yp:pid"].startswith("21.T11999/yprovstore-")
    assert pointer["yp:contentHash"].startswith("sha256:")
    assert PAYLOAD not in str(pointer), "the pointer must not embed the payload"


def test_resolve_returns_the_payload_within_one_process(tmp_path: Path) -> None:
    store = LightweightStore(root=tmp_path)
    pointer = store.put(PAYLOAD)

    assert store.resolve(pointer["yp:pid"]) == PAYLOAD


def test_resolve_detects_a_corrupted_blob_on_disk(tmp_path: Path) -> None:
    """Corrupt the stored file; a real integrity check must notice."""
    store = LightweightStore(root=tmp_path)
    pointer = store.put(PAYLOAD)
    store.save()

    blob = next(path for path in tmp_path.glob("*.txt"))
    blob.write_text("corrupted on disk", encoding="utf-8")

    with pytest.raises(ValueError, match="content-hash mismatch"):
        store.resolve(pointer["yp:pid"], verify=True)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("one line, no breaks", id="single-line"),
        pytest.param("first line\nsecond line\nthird", id="unix-newlines"),
        pytest.param("first line\r\nsecond line", id="windows-newlines"),
        pytest.param("trailing break\n", id="trailing-newline"),
        pytest.param("ünïcödé — and a σ", id="non-ascii"),
    ],
)
def test_stored_bytes_hash_to_what_the_pointer_promised(tmp_path: Path, payload: str) -> None:
    """The bytes on disk must hash to the ``yp:contentHash`` the graph asserts.

    This is the regression guard for a defect that would only have appeared on Windows and
    only for multi-line payloads: hashing the in-memory string (one byte per line break)
    while writing in text mode (two bytes per line break) makes the recorded hash describe
    bytes that were never stored. Model output contains newlines, so the first real
    LLM-backed run with an integrity check would have failed for a reason that looked like
    the integrity check being wrong.
    """
    store = LightweightStore(root=tmp_path)
    pointer = store.put(payload)

    blob = tmp_path / f"{pointer['yp:pid'].rsplit('-', 1)[-1]}.txt"
    on_disk = "sha256:" + hashlib.sha256(blob.read_bytes()).hexdigest()

    assert on_disk == pointer["yp:contentHash"]
    assert store.resolve(pointer["yp:pid"], verify=True) == payload


def test_a_cold_process_can_resolve_a_previously_stored_pid(tmp_path: Path) -> None:
    """The situation every later reader is in: the writing process is long gone."""
    writer = LightweightStore(root=tmp_path)
    pointer = writer.put(PAYLOAD)
    writer.save()

    reader = LightweightStore(root=tmp_path)
    assert reader.resolve(pointer["yp:pid"]) == PAYLOAD


def test_concurrent_writers_do_not_clobber_each_others_manifest(tmp_path: Path) -> None:
    """Four agents share one store directory, exactly as CA1/CA2/CA3/CAx do."""
    pids = []
    for index in range(4):
        agent_store = LightweightStore(root=tmp_path)
        pids.append(agent_store.put(f"payload from agent {index}")["yp:pid"])
        agent_store.save()

    final = LightweightStore(root=tmp_path)
    final_manifest = final.manifest or {}
    missing = [pid for pid in pids if pid not in final_manifest]
    assert not missing, f"{len(missing)} of {len(pids)} writes lost from the manifest"


# ──────────────────────────────────────────────────────────────────────────────────
# Artifacts from a real run
# ──────────────────────────────────────────────────────────────────────────────────
def test_every_pid_cited_in_provenance_is_in_the_store_manifest(
    prov_documents: list[tuple[Path, dict[str, Any]]],
    store_manifest: dict[str, Any],
) -> None:
    cited = cited_pids(prov_documents)
    dangling = {pid: files for pid, files in cited.items() if pid not in store_manifest}

    assert not dangling, (
        f"{len(dangling)} of {len(cited)} cited PIDs are unresolvable:\n  "
        + "\n  ".join(
            f"{pid} cited by {sorted(files)}" for pid, files in sorted(dangling.items())
        )
    )


def test_stored_blobs_match_their_recorded_hashes(
    prov_dir: Path, store_manifest: dict[str, Any]
) -> None:
    """Whatever the manifest does record must at least be internally consistent."""
    store_dir = prov_dir / "store"
    mismatched: list[str] = []
    for pid, entry in store_manifest.items():
        blob = store_dir / f"{pid.rsplit('-', 1)[-1]}.txt"
        if not blob.is_file():
            mismatched.append(f"{pid}: blob missing at {blob.name}")
            continue
        actual = "sha256:" + hashlib.sha256(blob.read_bytes()).hexdigest()
        if actual != entry["yp:contentHash"]:
            mismatched.append(f"{pid}: hash mismatch")

    assert not mismatched, "\n  ".join(mismatched)
