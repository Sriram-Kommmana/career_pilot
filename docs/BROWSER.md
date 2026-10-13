# Phase 5/6 — browser-assisted application automation and submission

What's actually built, what isn't, and how to use it. See
[`docs/SAFETY.md`](SAFETY.md) for the rules this had to follow and
[`docs/PLAN.md`](PLAN.md) for how it fits the roadmap.

## What this is

A controlled, human-supervised workflow: open a real, visible browser on a
supported employer application page, inspect its fields, propose a mapping
from your confirmed profile, fill only what you approve, verify the result by
reading the live page back, and — once you've explicitly approved the exact
form you reviewed — submit it through the same real browser session. The
existing, unchanged submission gate (`app/domain/submission_gate.py`) is the
only thing that can let a submission through; nothing in the browser layer
has any path to a click that bypasses it. **CareerPilot never auto-submits
anything on its own** — every submission starts from a deliberate, separate
"Approve and submit application" action, and never happens as a side effect
of discovery, preparation, inspection, filling, or verification.

## Supported forms

**Greenhouse only** (`job-boards.greenhouse.io`, `boards.greenhouse.io`).
Lever and Ashby are not built. Within Greenhouse:

- Standard fields (first name, last name, email, phone, resume, cover letter)
  are recognised by their stable ids.
- Custom, per-posting questions are matched by label text only — their ids
  are not stable across postings.
- Combobox widgets (e.g. a country picker) and checkbox/radio groups are
  reported as present but **not filled** — reliably driving a third-party JS
  widget without false confidence isn't attempted.
- Legal, consent, and demographic questions (employment agreements, EEO,
  gender, ethnicity, veteran status, disability, references, government IDs,
  etc.) are **never** given a proposed value, regardless of label wording.
- Work authorisation is the one legal-adjacent question CareerPilot will
  propose an answer for, because it reuses a value the user already and
  genuinely confirmed in their profile — not a guess.

Adapter field extraction was grounded in a real, live-observed GitLab
Greenhouse application page during design, not assumed. `docs/PLAN.md`'s
Phase 5 entry has the specifics.

## The two-approval model

- **Approval A (fill)** — reviewing the proposed mapping and clicking "Fill
  N approved fields" in the UI. This is a `mapping_token` check inside
  `app.services.browser_fill`, deliberately *not* the submission gate's
  `ApprovalRecordRow`. It authorises filling the page, nothing else.
- **Approval B (submission)** — the existing, unmodified
  `/review` → `/approve` → `/submit` flow. It requires the application to be
  in `WAITING_FOR_APPROVAL`, which only happens once every required field is
  both resolved *and* verified against the live page. No earlier approval can
  substitute for it. As of Phase 6, `/submit` is where the real employer
  click actually happens — see "Submitting an application" below.

## Starting and using a session

From an application's detail page, once it is `READY` (prepared, every
required field resolved from your confirmed profile):

