"""Cloud step 5: the production guard, docs switched off, and CORS defaults."""

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import DEFAULT_JWT_SECRET, Settings
from app.core.exceptions import InsecureConfiguration
from app.main import create_app

SAFE_JWT_SECRET = "x" * 48


def _settings(**overrides: Any) -> Settings:
    """Settings that pass the production guard, with `overrides` applied.

    `_env_file=None` keeps the developer's own `backend/.env` out of the tests.
    """
    values: dict[str, Any] = {
        "app_env": "production",
        "jwt_secret_key": SAFE_JWT_SECRET,
        "cors_origins": [],
        "azure_storage_account_name": "glancestore",
        "azure_client_id": "00000000-0000-0000-0000-000000000000",
        "database_url": "postgresql+asyncpg://glance-identity@db.example.postgres.azure.com/glance",
        "database_auth": "entra",
        "groq_api_key": "gsk_test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_a_fully_configured_production_passes() -> None:
    settings = _settings()

    assert settings.production_problems() == []
    settings.validate_for_production()  # does not raise


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"jwt_secret_key": DEFAULT_JWT_SECRET}, "JWT_SECRET_KEY is the default"),
        ({"jwt_secret_key": "short"}, "shorter than 32"),
        ({"cors_origins": ["*"]}, "CORS_ORIGINS"),
        (
            {"azure_storage_account_name": None},  # falls back to Azurite's connection string
            "AZURE_STORAGE_ACCOUNT_NAME is not set",
        ),
        (
            {
                "database_url": "postgresql+asyncpg://glance:glance@db.example.com/glance",
                "database_auth": "password",
            },
            "development credentials",
        ),
        (
            {"database_url": "postgresql+asyncpg://identity:secret@db.example.com/glance"},
            "still contains a password",
        ),
        ({"groq_api_key": None}, "GROQ_API_KEY"),
    ],
)
def test_each_development_value_is_refused(overrides: dict[str, Any], fragment: str) -> None:
    problems = _settings(**overrides).production_problems()

    assert any(fragment in problem for problem in problems), problems


def test_a_database_password_is_refused_even_a_real_one() -> None:
    settings = _settings(
        database_url="postgresql+asyncpg://glance:S3cure-and-long@db.example.com/glance",
        database_auth="password",
    )

    problems = settings.production_problems()

    assert any("DATABASE_AUTH is not 'entra'" in problem for problem in problems), problems


def test_a_storage_connection_string_with_a_real_key_is_refused_in_production() -> None:
    settings = _settings(
        azure_storage_account_name=None,
        azure_storage_connection_string="DefaultEndpointsProtocol=https;AccountName=real;AccountKey=abc;",
    )

    assert any("AZURE_STORAGE_ACCOUNT_NAME" in p for p in settings.production_problems())


@pytest.mark.parametrize(
    "overrides",
    [
        {"container_app_name": "glance-api"},
        {"azure_storage_account_name": "glancestore"},
        {"database_auth": "entra"},
    ],
)
def test_azure_without_app_env_production_is_refused(overrides: dict[str, Any]) -> None:
    settings = Settings(_env_file=None, app_env="development", **overrides)

    with pytest.raises(InsecureConfiguration, match="without APP_ENV=production"):
        settings.validate_for_production()


def test_the_error_lists_every_problem_at_once() -> None:
    settings = _settings(jwt_secret_key=DEFAULT_JWT_SECRET, cors_origins=["*"], groq_api_key=None)

    with pytest.raises(InsecureConfiguration) as caught:
        settings.validate_for_production()

    message = caught.value.message
    assert "JWT_SECRET_KEY" in message and "CORS_ORIGINS" in message and "GROQ_API_KEY" in message


def test_a_laptop_in_development_is_never_refused() -> None:
    Settings(_env_file=None, app_env="development", cors_origins=["*"]).validate_for_production()


def test_cors_allows_no_origin_by_default() -> None:
    assert Settings(_env_file=None).cors_origins == []


def test_the_idle_backoff_must_stay_below_the_liveness_window() -> None:
    with pytest.raises(ValueError, match="QUEUE_WORKER_LOOP_STALE_SECONDS"):
        Settings(
            _env_file=None,
            queue_worker_loop_stale_seconds=10.0,
            queue_idle_backoff_max_seconds=10.0,
        )


async def test_the_production_app_refuses_to_start_on_development_settings() -> None:
    app = create_app(_settings(jwt_secret_key=DEFAULT_JWT_SECRET))

    with pytest.raises(InsecureConfiguration):
        async with app.router.lifespan_context(app):
            pass


async def test_docs_are_off_in_production_and_on_in_development() -> None:
    production = create_app(_settings())
    development = create_app(Settings(_env_file=None))

    for app, expected in ((production, 404), (development, 200)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            for path in ("/docs", "/redoc", "/openapi.json"):
                assert (await ac.get(path)).status_code == expected, (path, expected)
            assert (await ac.get("/health")).status_code == 200
