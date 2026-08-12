"""Title matching shared by the three `ContentSource` implementations.

Extracted because all three had the same `token_set_ratio` floor copied
into them, and all three were wrong in the same way.

**Why not `token_set_ratio`.** It scores on the *intersection* of the two
token sets, so a candidate that merely contains the query scores 100:

    token_set_ratio("dune", "heretics of dune") == 100.0
    token_set_ratio("it",   "it ends with us")  == 100.0

Every sequel, companion volume and unrelated book whose title happens to
contain the query cleared the similarity floor. Open Library really did
return *Heretics of Dune* as the match for "Dune", and its cover,
description and sixteen subjects were then merged into Dune's cache entry
and stored as Dune's RAG passages. Attaching another book's content is
strictly worse than reporting nothing — it is confidently wrong, and it
poisons the corpus Module 5 retrieves from.

`token_sort_ratio` compares the full token sequences instead, so extra
words in the candidate cost score. On its own that over-rejects, because
catalogs decorate titles with subtitles and edition markers that carry no
meaning — "Dune: A Novel", "Dune (Movie Tie-In)". So each title is
reduced to a small set of variants (as-is, parentheticals removed,
subtitle dropped) and the best pairing wins. A genuine edition of the
right book matches through one of its variants; a different book in the
same series does not match through any.
"""

import re

from rapidfuzz import fuzz

# Parenthesised or bracketed decoration: "(Movie Tie-In)", "[Illustrated]".
_DECORATION = re.compile(r"[(\[][^)\]]*[)\]]")

# Separators a catalog uses to introduce a subtitle. Requires trailing
# whitespace so that "Vol.2" or "Dr. No" are not split mid-token.
_SUBTITLE = re.compile(r"\s*[:;,.]\s+|\s+-\s+")

# Below this, the candidate is treated as a different book. Calibrated so
# that "Dune" accepts "Dune: A Novel" (100) and rejects "Dune Messiah" (50)
# and "Heretics of Dune" (40).
MIN_TITLE_SIMILARITY = 75.0


def _variants(title: str) -> set[str]:
    """Reduces a title to the forms worth comparing.

    Args:
        title: A raw title, from either the cover or a catalog.

    Returns:
        The case-folded title, the same with parenthesised decoration
        removed, and the part before any subtitle separator. Empty strings
        are dropped.
    """
    folded = title.casefold().strip()
    undecorated = _DECORATION.sub(" ", folded).strip()
    stem = _SUBTITLE.split(undecorated, 1)[0].strip()
    return {value for value in (folded, undecorated, stem) if value}


def title_similarity(query: str, candidate: str) -> float:
    """Scores how well a catalog title matches the title being looked for.

    Args:
        query: The title being searched for, as read from the cover.
        candidate: The title a catalog returned.

    Returns:
        A score from 0 to 100 — the best over every pairing of the two
        titles' variants. Compare against `MIN_TITLE_SIMILARITY`.
    """
    query_variants = _variants(query)
    candidate_variants = _variants(candidate)
    if not query_variants or not candidate_variants:
        return 0.0

    return max(fuzz.token_sort_ratio(q, c) for q in query_variants for c in candidate_variants)
