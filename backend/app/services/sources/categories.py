"""Cleaning of catalog genre/subject labels ("categories").

Open Library subjects are user-edited and mix real genres with machine tags
(`award:hugo_award=1966`), library metadata (call numbers, reading levels),
list/marketing labels, characters and places, and the same genre spelled
many ways. Google Books uses BISAC paths (`FICTION / Science Fiction /
General`). Those labels feed the book screen's tags, the profile's favourite
genres, recommendation discovery seeds, the profile vector text and a RAG
passage, so they are cleaned once, where they enter the system: in each
source, in the cross-source merge, in candidate persistence and (for data
already stored) in migration 0004.

Known, accepted gap: non-English duplicates of a genre (`Ciencia-ficción`,
`Roman américain`) are neither translated nor language-detected. Open
Library lists them late, so they mostly fall past the cap.
"""

import re
import unicodedata
from collections.abc import Iterable

#: Catalog labels too broad to be a discovery query or a tag. Google Books
#: tags most novels `Fiction` and most children's books `Juvenile Fiction` —
#: top-level BISAC headings that describe the shelf, not the book. Folded
#: and matched exactly, so "Science fiction" is untouched.
GENERIC_SUBJECTS = frozenset(
    {
        "fiction",
        "juvenile fiction",
        "juvenile nonfiction",
        "nonfiction",
        "general",
        "literary collections",
        "literary criticism",
        "books",
    }
)

#: Default maximum number of labels kept per book.
DEFAULT_LIMIT = 8

# `award:hugo_award=1966`, `nyt:...`, `Series:Off-Campus`: a key, a colon and
# a value with no space. "Fiction: short stories" has a space after the colon
# and is a real label, so it does not match.
_MACHINE_TAG = re.compile(r"^[A-Za-z_]+:\S")

# Library call numbers such as `Ps1744.g57 y4534 1993`: 1-3 letters, digits,
# then a dotted cutter.
_CALL_NUMBER = re.compile(r"^[A-Z]{1,3}\d+(\.[A-Za-z0-9]+)+", re.IGNORECASE)

# Hashtag-like list names (`1000blackgirlbooks`): one token, letters and
# digits only, with a run of letters. Pure years/numbers are not matched.
_HASHTAG = re.compile(r"^(?=[A-Za-z0-9]+$)(?=.*\d)(?=.*[A-Za-z]{3})")

# Library/marketing labels, matched case-insensitively as a prefix.
_JUNK_PREFIXES = (
    "reading level",
    "large type",
    "accessible book",
    "protected daisy",
    "in library",
    "lending library",
    "open library staff picks",
    "new york times bestseller",
    "new york times reviewed",
    "long now manual for civilization",
    "special collections",
    "translations into",
)

# Characters and places are subjects of the story, not genres. Open Library
# uses the English wording and translations of it, anywhere in the label
# ("Dune (imaginary place), fiction").
_NOT_A_GENRE = re.compile(
    r"\((?:fictitious character|imaginary place|fictional character"
    r"|personnage fictif|lieu imaginaire|lugar imaginario|personaje ficticio)\)",
    re.IGNORECASE,
)

_PATH_SEPARATOR = " / "


def _is_generic(label: str) -> bool:
    """Whether a label is a generic shelf heading such as "Fiction"."""
    return label.strip().casefold() in GENERIC_SUBJECTS


def _fold(label: str) -> str:
    """Key under which spellings of one genre compare equal.

    Folds case, accents, hyphens/underscores and runs of whitespace, so
    `Science-fiction` equals `Science fiction`.
    """
    decomposed = unicodedata.normalize("NFKD", label)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().replace("-", " ").replace("_", " ").split())


def _is_junk(label: str) -> bool:
    """Whether a label is metadata or noise rather than a genre."""
    if _MACHINE_TAG.match(label) or _CALL_NUMBER.match(label) or _HASHTAG.match(label):
        return True
    if _NOT_A_GENRE.search(label):
        return True
    lowered = label.casefold()
    return lowered.startswith(_JUNK_PREFIXES)


def _normalise(label: str) -> str:
    """Reduces a path-like or shouted label to the genre it names.

    - `A / B / C` (BISAC): the first non-generic part, narrowed by the
      next one when there is one, so `FICTION / Science Fiction / General`
      becomes `Science Fiction` and `FICTION / Fantasy / Epic` becomes
      `Fantasy, epic` (never a bare `Epic`).
    - `Fiction, fantasy, epic`: when the first comma part is generic the
      label is a path too; its non-generic parts are kept, in order
      (`fantasy, epic`). Otherwise commas are left alone, since
      `World War, 1939-1945` is one subject.
    - `Married women--psychology--fiction`: subdivisions are dropped.
    - ALL-CAPS becomes sentence case.

    If nothing non-generic is left, the first part is returned (generic),
    so the caller can still keep it when it is all the book has.
    """
    parts = [p.strip() for p in label.split(_PATH_SEPARATOR) if p.strip()]
    if not parts:
        return ""
    specific = [p for p in parts if not _is_generic(p)]
    if not specific:
        label = parts[0]
    elif len(specific) == 1:
        label = specific[0]
    else:
        # The first specific part is the genre, the next one narrows it;
        # either alone loses something ("Epic" from `Fantasy / Epic` names
        # nothing). Deeper levels are dropped: two is enough for a tag.
        narrower = specific[1]
        label = f"{specific[0]}, {narrower[:1].lower()}{narrower[1:]}"

    label = label.split("--")[0].strip()

    comma_parts = [p.strip() for p in label.split(",") if p.strip()]
    if len(comma_parts) > 1 and _is_generic(comma_parts[0]):
        kept = [p for p in comma_parts if not _is_generic(p)]
        label = ", ".join(kept) if kept else comma_parts[0]

    if label.isupper() and len(label) > 4:
        label = label.capitalize()
    elif label[:1].islower() and label.islower():
        # Fully lower-case input ("fantasy") reads better as a sentence.
        label = label[:1].upper() + label[1:]
    return label


def clean_categories(labels: Iterable[str], limit: int = DEFAULT_LIMIT) -> list[str]:
    """Cleans a list of catalog genre labels.

    Rules, in order: whitespace is collapsed; metadata and noise are dropped
    (machine tags, call numbers, hashtags, library/marketing labels,
    characters and places); path-like and ALL-CAPS labels are normalised to
    the genre they name; duplicates are removed on a folded key, the first
    form seen winning (Google Books runs first in a merge, so its cleaner
    labels win); generic shelf labels such as "Fiction" are dropped unless
    nothing more specific remains; finally the list is capped at `limit`.

    Pure and idempotent: `clean_categories(clean_categories(x)) ==
    clean_categories(x)`. Non-English duplicates are not translated.

    Args:
        labels: Raw labels, in priority order.
        limit: Maximum number of labels to return.

    Returns:
        The cleaned labels, in first-seen order.
    """
    specific: dict[str, str] = {}
    generic: dict[str, str] = {}

    for raw in labels:
        label = " ".join(str(raw).split())
        if not label or _is_junk(label):
            continue
        label = _normalise(label)
        if not label or _is_junk(label):
            continue
        key = _fold(label)
        bucket = generic if _is_generic(label) else specific
        bucket.setdefault(key, label)

    chosen = specific or generic
    return list(chosen.values())[:limit]
