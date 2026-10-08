"""
util.py — small shared helpers for the runtime agents.

Keeps the agents focused on their logic: where to make the yProv4AgenticAI core
importable, timestamps, writing emitted PROV-JSON to the shared output volume, and
an optional LLM handle (used only by CAx, only when a key is present).
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# Make the canonical core importable whether we run from the repo (dev) or the
# image (where /app is the repo root on PYTHONPATH).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

OUT_DIR = Path(os.environ.get("OUT_DIR", "/out"))


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_prov(role: str, session_id: str, doc: Dict[str, Any]) -> Optional[Path]:
    """Write an emitted PROV-JSON doc to the shared output volume, best-effort."""
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        # sanitise: session ids can contain ':' (e.g. "example_0::cax"), which is a
        # reserved char on Windows hosts where /out is bind-mounted. Keep it portable.
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)
        path = OUT_DIR / f"{safe}__{role}_prov.json"
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
    except Exception as exc:                                          # noqa: BLE001
        print(f"[{role}] could not write output: {exc}", flush=True)
        return None


def get_llm() -> Optional[Callable[[str], str]]:
    """Return an llm(prompt)->text callable if OPENAI_API_KEY is set, else None.

    CAx uses this; every other agent is deterministic and needs no key, so the whole
    pipeline runs end-to-end with no LLM at all (CAx falls back to a stub summary).
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        model = os.environ.get("OPENAI_MODEL", "gpt-4o")

        def _llm(prompt: str) -> str:
            resp = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=400,
            )
            return (resp.choices[0].message.content or "").strip()

        return _llm
    except Exception as exc:                                          # noqa: BLE001
        print(f"[cax] LLM unavailable ({exc}); using stub summariser", flush=True)
        return None


def prov_counts(doc: Dict[str, Any]) -> Dict[str, int]:
    """Count activities, entities, and lineage relations in a PROV-JSON doc."""
    rel_keys = ("used", "wasGeneratedBy", "wasInformedBy",
                "wasDerivedFrom", "wasAssociatedWith", "actedOnBehalfOf")
    return {
        "activities": len(doc.get("activity", {}) or {}),
        "entities": len(doc.get("entity", {}) or {}),
        "relations": sum(len(doc.get(k, {}) or {}) for k in rel_keys),
    }
