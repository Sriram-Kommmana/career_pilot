# CareerPilot — Handoff at the Phase 3 → Phase 4 Boundary

Written 2026-10-12, verified directly against the repository (not from memory).
Everything below was checked by running commands or reading files during this
session; none of it is carried over unverified from a prior conversation.

## 1. Repository state

- Git repo root: `C:\web dev projects\career_pilot` (the backend/frontend
  directories are not separate repos).
- Branch: `main`. Up to date with `origin/main`.
- HEAD: `1530ec5e7b076108085da7e061325415934328be` —
  "Add unit tests for file validation and resume extraction, and implement
  resume upload functionality in the frontend"
- `git status`: **clean working tree**, nothing staged or unstaged.
- Last 3 commits on `main`:
  ```
  1530ec5 Add unit tests for file validation and resume extraction, and implement resume upload functionality in the frontend
  501c6e6 Add integration and unit tests for job discovery and connector functionality
  d636ff0 feat: add Settings and Tracker pages with user account management and application tracking
  ```
- No uncommitted changes, no untracked files, nothing unexpected. All Phase 3
  work (including the targeted acceptance-audit fixes from the prior session —
  the pypdf blank-line fix, the provenance-exposure fix, the test-storage
  isolation fix) is already committed and pushed to `origin/main`.

## 2. Phase status

| Phase | `docs/PLAN.md` description | Status |
|---|---|---|
| 1 | End-to-end foundation: profile, matching, applications/state machine, submission gate | **Complete** |
| 2 | Connectors and discovery (Greenhouse) | **Complete** |
| 3 | Evidence, resumes, RAG | **Complete** (deterministic/rule-based only — see §5). Passed a targeted acceptance audit this week that found and fixed two real defects (see §8). |
| 4 | Notifications and workers | **Not started.** Some scaffolding already exists (see §9) but the actual Phase 4 work has not begun. |
| 5 | Browser agent | Not started. `POST /applications/{id}/fill` deliberately raises `NotImplementedPhaseError(phase=5)`. |
| 6 | Review and submission | Not started (submission *gate* logic exists and is tested now; the submit endpoint exists per §6, but the browser-driven fill/verify loop that normally precedes it is Phase 5 work). |
| 7 | Intelligence, deployment, polish | Not started. |

## 3. Architecture and file locations

Layering (enforced, not just documented — `app/domain/` imports nothing from
`app/services/` or `app/api/`):

```
app/api/v1/      HTTP only. Routers call services, nothing else.
app/services/    Orchestrates domain logic against the database (SQLAlchemy async).
app/domain/      Pure logic. No DB, no FastAPI, no network, no system clock
                 (now is always a parameter). Unit-tested with zero infrastructure.
```

Backend tree (`backend/app/`):

```
api/v1/      applications.py, auth.py, jobs.py, observability.py, profile.py,
             resumes.py, router.py
core/        clock.py, config.py, devdb.py, errors.py, logging.py, security.py
db/          base.py, session.py, types.py
domain/      approval.py, dedup.py, file_validation.py, matching.py, normalize.py,
             provenance.py, redaction.py, resume_extraction.py, skills.py,
             states.py, submission_gate.py
connectors/  base.py, greenhouse.py, html_text.py
models/      application.py, job.py, observability.py, user.py
schemas/     application.py, auth.py, common.py, job.py, observability.py,
             profile.py, resume.py
services/    applications.py, auth.py, discovery.py, events.py, jobs.py,
             matching.py, notifications.py, profile.py, resumes.py
seed/        demo_jobs.py, runner.py
cli.py       db-start/stop/status/upgrade/downgrade/revision/reset, seed, serve
```

Frontend tree (`frontend/src/`):

```
pages/       Landing, Dashboard, JobFeed, JobDetail, Profile, Resumes, Tracker,
             ApplicationDetail, Notifications, AgentActivity, Settings, NotFound
components/  JobCard, MatchBreakdown, DemoDataBadge, StatusBadge, EmptyState,
             ErrorState, layout/AppShell, layout/ProtectedRoute, ui/* (shadcn-style primitives)
lib/         api.ts (request<T> + postForm for multipart), auth.tsx, format.ts, cn.ts, queryClient.ts
types/api.ts Hand-written types kept in sync with backend/app/schemas/*.py by hand
```

