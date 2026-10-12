# CareerPilot — Architecture & Implementation Plan

Status: v1, 2026-10-11. This document is the contract for how CareerPilot is built.
It is updated at the end of every phase.

> **Amendment, Phase 1 completion (2026-10-12):** the user asked not to use Docker.
> Local development runs on PostgreSQL 16 (not 17) managed directly by the backend
> via the `pgserver` package — no Docker, no system service. `docker-compose.yml`
> and the Docker-based setup described in §3 and §6 below were never built and are
> superseded by [`README.md`](../README.md), which reflects what actually runs.
> Redis, RQ, and the worker container remain planned for Phase 4 as described.

---

## 0. Reference repositories — findings and licence position

Two repositories were named as references.

| Repo | What it contributes conceptually | Licence as observed 2026-10-11 |
|---|---|---|
| `elbin0324/auto-apply` | Job ingestion pipeline, LLM scoring, Redis worker stages, tracking dashboard, SSE updates | **Proprietary — "all rights reserved", no OSS licence file** |
| `echo0715/dawn` | Real Chromium browser agent over CDP, `request_human_intervention(reason)` pattern, review-and-resume UI | **No licence file present** |

### Consequences (important)

Neither repository grants a licence to copy, modify, or redistribute its source.
Absent a licence, default copyright applies: all rights reserved.

Therefore:

1. **We do not clone, vendor, submodule, or copy either repository into this project.**
2. We do not copy code, file layouts transcribed line-for-line, prompt text, or
   distinctive comments from either project.
3. We *do* learn from their publicly documented architecture. General architectural
   ideas — "use a staged Redis worker pipeline", "pause the agent and ask a human when
   you hit a CAPTCHA" — are unprotectable concepts, and we implement them independently.
4. `docs/ATTRIBUTION.md` records this inspiration honestly without implying a licence
   we do not have.

If the project is ever published, re-check this position, because a repository can
add a licence later.

### Architectural lessons taken (ideas only)

From `auto-apply`:

- Separate ingestion from enrichment from scoring as distinct queue stages, so a slow
  or failing LLM never blocks job discovery.
- Persist the score *components*, not just the total, or the UI cannot explain itself.
- A tracker with explicit per-application state is the backbone of the product.
- SSE is sufficient for agent activity; websockets are not needed early.

From `dawn`:

- A real browser beats an HTTP client for application forms, because forms are
  JS-driven and ATS pages are hostile to naive POSTing.
- The single most important primitive is an explicit "I need a human" signal with a
  machine-readable *reason*, surfaced in the UI with a resume control.
- Agent loops must read page state back. An action returning successfully is not
  evidence that anything changed.

Lessons deliberately **rejected**:

- `auto-apply`'s "configurable thresholds and daily caps driving automated
  submissions" is exactly what our safety requirements forbid. CareerPilot never
  auto-submits.
- `dawn` gives a general-purpose vendored browser agent broad control of the browser.
  We use deterministic per-ATS adapters and confine the LLM to uncertain field mapping.

---

## 1. Architecture

### 1.1 Shape

A monorepo with one Python backend, one React frontend, and Postgres/Redis
infrastructure. No microservices. Workers are separate *processes*, not separate
services with their own APIs.

