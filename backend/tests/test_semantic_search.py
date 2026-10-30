"""VLT-08 (Step 1c), ADR-019: hybrid search — meaning-based ranking fused with keyword ranking.

Uses a tiny "concept" embedder so tests can say exactly which texts mean the same thing:
"Can the buyer walk away from the deal?" shares no keyword with "Either party may terminate…",
but both sit on the termination axis. Every leak case from the keyword suite is repeated on the
semantic path, because a vector store is a second copy of the data with its own filter.
"""

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.core.embeddings import EmbeddingError, FakeEmbedder, Kind, use_embedder
from app.core.vectors import (
    MemoryStore,
    Point,
    QdrantStore,
    Scope,
    _filter,
    collection_name,
    get_vector_store,
)
from app.db.session import tenant_session
from app.modules.assistant.retrieval import retrieve
from app.modules.vault import hybrid
from tests.conftest import World
from tests.test_vault_api import RunJobs, upload

SPA = (
    b"SHARE PURCHASE AGREEMENT\n\nThis Agreement is made between Lotus Holdings and Zenith LLP.\n\n"
    b"12. TERMINATION\n12.1 Either party may terminate this Agreement by giving thirty days written notice.\n"
)
ORDER = b"ORDER\nThe applicant is released on regular bail on furnishing a personal bond of Rs. 50,000.\n"
WALK_AWAY = "Can the buyer walk away from the deal?"


class ConceptEmbedder:
    """Axis i = how often a text mentions concept i. Passages also get a small bias axis, queries do not,
    so a query about nothing in particular is similar to nothing."""

    name = "stub-concepts"
    dim = 6
    AXES = (
        ("terminat", "walk away", "exit the", "rescind", "end the agreement"),
        ("bail", "custody", "released", "bond"),
        ("payment", "rupees", "rs.", "dues"),
        ("kestrel", "merger"),
        ("premises", "noida", "gurugram"),
    )

    def __init__(self) -> None:
        self.calls = 0

    def vector(self, text: str, kind: Kind) -> list[float]:
        t = text.lower()
        v = [float(sum(t.count(w) for w in words)) for words in self.AXES]
        v.append(0.1 if kind == "passage" else 0.0)
        n = sum(x * x for x in v) ** 0.5
        return [x / n for x in v] if n else v

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        self.calls += 1
        return [self.vector(t, kind) for t in texts]


class BrokenEmbedder(ConceptEmbedder):
    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        raise EmbeddingError("embedding service is down")


@pytest.fixture
def concepts(monkeypatch: pytest.MonkeyPatch) -> Iterator[ConceptEmbedder]:
    """Semantic search on, with the concept embedder and a fresh in-memory store."""
    s = get_settings()
    monkeypatch.setattr(s, "vector_store", "memory")
    monkeypatch.setattr(s, "semantic_min_score", 0.5)
    emb = ConceptEmbedder()
    store = get_vector_store(emb.name, emb.dim)
    assert isinstance(store, MemoryStore)
    store.points.clear()
    with use_embedder(emb):
        yield emb


def store_for(emb: ConceptEmbedder) -> MemoryStore:
    store = get_vector_store(emb.name, emb.dim)
    assert isinstance(store, MemoryStore)
    return store


async def full_search(client: AsyncClient, headers: dict[str, str], query: str, **kw: Any) -> dict[str, Any]:
    r = await client.post("/v1/search", json={"query": query, **kw}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- units


def test_rrf_rewards_agreement_between_rankings() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fused = hybrid.fuse([[a, b], [b, c]], k=60)
    assert [x for x, _ in fused] == [b, a, c]  # b is in both lists
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61)


async def test_fake_embedder_is_deterministic_normalised_and_lexical() -> None:
    e = FakeEmbedder()
    [v1, v2, v3] = await e.embed(
        ["termination notice period", "Termination notice period", "bail bond"], "passage"
    )
    assert v1 == v2 and abs(sum(x * x for x in v1) - 1) < 1e-9
    assert sum(x * y for x, y in zip(v1, v3, strict=True)) < 0.3


