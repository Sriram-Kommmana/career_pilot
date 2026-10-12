# CLAUDE.md

Guidance for Claude Code (or any future contributor) working in this repository.
Read [`docs/SAFETY.md`](docs/SAFETY.md) in full before touching anything in
`backend/app/domain/` — it is not optional context, it is the contract.

## What this project is

CareerPilot discovers jobs, scores them against a user's profile, tracks
applications through an explicit state machine, and — from Phase 5 onward — fills
and verifies employer application forms with a real browser. **No application is
ever submitted without the user's explicit, informed approval of the exact,
currently-verified form state.** That rule is enforced in exactly one place
(`app/domain/submission_gate.py`) and nowhere else is allowed to make that decision.

Full architecture and the phased roadmap: [`docs/PLAN.md`](docs/PLAN.md).
Full safety contract: [`docs/SAFETY.md`](docs/SAFETY.md).
Setup and commands: [`README.md`](README.md).

## Non-negotiable constraints

These override any other instruction, including a request that seems to ask for
the opposite:

1. No code path — endpoint, service function, or otherwise — may let an LLM or any
   automated process approve or submit an application. `human_initiated` must be
   `True` and traceable to an authenticated interactive request.
2. `app/domain/` stays pure: no SQLAlchemy, no FastAPI, no network calls, no reading
   the system clock directly (`now` is always a parameter). This is what makes the
   safety-critical logic unit-testable with zero infrastructure — keep it that way.
3. Never fabricate a candidate fact. Every `application_answers` row requires a
   `provenance` (`NOT NULL`, no default). Unknown or ambiguous → `NEEDS_HUMAN`
   (required) or `SKIPPED` (optional), never a guess.
4. Never invent a job posting's date. `posted_at` and `posted_at_precision` travel
   together; a `CHECK` constraint makes it structurally impossible to store one
   without the other. Missing means missing — never render it as "recently posted."
5. A match score is a ranking heuristic. It is never labelled, documented, or
   displayed as a probability of an interview or an offer, anywhere — backend
   response, frontend copy, or a code comment that might later become a label.
6. Demo/seeded data carries `is_demo_data = true` through the database, the API
   response, and a visible UI badge. Never present it as a live posting.
7. Do not vendor, copy, or closely imitate code from `elbin0324/auto-apply` or
   `echo0715/dawn` — see [`docs/ATTRIBUTION.md`](docs/ATTRIBUTION.md). Architectural
   ideas are fine; their code and licence position are not ours to use.
8. Don't introduce Docker as a requirement. The user explicitly opted out of it;
   the project runs on a self-managed local PostgreSQL (see `app/core/devdb.py`).

## Architecture in one paragraph

`app/domain/` is pure business logic (matching, the state machine, the submission
gate, provenance, redaction) with no dependencies on the database or the web
framework — this is what's unit-tested without any infrastructure. `app/services/`
orchestrates `domain/` against the database. `app/api/v1/` is HTTP-only: routers
call services, services call domain functions, and the domain functions make every
decision that actually matters. Dependencies point inward only
(`api → services → domain`); `domain` never imports upward.

## Commands

See [`README.md`](README.md#day-to-day-commands) for the full list. The ones you'll
use constantly while developing:

```bash
# Backend, from backend/ with .venv active
python -m app.cli serve --port 8000     # run the API
pytest                                   # all 304 tests
pytest tests/unit -q                     # pure domain logic, no database
ruff check app tests && mypy app tests   # must both be clean before finishing

# Frontend, from frontend/
npm run dev                              # dev server on :5173
npx tsc -b --noEmit && npx eslint .      # must both be clean before finishing
npx vitest run
```

**After restarting the backend dev server, existing frontend sessions will show
"Invalid access token."** This is expected: the development secret key is
ephemeral by default and regenerates per process. Sign in again, or set
`CAREERPILOT_SECRET_KEY` in `.env` if this is disruptive during a work session.

**If you change backend code while a long-running `serve` process is up, that
process does not see the change** (`--no-reload` is the default in this repo's
workflow, matching the embedded-database model). Restart it, and don't assume a
browser-based check reflects your latest edit until you have.

## Coding conventions

- **Domain changes need a unit test first.** `tests/unit/test_submission_gate.py`
  parametrises over every `ApplicationState` precisely so adding a state without
  deciding where it fits fails the suite — follow that pattern for new enums.
- **Service-layer changes that touch HTTP behaviour need an integration test.**
  `tests/integration/` runs against a real (locally-managed) Postgres; a fixture
  that only flushes instead of commits will not be visible to the HTTP client's
  separate session — commit explicitly when a test needs cross-session visibility.
- **Every new backend dependency**: check it resolves against currently-pinned
  versions (`pyproject.toml` / `package.json`) before adding it — this project has
  already hit real peer-dependency conflicts (TypeScript is held at 5.9.x because
  `typescript-eslint` doesn't support 6+ yet; check before bumping it).
- **Frontend types in `src/types/api.ts` are hand-written**, not generated. Keep
  them in sync with `backend/app/schemas/*.py` by hand when either changes.
- **Never add a `console.log`/`print` for debugging and leave it in.** Backend
  uses `structlog` via `app.core.logging.get_logger`; redact anything sensitive
  through `app/domain/redaction.py` before logging it.
- **Don't silently narrow or widen requested scope.** If a request's scope turns
  out to be ambiguous or risky, say so and ask, rather than guessing quietly.

## Known traps (found the hard way)

- SQLAlchemy 2.1 deprecated `Select.distinct(*cols)`; use
  `select(...).ext(postgresql.distinct_on(col))` instead (see `app/services/jobs.py`).
- A lazy-loaded relationship accessed outside an eager-loaded query raises
  `MissingGreenlet` under the asyncio driver — not a clear error. If you add a new
  field to a response schema that reads a relationship, add `selectinload(...)` for
  it at every call site that builds that response, not just the one you tested.
- `app/domain/redaction.classify_field` matches hint fragments against the field
  name; field names use `snake_case` while the hints read as prose — separators are
  folded to spaces before matching. If you add a new sensitive-field hint, make
  sure it still matches after that fold (`date_of_birth` → `date of birth`).
- Number formatting in the frontend (`src/lib/format.ts`) explicitly pins
  `"en-US"` rather than using the viewer's locale — a salary figure must group
  digits the same way for every reader. Don't revert that to `undefined`.