```
                   +------------------------------------------+
                   |  React + Vite SPA (frontend/)            |
                   |  TanStack Query - React Router - Tailwind|
                   +---------------+--------------------------+
                        REST /api/v1  +  SSE /api/v1/events
                                   |
                   +---------------v--------------------------+
                   |  FastAPI (backend/app/api)               |
                   |  - auth, ownership, permission checks    |
                   |  - THE submission gate lives here        |
                   +---------------+--------------------------+
                                   |
        +--------------------------+-------------------------------+
        |                          |                               |
+-------v---------+    +-----------v-----------+     +-------------v------------+
| domain/         |    | services/             |     | connectors/              |
| PURE logic      |    | orchestration over DB |     | Greenhouse - Lever -     |
| - matching      |    | - profile, jobs,      |     | Ashby - career pages     |
| - state machine |    |   applications,       |     | (normalise -> JobRecord) |
| - dedup         |    |   notifications       |     +-------------+------------+
| - provenance    |    +-----------+-----------+                   |
| - submission    |                |                               |
|   gate          |                |                               |
+-----------------+                |                               |
                   +---------------v--------------------------+    |
                   | Postgres 17 + pgvector  -  Redis 8       |<---+
                   +---------------+--------------------------+
                                   |
        +--------------------------+--------------------------+
        | workers/ (RQ)                                       |
        | discover -> normalise -> match -> notify            |
        +--------------------------+--------------------------+
                                   |
                   +---------------v--------------------------+
                   | agents/ (LangGraph)                      |
                   | prepare - fill - verify; each node a     |
                   | persisted checkpoint, interruptible      |
                   +---------------+--------------------------+
                                   |
                   +---------------v--------------------------+
                   | browser/ (Playwright - Chromium)         |
                   | deterministic ATS adapters, a11y tree    |
                   | NEVER has a submit capability wired to   |
                   | anything the LLM can call                |
                   +------------------------------------------+
```

### 1.2 The layering rule that matters most

`domain/` is pure Python: no SQLAlchemy, no FastAPI, no network, no LLM, and no clock
reads except through an injected parameter. Everything that decides *whether an
application may be submitted* lives there, as functions over plain dataclasses, and is
unit-testable without any infrastructure.

`services/` may touch the database. `api/` may touch HTTP. `agents/` and `browser/`
may touch the outside world. Dependencies point inward only. An LLM is never on the
path between a user's click and a state transition that matters.

### 1.3 Where the LLM is and is not

| Task | Who does it |
|---|---|
| Hard filters (role, location, employment type, eligibility) | Deterministic code |
| Match score and component weights | Deterministic code |
| Which profile evidence is relevant | Vector retrieval, deterministic ranking |
| Prose: match explanation, cover letter draft | LLM, clearly labelled as generated |
| Mapping an unrecognised form field to a known fact | LLM, output validated by Pydantic; low confidence means `NEEDS_HUMAN` |
| Deciding a form is filled correctly | Deterministic read-back of the live DOM |
| Deciding an application may be submitted | Deterministic backend gate. Never the LLM. |

Vector similarity is retrieval, not proof. A requirement counts as met only when it
maps to a fact that carries provenance.

### 1.4 Trust boundary

Job descriptions, web pages, resumes, and retrieved documents are **untrusted data**.
They are passed to the LLM inside clearly delimited, labelled data blocks, never
concatenated into the instruction region of a prompt. Tool permissions are decided by
the caller before the model runs, never by text the model read. Prompt injection in a
job description can at worst produce a bad suggestion that a human must still approve;
it can never reach the submission gate.

---

## 2. Directory structure

```
career_pilot/
  CLAUDE.md                     architecture, commands, safety constraints, conventions
  README.md                     setup, quickstart, verification
  .env.example                  placeholders only
  docker-compose.yml            postgres(pgvector) - redis - api - worker - web
  Makefile                      one entry point per task
  docs/
    PLAN.md  ARCHITECTURE.md  API.md  SCHEMA.md
    RISKS.md  TROUBLESHOOTING.md  ATTRIBUTION.md  SAFETY.md
  backend/
    pyproject.toml              deps, ruff, mypy, pytest config
    Dockerfile  alembic.ini
    alembic/versions/
    app/
      main.py                   app factory, middleware, error handlers
      core/                     config - security - logging - errors - clock - ids
      db/                       base - session - types
      models/                   SQLAlchemy ORM, one module per aggregate
      schemas/                  Pydantic request/response contracts
      domain/        <-- PURE   states - matching - normalise - dedup -
                                provenance - approval - submission_gate - redaction
      services/                 profile - jobs - matching - applications -
                                notifications - events
      connectors/               base - greenhouse - lever - ashby - career_page  (ph.2)
      agents/                   graph - nodes - prompts - llm provider           (ph.3+)
      browser/                  session - adapters/* - inspect - fill - verify   (ph.5)
      workers/                  queues - tasks                                   (ph.4)
      api/deps.py  api/v1/*     routers
      seed/                     demo_jobs.py - labelled DEMO, never "live"
    tests/
      unit/                     domain logic. no DB, no network. runs anywhere.
      integration/              API + Postgres. skips cleanly if no DB.
      browser/                  mock ATS forms served locally                     (ph.5)
  frontend/
    package.json  vite.config.ts  tsconfig.json
    src/
      main.tsx  App.tsx  router.tsx
      lib/                      api client - query client - formatters - cn
      components/ui/            shadcn-convention primitives
      components/               domain components (JobCard, MatchExplanation, ...)
      pages/                    Landing Dashboard JobFeed JobDetail Profile
                                Prepare Review Tracker Activity Notifications Settings
      test/                     setup - fixtures clearly labelled as fixtures
```

