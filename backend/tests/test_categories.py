"""Tests for `clean_categories` and the places that apply it."""

import json

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.init_db import alembic_config
from app.models.book import SourceName
from app.services.data_fetcher import BookDataFetcher
from app.services.sources.base import BookMetadata, SourceResult
from app.services.sources.categories import clean_categories
from tests.fakes import FakeContentSource
from tests.test_migrations import _run, database_url  # noqa: F401


@pytest.fixture
async def db(db_session_factory: async_sessionmaker[AsyncSession]) -> AsyncSession:
    """A session on the isolated test database."""
    return db_session_factory()


@pytest.mark.parametrize(
    "junk",
    [
        "award:hugo_award=1966",
        "award:retro_hugo",
        "nyt:trade-fiction-paperback=2020-09-27",
        "collection:otherwise_tiptree_award=retrohonor",
        "Series:Off-Campus",
        "Ps1744.g57 y4534 1993",
        "Reading Level-Grade 10",
        "Large type books",
        "Open Library Staff Picks",
        "New York Times bestseller",
        "New York Times reviewed",
        "Long Now Manual for Civilization",
        "1000blackgirlbooks",
        "Hari Seldon (Fictitious character)",
        "Dune (Imaginary place)",
        "Dune (Lieu imaginaire)",
        "Dune (Lugar imaginario)",
        "Dune (imaginary place), fiction",
    ],
)
def test_junk_is_dropped(junk: str) -> None:
    assert clean_categories([junk, "Ecology"]) == ["Ecology"]


@pytest.mark.parametrize(
    "label",
    [
        "Science fiction",
        "Fantasy",
        "Dystopias",
        "Ecology",
        "Psychological fiction",
        "Human-alien encounters",
        "Life on other planets",
        "Prophecies",
        "Fiction: short stories",
        "World War, 1939-1945",
        "Catch-22",
    ],
)
def test_real_genres_survive(label: str) -> None:
    assert clean_categories([label]) == [label]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FICTION / Science Fiction / General", "Science Fiction"),
        # Multi-level BISAC keeps the genre and one qualifier, never a
        # qualifier alone ("Epic", "Regency", "Dogs" name nothing).
        ("FICTION / Fantasy / Epic", "Fantasy, epic"),
        ("FICTION / Historical / General", "Historical"),
        ("FICTION / Romance / Historical / Regency", "Romance, historical"),
        ("Juvenile Fiction / Animals / Dogs", "Animals, dogs"),
        ("Fiction, science fiction, general", "Science fiction"),
        ("Fiction, fantasy, epic", "Fantasy, epic"),
        ("Married women--psychology--fiction", "Married women"),
        ("SCIENCE FICTION", "Science fiction"),
        ("  Science   fiction ", "Science fiction"),
        ("fantasy", "Fantasy"),
    ],
)
def test_labels_are_normalised(raw: str, expected: str) -> None:
    assert clean_categories([raw]) == [expected]


def test_spelling_variants_are_deduplicated_first_seen_wins() -> None:
    labels = ["Science fiction", "Science Fiction", "Science-fiction", "science fiction"]
    assert clean_categories(labels) == ["Science fiction"]
    assert clean_categories(["fantasy", "Fantasy"]) == ["Fantasy"]
    assert clean_categories(["Science-fiction", "Science fiction"]) == ["Science-fiction"]


def test_accents_fold_for_deduplication() -> None:
    assert clean_categories(["Ciencia-ficción", "Ciencia ficcion"]) == ["Ciencia-ficción"]


def test_generic_labels_are_kept_only_when_alone() -> None:
    assert clean_categories(["Fiction", "General", "Dystopias"]) == ["Dystopias"]
    assert clean_categories(["Fiction"]) == ["Fiction"]
    assert clean_categories(["JUVENILE NONFICTION"]) == ["Juvenile nonfiction"]
    assert clean_categories(["Fiction / General"]) == ["Fiction"]


