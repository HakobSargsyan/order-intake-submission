# AI workflow used during this exercise

## Tools and models

- **Dev-time tool:** Claude Code (claude.ai/code desktop app), model id
  `claude-sonnet-5`. Used in an ordinary interactive session (no custom
  subagents, hooks, or project-specific CLAUDE.md for this repo) to write
  the application code, the starter-data additions, and this
  documentation, with the candidate running every script/check and
  reading every diff before accepting it. The implementation was first
  built and fully verified in PHP, then ported to this Python/Flask stack
  -- see "One workflow example" below for why, and for the bugs the port
  itself surfaced.
- **Application-time model:** `claude-haiku-4-5-20251001` (configurable via
  `CLAUDE_MODEL` in `.env`), called via the official `anthropic` Python
  SDK from `orderintake/ai/claude_client.py`. `max_tokens=1024`, temperature
  left at the API default. Chosen over Sonnet after estimating cost for
  this specific task (narrow text segmentation, not complex reasoning) --
  see README.md "Real model integration" for the reasoning.
- No plugins, extensions, or MCP servers beyond the desktop app's defaults
  were used.

## Configuration files

- `orderintake/ai/extractor.py` contains the only prompt that matters: the
  system prompt given to the application's own model calls. It is
  committed in full in source, not summarized here, since it's short and
  load-bearing.
- No Claude Code hooks, custom skills, or agent/rule files were authored
  for this standalone repo -- `not-used` in `manifest.json` for those
  categories, accurately, rather than padded out.
- The starter pack's `skills/generate-assignment-data/SKILL.md` was read
  for guidance on how much fixture data to add and what to avoid
  inventing, but not installed/invoked as an actual Claude Code skill --
  the 7 added request files (R5-R11) were handwritten directly against
  `domain.md`.

## One workflow example

**Instruction given:** "Build `OrderProcessor`, `Catalog`, and
`number_words` so that catalog matching and quantity validation happen
entirely in code, with the model only segmenting request text into
per-product excerpts" (full context: this project's README
"Who decides what" section).

**How I checked the output, at every stage:** rather than trusting any
single pass, I ran the full pipeline end-to-end after every meaningful
change -- first in `--mock` mode (no API cost), then against the real
API, then again after porting the whole thing from PHP to Python -- each
time compared against the same independently hand-calculated
`checks/reference-cases.json`.

**What the `--mock` pass caught (two matching bugs, PHP implementation):**

1. The quantity cross-check regex matched the "1" inside the SKU token
   `CAB-1` as if it were a stated quantity -- e.g. "We need ten CAB-1
   cables" was read as quantity **1** instead of **10**. Since this
   function also cross-checks the *model's* quantity reading, this would
   have broken real-model validation too, not just the mock. **Fix:**
   negative lookbehind `(?<![A-Za-z]-)` excludes a digit directly
   preceded by `<letter>-` (a product-code pattern) -- ported as-is into
   `orderintake/domain/number_words.py`.
2. The catalog-description matcher used `cable`/`hub` without plural
   support, so it didn't match "cables"/"hubs" (no word boundary before
   the trailing "s"). This silently broke every description-only match,
   e.g. "two of the 2 meter USB-C cables" fell through to `unknown`.
   **Fix:** patterns changed to accept an optional trailing "s" -- ported
   as-is into `orderintake/domain/catalog.py`.

**What switching from `--mock` to the real API caught (PHP implementation,
the more interesting one):** after setting a real `ANTHROPIC_API_KEY` and
running the processor for real, the key was initially left empty by
mistake -- all 10 requests failed with a clear, intended error. After
fixing the key and re-running, **every request still came back
"already processed" and nothing changed** -- a real bug, not user error:
the processor treated "a `requests` row exists" as "this was already
handled", so a request that had previously *failed* was indistinguishable
from one that had *succeeded*, and could never be retried without wiping
the entire database. **Fix:** added `Storage.has_outcome()` (true only if
the request produced a real order or a recorded duplicate) and changed the
skip condition to check that instead of mere row existence -- carried into
`orderintake/order_processor.py` and `orderintake/storage.py` from the
start, since it's a correctness property of the design, not a PHP quirk.

