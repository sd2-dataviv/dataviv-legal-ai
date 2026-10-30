"""Minimal .docx writer (AST-09) using only the standard library: paragraphs, bold runs, a sources list.

python-docx is avoided because it depends on lxml, a compiled extension that Windows Smart App
Control can block (ADR-011)."""

import io
import zipfile
from xml.sax.saxutils import escape

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _run(text: str, *, bold: bool = False, size: int | None = None, colour: str | None = None) -> str:
    props = ""
    if bold or size or colour:
        props = "<w:rPr>" + ("<w:b/>" if bold else "") + (f'<w:color w:val="{colour}"/>' if colour else "")
        props += (f'<w:sz w:val="{size}"/>' if size else "") + "</w:rPr>"
    return f'<w:r>{props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _para(runs: str, *, space_after: int = 120) -> str:
    return f'<w:p><w:pPr><w:spacing w:after="{space_after}"/></w:pPr>{runs}</w:p>'


def build_docx(
    title: str,
    subtitle: str,
    blocks: list[list[tuple[str, str]]],
    sources: list[str],
    note: str,
    signatures: list[tuple[str, str, str]] | None = None,
) -> bytes:
    """blocks: paragraphs, each a list of (text, citation-suffix) runs. sources: "[1] Title, p. 3" lines.
    signatures: (signer label, signature anchor, date anchor) per signer; the anchors are written in white
    so they are invisible on paper but let the e-signature provider place its fields (Step 2e)."""
    body = [
        _para(_run(title, bold=True, size=32), space_after=60),
        _para(_run(subtitle, size=18, colour="5E6878")),
    ]
    for block in blocks:
        runs = "".join(
            _run(t + " ") + (_run(c + " ", size=16, colour="17784F") if c else "") for t, c in block
        )
        body.append(_para(runs))
    if sources:
        body.append(_para(_run("Sources", bold=True, size=24), space_after=60))
        body.extend(_para(_run(s, size=18), space_after=40) for s in sources)
    if signatures:
        body.append(_para(_run("Signatures", bold=True, size=24), space_after=120))
        for label, sign_anchor, date_anchor in signatures:
            body.append(_para(_run(label, bold=True, size=20), space_after=360))
            body.append(
                _para(
                    _run("Signature: ", size=20) + _run(sign_anchor, size=20, colour="FFFFFF"),
                    space_after=200,
                )
            )
            body.append(
                _para(_run("Date: ", size=20) + _run(date_anchor, size=20, colour="FFFFFF"), space_after=360)
            )
    if note:
        body.append(_para(_run(note, size=16, colour="5E6878")))
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + "".join(body)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'  # A4
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>'
        "</w:sectPr></w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", xml)
    return buf.getvalue()
