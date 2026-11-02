"""Clean the genre labels already stored in `books.categories`.

Open Library subjects brought machine tags (`award:hugo_award=1966`), call
numbers, list names, characters and duplicate spellings into the column.
New data is cleaned where it enters (see `app/services/sources/categories.py`);
this rewrites the rows stored before that, with the same function.

Data only: no schema change. A row whose cleaned list is empty gets NULL,
which is how the column represents "no genres". The downgrade does nothing:
the dropped labels were noise and cannot be reconstructed.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.services.sources.categories import clean_categories

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, categories FROM books WHERE json_typeof(categories) = 'array'")
    ).fetchall()
    update = sa.text("UPDATE books SET categories = CAST(:value AS json) WHERE id = :id")
    for book_id, categories in rows:
        if not isinstance(categories, list):
            continue
        cleaned = clean_categories(str(label) for label in categories)
        if cleaned == categories:
            continue
        conn.execute(update, {"id": book_id, "value": json.dumps(cleaned) if cleaned else None})

    # Each stored profile vector embeds its book's genre labels ("Genres: ...")
    # and is never recomputed while it exists, so the old, noisy labels would
    # keep steering recommendations. They are derived data, rebuilt on the
    # next recommendations request (one local embedding per book).
    conn.execute(sa.text("DELETE FROM book_profiles"))


def downgrade() -> None:
    # Irreversible by nature: the removed labels were noise. Nothing to undo.
    pass
