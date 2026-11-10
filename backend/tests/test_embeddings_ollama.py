"""VLT-08: embedding gateway, Ollama backend: native /api/embed wire format, checks, selection. Offline."""

import json
import math
from typing import Any

import httpx
import pytest

from app.core import embeddings
from app.core.config import get_settings
from app.core.embeddings import EmbeddingError, OllamaEmbedder
from app.core.vectors import collection_name

URL = "http://ollama.test:11434"


def mock(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    transport = httpx.MockTransport(handler)
    real_async, real_sync = httpx.AsyncClient, httpx.Client
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_async(transport=transport, **kw))
    monkeypatch.setattr(httpx, "post", lambda url, **kw: real_sync(transport=transport).post(url, **kw))


def vector(i: int, dim: int = 4) -> list[float]:
    return [float(i + 1)] * dim  # not normalised: the embedder must normalise


async def test_embed_uses_native_api_with_keep_alive_and_normalises(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        n = len(seen["body"]["input"])
        return httpx.Response(200, json={"model": "bge-m3", "embeddings": [vector(i) for i in range(n)]})

    mock(monkeypatch, handler)
    out = await OllamaEmbedder(URL, "bge-m3", 4, keep_alive="45m").embed(["a", "b"], "passage")
    assert seen["url"] == f"{URL}/api/embed"
    assert seen["body"] == {"model": "bge-m3", "input": ["a", "b"], "keep_alive": "45m", "truncate": True}
    assert len(out) == 2 and all(math.isclose(sum(x * x for x in v), 1.0) for v in out)


async def test_wrong_dimension_count_or_missing_model_are_clear_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies: list[httpx.Response] = [
        httpx.Response(200, json={"embeddings": [vector(0, dim=8)]}),
        httpx.Response(200, json={"embeddings": []}),
        httpx.Response(404, json={"error": 'model "bge-m3" not found, try pulling it first'}),
    ]
    mock(monkeypatch, lambda req: replies.pop(0))
    e = OllamaEmbedder(URL, "bge-m3", 4)
    with pytest.raises(EmbeddingError, match="8 dimensions"):
        await e.embed(["a"], "query")
    with pytest.raises(EmbeddingError, match="0 vectors for 1"):
        await e.embed(["a"], "query")
    with pytest.raises(EmbeddingError, match="try pulling it"):  # 404 is not retried
        await e.embed(["a"], "query")
    assert await e.embed([], "query") == []


def test_preload_warms_the_model_on_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"embeddings": [vector(0)]})

    mock(monkeypatch, handler)
    OllamaEmbedder(URL, "bge-m3", 4, keep_alive="30m").preload()
    assert seen[0]["keep_alive"] == "30m" and len(seen[0]["input"]) == 1

    mock(monkeypatch, lambda req: httpx.Response(500, text="boom"))
    with pytest.raises(EmbeddingError, match="HTTP 500"):
        OllamaEmbedder(URL, "bge-m3", 4).preload()


def test_settings_select_ollama_on_the_chat_models_server(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "embeddings_backend", "ollama")
    monkeypatch.setattr(s, "embeddings_model", "bge-m3")
    monkeypatch.setattr(s, "embeddings_dim", 1024)
    monkeypatch.setattr(s, "embeddings_url", None)
    monkeypatch.setattr(s, "ollama_url", URL)
    monkeypatch.setattr(embeddings, "_cached", None)
    monkeypatch.setattr(embeddings, "_override", None)
    e = embeddings.get_embedder()
    assert isinstance(e, OllamaEmbedder) and e.base_url == URL and e.name == "bge-m3" and e.dim == 1024
    # a new model name is a new collection, so vectors from the old runtime are never mixed in
    assert collection_name("bge-m3", 1024) != collection_name("BAAI/bge-m3", 1024)
