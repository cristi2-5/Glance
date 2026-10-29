"""pgvector: `book_chunks` and `book_profiles` replace the two vector-store collections.

Enables the `vector` extension first. On the dev/test image
(`pgvector/pgvector:pg17`) any database owner can do that; on Azure Database
for PostgreSQL Flexible Server `VECTOR` must first be added to the
`azure.extensions` server parameter, or `CREATE EXTENSION` fails (step 7).

Both tables cascade from `books`, and `book_chunks` also from
`text_sources`, so a deleted book or a refreshed source takes its chunks
with it. There is **no HNSW/IVFFlat index** on purpose: pgvector filters
after the approximate scan, which would silently drop a filtered book's
rows. The reasoning lives in `app/models/vector.py`.

`downgrade` drops the tables but not the extension: other objects
(or a later migration) may use it, and dropping it is not part of undoing
*this* schema.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "book_chunks",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("text_source_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("heading", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("license", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["text_source_id"], ["text_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_book_chunks_book_id"), "book_chunks", ["book_id"], unique=False)

    op.create_table(
        "book_profiles",
        sa.Column("book_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("document", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("book_id"),
    )


def downgrade() -> None:
    op.drop_table("book_profiles")
    op.drop_index(op.f("ix_book_chunks_book_id"), table_name="book_chunks")
    op.drop_table("book_chunks")
