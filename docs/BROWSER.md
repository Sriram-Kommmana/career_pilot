# Phase 5 — browser-assisted application automation

What's actually built, what isn't, and how to use it. See
[`docs/SAFETY.md`](SAFETY.md) for the rules this had to follow and
[`docs/PLAN.md`](PLAN.md) for how it fits the roadmap.

## What this is

A controlled, human-supervised workflow: open a real, visible browser on a
supported employer application page, inspect its fields, propose a mapping
from your confirmed profile, fill only what you approve, and verify the
result by reading the live page back. **It never submits anything.** The
existing, unchanged submission gate (`app/domain/submission_gate.py`) is the
only thing that can let a submission through, and the actual click that would
submit is not implemented yet (see "What isn't built" below).

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
  substitute for it.

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
- **The actual submission click.** `applications.submit()` still refuses with
  `SUBMISSION_NOT_IMPLEMENTED` even once the gate allows it. Reaching
  `WAITING_FOR_APPROVAL` with real verification is new in Phase 5; clicking
  the employer's submit control is Phase 6's job.

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
- No automated test depends on a live employer website, Docker, or a paid
  API.

## Security review findings (2026-10-13, fixed)

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
