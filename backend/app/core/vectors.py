"""Vector store (VLT-08, ADR-003, ADR-019): dense passage vectors with access-control payloads.

Every point carries ``firm_id, matter_id, document_id, version_id, doc_type``. A search takes a
``Scope`` that cannot be built without a firm and at least one matter, and the store turns it into
a mandatory filter, so no caller can forget it. Search results are re-checked in Postgres under
RLS before anyone sees them (hybrid.py), so a leak needs two independent failures (as ADR-002).

Backends, chosen by DATAVIV_VECTOR_STORE:
- ``qdrant``  Qdrant over its REST API with httpx (no qdrant-client: it pulls in grpcio and numpy,
              which Windows Smart App Control blocks; ADR-011). One collection per embedding model.
- ``memory``  pure-Python brute force, per process (tests).
- ``none``    semantic search off.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import get_settings


class VectorStoreError(Exception):
    """The vector store is unreachable or rejected a request."""


@dataclass(frozen=True)
class Point:
    chunk_id: uuid.UUID
    firm_id: uuid.UUID
    matter_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    doc_type: str
    vector: list[float]

    def payload(self) -> dict[str, str]:
        return {
            "firm_id": str(self.firm_id),
            "matter_id": str(self.matter_id),
            "document_id": str(self.document_id),
            "version_id": str(self.version_id),
            "doc_type": self.doc_type,
        }


@dataclass(frozen=True)
class Scope:
    """Where a search may look. Built from accessible_matter_ids(); never empty."""

    firm_id: uuid.UUID
    matter_ids: frozenset[uuid.UUID]
    document_ids: frozenset[uuid.UUID] | None = None
    doc_types: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not self.matter_ids:
            raise ValueError("A vector search needs at least one accessible matter")


@dataclass(frozen=True)
class Hit:
    chunk_id: uuid.UUID
    score: float  # cosine similarity, -1..1
    firm_id: uuid.UUID
    matter_id: uuid.UUID


class VectorStore(Protocol):
    name: str
    collection: str

    async def ensure(self, dim: int) -> None: ...
    async def upsert(self, points: list[Point]) -> None: ...
    async def delete_documents(self, firm_id: uuid.UUID, document_ids: list[uuid.UUID]) -> None: ...
    async def search(self, scope: Scope, vector: list[float], limit: int, min_score: float) -> list[Hit]: ...
    async def count(self, firm_id: uuid.UUID) -> int: ...


def collection_name(model: str, dim: int) -> str:
    """One collection per model and dimension, so switching models never mixes vector spaces."""
    slug = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    return f"{get_settings().vector_collection_prefix}_{slug}_{dim}"


def _allowed(scope: Scope, p: dict[str, str]) -> bool:
    """The scope as a predicate over a payload (memory store, and a final check on Qdrant results)."""
    return (
        p.get("firm_id") == str(scope.firm_id)
        and uuid.UUID(p["matter_id"]) in scope.matter_ids
        and (scope.document_ids is None or uuid.UUID(p["document_id"]) in scope.document_ids)
        and (scope.doc_types is None or p.get("doc_type") in scope.doc_types)
    )


# --------------------------------------------------------------------------- memory


@dataclass
class MemoryStore:
    collection: str
    name: str = "memory"
    points: dict[uuid.UUID, tuple[dict[str, str], list[float]]] = field(default_factory=dict)

    async def ensure(self, dim: int) -> None:
        return None

    async def upsert(self, points: list[Point]) -> None:
        for p in points:
            self.points[p.chunk_id] = (p.payload(), p.vector)

    async def delete_documents(self, firm_id: uuid.UUID, document_ids: list[uuid.UUID]) -> None:
        gone = {str(d) for d in document_ids}
        for cid, (payload, _) in list(self.points.items()):
            if payload["firm_id"] == str(firm_id) and payload["document_id"] in gone:
                del self.points[cid]

    async def search(self, scope: Scope, vector: list[float], limit: int, min_score: float) -> list[Hit]:
        scored = [
            (sum(a * b for a, b in zip(vector, v, strict=True)), cid, payload)
            for cid, (payload, v) in self.points.items()
            if _allowed(scope, payload)
        ]
        scored.sort(key=lambda t: -t[0])
        return [
            Hit(cid, s, uuid.UUID(payload["firm_id"]), uuid.UUID(payload["matter_id"]))
            for s, cid, payload in scored[:limit]
            if s >= min_score
        ]

    async def count(self, firm_id: uuid.UUID) -> int:
        return sum(1 for payload, _ in self.points.values() if payload["firm_id"] == str(firm_id))


# --------------------------------------------------------------------------- qdrant


def _filter(scope: Scope) -> dict[str, Any]:
    must: list[dict[str, Any]] = [
        {"key": "firm_id", "match": {"value": str(scope.firm_id)}},
        {"key": "matter_id", "match": {"any": sorted(str(m) for m in scope.matter_ids)}},
    ]
    if scope.document_ids is not None:
        must.append({"key": "document_id", "match": {"any": sorted(str(d) for d in scope.document_ids)}})
    if scope.doc_types is not None:
        must.append({"key": "doc_type", "match": {"any": sorted(scope.doc_types)}})
    return {"must": must}


class QdrantStore:
    name = "qdrant"

    def __init__(self, url: str, collection: str, api_key: str | None = None, timeout: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self.collection = collection
        self._headers = {"api-key": api_key} if api_key else {}
        self._timeout = timeout
        self._ready = False

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
                resp = await client.request(method, f"{self.url}{path}", json=body)
        except httpx.HTTPError as exc:
            raise VectorStoreError(f"Cannot reach Qdrant at {self.url} ({type(exc).__name__})") from exc
        if resp.status_code >= 400:
            raise VectorStoreError(f"Qdrant {method} {path}: HTTP {resp.status_code} {resp.text[:300]}")
        data: dict[str, Any] = resp.json()
        return data

    async def ensure(self, dim: int) -> None:
        if self._ready:
            return
        c = f"/collections/{self.collection}"
        exists = (await self._call("GET", f"/collections/{self.collection}/exists"))["result"]["exists"]
        if not exists:
            await self._call("PUT", c, {"vectors": {"size": dim, "distance": "Cosine"}})
            # firm_id is the tenant key: Qdrant co-locates each firm's points and filters on it first
            await self._call(
                "PUT",
                f"{c}/index?wait=true",
                {"field_name": "firm_id", "field_schema": {"type": "keyword", "is_tenant": True}},
            )
            for f in ("matter_id", "document_id", "doc_type"):
                await self._call("PUT", f"{c}/index?wait=true", {"field_name": f, "field_schema": "keyword"})
        else:
            size = (await self._call("GET", c))["result"]["config"]["params"]["vectors"]["size"]
            if size != dim:
                raise VectorStoreError(f"Collection {self.collection} holds {size}-dim vectors, not {dim}")
        self._ready = True

    async def upsert(self, points: list[Point]) -> None:
        if not points:
            return
        await self.ensure(len(points[0].vector))
        body = {
            "points": [{"id": str(p.chunk_id), "vector": p.vector, "payload": p.payload()} for p in points]
        }
        await self._call("PUT", f"/collections/{self.collection}/points?wait=true", body)

    async def delete_documents(self, firm_id: uuid.UUID, document_ids: list[uuid.UUID]) -> None:
        if not document_ids:
            return
        if not (await self._call("GET", f"/collections/{self.collection}/exists"))["result"]["exists"]:
            return
        flt = {
            "must": [
                {"key": "firm_id", "match": {"value": str(firm_id)}},
                {"key": "document_id", "match": {"any": [str(d) for d in document_ids]}},
            ]
        }
        await self._call("POST", f"/collections/{self.collection}/points/delete?wait=true", {"filter": flt})

    async def search(self, scope: Scope, vector: list[float], limit: int, min_score: float) -> list[Hit]:
        if (
            not self._ready
            and not (await self._call("GET", f"/collections/{self.collection}/exists"))["result"]["exists"]
        ):
            return []  # nothing embedded yet
        body = {
            "query": vector,
            "filter": _filter(scope),
            "limit": limit,
            "score_threshold": min_score,
            "with_payload": ["firm_id", "matter_id", "document_id", "doc_type"],
        }
        res = await self._call("POST", f"/collections/{self.collection}/points/query", body)
        hits = []
        for pt in res["result"]["points"]:
            payload = pt.get("payload") or {}
            if payload and _allowed(scope, payload):  # belt and braces: Qdrant already filtered
                hits.append(
                    Hit(
                        uuid.UUID(str(pt["id"])),
                        float(pt["score"]),
                        scope.firm_id,
                        uuid.UUID(payload["matter_id"]),
                    )
                )
        return hits

    async def count(self, firm_id: uuid.UUID) -> int:
        if not (await self._call("GET", f"/collections/{self.collection}/exists"))["result"]["exists"]:
            return 0
        flt = {"must": [{"key": "firm_id", "match": {"value": str(firm_id)}}]}
        res = await self._call(
            "POST", f"/collections/{self.collection}/points/count", {"filter": flt, "exact": True}
        )
        return int(res["result"]["count"])


# --------------------------------------------------------------------------- selection

_memory: dict[str, MemoryStore] = {}
_qdrant: dict[str, QdrantStore] = {}


def get_vector_store(model: str, dim: int) -> VectorStore | None:
    """The store holding vectors of ``model``, or None when DATAVIV_VECTOR_STORE=none."""
    s = get_settings()
    name = collection_name(model, dim)
    if s.vector_store == "memory":
        return _memory.setdefault(name, MemoryStore(collection=name))
    if s.vector_store == "qdrant":
        if name not in _qdrant:
            key = s.qdrant_api_key.get_secret_value() if s.qdrant_api_key else None
            _qdrant[name] = QdrantStore(s.qdrant_url, name, key)
        return _qdrant[name]
    return None
