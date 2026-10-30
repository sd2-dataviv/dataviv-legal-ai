"""Built-in workflow definitions (WFL-02) and sample playbooks a firm can copy and adapt (WFL-07)."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.modules.workflows.spec import validate

BUILTIN_DIR = Path(__file__).resolve().parent / "builtins"


@lru_cache
def builtins() -> dict[str, dict[str, Any]]:
    """slug -> spec. Every built-in is validated on load, so a broken one fails the tests, not a run."""
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(BUILTIN_DIR.glob("*.json")):
        spec = json.loads(f.read_text(encoding="utf-8"))
        validate(spec)
        out[spec["slug"]] = spec
    return out


def _clause(
    key: str, title: str, question: str, preferred: str, fallback: str, unacceptable: str, guidance: str = ""
) -> dict[str, str]:
    return {
        "key": key,
        "title": title,
        "question": question,
        "preferred": preferred,
        "fallback": fallback,
        "unacceptable": unacceptable,
        "guidance": guidance,
    }


PLAYBOOK_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "spa-buyer",
        "name": "Share purchase agreement - acting for the buyer",
        "contract_type": "share purchase agreement",
        "description": "Standard buyer-side positions for Indian private company acquisitions.",
        "clauses": [
            _clause(
                "termination",
                "Termination",
                "How can the agreement be terminated, and with how much notice?",
                "Termination only for material breach not cured within 30 days; no termination for convenience",
                "Either party may terminate on at least 30 days' written notice",
                "The seller may terminate at will or on less than 30 days' notice",
            ),
            _clause(
                "liability_cap",
                "Liability cap",
                "What is the cap on the seller's liability?",
                "A cap of at least 100% of the consideration",
                "A cap of at least 20% of the consideration",
                "A cap below 20% of the consideration, or no liability for breach of warranties",
                "Compare the cap with the consideration stated in the agreement.",
            ),
            _clause(
                "governing_law",
                "Governing law",
                "Which law governs the agreement?",
                "The laws of India",
                "The laws of India with a foreign-law carve-out for specific instruments",
                "Any foreign law",
            ),
            _clause(
                "disputes",
                "Dispute resolution",
                "How are disputes resolved, and where is the seat?",
                "Arbitration under the Arbitration and Conciliation Act, 1996, seated in New Delhi or Mumbai",
                "Arbitration seated elsewhere in India",
                "Foreign-seated arbitration, or courts outside India",
            ),
            _clause(
                "confidentiality",
                "Confidentiality",
                "How long do the confidentiality obligations last?",
                "At least five years after completion",
                "At least three years",
                "Less than three years, or none",
            ),
            _clause(
                "stamp_duty",
                "Stamp duty",
                "Who bears the stamp duty?",
                "The seller bears the stamp duty",
                "Stamp duty is shared equally",
                "The buyer bears all stamp duty",
            ),
        ],
    },
    {
        "id": "lease-lessee",
        "name": "Commercial lease - acting for the lessee",
        "contract_type": "lease",
        "description": "Lessee-side positions for office leases in India.",
        "clauses": [
            _clause(
                "lock_in",
                "Lock-in",
                "How long is the lock-in period?",
                "Lock-in of 12 months or less",
                "Lock-in of up to 24 months",
                "Lock-in longer than 24 months",
            ),
            _clause(
                "escalation",
                "Rent escalation",
                "By how much does the rent increase, and how often?",
                "At most 5% a year",
                "At most 15% every three years",
                "More than 5% a year",
            ),
            _clause(
                "deposit",
                "Security deposit",
                "How much is the security deposit?",
                "At most 3 months' rent",
                "At most 6 months' rent",
                "More than 6 months' rent",
                "Compare the deposit with the monthly rent.",
            ),
            _clause(
                "exit",
                "Exit after lock-in",
                "How much notice is needed to terminate after the lock-in?",
                "Three months or less",
                "Up to six months",
                "More than six months, or no right to exit",
            ),
            _clause(
                "jurisdiction",
                "Jurisdiction",
                "Which courts have jurisdiction?",
                "Courts where the premises are",
                "Courts at the lessor's registered office in India",
                "Courts outside India",
            ),
        ],
    },
]
