"""Embedding gateway (VLT-08, ADR-019): turns passages and search queries into dense vectors.

Backends sit behind one protocol, chosen by DATAVIV_EMBEDDINGS_BACKEND:

- ``none``  semantic search off; search stays keyword-only (the Step 1 behaviour).
- ``fake``  deterministic, offline, pure Python (tests). Hashes words and crude stems into 256
            dimensions, so texts that share vocabulary are close. Enough to exercise filters, fusion
            and thresholds; it knows nothing about meaning.
- ``http``  any OpenAI-compatible ``/embeddings`` endpoint: Hugging Face TEI, Infinity, vLLM, Ollama.
            Production: TEI serving BAAI/bge-m3 on the GPU VM. Pure httpx, so it also runs under
            Windows Smart App Control (ADR-011).
- ``ollama`` Ollama's native ``/api/embed`` (e.g. ``bge-m3`` on the office GPU server), which keeps the
            model loaded between searches. Pure httpx as well.
- ``local`` sentence-transformers inside this process (CPU or GPU). Linux/macOS dev convenience;
            needs ``pip install -e ".[local-embeddings]"`` (PyTorch, ~1 GB with the CPU build).

bge-m3 needs no query or passage prefixes. Every vector returned is L2-normalised, so cosine
similarity is a dot product and stores can compare scores across backends.
"""

import asyncio
import hashlib
import math
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Literal, Protocol

import httpx

from app.core.config import get_settings

Kind = Literal["query", "passage"]


class EmbeddingError(Exception):
    """The embedding backend could not produce vectors (down, misconfigured, wrong dimension)."""


class Embedder(Protocol):
    name: str  # model identifier recorded on every chunk it embedded, e.g. "BAAI/bge-m3"
    dim: int

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]: ...


def normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else v


# --------------------------------------------------------------------------- fake backend

_WORD = re.compile(r"[0-9a-zऀ-ॿ]+")
_FAKE_STOP = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with "
    "shall may any all such which who what when where how".split()
)
_SUFFIXES = ("ations", "ation", "ings", "ing", "ions", "ion", "ed", "es", "s")


def _stem(w: str) -> str:
    for suf in _SUFFIXES:
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


class FakeEmbedder:
    """Bag of hashed words and stems. Unrelated texts score near 0, shared vocabulary scores high."""

    name = "fake-hash-256"
    dim = 256

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for w in _WORD.findall(text.lower()):
            if w in _FAKE_STOP or len(w) < 2:
                continue
            for feature, weight in ((w, 1.0), ("~" + _stem(w), 0.7)):
                h = hashlib.blake2b(feature.encode(), digest_size=8).digest()
                i = int.from_bytes(h[:4], "little") % self.dim
                v[i] += weight if h[4] & 1 else -weight
        return normalise(v)

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        return [self._vector(t) for t in texts]


# --------------------------------------------------------------------------- HTTP backend


