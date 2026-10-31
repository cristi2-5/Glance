"""Cloud step 5: a failed database connection at startup is logged clearly."""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.init_db import init_db


async def test_a_connection_failure_at_startup_is_logged_with_its_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    dead = create_async_engine(
        "postgresql+asyncpg://glance:glance@127.0.0.1:1/glance", poolclass=NullPool
    )
    try:
        with pytest.raises(OSError):
            await init_db(dead)
    finally:
        await dead.dispose()

    out = capsys.readouterr().out
    assert "database_connect_failed" in out and "error_type" in out
