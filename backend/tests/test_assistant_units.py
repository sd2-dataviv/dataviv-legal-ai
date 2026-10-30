"""AST-03, AST-04, AST-09: citation parsing, sentence verification, retrieval terms, Word export."""

import io
import zipfile

from app.modules.assistant.citations import parse_answer, strip_markers
from app.modules.assistant.docx import build_docx
from app.modules.assistant.retrieval import query_terms
from app.modules.assistant.verify import verify

SOURCES = {
    "S1": "ORDER 12. The applicant is admitted to regular bail on furnishing a personal bond.",
    "S2": "CORAM: HON'BLE MR. JUSTICE A. KUMAR",
    "S3": "Take notice that my client demands payment of Rs. 5,00,000 within 15 days.",
}


def test_sentences_keep_abbreviations_lists_and_citations() -> None:
    s = parse_answer(
        "Bail was granted on a personal bond. [S1] The bench was Hon'ble Mr. Justice A. Kumar.[S2]\n\n"
        "1. Payment of Rs. 5,00,000 is demanded [S3].\n2. It is due within 15 days. [S3, S1]"
    )
    assert [x.text for x in s] == [
        "Bail was granted on a personal bond.",
        "The bench was Hon'ble Mr. Justice A. Kumar.",
        "Payment of Rs. 5,00,000 is demanded.",
        "It is due within 15 days.",
    ]
    assert [x.cites for x in s] == [["S1"], ["S2"], ["S3"], ["S3", "S1"]]
    assert [x.prefix for x in s] == ["", "", "1. ", "2. "]
    assert strip_markers("Granted. [S1][S2]") == "Granted."


def test_verifier_labels_supported_partial_unsupported_and_uncited() -> None:
    s = parse_answer(
        "The applicant is admitted to regular bail on a personal bond. [S1] "
        "The client demands Rs. 7,00,000 within 15 days. [S3] "
        "The weather in Delhi was pleasant and everyone celebrated. [S1] "
        "Bail is subject to strict reporting conditions and surrender of passport. [S1] "
        "The documents do not mention the hearing date. "
        "The respondent company has defaulted on several earlier loans. "
        "This cites a missing source. [S7]"
    )
    summary = verify(s, SOURCES, "ask")
    labels = [x.label for x in s]
    assert labels == [
        "supported",
        "unsupported",
        "unsupported",
        "unsupported",
        "uncited",
        "unsupported",
        "unsupported",
    ]
    assert "700000 not found" in s[1].reason and "no citation" in s[5].reason and "S7" in s[6].reason
    assert summary["supported"] == 1 and summary["uncited"] == 1


def test_draft_boilerplate_is_neutral_but_figures_still_checked() -> None:
    s = parse_answer(
        "Kindly comply within the stipulated period failing which proceedings will follow. "
        "My client demands Rs. 5,00,000 within 15 days. [S3]"
    )
    verify(s, SOURCES, "draft")
    assert [x.label for x in s] == ["uncited", "supported"]


def test_query_terms_drop_question_words_and_keep_clause_numbers_and_hindi() -> None:
    assert query_terms("What does clause 12.1 say about the notice period?") == [
        "clause",
        "12.1",
        "notice",
        "period",
    ]
    assert "जमानत" in query_terms("जमानत की शर्तें क्या हैं")


def test_docx_export_is_a_valid_word_package() -> None:
    data = build_docx(
        "Answer: Bail",
        "Question: bail?",
        [[("Bail was granted.", "[1]")]],
        ["[1] Order.pdf, p. 3"],
        "Check citations.",
    )
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(z.namelist())
        xml = z.read("word/document.xml").decode()
    assert "Bail was granted." in xml and "[1] Order.pdf, p. 3" in xml
