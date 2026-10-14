# CareerPilot safety model

These constraints override every feature request. If a change would weaken one of
them, the change does not ship.

---

## 1. The LLM never submits an application

There is no code path from a model's output to a submission. Concretely:

- No tool, function, endpoint, or queue message exposes "submit" to a model.
- `POST /api/v1/applications/{id}/submit` requires an authenticated **human** bearer
  token and an explicit prior approval for that exact application.
- The browser layer has no submit capability wired to agent control. The click that
  submits is performed by a deterministic code path reached only after the backend gate
  has returned `allowed=True`.
- `backend/tests/unit/test_submission_gate.py` and (Phase 5)
  `backend/tests/browser/test_greenhouse_adapter.py::TestNeverSubmitsDuringFill`
  are standing guards on this property — the latter instruments
  `HTMLFormElement.prototype.submit` before filling and asserts it is never
  called.
- **Confirmed as built (2026-10-13):** Phase 5 shipped real Playwright-driven
  inspection and controlled filling for Greenhouse application pages,
  verified both against sanitized local fixtures and manually against a live
  GitLab posting (see `docs/BROWSER.md`).
- **Confirmed as built (2026-10-13, Phase 6):** the actual submit click now
  exists, in `app.services.browser_submit`, reached only after the gate, a
  live re-read of the page, and an explicit, separate "Approve and submit
  application" action all agree. It is still not an agent capability: no
  tool, prompt, or model output can reach it, and
  `backend/tests/integration/test_browser_submission.py` is the standing
  integration-level guard — every refusal path, the one-click-per-attempt
  guarantee, and the never-auto-retry rule for an uncertain outcome are
  asserted there against a real (sanitized, local) Playwright session.

## 2. The submission gate is a backend decision

`app/domain/submission_gate.py` is a pure function. It returns `allowed` plus a list of
machine-readable blockers. All nine conditions must hold:

| # | Condition | Blocker code |
|---|---|---|
| 1 | Caller owns the application | `NOT_OWNER` |
| 2 | An approval record exists | `NO_APPROVAL` |
| 3 | `approval.form_state_hash == application.form_state_hash` | `APPROVAL_STALE` |
| 4 | Verification is `PASSED` and fresh | `NO_VERIFICATION`, `VERIFICATION_FAILED`, `VERIFICATION_STALE` |
| 5 | No unresolved required field, nothing `NEEDS_HUMAN` | `UNRESOLVED_REQUIRED_FIELDS`, `FIELDS_NEED_HUMAN` |
| 6 | No unresolved intervention | `UNRESOLVED_INTERVENTION` |
| 7 | State is exactly `WAITING_FOR_APPROVAL` | `WRONG_STATE` |
| 8 | No prior submission | `ALREADY_SUBMITTED` |
| 9 | Job not known closed | `JOB_CLOSED` |

A frontend button is a convenience, never a security boundary. The gate runs again
immediately before submission, inside the same transaction that claims the idempotency
key.

## 3. Approval is bound to the state it approved

`form_state_hash` is a stable digest of every field value, uploaded document, and
answer that a human reviewed. Any later change produces a different hash, which
invalidates the approval (`APPROVAL_STALE`) and forces a fresh review. Approval is not
a flag; it is a signed-off snapshot.

## 4. Nothing about the candidate is invented

- Employment history, dates, qualifications, salary history, work authorisation, and
  demographic answers are only ever used when the user supplied or confirmed them.
- `application_answers.provenance` is `NOT NULL`. An answer cannot be persisted without
  recording its source.
- Allowed provenance kinds: `USER_CONFIRMED_PROFILE`, `RESUME_EXTRACTION_CONFIRMED`,
  `USER_APPROVED_EVIDENCE`, `PREVIOUSLY_APPROVED_ANSWER`, `USER_DIRECT_INPUT`.
- `GENERATED_UNVERIFIED` exists for drafted prose only. It can never satisfy a required
  field, and the gate rejects it.
- Anything unknown or ambiguous becomes `NEEDS_HUMAN`. There is no "best guess" path.

