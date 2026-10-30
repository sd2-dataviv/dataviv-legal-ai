"""Ready-made column sets for common Indian legal reviews. A firm can edit them per table."""

from typing import TypedDict


class ColumnSpec(TypedDict):
    label: str
    question: str
    answer_type: str
    choices: list[str]


def _c(label: str, question: str, answer_type: str = "text", choices: list[str] | None = None) -> ColumnSpec:
    return {"label": label, "question": question, "answer_type": answer_type, "choices": choices or []}


TEMPLATES: dict[str, dict[str, object]] = {
    "contracts": {
        "title": "Contract review",
        "description": "Key commercial and legal terms of agreements",
        "columns": [
            _c("Parties", "Who are the parties to this agreement?"),
            _c("Date", "On what date was the agreement executed or does it take effect?", "date"),
            _c("Term", "What is the term or duration of the agreement?"),
            _c("Termination", "How can a party terminate, and how much notice is required?"),
            _c("Governing law", "Which law governs the agreement?"),
            _c(
                "Disputes",
                "How are disputes to be resolved?",
                "choice",
                ["Arbitration", "Courts", "Mediation"],
            ),
            _c("Liability cap", "What is the maximum amount of liability under the agreement?", "amount"),
            _c("Stamp duty", "Does the document say that stamp duty has been paid?", "yes_no"),
        ],
    },
    "litigation": {
        "title": "Litigation file",
        "description": "Court, parties, orders and dates across pleadings and orders",
        "columns": [
            _c("Date", "What is the date of this document or order?", "date"),
            _c("Court", "Which court or tribunal is this before?"),
            _c("Case number", "What is the case number or CNR?"),
            _c("Parties", "Who are the parties (petitioner/applicant and respondent)?"),
            _c("Relief", "What relief is sought, or what did the court order?"),
            _c("Next date", "When is the next date of hearing, if any?", "date"),
        ],
    },
    "notices": {
        "title": "Notices and correspondence",
        "description": "Who demands what, by when",
        "columns": [
            _c("From", "Who sent this notice or letter, and on whose behalf?"),
            _c("To", "To whom is it addressed?"),
            _c("Date", "What is the date of the notice or letter?", "date"),
            _c("Demand", "What does it demand or ask for?"),
            _c("Amount", "What amount of money is claimed or mentioned?", "amount"),
            _c("Time to comply", "How long does the recipient have to comply?"),
        ],
    },
}
