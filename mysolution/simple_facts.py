"""
Step 9: small, standalone facts that don't fit the rate-table model --
currently just the routing number. Kept separate from extraction.py /
rates_table.py since this is a single fixed value, not something with
tiers, terms, or history to resolve.
"""

from __future__ import annotations

import re
from typing import Optional

_ROUTING_RE = re.compile(r"routing number\D{0,15}(\d{9})", re.IGNORECASE)
_ROUTING_QUESTION_RE = re.compile(r"\brouting number\b", re.IGNORECASE)


def find_routing_number(page_text: str) -> Optional[str]:
    m = _ROUTING_RE.search(page_text)
    return m.group(1) if m else None


def is_routing_question(question: str) -> bool:
    return bool(_ROUTING_QUESTION_RE.search(question))
