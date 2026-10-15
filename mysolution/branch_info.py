"""
Step 10: branch hours and addresses.

Two distinct real sources on the site:

1. Lobby/Drive-up hours: NOT on the main contact page widget (that only
   shows "Open - Closes at 5PM", not a real schedule). The actual
   day-by-day schedule appears, repeatedly, inside newsroom articles
   about individual branch openings/renovations, each with a consistent
   "**Lobby**" ... "**Drive-up**" block. Different branches genuinely
   have different schedules (this is a real, intentional discrepancy the
   assignment tests for), so we keep every *distinct* schedule we see
   rather than collapsing them into one.

2. Branch addresses: the contact page's location-finder widget lists
   "### Name" / "<Type>" / "<Address>" blocks. The heading is almost
   always the generic "Dupaco Credit Union" for every branch -- the only
   way to find "the Galena branch" is to search the *address* text for
   the city name, not the heading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

_LOBBY_BLOCK_RE = re.compile(
    r"\*\*Lobby\*\*\s*(.*?)\s*\*\*Drive-up\*\*\s*(.*?)(?:\n\n|$)",
    re.IGNORECASE | re.DOTALL,
)

_LOCATION_BLOCK_RE = re.compile(
    r"###\s*(.+?)\n\n(.+?)\n\n([^\n]+(?:,\s*[A-Z]{2},?\s*\d{5})?)\n\n"
    r"(?:Open\s*[\u00b7\u2022-]\s*Closes at ([^\n]+)\n\n)?"
    r"([^\n#]*)?",
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class BranchLocation:
    name: str
    location_type: str
    address: str
    closes_at: Optional[str]
    source_url: str


def extract_hours_schedules(page_text: str) -> List[str]:
    """Returns schedule strings like 'Lobby: Monday-Thursday 8:30 a.m.-5
    p.m., Friday ..., Saturday ...; Drive-up: Monday-Friday 8 a.m.-6
    p.m.'. One page can (rarely) mention more than one branch's hours."""
    out = []
    for m in _LOBBY_BLOCK_RE.finditer(page_text):
        lobby, driveup = _clean(m.group(1)), _clean(m.group(2))
        if lobby and driveup:
            out.append(f"Lobby: {lobby} Drive-up: {driveup}")
    return out


def extract_locations(page_text: str, url: str) -> List[BranchLocation]:
    out = []
    for m in _LOCATION_BLOCK_RE.finditer(page_text):
        name, loc_type, address, closes_at, features = m.groups()
        name, loc_type, address = _clean(name), _clean(loc_type), _clean(address)
        # Only keep entries that look like a real street address (filters
        # out unrelated "### Heading" blocks elsewhere on the page that
        # don't match this specific location-card shape).
        if not re.search(r"\d{5}", address):
            continue
        out.append(
            BranchLocation(
                name=name,
                location_type=loc_type,
                address=address,
                closes_at=_clean(closes_at) if closes_at else None,
                source_url=url,
            )
        )
    return out


def find_location_by_keyword(locations: List[BranchLocation], keyword: str) -> Optional[BranchLocation]:
    """Branches are rarely named anything but 'Dupaco Credit Union' --
    match against the *address* (which has the city) instead."""
    keyword = keyword.lower()
    for loc in locations:
        if keyword in loc.address.lower() and loc.location_type.lower() == "dupaco branch":
            return loc
    return None
