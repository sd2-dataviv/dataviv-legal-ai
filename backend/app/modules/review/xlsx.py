"""Minimal .xlsx writer using only the standard library (VLT-09 export).

openpyxl would work too (it is pure Python), but this keeps the dependency list short and is all a
review table needs: several sheets, a bold frozen header row, column widths, wrapped text, filters.
"""

import io
import re
import zipfile
from xml.sax.saxutils import escape

_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
{sheets}
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF142640"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
</cellXfs>
</styleSheet>"""


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref: str, value: str, style: int) -> str:
    text = _ILLEGAL.sub("", value)[:32767]
    return f'<c r="{ref}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _sheet(rows: list[list[str]], widths: list[int]) -> str:
    cols = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths)
    )
    body = []
    for r, row in enumerate(rows):
        cells = "".join(_cell(f"{_col(c)}{r + 1}", str(v), 1 if r == 0 else 2) for c, v in enumerate(row))
        body.append(f'<row r="{r + 1}">{cells}</row>')
    last = _col(max(len(rows[0]) - 1, 0)) if rows else "A"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        f"<cols>{cols}</cols><sheetData>{''.join(body)}</sheetData>"
        f'<autoFilter ref="A1:{last}{max(len(rows), 1)}"/></worksheet>'
    )


def build_xlsx(sheets: list[tuple[str, list[list[str]], list[int]]]) -> bytes:
    """sheets: (name, rows with the header first, column widths in characters)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(len(sheets))
        )
        z.writestr("[Content_Types].xml", _CT.format(sheets=overrides))
        z.writestr("_rels/.rels", _RELS)
        names = "".join(
            f'<sheet name="{escape(re.sub(r"[][:*?/\\\\]", " ", name)[:31])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
            for i, (name, _, _) in enumerate(sheets)
        )
        z.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{names}</sheets></workbook>",
        )
        rels = "".join(
            f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(sheets))
        )
        rels += (
            f'<Relationship Id="rId{len(sheets) + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
        )
        z.writestr("xl/styles.xml", _STYLES)
        for i, (_, rows, widths) in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet(rows, widths))
    return buf.getvalue()
