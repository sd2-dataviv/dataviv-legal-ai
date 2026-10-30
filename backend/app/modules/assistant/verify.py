"""Sentence-level verification of a cited answer (AST-04, ADR-013). Pure functions, no I/O.

The default verifier is lexical and deterministic, so it costs no model tokens (Groq's developer
tier allows about 8K tokens a minute) and can never be argued with by a prompt:

- every figure in the sentence (amounts, dates, section and clause numbers) must appear in a cited
  source, otherwise the sentence is *unsupported*;
- otherwise the share of the sentence's content words found in its cited sources decides:
  ≥ 60 % *supported*, ≥ 35 % *partial*, below that *unsupported*;
- a sentence with no citation is *uncited*. In Ask and Summarise modes a substantive uncited
  sentence counts as *unsupported*; short framing sentences ("The documents do not say…") and
  drafting boilerplate in Draft mode stay neutral.

An optional model verifier (settings.assistant_verifier = "model") can refine *cited* sentences;
it can downgrade a label but never upgrade one that failed the figures check.
"""

import re
from collections import Counter

from app.modules.assistant.citations import Sentence

_STOP = set(
    """a an the and or but if then than that this these those there their they them it its is are was
    were be been being have has had do does did of to in on at by for from with without within into onto
    as per under over upon about after before between during against such any all each every either
    neither not no nor so very can could may might must shall should will would also which who whom
    whose what when where why how i we you he she his her our your my me us one two may also only same
    other another more most less least some said says say stated states state mentioned provides provided
    document documents source sources according page pages""".split()
)
_NO_ANSWER = re.compile(
    r"\b(do(es)? not|don't|doesn't|did not|cannot|can't|couldn't|could not|unable to|no (information|mention))\b"
    r".{0,60}\b(mention|contain|state|specify|say|cover|find|found|address|include|provide|disclose)",
    re.I,
)
_NUMBER = re.compile(r"(?<![\w/])(?:\d[\d,]*(?:\.\d+)?)(?![\w/])")
_WORD = re.compile(r"[a-z\u0900-\u097f][a-z\u0900-\u097f'\-]+", re.I)


def _stem(word: str) -> str:
    w = word.lower().strip("'-")
    for suffix in (
        "ations",
        "ation",
        "ments",
        "ment",
        "ings",
        "ing",
        "edly",
        "ies",
        "ied",
        "ed",
        "es",
        "ly",
        "s",
    ):
        if len(w) - len(suffix) >= 4 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def content_words(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(text) if w.lower() not in _STOP and len(w) > 2]


def figures(text: str) -> list[str]:
    """Numbers normalised so "5,00,000" == "500000" and "12.1" stays "12.1"."""
    out = []
    for n in _NUMBER.findall(text):
        v = n.replace(",", "").rstrip(".")
        if v:
            out.append(v)
    return out


def _near(word: str, source_words: set[str]) -> bool:
    """Prefix match for longer words, so "terminat" (terminate) meets "termin" (termination)."""
    if len(word) < 5:
        return False
    return any(len(s) >= 5 and (s.startswith(word[:5]) and word.startswith(s[:5])) for s in source_words)


def _check(sentence: Sentence, cited_text: str) -> tuple[str, str]:
    source_figures = set(figures(cited_text))
    missing = [f for f in figures(sentence.text) if f not in source_figures]
    if missing:
        return "unsupported", f"{', '.join(missing[:3])} not found in the cited source"
    words = content_words(sentence.text)
    if not words:
        return "supported", ""
    source_words = set(content_words(cited_text))
    found = sum(1 for w in words if w in source_words or _near(w, source_words))
    share = found / len(words)
    if share >= 0.6:
        return "supported", ""
    if share >= 0.35:
        return "partial", f"only {round(share * 100)} % of its terms appear in the cited source"
    return "unsupported", "the cited source does not say this"


def verify(sentences: list[Sentence], sources: dict[str, str], mode: str) -> dict[str, object]:
    """Label every sentence in place; return the summary stored on the message."""
    for s in sentences:
        invalid = [k for k in s.cites if k not in sources]
        valid = [k for k in s.cites if k in sources]
        s.cites = valid
        if invalid and not valid:
            s.label, s.reason = "unsupported", f"cites {', '.join(invalid)}, which was not supplied"
            continue
        if not valid:
            framing = len(content_words(s.text)) < 4 or bool(_NO_ANSWER.search(s.text))
            if mode == "draft" or framing:
                s.label, s.reason = "uncited", ""
            else:
                s.label, s.reason = "unsupported", "no citation"
            continue
        s.label, s.reason = _check(s, "\n".join(sources[k] for k in valid))
        if invalid:
            s.reason = (
                s.reason + "; " if s.reason else ""
            ) + f"also cited {', '.join(invalid)}, which was not supplied"
    counts = Counter(s.label for s in sentences)
    return {
        "verifier": "lexical",
        "supported": counts["supported"],
        "partial": counts["partial"],
        "unsupported": counts["unsupported"],
        "uncited": counts["uncited"],
    }


RANK = {"supported": 0, "partial": 1, "unsupported": 2}


def merge_model_labels(sentences: list[Sentence], model_labels: dict[int, tuple[str, str]]) -> None:
    """Apply model verdicts to cited sentences; only ever make a label stricter."""
    for i, s in enumerate(sentences):
        if i in model_labels and s.label in RANK:
            label, reason = model_labels[i]
            if label in RANK and RANK[label] > RANK[s.label]:
                s.label, s.reason = label, reason or s.reason
