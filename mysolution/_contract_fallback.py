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
