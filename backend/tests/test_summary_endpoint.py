"""Tests for `GET /books/{book_id}/summary`.

Covers the HTTP contract the mobile client codes against: ownership,
the shape of a successful response, and — the part that matters most for
the result screen — that "this book has no summary" arrives as a normal
`200` with `available=false` rather than an error status. The client
distinguishes an honest gap (fall back to the publisher's blurb) from a
backend failure (show an error), and it can only do that if the backend
says which is which.
"""

from collections.abc import AsyncIterator, Iterator

import chromadb
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import build_data_fetcher, build_rag_service, build_vision_service
from app.core.config import get_settings
from app.core.exceptions import AIProviderUnavailable
from app.db.session import get_db, get_session_factory
from app.main import app
from app.models.book import Book
from app.services.rag_service import RagService
from app.services.vector_store import ChromaVectorStore
from tests.fakes import HashingEmbeddingClient, ScriptedSummaryClient
from tests.fixtures.rag_corpus import build_dune
from tests.test_rag_service import grounded_responder


class _FailingAiClient:
    """An AI client that is always unreachable."""

    async def generate(
        self,
        model: str,
        prompt: str,
        images: list[bytes] | None = None,
        format: str | None = None,
        options: dict[str, object] | None = None,
    ) -> str:
        raise AIProviderUnavailable("Groq is unavailable after 3 attempts.")


@pytest.fixture
def chroma_client() -> Iterator[chromadb.api.ClientAPI]:
    """A clean in-memory Chroma instance per test."""
    client = chromadb.EphemeralClient(
        settings=chromadb.Settings(allow_reset=True, anonymized_telemetry=False)
    )
    client.reset()
    yield client
    client.reset()


def _service(chroma_client: chromadb.api.ClientAPI, ai_client: object) -> RagService:
    """A `RagService` over real Chroma with offline model backends."""
    return RagService(
        embeddings=HashingEmbeddingClient(),
        vector_store=ChromaVectorStore(chroma_client),
        ai_client=ai_client,  # type: ignore[arg-type]
        settings=get_settings(),
    )


async def _client_with(
    db_session_factory: async_sessionmaker[AsyncSession], rag_service: RagService
) -> AsyncIterator[AsyncClient]:
    """An HTTP client with the RAG service overridden to an offline one."""

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_session_factory] = lambda: db_session_factory
    app.dependency_overrides[build_rag_service] = lambda: rag_service
    # The other two are overridden in `conftest`'s `client` fixture, which
    # this test module does not use; the summary route never touches them,
    # but leaving them live would construct a real vision service on import
    # of the dependency graph.
    app.dependency_overrides.setdefault(build_vision_service, lambda: None)
    app.dependency_overrides.setdefault(build_data_fetcher, lambda: None)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


async def _register(client: AsyncClient, email: str = "reader@example.com") -> dict[str, str]:
    """Registers and logs a user in, returning an auth header for them."""
    password = "super-secret-password"
    await client.post("/auth/register", json={"email": email, "password": password})
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _seed_dune(db_session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Persists the *Dune* fixture and returns its id."""
    async with db_session_factory() as db:
        book = build_dune()
        db.add(book)
        await db.commit()
        return book.id


async def test_summary_returns_claims_and_resolvable_citations(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """A successful summary carries claims whose citations resolve to reviews.

    This is the contract the result screen renders against: it makes each
    claim tappable by looking its `chunk_ids` up in `reviews`, so an id
    with no matching review would be a dead link on the phone.
    """
    book_id = await _seed_dune(db_session_factory)
    service = _service(chroma_client, ScriptedSummaryClient(grounded_responder))

    async for client in _client_with(db_session_factory, service):
        headers = await _register(client)
        response = await client.get(f"/books/{book_id}/summary", headers=headers)

    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["available"] is True
    assert payload["book_id"] == book_id
    assert payload["claims"]
    assert payload["text"]

    review_ids = {review["id"] for review in payload["reviews"]}
    for claim in payload["claims"]:
        assert claim["chunk_ids"]
        for chunk_id in claim["chunk_ids"]:
            assert chunk_id in review_ids, f"citation {chunk_id} has no matching review"

    for review in payload["reviews"]:
        assert review["excerpt"]
        assert review["source"] in {"google_books", "open_library", "wikipedia"}


async def test_book_without_passages_returns_200_and_unavailable(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """An empty corpus is a normal `200`, not an error.

    The client needs to tell "no summary exists for this book" from "the
    request failed": the first falls back to the publisher's blurb, the
    second shows an error. A 404 or 503 here would collapse the two.
    """
    async with db_session_factory() as db:
        bare = Book(
            normalized_key="baltagul|mihail sadoveanu",
            title="Baltagul",
            author="Mihail Sadoveanu",
            metadata_found=False,
        )
        db.add(bare)
        await db.commit()
        book_id = bare.id

    service = _service(chroma_client, ScriptedSummaryClient(grounded_responder))

    async for client in _client_with(db_session_factory, service):
        headers = await _register(client)
        response = await client.get(f"/books/{book_id}/summary", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["text"] == ""
    assert payload["claims"] == []
    assert payload["reviews"] == []


async def test_unknown_book_is_404(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """A book id that was never cached is a genuine not-found."""
    service = _service(chroma_client, ScriptedSummaryClient(grounded_responder))

    async for client in _client_with(db_session_factory, service):
        headers = await _register(client)
        response = await client.get("/books/9999/summary", headers=headers)

    assert response.status_code == 404


async def test_summary_requires_authentication(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """The endpoint is behind the same auth as the rest of the API."""
    book_id = await _seed_dune(db_session_factory)
    service = _service(chroma_client, ScriptedSummaryClient(grounded_responder))

    async for client in _client_with(db_session_factory, service):
        response = await client.get(f"/books/{book_id}/summary")

    assert response.status_code == 401


async def test_provider_outage_is_a_503(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """An unreachable summary provider surfaces as 503, not as "no summary".

    The distinction is the whole reason the client can retry: a 503 means
    try again, `available=false` means there is nothing to try for.
    """
    book_id = await _seed_dune(db_session_factory)
    service = _service(chroma_client, _FailingAiClient())

    async for client in _client_with(db_session_factory, service):
        headers = await _register(client)
        response = await client.get(f"/books/{book_id}/summary", headers=headers)

    assert response.status_code == 503
    assert "detail" in response.json()


async def test_second_request_is_served_from_cache(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """Repeat requests reuse the stored summary rather than regenerating."""
    book_id = await _seed_dune(db_session_factory)
    ai_client = ScriptedSummaryClient(grounded_responder)
    service = _service(chroma_client, ai_client)

    async for client in _client_with(db_session_factory, service):
        headers = await _register(client)
        first = await client.get(f"/books/{book_id}/summary", headers=headers)
        second = await client.get(f"/books/{book_id}/summary", headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json()["text"] == second.json()["text"]
    assert len(ai_client.calls) == 1
