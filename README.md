# Order Intake & Exception Handling Platform

Junior AI Engineer take-home, **Alternative A** (`tasks/orders/`). Turns short
email-style customer order requests into validated draft orders, with a
review queue for anything ambiguous, unknown, or duplicate.

Python + Flask + SQLite, using the official `anthropic` SDK for the real
model integration.

## Quick start

```bash
# 1. Set up the environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Set your API key (or skip this and use --mock / the committed cache, see below)
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...

# 3. Process all requests in data/requests/
python bin/process.py

# 4. Run the hand-verified reference-case checks
python bin/check.py

# 5. Open the review dashboard
python app.py
# visit http://localhost:8787/
```

### Replaying without an API key

`storage/responses/*.json` holds every real Claude response this project
was built and checked against, keyed by request id. If `ANTHROPIC_API_KEY`
is unset (or `.env` is missing), `bin/process.py` will **not** call the
API for a request whose cached response already exists on disk -- it reads
the cached JSON instead and runs the exact same validation/pricing path.
So a reviewer can clone this repo and just run:

```bash
python bin/process.py   # replays storage/responses/*.json, no key needed
python bin/check.py
```

If a request has no cached response AND no API key is set, `process.py`
raises a clear error naming the request instead of inventing a result.

### `--mock` mode

`python bin/process.py --mock` uses `orderintake/ai/mock_extraction.py`, a
deterministic local stand-in for the model, instead of a real or cached
response. It exists purely so the rest of the pipeline (catalog matching,
pricing, dedup, persistence, dashboard, corrections) can be developed and
regression-tested without API cost. **It was not used to produce the
submitted check results** -- see "Real model integration" below. Every
response file under `storage/responses/` records whether it is
`"mode": "real"`, `"cached"`, or `"mock"`.

## Architecture

```
data/catalog.json          the 3-item fictional catalog (from seed.json)
data/requests/*.txt        11 email-style request files (4 from seed + 7 added)
data/requests.json         manifest mapping request id -> order_ref -> file

orderintake/domain/              rules that are NEVER delegated to the model -- pure functions, no I/O
orderintake/domain/catalog.py        code-only product lookup (SKU or unambiguous description)
orderintake/domain/number_words.py   code-only quantity cross-check (digits + spelled numbers)
orderintake/domain/pricing.py        10% bulk discount at qty>=10, round-half-up

orderintake/ai/                   everything that talks to (or stands in for) the model
orderintake/ai/extractor.py          builds the model prompt, parses its JSON response
orderintake/ai/claude_client.py      real/cached/mock Anthropic API call (official `anthropic` SDK) + response cache
orderintake/ai/mock_extraction.py    deterministic local stand-in for --mock, never used for real checks

orderintake/order_processor.py   orchestrator -- the only module that imports from BOTH domain/ and ai/
orderintake/storage.py           SQLite persistence (requests, orders, duplicates, corrections, errors)
orderintake/env.py               minimal .env loader (no extra dependency for two variables)

bin/process.py              CLI: process every request in data/requests.json
bin/check.py                 CLI: run checks/reference-cases.json against current DB state
bin/reset.py                 CLI: wipe local storage for a clean re-run

app.py                       Flask dashboard entrypoint
templates/index.html         operations queue (status filter, analytics)
templates/order.html         order detail: original text, proposed lines, correction form, history
static/style.css             shared styling

checks/reference-cases.json  the 11 hand-verified cases (ground truth, independent of the app)
tasks/orders/                 original starter-pack seed/domain/expected-results (untouched)
```

### Who decides what (the important design choice)

The model's **only** job is to segment a request's raw text into per-product
excerpts and give a best-effort reading of the stated quantity. It never
picks a SKU, never decides what's in the catalog, and never prices
anything. All of that is deterministic Python:

- `Catalog.match()` resolves a product by explicit SKU token, or by an
  unambiguous description keyword (a length marker + "cable", or "hub").
  Generic "the usual cable" matches two catalog items -> ambiguous. A
  product with no resemblance to any catalog entry -> unknown.
