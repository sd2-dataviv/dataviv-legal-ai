"""A tiny standard-library PDF writer: plain text in Helvetica on A4 pages.

Used where the platform itself must produce a PDF without compiled dependencies (ADR-011), e.g. the
signed copy and certificate made by the offline e-signature test backend. Text is reduced to Latin-1.
"""

import io
import textwrap

_PLAIN = str.maketrans(
    {
        0x2013: "-",  # en dash
        0x2014: "-",  # em dash
        0x2018: "'",
        0x2019: "'",
        0x201C: '"',
        0x201D: '"',
        0x2026: "...",
        0x20B9: "Rs.",  # rupee sign
        0x00A0: " ",  # no-break space
    }
)
_PAGE_W, _PAGE_H = 595, 842  # A4 in points
_MARGIN, _SIZE, _LEADING = 56, 10.5, 14
_LINES_PER_PAGE = int((_PAGE_H - 2 * _MARGIN) / _LEADING)
_WRAP = 92


def _latin1(s: str) -> str:
    return s.translate(_PLAIN).encode("latin-1", "replace").decode("latin-1")


def _escape(s: str) -> bytes:
    return _latin1(s).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1")


def text_pdf(title: str, paragraphs: list[str]) -> bytes:
    """A PDF whose first line is `title` (bold) followed by the paragraphs, wrapped and paginated."""
    lines: list[tuple[str, bool]] = [(title, True), ("", False)]
    for para in paragraphs:
        if not para.strip():
            lines.append(("", False))
            continue
        lines.extend((part, False) for part in textwrap.wrap(para, _WRAP) or [""])
    pages = [lines[i : i + _LINES_PER_PAGE] for i in range(0, len(lines), _LINES_PER_PAGE)] or [[]]

    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    regular = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    pages_id = len(objs) + 2 * len(pages) + 1  # the Pages object comes after every page and its content
    page_ids = []
    for page in pages:
        ops = [b"BT %.1f TL %d %d Td" % (_LEADING, _MARGIN, _PAGE_H - _MARGIN)]
        for text, is_bold in page:
            ops.append(
                b"/F%d %.1f Tf (%s) Tj T*"
                % (2 if is_bold else 1, _SIZE + (2 if is_bold else 0), _escape(text))
            )
        ops.append(b"ET")
        stream = b"\n".join(ops)
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>"
                % (pages_id, _PAGE_W, _PAGE_H, content, regular, bold)
            )
        )
    kids = b" ".join(b"%d 0 R" % i for i in page_ids)
    assert add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))) == pages_id
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(
        b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, xref)
    )
    return out.getvalue()
