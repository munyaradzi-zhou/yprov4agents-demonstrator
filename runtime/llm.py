"""The model call, and the provenance of having made it.

An agentic-AI provenance record that cannot say which model produced a piece of text, under
which prompt, at which temperature, is not an auditable record of an agent — it is an
auditable record of everything except the part that was non-deterministic. That was review
finding F1: in LLM mode the emitted graph named no model, no sampling parameters, no prompt
and no token counts, so the one step that could not be reproduced from the record was the
one step that most needed to be.

So a completion is not a string here. :class:`Completion` carries the text together with
everything needed to account for it, and :meth:`Completion.provenance` renders that as
``yp:`` attributes for the activity that made the call. Both LLM-bearing agents use the same
client, so the two records are comparable rather than each agent's own idea of what matters.

The prompt comes from :mod:`yprov4agenticai.prompts`, which existed for exactly this and
which nothing imported (finding F2). A prompt is part of the experimental apparatus: change
its wording and the output distribution changes, so the record cites the prompt's id,
version and content hash. A reviewer can then diff two versions and see why two runs differ.

This module lives in the runtime, not the library: the library stays free of any dependency
on a model vendor. What crosses the boundary is the attribute dict.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NamedTuple

from yprov4agenticai.prompts import Prompt

#: Model and sampling parameters. Named here rather than inlined at the call site because
#: they are part of the experimental apparatus — change them and the output distribution
#: changes — and because a run's provenance has to be able to cite them.
DEFAULT_OPENAI_MODEL = "gpt-4o"
LLM_TEMPERATURE = 0.2
LLM_MAX_TOKENS = 400

#: Characters of the response digest recorded on the activity. The full text is stored as an
#: entity with its own content hash; this ties the *activity* to the exact bytes the model
#: returned, independently of anything done to them afterwards.
_SHA_CHARS = 12


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_SHA_CHARS]


@dataclass(frozen=True, slots=True)
class Completion:
    """One model response, with everything needed to account for it."""

    text: str
    model: str
    temperature: float
    max_tokens: int
    latency_s: float
    prompt: Prompt
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    model_resolved: str | None = None
    system_fingerprint: str | None = None

    @property
    def truncated(self) -> bool:
        """Whether the model stopped because it ran out of budget rather than of things to say.

        Worth recording, and worth an agent checking: a summary cut off mid-sentence is a
        different artifact from a summary that finished, and the two are indistinguishable
        from the text alone once the text is long enough.
        """
        return self.finish_reason == "length"

    def provenance(self) -> dict[str, Any]:
        """``yp:`` attributes for the activity that made this call.

        Sampling parameters and the prompt's identity are always present: without them the
        call cannot be repeated. Token counts and the finish reason are present when the
        API reported them, and absent rather than zero when it did not — a zero token count
        is a measurement, and we did not make it.
        """
        attributes: dict[str, Any] = {
            "yp:model": self.model,
            "yp:temperature": self.temperature,
            "yp:max_tokens": self.max_tokens,
            "yp:llm_latency_s": round(self.latency_s, 3),
            "yp:response_sha": _sha(self.text),
            **self.prompt.provenance(),
        }
        if self.prompt_tokens is not None:
            attributes["yp:prompt_tokens"] = self.prompt_tokens
        if self.completion_tokens is not None:
            attributes["yp:completion_tokens"] = self.completion_tokens
        if self.finish_reason is not None:
            attributes["yp:finish_reason"] = self.finish_reason
        # ``yp:model`` is what was asked for, and a provider re-points an alias such as
        # "gpt-4o" at new snapshots over time. Repeating a call needs the snapshot that
        # actually answered, and the backend configuration it ran on.
        if self.model_resolved is not None:
            attributes["yp:model_resolved"] = self.model_resolved
        if self.system_fingerprint is not None:
            attributes["yp:system_fingerprint"] = self.system_fingerprint
        return attributes


class RawResponse(NamedTuple):
    """What the vendor call reports back. Fields the API did not report stay ``None``."""

    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    #: The model snapshot that answered, which may differ from the alias requested.
    model: str | None = None
    #: The provider's identifier for the backend configuration that served the call.
    system_fingerprint: str | None = None


#: The vendor call, isolated behind one signature: (model, temperature, max_tokens,
#: system, user) -> RawResponse, or a plain tuple of its leading fields. Injecting it is
#: what lets a test exercise the provenance without a network or an API key.
RawCall = Callable[[str, float, int, str, str], tuple[Any, ...]]


class LLMClient:
    """Makes model calls and reports what it did.

    Parameters
    ----------
    raw
        The vendor call (see :data:`RawCall`).
    model, temperature, max_tokens
        Recorded on every completion, so the record states the configuration that was in
        force rather than the configuration the reader assumes.
    """

    def __init__(
        self,
        raw: RawCall,
        model: str = DEFAULT_OPENAI_MODEL,
        temperature: float = LLM_TEMPERATURE,
        max_tokens: int = LLM_MAX_TOKENS,
    ) -> None:
        self._raw = raw
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def complete(self, prompt: Prompt, user: str) -> Completion:
        """Send a registered prompt plus a user message, and return an accountable result."""
        started = time.perf_counter()
        response = RawResponse(
            *self._raw(self.model, self.temperature, self.max_tokens, prompt.text, user)
        )
        return Completion(
            text=response.text.strip(),
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            latency_s=time.perf_counter() - started,
            prompt=prompt,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            finish_reason=response.finish_reason,
            model_resolved=response.model,
            system_fingerprint=response.system_fingerprint,
        )


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


def get_llm() -> LLMClient | None:
    """Return a configured :class:`LLMClient`, or None when no model is available.

    Returning None is the supported no-key path: every agent has a deterministic fallback,
    so the pipeline runs end to end without an API key. When ``LLM_REQUIRED`` is set, both
    a missing key and an unusable client raise instead, so a run cannot be archived under a
    label it did not earn.

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

    def raw(
        model: str, temperature: float, max_tokens: int, system: str, user: str
    ) -> RawResponse:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choice = response.choices[0]
        usage = response.usage
        return RawResponse(
            text=choice.message.content or "",
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            finish_reason=choice.finish_reason,
            model=getattr(response, "model", None),
            system_fingerprint=getattr(response, "system_fingerprint", None),
        )

    return LLMClient(raw, model=model)
