"""
Step 2: stores Facts extracted from every page/snapshot and answers two
questions:

  1. resolve(topic_key, as_of) -> "what's the right value for this topic,
     as of this date, when sources disagree?"
  2. best_matching_topics(question) -> "which topic_key(s) does this
     question seem to be about?" (plain keyword overlap -- fast, no
     embeddings, keeps answer() comfortably under the 2s speed target)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

from extraction import Fact

# Lower index = more authoritative for "what's true right now" when two
# pages disagree in the same snapshot (e.g. a stale calculator vs. the
# actual rates page).
PAGE_TYPE_AUTHORITY = ["rates_page", "product", "hours_page", "calculator", "other", "blog", "press"]


def _authority_rank(page_type: str) -> int:
    try:
        return PAGE_TYPE_AUTHORITY.index(page_type)
    except ValueError:
        return len(PAGE_TYPE_AUTHORITY)


def _parse_date(s: str) -> date:
    y, m, d = (int(x) for x in s.split("-"))
    return date(y, m, d)


@dataclass
class Resolved:
    display: str
    source_url: str
    page_type: str
    reason: str


class KnowledgeBase:
    def __init__(self) -> None:
        self._facts: Dict[str, List[Fact]] = defaultdict(list)
        self._latest_date: Optional[date] = None

    def add_facts(self, facts: List[Fact]) -> None:
        for f in facts:
            self._facts[f.topic_key].append(f)
            d = _parse_date(f.snapshot_date)
            if self._latest_date is None or d > self._latest_date:
                self._latest_date = d

    def topic_keys(self) -> List[str]:
        return list(self._facts.keys())

    def resolve(self, topic_key: str, as_of: Optional[str] = None) -> Optional[Resolved]:
        facts = self._facts.get(topic_key, [])
        if not facts:
            return None

        cutoff = _parse_date(as_of) if as_of else self._latest_date
        if cutoff is None:
            return None

        eligible = [f for f in facts if _parse_date(f.snapshot_date) <= cutoff]
        if not eligible:
            return None

        most_recent = max(_parse_date(f.snapshot_date) for f in eligible)
        candidates = [f for f in eligible if _parse_date(f.snapshot_date) == most_recent]

        # De-dup identical displayed values from the same page_type (a
        # value often repeats many times on one long page).
        seen = set()
        uniq = []
        for f in candidates:
            key = (f.display, f.page_type)
            if key not in seen:
                seen.add(key)
                uniq.append(f)
        candidates = uniq

        if len(candidates) == 1:
            winner = candidates[0]
            return Resolved(winner.display, winner.source_url, winner.page_type, "single source")

        candidates.sort(key=lambda f: _authority_rank(f.page_type))
        winner = candidates[0]
        losers = sorted({c.page_type for c in candidates[1:]})
        return Resolved(
            winner.display,
            winner.source_url,
            winner.page_type,
            f"{winner.page_type} is authoritative over {', '.join(losers)}",
        )

    def best_matching_topics(self, question: str, top_k: int = 5) -> List[str]:
        q_tokens = set(_tokenize(question))
        scores: List[Tuple[float, str]] = []
        for topic_key, facts in self._facts.items():
            best = 0.0
            topic_tokens = set(_tokenize(topic_key.replace(":", " ")))
            for f in facts:
                ctx_tokens = set(_tokenize(f.context))
                overlap = len(q_tokens & (ctx_tokens | topic_tokens))
                if overlap > best:
                    best = overlap
            if best > 0:
                scores.append((best, topic_key))
        scores.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scores[:top_k]]


def _tokenize(text: str) -> List[str]:
    import re

    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2]
