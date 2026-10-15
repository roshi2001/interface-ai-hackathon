"""
Step 3: structured parser for Dupaco's consolidated rates tables.

The real /rates page (and a few product pages) use one very consistent
pattern, repeated dozens of times on the same page:

    <Section heading, e.g. "Share Savings" or "Platinum Visa">

    | Account Type | Balance Tier | Dividend Rate | Annual Percentage Yield (APY)1 | ... |
    | --- | --- | --- | --- | --- |
    | Share Savings | $25,000+ | 0.20% | 0.20%1 | ... |
    | Share Savings | $10,000 - $24,999.99 | 0.15% | 0.15%1 | ... |

    Rates are effective as of 06/15/2026 and are subject to change...

Parsing this directly (instead of running generic regex over the prose)
is far more reliable: every row is already a clean, unambiguous fact, and
the "Rates are effective as of ..." line gives each table its own real
effective date -- which matters a lot here, since different tables on
the *same* page, in the *same* snapshot, are routinely stamped with
different effective dates (e.g. Certificates effective 09/01/2026 while
Checking on the same page is still effective 06/02/2026).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

EFFECTIVE_RE = re.compile(r"effective as of\s+(\d{1,2})/(\d{1,2})/(\d{4})", re.IGNORECASE)
PERCENT_RE = re.compile(r"(\d+\.\d{1,3})\s*%")
TIER_RANGE_RE = re.compile(r"\$?([\d,]{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*[-\u2013\u2014]\s*\$?([\d,]{1,3}(?:,\d{3})*(?:\.\d{2})?)")
TIER_PLUS_RE = re.compile(r"\$?([\d,]{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*\+")
TERM_RE = re.compile(r"(\d{1,3})\s*[- ]?\s*(?:month|yr|year)s?", re.IGNORECASE)


def _to_iso(mm: str, dd: str, yyyy: str) -> str:
    return f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d}"


def _clean_cell(cell: str) -> str:
    cell = cell.strip()
    cell = re.sub(r"\\?\*+$", "", cell)  # trailing footnote asterisks
    return cell.strip()


def _parse_number(s: str) -> Optional[float]:
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


# Tables live on more than one page -- the canonical /rates page, product
# pages (auto loan page, checking page...), and unfortunately also stale
# calculator pages that embed a copy of a table and never get updated
# (this is literally the scenario the assignment is built around). Rank
# them so the real rates page always wins a genuine disagreement, and a
# stale calculator copy essentially never wins unless it's the only
# source at all.
PAGE_TYPE_AUTHORITY = {"rates_page": 0, "product": 1, "other": 2, "calculator": 3}


def classify_page_type(url: str) -> str:
    u = url.lower()
    if "/calculat" in u:
        return "calculator"
    if u.rstrip("/").endswith("/rates") or "/rates#" in u:
        return "rates_page"
    if any(seg in u for seg in ("/personal/", "/business/", "/savings-account", "/credit-cards", "/loans")):
        return "product"
    return "other"


@dataclass
class RateRow:
    section: str  # e.g. "Share Savings", "Platinum Visa", "Home Loans - Purchase"
    row_label: str  # the row's own first-column text, e.g. "$10,000 - $24,999.99"
    columns: Dict[str, str]  # full header -> cell text, for composing a rich answer
    snapshot_date: str  # ISO date of the crawl
    effective_date: Optional[str]  # ISO date from "Rates are effective as of ..."
    source_url: str
    page_type: str = "other"
    tier_low: Optional[float] = None
    tier_high: Optional[float] = None  # None + tier_low set means "X and up"
    term_months: Optional[int] = None
    search_text: str = field(default="")  # cached lowercase text used for matching

    @property
    def effective_sort_key(self) -> Tuple[str, str]:
        # snapshot_date is the primary key: it's always known and always
        # monotonic with real crawl recency, so it correctly answers "what
        # did the site show as of this point in time" even if the page's
        # own "effective as of" label refers to an earlier date the rate
        # was actually set. effective_date is only a tiebreaker for two
        # tables observed in the very same snapshot that disagree. (Using
        # effective_date as primary is a trap: when it's missing for one
        # snapshot and present for another, falling back to that
        # snapshot's own crawl date as a *substitute* primary key mixes
        # two different kinds of date and can make an older crawl outrank
        # a genuinely newer one.)
        return (self.snapshot_date, self.effective_date or self.snapshot_date)


def _find_section_heading(lines: List[str], table_start_idx: int) -> str:
    for i in range(table_start_idx - 1, max(-1, table_start_idx - 6), -1):
        line = lines[i].strip()
        if not line or line.startswith("|") or line.startswith("Rates are effective"):
            continue
        if line.startswith("#"):
            return line.lstrip("#").strip()
        return line
    return ""


def _find_effective_date(lines: List[str], table_end_idx: int, snapshot_date: str) -> Optional[str]:
    for i in range(table_end_idx + 1, min(len(lines), table_end_idx + 4)):
        m = EFFECTIVE_RE.search(lines[i])
        if m:
            return _to_iso(m.group(1), m.group(2), m.group(3))
    return None


def _parse_tier(row_label: str, tier_cell: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    text = f"{row_label} {tier_cell or ''}"
    m = TIER_RANGE_RE.search(text)
    if m:
        lo, hi = _parse_number(m.group(1)), _parse_number(m.group(2))
        return lo, hi
    m = TIER_PLUS_RE.search(text)
    if m:
        lo = _parse_number(m.group(1))
        return lo, None
    return None, None


def _parse_term(row_label: str, term_cell: Optional[str]) -> Optional[int]:
    # Check the *term cell* alone for "year"/"yr" units, not the combined
    # text -- a row_label like "2024-27 Model Year" always contains the
    # word "Year" for an unrelated reason (it's a model-year range, not a
    # loan term), and checking the combined text silently multiplied
    # every auto-loan term by 12 (turning "60 Months" into 720).
    if term_cell:
        m = TERM_RE.search(term_cell)
        if m:
            months = int(m.group(1))
            if re.search(r"\byr\b|\byear", term_cell, re.IGNORECASE):
                months *= 12
            return months
    m = TERM_RE.search(row_label)
    if m:
        months = int(m.group(1))
        if re.search(r"\byr\b|\byear", row_label, re.IGNORECASE):
            months *= 12
        return months
    return None


_FINANCIAL_HEADER_RE = re.compile(
    r"rate|apr|apy|dividend|tier|term|balance|yield|program|card", re.IGNORECASE
)


def parse_rate_tables(page_text: str, url: str, snapshot_date: str) -> List[RateRow]:
    lines = page_text.split("\n")
    rows_out: List[RateRow] = []
    page_type = classify_page_type(url)

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            header_cells = [_clean_cell(c) for c in line.strip("|").split("|")]

            # Skip tables that clearly aren't financial (e.g. generic
            # "Col2 | Col3"-style tables scraped from unrelated pages).
            if not any(_FINANCIAL_HEADER_RE.search(h) for h in header_cells):
                i += 1
                continue

            table_start = i
            j = i + 2
            data_rows: List[List[str]] = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                cells = [_clean_cell(c) for c in lines[j].strip().strip("|").split("|")]
                if len(cells) == len(header_cells):
                    data_rows.append(cells)
                j += 1
            table_end = j - 1

            section = _find_section_heading(lines, table_start)
            effective_date = _find_effective_date(lines, table_end, snapshot_date)

            tier_col_idx = next(
                (idx for idx, h in enumerate(header_cells) if "tier" in h.lower()), None
            )
            term_col_idx = next(
                (idx for idx, h in enumerate(header_cells) if "term" in h.lower()), None
            )

            for cells in data_rows:
                row_label = cells[0]
                columns = dict(zip(header_cells, cells))
                tier_cell = cells[tier_col_idx] if tier_col_idx is not None else None
                term_cell = cells[term_col_idx] if term_col_idx is not None else None
                tier_lo, tier_hi = _parse_tier(row_label, tier_cell)
                term_months = _parse_term(row_label, term_cell)

                # Include header *names* too (e.g. "Dividend Rate",
                # "Annual Percentage Yield (APY)"), not just cell values --
                # otherwise words like "rate" or "apy" never appear in any
                # row's searchable text at all, since they only ever show
                # up as column headers, never inside a cell.
                search_text = " ".join(
                    [section, row_label] + header_cells + list(columns.values())
                ).lower()

                rows_out.append(
                    RateRow(
                        section=section,
                        row_label=row_label,
                        columns=columns,
                        snapshot_date=snapshot_date,
                        effective_date=effective_date,
                        source_url=url,
                        page_type=page_type,
                        tier_low=tier_lo,
                        tier_high=tier_hi,
                        term_months=term_months,
                        search_text=search_text,
                    )
                )

            i = j
        else:
            i += 1

    return rows_out
