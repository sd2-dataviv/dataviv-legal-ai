"""FND-06: model gateway — Groq wire format, rate limits, errors, firm model policy. Offline."""

import json
from typing import Any

import httpx
import pytest

from app.core.llm import (
    Done,
    FakeLLM,
    Gateway,
    GroqBackend,
    LLMError,
    Message,
    ModelNotAllowed,
    ModelPolicy,
    Notice,
    TextDelta,
    Usage,
    cost_usd,
    parse_sse_data,
)

MODEL = "openai/gpt-oss-120b"


def sse_body(*chunks: dict[str, Any]) -> bytes:
    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"


def delta(text: str) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": {"content": text}}]}


def backend(handler: Any, sleeps: list[float] | None = None) -> GroqBackend:
    async def fake_sleep(s: float) -> None:
        (sleeps if sleeps is not None else []).append(s)

    return GroqBackend(
        "gsk_test",
        base_url="https://api.groq.test/openai/v1",
        models=[MODEL],
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
        max_wait=30,
    )


def test_parse_sse_data_reads_deltas_and_groq_usage() -> None:
    assert parse_sse_data("[DONE]") == []
    assert parse_sse_data(json.dumps(delta("Bail"))) == [TextDelta("Bail")]
    final = {
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "x_groq": {"usage": {"prompt_tokens": 120, "completion_tokens": 30}},
    }
    assert parse_sse_data(json.dumps(final)) == [Usage(120, 30)]
    with pytest.raises(LLMError):
        parse_sse_data(json.dumps({"error": {"message": "boom"}}))


async def test_stream_sends_reasoning_controls_and_yields_text_then_usage() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        body = sse_body(
            delta("The applicant "),
            delta("is on bail. [S1]"),
            {"choices": [{"delta": {}}], "x_groq": {"usage": {"prompt_tokens": 50, "completion_tokens": 9}}},
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    items = [i async for i in backend(handler).stream(MODEL, [Message("user", "q")], max_tokens=500)]
    assert items == [TextDelta("The applicant "), TextDelta("is on bail. [S1]"), Usage(50, 9)]
    b = seen["body"]
    assert b["model"] == MODEL and b["stream"] is True and b["max_completion_tokens"] == 500
    assert b["reasoning_effort"] == "low" and b["include_reasoning"] is False
    assert seen["auth"] == "Bearer gsk_test"


async def test_stream_waits_out_a_rate_limit_then_continues() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "7"}, json={"error": {"message": "slow down"}})
        return httpx.Response(200, content=sse_body(delta("ok")))

    items = [i async for i in backend(handler, sleeps).stream(MODEL, [Message("user", "q")], max_tokens=10)]
    assert isinstance(items[0], Notice) and "7 s" in items[0].text
    assert TextDelta("ok") in items and sleeps == [7.0]


async def test_long_rate_limit_and_bad_key_raise_clear_errors() -> None:
    def limited(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "120"})

    with pytest.raises(LLMError) as e:
        _ = [i async for i in backend(limited).stream(MODEL, [Message("user", "q")], max_tokens=10)]
    assert e.value.status == 429 and e.value.retry_after == 120

    def bad_key(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})

    with pytest.raises(LLMError, match="rejected the API key") as e2:
        await backend(bad_key).complete(MODEL, [Message("user", "q")], max_tokens=10, json_schema=None)
    assert "gsk_test" not in str(e2.value)


async def test_complete_with_json_schema_uses_strict_structured_output() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            },
        )

    schema = {
        "title": "check",
        "type": "object",
        "additionalProperties": False,
        "required": ["ok"],
        "properties": {"ok": {"type": "boolean"}},
    }
    text, usage = await backend(handler).complete(
        MODEL, [Message("user", "q")], max_tokens=50, json_schema=schema
    )
    assert json.loads(text) == {"ok": True} and usage == Usage(11, 4)
    rf = seen["body"]["response_format"]
    assert (
        rf["type"] == "json_schema"
        and rf["json_schema"]["strict"] is True
        and rf["json_schema"]["name"] == "check"
    )


SCHEMA = {
    "title": "check",
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}


def json_failure(generated: str = "") -> httpx.Response:
    return httpx.Response(
        400,
        json={
            "error": {
                "message": "Failed to validate JSON. Please adjust your prompt.",
                "type": "invalid_request_error",
                "code": "json_validate_failed",
                "failed_generation": generated,
            }
        },
    )


async def test_rejected_structured_output_falls_back_to_json_mode() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        if len(bodies) == 1:
            return json_failure()
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {}})

    text, _ = await backend(handler).complete(
        MODEL, [Message("user", "q")], max_tokens=50, json_schema=SCHEMA
    )
    assert json.loads(text) == {"ok": True} and len(bodies) == 2
    assert bodies[0]["response_format"]["type"] == "json_schema"
    assert bodies[1]["response_format"] == {"type": "json_object"}
    assert '"required": ["ok"]' in bodies[1]["messages"][-1]["content"]  # the schema travels in the prompt


async def test_twice_rejected_output_is_salvaged_for_the_caller_to_check() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return json_failure('Here you go: {"ok": true} hope that helps')

    text, usage = await backend(handler).complete(
        MODEL, [Message("user", "q")], max_tokens=50, json_schema=SCHEMA
    )
    assert text == '{"ok": true}' and usage == Usage()

    def empty(req: httpx.Request) -> httpx.Response:
        return json_failure("")

    with pytest.raises(LLMError, match="JSON"):
        await backend(empty).complete(MODEL, [Message("user", "q")], max_tokens=50, json_schema=SCHEMA)


def test_model_policy_routes_and_limits_models() -> None:
    cat = [MODEL, "other/model"]
    p = ModelPolicy(
        cat, MODEL, {"allowed": ["other/model", "not/in-catalogue"], "routes": {"draft": "other/model"}}
    )
    assert p.allowed == ["other/model"] and p.default == "other/model"
    assert p.resolve("draft") == "other/model" and p.resolve("qa", "auto") == "other/model"
    with pytest.raises(ModelNotAllowed):
        p.resolve("qa", MODEL)
    assert ModelPolicy(cat, MODEL, {}).resolve("qa") == MODEL


async def test_gateway_reports_usage_latency_and_cost() -> None:
    gw = Gateway(FakeLLM(), ModelPolicy(["fake-1"], "fake-1"))
    msgs = [
        Message("user", '<source id="S1" document="d">The applicant is on bail.</source> Question: bail?')
    ]
    items = [i async for i in gw.stream("qa", msgs)]
    assert (
        "".join(i.text for i in items if isinstance(i, TextDelta)).strip() == "The applicant is on bail. [S1]"
    )
    done = items[-1]
    assert isinstance(done, Done) and done.model == "fake-1" and done.usage.output_tokens > 0
    assert cost_usd(MODEL, Usage(1_000_000, 1_000_000)) == pytest.approx(0.75)