def test_scope_cannot_be_empty_and_qdrant_filter_is_mandatory() -> None:
    with pytest.raises(ValueError):
        Scope(uuid.uuid4(), frozenset())
    firm, m1, m2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    must = _filter(Scope(firm, frozenset({m1, m2}), doc_types=frozenset({"order"})))["must"]
    assert must[0] == {"key": "firm_id", "match": {"value": str(firm)}}
    assert must[1] == {"key": "matter_id", "match": {"any": sorted([str(m1), str(m2)])}}
    assert must[2] == {"key": "doc_type", "match": {"any": ["order"]}}


async def test_memory_store_filters_like_qdrant() -> None:
    store = MemoryStore(collection="unit")
    firm_a, firm_b, m1, m2, d1, d2 = (uuid.uuid4() for _ in range(6))
    v = [1.0, 0.0]
    pts = [
        Point(uuid.uuid4(), firm_a, m1, d1, uuid.uuid4(), "order", v),
        Point(uuid.uuid4(), firm_a, m2, d2, uuid.uuid4(), "notice", v),
        Point(uuid.uuid4(), firm_b, m1, d1, uuid.uuid4(), "order", v),  # same matter id, other firm
    ]
    await store.upsert(pts)
    got = await store.search(Scope(firm_a, frozenset({m1})), v, 10, 0.5)
    assert [h.chunk_id for h in got] == [pts[0].chunk_id]
    got = await store.search(Scope(firm_a, frozenset({m1, m2}), doc_types=frozenset({"notice"})), v, 10, 0.5)
    assert [h.chunk_id for h in got] == [pts[1].chunk_id]
    assert await store.search(Scope(firm_a, frozenset({m1})), [0.0, 1.0], 10, 0.5) == []  # below threshold
    await store.delete_documents(firm_a, [d1])
    assert await store.count(firm_a) == 1 and await store.count(firm_b) == 1


def test_collection_name_changes_with_the_model() -> None:
    assert collection_name("BAAI/bge-m3", 1024) == "dataviv_chunks_baai_bge_m3_1024"
    assert collection_name("BAAI/bge-m3", 1024) != collection_name("intfloat/multilingual-e5-large", 1024)


# --------------------------------------------------------------------------- API


async def test_ingestion_embeds_and_meaning_finds_what_keywords_miss(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    concepts: ConceptEmbedder,
) -> None:
    mid, h = new_matter["id"], world.partner_a.headers
    res = await upload(client, mid, h, ("spa.txt", SPA), ("bail-order.txt", ORDER))
    await run_jobs()
    doc_id = res["files"][0]["document_id"]
    detail = (await client.get(f"/v1/documents/{doc_id}", headers=h)).json()
    assert detail["chunk_count"] >= 1 and detail["semantic_chunks"] == detail["chunk_count"]

    assert (await full_search(client, h, WALK_AWAY, matter_ids=[mid], mode="keyword"))["hits"] == []
    out = await full_search(client, h, WALK_AWAY, matter_ids=[mid])
    assert out["mode"] == "hybrid" and out["notice"] is None
    top = out["hits"][0]
    assert top["document_title"] == "spa.txt" and top["match"] == "semantic" and top["similarity"] > 0.9
    assert all(x["document_title"] != "bail-order.txt" for x in out["hits"])  # below the similarity floor

    both = await full_search(client, h, "terminate", matter_ids=[mid])
    assert both["hits"][0]["match"] == "both" and "«" in both["hits"][0]["snippet"]

    await hybrid.backfill(world.firm_a)  # other tests' passages were embedded by the suite's default model
    status = (await client.get("/v1/search/status", headers=h)).json()
    assert status["enabled"] and status["model"] == "stub-concepts" and status["pending_passages"] == 0
    assert status["vectors"] >= 2 and status["reachable"] is True


async def test_unrelated_question_finds_nothing_by_meaning(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    concepts: ConceptEmbedder,
) -> None:
    mid, h = new_matter["id"], world.partner_a.headers
    await upload(client, mid, h, ("spa.txt", SPA))
    await run_jobs()
    out = await full_search(client, h, "What colour is the sky?", matter_ids=[mid], mode="semantic")
    assert out["mode"] == "semantic" and out["hits"] == []


