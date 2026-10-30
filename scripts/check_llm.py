"""One tiny call through the model gateway, to prove the provider, key and model work.

Run from repo root:  python scripts/check_llm.py   (Windows: scripts\\windows\\check_llm.bat)
Prints the backend, model, token usage, latency and cost. Never prints the API key.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.core.llm import Done, LLMError, Message, Notice, TextDelta, gateway_for  # noqa: E402


async def main() -> int:
    s = get_settings()
    key = s.groq_api_key.get_secret_value().strip() if s.groq_api_key else ""
    print(f"Backend: {s.llm_backend}   default model: {s.llm_default_model}   GROQ_API_KEY: {'set' if key else 'missing'}")
    try:
        gw = gateway_for({})
        print(f"Calling {gw.policy.default} (streamed) ...", flush=True)
        print("  reply: ", end="", flush=True)
        async for item in gw.stream(
            "qa",
            [Message("system", "Reply with exactly: OK, Dataviv."), Message("user", "Say it.")],
            max_tokens=300,
        ):
            if isinstance(item, TextDelta):
                print(item.text, end="", flush=True)
            elif isinstance(item, Notice):
                print(f"\n  [{item.text}]", flush=True)
            elif isinstance(item, Done):
                print(
                    f"\n  model {item.model} | {item.usage.input_tokens} in / {item.usage.output_tokens} out tokens"
                    f" | {item.latency_ms} ms | ${item.cost_usd:.6f}"
                )
    except LLMError as e:
        print(f"\nFAILED: {e}")
        return 1
    print("LLM_OK" if s.llm_backend == "groq" else "LLM_OK (offline test model - no key configured)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
