"""
Fallback definitions for the harness contract types.

solution.py tries `from harness.contract import Answer, Source, Conflict`
first (the real shapes from the kit). If that import fails -- e.g. while
developing this solution before the kit's harness/ folder is dropped in --
it falls back to these equivalent dataclasses so the code still runs and
can be unit-tested standalone.

These mirror the shapes documented in the assignment PDF exactly:

    Answer(
        text=...,
        sources=[Source(url=...)],
        confident=True/False,
        conflict_note=None/str,
    )

    Conflict(
        topic=...,
        values=[...],
        sources=[...],
        resolution=...,
        reason=...,
    )
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Source:
    url: str
    title: Optional[str] = None


@dataclass
class Answer:
    text: str
    sources: List[Source] = field(default_factory=list)
    confident: bool = True
    conflict_note: Optional[str] = None


@dataclass
class Conflict:
    topic: str
    values: List[str]
    sources: List[Source]
    resolution: str
    reason: str
