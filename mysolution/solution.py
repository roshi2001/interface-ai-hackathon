"""
Step 2 submission.

Strategy so far:
  - Specialized extraction for rate / APY / fee facts (extraction.py),
    stored in a KnowledgeBase (knowledge_base.py) that resolves which
    source wins when pages disagree, and supports as_of lookups.
  - Everything else still falls back to the starter's naive paragraph
    search, so the system always returns *something* while we build out
    more specialized extractors in later steps.

Keep the `build()` function and the three methods; this file grows in
later steps (branch directory, routing number, dateline-aware "About
Dupaco" boilerplate stats, conflicts(), refusal logic...).
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(next(d / "harness" for d in HERE.parents if (d / "harness" / "contract.py").exists())))

from contract import Answer, Conflict, Snapshot, Source  # noqa: E402
from extraction import extract_facts  # noqa: E402
from knowledge_base import KnowledgeBase  # noqa: E402
from rates_table import parse_rate_tables  # noqa: E402
from rate_matching import (  # noqa: E402
    best_row_groups,
    resolve_as_of,
    compose_answer_text,
    detect_enumeration_category,
    enumerate_category,
    MIN_TRUSTED_SCORE,
    MIN_TRUSTED_SCORE_AS_OF,
)
from simple_facts import find_routing_number, is_routing_question  # noqa: E402
from branch_info import (  # noqa: E402
    extract_hours_schedules,
    extract_locations,
    find_location_by_keyword,
)

WORD = re.compile(r"[a-z0-9$%.]+")

# Only try the specialized rate/APY path when the question looks like it's
# actually asking about one -- avoids accidentally hijacking unrelated
# questions that happen to share a stray word.
_RATE_QUESTION_RE = re.compile(
    r"\b(rate|apr|apy|interest|dividend|fee|charge)s?\b", re.IGNORECASE
)
_LOBBY_Q_RE = re.compile(r"\blobby hours\b", re.IGNORECASE)
_DRIVEUP_Q_RE = re.compile(r"\bdrive-?up hours\b", re.IGNORECASE)
_SAME_HOURS_Q_RE = re.compile(r"\b(all|same|different|every)\b.*\b(hours|branch(es)?)\b", re.IGNORECASE)
_BRANCH_NAME_RE = re.compile(r"([A-Z][a-zA-Z.&'-]*(?:\s+[A-Z][a-zA-Z.&'-]*){0,2})\s+[Bb]ranch\b")
_BRANCH_ADDRESS_Q_RE = re.compile(r"\baddress\b", re.IGNORECASE)


class DupacoKnowledge:
    def __init__(self):
        self.kb = KnowledgeBase()
        self.rate_rows = []  # structured table rows (Step 3, most reliable)
        self.pages = {}  # url -> (snapshot_id, markdown)  [naive fallback]
        self.routing_number = None
        self.routing_source = None
        self.hours_schedules = {}  # normalized schedule -> (display, url, count)
        self.locations = {}  # address -> BranchLocation (latest snapshot wins)

    def ingest(self, snapshot: Snapshot) -> None:
        for p in snapshot.pages:
            self.pages[p.url] = (snapshot.snapshot_id, p.markdown)
            self.rate_rows.extend(parse_rate_tables(p.markdown, p.url, snapshot.taken_on))
            facts = extract_facts(p.markdown, p.url, p.title, snapshot.taken_on)
            self.kb.add_facts(facts)
            if self.routing_number is None:
                found = find_routing_number(p.markdown)
                if found:
                    self.routing_number = found
                    self.routing_source = p.url

            for schedule in extract_hours_schedules(p.markdown):
                key = re.sub(r"[\u2013\u2014]", "-", schedule.lower())
                if key in self.hours_schedules:
                    disp, url, count = self.hours_schedules[key]
                    self.hours_schedules[key] = (disp, url, count + 1)
                else:
                    self.hours_schedules[key] = (schedule, p.url, 1)

            for loc in extract_locations(p.markdown, p.url):
                self.locations[loc.address] = loc  # later snapshots overwrite

    def answer(self, question, as_of=None):
        # Note: "Can you repeat the routing number?" already contains the
        # literal phrase "routing number", so this stateless check handles
        # it correctly on its own -- no conversational memory needed.
        if is_routing_question(question) and self.routing_number:
            return Answer(
                text=self.routing_number,
                sources=[Source(url=self.routing_source)],
                confident=True,
            )

        branch_match = _BRANCH_NAME_RE.search(question)
        if branch_match and (_BRANCH_ADDRESS_Q_RE.search(question) or "where" in question.lower()):
            keyword = branch_match.group(1).strip()
            loc = find_location_by_keyword(list(self.locations.values()), keyword)
            if loc:
                return Answer(text=loc.address, sources=[Source(url=loc.source_url)], confident=True)
            # A named branch we have no record of at all (e.g. a closed,
            # renamed, or never-existent location) -- refuse rather than
            # fabricate an address, same principle as the rate refusals.
            return Answer(
                text=f"I don't have a verified address on file for a '{keyword}' branch; please contact us directly.",
                confident=False,
            )

        if _LOBBY_Q_RE.search(question) or _DRIVEUP_Q_RE.search(question):
            schedules = sorted(self.hours_schedules.values(), key=lambda v: -v[2])
            if schedules:
                if _SAME_HOURS_Q_RE.search(question) and len(schedules) > 1:
                    # Find two schedules that genuinely differ in their
                    # Saturday close time, to directly support "no, at
                    # least two schedules exist" rather than vague hedging.
                    with_1230 = next((s for s, _, _ in schedules if "12:30" in s), None)
                    with_noon = next((s for s, _, _ in schedules if "noon" in s), None)
                    if with_1230 and with_noon:
                        return Answer(
                            text=(
                                f"No, lobby hours vary by branch. For example: {with_1230}; "
                                f"another branch has {with_noon}. Please ask which branch you'd like."
                            ),
                            confident=True,
                        )
                top_schedule, top_url, _ = schedules[0]
                if _DRIVEUP_Q_RE.search(question):
                    driveup_part = top_schedule.split("Drive-up:", 1)[-1].strip()
                    return Answer(
                        text=(
                            f"Drive-up hours at most branches: {driveup_part} "
                            "Hours vary by branch; let me know which one you'd like."
                        ),
                        sources=[Source(url=top_url)],
                        confident=True,
                    )
                lobby_part = top_schedule.split("Drive-up:", 1)[0].replace("Lobby:", "").strip()
                return Answer(
                    text=(
                        f"Lobby hours vary by branch; a typical schedule is {lobby_part} "
                        "Let me know which branch you'd like, or if you want hours for all branches."
                    ),
                    sources=[Source(url=top_url)],
                    confident=True,
                )

        if _RATE_QUESTION_RE.search(question):
            enum_category = detect_enumeration_category(question)
            if enum_category:
                entries = enumerate_category(self.rate_rows, enum_category, as_of)
                if entries:
                    return Answer(
                        text="; ".join(text for _, text in entries) + ".",
                        sources=[Source(url=row.source_url) for row, _ in entries[:3]],
                        confident=True,
                    )

            # Step 3: structured table rows first -- much more reliable
            # than prose regex when the fact lives in a clean rate table.
            groups = best_row_groups(self.rate_rows, question, as_of=as_of)
            threshold = MIN_TRUSTED_SCORE_AS_OF if as_of else MIN_TRUSTED_SCORE
            if groups and groups[0][0] >= threshold:
                for score, group in groups:
                    if score < threshold:
                        break  # remaining candidates are even weaker
                    row = resolve_as_of(group, as_of)
                    if row:
                        return Answer(
                            text=compose_answer_text(row),
                            sources=[Source(url=row.source_url)],
                            confident=True,
                        )
            else:
                # Nothing scored well enough to trust -- this is the
                # signal that distinguishes "we don't publish that rate"
                # (7/1 ARM, construction loan) from a genuine match.
                return Answer(
                    text="I don't see that rate listed on our site; please contact us directly for details.",
                    confident=False,
                )

            # Step 2 fallback: generic prose regex extraction.
            topics = self.kb.best_matching_topics(question)
            for topic in topics:
                resolved = self.kb.resolve(topic, as_of=as_of)
                if resolved:
                    return Answer(
                        text=resolved.display,
                        sources=[Source(url=resolved.source_url)],
                        confident=True,
                        conflict_note=resolved.reason if "authoritative" in resolved.reason else None,
                    )

        # --- fallback: naive paragraph search (from the starter) --------
        q = set(WORD.findall(question.lower()))
        best, best_score = None, 0
        for url, (sid, md) in self.pages.items():
            for para in md.split("\n\n"):
                s = len(q & set(WORD.findall(para.lower())))
                if s > best_score:
                    best, best_score = (url, sid, para.strip()), s
        MIN_FALLBACK_OVERLAP = 3  # shared words with the question
        if not best or best_score < MIN_FALLBACK_OVERLAP:
            return Answer(
                text="I don't have that information; please contact us directly for details.",
                confident=False,
            )
        url, sid, para = best
        return Answer(text=para[:600], sources=[Source(url=url, snapshot_id=sid)])

    def conflicts(self):
        return []  # next step


def build():
    return DupacoKnowledge()