async def test_semantic_path_respects_membership_walls_firms_and_filters(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    concepts: ConceptEmbedder,
) -> None:
    mid = new_matter["id"]
    q = "merger plans"  # only the concept axis matches; no keyword in common with the document
    await upload(client, mid, world.partner_a.headers, ("secret.txt", b"Project Kestrel term sheet."))
    await run_jobs()

    async def semantic(user_headers: dict[str, str], **kw: Any) -> list[dict[str, Any]]:
        return (await full_search(client, user_headers, q, mode="semantic", **kw))["hits"]  # type: ignore[no-any-return]

    assert await semantic(world.partner_a.headers)
    assert await semantic(world.associate_a.headers) == []  # same firm, not a member
    assert await semantic(world.partner_b.headers) == []  # other firm
    assert await semantic(world.admin_a.headers) == []  # admins get no implicit access
    assert await semantic(world.partner_a.headers, doc_types=["judgment"]) == []
    other = await client.post(
        "/v1/matters", json={"title": "Other", "client_name": "X"}, headers=world.partner_a.headers
    )
    assert await semantic(world.partner_a.headers, matter_ids=[other.json()["id"]]) == []

    await client.post(
        f"/v1/matters/{mid}/members",
        json={"user_id": str(world.associate_a.id)},
        headers=world.partner_a.headers,
    )
    assert await semantic(world.associate_a.headers)
    await client.post(
        f"/v1/matters/{mid}/walls",
        json={"user_id": str(world.associate_a.id), "reason": "Acted for the counterparty"},
        headers=world.admin_a.headers,
    )
    assert await semantic(world.associate_a.headers) == []  # wall beats membership, no re-index needed


async def test_vectors_follow_delete_and_new_versions(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    concepts: ConceptEmbedder,
) -> None:
    mid, h = new_matter["id"], world.partner_a.headers
    store = store_for(concepts)
    first = await upload(client, mid, h, ("lease.txt", b"The premises are at Gurugram."))
    await run_jobs()
    doc_id = first["files"][0]["document_id"]

    def doc_points() -> list[uuid.UUID]:
        return [cid for cid, (p, _) in store.points.items() if p["document_id"] == doc_id]

    v1 = doc_points()
    assert len(v1) == 1
    await upload(client, mid, h, ("LEASE.txt", b"The premises move to Noida."))
    await run_jobs()
    v2 = doc_points()
    assert len(v2) == 1 and v2 != v1  # old version's vector removed, new one added

    assert (await client.delete(f"/v1/documents/{doc_id}", headers=h)).status_code == 204
    assert doc_points() == []
    assert (await full_search(client, h, "where is the property located", matter_ids=[mid]))["hits"] == []


async def test_stale_vector_without_passage_is_never_returned(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    concepts: ConceptEmbedder,
) -> None:
    """If the vector store missed a delete (it was down), Postgres still decides what exists."""
    mid, h = new_matter["id"], world.partner_a.headers
    res = await upload(client, mid, h, ("spa.txt", SPA))
    await run_jobs()
    store = store_for(concepts)
    saved = dict(store.points)
    assert (
        await client.delete(f"/v1/documents/{res['files'][0]['document_id']}", headers=h)
    ).status_code == 204
    store.points.update(saved)  # simulate the missed delete
    assert (await full_search(client, h, WALK_AWAY, matter_ids=[mid]))["hits"] == []


async def test_outage_falls_back_to_keywords_then_backfill_catches_up(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    concepts: ConceptEmbedder,
) -> None:
    mid, h = new_matter["id"], world.partner_a.headers
    with use_embedder(BrokenEmbedder()):
        res = await upload(client, mid, h, ("spa.txt", SPA))
        await run_jobs()
        doc = (await client.get(f"/v1/documents/{res['files'][0]['document_id']}", headers=h)).json()
        assert doc["status"] == "indexed"  # keyword search works at once
        out = await full_search(client, h, "thirty days", matter_ids=[mid])
        assert out["mode"] == "keyword" and out["hits"] and "unavailable" in (out["notice"] or "")

    status = (await client.get("/v1/search/status", headers=h)).json()
    assert status["pending_passages"] >= 1
    assert await hybrid.backfill(world.firm_a) >= 1
    status = (await client.get("/v1/search/status", headers=h)).json()
    assert status["pending_passages"] == 0
    assert (await full_search(client, h, WALK_AWAY, matter_ids=[mid]))["hits"][0]["match"] == "semantic"


