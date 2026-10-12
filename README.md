# CareerPilot

An agentic job-search and application platform. CareerPilot discovers jobs, scores
them against your profile with a transparent, deterministic matching engine, tracks
applications through an explicit state machine, and — from a later phase — fills and
verifies employer forms with a real browser. **No application is ever submitted
without your explicit, informed approval of the exact form.** See
[`docs/SAFETY.md`](docs/SAFETY.md) for the full safety contract.

This is a real, working implementation, built in phases. **Phase 1 — a working
end-to-end foundation — is complete.** See [Project status](#project-status) below for
exactly what that means.

## Stack

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript, Vite, Tailwind CSS v4, TanStack Query, React Router |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.1 (async), Alembic |
| Database | PostgreSQL 16 + pgvector, run locally with **no Docker** (see below) |
| Agent orchestration | LangGraph (from Phase 3) |
| Browser automation | Playwright (from Phase 5) |
| Testing | pytest (backend), Vitest + React Testing Library (frontend) |

## Quickstart

### Prerequisites

- **Python 3.12** available via the `py` launcher (`py -3.12 --version`). Windows,
  macOS and Linux are all supported; commands below show the Windows (`py`) form,
  with POSIX equivalents noted.
- **Node.js 20+** and npm.
- **No Docker, no PostgreSQL install, no system service required.** The backend
  manages its own local PostgreSQL 16 server (via the `pgserver` package, which
  ships real PostgreSQL binaries with pgvector built in) in `var/pgdata/` at the
  repository root. It starts automatically the first time anything touches the
  database.

### 1. Backend

```bash
cd backend
py -3.12 -m venv .venv                 # POSIX: python3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"   # POSIX: .venv/bin/python -m pip install -e ".[dev]"

# Apply migrations (this also starts the local database automatically)
.venv/Scripts/python.exe -m app.cli db-upgrade

# Load labelled demo data and a demo account
.venv/Scripts/python.exe -m app.cli seed
#   -> creates demo@example.com / careerpilot-demo-2026

# Run the API
.venv/Scripts/python.exe -m app.cli serve --port 8000
```

The API is now at **http://localhost:8000**, with interactive docs at
`http://localhost:8000/api/v1/docs`.

### 2. Frontend

In a second terminal:

```bash
cd frontend
cp .env.example .env.local     # VITE_API_BASE_URL=http://localhost:8000
npm install
npm run dev
```

Open **http://localhost:5173** and sign in with the demo account
(`demo@example.com` / `careerpilot-demo-2026`), or register a new one.

### 3. Verify it's working

- `GET http://localhost:8000/health` should return `{"status": "ok", ...}`.
- The job feed should show seeded postings, each visibly labelled **"Demo data"** —
  these are realistic sample postings written for this repository, not live jobs.
  See [`docs/SAFETY.md` §12](docs/SAFETY.md).

## Day-to-day commands

All backend commands run through `app.cli` (`python -m app.cli --help` lists them
all) so there's one entry point regardless of platform:

```bash
# Database
python -m app.cli db-status          # is the local database running?
python -m app.cli db-upgrade         # apply migrations
python -m app.cli db-revision -m "message" --autogenerate
python -m app.cli db-reset --yes     # drop everything and re-migrate (destructive)
python -m app.cli db-stop            # stop the local database

# Data
python -m app.cli seed               # load labelled demo jobs + demo account
python -m app.cli seed --reset       # remove previously seeded jobs first

# Server
python -m app.cli serve --port 8000
```

Testing and code quality (from `backend/`, with the venv active):

```bash
pytest                               # unit + integration (304 tests)
pytest tests/unit -q                 # pure domain logic only — no database needed
pytest -m integration                # API + real database
ruff check app tests                 # lint
ruff format app tests                # format
mypy app tests                       # type check (strict mode)
```

Frontend (from `frontend/`):

```bash
npm run dev                          # dev server, http://localhost:5173
npm run build                        # production build (runs tsc -b first)
npm run lint                         # eslint
npm run typecheck                    # tsc -b --noEmit
npm test                             # vitest (23 tests)
```

## Project structure

```
career_pilot/
  backend/
    app/
      domain/        pure business logic — no database, no network, no clock.
                      This is where every safety rule lives: the state machine,
                      the submission gate, approval binding, provenance, redaction,
                      the matching engine. Unit-tested with zero infrastructure.
      services/       orchestration over the database, built on top of domain/
      api/v1/         FastAPI routers — HTTP only, no business logic
      models/         SQLAlchemy ORM
      schemas/        Pydantic request/response contracts
      core/           config, security, logging, errors, the embedded-database
                      manager (devdb.py)
      seed/           labelled demo data
    alembic/          migrations
    tests/
      unit/           domain logic — no infrastructure (237 tests)
      integration/    real Postgres, full HTTP stack (67 tests)
  frontend/
    src/
      pages/          one file per route
      components/     shared UI, including the shadcn-style primitives in ui/
      lib/            API client, auth context, formatting, query client
      types/api.ts    hand-written types mirroring the backend's Pydantic schemas
  docs/
    PLAN.md           the original architecture and phased plan
    SAFETY.md         the full safety contract — read this first
    ATTRIBUTION.md    the licensing position on the two reference repositories
```

## Project status

### Phase 1 — complete

- FastAPI backend, React frontend, PostgreSQL + migrations, all connected and
  verified end-to-end through a real browser session.
- Registration and login (JWT, Argon2 password hashing).
- Full profile management: basic info, preferences, work authorisation, skills,
  projects, experience, education, certifications — add/edit/delete, all live.
- Nine realistic, clearly labelled demo postings covering the edge cases that
  matter: an unknown posting date, a closed posting, cross-source duplicates, no
  stated requirements, internship/contract types.
- A deterministic matching engine: hard filters, six weighted score components,
  per-requirement verdicts, and a full explanation — never a bare number, always
  labelled as a ranking heuristic, never as a probability.
- The application state machine and the submission gate, both enforced
  server-side and fully exercised through the UI.
- 304 backend tests (unit + integration) and 23 frontend tests, all passing.

### Not yet built

- **Live job connectors** (Greenhouse, Lever, Ashby, career pages) — Phase 2.
- **Resume upload and extraction** — Phase 3. The endpoint exists and returns a
  documented 501 rather than pretending to work.
- **The browser agent** that fills and verifies forms — Phase 5. Until it exists,
  no application can reach `WAITING_FOR_APPROVAL` through normal use, and
  `/submit` correctly refuses every attempt. A "record that you applied yourself"
  path exists as the honest interim option, explicitly marked as unverified.
- **Background workers, scheduled discovery, email/Telegram notifications** —
  Phase 4.
- **An LLM provider** — not configured. Matching is fully deterministic; no API
  key is required or used anywhere in Phase 1.

See [`docs/PLAN.md`](docs/PLAN.md) for the full phased roadmap and
[`docs/SAFETY.md`](docs/SAFETY.md) for the safety contract every later phase has to
hold to.

## Troubleshooting

**`db-upgrade` or `serve` hangs on first run.** The embedded PostgreSQL server is
initialising its data directory in `var/pgdata/` at the repository root — this takes
a few seconds the very first time, then is instant on every subsequent run.

**"Invalid access token" after restarting the backend.** Expected in development:
`CAREERPILOT_SECRET_KEY` defaults to a fresh random value each process start unless
you set one explicitly in `.env`, so a restart invalidates existing sessions. Sign in
again. Set `CAREERPILOT_SECRET_KEY` in `.env` (see `.env.example`) if you want tokens
to survive a restart.

**Frontend can't reach the API.** Confirm `backend/` is running on port 8000 and that
`frontend/.env.local` has `VITE_API_BASE_URL=http://localhost:8000`. CORS is
pre-configured for `http://localhost:5173` in `.env.example`.

**Integration tests skip instead of running.** They need the local database; run any
`python -m app.cli db-*` command once to start it, or just run the tests — they start
it automatically and only skip if that genuinely fails, with the reason printed.

**Port 5433/8000/5173 already in use.** Nothing in this project hardcodes those as
exclusive; stop whatever else is using them or change `CAREERPILOT_*` / `VITE_*` /
Vite's `--port` as needed.
