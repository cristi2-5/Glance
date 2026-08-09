"""Fixtures pytest comune tuturor testelor backend-ului Glance."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Client HTTP async legat direct de aplicația FastAPI, fără server real.

    Yields:
        Un `AsyncClient` httpx configurat cu transportul ASGI al aplicației.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