async def test_semantic_off_keeps_keyword_behaviour(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, h = new_matter["id"], world.partner_a.headers
    with use_embedder(None):
        await upload(client, mid, h, ("spa.txt", SPA))
        await run_jobs()
        out = await full_search(client, h, "thirty days", matter_ids=[mid], mode="semantic")
        assert out["mode"] == "keyword" and out["hits"][0]["match"] == "keyword"
        assert "not switched on" in out["notice"]
        assert (await client.get("/v1/search/status", headers=h)).json()["enabled"] is False


async def test_assistant_agent_and_workflow_retrieval_use_meaning(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    concepts: ConceptEmbedder,
) -> None:
    mid = uuid.UUID(new_matter["id"])
    await upload(client, str(mid), world.partner_a.headers, ("spa.txt", SPA), ("bail-order.txt", ORDER))
    await run_jobs()
    async with tenant_session(world.firm_a) as db:
        sources, terms = await retrieve(db, mid, WALK_AWAY, None, "ask")
        assert sources and "terminate" in sources[0].text and sources[0].document_title == "spa.txt"
        assert terms  # keyword terms are still reported (agents show them when nothing matches)
        nothing, _ = await retrieve(db, mid, "What colour is the sky?", None, "ask")
        assert nothing == []
    async with tenant_session(world.firm_b) as db:  # another firm's context sees nothing
        assert (await retrieve(db, mid, WALK_AWAY, None, "ask"))[0] == []


# --------------------------------------------------------------------------- real Qdrant (optional)

QDRANT_URL = os.environ.get("DATAVIV_TEST_QDRANT_URL")


@pytest.mark.skipif(not QDRANT_URL, reason="set DATAVIV_TEST_QDRANT_URL=http://127.0.0.1:6333 to run")
async def test_qdrant_store_round_trip_and_isolation() -> None:
    assert QDRANT_URL
    store = QdrantStore(QDRANT_URL, f"dataviv_test_{uuid.uuid4().hex[:8]}")
    firm_a, firm_b, m1, m2, d1, d2 = (uuid.uuid4() for _ in range(6))
    try:
        assert (
            await store.search(Scope(firm_a, frozenset({m1})), [1.0, 0.0, 0.0], 5, 0.1) == []
        )  # no collection yet
        pts = [
            Point(uuid.uuid4(), firm_a, m1, d1, uuid.uuid4(), "order", [1.0, 0.0, 0.0]),
            Point(uuid.uuid4(), firm_a, m2, d2, uuid.uuid4(), "notice", [0.9, 0.1, 0.0]),
            Point(uuid.uuid4(), firm_b, m1, d1, uuid.uuid4(), "order", [1.0, 0.0, 0.0]),
        ]
        await store.upsert(pts)
        got = await store.search(Scope(firm_a, frozenset({m1, m2})), [1.0, 0.0, 0.0], 5, 0.5)
        assert [h.chunk_id for h in got] == [pts[0].chunk_id, pts[1].chunk_id]
        got = await store.search(Scope(firm_a, frozenset({m1})), [1.0, 0.0, 0.0], 5, 0.5)
        assert [h.chunk_id for h in got] == [pts[0].chunk_id]
        got = await store.search(
            Scope(firm_a, frozenset({m2}), doc_types=frozenset({"order"})), [1.0, 0, 0], 5, 0.5
        )
        assert got == []
        assert await store.search(Scope(firm_a, frozenset({m1})), [0.0, 0.0, 1.0], 5, 0.5) == []
        assert await store.count(firm_a) == 2 and await store.count(firm_b) == 1
        await store.delete_documents(firm_a, [d1])
        assert await store.count(firm_a) == 1 and await store.count(firm_b) == 1  # other firm untouched
    finally:
        await store._call("DELETE", f"/collections/{store.collection}")
