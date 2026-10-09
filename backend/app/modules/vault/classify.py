"""Rule-based legal document classification (VLT-06).

Looks at the first ~2,000 characters, where Indian pleadings and orders put their cause title. Rules are
ordered: more specific types first. Model-based classification replaces this in Step 2 behind the same
function, and these rules stay as the fallback.
"""

import re

DOC_TYPES = (
    "petition",
    "written_statement",
    "order",
    "judgment",
    "agreement",
    "notice",
    "affidavit",
    "correspondence",
    "other",
)

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("affidavit", re.compile(r"\baffidavit\b|\bsolemnly affirm|\bdeponent\b", re.I)),
    ("written_statement", re.compile(r"\bwritten statement\b|\breply on behalf of (the )?respondent", re.I)),
    ("judgment", re.compile(r"\bjudgment\b|\bjudgement\b|\bj\s*u\s*d\s*g\s*m\s*e\s*n\s*t\b", re.I)),
    ("order", re.compile(r"\border\b.{0,40}\b(dated|passed)\b|^\s*order\s*$|\bcoram\b|\bpresent\s*:", re.I | re.M)),
    (
        "petition",
        re.compile(
            r"\b(writ|civil|criminal|special leave|bail|transfer|review|revision)\s+petition\b"
            r"|\bpetition (under|u/s)\b|\bpetitioner\b|\bplaint\b|\bsuit no\b",
            re.I,
        ),
    ),
    ("notice", re.compile(r"\blegal notice\b|\bnotice under section\b|\btake notice\b|\bnotice is hereby\b", re.I)),
    (
        "agreement",
        re.compile(
            r"\b(agreement|deed|memorandum of understanding|mou|contract)\b.{0,200}"
            r"\b(between|entered into|made on|executed)\b",
            re.I | re.S,
        ),
    ),
    ("correspondence", re.compile(r"^\s*(from|to|subject|dear)\s*:?\s", re.I | re.M)),
]


def classify(text: str) -> str:
    head = text[:2000]
    for doc_type, pattern in _RULES:
        if pattern.search(head):
            return doc_type
    return "other"
