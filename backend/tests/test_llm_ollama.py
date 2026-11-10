"""FND-06: model gateway, local Ollama backend: native /api/chat wire format, errors, selection. Offline."""

import json
import re
from typing import Any

import httpx
import pytest

from app.core import llm
from app.core.config import get_settings
from app.core.llm import LLMError, Message, Notice, OllamaBackend, TextDelta, Usage, parse_ollama_line

MODEL = "gpt-oss:20b"
URL = "http://ollama.test:11434"
SCHEMA = {
    "title": "probe",
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def ndjson(*lines: dict[str, Any]) -> bytes:
    return b"".join(json.dumps(x).encode() + b"\n" for x in lines)


def chunk(content: str = "", thinking: str = "") -> dict[str, Any]:
    return {
        "model": MODEL,
        "message": {"role": "assistant", "content": content, "thinking": thinking},
        "done": False,
    }


def final(prompt: int = 0, out: int = 0, reason: str = "stop") -> dict[str, Any]:
    return {
        "model": MODEL,
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "done_reason": reason,
        "prompt_eval_count": prompt,
        "eval_count": out,
    }


def backend(handler: Any, sleeps: list[float] | None = None) -> OllamaBackend:
    async def fake_sleep(s: float) -> None:
        (sleeps if sleeps is not None else []).append(s)

    return OllamaBackend(
        URL, models=[MODEL], num_ctx=8192, transport=httpx.MockTransport(handler), sleep=fake_sleep
    )


def test_parse_ollama_line_keeps_content_and_drops_reasoning() -> None:
    assert parse_ollama_line("") == []
    assert parse_ollama_line(json.dumps(chunk(thinking="let me think"))) == []
    assert parse_ollama_line(json.dumps(chunk("Bail"))) == [TextDelta("Bail")]
    assert parse_ollama_line(json.dumps(final(120, 30))) == [Usage(120, 30)]
    with pytest.raises(LLMError, match="boom"):
        parse_ollama_line(json.dumps({"error": "boom"}))


async def test_stream_sends_context_and_reasoning_then_yields_text_and_usage() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers.get("authorization")
        body = ndjson(
            chunk(thinking="hmm"), chunk("The applicant "), chunk("is on bail. [S1]"), final(200, 12)
        )
        return httpx.Response(200, content=body)

    items = [i async for i in backend(handler).stream(MODEL, [Message("user", "q")], max_tokens=300)]
    assert seen["url"] == f"{URL}/api/chat" and seen["auth"] is None
    body = seen["body"]
    assert body["stream"] is True and body["think"] == "low" and "format" not in body
    assert body["options"] == {"temperature": 0.2, "num_predict": 300, "num_ctx": 8192}
    assert body["messages"] == [{"role": "user", "content": "q"}]
    assert "".join(i.text for i in items if isinstance(i, TextDelta)) == "The applicant is on bail. [S1]"
    assert items[-1] == Usage(200, 12)


async def test_reasoning_is_only_requested_from_gpt_oss() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={**final(5, 2), "message": {"role": "assistant", "content": "OK"}})

    b = OllamaBackend(URL, models=["llama3.1:8b"], transport=httpx.MockTransport(handler))
    assert await b.complete("llama3.1:8b", [Message("user", "q")], max_tokens=10, json_schema=None) == (
        "OK",
        Usage(5, 2),
    )
    assert "think" not in seen


async def test_complete_with_json_schema_sends_it_as_format_and_strips_fences() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        reply = {**final(50, 8), "message": {"role": "assistant", "content": '```json\n{"ok": true}\n```'}}
        return httpx.Response(200, json=reply)

    text, usage = await backend(handler).complete(
        MODEL, [Message("user", "q")], max_tokens=50, json_schema=SCHEMA
    )
    assert seen["format"] == SCHEMA and seen["stream"] is False
    assert text == '{"ok": true}' and usage == Usage(50, 8)


async def test_schema_the_server_rejects_falls_back_to_json_mode() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        if len(bodies) == 1:
            return httpx.Response(400, json={"error": "invalid format"})
        return httpx.Response(
            200, json={**final(), "message": {"role": "assistant", "content": '{"ok": false}'}}
        )

    text, _ = await backend(handler).complete(
        MODEL, [Message("user", "q")], max_tokens=50, json_schema=SCHEMA
    )
    assert text == '{"ok": false}'
    assert bodies[1]["format"] == "json" and "JSON schema" in bodies[1]["messages"][-1]["content"]


async def test_reasoning_that_uses_the_whole_budget_is_a_clear_error() -> None:
    def stream_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ndjson(chunk(thinking="long..."), final(10, 300, "length")))

    with pytest.raises(LLMError, match="output budget"):
        [i async for i in backend(stream_handler).stream(MODEL, [Message("user", "q")], max_tokens=300)]

    def complete_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=final(10, 300, "length"))

    with pytest.raises(LLMError, match="output budget"):
        await backend(complete_handler).complete(
            MODEL, [Message("user", "q")], max_tokens=300, json_schema=None
        )


async def test_busy_server_is_retried_and_missing_model_says_how_to_pull_it() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def busy_then_ok(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "server busy"})
        return httpx.Response(200, content=ndjson(chunk("OK"), final(1, 1)))

    items = [
        i async for i in backend(busy_then_ok, sleeps).stream(MODEL, [Message("user", "q")], max_tokens=5)
    ]
    assert isinstance(items[0], Notice) and sleeps == [2.0]
    assert [i for i in items if isinstance(i, TextDelta)] == [TextDelta("OK")]

    def missing(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": f"model '{MODEL}' not found"})

    with pytest.raises(LLMError, match=f"ollama pull {MODEL}") as err:
        await backend(missing).complete(MODEL, [Message("user", "q")], max_tokens=5, json_schema=None)
    assert err.value.status == 404


async def test_unreachable_server_names_the_address() -> None:
    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(LLMError, match=re.escape(URL)):
        await backend(down).complete(MODEL, [Message("user", "q")], max_tokens=5, json_schema=None)
    with pytest.raises(LLMError, match=re.escape(URL)):
        [i async for i in backend(down).stream(MODEL, [Message("user", "q")], max_tokens=5)]


def test_settings_select_the_ollama_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "llm_backend", "ollama")
    monkeypatch.setattr(s, "ollama_url", URL)
    monkeypatch.setattr(s, "llm_default_model", MODEL)
    monkeypatch.setattr(s, "llm_models", ["openai/gpt-oss-120b"])  # left over from Groq: not offered
    b = llm.get_backend()
    assert isinstance(b, OllamaBackend) and b.models() == [MODEL]
    gw = llm.gateway_for({"allowed": ["openai/gpt-oss-120b"]})  # an old firm policy falls back safely
    assert gw.policy.resolve("qa") == MODEL
    assert llm.cost_usd(MODEL, Usage(1_000_000, 1_000_000)) == 0.0  # local: no per-token price
