"""Pydantic schemas for the generated summary and its citations.

The contract is deliberately **structured, not prose with footnotes**.
A summary returned as a single string with `[1]` markers would have to be
parsed back apart by the client to make citations tappable, and any
parser would be guessing at the model's formatting. Instead the summary
*is* an ordered list of claims, each carrying the ids of the chunks that
support it, and `text` is derived server-side by joining them. The two
therefore cannot disagree, and "every sentence traces to a chunk" is a
property the type system half-enforces rather than a hope about the
prompt.
"""

from pydantic import BaseModel, ConfigDict, Field


class SummaryClaim(BaseModel):
    """One statement in the summary, with the chunks that support it.

    Attributes:
        text: The claim, as a complete sentence.
        chunk_ids: Ids of the retrieved chunks backing it. Never empty —
            a claim that cited nothing, or cited something that was not
            retrieved, is dropped during verification rather than shown.
    """

    text: str
    chunk_ids: list[str] = Field(min_length=1)


class SourceReview(BaseModel):
    """A cited excerpt, as the client renders it.

    Mirrors the `SourceReview` interface the mobile result screen already
    uses, so the citations drop into the existing UI structure.

    Attributes:
        id: The chunk id. `SummaryClaim.chunk_ids` refers to this.
        source: Which official source produced it (`google_books`,
            `open_library`, `wikipedia`).
        source_title: A readable label for the passage — the section
            heading where there is one, otherwise the kind of passage.
        excerpt: The chunk text.
        url: A link back to the original page, when the source gave one.
        license: The licence the text is published under, so the client
            can honour per-source attribution.
    """

    id: str
    source: str
    source_title: str
    excerpt: str
    url: str | None = None
    license: str | None = None


class BookSummary(BaseModel):
    """The generated summary for one book, with everything needed to cite it.

    Attributes:
        book_id: The book this summary describes. Every chunk behind every
            claim belongs to this book — enforced in `vector_store.py`.
        available: `False` when no summary could be produced, either
            because the book has no passages to retrieve over or because
            every generated claim failed verification. The client then
            falls back to the publisher's blurb.
        text: The summary as prose, derived by joining the claims. Empty
            when `available` is `False`.
        claims: The summary's statements, in reading order.
        reviews: The cited chunks, in first-citation order. Only chunks
            actually referenced by a surviving claim appear here.
        uncovered: Aspects the model was asked about but the retrieved
            passages did not support — reported plainly instead of being
            filled in with plausible invention.
        model: The model that generated it, for provenance.
        generated_at: ISO 8601 timestamp of generation.
    """

    model_config = ConfigDict(protected_namespaces=())

    book_id: int
    available: bool
    text: str = ""
    claims: list[SummaryClaim] = []
    reviews: list[SourceReview] = []
    uncovered: list[str] = []
    model: str | None = None
    generated_at: str | None = None
