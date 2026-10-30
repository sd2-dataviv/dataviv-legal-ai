"""VLT-09, VLT-10, NFR-09: review tables end to end with the offline model."""

import csv
import io
import zipfile
from collections.abc import Awaitable, Callable
from typing import Any

from httpx import AsyncClient

from tests.conftest import World
from tests.test_drafts_api import team
from tests.test_vault_api import NOTICE_DOCX, ORDER_PDF, SPA_TXT, upload

RunJobs = Callable[[], Awaitable[int]]

LEASE_TXT = (
    b"LEASE DEED\n\nThis Lease Deed is executed at Gurugram on 25 March 2026 between Mr. Suresh Iyer and Brightpath "
    b"Learning Private Limited.\n\n3. RENT\n3.1 The Lessee shall pay a monthly rent of Rs. 2,40,000.\n\n"
    b"5. JURISDICTION\n5.1 The courts at Gurugram shall have exclusive jurisdiction.\n"
)


async def matter_docs(
    client: AsyncClient, world: World, matter: dict[str, Any], run_jobs: RunJobs
) -> tuple[str, dict[str, str]]:
    mid = await team(client, world, matter)
    res = await upload(
        client,
        mid,
        world.partner_a.headers,
        ("order.pdf", ORDER_PDF),
        ("notice.docx", NOTICE_DOCX),
        ("spa.txt", SPA_TXT),
        ("lease.txt", LEASE_TXT),
    )
    await run_jobs()
    return mid, {f["filename"]: f["document_id"] for f in res["files"]}


def cell(grid: dict[str, Any], doc: str, label: str) -> dict[str, Any]:
    col = next(c for c in grid["column_list"] if c["label"] == label)
    return next(c for c in grid["cells"] if c["document_id"] == doc and c["column_id"] == col["id"])


