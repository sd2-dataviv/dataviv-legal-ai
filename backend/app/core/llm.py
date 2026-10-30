"""Model gateway (FND-06, ADR-012). Every model call in the codebase goes through `Gateway`.

Callers name a *task class*; the firm's model policy picks the model ("Auto"), or the caller asks
for a specific model the policy allows. Backends:

- `FakeLLM`: offline and deterministic, for tests and for dev without a key.
- `GroqBackend`: Groq's OpenAI-compatible chat completions over HTTPS, streamed with SSE.
  Rate limits (HTTP 429) are honoured using `retry-after`, up to `llm_max_wait_seconds`.

The gateway never logs prompts or keys; it returns token usage, latency and cost so callers can
store them on the answer and in the audit trail.
"""

import asyncio
import json
import math
import re
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from app.core.config import get_settings

TaskClass = Literal["extract", "qa", "draft", "reason"]
TASKS: tuple[TaskClass, ...] = ("extract", "qa", "draft", "reason")


@dataclass(slots=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class TextDelta:
    text: str


@dataclass(slots=True)
class Notice:
    """Something the user should know while waiting, e.g. a rate-limit pause."""

    text: str


@dataclass(slots=True)
class Done:
    model: str
    usage: Usage
    latency_ms: int
    cost_usd: float


StreamItem = TextDelta | Notice | Done


@dataclass(slots=True)
class Completion:
    text: str
    model: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    cost_usd: float = 0.0


class LLMError(Exception):
    """A model call failed. `str(err)` is safe to show to users (never contains keys or prompts)."""

    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class ModelNotAllowed(LLMError):
    pass


class Backend(Protocol):
    name: str

    def models(self) -> list[str]: ...

    async def complete(
        self, model: str, messages: list[Message], *, max_tokens: int, json_schema: dict[str, Any] | None
    ) -> tuple[str, Usage]: ...

    def stream(
        self, model: str, messages: list[Message], *, max_tokens: int
    ) -> AsyncIterator[TextDelta | Notice | Usage]: ...


# --------------------------------------------------------------------------- fake backend

_SOURCE_RE = re.compile(r'<source id="(S\d+)"[^>]*>(.*?)</source>', re.S)


def _first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    m = re.search(r"^(.{20,240}?[.;])(\s|$)", flat)
    return (m.group(1) if m else flat[:200]).rstrip(".;") + "."


FakeStructured = Callable[[list[Message]], dict[str, Any]]
_FAKE_STRUCTURED: dict[str, FakeStructured] = {}


def register_fake_structured(schema_title: str, handler: FakeStructured) -> None:
    """Let a feature teach the offline model how to answer its JSON schema (tests and keyless dev)."""
    _FAKE_STRUCTURED[schema_title] = handler


class FakeLLM:
    """Deterministic, offline. Answers by quoting the first sentence of up to two sources, with
    citations, so retrieval, citation parsing and verification can be tested end to end."""

    name = "fake"

    def models(self) -> list[str]:
        return ["fake-1"]

    def _answer(self, messages: list[Message]) -> str:
        last = next((m.content for m in reversed(messages) if m.role == "user"), "")
        sources = _SOURCE_RE.findall(last)
        if not sources:
            return "The documents provided do not cover this."
        return " ".join(f"{_first_sentence(text)} [{key}]" for key, text in sources[:2])

    async def complete(
        self, model: str, messages: list[Message], *, max_tokens: int, json_schema: dict[str, Any] | None
    ) -> tuple[str, Usage]:
        if json_schema is not None and json_schema.get("title") in _FAKE_STRUCTURED:
            payload = _FAKE_STRUCTURED[json_schema["title"]](messages)
            return json.dumps(payload), Usage(sum(len(m.content.split()) for m in messages), 40)
        if json_schema is not None:  # used by the model verifier: everything checks out
            n = len(re.findall(r"^\d+\. ", messages[-1].content, re.M))
            payload = {"sentences": [{"index": i, "label": "supported", "reason": ""} for i in range(n)]}
            return json.dumps(payload), Usage(10, 10)
        text = self._answer(messages)
        return text, Usage(sum(len(m.content.split()) for m in messages), len(text.split()))

    async def stream(
        self, model: str, messages: list[Message], *, max_tokens: int
    ) -> AsyncIterator[TextDelta | Notice | Usage]:
        text = self._answer(messages)
        for word in text.split(" "):
            yield TextDelta(word + " ")
        yield Usage(sum(len(m.content.split()) for m in messages), len(text.split()))


# --------------------------------------------------------------------------- Groq backend


def parse_sse_data(data: str) -> list[TextDelta | Usage]:
    """One `data:` payload of an OpenAI-compatible stream → text deltas and/or final usage."""
    data = data.strip()
    if not data or data == "[DONE]":
        return []
    obj = json.loads(data)
    if "error" in obj:
        err = obj["error"]
        raise LLMError(f"Model stream error: {err.get('message', err) if isinstance(err, dict) else err}")
    out: list[TextDelta | Usage] = []
    for choice in obj.get("choices") or []:
        content = (choice.get("delta") or {}).get("content")
        if content:
            out.append(TextDelta(content))
    usage = obj.get("usage") or (obj.get("x_groq") or {}).get("usage")
    if usage:
        out.append(Usage(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)))
    return out