- `OrderProcessor._resolve_quantity()` takes the model's `quantity_value`
  and **independently cross-checks it** against `number_words`, a
  from-scratch regex/word scan of the same raw excerpt. If the two
  disagree, or neither finds a stated individual-item count (e.g. "a box
  of hubs"), the line is flagged `ambiguous` rather than guessed.
- `pricing.line_total()` applies the 10% bulk discount at qty >= 10 with
  round-half-up (NOT Python's built-in `round()`, which uses banker's
  rounding), never the model.

This keeps every priced/validated number traceable to code you can read in
five minutes, and the model's output is always checked rather than trusted.

### Duplicate handling

Per `domain.md` rule 4, dedup key is `order_ref`, checked **before** any
model call -- so resending an identical request costs nothing and is
recorded in the `duplicates` table, never a second draft, never counted as
a new order in the dashboard's "ready for review" stat.

### Reviewer correction

`app.py`'s `/order/<order_ref>` route lets a reviewer overwrite one line's
`quantity` or `sku`; `OrderProcessor.apply_correction()` re-runs the exact
same catalog/pricing code on the corrected value, recomputes the order
status and total, and `Storage.record_correction()` keeps the before/after
+ result in a `corrections` table so the history survives a restart. The
dashboard badges an order "reviewer-confirmed" once at least one correction
exists, vs. "auto-draft, unreviewed" otherwise (per the brief's requirement
to distinguish a structurally valid draft from a human-reviewed one). The
order detail page also shows a readable **model-proposal-vs-correction
table** (field, old value, new value, order status and total before/after)
rather than a raw log dump -- the one optional enhancement implemented for
this submission.

### A Flask-specific bug worth flagging up front

The first working version of `app.py` shared **one global `sqlite3`
connection** across all requests. It worked for the CLI scripts (single
process, single thread) but crashed every dashboard page with
`sqlite3.ProgrammingError: SQLite objects created in a thread can only be
used in that same thread` -- Flask's dev server can dispatch requests on
different threads. Fixed by opening a fresh `Storage` (and thus a fresh
sqlite3 connection) per request instead of one shared at module load time.
Caught immediately by actually curling the running dashboard rather than
just eyeballing the code -- see `ai-workflow/README.md` for the full story.

## Real model integration

- Model: `claude-haiku-4-5-20251001` (set via `CLAUDE_MODEL` in `.env` --
  `orderintake/claude_client.py` has no hardcoded model). Haiku was chosen
  over Sonnet after estimating cost for this task: the model's job here is
  narrow text segmentation, not complex reasoning, and the whole 11-request
  batch costs a fraction of a cent either way, so the cheaper/faster tier
  was preferred with no accuracy tradeoff observed in the check results.
- Uses the official `anthropic` Python SDK (`client.messages.create(...)`),
  not a hand-rolled HTTP call.
- System prompt instructs the model to return only
  `{"lines":[{"raw_excerpt","quantity_value","quantity_ambiguous","quantity_notes"}]}`
  and explicitly forbids it from picking SKUs/prices (`orderintake/extractor.py`).
- Every call is cached to `storage/responses/{request_id}.json` with a
  `mode` field (`real`/`cached`/`mock`) so a reviewer can tell a replayed
  response from a mock stub from a live call.
- Invalid/unparseable model output does not crash the batch: the request
  is marked `needs_clarification` with the parse error recorded as the
  reason (see `OrderProcessor.process()`).

## Data: seed + additions

`tasks/orders/` holds the **original, untouched** starter-pack files
(`domain.md`, `seed.json`, `expected-seed-results.json`). `data/requests/`
extends the 4 seed requests (R1-R4) to 11, keeping the original 4 verbatim
and adding:

| id | order_ref | added to cover |
|----|-----------|-----------------|
| R5 | O4 | bulk discount (qty >= 10, spelled-out number "ten") |
| R6 | O5 | multi-line order, two different SKUs in one email |
| R7 | O6 | product resolved via description ("2 meter USB-C cable"), not SKU |
| R8 | O7 | product resolved, quantity ambiguous ("a box of hubs") |
| R9 | O8 | unknown product, no catalog resemblance at all |
| R10 | O9 | second bulk-discount order, standalone (never mutated) |
| R11 | O10 | dedicated order for the reviewer-correction demo -- deliberately kept separate from R10/O9, see "Minimum demonstration" below for why |

Generation method: handwritten, not scripted/random (no seed to record).
Each one was picked to exercise exactly one domain.md rule; see
`checks/reference-cases.json` for the hand-calculated expected result and
the rule each case is checking.

## Minimum demonstration / check results

Run `python bin/check.py` -- it currently reports **11/11 passed** against
`checks/reference-cases.json`:

1. **R1** normal order -> matches hand-checked total (4000 cents).
2. **R2** unknown product -> unresolved with a clarification draft, not
   silently mapped to a catalog item.
3. **R3** ambiguous product *and* ambiguous quantity -> flagged, not guessed.
4. **R4** duplicate of R1 -> recorded as duplicate, **0** new drafts.
5. **R5/R10** bulk-discount lines -> totals match an independent hand calc.
6. **R6** multi-line order -> per-line + combined total correct.
7. **R7** description-only match (no SKU token) -> resolves correctly.
8. **R8** quantity ambiguous despite a resolved product -> flagged, not guessed.
9. **Reviewer correction on O10/R11** (qty 12 -> 15): saved proposal changes,
   validation + discount rerun (21600 -> 27000 cents), and the result is
   read back from SQLite by a separate process invocation (i.e. survives
   a restart). O10 is used **only** for this correction -- not asserted as
   its own static "before" case -- precisely so that re-running
   `bin/check.py` repeatedly stays green forever (see the bug writeup in
   `ai-workflow/README.md`).

No failures to explain -- all 11 cases pass against hand-calculated
expectations that were computed independently of the application before
it was run (see each case's `verified_by` field), using **real**
`claude-haiku-4-5-20251001` responses (check `storage/responses/*.json`
for `"mode": "real"`), not the `--mock` stand-in.

## Known ambiguities / limitations

- **Round-half-up is implemented but not exercised by this data.** The
  supplied catalog's unit prices (2000, 3000, 5000 cents) are all multiples
  of 1000, so `subtotal * 10%` is always a whole number of cents for any
  integer quantity -- the half-up branch in `pricing.round_half_up()` is
  never actually hit by these reference cases. I didn't change the fixed
  catalog to force a fractional case, per the brief's "keep the supplied
  catalog" instruction; this is a gap in test *coverage*, not in the
  rounding logic itself (unit-testable in isolation if needed).
- **Quantity cross-check is a heuristic, not NLP.** `number_words` only
  recognizes digits and the words one-twenty; a request phrased as
  "a dozen cables" would be flagged ambiguous rather than resolved to 12.
  Documented rather than silently guessed, consistent with the brief.
- **Catalog matching keywords are hand-written for this 3-item catalog**
  (length markers + "cable"/"hub"). It would not generalize to a larger
  catalog without a real retrieval step -- out of scope for this exercise's
  3-SKU catalog per the brief's core-scope note.
- **A request that fails (e.g. a transient API error) is retried on the
  next `bin/process.py` run, not silently skipped** -- `OrderProcessor`
  only treats a request as done once it has produced a real outcome (an
  order or a recorded duplicate), not merely because a `requests` row
  exists. This was a real bug caught while switching from `--mock` to the
  live API (see `ai-workflow/README.md`'s workflow example) and fixed
  before submission, rather than left for the reviewer to hit.
- One optional enhancement was implemented: a readable model-proposal-vs-
  reviewer-correction table on the order detail page (field, old value,
  new value, order status before/after, total before/after), rather than
  a raw correction-history dump. The other two optional items (an
  additional request format such as an image attachment, and structured
  export of reviewed orders) were not attempted; core flow and its
  exceptions were prioritized first, per the brief's own guidance.

## Time spent

Approximately 5-5.5 hours total: ~30 min reading `domain.md` and planning
the model/code split, ~1.5 hours initial implementation, ~1 hour end-to-end
testing against `--mock` and then the real API (found and fixed a
retry/idempotency bug and a check-script coupling bug), ~45 min
documentation, plus ~1.5-2 hours porting the working, already-verified
implementation from an initial PHP version to this Python/Flask stack
(mechanical port of already-proven logic, plus one new Flask-specific
threading bug found and fixed during dashboard testing -- see
`ai-workflow/README.md`).
