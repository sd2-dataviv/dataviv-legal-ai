"""Agent definitions (AGT-01): instructions, allowed tools, approval policy and budgets, as JSON.

    {"slug": "matter-researcher", "name": "...", "description": "...", "instructions": "...",
     "tools": ["search_documents", "read_passage"],          # final_answer is always available
     "approval": {"create_draft": "confirm"},                  # write tools: auto | confirm | manager
     "budget": {"max_steps": 8, "max_cost_usd": 0.03},
     "subagent": "document-analyst"}                           # needed with delegate_to_subagents

Approval policy for write tools (read tools always run freely):
  auto     the action runs at once (recorded in the trace and the audit log)
  confirm  anyone who can edit the matter confirms it, including the person who started the run
  manager  the matter lead or a partner on the matter, never the person who started the run
A write tool with no policy given defaults to "confirm".
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

READ_TOOLS = frozenset(
    {
        "recall_memory",
        "list_documents",
        "search_documents",
        "read_passage",
        "read_document",
        "delegate_to_subagents",
    }
)
WRITE_TOOLS = frozenset({"remember_fact", "create_draft"})
TOOLS = READ_TOOLS | WRITE_TOOLS
POLICIES = ("auto", "confirm", "manager")
MAX_STEPS = 30
MAX_COST_USD = 1.0
_SLUG = re.compile(r"^[a-z][a-z0-9-]{2,60}$")
BUILTIN_DIR = Path(__file__).resolve().parent / "builtins"


class SpecError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def validate(spec: dict[str, Any], known: dict[str, dict[str, Any]] | None = None) -> None:
    """Raise SpecError listing every problem. `known` = definitions a subagent may name."""
    p: list[str] = []
    if not isinstance(spec, dict):
        raise SpecError(["The definition must be a JSON object"])
    if not _SLUG.match(str(spec.get("slug", ""))):
        p.append("slug: 3-61 lower-case letters, digits or hyphens, starting with a letter")
    if not str(spec.get("name", "")).strip():
        p.append("name is required")
    if len(str(spec.get("instructions", "")).strip()) < 20:
        p.append("instructions: say what the agent should do (at least 20 characters)")
    tools = spec.get("tools", [])
    if not isinstance(tools, list) or not tools:
        p.append("tools must be a non-empty list")
        tools = []
    for t in tools:
        if t not in TOOLS:
            p.append(f"tools: {t!r} is not a tool; choose from {sorted(TOOLS)}")
    if len(set(tools)) != len(tools):
        p.append("tools: each tool once")
    approval = spec.get("approval", {}) or {}
    if not isinstance(approval, dict):
        p.append("approval must be an object of tool: policy")
        approval = {}
    for tool, policy in approval.items():
        if tool not in WRITE_TOOLS:
            p.append(f"approval: {tool!r} is not a write tool (read tools never need approval)")
        elif policy not in POLICIES:
            p.append(f"approval: {tool} must be one of {list(POLICIES)}")
    budget = spec.get("budget", {}) or {}
    steps, cost = budget.get("max_steps", 10), budget.get("max_cost_usd", 0.05)
    if not isinstance(steps, int) or not 1 <= steps <= MAX_STEPS:
        p.append(f"budget.max_steps must be a whole number from 1 to {MAX_STEPS}")
    if not isinstance(cost, (int, float)) or not 0 < cost <= MAX_COST_USD:
        p.append(f"budget.max_cost_usd must be more than 0 and at most {MAX_COST_USD}")
    if "delegate_to_subagents" in tools:
        sub = spec.get("subagent")
        target = (known or builtins()).get(str(sub)) if sub else None
        if target is None:
            p.append("subagent: name the agent that works on each document (e.g. document-analyst)")
        elif "delegate_to_subagents" in target.get("tools", []):
            p.append("subagent: a subagent cannot delegate further")
        elif set(target.get("tools", [])) & WRITE_TOOLS:
            p.append("subagent: subagents are read-only; choose one without write tools")
    if p:
        raise SpecError(p)


def policy(spec: dict[str, Any], tool: str) -> str:
    if tool not in WRITE_TOOLS:
        return "auto"
    return str((spec.get("approval") or {}).get(tool, "confirm"))


def budget(spec: dict[str, Any]) -> tuple[int, float]:
    b = spec.get("budget") or {}
    return int(b.get("max_steps", 10)), float(b.get("max_cost_usd", 0.05))


@lru_cache
def builtins() -> dict[str, dict[str, Any]]:
    """slug -> spec, validated on load (subagents first, so orchestrators can name them)."""
    specs = {}
    for f in sorted(BUILTIN_DIR.glob("*.json")):
        spec = json.loads(f.read_text(encoding="utf-8"))
        specs[spec["slug"]] = spec
    for spec in specs.values():
        validate(spec, specs)
    return specs
