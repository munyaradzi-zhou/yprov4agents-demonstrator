"""Content-addressed payload store: the data plane of pointers-not-payloads.

A provenance graph should grow with the number of steps an agent takes, not with the size
of the data it handles. So when an agent produces a heavy artifact — a full tool output, a
memory transcript, a rendered document — it PUTs the payload here and the graph carries
only ``yp:pid`` (where it is) and ``yp:contentHash`` (what it was).

That trade is only sound if the pointer resolves. A PID nobody can follow is worse than an
inline value: it reads as evidence while supplying none. Three properties make it hold:

*Byte-exact.* The bytes hashed by :meth:`put` are the bytes written, and the bytes read by
:meth:`resolve` are re-hashed before being returned. All I/O is binary. Text-mode writes
translate newlines on Windows, which would make the same logical payload hash differently
on different platforms and silently invalidate every integrity claim in the graph.

*Resolvable from a cold start.* Every payload gets a sidecar record next to it, and a store
opened on an existing directory reads them. The process that wrote a payload is usually long
gone by the time anyone wants to check it.

*Safe for concurrent writers.* Several agents share one store directory. Each write touches
only files named after its own content hash, so there is no shared file to serialise on and
no lock to acquire. ``_manifest.json`` is written as a convenience index, derived from the
sidecars rather than authoritative.

    store = LightweightStore(root="out/store")
    pointer = store.put("…full transcript…", kind="memory")
    #   {"yp:pid": "21.T11999/yprovstore-<hash12>", "yp:contentHash": "sha256:<hash>"}
    tracker.register_memory("turn 2 context", turn=2, **ptr_kwargs(pointer))
    payload = store.resolve(pointer["yp:pid"])      # reads from disk, verifies the hash
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

# Handle-style prefix used across the yProv ecosystem (yProvStore).
#
# These are MOCK handles. The prefix is not registered to this project and nothing
# resolves them globally; they are locally meaningful identifiers shaped like the real
# thing so the demonstrator exercises the same code paths a production yProvStore would.
# Any published description of this store has to say so.
PID_PREFIX = "21.T11999/yprovstore-"

#: Characters of the hex digest used in a PID and in on-disk filenames. 48 bits of hash:
#: ample for a demonstrator, and the full digest is always carried alongside in
#: ``yp:contentHash``, so truncation affects addressing, never verification.
_HASH_PREFIX_LEN = 12

_PAYLOAD_SUFFIX = ".txt"
_SIDECAR_SUFFIX = ".meta.json"
_MANIFEST_NAME = "_manifest.json"
_PREVIEW_CHARS = 140


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class LightweightStore:
    """Content-addressed store: ``put(payload) -> pointer``; ``resolve(pid) -> payload``.

    Parameters
    ----------
    root
        Directory holding payloads and their sidecar records. When omitted the store is
        purely in-memory, which is useful in tests but cannot outlive the process.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        # Resolved, not stored as given. A payload's record carries a `file:` URI, and
        # `Path.as_uri()` raises on a relative path — so a store opened on "out/store"
        # used to work until the first `put`, then crash with "relative path can't be
        # expressed as a file URI". Resolving here also makes the recorded URI independent
        # of the working directory a later reader happens to have.
        self.root = Path(root).resolve() if root else None
        #: pid -> record {uri, yp:contentHash, size_bytes, kind, preview, created_at}
        self.manifest: dict[str, dict] = {}
        #: pid -> payload. Only used when there is no root; a rooted store reads through
        #: to disk so that an integrity check is a real round trip rather than a
        #: comparison of an object with itself.
        self._blobs: dict[str, str] = {}
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)
            self.load()

    # ── addressing ────────────────────────────────────────────────────────────────
    @staticmethod
    def pid_for(payload_hash_hex: str) -> str:
        return f"{PID_PREFIX}{payload_hash_hex[:_HASH_PREFIX_LEN]}"

    @staticmethod
    def _stem(pid: str) -> str:
        """Filename stem for a PID. Content-addressed, so the PID names its own file."""
        return pid.removeprefix(PID_PREFIX)

    def _payload_path(self, pid: str) -> Path | None:
        return None if self.root is None else self.root / f"{self._stem(pid)}{_PAYLOAD_SUFFIX}"

    def _sidecar_path(self, pid: str) -> Path | None:
        return None if self.root is None else self.root / f"{self._stem(pid)}{_SIDECAR_SUFFIX}"

    # ── write ─────────────────────────────────────────────────────────────────────
    def put(self, payload: str, kind: str = "artifact") -> dict:
        """Store a payload and return its pointer.

        The returned ``yp:contentHash`` is over the exact bytes written, so a later
        :meth:`resolve` of those bytes reproduces it on any platform.
        """
        raw = payload.encode("utf-8")
        digest = _sha256_hex(raw)
        pid = self.pid_for(digest)
        payload_path = self._payload_path(pid)

        record = {
            "uri": payload_path.as_uri() if payload_path else f"mem://{kind}/{digest[:12]}",
            "yp:contentHash": f"sha256:{digest}",
            "size_bytes": len(raw),
            "kind": kind,
            "preview": (payload[:_PREVIEW_CHARS] + "…")
            if len(payload) > _PREVIEW_CHARS
            else payload,
            "created_at": dt.datetime.now(dt.UTC).isoformat(),
        }
        self.manifest[pid] = record

        if payload_path is not None:
            # Binary write: these are the bytes that were hashed.
            payload_path.write_bytes(raw)
            sidecar = self._sidecar_path(pid)
            assert sidecar is not None
            sidecar.write_text(
                json.dumps({"yp:pid": pid, **record}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        else:
            self._blobs[pid] = payload

        return {"yp:pid": pid, "yp:contentHash": f"sha256:{digest}"}

    # ── read ──────────────────────────────────────────────────────────────────────
    def resolve(self, pid: str, verify: bool = True) -> str:
        """Fetch a payload by PID, verifying its hash by default.

        A rooted store reads the file. That is the point: the verification is meant to
        establish that what is on disk is what was promised, which a cached copy of the
        string just written cannot demonstrate.

        Raises
        ------
        KeyError
            The PID is not in this store.
        FileNotFoundError
            The PID is known but its payload is missing from disk.
        ValueError
            The stored bytes do not hash to the recorded content hash.
        """
        if self.root is None:
            if pid not in self._blobs:
                raise KeyError(f"unknown PID in this in-memory store: {pid}")
            payload = self._blobs[pid]
            raw = payload.encode("utf-8")
        else:
            payload_path = self._payload_path(pid)
            assert payload_path is not None
            if not payload_path.is_file():
                raise FileNotFoundError(f"payload for {pid} is missing at {payload_path}")
            raw = payload_path.read_bytes()
            payload = raw.decode("utf-8")

        if verify:
            expected = self.manifest.get(pid, {}).get("yp:contentHash")
            if expected is None:
                raise KeyError(f"no content hash recorded for {pid}; cannot verify")
            actual = f"sha256:{_sha256_hex(raw)}"
            if actual != expected:
                raise ValueError(f"content-hash mismatch for {pid}: {actual} != {expected}")
        return payload

    # ── manifest ──────────────────────────────────────────────────────────────────
    def load(self) -> int:
        """Read sidecar records from the store directory. Returns how many were found.

        Called on construction, which is what lets a fresh process dereference a PID
        written by one that has exited.
        """
        if self.root is None or not self.root.is_dir():
            return 0
        found = 0
        for sidecar in sorted(self.root.glob(f"*{_SIDECAR_SUFFIX}")):
            try:
                record = json.loads(sidecar.read_text(encoding="utf-8"))
                pid = record.pop("yp:pid")
            except (OSError, ValueError, KeyError):
                # A damaged sidecar must not prevent every other payload from resolving.
                continue
            self.manifest.setdefault(pid, record)
            found += 1
        return found

    def save(self, path: str | Path | None = None) -> Path:
        """Write the manifest index, merging whatever other writers have recorded.

        The sidecars are authoritative, so this rebuilds the index from them before
        writing. Several agents share a store directory, and an earlier version of this
        method wrote only the calling agent's own entries — so the index on disk held
        whichever agent happened to save last, and most PIDs cited in the emitted
        provenance could not be looked up.
        """
        self.load()
        out = (
            Path(path)
            if path
            else (self.root / _MANIFEST_NAME if self.root else Path("datastore_manifest.json"))
        )
        out.write_text(
            json.dumps(dict(sorted(self.manifest.items())), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        return out


def ptr_kwargs(pointer: dict) -> dict:
    """Adapt a store pointer to :meth:`ProvTracker.register_entity` keyword arguments."""
    return {"pid": pointer["yp:pid"], "content_hash": pointer["yp:contentHash"]}
