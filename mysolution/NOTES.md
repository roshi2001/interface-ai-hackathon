# Approach

## Architecture

The system is built in layers, each handling a different kind of fact on the site:

1. **Structured rate tables** (`rates_table.py`, `rate_matching.py`): Most rates
   on the site live in clean markdown tables under headings like "Share Savings"
   or "Auto & Motorcycle Loans". I parse these directly into rows rather than
   using regex over prose, since the table structure itself (a product name,
   a tier/term column, a rate column) is far more reliable than trying to
   infer the same thing from free text.

2. **Question matching** (`rate_matching.py`): Rather than plain keyword
   overlap (too weak when dozens of certificate rows all share words like
   "month" and "APY"), I extract the same structured hints from the
   *question* that I extract from each row: term length, dollar tier,
   whether it's asking about an introductory/jumbo variant, and which
   product family it's in. Rows are scored on how well these line up, not
   just shared words.

3. **Deciding what's "current"**: A row's crawl snapshot date is the
   primary signal for recency (always known, always reliable). The page's
   own "effective as of" date is a secondary tiebreaker only. This matters
   because the business renamed some things over time (e.g. an auto loan
   bracket called "2023-26 Model Year" became "2024-27 Model Year"), so
   "which snapshot last saw this exact row" is a stronger signal of
   current terminology than numeric range-matching alone.

4. **Page authority**: The same fact sometimes appears on more than one
   page — the canonical /rates page, a product page, and occasionally a
   stale calculator page that was never updated. Rows are ranked
   rates_page > product > other > calculator, so a stale calculator copy
   essentially never wins over a better source.

5. **Confidence threshold**: If the best-scoring candidate for a question
   falls below a minimum score, the system answers "I don't know" instead
   of guessing. This is what lets it correctly decline to quote a rate for
   products the site doesn't actually publish (e.g. a 7/1 ARM).

6. **Branch info** (`branch_info.py`): Branch addresses come from the
   contact page's location widget (matched by address text, not branch
   name, since every branch is generically labeled "Dupaco Credit Union").
   Lobby/drive-up hours are NOT on that widget — they're scattered across
   individual branch-opening newsroom articles, so I collect every distinct
   schedule seen across the whole site and can report both a typical
   schedule and explicitly confirm when branches differ.

7. **Simple facts** (`simple_facts.py`): The routing number is looked up
   directly by regex; genuinely static facts don't need the table
   machinery.

## What I'd improve with more time

- `conflict_resolution` category: questions like "how many branches does
  Dupaco have" require synthesizing a single current fact (e.g. "22") from
  a boilerplate paragraph that's repeated, with different numbers, across
  dozens of old press releases. I'd build a dedicated extractor that finds
  each press release's own dateline and picks the most recently-dated
  instance of the fact, rather than just the most recently-crawled page.
- Full enumeration (e.g. "list all certificate terms") currently works for
  credit cards and checking; extending it to certificates and savings
  tiers would help the remaining `list_completeness` cases.
- An LLM-assisted layer at ingest time (not at answer time, to protect
  the speed score) could handle messier prose facts — like the Hy-Vee
  branch closure announcement — that don't fit the table/widget model.

## Dependencies

None beyond the Python standard library — no API keys required.