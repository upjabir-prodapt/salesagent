"""Firestore search cache repository unit tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.shared.exceptions import DatabaseError
from src.shared.repositories.firestore_repository import (
    FirestoreSearchCacheRepository,
    company_key,
    query_hash,
)


class FakeDocument:
    def __init__(self, data: dict):
        self._data = data

    def to_dict(self) -> dict:
        return self._data


class FakeQuery:
    """Records the filters applied and replays a canned document list."""

    def __init__(self, docs: list[dict], calls: dict):
        self._docs = docs
        self._calls = calls

    def where(self, filter=None):  # noqa: A002 - mirrors the Firestore API
        self._calls.setdefault("filters", []).append(filter)
        return self

    def order_by(self, field, direction=None):
        self._calls["order_by"] = (field, direction)
        return self

    def select(self, fields):
        self._calls["select"] = fields
        return self

    def count(self):
        self._calls["count"] = True
        aggregation = MagicMock()
        result = MagicMock()
        result.value = len(self._docs)
        aggregation.get.return_value = [[result]]
        return aggregation

    def stream(self):
        return [FakeDocument(d) for d in self._docs]


class FakeCollection(FakeQuery):
    def document(self, doc_id):
        self._calls.setdefault("doc_ids", []).append(doc_id)
        return f"doc:{doc_id}"


class FakeBatch:
    def __init__(self, writes: list):
        self._writes = writes
        self.committed = 0

    def set(self, ref, document):
        self._writes.append((ref, document))

    def commit(self):
        self.committed += 1


class FakeClient:
    def __init__(self, docs: list[dict] | None = None):
        self.docs = docs or []
        self.calls: dict = {}
        self.writes: list = []
        self.batches: list[FakeBatch] = []

    def collection(self, name):
        self.calls["collection"] = name
        return FakeCollection(self.docs, self.calls)

    def batch(self):
        batch = FakeBatch(self.writes)
        self.batches.append(batch)
        return batch


@pytest.fixture
def repo() -> FirestoreSearchCacheRepository:
    return FirestoreSearchCacheRepository(client=FakeClient())


def test_company_key_is_case_and_whitespace_insensitive() -> None:
    assert company_key("  Acme Corp  ") == company_key("acme corp")
    assert company_key("Acme Corp") != company_key("Other Corp")


def test_insert_batch_upserts_one_document_per_query(
    repo: FirestoreSearchCacheRepository,
) -> None:
    records = [
        {
            "company_name": "Acme Corp",
            "query": "acme revenue 2025",
            "query_hash": query_hash("acme revenue 2025"),
            "search_results": json.dumps([{"url": "https://acme.example/0"}]),
            "domain": "market_analyst",
            "search_date": "2026-08-22T10:00:00+00:00",
        },
        {
            "company_name": "Acme Corp",
            "query": "acme ceo",
            "query_hash": query_hash("acme ceo"),
            "search_results": [{"url": "https://acme.example/1"}],
            "domain": "executive",
            "search_date": datetime(2026, 8, 22, 11, tzinfo=UTC),
        },
    ]

    assert repo.insert_search_query_batch(records) is True

    client = repo.client
    assert len(client.writes) == 2
    assert len(client.batches) == 1
    assert client.batches[0].committed == 1

    _, first = client.writes[0]
    assert first["company_key"] == company_key("Acme Corp")
    assert first["query"] == "acme revenue 2025"
    assert first["search_date"].tzinfo is not None

    # Non-string payloads are JSON-encoded so the field stays a single string.
    _, second = client.writes[1]
    assert second["search_results"] == '[{"url": "https://acme.example/1"}]'

    # Document ids are deterministic: re-running the same query upserts.
    doc_ids = client.calls["doc_ids"]
    assert (
        doc_ids[0] == f"{company_key('Acme Corp')}__{query_hash('acme revenue 2025')}"
    )
    assert len(set(doc_ids)) == 2


def test_insert_batch_noop_on_empty(repo: FirestoreSearchCacheRepository) -> None:
    assert repo.insert_search_query_batch([]) is True
    assert repo.client.writes == []


def test_insert_batch_wraps_unexpected_errors() -> None:
    client = FakeClient()
    client.batch = MagicMock(side_effect=RuntimeError("boom"))
    repo = FirestoreSearchCacheRepository(client=client)

    with pytest.raises(DatabaseError):
        repo.insert_search_query_batch([{"company_name": "Acme", "query": "q"}])


def test_get_searches_for_company_orders_by_date_desc() -> None:
    docs = [
        {
            "company_name": "Acme Corp",
            "query": "acme ceo",
            "search_results": '[{"url": "https://acme.example/1"}]',
            "domain": "executive",
            "search_date": datetime(2026, 8, 22, 11, tzinfo=UTC),
        }
    ]
    repo = FirestoreSearchCacheRepository(client=FakeClient(docs))

    results = repo.get_searches_for_company("Acme Corp")

    assert len(results) == 1
    assert repo.client.calls["order_by"][0] == "search_date"


def test_count_searches_reads_aggregation_value() -> None:
    docs = [{"query": "a"}, {"query": "b"}, {"query": "c"}]
    repo = FirestoreSearchCacheRepository(client=FakeClient(docs))

    assert repo.count_searches("Acme Corp") == 3


def test_get_cached_query_hashes_returns_matches() -> None:
    hashes = [query_hash("q1"), query_hash("q2")]
    docs = [{"query_hash": hashes[0]}]
    repo = FirestoreSearchCacheRepository(client=FakeClient(docs))

    found = repo.get_cached_query_hashes("Acme Corp", hashes)

    assert found == {hashes[0]}
    assert repo.client.calls["select"] == ["query_hash"]


def test_get_cached_query_hashes_empty_input(
    repo: FirestoreSearchCacheRepository,
) -> None:
    assert repo.get_cached_query_hashes("Acme Corp", []) == set()