def _describe(status: int, body: bytes) -> str:
    detail = ""
    try:
        err = json.loads(body).get("error", {})
        detail = str(err.get("message", "")) if isinstance(err, dict) else str(err)
    except (ValueError, AttributeError):
        detail = body[:200].decode(errors="replace")
    if status == 401:
        return "Groq rejected the API key. Check GROQ_API_KEY in backend\\.env and restart the API."
    if status == 403:
        return f"Groq refused the request (403). {detail}".strip()
    if status == 404:
        return f"Groq does not offer this model to your account. {detail}".strip()
    if status == 413:
        return "The request is larger than Groq accepts. Ask about fewer documents at once."
    return f"Groq returned HTTP {status}. {detail[:300]}".strip()


def _error(body: bytes) -> dict[str, Any]:
    try:
        err = json.loads(body).get("error", {})
    except (ValueError, AttributeError):
        return {}
    return err if isinstance(err, dict) else {}


def _json_failure(body: bytes) -> bool:
    err = _error(body)
    return err.get("code") in ("json_validate_failed", "json_generate_failed") or "JSON" in str(
        err.get("message", "")
    )


def _failed_generation(body: bytes) -> str | None:
    """The text Groq could not validate, if it contains a JSON object we can try ourselves."""
    text = str(_error(body).get("failed_generation") or "")
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else None