def test_cap_and_order() -> None:
    labels = [f"Genre {i}" for i in range(12)]
    assert clean_categories(labels) == labels[:8]
    assert clean_categories(labels, limit=3) == labels[:3]


def test_empty_and_all_junk() -> None:
    assert clean_categories([]) == []
    assert clean_categories(["award:x=1", "", "   "]) == []


def test_real_list_from_the_dev_database() -> None:
    raw = [
        "award:hugo_award=1966",
        "Science fiction",
        "Science Fiction",
        "Science-fiction",
        "FICTION / Science Fiction / General",
        "Fiction, science fiction, general",
        "Dune (Imaginary place)",
        "Ecology",
        "Prophecies",
        "nyt:trade-fiction-paperback=2020-09-27",
        "Reading Level-Grade 10",
        "Fiction",
        "General",
        "Married women--psychology--fiction",
        "Ciencia-ficción",
        "Hari Seldon (Fictitious character)",
        "Open Library Staff Picks",
        "Dystopias",
    ]
    assert clean_categories(raw) == [
        "Science fiction",
        "Ecology",
        "Prophecies",
        "Married women",
        "Ciencia-ficción",
        "Dystopias",
    ]


@pytest.mark.parametrize(
    "labels",
    [
        ["Fiction", "General"],
        ["FICTION / Science Fiction / General", "Fiction, fantasy, epic", "fantasy"],
        ["Married women--psychology--fiction", "award:x=1", "Science-fiction"],
        [f"Genre {i}" for i in range(20)],
        [],
    ],
)
def test_cleaning_is_idempotent(labels: list[str]) -> None:
    once = clean_categories(labels)
    assert clean_categories(once) == once


async def test_merge_keeps_googles_label_and_drops_cross_source_duplicates(
    db: AsyncSession,
) -> None:
    google = FakeContentSource(
        SourceName.GOOGLE_BOOKS,
        SourceResult(
            source=SourceName.GOOGLE_BOOKS,
            metadata=BookMetadata(
                title="Dune", author="Frank Herbert", categories=["Science fiction", "Fiction"]
            ),
        ),
    )
    open_library = FakeContentSource(
        SourceName.OPEN_LIBRARY,
        SourceResult(
            source=SourceName.OPEN_LIBRARY,
            metadata=BookMetadata(categories=["Science-fiction", "Ecology", "award:hugo=1966"]),
        ),
    )
    fetcher = BookDataFetcher(
        sources=[google, open_library], settings=get_settings(), cover_fallback=None
    )

    book = await fetcher.fetch(db, "Dune", "Frank Herbert")

    assert book.categories == ["Science fiction", "Ecology"]


def test_migration_0004_cleans_stored_categories(database_url: str) -> None:  # noqa: F811
    cfg = alembic_config(database_url)
    command.upgrade(cfg, "0003")
    rows = {
        "a": json.dumps(["award:hugo_award=1966", "Science Fiction", "Science-fiction", "Fiction"]),
        "b": json.dumps(["award:x=1", "Reading Level-Grade 10"]),
        "c": None,
        "d": json.dumps(["Dystopias"]),
    }

    async def _seed() -> None:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                for key, value in rows.items():
                    await conn.execute(
                        text(
                            "INSERT INTO books (normalized_key, title, categories,"
                            " metadata_found, created_at, updated_at)"
                            " VALUES (:k, :k, CAST(:v AS json), false, now(), now())"
                        ),
                        {"k": key, "v": value},
                    )
        finally:
            await engine.dispose()

    _run(_seed())
    command.upgrade(cfg, "head")

    async def _read() -> dict[str, object]:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT title, categories FROM books"))
                return {title: cats for title, cats in result}
        finally:
            await engine.dispose()

    cleaned = _run(_read())
    assert cleaned["a"] == ["Science Fiction"]
    assert cleaned["b"] is None
    assert cleaned["c"] is None
    assert cleaned["d"] == ["Dystopias"]