## 5. Anti-bot measures are never bypassed

CAPTCHA, MFA, SSO, and authentication challenges are out of scope as engineering
targets. When one is detected the run pauses and records an intervention reason
(`NEEDS_CAPTCHA`, `NEEDS_MFA`, `NEEDS_LOGIN`) for the user to resolve in the real
browser. No solver service, no header spoofing to defeat bot detection, no
fingerprint evasion.

## 6. Browser actions are verified by reading the page back

A Playwright call that returns without raising is not evidence. Every consequential
field is re-read from the live DOM or accessibility tree and compared with the intended
value. Mismatch produces `VERIFICATION_FAILED`, which is a terminal state for that
attempt, not a warning. Screenshots are supporting evidence only.

## 7. Submission success is claimed only on evidence

A submission is reported as `SUBMITTED` only when the resulting page state supports it.
An ambiguous result becomes `SUBMITTED_UNCERTAIN` and requires human reconciliation.

**Known limit:** submission outcome detection is best-effort. Its success/rejection
markers and structural checks have been exercised only against sanitized local
fixtures and have **not** been validated against an actual employer-side
submission; treat `verified_by_careerpilot` as corroboration, never proof.
A potentially successful submission is never retried automatically.

**Confirmed as built (2026-10-13, Phase 6):** `app.browser.adapters.greenhouse`'s
outcome classifier only ever reports `CONFIRMED_SUCCESS` on a confirmation
signal that is both genuinely new since a snapshot taken immediately before
the click (never a phrase that was already sitting on the page — see the
adversarial finding below) and structurally corroborated (navigation or the
form disappearing); `CONFIRMED_FAILURE` likewise requires a new rejection
signal with the URL unchanged and the form still present. Any exception
during or after the click, including one from Playwright's own click
implementation, is `UNCERTAIN`, never `CONFIRMED_FAILURE`, because an
exception alone cannot rule out that the click's real-world effect already
fired. Reconciling an uncertain outcome (`POST /reconcile-submission`) never
clicks anything again, by construction — it applies the same
new-since-baseline plus structural-corroboration standard (URL changed or
form gone) where a baseline is available, requires the form to be gone
otherwise, and the final `SUBMITTED`/`FAILED`
outcome is always the user's own explicit choice, never inferred from that
check.

**Adversarial pre-commit review finding (2026-10-13, fixed):** the first
version of this classifier compared the page's text against nothing —
a success or rejection phrase already present on the page *before* the
click (unrelated marketing copy, a static validation hint, leftover text
from an earlier attempt) would have been misread as evidence of the current
attempt. See `docs/BROWSER.md`'s "Phase 6" security review findings section
for the fix and its regression tests.

## 8. Secrets never reach prompts, logs, or ordinary rows

- Passwords are argon2 hashes. Plaintext is never stored, logged, or prompted.
- Browser cookies and session state are never written to a database row, a log line, an
  LLM prompt, or the vector store.
- `app/domain/redaction.py` masks sensitive field values before anything is logged or
  shown in an artefact. Sensitive values are stored masked in field records.

## 9. Untrusted input cannot change behaviour

Job descriptions, web pages, resumes, and retrieved documents are data. They are placed
in labelled, delimited blocks and never in the instruction region of a prompt. Tool
permissions are fixed by the caller before the model runs. Model output is parsed
through Pydantic and rejected if it does not validate. The worst case for an injected
instruction is a bad suggestion a human must still approve.

## 10. Sources are respected

Documented public ATS endpoints are preferred over scraping. Per-source rate limits and
backoff are enforced. Robots policies are honoured where applicable. Access controls are
never evaded. The product does not claim comprehensive job coverage, because it does not
have it.

## 11. Scores are ranking heuristics

A match score orders a list. It is not a calibrated probability of an interview or an
offer, and is never labelled as one in the API or the UI. The component breakdown,
matched requirements, missing requirements, and evidence references are always
available alongside the number.

## 12. Demo data is labelled as demo data

Seeded jobs carry `is_demo_data = true` in the database, in every API response, and as a
visible badge in the UI. They are never presented as live postings.
