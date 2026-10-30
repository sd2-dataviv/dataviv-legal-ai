"""Text extraction per page (VLT-03). Pure Python on purpose: no compiled dependencies, so it runs on
locked-down Windows (Smart App Control) as well as Linux. Layout-aware parsing (Docling) comes later.

Each extractor returns ``Page`` objects. A page with too little text is flagged ``needs_ocr``.
"""

import email
import email.policy
import html
import io
import re
import zipfile
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import PurePath
from xml.etree import ElementTree as ET

MIN_TEXT_CHARS = 25

SUPPORTED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".eml": "message/rfc822",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/tiff"}


class UnsupportedFile(Exception):
    pass


@dataclass(slots=True)
class Page:
    number: int  # 1-based
    text: str
    needs_ocr: bool = False
    ocr_confidence: float | None = None


def mime_for(filename: str) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFile(f"Unsupported file type '{ext or '(none)'}'")
    return SUPPORTED_EXTENSIONS[ext]


def extract(data: bytes, mime: str) -> list[Page]:
    if mime == "application/pdf":
        return _pdf(data)
    if mime.endswith("wordprocessingml.document"):
        return _docx(data)
    if mime in ("text/plain", "text/markdown"):
        return _plain(_decode(data))
    if mime == "message/rfc822":
        return _plain(_eml(data))
    if mime in IMAGE_MIMES:
        return [Page(1, "", needs_ocr=True)]
    raise UnsupportedFile(mime)


def _normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _page(number: int, text: str) -> Page:
    t = _normalise(text)
    return Page(number, t, needs_ocr=len(t) < MIN_TEXT_CHARS)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("cp1252", errors="replace")


def _plain(text: str) -> list[Page]:
    # Form feeds mark page breaks in exported text; otherwise one logical page.
    parts = text.split("\f")
    pages = [_page(i + 1, p) for i, p in enumerate(parts)]
    for p in pages:  # plain text is never OCR material
        p.needs_ocr = False
    return pages or [Page(1, "")]


def _pdf(data: bytes) -> list[Page]:
    from pypdf import PdfReader  # pure-Python PDF parser

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise UnsupportedFile("PDF is password-protected") from exc
    pages: list[Page] = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append(_page(i + 1, text))
    return pages or [Page(1, "", needs_ocr=True)]


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx(data: bytes) -> list[Page]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise UnsupportedFile("Not a valid .docx file") from exc
    root = ET.fromstring(xml)  # noqa: S314 - see module note
    pages: list[list[str]] = [[]]
    for para in root.iter(f"{_W}p"):
        buf: list[str] = []
        for node in para.iter():
            if node.tag == f"{_W}t" and node.text:
                buf.append(node.text)
            elif node.tag == f"{_W}tab":
                buf.append("\t")
            elif node.tag == f"{_W}br" and node.get(f"{_W}type") == "page":
                pages[-1].append("".join(buf))
                buf = []
                pages.append([])
            elif node.tag == f"{_W}lastRenderedPageBreak" and (buf or pages[-1]):
                pages[-1].append("".join(buf))
                buf = []
                pages.append([])
        pages[-1].append("".join(buf))
    result = [_page(i + 1, "\n".join(lines)) for i, lines in enumerate(pages)]
    for p in result:  # a DOCX page with no text is blank, not a scan
        p.needs_ocr = False
    return [p for p in result if p.text] or [Page(1, "")]


def _eml(data: bytes) -> str:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    if not isinstance(msg, EmailMessage):  # always true with policy.default; narrows the type
        return ""
    head = "\n".join(f"{h}: {msg[h]}" for h in ("From", "To", "Cc", "Date", "Subject") if msg[h])
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        body = str(body_part.get_content())
        if body_part.get_content_type() == "text/html":
            body = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", body)))
    return f"{head}\n\n{body}"
