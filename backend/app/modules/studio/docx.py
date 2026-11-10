"""Word export for studio documents: a centred title, bold CAPITAL headings, indented sub-clauses and
highlighted [placeholders], then the drafting notes. Standard library only, like assistant.docx."""

import io
import re
import zipfile
from xml.sax.saxutils import escape

from app.modules.assistant.docx import _CT, _RELS

_PLACEHOLDER = re.compile(r"(\[[^\[\]\n]{1,120}\])")
_SUB = re.compile(r"^\d+\.\d+(\.\d+)*\.?\s")  # 1.1 / 2.3.1
_ITEM = re.compile(r"^\(([a-z]{1,3}|[ivx]{1,6})\)\s", re.I)  # (a) / (iv)


def _runs(text: str, *, bold: bool = False, size: int = 22, colour: str | None = None) -> str:
    out = []
    for part in _PLACEHOLDER.split(text):
        if not part:
            continue
        props = "<w:b/>" if bold else ""
        if colour:
            props += f'<w:color w:val="{colour}"/>'
        if _PLACEHOLDER.fullmatch(part):
            props += '<w:highlight w:val="yellow"/>'
        props += f'<w:sz w:val="{size}"/>'
        out.append(f'<w:r><w:rPr>{props}</w:rPr><w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
    return "".join(out)


def _para(
    runs: str, *, after: int = 120, indent: int = 0, align: str | None = None, keep: bool = False
) -> str:
    props = f'<w:spacing w:after="{after}"/>'
    if indent:
        props += f'<w:ind w:left="{indent}"/>'
    if align:
        props += f'<w:jc w:val="{align}"/>'
    if keep:
        props += "<w:keepNext/>"
    return f"<w:p><w:pPr>{props}</w:pPr>{runs}</w:p>"


def _is_heading(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return bool(letters) and len(line) <= 90 and all(c.isupper() for c in letters) and not line.endswith(",")


def build_studio_docx(content: str, notes: list[str], footer: str) -> bytes:
    lines = [ln.rstrip() for ln in content.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    body: list[str] = []
    if lines:
        body.append(_para(_runs(lines.pop(0).strip(), bold=True, size=28), after=240, align="center"))
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if _is_heading(line):
            body.append(_para(_runs(line, bold=True), after=120, keep=True))
        elif _SUB.match(line):
            body.append(_para(_runs(line), indent=567))
        elif _ITEM.match(line) or line.startswith(("- ", "• ")):
            body.append(_para(_runs(line), indent=1134))
        else:
            body.append(_para(_runs(line), align="both"))
    if notes:
        body.append(
            _para(
                _runs("Drafting notes (remove before sending)", bold=True, size=20, colour="8A5300"), after=60
            )
        )
        body.extend(_para(_runs(f"- {n}", size=18, colour="5E6878"), after=40, indent=284) for n in notes)
    if footer:
        body.append(_para(_runs(footer, size=16, colour="5E6878"), after=0))
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
