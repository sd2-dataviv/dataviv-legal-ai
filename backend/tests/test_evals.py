"""NFR-03: the evaluation harness scores correctly and runs end to end (offline model)."""

from collections.abc import Awaitable, Callable
from typing import Any

from httpx import AsyncClient

from app.modules.review.normalise import check, normalise_amount, normalise_date, normalise_value
from evals.run import load_suite, run_suites, to_markdown
from evals.scoring import contains, dates_in, score_case, score_cell
from tests.conftest import World

RunJobs = Callable[[], Awaitable[int]]


def test_review_normalisation() -> None:
    assert [
        normalise_date(d) for d in ("12.03.2026", "12th March, 2026", "March 12, 2026", "2026-03-12")
    ] == ["2026-03-12"] * 4
    assert [normalise_amount(a) for a in ("Rs. 5,00,000", "5 lakh", "INR 1.5 crore")] == [
        "500000",
        "500000",
        "15000000",
    ]
    assert normalise_value("choice", "arbitration at Delhi", "", ["Arbitration", "Courts"]) == (
        "Arbitration",
        None,
    )
    assert normalise_value("yes_no", "Yes.", "", []) == ("yes", None)
    passage = "Take notice that my client demands payment of Rs. 5,00,000 within 15 days."
    assert check("Rs. 5,00,000 in 15 days", "demands payment of Rs. 5,00,000 within 15 days", passage) == (
        True,
        None,
    )
    assert check("Rs. 7,00,000", "demands payment of Rs. 5,00,000", passage)[0] is False
    assert check("x", "pays nothing at all", passage)[0] is False


def test_scoring_rules() -> None:
    assert contains("Pay Rs 18,75,000", "18,75,000") and contains("pay 1875000 now", "18,75,000")
    assert not contains("Rs 10,00,000", "1 crore")
    assert dates_in("Listed on 15.04.2026.") == {"2026-04-15"}
    case = {
        "id": "x",
        "q": "q",
        "expect_all": ["18,75,000"],
        "forbid": ["1 crore"],
        "cite": "n.txt",
        "kind": "injection",
    }
    msg: dict[str, Any] = {
        "content": "Acme demands Rs. 18,75,000. Others claim Rs. 1 crore.",
        "model": "m",
        "sentences": [
            {"text": "Acme demands Rs. 18,75,000.", "cites": ["S1"], "label": "supported"},
            {"text": "Others claim Rs. 1 crore.", "cites": ["S1"], "label": "partial"},
        ],
        "sources": [{"key": "S1", "document_id": "d1"}],
    }
    r = score_case(case, msg, {"d1": "n.txt"})
    assert (
        r.correct
        and r.cited
        and r.injection_resisted is False
        and r.unflagged_fabrications == 1
        and not r.passed
    )
    assert (
        score_case({"id": "a", "q": "q", "kind": "abstain"}, {"content": "x", "model": None}, {}).abstained
        is True
    )
    assert score_cell("amount", ["240000"], {"status": "done", "value": "240000"}) == (True, "240000")
    assert score_cell("yes_no", ["no", None], {"status": "not_found", "value": None})[0] is True


async def test_harness_runs_end_to_end_offline(client: AsyncClient, world: World, run_jobs: RunJobs) -> None:
    report = await run_suites(client, world.partner_a.headers, ["assistant", "review"], run_jobs, limit=4)
    a, rv = report["suites"]["assistant"], report["suites"]["review"]
    assert a["summary"]["cases"] == 4 and a["summary"]["errors"] == 0
    assert {g["metric"] for g in a["summary"]["gates"]} == set(load_suite("assistant")["gates"])
    assert len(rv["cells"]) == 15 and rv["summary"]["verified_rate"] is not None
    assert report["backend"] == "fake"
    assert "# Evaluation report" in to_markdown(report)
