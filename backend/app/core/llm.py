"""Model gateway (FND-06; ADR-006). Every model call in the codebase goes through here.

Callers pass a *task class*; the firm's model policy decides which model serves it.
Step 0 ships the interface and a deterministic FakeLLM; Step 1 adds the LiteLLM backend.
"""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol

TaskClass = Literal["extract", "qa", "draft", "reason"]


@dataclass(slots=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(slots=True)
class Completion:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    meta: dict[str, object] = field(default_factory=dict)


class LLM(Protocol):
    async def complete(self, task: TaskClass, messages: list[Message]) -> Completion: ...
    def stream(self, task: TaskClass, messages: list[Message]) -> AsyncIterator[str]: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class FakeLLM:
    """Deterministic, offline. Echoes the last user message; embeddings are hash-derived."""

    model = "fake-1"

    async def complete(self, task: TaskClass, messages: list[Message]) -> Completion:
        last = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return Completion(text=f"[{task}] {last}", model=self.model, input_tokens=len(last.split()))

    async def stream(self, task: TaskClass, messages: list[Message]) -> AsyncIterator[str]:
        for word in (await self.complete(task, messages)).text.split():
            yield word + " "

    async def embed(self, texts: list[str]) -> list[list[float]]:
        def vec(t: str) -> list[float]:
            d = hashlib.sha256(t.encode()).digest()
            return [b / 255 for b in d[:16]]

        return [vec(t) for t in texts]


def get_llm() -> LLM:
    from app.core.config import get_settings

    if get_settings().llm_backend == "fake":
        return FakeLLM()
    raise NotImplementedError("LiteLLM backend arrives in Step 1 (Vault)")