---

## 3. Dependencies and compatibility

Versions verified against PyPI / npm on 2026-10-11.

### Backend — Python 3.12

Local interpreters present: 3.12.x, 3.10.11, 3.8. **3.12 is required**, because
SQLAlchemy 2.1 declares `requires-python >= 3.11`. `python` on PATH is 3.10, so all
commands use the `py -3.12` launcher or the project venv.

| Package | Version | Note |
|---|---|---|
| fastapi | 0.143.x | |
| uvicorn[standard] | 0.54.x | |
| pydantic | 2.14.x | all external data validated here |
| pydantic-settings | 2.15.x | single config layer |
| sqlalchemy[asyncio] | 2.1.x | **needs py >= 3.11** |
| alembic | 1.20.x | async env.py |
| asyncpg | 0.32.x | app driver |
| psycopg[binary] | 3.3.x | sync driver for Alembic and test DB bootstrap |
| pgvector | 0.5.x | extension enabled ph.1, columns added ph.3 |
| redis | 8.1.x | |
| rq | 2.12.x | Redis-backed queue. Chosen over Celery: smaller surface, no broker zoo |
| sse-starlette | 3.5.x | |
| pyjwt | 2.15.x | **chosen over python-jose**, which is effectively unmaintained |
| pwdlib[argon2] | 0.3.x | **chosen over passlib**, which breaks against bcrypt 5.x |
| structlog | 26.1.x | structured logs and correlation IDs |
| tenacity | 9.2.x | bounded retries for connectors |
| pypdf | 6.20.x | resume text extraction (ph.3) |
| langgraph | 1.2.x | ph.3+ |
| playwright | 1.63.x | ph.5 |
| pytest / pytest-asyncio | 9.1.x / 1.4.x | |
| httpx | 0.28.x | test client and connector HTTP |
| ruff | 0.17.x | lint and format |
| mypy | 2.4.x | |

### Frontend — Node 24

| Package | Version | Note |
|---|---|---|
| react / react-dom | 19.3.x | |
| typescript | **5.9.3** | *not* 7.0.x: `typescript-eslint@8` declares `typescript >=4.8.4 <6.1.0`, so TS 7 would break linting. Revisit when typescript-eslint ships TS 7 support. |
| vite | 8.3.x | |
| @vitejs/plugin-react | 6.1.x | peer `vite ^8` ok |
| tailwindcss + @tailwindcss/vite | 4.3.x | CSS-first config, no tailwind.config.js |
| react-router-dom | 7.18.x | |
| @tanstack/react-query | 5.104.x | |
| vitest | 5.0.x | peer vite ^6.4 / ^7 / ^8 ok |
| @testing-library/react | 16.3.x | |
| @playwright/test | 1.64.x | ph.5 |
| zod | 4.6.x | validate API responses at the boundary |
| react-hook-form | 7.90.x | |
| lucide-react, clsx, tailwind-merge | current | |

**shadcn/ui**: its conventions are followed and primitives live in
`src/components/ui/`, hand-written rather than CLI-generated. This keeps the dependency
list short and avoids a codegen step in CI. Registry components can be added later
because the folder layout and `cn()` helper match.

### Infrastructure

- `pgvector/pgvector:0.8.7-pg17-trixie` — Postgres 17 with the extension preinstalled.
  Postgres 18 tags exist; 17 chosen as the conservative current major.
- `redis:8-alpine`.
- Docker Desktop 28.0.4 / Compose v2.34 are installed. **The daemon was not running at
  the time of writing**, which is why the test suite is split so domain tests never
  need it.

