"""The `RecommendationState` model — when this reader's candidate pool was last widened.

Recommendations are ranked from the `book_profiles` Chroma collection,
which is cheap: it is a vector average over books the reader liked and one
similarity search. What is *not* cheap is **discovery** — asking Google
Books and Open Library for books in this reader's favourite genres and by
their favourite authors, which is several requests against a quota and one
local embedding pass per book that comes back new.

So discovery must not run on every screen open, and it must not be left to
a fixed interval either. This row records both halves of "is the pool
still right for this reader":

- `refreshed_at` — the clock. Catalogs do not gain new books by the hour.
- `seed` — a fingerprint of the derived preferences the pool was built
  from. Rating a book 5 in a genre the reader had never rated before
  changes what should be discovered *immediately*, and a TTL alone would
  serve the old pool for the rest of the day. Comparing the fingerprint
  catches that at the next request, with no invalidation call anywhere in
  the library's write path — the same shape as Module 5's
  `summary_generated_at` vs `sources_fetched_at` comparison.

**Only the bookkeeping is per user. The candidate pool itself is shared.**
The books discovery persists are ordinary `Book` rows and ordinary vectors:
another reader who likes the same genre ranks against the same pool. What
is private is the profile vector, the exclusions, and the ranking — all
computed per request, none of it stored here.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class RecommendationState(Base):
    """When and for which derived preferences a reader's pool was last widened.

    Attributes:
        id: Unique identifier.
        user_id: The reader. Unique — one row per user, created on their
            first recommendation request.
        seed: Fingerprint of the derived genres and authors that seeded the
            last discovery run. A mismatch against the current preferences
            means the pool was built for a reader this one no longer is.
        refreshed_at: When discovery last actually reached the catalogs.
            Left untouched when every source was unreachable, so a transient
            outage is retried at the next request rather than being recorded
            as a successful run — the same rule `BookDataFetcher` applies to
            `sources_fetched_at`.
        complete: Whether *every* query of that run got an answer. A run
            during which a catalog went down still widens the pool and is
            worth keeping, but it is not the run we would have made, so it
            expires on `recommendation_degraded_ttl_hours` instead of the
            full TTL. Without this the flag would be binary — "we reached
            something" — and one bad minute would pin a half-built pool
            for a day.

            Defaults to **false**, which is what a row predating this
            column gets when `init_db` reconciles it in. That is the cheap
            direction to be wrong in: an unknown run treated as degraded
            costs one extra discovery pass, while an unknown run treated
            as clean pins a possibly half-built pool for a day — the exact
            failure this column exists to prevent, silently reintroduced
            by its own backfill. The service always assigns it explicitly.
    """

    __tablename__ = "recommendation_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    seed: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