class GroqBackend:
    name = "groq"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        models: list[str],
        timeout: float = 90.0,
        max_wait: float = 30.0,
        reasoning_effort: str = "low",
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._models = models
        self._timeout = timeout
        self._max_wait = max_wait
        self._effort = reasoning_effort
        self._transport = transport
        self._sleep = sleep

    def models(self) -> list[str]:
        return list(self._models)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, connect=15.0),
            transport=self._transport,
            headers={"Authorization": f"Bearer {self._key}"},
        )

    def _body(
        self,
        model: str,
        messages: list[Message],
        max_tokens: int,
        *,
        stream: bool,
        schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_completion_tokens": max_tokens,
            "temperature": 0.2,
            "stream": stream,
        }
        if model.startswith("openai/gpt-oss"):  # reasoning models: keep the reasoning short and private
            body["reasoning_effort"] = self._effort
            body["include_reasoning"] = False
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.get("title", "result"), "strict": True, "schema": schema},
            }
        return body

    def _wait_for(self, resp: httpx.Response) -> float:
        try:
            return max(0.0, float(resp.headers.get("retry-after", "5")))
        except ValueError:
            return 5.0

    def _json_mode_body(
        self, model: str, messages: list[Message], max_tokens: int, schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Fallback when strict structured output fails: plain JSON mode, with the schema in the prompt.
        The caller still validates the reply, so nothing unchecked gets through."""
        hint = Message(
            "system",
            "Reply with ONE JSON object only (no prose, no code fences) that matches this JSON schema:\n"
            + json.dumps(schema, ensure_ascii=False),
        )
        body = self._body(model, [*messages, hint], max_tokens, stream=False, schema=None)
        body["response_format"] = {"type": "json_object"}
        return body

    async def complete(
        self, model: str, messages: list[Message], *, max_tokens: int, json_schema: dict[str, Any] | None
    ) -> tuple[str, Usage]:
        body = self._body(model, messages, max_tokens, stream=False, schema=json_schema)
        json_mode = False
        async with self._client() as client:
            for attempt in range(4):
                try:
                    resp = await client.post(self._url, json=body)
                except httpx.HTTPError as e:
                    raise LLMError(
                        f"Could not reach Groq ({type(e).__name__}). Check the internet connection."
                    ) from e
                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = self._wait_for(resp) if resp.status_code == 429 else 2.0
                    if attempt < 2 and wait <= self._max_wait:
                        await self._sleep(wait)
                        continue
                    raise (
                        LLMError(
                            f"Groq rate limit reached; try again in {math.ceil(wait)} s.",
                            status=429,
                            retry_after=wait,
                        )
                        if resp.status_code == 429
                        else LLMError(_describe(resp.status_code, resp.content))
                    )
                if resp.status_code == 400 and json_schema is not None and _json_failure(resp.content):
                    # gpt-oss sometimes produces JSON the strict validator rejects (Groq: json_validate_failed)
                    if not json_mode:
                        json_mode = True
                        body = self._json_mode_body(model, messages, max_tokens, json_schema)
                        continue
                    salvaged = _failed_generation(resp.content)
                    if salvaged:
                        return salvaged, Usage()  # the caller parses and validates it
                if resp.status_code >= 400:
                    raise LLMError(_describe(resp.status_code, resp.content), status=resp.status_code)
                obj = resp.json()
                text = obj["choices"][0]["message"].get("content") or ""
                u = obj.get("usage") or {}
                return text, Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
        raise LLMError("Groq did not answer.")  # pragma: no cover - loop always returns or raises

    async def stream(
        self, model: str, messages: list[Message], *, max_tokens: int
    ) -> AsyncIterator[TextDelta | Notice | Usage]:
        body = self._body(model, messages, max_tokens, stream=True, schema=None)
        async with self._client() as client:
            for attempt in range(3):
                try:
                    async with client.stream("POST", self._url, json=body) as resp:
                        if resp.status_code == 429 or resp.status_code >= 500:
                            await resp.aread()
                            wait = self._wait_for(resp) if resp.status_code == 429 else 2.0
                            if attempt < 2 and wait <= self._max_wait:
                                yield Notice(
                                    f"Groq rate limit reached; continuing in {math.ceil(wait)} s."
                                    if resp.status_code == 429
                                    else "Groq is busy; retrying."
                                )
                                await self._sleep(wait)
                                continue
                            if resp.status_code == 429:
                                raise LLMError(
                                    f"Groq rate limit reached; try again in {math.ceil(wait)} s.",
                                    status=429,
                                    retry_after=wait,
                                )
                            raise LLMError(_describe(resp.status_code, resp.content), status=resp.status_code)
                        if resp.status_code >= 400:
                            raise LLMError(
                                _describe(resp.status_code, await resp.aread()), status=resp.status_code
                            )
                        async for line in resp.aiter_lines():
                            if line.startswith("data:"):
                                for item in parse_sse_data(line[5:]):
                                    yield item
                        return
                except httpx.HTTPError as e:
                    raise LLMError(f"Lost the connection to Groq ({type(e).__name__}).") from e


# --------------------------------------------------------------------------- policy and gateway


class ModelPolicy:
    """A firm's policy (`firms.model_policy`): which models are allowed and which serves each task.

    `{"allowed": ["openai/gpt-oss-120b"], "routes": {"qa": "openai/gpt-oss-120b"}}`; empty = defaults.
    """

    def __init__(self, catalogue: list[str], default: str, raw: dict[str, Any] | None = None) -> None:
        raw = raw or {}
        wanted = raw.get("allowed") or catalogue
        self.allowed = [m for m in wanted if m in catalogue] or catalogue[:1]
        self.default = default if default in self.allowed else self.allowed[0]
        routes = raw.get("routes") or {}
        self.routes: dict[str, str] = {t: m for t, m in routes.items() if t in TASKS and m in self.allowed}

    def resolve(self, task: TaskClass, requested: str | None = None) -> str:
        if requested and requested != "auto":
            if requested not in self.allowed:
                raise ModelNotAllowed(
                    f"Model {requested} is not permitted by your firm's policy.", status=422
                )
            return requested
        return self.routes.get(task, self.default)

    def as_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "default": self.default, "routes": self.routes}


def cost_usd(model: str, usage: Usage) -> float:
    price_in, price_out = get_settings().llm_prices_per_million.get(model, (0.0, 0.0))
    return round((usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000, 6)


class Gateway:
    def __init__(self, backend: Backend, policy: ModelPolicy) -> None:
        self.backend = backend
        self.policy = policy

    async def complete(
        self,
        task: TaskClass,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        chosen = self.policy.resolve(task, model)
        started = time.perf_counter()
        text, usage = await self.backend.complete(
            chosen,
            messages,
            max_tokens=max_tokens or get_settings().llm_max_output_tokens,
            json_schema=json_schema,
        )
        return Completion(
            text=text,
            model=chosen,
            usage=usage,
            latency_ms=round((time.perf_counter() - started) * 1000),
            cost_usd=cost_usd(chosen, usage),
        )

    async def stream(
        self,
        task: TaskClass,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamItem]:
        chosen = self.policy.resolve(task, model)
        started = time.perf_counter()
        usage = Usage()
        async for item in self.backend.stream(
            chosen, messages, max_tokens=max_tokens or get_settings().llm_max_output_tokens
        ):
            if isinstance(item, Usage):
                usage = item
            else:
                yield item
        yield Done(chosen, usage, round((time.perf_counter() - started) * 1000), cost_usd(chosen, usage))


_override: Backend | None = None


@contextmanager
def use_backend(backend: Backend) -> Iterator[None]:
    """Tests: route every gateway call to `backend` for the duration of the block."""
    global _override
    previous, _override = _override, backend
    try:
        yield
    finally:
        _override = previous


def get_backend() -> Backend:
    if _override is not None:
        return _override
    s = get_settings()
    if s.llm_backend == "groq":
        if s.groq_api_key is None or not s.groq_api_key.get_secret_value().strip():
            raise LLMError("DATAVIV_LLM_BACKEND is groq but GROQ_API_KEY is empty in backend\\.env.")
        return GroqBackend(
            s.groq_api_key.get_secret_value().strip(),
            base_url=s.groq_base_url,
            models=s.llm_models,
            timeout=s.llm_timeout_seconds,
            max_wait=s.llm_max_wait_seconds,
            reasoning_effort=s.llm_reasoning_effort,
        )
    return FakeLLM()


def gateway_for(model_policy: dict[str, Any] | None) -> Gateway:
    """The gateway for one firm: its backend plus its model policy."""
    backend = get_backend()
    catalogue = backend.models()
    default = get_settings().llm_default_model if backend.name != "fake" else catalogue[0]
    return Gateway(backend, ModelPolicy(catalogue, default, model_policy))
