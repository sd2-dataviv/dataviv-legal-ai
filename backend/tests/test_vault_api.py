"""VLT-01..08, VLT-11, NFR-09: upload, background ingestion with progress, dedupe, versions, search
permissions, citation lookup, download and delete — end to end against PostgreSQL."""

from collections.abc import Awaitable, Callable
from typing import Any

from httpx import AsyncClient

from tests.conftest import World
from tests.factories import make_docx, make_pdf

RunJobs = Callable[[], Awaitable[int]]

ORDER_PDF = make_pdf(
    [
        ["IN THE HIGH COURT OF DELHI AT NEW DELHI", "BAIL APPLN. 1234/2026", "CORAM: HON'BLE MR. JUSTICE A. KUMAR"],
        [],  # scan-like page: waits for OCR
        ["ORDER", "12. The applicant is admitted to regular bail on furnishing a personal bond."],
    ]
)
NOTICE_DOCX = make_docx(["LEGAL NOTICE", "Take notice that my client demands payment of Rs. 5,00,000 within 15 days."])
SPA_TXT = (
    "SHARE PURCHASE AGREEMENT\n\nThis Agreement is made between Lotus Holdings and Zenith LLP.\n\n"
    "12. TERMINATION\n12.1 Either party may terminate this Agreement by giving thirty days written notice.\n"
).encode()


async def upload(client: AsyncClient, matter_id: str, headers: dict[str, str], *files: tuple[str, bytes]) -> Any:
    r = await client.post(
        f"/v1/matters/{matter_id}/documents",
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
        headers=headers,
    )
    assert r.status_code == 202, r.text
    return r.json()


