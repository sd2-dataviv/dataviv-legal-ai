"""VLT-03..06: extraction, classification and chunking (no database)."""

import pytest

from app.modules.vault.chunker import chunk_pages
from app.modules.vault.classify import classify
from app.modules.vault.extract import Page, UnsupportedFile, extract, mime_for
from tests.factories import make_docx, make_pdf

AGREEMENT = """SHARE PURCHASE AGREEMENT

This Share Purchase Agreement is made on 1 March 2026 between Lotus Holdings Private Limited and Zenith LLP.

1. DEFINITIONS
1.1 "Completion" means completion of the sale and purchase of the Sale Shares.

2. SALE AND PURCHASE
2.1 The Seller shall sell and the Buyer shall purchase the Sale Shares free from all Encumbrances.
(a) the Sale Shares shall be transferred with all rights attached;
(b) the consideration shall be paid in cash.

12. TERMINATION
12.1 Either party may terminate this Agreement by giving thirty days written notice to the other party.
"""


def test_pdf_pages_and_ocr_flag() -> None:
    pdf = make_pdf(
        [["IN THE HIGH COURT OF DELHI AT NEW DELHI", "ORDER", "Bail is granted to the petitioner."], []]
    )
    pages = extract(pdf, mime_for("order.pdf"))
    assert [p.number for p in pages] == [1, 2]
    assert "HIGH COURT" in pages[0].text and not pages[0].needs_ocr
    assert pages[1].needs_ocr  # blank page = scan-like, waits for OCR


def test_docx_page_breaks() -> None:
    docx = make_docx(["LEGAL NOTICE", "First page text long enough.", "<pagebreak>", "Second page demand."])
    pages = extract(docx, mime_for("notice.docx"))
    assert len(pages) == 2 and pages[1].text == "Second page demand."
    assert not any(p.needs_ocr for p in pages)


def test_eml_headers_and_html_body() -> None:
    eml = (
        b"From: clerk@court.in\r\nSubject: Listing\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><style>p{}</style><p>Matter listed on <b>12 March</b> &amp; adjourned.</p></html>"
    )
    text = extract(eml, "message/rfc822")[0].text
    assert "Subject: Listing" in text and "12 March" in text and "&" in text and "<b>" not in text


def test_unsupported_and_bad_docx() -> None:
    with pytest.raises(UnsupportedFile):
        mime_for("setup.exe")
    with pytest.raises(UnsupportedFile):
        extract(b"not a zip", mime_for("x.docx"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("IN THE HIGH COURT OF DELHI\nCORAM: HON'BLE MR. JUSTICE A\nORDER\n12.03.2026", "order"),
        ("IN THE SUPREME COURT OF INDIA\nJUDGMENT\nThe appeal is dismissed.", "judgment"),
        ("BAIL PETITION UNDER SECTION 483 BNSS\nPetitioner: Vikram Kumar", "petition"),
        ("WRITTEN STATEMENT ON BEHALF OF DEFENDANT NO. 1", "written_statement"),
        ("AFFIDAVIT\nI, Ramesh Kumar, do hereby solemnly affirm", "affidavit"),
        ("LEGAL NOTICE\nUnder instructions from my client, take notice that", "notice"),
        (AGREEMENT, "agreement"),
        ("Dear Sir,\nPlease find the draft attached.", "correspondence"),
        ("Shopping list: rice, dal", "other"),
    ],
)
def test_classify(text: str, expected: str) -> None:
    assert classify(text) == expected


def test_chunks_follow_clause_structure_and_spans() -> None:
    pages = [Page(1, AGREEMENT.strip())]
    chunks = chunk_pages(pages, max_chars=300, min_chars=60)
    sections = [c.section_path for c in chunks]
    assert sections[0] == "SHARE PURCHASE AGREEMENT"
    assert "12. TERMINATION" in sections
    full = AGREEMENT.strip()
    for c in chunks:
        assert full[c.char_start : c.char_start + 15] == c.text[:15]
    termination = next(c for c in chunks if c.section_path == "12. TERMINATION")
    assert "thirty days" in termination.text


def test_long_paragraph_is_split_under_limit() -> None:
    long_para = " ".join(f"Sentence number {i} of the recitals is here." for i in range(200))
    chunks = chunk_pages([Page(1, long_para)], max_chars=500)
    assert len(chunks) > 5 and all(len(c.text) <= 520 for c in chunks)


def test_page_ranges_carry_through() -> None:
    pages = [
        Page(1, "1. Facts\nThe accused was arrested on 1 Jan."),
        Page(2, "2. Grounds\nThe arrest was illegal."),
    ]
    chunks = chunk_pages(pages, max_chars=1200, min_chars=10)
    assert {(c.page_from, c.page_to) for c in chunks} <= {(1, 1), (2, 2), (1, 2)}
    assert min(c.page_from for c in chunks) == 1 and max(c.page_to for c in chunks) == 2