1. **Open browser session** — launches an isolated, visible Chromium window
   (headed by default — `CAREERPILOT_BROWSER_HEADLESS=true` to run headless)
   and navigates to the job's application URL. Refuses with `409
   UNSUPPORTED_ORIGIN` for anything outside the Greenhouse allowlist, `file:`/
   `javascript:`/`data:` schemes, and local/private-network hosts — checked
   server-side, independent of the allowlist.
2. **Inspect this page** — extracts fields and proposes a mapping. Refuses
   with `409 UNSUPPORTED_FORM` if the page isn't a Greenhouse application form
   this adapter recognises, rather than guessing.
3. Review the mapping. Anything flagged "Needs you" has no confirmed source,
   is ambiguous, or is a sensitive/legal question — answer it yourself in the
   visible browser window if you want it filled.
4. **Fill N approved fields** — fills only the supported, unflagged fields,
   then reads every extracted field back (including ones never proposed a
   value) to catch anything you typed yourself. Reports `verification_status`
   honestly: `PASSED` only if every required field is both resolved in
   CareerPilot's database *and* verified on the live page — not just one or
   the other.
5. Close the session explicitly when done. There is no background timer: a
   session past its TTL (`CAREERPILOT_BROWSER_SESSION_TTL_SECONDS`, default
   600s) or whose browser has disconnected is detected and torn down lazily —
   on the next request that touches it, or when a later `POST
   /browser-session` call (for any application) opportunistically evicts
   expired sessions before creating a new one. A process restart invalidates
   every session by construction: sessions are in-memory only, never
   persisted.

Only one session may be open per application at a time (`409
BROWSER_SESSION_EXISTS`), and a session belongs to exactly the user and
application that opened it. Reading or filling it as a different user gets
the same `404` any other owner-scoped resource in this API returns — it
never confirms that someone else's session exists. Closing it as a different
user is a safe no-op instead: the endpoint only ever closes a session found
under the *caller's own* `(user_id, application_id)` pair, so it returns `200`
whether or not a session exists, and never touches, confirms, or closes one
that belongs to someone else.

## Submitting an application

Once a session has reached `WAITING_FOR_APPROVAL` (every required field
resolved *and* verified on the live page), submitting is a separate,
deliberate sequence — never a side effect of filling, and never automatic.

1. **Review** (`GET /review`) — the current snapshot and the gate's advisory
   verdict. Always safe to call; changes nothing.
2. **Approve** (`POST /approve`) — records your consent to submit *this exact
   form state*, bound to its `form_state_hash`. Any later change to the form
   — a re-fill, a user edit, or (new in Phase 6) the live page itself
   drifting — recomputes the hash and revokes this approval automatically.
   A prior approval to *fill* the form (Approval A) is never treated as
   approval to *submit* it; they are different tokens checked by different
   code.
3. **Submit** (`POST /submit`, requires `confirm: true` and the matching
   `form_state_hash`) — `app.services.browser_submit.submit` runs, in order,
   entirely before anything is clicked:
   - Resolves the live browser session for this user and application
     (`404 BROWSER_SESSION_NOT_FOUND` / `409 BROWSER_SESSION_EXPIRED` if it
     isn't there or has disconnected).
   - Re-checks the session is still on a supported Greenhouse origin and on
     *this application's* page specifically, not just the same origin
     (`409 UNSUPPORTED_ORIGIN` otherwise).
   - Requires the session to have actually been inspected
     (`409 BROWSER_SESSION_NOT_INSPECTED`) — a freshly (re)opened session has
     no record of what it reviewed, so a submission is bound to a session
     that really did review the page, not merely one that happens to be
     open.
   - Re-reads every field it previously filled and verified, live, right
     now, and compares each to what was actually filled. Any mismatch — the
     page changed after you approved it, whether CareerPilot's own re-fill or
     someone typing into the visible window — marks that field unverified,
     **persists the resulting approval invalidation**, and refuses with
     `409 APPROVAL_STALE` even though the stored database hash looked fine a
     moment ago.
   - Locates the one deterministic submit control: a single, visible,
     enabled `type="submit"` element inside the recognised form whose
     accessible name says "submit" — never a generic text match, never
     a guess between candidates. Anything ambiguous (zero matches, more than
     one, disabled, unclear name) refuses with `409 SUBMIT_CONTROL_NOT_FOUND`
     and asks you to finish by hand.
   - Re-runs the submission gate itself — ownership, approval, verification
     freshness, unresolved fields, interventions, state, and
     already-submitted — unchanged from Phase 1.
   - Only once every one of the above has passed does it **claim the
     attempt**: an idempotency key and the `SUBMITTING` state, committed
     durably in its own transaction *before* the click, so a crash between
     claiming and recording the outcome is never mistaken for "nothing
     happened."
4. **The click** — happens exactly once, and its outcome is always recorded,
   never left ambiguous in the database even when it's ambiguous on the
   page:
   - **`SUBMITTED`** — a short, explicitly best-effort text signal (in
     `app.browser.adapters.greenhouse`) appears that was **not already on
     the page** before the click (a snapshot of the page's own text and URL
     is taken immediately before clicking, specifically so a phrase that was
     already sitting somewhere — marketing copy, a testimonial, leftover
     text from a previous attempt — can never be mistaken for evidence of
     this attempt), **and** the page's own structure corroborates it: either
     the URL changed or the application form itself is no longer visible,
     which is what a real confirmation actually looks like. A new-looking
     phrase with neither corroboration is not trusted on text alone. None of
     this has been verified against a real Greenhouse submission — the
     Phase 5 manual walkthrough deliberately never submitted a real
     application — so treat a confirmed success as reliable but not
     infallible, and spot-check important applications yourself.
   - **`FAILED`** — the click itself raised nothing, and a rejection signal
     appears that is likewise new since the pre-click snapshot, with the URL
     unchanged and the form still visibly present — i.e. the form's own
     client-side validation visibly blocked the attempt in front of us, with
     no real ambiguity about whether anything was sent.
   - **`SUBMITTED_UNCERTAIN`** — anything else: no new, corroborated signal
     within the timeout
     (`CAREERPILOT_BROWSER_SUBMISSION_CONFIRMATION_TIMEOUT_MS`, default 8s),
     a signal that matched but wasn't corroborated, the page disconnected,
     or any other exception during or after the click. This is the
     deliberately conservative default — an exception during the click is
     *never* read as "nothing was sent," because Playwright's own click
     implementation can raise even when the underlying action already
     fired. Never retried automatically.

   Reconciling an uncertain outcome (`POST /reconcile-submission`) applies
   the same discipline to its own passive, read-only check: on the same
   still-open session that made the attempt, it compares against that exact
   pre-click snapshot; on a different or reopened session (no snapshot
   available), it requires the application form to be gone before treating
   a matching phrase as corroborating at all. Either way this is only ever
   corroborating metadata (`verified_by_careerpilot`) alongside the user's
   own explicit `SUBMITTED`/`FAILED` choice — it never decides the outcome
   by itself, and the UI shows the two cases with different wording rather
   than presenting a user attestation as something CareerPilot verified.
5. **One attempt, one outcome.** Once the attempt is claimed, every possible
   result — success, failure, or uncertain — is terminal for it. A second
   `/submit` call afterwards is refused with `ALREADY_SUBMITTED`, even for a
   confirmed failure. Recovering requires the existing, deliberately
   conservative `FAILED → PREPARING` path, not a quiet retry. Concurrent or
   duplicated `/submit` calls for the same application are serialised by a
   per-application lock plus a row-level `SELECT … FOR UPDATE` on the claim
   step, so a double click or a retried request can reach the browser at
   most once.
6. **Reconciling an uncertain outcome** (`POST /reconcile-submission`, only
   legal from `SUBMITTED_UNCERTAIN`) — never clicks anything again. If a
   browser session for the application is still open, it passively re-reads
   the current page for the same confirmation markers (no click, no fill) as
   corroborating evidence; the final `SUBMITTED`/`FAILED` outcome is always
   your own explicit attestation, recorded the same honest way
   `record-manual-submission` already records a submission made outside
   CareerPilot entirely (`verified_by_careerpilot: false` when there was
   nothing left to check).

**What this does *not* claim:** guaranteed exactly-once delivery to the
employer (only exactly-once-from-this-side: CareerPilot never knowingly
clicks twice, but it cannot know whether the employer's own servers received
and processed a request independently of what it observed), or universal
support beyond a single Greenhouse application page per attempt.

## What isn't built

- **Lever, Ashby, or any other ATS.** Only Greenhouse.
- **Combobox/checkbox/radio filling.** Reported as needing you, never guessed.
- **CAPTCHA, MFA, or login walls.** Out of scope by design
  (`docs/SAFETY.md` §5) — pause and let the human resolve it in the visible
  browser.
- **File upload content.** `app.browser.mapping` never proposes a value for
  any file-kind field, unconditionally — not because a resume/cover-letter
  upload never happens to match a label rule, but because file inputs are
  excluded before label matching runs at all, regardless of what the field is
  labelled. (An earlier version of this mapping let a file input with a label
  like "Cover Letter" fall through to the text-answer rule for that phrase,
  which would have proposed a plain-text draft as something to "attach" — a
  regression test, `TestFileFieldsAreNeverProposedAText`, now pins this down.)
  File fields always show as "Needs you"; attach a resume or cover letter by
  hand in the visible browser.
- **A verified-accurate success/failure detector.** The post-click outcome
  classifier (`app.browser.adapters.greenhouse`'s success/rejection text
  markers) is explicitly best-effort and has never been checked against a
  real Greenhouse submission — no automated test, and no manual walkthrough
  so far, has submitted a real application. An unrecognised real confirmation
  page is reported as `SUBMITTED_UNCERTAIN`, not guessed into a success.
- **Retrying a confirmed failure automatically.** Every claimed submission
  attempt — success, failure, or uncertain — is one-shot by design; recovery
  goes through `FAILED → PREPARING`, not a quiet resubmit, even when the
  failure is one CareerPilot is confident nothing was sent.
- **Combobox/checkbox/radio filling**, CAPTCHA/MFA/login handling, and
  file-upload content generation remain exactly as limited as in Phase 5 (see
  below) — Phase 6 does not relax any of them to make a submission more
  likely to succeed.

## Testing

- `tests/unit/test_browser_policy.py`, `tests/unit/test_browser_mapping.py` —
  pure logic, no browser, no database.
- `tests/browser/` — real Playwright/Chromium against sanitized local HTML
  fixtures (`tests/browser/fixtures/`), loaded with `page.set_content()`,
  never a network request. Requires `pip install -e ".[browser]"` and
  `python -m playwright install chromium`; skips with an actionable message
  if unavailable.
- `tests/integration/test_browser_session.py`,
  `tests/integration/test_browser_fill_verification.py` — the HTTP routes and
  the full fill → verify → gate pipeline, against the same sanitized
  fixtures via a real Postgres and a real (but locally injected, not
  live-navigated) browser session.
- `tests/browser/test_greenhouse_adapter.py`'s `TestFindSubmitControl` and
  `TestSubmitOutcomeClassification` — the deterministic-control guard and
  each outcome branch (`CONFIRMED_SUCCESS`/`CONFIRMED_FAILURE`/`UNCERTAIN`),
  against dedicated fixtures (`greenhouse_submission_*.html`) whose
  `onsubmit` handlers only ever manipulate the DOM in place — never a real
  navigation or network request, since `page.set_content()` pages have no
  server to send one to.
- `tests/integration/test_browser_submission.py` — the Phase 6 submission
  flow end to end: a successful submission with exactly one real click,
  confirmed failure, uncertain outcomes with no auto-retry, every refusal
  path (no approval, stale hash, missing required fields, a never-inspected
  session, a disconnected browser, navigation to an unsupported origin, a
  mismatched user, live-page drift invalidating a stale approval), concurrent
  and duplicate submission requests, a simulated crash after the click, and
  reconciliation of an uncertain outcome without ever resubmitting. Every
  test asserts the exact number of real clicks that occurred
  (`window.__submitAttempts` in each fixture), not just the HTTP status.
- No automated test depends on a live employer website, Docker, or a paid
  API.

## Security review findings

### Phase 5 (2026-10-13, fixed)

A post-implementation review of origin handling, session scoping, mapping
freshness, provenance, and redaction found three concrete defects, each now
fixed with a regression test:

1. **`inspect()` never re-validated origin.** `fill()` re-checked the
   session's current origin before acting; `inspect()` did not, so a page
   that navigated itself (a client-side redirect, a link followed in the
   visible browser) between session creation and an inspect call would have
   its fields read and a mapping proposed without that mapping ever being
   validated against a supported origin. Fixed by adding the same check
   `fill()` already had. See
   `tests/integration/test_browser_session.py::TestInspectRevalidatesOrigin`.
2. **A file-kind field could be proposed a text value.** `app.browser.mapping`'s
   label-text rules (e.g. "cover letter" → the drafted cover-letter text)
   were matched against a field's label without checking its *kind* first —
   a Greenhouse cover-letter upload literally labelled "Cover Letter" would
   have been offered as ready-to-fill with plain text, which
   `adapter.fill_field` would then try to pass to `set_input_files()` and
   fail (safely, but visibly wrong). Fixed by excluding every file-kind field
   from label matching unconditionally. See
   `tests/unit/test_browser_mapping.py::TestFileFieldsAreNeverProposedAText`.
3. **A fill failure's exception text could leak a confirmed value.**
   Confirmed by direct reproduction against a real browser: Playwright's own
   retry/call-log embeds the literal attempted value once a locator resolves
   but the fill action keeps retrying (e.g. a disabled field) —
   `fill("<the value>")` appears verbatim in the exception's string form.
   That text was being stored, unredacted, in `ApplicationField.verification_note`
   and returned in the API response — a real path for a sensitive confirmed
   value (phone, salary, work authorisation) to end up in a column
   `docs/SAFETY.md` requires to be stored masked. Chromium partially masks a
   `type="tel"` input's value in its own log, which is not something this
   project controls or can rely on for other field types. Fixed by never
   persisting or returning the raw exception text — only its type is logged,
   server-side, for diagnosis. See
   `tests/integration/test_browser_fill_verification.py::TestVerificationNoteNeverLeaksAValue`.

### Phase 6 (2026-10-13, fixed)

An adversarial pre-commit review of the outcome classifier and reconciliation
found one concrete defect, fixed with regression tests:

1. **The outcome classifier had no concept of "before" — only "after".**
   `_observe_after_click` scanned the live page's text for a success or
   rejection phrase with nothing to compare it against. A phrase that was
   already sitting on the page before the click was ever made — unrelated
   marketing or testimonial copy containing "thank you for your interest",
   a static "this field is required" disclaimer near an untouched optional
   field, or leftover text from an earlier attempt — would have been
   misread as `CONFIRMED_SUCCESS` or `CONFIRMED_FAILURE` for an attempt it
   had nothing to do with. Fixed by capturing the page's text and URL
   immediately before the click as a baseline, requiring any matched phrase
   to be genuinely new since that baseline, and additionally requiring
   structural corroboration (navigation or the form disappearing, for
   success; an unchanged URL with the form still present, for failure) —
   text alone, old or new, is never sufficient on its own. The same
   baseline is carried onto the session so a later `/reconcile-submission`
   call on the same still-open session applies the identical standard; a
   reconciliation on a different or reopened session (no baseline
   available) falls back to requiring the form to be gone. See
   `tests/browser/test_greenhouse_adapter.py::TestOutcomeClassifierRejectsStaleOrUnrelatedContent`
   and `tests/integration/test_browser_submission.py::TestUncertainOutcomeReconciliation`'s
   `test_reconciliation_does_not_treat_pre_attempt_content_as_new_evidence` /
   `test_reconciliation_on_a_reopened_session_requires_the_form_to_be_gone`.
   Each of these was confirmed to fail against the pre-fix behaviour before
   the fix and pass after, by temporarily reverting the classifier and
   reconciliation logic and re-running them.

## Manual verification performed

On 2026-10-13, the full flow was exercised by hand against a **live**
GitLab posting already present in a seeded account's job feed (ingested
earlier by the Phase 2 Greenhouse connector): opened a real headed browser
session at `https://job-boards.greenhouse.io/...`, inspected the real page,
reviewed the proposed mapping (first name/last name/email/a dynamic
per-posting "LinkedIn Profile" question were proposed; the real page's legal
"employment agreements" question, its EEO/gender/ethnicity/veteran section,
and its country/phone comboboxes were all correctly left for the human),
filled and verified the four approved fields (all `PASSED`), confirmed the
submission gate correctly refused with `WRONG_STATE`/`NO_APPROVAL`/
`VERIFICATION_FAILED`/`UNRESOLVED_REQUIRED_FIELDS` (work authorisation, visa
sponsorship, and resume have no matching field on this particular form, so
they were never verified against it even though they're resolved in
CareerPilot's own database), and closed the session — confirmed via the
backend log and a clean process check, not just the UI. **Nothing was
submitted to GitLab at any point** — no submit/approve endpoint was called.

## Phase 6 manual verification

On 2026-10-13, the submission UI was exercised by hand against a seeded demo
application (not the live GitLab posting above): opened the application
detail page, confirmed the "Submission gate" card correctly lists every
outstanding blocker before approval exists, and clicked "Open browser
session" against the seeded job's (non-Greenhouse, demo) `application_url` —
confirmed the real, unpatched origin check correctly refused with "This
origin is not one of CareerPilot's supported employer application sites.",
rendered cleanly in the UI with no console errors. The demo dataset contains
no real Greenhouse posting, so the full approve → submit → confirmed-outcome
UI path (the "Approve and submit application" confirmation panel, and the
success/failure/uncertain banners) was **not** exercised against a live
browser click by hand — that path is instead covered end to end, with a real
Playwright browser, by `tests/integration/test_browser_submission.py`
(16 tests, all passing) and `tests/browser/test_greenhouse_adapter.py`'s
submission-classification tests, against the sanitized local fixtures
described above. Nothing resembling a real employer submission has been
performed by hand or by any automated test.
