"""Turn a model answer into sentences with citations (AST-03). Pure functions, no I/O.

The model is told to end factual sentences with markers such as `[S1]` or `[S1][S3]`. This module
splits the answer into blocks (paragraphs and list items) and sentences, attaches each marker to
its sentence, strips the markers from the text, and flags markers that name no supplied source.
Sentence splitting knows the abbreviations common in Indian legal writing (Rs., No., Hon'ble Mr.,
s., v., Ltd., Pvt.) so "Rs. 5,00,000" or "A. Kumar" does not end a sentence.
"""

import re
from dataclasses import dataclass, field

_ABBREVIATIONS = {
    "rs",
    "no",
    "nos",
    "mr",
    "mrs",
    "ms",
    "dr",
    "sh",
    "smt",
    "hon'ble",
    "honble",
    "sec",
    "secs",
    "s",
    "ss",
    "art",
    "arts",
    "v",
    "vs",
    "ltd",
    "pvt",
    "co",
    "corp",
    "inc",
    "llp",
    "st",
    "viz",
    "etc",
    "cl",
    "para",
    "paras",
    "p",
    "pp",
    "sr",
    "jr",
    "govt",
    "dept",
    "e.g",
    "i.e",
    "u/s",
    "r/w",
    "w.e.f",
    "approx",
    "ord",
    "r",
    "o",
    "ch",
    "vol",
    "ed",
    "j",
    "jj",
    "cj",
    "addl",
    "asst",
    "spl",
    "misc",
}
_MARKER = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]|【\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*】")
_MARKERS_AFTER = re.compile(r"(?:\s*(?:\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]|【[^】]{1,40}】))*")
_LIST_PREFIX = re.compile(r"^\s*(?:[-*•]|\d{1,3}[.)]|\([a-z0-9]{1,3}\))\s+")
_HEADING = re.compile(r"^\s*#{1,6}\s+")


@dataclass
class Sentence:
    text: str
    cites: list[str] = field(default_factory=list)
    block: int = 0
    prefix: str = ""  # list marker of the block's first sentence, e.g. "1. " or "- "
    label: str = "uncited"
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "cites": self.cites,
            "block": self.block,
            "prefix": self.prefix,
            "label": self.label,
            "reason": self.reason,
        }


def _split(block: str) -> list[str]:
    out: list[str] = []
    start = 0
    for m in re.finditer(r"[.!?।]+", block):
        end = m.end()
        end += _MARKERS_AFTER.match(block, end).end() - end  # type: ignore[union-attr]
        rest = block[end:]
        if rest and not rest[0].isspace():
            continue  # "5.5", "i.e.", "S1.2"
        before = block[start : m.start()]
        word = re.search(r"([\w'/.]+)$", before)
        if m.group() == "." and word:
            w = word.group(1).lower().rstrip(".")
            if (
                w in _ABBREVIATIONS
                or (len(w) == 1 and w.isalpha())
                or (w.isdigit() and not before[: -len(w)].strip())
            ):
                continue  # abbreviation, initial ("A. Kumar") or clause number ("12. The applicant")
        if not before.strip():
            continue
        out.append(block[start:end].strip())
        start = end
    tail = block[start:].strip()
    if tail:
        if out and _MARKER.fullmatch(tail.replace(" ", "")):
            out[-1] += " " + tail  # stray markers after the last full stop
        else:
            out.append(tail)
    return out


def _keys(text: str) -> list[str]:
    keys: list[str] = []
    for m in _MARKER.finditer(text):
        for k in re.split(r"\s*[,;]\s*", (m.group(1) or m.group(2)).strip()):
            if k not in keys:
                keys.append(k)
    return keys


def _clean(text: str) -> str:
    text = _MARKER.sub("", text).replace("**", "").replace("__", "")
    text = re.sub(r"\s+([.,;:!?।])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def parse_answer(answer: str) -> list[Sentence]:
    """Answer text → sentences, in order, with citation keys and list/paragraph structure kept."""
    sentences: list[Sentence] = []
    block_no = -1
    for raw in re.split(r"\n\s*\n|\n(?=\s*(?:[-*•]|\d{1,3}[.)]|\([a-z0-9]{1,3}\)|#{1,6})\s)", answer.strip()):
        block = " ".join(line.strip() for line in raw.splitlines() if line.strip())
        if not block:
            continue
        block_no += 1
        prefix = ""
        if m := _LIST_PREFIX.match(block) or _HEADING.match(block):
            prefix = m.group().strip() + " " if not m.group().strip().startswith("#") else ""
            block = block[m.end() :]
        for i, part in enumerate(_split(block)):
            text = _clean(part)
            if not text:
                continue
            sentences.append(
                Sentence(text=text, cites=_keys(part), block=block_no, prefix=prefix if i == 0 else "")
            )
    return sentences


def strip_markers(answer: str) -> str:
    """Plain text without citation markers (for thread search, history and export)."""
    return "\n".join(_clean(line) for line in answer.splitlines())
