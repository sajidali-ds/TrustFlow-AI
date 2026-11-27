from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

_STOPWORDS = {
    "the", "a", "an", "is", "are", "of", "to", "and", "or", "in", "on",
    "for", "must", "be", "if", "it", "as", "by", "with", "than", "at",
}

_DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "policies.json"
)


def _tokenize(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _load_policies() -> List[Dict[str, Any]]:
    with open(_DATA_PATH, "r") as f:
        return json.load(f)


def retrieve_policies(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """Return the top_k policy documents most relevant to the query."""
    policies = _load_policies()
    q_tokens = _tokenize(query)
    scored = []
    for doc in policies:
        doc_tokens = _tokenize(doc["title"] + " " + doc["text"])
        overlap = len(q_tokens & doc_tokens)
        if overlap:
            scored.append((overlap, doc))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [doc for _, doc in scored[:top_k]] or policies[:top_k]
