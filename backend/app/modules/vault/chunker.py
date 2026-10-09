"""Structure-aware chunking (VLT-05).

Legal text is split where lawyers would cite it: numbered clauses and paragraphs (``12.``, ``12.3``,
``(a)``, ``(iv)``), ``Section``/``Article``/``Clause``/``Schedule`` headings, and short all-capitals
headings. Units are packed into chunks of at most ``max_chars`` without crossing a top-level heading,
and every chunk keeps its page range and character span in the document's concatenated text so a
citation can open the exact passage.
"""

import re
from dataclasses import dataclass

from app.modules.vault.extract import Page

MAX_CHARS = 1200
MIN_CHARS = 200

_HEADING = re.compile(
    r"^\s*(?:"
    r"(?P<num>\d{1,3}(?:\.\d{1,3}){0,3})[.)]?\s+\S"  # 12.  12.3  4)
    r"|(?P<kw>(?:section|article|clause|schedule|annexure|chapter|part)\s+[\w.\-]+)"
    r"|(?P<caps>[A-Z][A-Z0-9 ,&'()\-/]{3,80})$"  # SHORT CAPITALS HEADING
    r")",
    re.I,
)
_SUB = re.compile(r"^\s*\((?:[a-z]{1,2}|[ivxlc]{1,6}|\d{1,2})\)\s+\S", re.I)


@dataclass(slots=True)
class ChunkDraft:
    ordinal: int
    text: str
    section_path: str | None
    page_from: int
    page_to: int
    char_start: int
    char_end: int
    ocr_confidence: float | None = None


@dataclass(slots=True)
class _Unit:
    text: str
    page: int
    start: int
    end: int
    heading: str | None  # set when this unit opens a new top-level section


def _heading_label(line: str) -> str | None:
    m = _HEADING.match(line)
    if not m:
        return None
    if m.group("caps"):
        caps = m.group("caps").strip()
        # all-capitals only counts when the line is genuinely upper-case and short
        if caps != caps.upper() or len(caps.split()) > 12:
            return None
    label = line.strip()
    return label[:80] + ("…" if len(label) > 80 else "")


def _units(pages: list[Page]) -> list[_Unit]:
    """Paragraph-level units with absolute character offsets (pages joined with a blank line)."""
    units: list[_Unit] = []
    offset = 0
    for page in pages:
        text = page.text
        pos = 0
        for block in re.split(r"(\n\s*\n|\n(?=\s*(?:\(\w{1,6}\)|\d{1,3}(?:\.\d{1,3})*[.)]\s)))", text):
            if not block or block.isspace():
                pos += len(block)
                continue
            start = text.find(block, pos)
            pos = start + len(block)
            stripped = block.strip()
            if not stripped:
                continue
            lead = len(block) - len(block.lstrip())
            a = offset + start + lead
            first_line = stripped.splitlines()[0]
            heading = _heading_label(first_line)
            if heading is None and _SUB.match(first_line):
                heading = None  # sub-clauses stay inside their parent section
            units.append(_Unit(stripped, page.number, a, a + len(stripped), heading))
        offset += len(text) + 2
    return units


def _split_long(u: _Unit, max_chars: int) -> list[_Unit]:
    if len(u.text) <= max_chars:
        return [u]
    out: list[_Unit] = []
    sentences = re.split(r"(?<=[.;:!?])\s+", u.text)
    buf, buf_start, cursor = "", u.start, u.start
    for s in sentences:
        idx = u.text.find(s, cursor - u.start)
        s_start = u.start + (idx if idx >= 0 else cursor - u.start)
        if buf and len(buf) + 1 + len(s) > max_chars:
            out.append(_Unit(buf, u.page, buf_start, buf_start + len(buf), None))
            buf, buf_start = "", s_start
        while len(s) > max_chars:  # one enormous sentence: hard split
            out.append(_Unit(s[:max_chars], u.page, s_start, s_start + max_chars, None))
            s, s_start = s[max_chars:], s_start + max_chars
        if not buf:
            buf_start = s_start
        buf = f"{buf} {s}" if buf else s
        cursor = s_start + len(s)
    if buf:
        out.append(_Unit(buf, u.page, buf_start, buf_start + len(buf), None))
    if out:
        out[0].heading = u.heading
    return out


def chunk_pages(pages: list[Page], max_chars: int = MAX_CHARS, min_chars: int = MIN_CHARS) -> list[ChunkDraft]:
    units: list[_Unit] = []
    for u in _units(pages):
        units.extend(_split_long(u, max_chars))
    conf = {p.number: p.ocr_confidence for p in pages}

    chunks: list[ChunkDraft] = []
    section: str | None = None  # heading in force at the current position
    carried: str | None = None  # heading in force when the current chunk started
    cur: list[_Unit] = []

    def flush() -> None:
        nonlocal carried
        if not cur:
            return
        headings = [x.heading for x in cur if x.heading]
        label = " / ".join(headings) if headings else carried
        text = "\n\n".join(x.text for x in cur)
        pages_in = [x.page for x in cur]
        confs = [c for c in (conf.get(p) for p in set(pages_in)) if c is not None]
        chunks.append(
            ChunkDraft(
                ordinal=len(chunks),
                text=text,
                section_path=label,
                page_from=min(pages_in),
                page_to=max(pages_in),
                char_start=cur[0].start,
                char_end=cur[-1].end,
                ocr_confidence=min(confs) if confs else None,
            )
        )
        cur.clear()

    for u in units:
        size = sum(len(x.text) + 2 for x in cur)
        if u.heading is not None and cur and size >= min_chars:
            flush()
        elif cur and size + len(u.text) > max_chars:
            flush()
        if not cur:
            carried = section
        if u.heading is not None:
            section = u.heading
        cur.append(u)
    flush()
    return chunks
