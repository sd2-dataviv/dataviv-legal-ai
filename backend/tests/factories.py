"""Builders for test files, so tests carry no binary fixtures."""

import io
import zipfile


def make_pdf(pages: list[list[str]]) -> bytes:
    """A valid PDF with one text line per list item, Helvetica 11pt. Empty list = blank (scan-like) page."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    pages_id_placeholder = len(objs) + 1 + 2 * len(pages)  # Pages object is added last
    for lines in pages:
        ops = [b"BT /F1 11 Tf 14 TL 72 760 Td"]
        for line in lines:
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({esc}) Tj T*".encode("latin-1"))
        ops.append(b"ET")
        stream = b"\n".join(ops)
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (pages_id_placeholder, content, font)
            )
        )
    kids = b" ".join(b"%d 0 R" % i for i in page_ids)
    pages_id = add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids)))
    assert pages_id == pages_id_placeholder
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
    out.write(b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, xref))
    return out.getvalue()


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(paragraphs: list[str]) -> bytes:
    """Minimal DOCX. A paragraph equal to '<pagebreak>' inserts a page break."""
    body = []
    for p in paragraphs:
        if p == "<pagebreak>":
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        else:
            esc = p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            body.append(f"<w:p><w:r><w:t xml:space=\"preserve\">{esc}</w:t></w:r></w:p>")
    doc = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{_W}"><w:body>{"".join(body)}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        z.writestr("word/document.xml", doc)
    return buf.getvalue()