### Deliberately excluded

Kafka, Kubernetes, a separate vector database, a GraphQL layer, a state-management
library beyond TanStack Query, and any paid API binding. None solves a demonstrated
need yet.

---

## 4. Database schema

Full rationale in `docs/SCHEMA.md`. Primary keys are UUIDv4. Every table has
`created_at`/`updated_at` (timestamptz, UTC). Every row reachable by a request carries
a path to a `user_id`, so ownership is always checkable.

```
users --+-- profiles --+-- education_records
        |              +-- skills
        |              +-- projects ---------+
        |              +-- experience_records+
        |              +-- certifications    |
        +-- resumes ------------------------+ |
        +-- profile_evidence <--------------+-+--- (source_kind, source_id)
        +-- notifications
        +-- applications --+-- application_answers
        |                  +-- application_fields     (ph.5 browser field records)
        |                  +-- application_events
        |                  +-- approval_records
        +-- agent_runs ----- tool_executions
        +-- job_match_assessments

companies -- jobs --+-- job_requirements
                    +-- job_match_assessments
job_sources -- jobs
```

Decisions worth stating:

- **`jobs.posted_at` is nullable and paired with `posted_at_precision`**
  (`exact` / `date_only` / `unknown`). A missing date is never rendered as "posted
  recently". No posting date is ever synthesised.
- **`UNIQUE (job_source_id, source_job_id)`** is the ingestion idempotency key.
- **`jobs.fingerprint`** is a deterministic hash of normalised company, title, and
  location; indexed; used for cross-source dedup. Duplicates are *linked*, not deleted,
  and the canonical `application_url` of each source is preserved.
- **`UNIQUE (user_id, job_id) WHERE state NOT IN ('CANCELLED','FAILED')`** — a partial
  unique index that structurally prevents duplicate live applications.
- **`applications.form_state_hash`** and **`approval_records.form_state_hash`**: an
  approval is bound to the exact form state it approved. Any later change invalidates
  it. This is the mechanism behind safety requirement 3.
- **`applications.submission_idempotency_key`** is UNIQUE, so a double-clicked submit
  cannot become two submissions.
- **`application_answers.provenance`** is NOT NULL. There is no way to persist an answer
  without recording where it came from.
- **`jobs.is_demo_data`** — seeded demo rows are flagged in the database and rendered
  with a visible "demo data" badge. Seeded jobs are never presented as live results.
- Screenshots, resume binaries, and artefacts are stored at a filesystem/object path;
  only metadata and the path go in the row. Nothing sensitive goes into pgvector.
- `users.password_hash` is argon2. No plaintext password, cookie, or session secret is
  ever written to a row, a log line, or a prompt.

---

## 5. API contract