Routes in `App.tsx`: `/login`, `/dashboard`, `/jobs`, `/jobs/:jobId`, `/profile`,
`/resumes`, `/tracker`, `/applications/:applicationId`, `/notifications`,
`/activity`, `/settings`, `*` → NotFound. All but `/login` are behind
`ProtectedRoute`.

No Docker anywhere in the project. Postgres is self-managed via
`app/core/devdb.py` / `python -m app.cli db-start`, matching the project's
explicit no-Docker constraint.

## 4. Database, migrations, state machine, submission gate

**Migrations** (`backend/alembic/versions/`):
1. `20261011_2315_065da2315c77_baseline_schema.py` — the full Phase 1/2 schema.
2. `20261012_1225_73513623a4d8_add_resume_extraction_status_and_error_.py` —
   additive: `resumes.extraction_status` (`VARCHAR NOT NULL DEFAULT 'pending'`),
   `resumes.extraction_error` (`TEXT NULL`). Phase 3's only schema change.

A dedicated `tests/integration/test_migrations.py` checks migrations and
SQLAlchemy models agree (tests themselves use `Base.metadata.create_all`, not
Alembic, so a migration bug can't make the whole suite unrunnable).

**`Resume` model** (`app/models/user.py`): `user_id`, `label`,
`original_filename` (display only — the on-disk filename is derived from the
generated id, never from client input), `content_type`, `size_bytes`,
`content_sha256` (server-computed), `storage_path`, `is_default`,
`extracted_text`, `extraction_payload` (JSONB — the staged, unconfirmed
`ExtractionProposal`), `extraction_confirmed_at`, `extraction_status`,
`extraction_error`.

**`Profile` + 5 child tables**: `EducationRecord`, `Skill`, `Project`,
`ExperienceRecord`, `Certification` — each carries a `provenance` column
(`ProvenanceKind`). `ProfileEvidence` holds free-text evidence chunks that
don't fit a structured shape (e.g. an "Achievements" section).

**Application state machine** (`app/domain/states.py`), `ApplicationState`:
`DISCOVERED → MATCHED → SELECTED → PREPARING → READY → FILLING → VERIFYING →
WAITING_FOR_USER_INPUT / WAITING_FOR_APPROVAL → SUBMITTING → SUBMITTED /
SUBMITTED_UNCERTAIN / FAILED / CANCELLED`. `shortest_path()` walks it legally;
tests parametrize over every state exhaustively
(`tests/unit/test_submission_gate.py` / `test_states.py`).

**Submission gate** (`app/domain/submission_gate.py`) — the one function
allowed to decide whether a real application can be sent:
- Pure (no DB/HTTP/clock — `now` is a parameter), closed-by-default (every
  condition must actively clear), returns *all* blockers at once, and is
  evaluated twice (review screen, then again inside the submit transaction —
  the second call is the one that matters).
- `BlockerCode` enum: `NOT_OWNER`, `NO_APPROVAL`, `APPROVAL_REVOKED`,
  `APPROVAL_STALE`, `APPROVAL_NOT_FOR_THIS_APPLICATION`, `NO_VERIFICATION`,
  `VERIFICATION_FAILED`, `VERIFICATION_INCONCLUSIVE`, `VERIFICATION_STALE`,
  `UNRESOLVED_REQUIRED_FIELDS`, `FIELDS_NEED_HUMAN`, `UNKNOWN_FIELDS_PRESENT`,
  `UNRESOLVED_INTERVENTION`, `WRONG_STATE`, `ALREADY_SUBMITTED`, `JOB_CLOSED`,
  `NO_FORM_STATE`, `NOT_HUMAN_INITIATED`.
- No model can reach it and no model output is an input to it. This is
  unchanged by Phase 3 and must stay unchanged by Phase 4 — Phase 4 has no
  legitimate reason to touch `submission_gate.py`.

## 5. Resume extraction, confirmation, provenance, matching (Phase 3's scope)

**Trust levels, enforced by which function may write what**
(`app/services/resumes.py`):
- `upload_resume` — validates, hashes, stores. Never touches `Profile`.
- `extract_resume` / `build_proposal_out` — runs the deterministic extractor,
  stages the result in `Resume.extraction_payload`. Never touches `Profile`.
- `confirm_extraction` — the **only** function that writes `Profile` child
  rows, only for items the user explicitly accepted, and only creates a new
  record unless the request supplies an existing record's id to update (silence
  = add-as-new, never silently overwrite an existing confirmed row).

