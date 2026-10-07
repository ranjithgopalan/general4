"""
Graph lane query expansion — domain vocabulary synonym injection (docs/20 §8).

Before the graph seed search, any canonical domain term found in the query is augmented
with its configured aliases from ``GRAPH_DOMAIN_VOCAB_JSON``.  This improves graph lane
recall when the user's phrasing ("writing system", "policy platform") differs from the
KB's canonical label ("WOD", "PEGA").

The expanded string is used ONLY by the graph lane; BM25 and dense lanes receive the
original masked query unchanged, so this never inflates vector or full-text results.
"""

from __future__ import annotations

import re

from app.config.settings import get_settings


def expand_query(query: str, vocab: dict[str, list[str]] | None = None) -> str:
    """Append alias tokens for every known domain term found in *query*.

    Aliases already present in the query are not duplicated.  The original
    query text is never modified — aliases are appended so the original token
    scoring from the inverted index is preserved and aliases only add recall.

    Args:
        query: The (possibly PII-masked) user query.
        vocab: Optional override; defaults to ``Settings.GRAPH_DOMAIN_VOCAB``.

    Returns:
        The query with alias tokens appended, or the original query unchanged
        when no vocab terms are detected or ``vocab`` is empty.
    """
    if not query:
        return query
    if vocab is None:
        vocab = get_settings().GRAPH_DOMAIN_VOCAB
    if not vocab:
        return query

    ql = query.lower()
    extras: list[str] = []
    for term, aliases in vocab.items():
        if re.search(r"\b" + re.escape(term.lower()) + r"\b", ql):
            for alias in aliases:
                if alias.lower() not in ql:
                    extras.append(alias)

    if not extras:
        return query
    return query + " " + " ".join(extras)
