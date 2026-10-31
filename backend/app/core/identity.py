"""Managed identity: the one place that creates Azure credentials.

In Azure the containers hold no storage key and no database password. They
hold a **user-assigned managed identity**; the platform lets code inside the
container ask a local endpoint for short-lived tokens, and Storage and
PostgreSQL accept those tokens in place of a secret.

`ManagedIdentityCredential` is used on purpose, not `DefaultAzureCredential`.
The latter tries a chain (environment variables, workload identity, the Azure
CLI, a developer's `az login`, ...): convenient on a laptop, but it is slow to
fall through and it can silently authenticate as whoever happens to be logged
in. A production container should use the identity assigned to it or fail
loudly -- never discover another one.

The credential factory is a module-level function so tests replace it with a
fake and never touch the network.
"""

from collections.abc import Awaitable, Callable

from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import ManagedIdentityCredential

from app.core.config import Settings

#: Token scope for Azure Database for PostgreSQL (Microsoft Entra authentication).
POSTGRES_TOKEN_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


def create_credential(settings: Settings) -> AsyncTokenCredential:
    """Creates the managed-identity credential for this deployment.

    Args:
        settings: Supplies `azure_client_id`, the user-assigned identity. With
            `None` the platform's system-assigned identity is used.

    Returns:
        A credential whose `close()` must be awaited at shutdown.
    """
    return ManagedIdentityCredential(client_id=settings.azure_client_id)


def postgres_token_provider(credential: AsyncTokenCredential) -> Callable[[], Awaitable[str]]:
    """Builds the callable asyncpg uses as the connection *password*.

    asyncpg calls it (awaiting the result if it is awaitable) every time it
    opens a connection, so a pooled replica that lives for days always logs
    in with a fresh token: `azure-identity` caches tokens and renews them
    shortly before expiry, so most calls cost nothing.

    Args:
        credential: The managed-identity credential to ask.

    Returns:
        An async function returning the raw token string.
    """

    async def provide() -> str:
        token: AccessToken = await credential.get_token(POSTGRES_TOKEN_SCOPE)
        return token.token

    return provide