While re-verifying that fix by running the check script several times in
a row (to prove the retry fix didn't break idempotency), a **second**,
unrelated bug surfaced: the reviewer-correction reference case and a
plain "bulk discount" reference case both targeted the same order, so
once the correction ran once, the static "before" case for that order
could never pass again on a later rerun. **Fix:** gave the correction demo
its own dedicated order (`O10`/`R11`) that no other case asserts a static
value for, so every case now asserts a *final* state exactly once.

**What the Python/Flask port itself caught (a genuinely new bug, not a
translation of a PHP one):** `app.py`'s first version opened one SQLite
connection at module load time and reused it across all Flask requests.
The CLI scripts (`bin/process.py`, `bin/check.py`) worked fine the same
way, since each is a single-threaded process end to end. But the Flask
dev server dispatches requests on different threads, and `sqlite3`
connections refuse cross-thread use by default -- every dashboard page
crashed with `sqlite3.ProgrammingError: SQLite objects created in a
thread can only be used in that same thread`. This was only caught
because the dashboard was actually curled and its real HTTP response
checked, not just visually reviewed. **Fix:** `app.py` now opens a fresh
`Storage` (and thus a fresh connection) per request via a `get_storage()`
helper instead of one shared global.

All of the above was found and fixed *before* submission. Final
verification loop:
`python bin/reset.py && python bin/process.py && python bin/check.py`
(repeated several times, plus the dashboard hit directly over HTTP) --
11/11 passed, consistently, against real `claude-haiku-4-5-20251001`
responses, which were themselves replayed unchanged from the PHP run
(same cache file format, same request text -> zero extra API calls spent
on the rewrite itself).

**A fifth bug, found later while adding the simulated-failure case below:**
adding `R13` (a request whose cached response is deliberately invalid, to
exercise the "model call failed" path on demand -- see "Simulated failure
case") crashed `bin/process.py` on its *second* run with
`pymysql.err.IntegrityError: Duplicate entry 'R13' for key
'processing_errors.PRIMARY'`. Root cause: `Storage.record_error()` did a
plain `INSERT`, but `request_id` is the table's primary key, and a request
that keeps failing is *by design* retried on every run (`has_outcome()`
only becomes true once a request produces a real order or duplicate) --
so the exact same `request_id` legitimately fails more than once across
runs, and the second `INSERT` collided with the first. This was invisible
before because every earlier failure in this project was transient (fixed
and retried successfully exactly once); a *permanently* failing request
had never existed until this one. **Fix:** `record_error()` now does
`INSERT ... ON DUPLICATE KEY UPDATE`, keeping the latest reason/timestamp
instead of crashing -- verified stable by running `bin/process.py` three
times in a row afterward.

## Simulated failure case

`checks/reference-cases.json`'s `R13` case and `storage/responses/R13.json`
are a deliberate simulation, not a real bug or a real API outage:
`R13.json`'s content is plain text, intentionally not valid JSON, and says
so in the file itself. Reading it makes `ClaudeClient.complete()`'s
`json.load()` raise, which `OrderProcessor.process()` catches via its
existing broad `except Exception` and records as `status: "failed"` --
deterministically, on every run, independent of whether a real
`ANTHROPIC_API_KEY` is set. This exists because the brief asks for
"simulated failures" to be labeled and distinguishable from real model
calls: `R13` is labeled as such in `data/requests.json`'s note field, in
this file, and in `checks/reference-cases.json`'s `verified_by` field --
never presented as a real/cached/mock response. `bin/check.py`'s dedicated
`"failed"` branch verifies it by checking `Storage.list_unresolved_errors()`
and confirming no `orders` row was ever created for `O12` (a failure never
produces a draft).

## Reproduce or replay

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python bin/process.py    # replays storage/responses/*.json if present and no API key is set,
                          # otherwise calls claude-haiku-4-5-20251001 live and caches the result
python bin/check.py      # re-verifies against checks/reference-cases.json (safe to run repeatedly)
python app.py             # dashboard at http://localhost:8787/
```

No Claude Code hooks run automatically against this repo -- there is
nothing to "enable" or review before running; everything above is a plain
Python CLI/script plus a minimal Flask app.

## Decisions and limitations

A default Claude Code setup (no custom skills/hooks/agents) was enough for
this exercise's scope -- the only thing worth preserving beyond the source
code itself is the prompt in `orderintake/ai/extractor.py` and the worked debugging
examples above. I would not fabricate custom agents or hooks just to fill
out this manifest; `not-used` is recorded accurately where nothing was
used. Building the PHP version first and then porting it was a deliberate
choice to separate "is the design correct" from "does this language's
idioms introduce anything new" -- which is exactly what the Flask
threading bug confirmed was worth doing, rather than risk discovering a
language-specific bug for the first time under deadline pressure.
