"""Scan photos: `jobs.photo_blob` and `library_entries.photo_blob`.

Cloud step 4 keeps the (normalised) photo of every scan in a private Blob
container. These two nullable columns hold the blob *names*:

- `jobs.photo_blob` is how the queue worker finds the image to analyse, and
  what a correction moves to the corrected book's entry.
- `library_entries.photo_blob` is the reader's latest photo of that book,
  shown in the app and deleted together with the entry.

Both are nullable with no default, so existing rows (scanned before photos
were kept) simply have none, and the migration is a pure metadata change.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("photo_blob", sa.Text(), nullable=True))
    op.add_column("library_entries", sa.Column("photo_blob", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("library_entries", "photo_blob")
    op.drop_column("jobs", "photo_blob")
