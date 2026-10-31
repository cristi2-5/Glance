"""Cloud step 5: managed identity for Storage and PostgreSQL, tested with a fake credential.

Nothing here reaches the network: the real `ManagedIdentityCredential` only
works inside Azure, so the credential factory is replaced. What is proven is
the wiring -- which URL and credential each client gets, and that the engine
hands asyncpg a token as the password -- not Azure itself.
"""

import ssl
from collections.abc import Iterator
from typing import Any

import asyncpg
import pytest
from azure.core.credentials import AccessToken
from azure.storage.blob.aio import ContainerClient
from azure.storage.queue.aio import QueueClient

from app.core import identity
from app.core.config import Settings
from app.db import session as db_session
from app.services import storage


class FakeCredential:
    """Stands in for `ManagedIdentityCredential`: records scopes, issues a fixed token."""

    def __init__(self) -> None:
        self.scopes: list[tuple[str, ...]] = []
        self.closed = False

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        self.scopes.append(scopes)
        return AccessToken("entra-token-123", 4_102_444_800)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_credential(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeCredential]:
    credential = FakeCredential()
    monkeypatch.setattr(identity, "create_credential", lambda settings: credential)
    yield credential


def _clear_storage_caches() -> None:
    for factory in (storage.get_blob_store, storage.get_job_queue, storage._credential):
        factory.cache_clear()


@pytest.fixture
def account_storage(
    monkeypatch: pytest.MonkeyPatch, fake_credential: FakeCredential
) -> Iterator[Settings]:
    """Storage settings with an account name, and clean factory caches around the test."""
    settings = Settings(_env_file=None, azure_storage_account_name="glancestore")
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    _clear_storage_caches()
    yield settings
    _clear_storage_caches()


async def test_an_account_name_selects_the_account_urls_and_the_identity(
    account_storage: Settings, fake_credential: FakeCredential
) -> None:
    blobs = storage.get_blob_store()
    queue = storage.get_job_queue()

    assert blobs._service.url.startswith("https://glancestore.blob.core.windows.net")
    assert queue._service.url.startswith("https://glancestore.queue.core.windows.net")
    assert blobs._service.credential is fake_credential
    assert queue._service.credential is fake_credential
    # One credential for both clients, and no account key anywhere on them.
    assert "AccountKey" not in blobs._service.url + queue._service.url

    await storage.close_storage()
    assert fake_credential.closed, "the credential is closed with the storage clients"


def test_without_an_account_name_the_connection_string_path_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, fake_credential: FakeCredential
) -> None:
    # Explicit: inside the compose `test` container the environment supplies its own.
    settings = Settings(
        _env_file=None,
        azure_storage_connection_string=(
            "DefaultEndpointsProtocol=http;AccountName=acct;AccountKey=a2V5;"
            "BlobEndpoint=http://127.0.0.1:10000/acct;"
        ),
    )
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    _clear_storage_caches()
    try:
        blobs = storage.get_blob_store()
        assert "127.0.0.1:10000" in blobs._service.url
        assert not storage._credential.cache_info().currsize, "no identity is ever created"
    finally:
        _clear_storage_caches()


async def test_the_postgres_token_provider_asks_for_the_postgres_scope(
    fake_credential: FakeCredential,
) -> None:
    provider = identity.postgres_token_provider(fake_credential)  # type: ignore[arg-type]

    assert await provider() == "entra-token-123"
    assert fake_credential.scopes == [(identity.POSTGRES_TOKEN_SCOPE,)]


async def test_an_entra_engine_gives_asyncpg_the_token_as_its_password(
    monkeypatch: pytest.MonkeyPatch, fake_credential: FakeCredential
) -> None:
    captured: dict[str, Any] = {}

    async def fake_connect(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        raise ConnectionRefusedError("stop here: only the arguments matter")

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    settings = Settings(_env_file=None, database_auth="entra")
    engine = db_session.build_engine(
        "postgresql+asyncpg://glance-identity@db.example.com/glance", settings
    )

    with pytest.raises(Exception):  # noqa: B017 - the fake refuses; the kwargs are the point
        async with engine.connect():
            pass

    password = captured["password"]
    assert callable(password)
    assert await password() == "entra-token-123"
    await db_session.dispose_engine(engine)
    assert fake_credential.closed


async def test_a_password_engine_creates_no_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(settings: Settings) -> None:
        raise AssertionError("password auth must not touch the managed identity")

    monkeypatch.setattr(identity, "create_credential", explode)

    engine = db_session.build_engine(
        "postgresql+asyncpg://glance:glance@127.0.0.1:5433/glance", Settings(_env_file=None)
    )
    await db_session.dispose_engine(engine)


async def test_identity_mode_never_creates_the_container_or_the_queue(
    account_storage: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In Azure the container and queue are provisioned by Bicep.

    The api's identity may only *send* to the queue, so a create call would
    be a 403 on every first use -- every scan a 503. The adapters must not
    even try.
    """

    async def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("identity mode must not create storage resources")

    monkeypatch.setattr(ContainerClient, "create_container", _refuse)
    monkeypatch.setattr(QueueClient, "create_queue", _refuse)

    await storage.get_blob_store()._container_client()
    await storage.get_job_queue()._queue_client()


async def _captured_connect_kwargs(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, url: str
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_connect(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        raise ConnectionRefusedError("stop here: only the arguments matter")

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    engine = db_session.build_engine(url, settings)
    with pytest.raises(Exception):  # noqa: B017 - the fake refuses; the kwargs are the point
        async with engine.connect():
            pass
    await db_session.dispose_engine(engine)
    return captured


def _assert_verifying(context: Any) -> None:
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


async def test_an_entra_engine_verifies_the_server_certificate(
    monkeypatch: pytest.MonkeyPatch, fake_credential: FakeCredential
) -> None:
    kwargs = await _captured_connect_kwargs(
        monkeypatch,
        Settings(_env_file=None, database_auth="entra"),
        "postgresql+asyncpg://glance-identity@db.example.com/glance",
    )

    _assert_verifying(kwargs["ssl"])


async def test_a_production_engine_verifies_the_server_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = await _captured_connect_kwargs(
        monkeypatch,
        Settings(_env_file=None, app_env="production"),
        "postgresql+asyncpg://glance:x@db.example.com/glance",
    )

    _assert_verifying(kwargs["ssl"])


async def test_the_development_password_engine_gets_no_ssl_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = await _captured_connect_kwargs(
        monkeypatch,
        Settings(_env_file=None),
        "postgresql+asyncpg://glance:glance@127.0.0.1:5433/glance",
    )

    assert "ssl" not in kwargs


def test_the_queue_worker_options_bound_the_sdk_retries(account_storage: Settings) -> None:
    try:
        storage.configure_client_options(storage.QUEUE_WORKER_CLIENT_OPTIONS)
        queue = storage.get_job_queue()
        blobs = storage.get_blob_store()
        assert queue._service._config.retry_policy.initial_backoff == 2  # type: ignore[attr-defined]
        assert blobs._service._config.retry_policy.initial_backoff == 2  # type: ignore[attr-defined]
    finally:
        storage.configure_client_options({})


def test_the_api_keeps_the_sdk_default_retries(account_storage: Settings) -> None:
    storage.configure_client_options({})

    queue = storage.get_job_queue()

    assert queue._service._config.retry_policy.initial_backoff != 2  # type: ignore[attr-defined]