Full schemas in `docs/API.md`. All routes under `/api/v1`. Bearer JWT except `auth/*`
and health. Errors use a single envelope:
`{error: {code, message, details?, correlation_id}}`.

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/register`, `/auth/login` | argon2, JWT access token |
| GET | `/auth/me` | |
| GET/PATCH | `/profile` | owner-scoped |
| CRUD | `/profile/{education,skills,projects,experience,certifications}` | |
| POST/GET/DELETE | `/resumes`, `/resumes/{id}` | multipart upload |
| POST | `/resumes/{id}/extract`, `/resumes/{id}/confirm-extraction` | extraction is a *proposal* until confirmed (ph.3) |
| GET | `/jobs` | filter, sort, paginate |
| GET | `/jobs/{id}` | |
| POST | `/jobs/discover` | enqueue connector run (ph.2) |
| POST | `/jobs/{id}/analyze` | deterministic assessment, persisted |
| GET | `/jobs/{id}/match` | stored assessment and explanation |
| POST/GET | `/applications`, `/applications/{id}` | |
| POST | `/applications/{id}/prepare` | to READY or WAITING_FOR_USER_INPUT |
| POST | `/applications/{id}/fill` | browser agent (ph.5) |
| POST | `/applications/{id}/approve` | binds approval to `form_state_hash` |
| POST | `/applications/{id}/submit` | **the gate.** 409 plus blockers unless every condition holds |
| POST | `/applications/{id}/cancel` | |
| PATCH | `/applications/{id}/outcome` | user-set interview / offer / rejected |
| GET | `/notifications`, POST `/notifications/{id}/read` | |
| GET | `/agent-runs`, `/agent-runs/{id}` | includes tool executions |
| GET | `/events` | SSE agent activity |

Hard rule: **there is no endpoint, tool, or code path by which a model can approve or
submit.** `/approve` and `/submit` require an authenticated human bearer token.

### The submission gate

`POST /applications/{id}/submit` runs `domain.submission_gate.evaluate(...)`, a pure
function returning `allowed` plus a list of blockers. Every one of these must hold:

1. caller is authenticated **and** owns the application
2. an `approval_record` exists for *this* application
3. `approval.form_state_hash == application.form_state_hash` (nothing changed since approval)
4. a verification result exists, is `PASSED`, and is newer than `max_verification_age`
5. no required field is unresolved and no field is `NEEDS_HUMAN`
6. no unresolved intervention reason
7. state is exactly `WAITING_FOR_APPROVAL`
8. no prior submission for this application (idempotency key unused)
9. the job is not known to be closed

Failure returns `409` listing the blockers. The frontend button is a convenience, not a
boundary. If the submission result is ambiguous, state becomes `SUBMITTED_UNCERTAIN`
and requires human reconciliation; it is never auto-retried.

---

## 6. Phased task checklist

### Phase 1 — end-to-end foundation (building now)

- [ ] Monorepo scaffold, Makefile, Docker Compose, `.env.example`
- [ ] FastAPI app factory, config layer, structured logging, correlation IDs, error envelope
- [ ] Postgres and Alembic baseline migration, pgvector extension enabled
- [ ] Auth: register/login/me, argon2 and JWT, ownership dependency
- [ ] Profile CRUD including education, skills, projects, experience, certifications
- [ ] Jobs: list and detail with filtering; **flagged** demo seed data
- [ ] Deterministic matching engine: hard filters, component scores, explanation
- [ ] Application state machine with persisted events
- [ ] Application tracker endpoints; duplicate-application prevention
- [ ] Submission gate implemented and enforced now (correctly refuses; no browser yet)
- [ ] Frontend: landing/login, dashboard, job feed, job detail and explanation, profile, tracker
- [ ] Tests: unit (no infra) and integration (Postgres); lint; type check

### Phase 2 — connectors and discovery

Connector interface; Greenhouse, Lever, Ashby; configurable career pages; rate limiting
and robots policy; bounded retries; normalisation and cross-source dedup; connector
health records. Honest coverage language in the UI.

### Phase 3 — evidence, resumes, RAG

Resume upload, extraction, then **user confirmation** before facts become authoritative.
`profile_evidence` plus pgvector embeddings. Evidence-backed matching. LLM explanation
and cover-letter drafting behind a configurable provider. Generated prose visually
separated from verified facts everywhere.

### Phase 4 — notifications and workers

**Amendment, Phase 4A completion (2026-10-12):** this phase shipped in two
milestones rather than as RQ/Redis from the start. An investigation at the Phase
3→4 boundary found that `redis`/`rq` were declared (in `pyproject.toml`'s optional
`workers` extra) but never installed or imported, that `POST /jobs/discover`
already ran synchronously by design, and that `app.services.notifications` was
fully implemented with zero callers — the gap was wiring, not missing
infrastructure, and there is no Docker-free, locally-embeddable Redis equivalent
to the `pgserver` story `app.core.devdb` already gives Postgres. **4A** (shipped):
scheduled discovery and score-threshold, database-deduplicated in-app
notifications, via a new `python -m app.cli sweep` command intended for an
OS-level scheduler (Task Scheduler/cron — see `docs/SCHEDULING.md`), guarded by a
Postgres advisory lock against overlapping runs, reusing
`discovery.run_discovery`/`matching.analyze_many`/`notifications.notify_job_matches`
unchanged. **4B** (not yet built): personalized application preparation —
auto-advancing a matched job into an `Application` in `PREPARING`. RQ/Redis and
SSE-over-Redis-pub/sub remain deferred until a genuine multi-process or
multi-worker need is demonstrated, not built speculatively. Email and Telegram
remain unimplemented modular adapters.

### Phase 5 — browser agent

Playwright/Chromium session management; a11y-tree and DOM inspection; deterministic
Greenhouse/Lever/Ashby adapters; field mapping from provenanced facts; fill then
**read-back verify**; resume upload; artefact capture with redaction; intervention
signals for login, CAPTCHA, MFA; unknown layouts fail safe. Mock-form test suite,
including a test asserting nothing is ever submitted during fill.

### Phase 6 — review and submission

Full review screen; approval binding and invalidation; the gate end-to-end;
idempotency; uncertain-result reconciliation; audit log surface.

### Phase 7 — intelligence, deployment, polish

Corpus analytics with sample sizes; skill-gap view; deployment config; accessibility
pass; performance; docs.

---

## 7. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **A submission happens without genuine human consent** | Low | Catastrophic — a real application sent to a real employer | Gate is a pure function with 9 conditions, unit-tested; no LLM-reachable submit path; approval bound to form hash; browser layer has no submit capability until ph.6; a standing test asserts fill never submits |
| R2 | Fabricated candidate facts reach an employer | Medium | Severe — misrepresentation | Every answer requires provenance (NOT NULL); unknown means `NEEDS_HUMAN`; generated prose labelled; no inference of personal details; review screen shows source per field |
| R3 | ATS markup changes break adapters silently | High | Medium | Deterministic adapters that fail closed; read-back verification; adapter health metrics; never assume an action worked |
| R4 | Scraping violates terms, robots, or rate limits | Medium | High — legal and access loss | Prefer documented public ATS endpoints; per-source rate limits and backoff; honour robots where applicable; no access-control evasion; no anti-bot bypass; sources declared per connector |
| R5 | Prompt injection via job description or page content | High | High | Untrusted content in labelled data blocks only; permissions decided before the model runs; model output validated by Pydantic; model cannot change state or permissions |
| R6 | PII or credential leakage | Medium | Severe | Argon2 hashes; no plaintext secrets in rows, logs, or prompts; redaction layer for artefacts and field values; screenshots scrubbed; nothing sensitive in pgvector; `.env` git-ignored |
| R7 | Match score misread as "chance of an offer" | High | Medium — user harm | Score typed and labelled as a ranking heuristic in the API *and* the UI; component breakdown always shown; no probability language anywhere |
| R8 | Seeded demo jobs mistaken for live postings | Medium | High — trust | `is_demo_data` flag in the DB, in the API response, and as a visible badge; README states Phase 1 ships no live jobs |
| R9 | CAPTCHA or MFA tempts a bypass "fix" | Medium | Severe | Explicitly out of scope; intervention states instead; documented in `docs/SAFETY.md` |
| R10 | Browser flakiness produces false "filled" claims | High | Medium | Verification reads live DOM; `VERIFICATION_FAILED` is a first-class state; screenshots are supporting, not primary, evidence |
| R11 | Ambiguous submit result causes a double application | Low | High | Unique idempotency key; `SUBMITTED_UNCERTAIN` plus manual reconciliation; never blind-retry |
| R12 | Reference-repo code contaminates this codebase | Low | Severe — licence breach | No clone, no vendor, no copy; independent implementation; `docs/ATTRIBUTION.md` |
| R13 | Dependency churn (TS 7, Vite 8, SQLAlchemy 2.1) breaks the build | Medium | Low | Versions pinned and peer-checked; TS held at 5.9.3 for typescript-eslint; rationale recorded above |
| R14 | Docker daemon unavailable blocks all testing | Medium | Low | Domain tests are pure and infra-free; DB tests skip with an actionable message |

---

## 8. Open decisions needing the project lead

1. **Runtime LLM provider.** Not needed for Phase 1, which is fully deterministic.
   Phase 3 needs a decision. A Claude Code subscription does **not** grant
   application-level API access, so a paid provider key would be required — that is a
   cost, so it will not be configured without explicit approval. Phase 3 will ship
   behind a provider interface with a deterministic no-LLM fallback, so the product
   stays functional and testable without any paid service.
2. **Hosting target** for Phase 7. No action needed yet.
