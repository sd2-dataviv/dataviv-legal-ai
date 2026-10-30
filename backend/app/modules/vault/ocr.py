"""Adapter for the Dataviv OCR service (VLT-04). Contract: docs/steps/step1-vault.md.

Only this file knows the OCR service's API. If the service changes, change ``_parse``/``recognise`` here.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings


@dataclass(slots=True)
class OcrPage:
    page: int
    text: str
    confidence: float | None


class OcrUnavailable(Exception):
    pass


class OcrClient:
    def __init__(self, base_url: str, languages: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.languages = languages
        self.timeout = timeout

    async def recognise(self, data: bytes, filename: str, mime: str) -> list[OcrPage]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.base_url}/ocr",
                    files={"file": (filename, data, mime)},
                    data={"languages": self.languages},
                )
                r.raise_for_status()
                return self._parse(r.json())
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise OcrUnavailable(f"OCR service error: {exc}") from exc

    @staticmethod
    def _parse(body: dict[str, Any]) -> list[OcrPage]:
        return [OcrPage(int(p["page"]), str(p.get("text", "")), p.get("confidence")) for p in body["pages"]]


def get_ocr_client() -> OcrClient | None:
    s = get_settings()
    if not s.ocr_url:
        return None
    return OcrClient(s.ocr_url, s.ocr_languages, s.ocr_timeout_seconds)