async def search(client: AsyncClient, headers: dict[str, str], query: str, **kw: Any) -> list[dict[str, Any]]:
    r = await client.post("/v1/search", json={"query": query, **kw}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["hits"]  # type: ignore[no-any-return]


async def test_upload_ingest_progress_and_search(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, h = new_matter["id"], world.partner_a.headers
    res = await upload(client, mid, h, ("bail-order.pdf", ORDER_PDF), ("notice.docx", NOTICE_DOCX), ("spa.txt", SPA_TXT))
    assert [f["status"] for f in res["files"]] == ["queued", "queued", "queued"]

    job = (await client.get(f"/v1/jobs/{res['job_id']}", headers=h)).json()
    assert job["status"] == "queued" and job["total"] == 3 and job["progress"] == 0

    assert await run_jobs() >= 1
    job = (await client.get(f"/v1/jobs/{res['job_id']}", headers=h)).json()
    assert job["status"] == "succeeded" and job["done"] == 3 and job["progress"] == 100, job

    docs = {d["title"]: d for d in (await client.get(f"/v1/matters/{mid}/documents", headers=h)).json()}
    assert docs["bail-order.pdf"]["doc_type"] == "order"
    assert docs["bail-order.pdf"]["status"] == "partial" and docs["bail-order.pdf"]["ocr_pending_pages"] == [2]
    assert docs["bail-order.pdf"]["page_count"] == 3
    assert docs["notice.docx"]["doc_type"] == "notice" and docs["notice.docx"]["status"] == "indexed"
    assert docs["spa.txt"]["doc_type"] == "agreement"

    hits = await search(client, h, "terminated notice", matter_ids=[mid])  # stemming: terminated -> terminate
    assert hits and hits[0]["document_title"] == "spa.txt"
    assert "12. TERMINATION" in hits[0]["section_path"] and "«" in hits[0]["snippet"]

    hits = await search(client, h, "regular bail", matter_ids=[mid])
    assert hits[0]["document_title"] == "bail-order.pdf" and hits[0]["page_to"] == 3  # page 2 awaits OCR

    only_notices = await search(client, h, "notice", matter_ids=[mid], doc_types=["notice"])
    assert {x["doc_type"] for x in only_notices} == {"notice"}


async def test_search_respects_membership_walls_and_firms(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid = new_matter["id"]
    await upload(client, mid, world.partner_a.headers, ("walled-secret.txt", b"Project Kestrel merger term sheet."))
    await run_jobs()
    assert await search(client, world.partner_a.headers, "Kestrel")
    assert await search(client, world.associate_a.headers, "Kestrel") == []  # same firm, not a member
    assert await search(client, world.partner_b.headers, "Kestrel") == []  # other firm
    assert await search(client, world.admin_a.headers, "Kestrel") == []  # admins get no implicit access

    await client.post(f"/v1/matters/{mid}/members", json={"user_id": str(world.associate_a.id)}, headers=world.partner_a.headers)
    assert await search(client, world.associate_a.headers, "Kestrel")
    await client.post(
        f"/v1/matters/{mid}/walls",
        json={"user_id": str(world.associate_a.id), "reason": "Acted for the counterparty"},
        headers=world.admin_a.headers,
    )
    assert await search(client, world.associate_a.headers, "Kestrel") == []  # wall beats membership


async def test_duplicate_and_new_version(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, h = new_matter["id"], world.partner_a.headers
    first = await upload(client, mid, h, ("draft.txt", b"Version one mentions the Gurugram premises."))
    await run_jobs()
    again = await upload(client, mid, h, ("copy-of-draft.txt", b"Version one mentions the Gurugram premises."))
    assert again["files"][0]["status"] == "duplicate" and again["job_id"] is None
    assert again["files"][0]["document_id"] == first["files"][0]["document_id"]

    v2 = await upload(client, mid, h, ("DRAFT.txt", b"Version two moves to the Noida premises."))
    assert v2["files"][0]["status"] == "new_version" and v2["files"][0]["version_no"] == 2
    await run_jobs()
    assert await search(client, h, "Noida", matter_ids=[mid])
    assert await search(client, h, "Gurugram", matter_ids=[mid]) == []  # old version's text replaced
    detail = (await client.get(f"/v1/documents/{first['files'][0]['document_id']}", headers=h)).json()
    assert [v["version_no"] for v in detail["versions"]] == [2, 1] and detail["chunk_count"] >= 1


async def test_citation_download_and_delete(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, h = new_matter["id"], world.partner_a.headers
    res = await upload(client, mid, h, ("spa.txt", SPA_TXT))
    await run_jobs()
    doc_id = res["files"][0]["document_id"]
    hit = (await search(client, h, "thirty days", matter_ids=[mid]))[0]

    chunk = (await client.get(f"/v1/documents/{doc_id}/chunks/{hit['chunk_id']}", headers=h)).json()
    assert "thirty days" in chunk["text"] and chunk["page_from"] == 1 and chunk["char_end"] > chunk["char_start"]
    assert (await client.get(f"/v1/documents/{doc_id}/chunks/{hit['chunk_id']}", headers=world.partner_b.headers)).status_code == 404

    dl = await client.get(f"/v1/documents/{doc_id}/download", headers=h)
    assert dl.status_code == 200 and dl.content == SPA_TXT and "spa.txt" in dl.headers["content-disposition"]
    assert (await client.get(f"/v1/documents/{doc_id}/download", headers=world.associate_a.headers)).status_code == 404

    assert (await client.delete(f"/v1/documents/{doc_id}", headers=h)).status_code == 204
    assert await search(client, h, "thirty days", matter_ids=[mid]) == []
    assert (await client.get(f"/v1/documents/{doc_id}", headers=h)).status_code == 404


async def test_rejections_and_viewer_cannot_upload(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid, h = new_matter["id"], world.partner_a.headers
    res = await upload(client, mid, h, ("tool.exe", b"MZ..."), ("ok.txt", b"Fine text for the record."))
    assert [f["status"] for f in res["files"]] == ["rejected", "queued"] and res["job_id"]

    await client.post(
        f"/v1/matters/{mid}/members",
        json={"user_id": str(world.paralegal_a.id), "matter_role": "viewer"},
        headers=h,
    )
    r = await client.post(
        f"/v1/matters/{mid}/documents", files=[("files", ("x.txt", b"x", "text/plain"))], headers=world.paralegal_a.headers
    )
    assert r.status_code == 403
    r = await client.post(
        f"/v1/matters/{mid}/documents", files=[("files", ("x.txt", b"x", "text/plain"))], headers=world.partner_b.headers
    )
    assert r.status_code == 404  # other firm: the matter does not exist for them


async def test_hindi_text_is_searchable(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, h = new_matter["id"], world.partner_a.headers
    text = "याचिकाकर्ता ने उच्च न्यायालय में जमानत याचिका दायर की। सुनवाई 12 मार्च को होगी।"
    await upload(client, mid, h, ("hindi-petition.txt", text.encode("utf-8")))
    await run_jobs()
    hits = await search(client, h, "जमानत", matter_ids=[mid])
    assert hits and hits[0]["document_title"] == "hindi-petition.txt"


async def test_worker_handles_several_firms(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    rb = await client.post(
        "/v1/matters", json={"title": "Mehta v Union", "client_name": "Mehta"}, headers=world.partner_b.headers
    )
    mid_b = rb.json()["id"]
    await upload(client, new_matter["id"], world.partner_a.headers, ("a.txt", b"Firm A only: Saffron clause."))
    await upload(client, mid_b, world.partner_b.headers, ("b.txt", b"Firm B only: Indigo clause."))
    assert await run_jobs() >= 2
    assert await search(client, world.partner_b.headers, "Indigo")
    assert await search(client, world.partner_b.headers, "Saffron") == []
    assert await search(client, world.partner_a.headers, "Indigo") == []
