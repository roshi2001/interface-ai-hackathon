"""
Step 2: Fact extraction for rates, APY, and fees.

Calibrated against real Dupaco pages, e.g.:
    "- APR1: 15.50%\u201321.00%"          (footnote digit glued to label, en-dash range)
    "6-month certificate APY: 2.95%"   (plain, from the selftest kit)
    "0.15% (dividend rate and APY) for $10,000-$24,999.99"  (tiered savings)

Deliberately regex + heuristics, not an LLM call: this runs once per page
during ingest() (ingest has a 30-minute budget for all 5 snapshots), and
answer() must stay fast (<=2s) for full speed credit, so all the "thinking"
has to happen up front, not per-question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class Fact:
    category: str  # "rate" | "apy" | "fee"
    qualifiers: Tuple[str, ...]  # e.g. ("auto_loan", "60mo")
    low: float  # lower (or only) numeric value, e.g. 15.50
    high: Optional[float]  # upper bound of a range, e.g. 21.00, else None
    display: str  # human-readable value, e.g. "15.50%\u201321.00% APR"
    snapshot_date: str  # ISO date of the crawl that saw this page
    source_url: str
    page_type: str
    context: str

    @property
    def topic_key(self) -> str:
        return f"{self.category}:{':'.join(self.qualifiers)}"


# ---------------------------------------------------------------------------
# Page-type classification -> drives authority ranking (see knowledge_base.py)
# ---------------------------------------------------------------------------

_PAGE_TYPE_URL_PATTERNS = [
    (r"/calculat", "calculator"),
    (r"/rates?/?$", "rates_page"),
    (r"#rates", "rates_page"),
    (r"/newsroom", "press"),
    (r"/learn/blog", "blog"),
    (r"/personal/credit-cards", "product"),
    (r"/business/credit-cards", "product"),
    (r"/personal/loans", "product"),
    (r"/personal/savings-account", "product"),
    (r"/business/savings-accounts", "product"),
    (r"/contact", "hours_page"),
]


def classify_page_type(url: str, title: str) -> str:
    haystack = f"{url} {title}".lower()
    for pattern, page_type in _PAGE_TYPE_URL_PATTERNS:
        if re.search(pattern, haystack):
            return page_type
    return "other"


# ---------------------------------------------------------------------------
# Qualifier extraction: what distinguishes two otherwise-similar facts
# ---------------------------------------------------------------------------

_TERM_RE = re.compile(r"(\d{1,3})[\s-]*month", re.IGNORECASE)

_PRODUCT_KEYWORDS = [
    # order matters: more specific phrases first
    ("jumbo certificate", "jumbo_certificate"),
    ("jumbo special", "jumbo_certificate"),
    ("certificate special", "certificate"),
    ("certificate", "certificate"),
    ("auto loan", "auto_loan"),
    ("auto & motorcycle", "auto_loan"),
    ("motorcycle loan", "auto_loan"),
    ("30-year", "mortgage_30yr"),
    ("15-year", "mortgage_15yr"),
    ("mortgage", "mortgage"),
    ("home loan", "mortgage"),
    ("personal loan", "personal_loan"),
    ("platinum visa", "platinum_visa"),
    ("rewards visa", "rewards_visa"),
    ("business platinum", "business_platinum_visa"),
    ("business rewards", "business_rewards_visa"),
    ("share savings", "share_savings"),
    ("bright savings", "bright_savings"),
    ("1st rate checking", "first_rate_checking"),
    ("free checking", "free_checking"),
]

_MODEL_YEAR_RANGE_RE = re.compile(r"(\d{4})\s*[-\u2013\u2014]\s*(\d{2,4})\s*model", re.IGNORECASE)

_DOLLAR_TIER_RE = re.compile(
    r"\$?([\d,]{3,})(?:\.\d\d)?\s*[-\u2013\u2014]\s*\$?([\d,]{3,})(?:\.\d\d)?"
)
_DOLLAR_PLUS_RE = re.compile(r"\$?([\d,]{3,})\s*(?:and up|or more|\+|and above)", re.IGNORECASE)
_LOAN_AMOUNT_RE = re.compile(r"(?:up to|loans? up to)\s*\$?([\d,]{3,})", re.IGNORECASE)


def _extract_qualifiers(context: str) -> Tuple[str, ...]:
    ctx_lower = context.lower()
    tags: List[str] = []

    for phrase, tag in _PRODUCT_KEYWORDS:
        if phrase in ctx_lower:
            tags.append(tag)
            break

    for term_match in _TERM_RE.finditer(ctx_lower):
        tags.append(f"{term_match.group(1)}mo")

    my = _MODEL_YEAR_RANGE_RE.search(ctx_lower)
    if my:
        tags.append(f"{my.group(1)}-{my.group(2)}")

    dt = _DOLLAR_TIER_RE.search(ctx_lower)
    if dt:
        tags.append(f"tier_{dt.group(1).replace(',', '')}_{dt.group(2).replace(',', '')}")
    else:
        dp = _DOLLAR_PLUS_RE.search(ctx_lower)
        if dp:
            tags.append(f"tier_{dp.group(1).replace(',', '')}_plus")

    la = _LOAN_AMOUNT_RE.search(ctx_lower)
    if la:
        tags.append(f"loanamt_{la.group(1).replace(',', '')}")

    if "business" in ctx_lower and "business" not in " ".join(tags):
        tags.append("business")
    if "standard" in ctx_lower:
        tags.append("standard")
    if "jumbo" in ctx_lower and "jumbo" not in " ".join(tags):
        tags.append("jumbo")

    return tuple(dict.fromkeys(tags)) or ("general",)  # dedupe, keep order


# ---------------------------------------------------------------------------
# Rate / APY / fee value extraction
# ---------------------------------------------------------------------------

# Matches "15.50%" optionally followed by "-21.00%" (en-dash, em-dash, or
# hyphen), with an optional footnote digit glued to a preceding label like
# "APR1:" (handled by just not requiring a word boundary before the number).
_RANGE_RATE_RE = re.compile(
    r"(\d+\.\d{1,3})\s*%\s*[-\u2013\u2014]\s*(\d+\.\d{1,3})\s*%\s*(APR|APY)?",
    re.IGNORECASE,
)
_SINGLE_RATE_RE = re.compile(r"(\d+\.\d{1,3})\s*%\s*(APR|APY)?", re.IGNORECASE)

_FEE_RE = re.compile(r"\$\s*(\d{1,4}(?:\.\d{2})?)")

_CONTEXT_WINDOW = 100


def _context(text: str, start: int, end: int) -> str:
    lo = max(0, start - _CONTEXT_WINDOW)
    hi = min(len(text), end + _CONTEXT_WINDOW)
    return text[lo:hi].replace("\n", " ").strip()


def _infer_rate_kind(ctx: str, explicit: Optional[str]) -> str:
    if explicit:
        return explicit.upper()
    return "APY" if "apy" in ctx.lower() else "APR"


def extract_facts(page_text: str, url: str, title: str, snapshot_date: str) -> List[Fact]:
    facts: List[Fact] = []
    page_type = classify_page_type(url, title)

    consumed_spans: List[Tuple[int, int]] = []

    # --- Ranges first (so "15.50%-21.00%" isn't double counted as two
    #     separate single-rate facts) -----------------------------------
    for m in _RANGE_RATE_RE.finditer(page_text):
        ctx = _context(page_text, m.start(), m.end())
        kind = _infer_rate_kind(ctx, m.group(3))
        low, high = float(m.group(1)), float(m.group(2))
        qualifiers = _extract_qualifiers(ctx)
        facts.append(
            Fact(
                category="apy" if kind == "APY" else "rate",
                qualifiers=qualifiers,
                low=low,
                high=high,
                display=f"{m.group(1)}%\u2013{m.group(2)}% {kind}",
                snapshot_date=snapshot_date,
                source_url=url,
                page_type=page_type,
                context=ctx,
            )
        )
        consumed_spans.append((m.start(), m.end()))

    def _in_consumed(pos: int) -> bool:
        return any(s <= pos < e for s, e in consumed_spans)

    # --- Single rates ---------------------------------------------------
    for m in _SINGLE_RATE_RE.finditer(page_text):
        if _in_consumed(m.start()):
            continue
        ctx = _context(page_text, m.start(), m.end())
        kind = _infer_rate_kind(ctx, m.group(2))
        qualifiers = _extract_qualifiers(ctx)
        facts.append(
            Fact(
                category="apy" if kind == "APY" else "rate",
                qualifiers=qualifiers,
                low=float(m.group(1)),
                high=None,
                display=f"{m.group(1)}% {kind}",
                snapshot_date=snapshot_date,
                source_url=url,
                page_type=page_type,
                context=ctx,
            )
        )

    # --- Fees -------------------------------------------------------
    if "fee" in page_text.lower():
        for m in _FEE_RE.finditer(page_text):
            ctx = _context(page_text, m.start(), m.end())
            if "fee" not in ctx.lower():
                continue
            qualifiers = _extract_qualifiers(ctx)
            facts.append(
                Fact(
                    category="fee",
                    qualifiers=qualifiers,
                    low=float(m.group(1)),
                    high=None,
                    display=f"${m.group(1)}",
                    snapshot_date=snapshot_date,
                    source_url=url,
                    page_type=page_type,
                    context=ctx,
                )
            )

    return facts
