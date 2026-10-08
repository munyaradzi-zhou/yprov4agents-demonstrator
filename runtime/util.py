"""
util.py — small shared helpers for the runtime agents.

Keeps the agents focused on their logic: where to make the yProv4AgenticAI core
importable, timestamps, writing emitted PROV-JSON to the shared output volume, and
an optional LLM handle (used only by CAx, only when a key is present).
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Make the canonical core importable whether we run from the repo (dev) or the
# image (where /app is the repo root on PYTHONPATH).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# One clock for the whole system: the library defines the timestamp format it writes into
# prov:startTime, and CA3 reads those values back as a timeline. A second definition here
# would be a second opinion about when things happened. Imported after the path setup
# above, which is what makes the package importable from a source checkout.
from yprov4agenticai import now_iso as now_iso  # noqa: E402

OUT_DIR = Path(os.environ.get("OUT_DIR", "/out"))

# Model and sampling parameters for every LLM-bearing agent. Named here rather than
# inlined at the call site because they are part of the experimental apparatus: change
# them and the output distribution changes, so a run's provenance has to be able to cite
# them (see todo.md F1 on recording model provenance in the emitted graph).
DEFAULT_OPENAI_MODEL = "gpt-4o"
LLM_TEMPERATURE = 0.2
LLM_MAX_TOKENS = 400


def write_prov(role: str, session_id: str, doc: dict[str, Any]) -> Path | None:
    """Write an emitted PROV-JSON doc to the shared output volume, best-effort."""
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        # sanitise: session ids can contain ':' (e.g. "example_0::cax"), which is a
        # reserved char on Windows hosts where /out is bind-mounted. Keep it portable.
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)
        path = OUT_DIR / f"{safe}__{role}_prov.json"
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
    except Exception as exc:
        print(f"[{role}] could not write output: {exc}", flush=True)
        return None


def llm_is_required() -> bool:
    """Whether a missing or failing model must abort the run instead of falling back.

    Deterministic fallbacks exist so the demonstrator runs without a key, which is right
    for a demo and wrong for a measurement: a run whose numbers are reported as LLM-backed
    must not quietly produce stub output instead. Set ``LLM_REQUIRED=1`` whenever a run
    will be archived or published.
    """
    return os.environ.get("LLM_REQUIRED", "").strip().lower() in {"1", "true", "yes"}


def enable_system_trust_store() -> None:
    """Verify TLS against the operating system's certificate store.

    Networks that terminate TLS at a proxy present a locally-trusted root CA which is not
    in certifi's bundle, so the API client fails to connect and every agent silently falls
    back to deterministic output. Routing verification through the OS store is what the
    corresponding ``native-tls`` setting does for the package installer.

    Best-effort and idempotent: on a network that needs no interception this changes
    nothing, and a missing ``truststore`` is reported rather than raised.
    """
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        print(
            "[llm] truststore is not installed; TLS will use the bundled CA roots. "
            "Install it if API calls fail with a connection error behind a proxy.",
            flush=True,
        )


def get_llm() -> Callable[[str], str] | None:
    """Return an ``llm(prompt) -> text`` callable, or None when no model is configured.

    Returning None is the supported no-key path: every agent has a deterministic
    fallback, so the pipeline runs end to end without an API key. When ``LLM_REQUIRED``
    is set, both a missing key and an unusable client raise instead, so a run cannot be
    archived under a label it did not earn.

    Raises
    ------
    RuntimeError
        ``LLM_REQUIRED`` is set and no usable model could be configured.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        if llm_is_required():
            raise RuntimeError(
                "LLM_REQUIRED is set but OPENAI_API_KEY is empty: refusing to produce "
                "deterministic output that would be reported as model output."
            )
        return None

    enable_system_trust_store()
    model = os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
    except Exception as exc:
        if llm_is_required():
            raise RuntimeError(
                f"LLM_REQUIRED is set but the client is unusable: {exc}"
            ) from exc
        print(
            f"[llm] model unavailable ({exc}); agents will use deterministic output", flush=True
        )
        return None

    def call(prompt: str) -> str:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
        )
        return (response.choices[0].message.content or "").strip()

    return call


def prov_counts(doc: dict[str, Any]) -> dict[str, int]:
    """Count activities, entities, and lineage relations in a PROV-JSON doc."""
    rel_keys = (
        "used",
        "wasGeneratedBy",
        "wasInformedBy",
        "wasDerivedFrom",
        "wasAssociatedWith",
        "actedOnBehalfOf",
    )
    return {
        "activities": len(doc.get("activity", {}) or {}),
        "entities": len(doc.get("entity", {}) or {}),
        "relations": sum(len(doc.get(k, {}) or {}) for k in rel_keys),
    }
