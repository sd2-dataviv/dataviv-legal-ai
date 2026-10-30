"""Normalise and check one review-table answer (VLT-09). Pure functions, no I/O.

- Dates become ISO (2026-03-12). Indian order is assumed for numeric dates: 12/03/2026 is 12 March.
- Amounts become rupees as a plain number: "Rs. 5,00,000", "5 lakh" and "INR 1.5 crore" all work.
- Yes/no and choices are matched case-insensitively.
- A quote counts only if it appears in the cited passage word for word (ignoring case, spacing,
  curly quotes and dashes); every figure in the answer must also appear in that passage.
"""

import re
import unicodedata
from datetime import date

from app.modules.assistant.verify import figures

_MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for m in names
}
_ORD = r"(?:st|nd|rd|th)?"


def _iso(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def normalise_date(text: str) -> str | None:
    t = text.strip().lower().replace(",", " ")
    if m := re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):
        return _iso(int(m[1]), int(m[2]), int(m[3]))
    if m := re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", t):  # DD/MM/YYYY (Indian order)
        return _iso(int(m[3]), int(m[2]), int(m[1]))
    if m := re.search(rf"\b(\d{{1,2}}){_ORD}\s+(?:day\s+of\s+)?([a-z]+)\.?\s+(\d{{4}})\b", t):
        if m[2] in _MONTHS:
            return _iso(int(m[3]), _MONTHS[m[2]], int(m[1]))
    if m := re.search(rf"\b([a-z]+)\.?\s+(\d{{1,2}}){_ORD}\s+(\d{{4}})\b", t):
        if m[1] in _MONTHS:
            return _iso(int(m[3]), _MONTHS[m[1]], int(m[2]))
    return None


def normalise_amount(text: str) -> str | None:
    t = text.lower().replace("\u20b9", " rs ").replace("inr", " rs ")
    number = r"(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|lacs?|crores?|cr\b|thousand|million)?"
    # Prefer the figure after a currency marker, so "Clause 3.1: Rs. 2,40,000" gives 240000, not 3.1
    m = re.search(r"\b(?:rs\.?|rupees)\s*" + number, t) or re.search(number, t)
    if not m:
        return None
    n = float(m[1].replace(",", ""))
    scale = {
        "lakh": 1e5,
        "lakhs": 1e5,
        "lac": 1e5,
        "lacs": 1e5,
        "crore": 1e7,
        "crores": 1e7,
        "cr": 1e7,
        "thousand": 1e3,
        "million": 1e6,
    }.get(m[2] or "", 1)
    v = n * scale
    return str(int(v)) if v == int(v) else f"{v:.2f}"


def normalise_value(
    answer_type: str, raw: str | None, answer: str, choices: list[str]
) -> tuple[str | None, str | None]:
    """→ (normalised value, flag explaining why it could not be normalised)."""
    source = (raw or "").strip() or answer
    if answer_type == "text":
        return (raw or answer).strip() or None, None
    if answer_type == "date":
        v = normalise_date(source) or normalise_date(answer)
        return v, None if v else "Not a recognisable date"
    if answer_type == "amount":
        v = normalise_amount(source) or normalise_amount(answer)
        return v, None if v else "Not a recognisable amount"
    if answer_type == "yes_no":
        low = source.strip().lower()
        if low.startswith(("yes", "true", "y")):
            return "yes", None
        if low.startswith(("no", "false", "n")):
            return "no", None
        return None, "Not a clear yes or no"
    if answer_type == "choice":
        low = source.strip().lower()
        for c in choices:
            if low == c.lower() or c.lower() in low:
                return c, None
        return None, "Not one of the listed choices"
    return source or None, None


# Dashes become hyphens; quote marks are dropped, so a model wrapping its quote in quotes is not penalised
_PUNCT = str.maketrans(
    {
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": None,
        "\u2019": None,
        "\u201c": None,
        "\u201d": None,
        '"': None,
        "'": None,
    }
)


def _flat(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).casefold()
    t = t.translate(_PUNCT)
    return re.sub(r"\s+", " ", t).strip()


def quote_in(quote: str, passage: str) -> bool:
    """The quote, or each part of it around "...", appears in the passage."""
    parts = [p for p in re.split(r"\s*(?:\.\.\.|\u2026)\s*", _flat(quote)) if len(p) >= 4]
    flat = _flat(passage)
    return bool(parts) and all(p in flat for p in parts)


def check(answer: str, quote: str, passage: str) -> tuple[bool, str | None]:
    """Is the answer backed by its quote and passage? → (verified, flag)."""
    if not quote.strip():
        return False, "No supporting quote was given"
    if not quote_in(quote, passage):
        return False, "The quote does not appear word for word in the cited passage"
    have = set(figures(passage))
    missing = [f for f in figures(answer) if f not in have]
    if missing:
        return False, f"{', '.join(missing[:3])} not found in the cited passage"
    return True, None
