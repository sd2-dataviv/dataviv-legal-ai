"""Workflow definitions (WFL-01, WFL-06): a declarative JSON spec of typed inputs and typed steps.

    {"slug": "legal-notice", "name": "...", "description": "...",
     "inputs": [{"key": "recipient", "label": "Recipient", "type": "text", "required": true}],
     "steps":  [{"id": "facts", "type": "retrieve", "title": "Find the facts", "query": "{{inputs.demand}}"},
                {"id": "notice", "type": "llm", "task": "draft", "sources": ["facts"], "instructions": "..."},
                {"id": "sign_off", "type": "approval", "role": "matter_manager", "message": "..."},
                {"id": "deliver", "type": "output_document", "kind": "notice", "from_step": "notice",
                 "title": "Save the notice", "document_title": "Legal notice to {{inputs.recipient}}"}]}

`title` is the step's display name (plain text); every other parameter may use templates.
Templates use {{inputs.x}}, {{steps.<id>.<field>}}, {{matter.title}}, {{matter.client_name}} and {{today}}.
A parameter that is exactly one reference ("{{inputs.document}}") keeps its value's type (an id, a list).
Validation runs before a definition is saved and before every run, so a run never meets a bad reference.
"""

import json
import re
from datetime import date
from typing import Any

INPUT_TYPES = {"text", "textarea", "number", "choice", "document", "documents", "playbook"}
STEP_TYPES = {"retrieve", "extract", "review_table", "playbook_review", "llm", "approval", "output_document"}
ANSWER_TYPES = {"text", "yes_no", "date", "amount", "choice"}
DOCUMENT_KINDS = {"notice", "reply", "email", "memo", "other"}
REQUIRED: dict[str, tuple[str, ...]] = {
    "retrieve": ("query",),
    "extract": ("document", "fields"),
    "review_table": ("columns",),
    "playbook_review": ("document", "playbook"),
    "llm": ("instructions",),
    "approval": (),
    "output_document": ("document_title",),
}
_REF = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}")
_SLUG = re.compile(r"^[a-z][a-z0-9-]{2,60}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


class SpecError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return _REF.findall(value)
    if isinstance(value, list):
        return [r for v in value for r in _refs(v)]
    if isinstance(value, dict):
        return [r for v in value.values() for r in _refs(v)]
    return []


def validate(spec: dict[str, Any]) -> None:
    """Raise SpecError listing every problem (not just the first)."""
    p: list[str] = []
    if not isinstance(spec, dict):
        raise SpecError(["The definition must be a JSON object"])
    if not _SLUG.match(str(spec.get("slug", ""))):
        p.append("slug: 3-61 lower-case letters, digits or hyphens, starting with a letter")
    if not str(spec.get("name", "")).strip():
        p.append("name is required")
    inputs = spec.get("inputs", [])
    input_keys: set[str] = set()
    if not isinstance(inputs, list):
        p.append("inputs must be a list")
        inputs = []
    for i, inp in enumerate(inputs):
        key = str(inp.get("key", ""))
        if not _KEY.match(key):
            p.append(f"inputs[{i}].key {key!r} must be lower_snake_case")
        if key in input_keys:
            p.append(f"inputs[{i}].key {key!r} is repeated")
        input_keys.add(key)
        if inp.get("type") not in INPUT_TYPES:
            p.append(f"inputs[{i}].type must be one of {sorted(INPUT_TYPES)}")
        if inp.get("type") == "choice" and not inp.get("choices"):
            p.append(f"inputs[{i}] is a choice and needs choices")
    steps = spec.get("steps", [])
    if not isinstance(steps, list) or not steps:
        p.append("steps must be a non-empty list")
        steps = []
    seen: list[str] = []
    for i, st in enumerate(steps):
        sid, kind = str(st.get("id", "")), st.get("type")
        where = f"steps[{i}] ({sid or '?'})"
        if not _KEY.match(sid):
            p.append(f"{where}: id must be lower_snake_case")
        if sid in seen:
            p.append(f"{where}: id is repeated")
        if kind not in STEP_TYPES:
            p.append(f"{where}: type must be one of {sorted(STEP_TYPES)}")
        else:
            for field in REQUIRED[kind]:
                if field not in st:
                    p.append(f"{where}: {kind} steps need '{field}'")
        if kind == "extract":
            for j, f in enumerate(st.get("fields", [])):
                if not _KEY.match(str(f.get("key", ""))) or not f.get("question"):
                    p.append(f"{where}: fields[{j}] needs a lower_snake_case key and a question")
                if f.get("answer_type", "text") not in ANSWER_TYPES:
                    p.append(f"{where}: fields[{j}].answer_type must be one of {sorted(ANSWER_TYPES)}")
        if kind == "llm" and st.get("task", "draft") not in ("qa", "draft"):
            p.append(f"{where}: task must be qa or draft")
        if kind in ("llm", "output_document") and st.get("kind", "memo") not in DOCUMENT_KINDS:
            p.append(f"{where}: kind must be one of {sorted(DOCUMENT_KINDS)}")
        if kind == "approval" and st.get("role", "matter_manager") not in ("matter_manager", "firm_partner"):
            p.append(f"{where}: role must be matter_manager or firm_partner")
        for ref_list in ("sources", "context", "show"):
            for ref in st.get(ref_list, []) or []:
                if ref not in seen:
                    p.append(f"{where}: {ref_list} refers to {ref!r}, which is not an earlier step")
        if kind == "output_document" and st.get("from_step") and st["from_step"] not in seen:
            p.append(f"{where}: from_step {st['from_step']!r} is not an earlier step")
        for ref in _refs({k: v for k, v in st.items() if k not in ("id", "type")}):
            head, _, rest = ref.partition(".")
            if head == "inputs" and rest.split(".")[0] not in input_keys:
                p.append(f"{where}: {{{{{ref}}}}} names an input that does not exist")
            elif head == "steps" and rest.split(".")[0] not in seen:
                p.append(f"{where}: {{{{{ref}}}}} names a step that has not run yet")
            elif head not in ("inputs", "steps", "matter", "today"):
                p.append(f"{where}: {{{{{ref}}}}} is not a known reference")
        seen.append(sid)
    if p:
        raise SpecError(p)


def _lookup(path: str, ctx: dict[str, Any]) -> Any:
    if path == "today":
        return date.today().strftime("%d %B %Y")
    cur: Any = ctx
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list) and all(isinstance(v, dict) and "text" in v for v in value):
        return "\n".join(str(v["text"]) for v in value)
    return json.dumps(value, ensure_ascii=False)


def render(value: Any, ctx: dict[str, Any]) -> Any:
    """Resolve references in a step parameter (recursively through lists and objects)."""
    if isinstance(value, str):
        whole = _REF.fullmatch(value.strip())
        if whole:
            return _lookup(whole.group(1), ctx)
        return _REF.sub(lambda m: _as_text(_lookup(m.group(1), ctx)), value)
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    return value
