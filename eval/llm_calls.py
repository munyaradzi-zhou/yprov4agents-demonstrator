"""Model calls for the evaluation studies: pinned, cached, and accountable.

The two studies that use a model as an instrument (``eval.reconstruct`` and
``eval.diagnose``) make hundreds of calls, and a result built on them has to survive being
re-rendered, re-scored and audited without paying for the calls again or getting different
answers. So every call goes through :func:`ask`, which

* pins a dated model snapshot and sends a fixed seed, temperature and token budget;
* asks for a JSON object, so an answer is parsed rather than read;
* writes the request and the full response, with the model that answered, the provider's
  fingerprint and the token counts, to a cache file named by a hash of the request;
* answers from that cache when the same request is made again.

The cache is the evidence: it is archived with the study, and every number the study
reports can be recomputed from it with no key and no network. ``--offline`` makes a run
fail on any request the cache cannot answer instead of calling the provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

#: Dated snapshots, not aliases: an alias moves under a caller that changed nothing.
OPERATOR_MODELS = ("gpt-4o-2024-08-06", "gpt-4o-mini-2024-07-18")
DEFAULT_MODEL = OPERATOR_MODELS[0]
SEED = 20261017


class OfflineMiss(RuntimeError):
    """A request the cache cannot answer, made while calls to the provider are forbidden."""


@dataclass(frozen=True)
class Answer:
    """One model response, parsed, with what is needed to account for it."""

    data: dict[str, Any]
    text: str
    model_requested: str
    model_resolved: str | None
    system_fingerprint: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_s: float | None
    cached: bool
    key: str


def _api_key() -> str:
    """The key from the environment, or from the project's ``.env``; never printed."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    env = Path(".env")
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "OPENAI_API_KEY" and value.strip():
                return value.strip().strip("'\"")
    raise RuntimeError("OPENAI_API_KEY is not set and .env has none")


_client: Any = None


def _call(request: dict[str, Any]) -> tuple[dict[str, Any], float]:
    global _client
    if _client is None:
        from openai import OpenAI

        from runtime.llm import enable_system_trust_store

        enable_system_trust_store()
        _client = OpenAI(api_key=_api_key())
    started = time.perf_counter()
    attempts = 8
    for attempt in range(attempts):
        try:
            response = _client.chat.completions.create(**request)
            break
        except Exception as exc:  # rate limits and transient faults: back off and retry
            # An exhausted balance is not transient: stop rather than retry into it.
            if "insufficient_quota" in str(exc) or attempt == attempts - 1:
                raise
            wait = min(2.0 * 2**attempt, 90.0)
            print(f"[llm] {type(exc).__name__}; retrying in {wait:.0f}s", flush=True)
            time.sleep(wait)
    return response.model_dump(), time.perf_counter() - started


def ask(
    system: str,
    user: str,
    *,
    cache: Path,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.0,
    max_tokens: int = 500,
    seed: int = SEED,
    offline: bool = False,
) -> Answer:
    """Send one request, or answer it from the cache. Returns the parsed JSON object."""
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "seed": seed,
        "response_format": {"type": "json_object"},
    }
    key = hashlib.sha256(json.dumps(request, sort_keys=True).encode("utf-8")).hexdigest()
    path = cache / f"{key[:2]}/{key}.json"
    if path.is_file():
        stored = json.loads(path.read_text(encoding="utf-8"))
        response, latency, cached = stored["response"], stored.get("latency_s"), True
    else:
        if offline:
            raise OfflineMiss(f"no cached answer for request {key[:12]}")
        response, latency = _call(request)
        cached = False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"request": request, "response": response, "latency_s": latency}, indent=1
            ),
            encoding="utf-8",
        )
    choice = response["choices"][0]
    text = choice["message"]["content"] or ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {}
    usage = response.get("usage") or {}
    return Answer(
        data=data if isinstance(data, dict) else {},
        text=text,
        model_requested=model,
        model_resolved=response.get("model"),
        system_fingerprint=response.get("system_fingerprint"),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        latency_s=latency,
        cached=cached,
        key=key,
    )


def accounting(answers: list[Answer]) -> dict[str, Any]:
    """What a set of calls consumed, and which backends answered."""
    return {
        "calls": len(answers),
        "from_cache": sum(a.cached for a in answers),
        "prompt_tokens": sum(a.prompt_tokens or 0 for a in answers),
        "completion_tokens": sum(a.completion_tokens or 0 for a in answers),
        "models_resolved": sorted({a.model_resolved or "?" for a in answers}),
        "fingerprints": len({a.system_fingerprint for a in answers if a.system_fingerprint}),
    }


def as_record(answer: Answer) -> dict[str, Any]:
    return {k: v for k, v in asdict(answer).items() if k != "text"}