**Extraction** (`app/domain/resume_extraction.py`) — pure, deterministic,
no LLM, no network, no DB. `extract(resume_text) -> ExtractionProposal` of
`ExtractedItem`s (`field`, `data`, `source_excerpt`, `locator`, `confidence`,
`needs_review`). Section-heading regex detection; skills reuse
`app.domain.skills.extract_skill_mentions`/`KNOWN_TECHNOLOGIES`; dates are
never guessed — an unparseable date is simply omitted, never defaulted.
`_split_by_date_anchors()` (added during this week's audit) splits a block
that contains more than one date-range match into per-entry sub-blocks, fixing
a real bug: `pypdf.PdfReader.extract_text()` silently drops lines with no
visible text, which deletes the blank-line separators between resume entries
and previously merged two jobs/degrees into one. DOCX was never affected
(`python-docx` preserves empty paragraphs). Both unit
(`TestEntriesWithoutBlankLineSeparators`) and integration
(`TestRealFileExtraction`, against real generated PDF/DOCX bytes through the
actual upload+extract HTTP endpoints) regression tests exist for this.

**Provenance** (`app/domain/provenance.py`), `ProvenanceKind`:
authoritative — `USER_CONFIRMED_PROFILE`, `RESUME_EXTRACTION_CONFIRMED`,
`USER_APPROVED_EVIDENCE`, `PREVIOUSLY_APPROVED_ANSWER`, `USER_DIRECT_INPUT`;
not authoritative — `RESUME_EXTRACTION_UNCONFIRMED`, `GENERATED_UNVERIFIED`,
`UNKNOWN`. `RESUME_EXTRACTION_UNCONFIRMED` is never actually assigned anywhere
in the codebase — unconfirmed extraction output lives only as a raw JSON blob
in `Resume.extraction_payload`, never wrapped in anything the matching or
application-answer path reads, which is structurally stronger than relying on
the enum check alone. `ProjectOut`, `ExperienceOut`, `CertificationOut` all
expose `provenance` in the API response (this was the one gap flagged at the
end of Phase 3 implementation; it was closed during this week's audit —
`EducationOut`/`SkillOut` already had it). The frontend's `Profile.tsx` shows
a "From resume" badge on any child record whose provenance is
`RESUME_EXTRACTION_CONFIRMED`.

**Matching** (`app/domain/matching.py`) — six weighted components, each in
`[0, 1]`, combined and renormalized over whatever components were actually
assessable:
```
skill_overlap   0.30
evidence        0.20
role_relevance  0.20
education       0.10
location        0.10
preferences     0.10
```
`profile_evidence` (confirmed `ProfileEvidence` rows) additively credits the
`evidence` component alongside `Project`/`ExperienceRecord.skills_used` — no
change to the scoring formula, weights, or `ScoreKind` (still exactly one
member, `RANKING_HEURISTIC`) was needed for this. The disclaimer string "A
ranking heuristic used to order jobs. It is not a probability of an interview
or an offer." travels with every match response and renders verbatim in
`MatchBreakdown.tsx`. `evidence_refs` cite `{kind, id, locator}` pointing at
the exact confirmed record that backed a match.

## 6. API endpoints

`app/api/v1/router.py` mounts: `auth`, `profile`, `jobs`, `applications`,
`resumes`, `observability`.

- **auth**: `POST /auth/register`, `POST /auth/login`, `GET /auth/me`
- **profile**: `GET/PATCH /profile`; `POST/PATCH/DELETE` on
  `/profile/education`, `/profile/skills`, `/profile/projects`,
  `/profile/experience`, `/profile/certifications`
- **jobs**: `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/analyze`,
  `POST /jobs/discover`
- **applications**: `POST/GET /applications`, `GET /applications/tracker`,
  `GET /applications/{id}`, `POST /applications/{id}/prepare`,
  `POST /applications/{id}/answer`, `POST /applications/{id}/fill` (501,
  `NotImplementedPhaseError(phase=5)` — intentional, this is the browser-agent
  endpoint), `GET /applications/{id}/review`,
  `POST /applications/{id}/approve`, `POST /applications/{id}/submit`,
  `POST /applications/{id}/record-manual-submission`,
  `POST /applications/{id}/cancel`, `PATCH /applications/{id}/outcome`
- **resumes**: `GET/POST /resumes`, `GET/DELETE /resumes/{id}`,
  `POST /resumes/{id}/extract`, `GET /resumes/{id}/extraction`,
  `POST /resumes/{id}/confirm-extraction`
- **observability**: `GET /observability/notifications`,
  `POST /observability/notifications/{id}/read`,
  `POST /observability/notifications/read-all`,
  `GET /observability/agent-runs`, `GET /observability/agent-runs/{id}`,
  `GET /observability/events` (Server-Sent Events, in-process broker today —
  see §9)

## 7. Test counts and exact verification commands

Run and verified directly in this session, not recalled from memory:

```bash
# Backend, from backend/ with .venv active
pytest -q                      # 452 passed, 0 failed, 0 skipped
ruff check app tests           # All checks passed!
mypy app tests                 # Success: no issues found in 88 source files

# Frontend, from frontend/
npx tsc -b --noEmit             # clean, no output
npx eslint .                    # clean, no output
npx vitest run                  # 4 test files, 23 tests, all passed
```

Test files: 10 backend unit test files, 7 backend integration test files (both
listed in §3's domain/services trees' counterparts under `backend/tests/`), 4
frontend test files (`MatchBreakdown.test.tsx`, `StatusBadge.test.tsx`,
`format.test.ts`, `Landing.test.tsx`).

## 8. Known bugs, limitations, and this week's acceptance-audit findings

A targeted 8-point acceptance audit ran against Phase 3 immediately before this
handoff. Two real defects were found and fixed (both already committed, per
§1):

1. **PDF blank-line collapse (fixed).** `pypdf.extract_text()` drops a line
   with no visible text, deleting the blank-line separators between resume
   entries, which silently merged a two-job PDF into one extracted item (and
   lost that job's skills). DOCX was unaffected. Fixed with
   `_split_by_date_anchors()`; see §5. Regression tests added at both the unit
   and integration level.
2. **Provenance not exposed for Project/Experience/Certification (fixed).**
   `EducationOut`/`SkillOut` had `provenance` since Phase 1; the other three
   schemas didn't. Fixed additively; frontend badge added; 4 new tests added.
3. **Test-storage leak into dev data (fixed, operational not functional).**
   Integration tests had no storage-directory isolation the way they do for
   the database — every resume-upload test wrote a real file into the same
   `var/storage/resumes/` a developer's own `serve` process uses, with no
   cleanup. 352 orphaned files (no matching DB row) had accumulated; cleaned
   up after cross-referencing every file against live `Resume.storage_path`
   rows, explicitly preserving the demo account's resume and leaving 3 live
   files untouched. Fixed going forward via a session-scoped temp directory +
   `atexit` cleanup in `tests/conftest.py`.

**Known limitations still outstanding (not bugs — explicitly out of Phase 3's
scope):**
- No LLM/embedding provider is wired in. Extraction, matching, and evidence
  are all deterministic/rule-based by design (see §10). The code has a narrow
  extension point for a future provider but nothing calls out to one.
- No OCR — a scanned/image-only PDF with no text layer extracts nothing and
  the resume's `extraction_status` becomes `"failed"` with a clear error,
  never a crash or a guess.
- No antivirus/malware scanning on uploaded files (no `Docker`/`clamd` — out
  of scope for a no-Docker local project). Mitigation is structural: parsers
  only ever read a text stream, never render/execute/open macros.
- No "this resume looks outdated" heuristic — deliberately not attempted
  (false positives would be worse than silence).
- `POST /applications/{id}/fill` is an intentional 501 stub for Phase 5
  (browser agent) — not a defect.

## 9. Phase 4 scaffolding that already exists

Some Phase 4 surface was already built ahead of time as part of Phase 1/2/3
work, and should not be re-built from scratch:

- `Notification` model + `app/services/notifications.py` — in-app
  notifications with dedup already enforced at the database level via a
  unique `dedupe_key` per user (not just an application-level check). Email
  and Telegram adapters are deliberately absent pending Phase 4.
- `GET /observability/events` (SSE) already exists and is already documented
  as "Phase 1 publishes through an in-process broker... Phase 4 swaps the
  broker for Redis pub/sub without changing this contract" — i.e. the HTTP
  contract is meant to stay stable; only the backing broker changes.
- `redis>=8.1,<9` and `rq>=2.12,<3` are already pinned in
  `backend/pyproject.toml`, and `Settings.redis_url` already exists in
  `app/core/config.py` (commented `# --- redis (Phase 4) ---`) — **but neither
  package is imported anywhere in `app/` yet.** No queue, no worker process,
  no scheduler exists. This is 100% still Phase 4 work, just with the
  dependency already resolved and pinned.
- Frontend already has `Notifications.tsx` and `AgentActivity.tsx` pages
  consuming the existing notification/event endpoints.

**Open question worth raising before deep Phase 4 work starts:** `docs/PLAN.md`
describes Phase 4 as "RQ queues and worker processes; scheduled discovery;
score threshold and filters; notification dedup; in-app notifications and
history; SSE wired to Redis pub/sub" — i.e. it assumes a locally-run Redis
instance, the same way Postgres is locally self-managed via
`app/core/devdb.py`. No equivalent `devdb.py`-style local Redis manager exists
yet. If the no-Docker constraint holds (it does, per `CLAUDE.md` point 8),
Phase 4 will need either a `devredis.py` in the same spirit, or a decision to
keep the in-process broker for longer and defer the Redis swap. This should be
confirmed with the project lead before committing to an RQ/Redis-based design.

## 10. Agreed Phase 4 scope and constraints

As directed going into this phase:

- **Deterministic logic only** — no paid LLM API calls, consistent with the
  Phase 3 decision to ship extraction/matching fully rule-based. Any
  scheduling, filtering, or threshold logic in Phase 4 should remain
  rule-based (e.g. a score-threshold notification trigger, not a
  model-scored one).
- **No Docker** — unchanged project-wide constraint (`CLAUDE.md` point 8). See
  the open question in §9 about how this interacts with `docs/PLAN.md`'s
  Redis assumption.
- **No browser automation** — Phase 5's territory, not Phase 4's. Nothing in
  Phase 4 should touch `app/browser/` (doesn't exist yet) or the
  `POST /applications/{id}/fill` 501 stub.
- **No application submission changes** — the submission gate
  (`app/domain/submission_gate.py`) and the state machine are Phase 1
  deliverables that must stay untouched by Phase 4 unless a specific,
  separately-approved reason arises.

## 11. Recommended next implementation steps

1. Resolve the open Redis/no-Docker question in §9 with the project lead
   before writing any queue/worker code — it determines whether Phase 4 needs
   a `devredis.py`-style local manager or should defer the broker swap.
2. Plan Phase 4 the same way Phase 3 was planned: inspect what already exists
   (§9) vs. what's genuinely new, produce a concrete milestone list with
   acceptance criteria, and get it approved before implementing — per the
   project's established working pattern for this repo.
3. Likely milestones based on `docs/PLAN.md`'s Phase 4 paragraph: (a) a
   worker/queue mechanism for scheduled discovery, (b) score-threshold-driven
   notification triggers reusing the existing `Notification`
   model/dedupe-key design, (c) the SSE broker swap (or an explicit decision
   to defer it), (d) notification history/read-state in the frontend (the
   backend endpoints already exist — check whether `Notifications.tsx`
   already fully consumes them or if that's still open).
4. Re-run the same regression discipline used for Phase 3: `pytest -q`,
   `ruff check app tests`, `mypy app tests` after each milestone, not just at
   the end; `npx tsc -b --noEmit && npx eslint .` and `npx vitest run` once
   frontend work starts.
5. Do not start implementation without explicit approval, matching this
   project's established planning-then-approval workflow.