class HttpEmbedder:
    """OpenAI-compatible ``POST {base_url}/embeddings``. TEI, Infinity, vLLM and Ollama all speak it."""

    def __init__(
        self, base_url: str, model: str, dim: int, api_key: str | None = None, timeout: float = 60.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.name = model
        self.dim = dim
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout

    def _request(self, texts: list[str]) -> tuple[str, dict[str, Any]]:
        return f"{self.base_url}/embeddings", {"model": self.name, "input": texts}

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        if not texts:
            return []
        url, body = self._request(texts)
        last = ""
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
            for attempt in range(4):
                try:
                    resp = await client.post(url, json=body)
                except httpx.HTTPError as exc:
                    last = f"cannot reach {self.base_url}: {type(exc).__name__}"
                else:
                    if resp.status_code == 200:
                        return self._parse(resp.json(), len(texts))
                    last = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    if resp.status_code not in (429, 500, 502, 503, 504):
                        break
                await asyncio.sleep(0.5 * 2**attempt)
        raise EmbeddingError(f"Embedding service failed ({last})")

    def _parse(self, data: dict[str, Any], n: int) -> list[list[float]]:
        items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        return self._checked([d["embedding"] for d in items], n)

    def _checked(self, raw: list[list[float]], n: int) -> list[list[float]]:
        if len(raw) != n:
            raise EmbeddingError(f"Embedding service returned {len(raw)} vectors for {n} texts")
        out = [normalise([float(x) for x in v]) for v in raw]
        if out and len(out[0]) != self.dim:
            raise EmbeddingError(
                f"{self.name} returned {len(out[0])} dimensions; DATAVIV_EMBEDDINGS_DIM is {self.dim}"
            )
        return out


class OllamaEmbedder(HttpEmbedder):
    """Ollama's native ``POST {base_url}/api/embed``, e.g. bge-m3 on the same server as the chat model.

    Used rather than Ollama's OpenAI-compatible /v1/embeddings because only the native API takes
    ``keep_alive``: without it Ollama unloads the model after five idle minutes and the next search waits
    for it to load again. Inputs longer than the model's context are truncated by the server.
    """

    def __init__(
        self, base_url: str, model: str, dim: int, keep_alive: str = "30m", timeout: float = 60.0
    ) -> None:
        super().__init__(base_url, model, dim, None, timeout)
        self._keep_alive = keep_alive

    def _request(self, texts: list[str]) -> tuple[str, dict[str, Any]]:
        body = {"model": self.name, "input": texts, "keep_alive": self._keep_alive, "truncate": True}
        return f"{self.base_url}/api/embed", body

    def _parse(self, data: dict[str, Any], n: int) -> list[list[float]]:
        return self._checked(data.get("embeddings") or [], n)

    def preload(self) -> None:
        """Load the model on the server now (blocking; run it in a thread) so the first search is quick."""
        url, body = self._request(["warm up"])
        try:
            resp = httpx.post(url, json=body, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"cannot reach {self.base_url}: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise EmbeddingError(f"Embedding service failed (HTTP {resp.status_code}: {resp.text[:200]})")
        self._parse(resp.json(), 1)  # also checks the dimension


# --------------------------------------------------------------------------- local backend


class LocalEmbedder:
    """sentence-transformers in-process. The model loads once, on first use, in a worker thread."""

    def __init__(self, model: str, dim: int, max_tokens: int, batch_size: int, device: str | None) -> None:
        self.name = model
        self.dim = dim
        self._max_tokens = max_tokens
        self._batch = batch_size
        self._device = device
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise EmbeddingError(
                        "DATAVIV_EMBEDDINGS_BACKEND=local needs sentence-transformers: "
                        'pip install -e ".[local-embeddings]" (or bash scripts/linux/setup.sh --semantic)'
                    ) from exc
                m = SentenceTransformer(self.name, device=self._device)
                m.max_seq_length = self._max_tokens
                dim_of = getattr(m, "get_embedding_dimension", None) or m.get_sentence_embedding_dimension
                got = dim_of()  # renamed in sentence-transformers 5
                if got != self.dim:
                    raise EmbeddingError(
                        f"{self.name} has {got} dimensions; DATAVIV_EMBEDDINGS_DIM is {self.dim}"
                    )
                self._model = m
            return self._model

    def preload(self) -> None:
        """Load the model now (blocking; run it in a thread) so the first search does not wait for it."""
        self._load()

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._load().encode(
            texts, batch_size=self._batch, normalize_embeddings=True, show_progress_bar=False
        )
        return [[float(x) for x in v] for v in vectors]

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        if not texts:
            return []
        try:
            return await asyncio.to_thread(self._encode, texts)
        except EmbeddingError:
            raise
        except Exception as exc:  # torch / tokenizer failures surface as one error type
            raise EmbeddingError(f"Local embedding failed: {type(exc).__name__}: {exc}") from exc


# --------------------------------------------------------------------------- selection

_override: Embedder | None = None
_cached: Embedder | None = None


@contextmanager
def use_embedder(embedder: Embedder | None) -> Iterator[None]:
    """Tests: route every embedding call to ``embedder`` (None = semantic search off) for the block."""
    global _override
    previous, _override = _override, embedder if embedder is not None else _Off()
    try:
        yield
    finally:
        _override = previous


class _Off:
    name = "none"
    dim = 0

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        raise EmbeddingError("Semantic search is off")


def get_embedder() -> Embedder | None:
    """The configured embedder, or None when semantic search is off."""
    global _cached
    if _override is not None:
        return None if isinstance(_override, _Off) else _override
    s = get_settings()
    if s.embeddings_backend == "none":
        return None
    if _cached is None or _cached.name != _configured_name():
        _cached = _build()
    return _cached


def _configured_name() -> str:
    s = get_settings()
    return FakeEmbedder.name if s.embeddings_backend == "fake" else s.embeddings_model


def _build() -> Embedder:
    s = get_settings()
    if s.embeddings_backend == "fake":
        return FakeEmbedder()
    if s.embeddings_backend == "http":
        if not s.embeddings_url:
            raise EmbeddingError("DATAVIV_EMBEDDINGS_BACKEND=http needs DATAVIV_EMBEDDINGS_URL")
        key = s.embeddings_api_key.get_secret_value() if s.embeddings_api_key else None
        return HttpEmbedder(
            s.embeddings_url, s.embeddings_model, s.embeddings_dim, key, s.embeddings_timeout_seconds
        )
    if (
        s.embeddings_backend == "ollama"
    ):  # the chat model's server unless DATAVIV_EMBEDDINGS_URL says otherwise
        return OllamaEmbedder(
            s.embeddings_url or s.ollama_url,
            s.embeddings_model,
            s.embeddings_dim,
            s.ollama_keep_alive,
            s.embeddings_timeout_seconds,
        )
    return LocalEmbedder(
        s.embeddings_model, s.embeddings_dim, s.embeddings_max_tokens, s.embeddings_batch, s.embeddings_device
    )


async def embed_batched(
    embedder: Embedder,
    texts: list[str],
    kind: Kind = "passage",
    on_progress: Callable[[int, int], Any] | None = None,
) -> list[list[float]]:
    """Embed many texts in batches of DATAVIV_EMBEDDINGS_BATCH, reporting (done, total) after each."""
    size = max(1, get_settings().embeddings_batch)
    out: list[list[float]] = []
    for i in range(0, len(texts), size):
        out.extend(await embedder.embed(texts[i : i + size], kind))
        if on_progress is not None:
            r = on_progress(len(out), len(texts))
            if asyncio.iscoroutine(r):
                await r
    return out
