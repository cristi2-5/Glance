"""baseline: the schema as of Module 6b, plus the Postgres-safe column types.

Generated from the models against an empty database, then reviewed by hand:
the one boolean server default is written `sa.false()` (rendered as `0` on
SQLite and `false` on PostgreSQL, where `DEFAULT 0` on a boolean is a type
error), and the external free-text columns are `Text` rather than bounded
`String(n)`. Dialect-neutral on purpose; the drift test in
`tests/test_migrations.py` keeps it in step with the models.

Databases created before Alembic existed are brought under it with
`alembic stamp head` — only once the previous code has opened them, since its
column reconciliation is what brought them up to this schema.

Revision ID: 0001
Revises:
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "books",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("normalized_key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("categories", sa.JSON(), nullable=True),
        sa.Column("cover_url", sa.Text(), nullable=True),
        sa.Column("isbn_13", sa.String(length=20), nullable=True),
        sa.Column("isbn_10", sa.String(length=20), nullable=True),
        sa.Column("average_rating", sa.Float(), nullable=True),
        sa.Column("ratings_count", sa.Integer(), nullable=True),
        sa.Column("metadata_found", sa.Boolean(), nullable=False),
        sa.Column("sources_fetched_at", sa.DateTime(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("summary_generated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("books", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_books_author"), ["author"], unique=False)
        batch_op.create_index(batch_op.f("ix_books_isbn_13"), ["isbn_13"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_books_normalized_key"), ["normalized_key"], unique=True
        )
        batch_op.create_index(batch_op.f("ix_books_title"), ["title"], unique=False)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_email"), ["email"], unique=True)

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_jobs_user_id"), ["user_id"], unique=False)

    op.create_table(
        "library_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("scan_count", sa.Integer(), nullable=False),
        sa.Column("first_scanned_at", sa.DateTime(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("rated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)", name="ck_library_rating_range"
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "book_id", name="uq_library_user_book"),
    )
    with op.batch_alter_table("library_entries", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_library_entries_book_id"), ["book_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_library_entries_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_library_entries_user_id"), ["user_id"], unique=False)
        batch_op.create_index("ix_library_user_updated", ["user_id", "updated_at"], unique=False)

    op.create_table(
        "recommendation_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Text(), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(), nullable=True),
        sa.Column("complete", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("recommendation_states", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_recommendation_states_user_id"), ["user_id"], unique=True
        )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_refresh_tokens_token_hash"), ["token_hash"], unique=True
        )

    op.create_table(
        "text_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("heading", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("license", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("text_sources", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_text_sources_book_id"), ["book_id"], unique=False)
        batch_op.create_index("ix_text_sources_book_kind", ["book_id", "kind"], unique=False)

    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("library_entry_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["library_entry_id"], ["library_entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("journal_entries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_journal_entries_library_entry_id"), ["library_entry_id"], unique=False
        )
        batch_op.create_index(
            "ix_journal_entry_created", ["library_entry_id", "created_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("journal_entries", schema=None) as batch_op:
        batch_op.drop_index("ix_journal_entry_created")
        batch_op.drop_index(batch_op.f("ix_journal_entries_library_entry_id"))

    op.drop_table("journal_entries")
    with op.batch_alter_table("text_sources", schema=None) as batch_op:
        batch_op.drop_index("ix_text_sources_book_kind")
        batch_op.drop_index(batch_op.f("ix_text_sources_book_id"))

    op.drop_table("text_sources")
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_refresh_tokens_token_hash"))

    op.drop_table("refresh_tokens")
    with op.batch_alter_table("recommendation_states", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_recommendation_states_user_id"))

    op.drop_table("recommendation_states")
    with op.batch_alter_table("library_entries", schema=None) as batch_op:
        batch_op.drop_index("ix_library_user_updated")
        batch_op.drop_index(batch_op.f("ix_library_entries_user_id"))
        batch_op.drop_index(batch_op.f("ix_library_entries_status"))
        batch_op.drop_index(batch_op.f("ix_library_entries_book_id"))

    op.drop_table("library_entries")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jobs_user_id"))

    op.drop_table("jobs")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_email"))

    op.drop_table("users")
    with op.batch_alter_table("books", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_books_title"))
        batch_op.drop_index(batch_op.f("ix_books_normalized_key"))
        batch_op.drop_index(batch_op.f("ix_books_isbn_13"))
        batch_op.drop_index(batch_op.f("ix_books_author"))

    op.drop_table("books")