async def test_build_run_and_read_a_review_table(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, docs = await matter_docs(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    templates = (await client.get("/v1/review-templates", headers=h)).json()
    assert {t["id"] for t in templates} == {"contracts", "litigation", "notices"}

    r = await client.post(
        f"/v1/matters/{mid}/review-tables",
        json={
            "title": "Agreements",
            "columns": [
                {"label": "Rent", "question": "What is the monthly rent payable?", "answer_type": "amount"},
                {"label": "Termination", "question": "How can a party terminate, with how much notice?"},
                {
                    "label": "Jurisdiction",
                    "question": "Which courts have jurisdiction?",
                    "answer_type": "choice",
                    "choices": ["Gurugram", "Delhi"],
                },
            ],
            "document_ids": [docs["spa.txt"], docs["lease.txt"]],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["columns"] == 3 and t["documents"] == 2 and t["can_edit"] is True

    run = await client.post(f"/v1/review-tables/{t['id']}/run", json={}, headers=h)
    assert run.status_code == 202 and run.json()["cells_queued"] == 6 and run.json()["documents"] == 2
    again = await client.post(f"/v1/review-tables/{t['id']}/run", json={}, headers=h)
    assert again.status_code == 409  # one run at a time
    await run_jobs()

    grid = (await client.get(f"/v1/review-tables/{t['id']}", headers=h)).json()
    assert grid["job"]["status"] == "succeeded" and grid["job"]["done"] == 2 and grid["totals"]["queued"] == 0
    rent = cell(grid, docs["lease.txt"], "Rent")
    assert rent["status"] == "done" and rent["value"] == "240000" and rent["verified"] is True
    assert rent["quote"] and "2,40,000" in rent["quote"] and rent["page_from"] == 1 and rent["chunk_id"]
    term = cell(grid, docs["spa.txt"], "Termination")
    assert term["status"] == "done" and "thirty days" in term["answer"].lower() and term["verified"] is True
    assert cell(grid, docs["lease.txt"], "Jurisdiction")["value"] == "Gurugram"
    assert cell(grid, docs["spa.txt"], "Rent")["status"] in ("not_found", "done")

    # nothing left to do for "missing"; "all" re-reads everything
    assert (await client.post(f"/v1/review-tables/{t['id']}/run", json={}, headers=h)).json()[
        "cells_queued"
    ] == 0
    listing = (await client.get(f"/v1/matters/{mid}/review-tables", headers=h)).json()
    assert listing[0]["id"] == t["id"] and listing[0]["job"]["status"] == "succeeded"


async def test_corrections_survive_reruns_and_reviews_are_recorded(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, docs = await matter_docs(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    t = (
        await client.post(
            f"/v1/matters/{mid}/review-tables",
            json={
                "columns": [
                    {
                        "label": "Rent",
                        "question": "What is the monthly rent payable?",
                        "answer_type": "amount",
                    }
                ],
                "document_ids": [docs["lease.txt"]],
            },
            headers=h,
        )
    ).json()
    await client.post(f"/v1/review-tables/{t['id']}/run", json={}, headers=h)
    await run_jobs()
    c = cell((await client.get(f"/v1/review-tables/{t['id']}", headers=h)).json(), docs["lease.txt"], "Rent")

    r = await client.patch(
        f"/v1/review-tables/{t['id']}/cells/{c['id']}",
        json={"answer": "Rs. 2,50,000 per month", "reviewed": True},
        headers=h,
    )
    fixed = cell(r.json(), docs["lease.txt"], "Rent")
    assert fixed["answer"] == "Rs. 2,50,000 per month" and fixed["value"] == "250000"
    assert fixed["edited_by"]["id"] == str(world.associate_a.id) and fixed["reviewed_by"]["id"] == str(
        world.associate_a.id
    )
    assert "2,40,000" in fixed["model_answer"]

    await client.post(f"/v1/review-tables/{t['id']}/run", json={"scope": "all"}, headers=h)
    await run_jobs()
    after = cell(
        (await client.get(f"/v1/review-tables/{t['id']}", headers=h)).json(), docs["lease.txt"], "Rent"
    )
    assert after["answer"] == "Rs. 2,50,000 per month" and "correction" in after["flag"]

    r = await client.patch(f"/v1/review-tables/{t['id']}/cells/{c['id']}", json={"revert": True}, headers=h)
    assert cell(r.json(), docs["lease.txt"], "Rent")["answer"] == after["model_answer"]


async def test_changing_a_question_clears_its_answers(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, docs = await matter_docs(client, world, new_matter, run_jobs)
    h = world.partner_a.headers
    t = (
        await client.post(
            f"/v1/matters/{mid}/review-tables",
            json={"template": "contracts", "document_ids": [docs["spa.txt"]]},
            headers=h,
        )
    ).json()
    assert t["title"] == "Contract review" and t["columns"] == 8
    await client.post(f"/v1/review-tables/{t['id']}/run", json={}, headers=h)
    await run_jobs()
    grid = (await client.get(f"/v1/review-tables/{t['id']}", headers=h)).json()
    col = grid["column_list"][0]
    assert any(c["column_id"] == col["id"] and c["status"] != "empty" for c in grid["cells"])
    r = await client.patch(
        f"/v1/review-tables/{t['id']}/columns/{col['id']}",
        json={"question": "Who signed the agreement for the seller?"},
        headers=h,
    )
    assert not any(c["column_id"] == col["id"] for c in r.json()["cells"])
    r = await client.post(
        f"/v1/review-tables/{t['id']}/columns",
        json={"label": "Seat", "question": "Where is the seat of arbitration?"},
        headers=h,
    )
    assert r.json()["columns"] == 9 and r.json()["column_list"][-1]["position"] == 8
    r = await client.delete(f"/v1/review-tables/{t['id']}/columns/{col['id']}", headers=h)
    assert r.json()["columns"] == 8


async def test_exports_csv_and_excel_with_evidence(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, docs = await matter_docs(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    t = (
        await client.post(
            f"/v1/matters/{mid}/review-tables",
            json={
                "columns": [
                    {
                        "label": "Rent",
                        "question": "What is the monthly rent payable?",
                        "answer_type": "amount",
                    }
                ],
                "document_ids": [docs["lease.txt"]],
            },
            headers=h,
        )
    ).json()
    await client.post(f"/v1/review-tables/{t['id']}/run", json={}, headers=h)
    await run_jobs()
    viewer = world.paralegal_a.headers  # viewers may export
    r = await client.get(f"/v1/review-tables/{t['id']}/export", params={"format": "csv"}, headers=viewer)
    assert r.status_code == 200 and r.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert (
        rows[0] == ["Document", "Type", "Rent", "Rent - source"]
        and "2,40,000" in rows[1][2]
        and "verified" in rows[1][3]
    )
    x = await client.get(f"/v1/review-tables/{t['id']}/export", headers=viewer)
    assert "spreadsheetml" in x.headers["content-type"]
    with zipfile.ZipFile(io.BytesIO(x.content)) as z:
        assert "xl/worksheets/sheet2.xml" in z.namelist()
        assert "2,40,000" in z.read("xl/worksheets/sheet2.xml").decode()


async def test_review_tables_follow_matter_permissions(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, docs = await matter_docs(client, world, new_matter, run_jobs)
    body = {"columns": [{"label": "Rent", "question": "What is the monthly rent payable?"}]}
    assert (
        await client.post(f"/v1/matters/{mid}/review-tables", json=body, headers=world.paralegal_a.headers)
    ).status_code == 403
    t = (
        await client.post(f"/v1/matters/{mid}/review-tables", json=body, headers=world.associate_a.headers)
    ).json()
    assert t["documents"] == 4  # default: every indexed document
    assert (
        await client.post(f"/v1/review-tables/{t['id']}/run", json={}, headers=world.paralegal_a.headers)
    ).status_code == 403
    assert (
        await client.get(f"/v1/review-tables/{t['id']}", headers=world.paralegal_a.headers)
    ).status_code == 200
    for outsider in (world.admin_a, world.partner_b):
        assert (await client.get(f"/v1/review-tables/{t['id']}", headers=outsider.headers)).status_code == 404
    other = await client.post(
        "/v1/matters", json={"title": "Other matter", "client_name": "Y"}, headers=world.partner_a.headers
    )
    r = await client.post(
        f"/v1/review-tables/{t['id']}/documents",
        json={"document_ids": [docs["spa.txt"]]},
        headers=world.associate_a.headers,
    )
    assert r.status_code == 200
    r = await client.post(
        f"/v1/matters/{other.json()['id']}/review-tables",
        json={**body, "document_ids": [docs["spa.txt"]]},
        headers=world.partner_a.headers,
    )
    assert r.status_code == 404  # documents must belong to the table's matter


async def test_cancel_stops_a_run_and_frees_the_table(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, _ = await matter_docs(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    t = (
        await client.post(f"/v1/matters/{mid}/review-tables", json={"template": "notices"}, headers=h)
    ).json()
    await client.post(f"/v1/review-tables/{t['id']}/run", json={}, headers=h)
    r = await client.post(f"/v1/review-tables/{t['id']}/cancel", headers=h)
    assert (
        r.status_code == 200
        and r.json()["job"]["status"] == "cancelled"
        and r.json()["totals"]["queued"] == 0
    )
    await run_jobs()  # a cancelled job is never picked up
    assert (await client.get(f"/v1/review-tables/{t['id']}", headers=h)).json()["totals"]["done"] == 0
    assert (await client.post(f"/v1/review-tables/{t['id']}/run", json={}, headers=h)).status_code == 202
