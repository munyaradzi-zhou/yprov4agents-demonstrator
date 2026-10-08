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


@pytest.mark.known_defect
@pytest.mark.xfail(
    strict=True,
    reason="todo.md C2: resolve() returns the in-process copy held by put(), so the disk "
    "file is never read and the hash is recomputed over the same object. The "
    "round-trip CA1 cites as evidence for ACCEPT does not happen. Remove this "
    "marker once resolve() reads through to the store root.",
)
def test_resolve_detects_a_corrupted_blob_on_disk(tmp_path: Path) -> None:
    """Corrupt the stored file; a real integrity check must notice."""
    store = LightweightStore(root=tmp_path)
    pointer = store.put(PAYLOAD)
    store.save()

    blob = next(path for path in tmp_path.glob("*.txt"))
    blob.write_text("corrupted on disk", encoding="utf-8")

    with pytest.raises(ValueError, match="content-hash mismatch"):
        store.resolve(pointer["yp:pid"], verify=True)


@pytest.mark.known_defect
@pytest.mark.xfail(
    strict=True,
    reason="todo.md D2: LightweightStore has no load(), so a fresh process cannot read the "
    "manifest and cannot dereference any PID. Remove this marker once the store "
    "loads its manifest on construction.",
)
def test_a_cold_process_can_resolve_a_previously_stored_pid(tmp_path: Path) -> None:
    """The situation every later reader is in: the writing process is long gone."""
    writer = LightweightStore(root=tmp_path)
    pointer = writer.put(PAYLOAD)
    writer.save()

    reader = LightweightStore(root=tmp_path)
    assert reader.resolve(pointer["yp:pid"]) == PAYLOAD


@pytest.mark.known_defect
@pytest.mark.xfail(
    strict=True,
    reason="todo.md D1: each agent keeps a private in-memory manifest and save() writes "
    "only its own entries, so the shared manifest is whatever the last writer held. "
    "Remove this marker once save() merges.",
)
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
@pytest.mark.known_defect
@pytest.mark.xfail(
    strict=True,
    reason="todo.md D1: measured at baseline, the emitted graphs cite 8 PIDs of which 2 "
    "appear in the manifest. Remove this marker when every cited PID resolves.",
)
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
    import hashlib

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
