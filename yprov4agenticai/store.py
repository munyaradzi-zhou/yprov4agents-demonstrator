"""
store.py — LightweightStore: content-addressed payload store (lightweight-prov)
===============================================================================
The data-plane half of the pointers-not-payloads design. When an
agent produces a heavy artifact (a full tool output, a memory transcript, a final
document), it PUTs the payload here; the store computes a SHA-256, mints a
persistent identifier (PID), and returns the pointer. The PROV graph then carries
only `yp:pid` + `yp:contentHash` (via ProvTracker.register_entity / register_memory),
never the payload — so the graph grows with the number of steps, not the size of
the data.

This is an in-process / filesystem reference implementation with the same interface
a production yProvStore + MinIO backend would expose (put -> pointer; resolve ->
bytes). Dependency-free (stdlib only), so it does not change the core library's
zero-dependency guarantee. Opt-in: nothing imports it unless an agent chooses to.

    store = LightweightStore()                       # in-memory manifest
    ptr = store.put("…full transcript…", kind="memory")
    #   ptr == {"yp:pid": "21.T11999/yprovstore-<hash12>", "yp:contentHash": "sha256:<hash>"}
    tracker.register_memory("turn 2 context", turn=2, **ptr_kwargs(ptr))
    payload = store.resolve(ptr["yp:pid"])           # fetch + verify by hash
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Handle-style PID prefix used across the yProv ecosystem (yProvStore).
PID_PREFIX = "21.T11999/yprovstore-"


class LightweightStore:
    """Content-addressed store: put(payload) -> pointer; resolve(pid) -> payload.

    Parameters
    ----------
    root : optional directory. If given, payloads are also written to
           ``root/<hash12>.txt`` and the manifest to ``root/_manifest.json`` on
           ``save()``; if omitted, the store is purely in-memory.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else None
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)
        self.manifest: dict = {}  # pid -> {uri, yp:contentHash, size_bytes, kind, preview}
        self._blobs: dict = {}  # pid -> payload (in-memory copy for resolve)

    def put(self, payload: str, kind: str = "artifact") -> dict:
        """Store a payload; return {"yp:pid": …, "yp:contentHash": …}."""
        raw = payload.encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        pid = f"{PID_PREFIX}{sha[:12]}"
        uri = (
            f"file://{(self.root / (sha[:12] + '.txt')).as_posix()}"
            if self.root
            else f"mem://{kind}/{sha[:12]}"
        )
        self.manifest[pid] = {
            "uri": uri,
            "yp:contentHash": f"sha256:{sha}",
            "size_bytes": len(raw),
            "kind": kind,
            "preview": (payload[:140] + "…") if len(payload) > 140 else payload,
        }
        self._blobs[pid] = payload
        if self.root:
            (self.root / f"{sha[:12]}.txt").write_text(payload, encoding="utf-8")
        return {"yp:pid": pid, "yp:contentHash": f"sha256:{sha}"}

    def resolve(self, pid: str, verify: bool = True) -> str:
        """Fetch the payload for a PID; optionally verify its SHA-256 first."""
        if pid not in self._blobs and self.root:
            hash12 = pid.replace(PID_PREFIX, "")
            self._blobs[pid] = (self.root / f"{hash12}.txt").read_text(encoding="utf-8")
        payload = self._blobs[pid]
        if verify:
            got = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
            want = self.manifest[pid]["yp:contentHash"]
            if got != want:
                raise ValueError(f"content-hash mismatch for {pid}: {got} != {want}")
        return payload

    def save(self, path: str | Path | None = None) -> Path:
        """Write the manifest to JSON (defaults to root/_manifest.json)."""
        out = (
            Path(path)
            if path
            else (
                self.root / "_manifest.json" if self.root else Path("datastore_manifest.json")
            )
        )
        out.write_text(
            json.dumps(self.manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return out


def ptr_kwargs(pointer: dict) -> dict:
    """Adapt a store pointer to ProvTracker.register_* keyword arguments."""
    return {"pid": pointer["yp:pid"], "content_hash": pointer["yp:contentHash"]}
