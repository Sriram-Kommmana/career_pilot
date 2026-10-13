"""Application error types and the single error envelope.

Every failure the API reports comes through here, so clients see one shape:

    {"error": {"code": ..., "message": ..., "details": ..., "correlation_id": ...}}

Exceptions are never swallowed. Anything unexpected becomes a 500 with a correlation
id that ties the response to the structured log entry.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for errors that map to a deliberate HTTP response.

    ``code`` is a stable machine-readable string. Clients branch on it; the
    ``message`` is for humans and may change.
    """

    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details


class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


class AuthenticationError(AppError):
    status_code = 401
    code = "NOT_AUTHENTICATED"


class PermissionDeniedError(AppError):
    """The caller is authenticated but does not own, or may not act on, the resource.

    Ownership failures deliberately use 404 at the API edge for resources addressed by
    an opaque id, so the API does not confirm that someone else's id exists. This class
    is for cases where the resource is legitimately visible but the action is not
    permitted.
    """

    status_code = 403
    code = "PERMISSION_DENIED"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    """The request is well-formed but conflicts with current state.

    Used for duplicate applications, invalid state transitions, and a refused
    submission. A refused submission carries the gate's blocker list in ``details``.
    """

    status_code = 409
    code = "CONFLICT"


class InvalidStateTransitionError(ConflictError):
    code = "INVALID_STATE_TRANSITION"


class DuplicateApplicationError(ConflictError):
    code = "DUPLICATE_APPLICATION"


class SubmissionBlockedError(ConflictError):
    """The submission gate refused. ``details.blockers`` says exactly why."""

    code = "SUBMISSION_BLOCKED"


class NotImplementedPhaseError(AppError):
    """An endpoint that exists in the contract but belongs to a later phase.

    Returning this is honest: the route is reachable, documented, and tells the caller
    which phase implements it. It never pretends to have done the work.
    """

    status_code = 501
    code = "NOT_IMPLEMENTED_YET"

    def __init__(self, feature: str, *, phase: int) -> None:
        super().__init__(
            f"{feature} is not implemented yet. Planned for Phase {phase}.",
            details={"feature": feature, "phase": phase},
        )


class BrowserSessionNotFoundError(NotFoundError):
    """No session with this id, or it does not belong to this user/application.

    Deliberately the same 404 the rest of the API uses for an unowned resource
    addressed by an opaque id — this never confirms that someone else's
    session exists.
    """

    code = "BROWSER_SESSION_NOT_FOUND"


class BrowserSessionExpiredError(ConflictError):
    """The session existed but has expired, disconnected, or been closed.

    A restarted backend process loses every in-memory session by construction
    — this is also raised in that case. The required response is the same
    either way: re-inspect and re-approve, never silently resume.
    """

    code = "BROWSER_SESSION_EXPIRED"


class BrowserSessionConflictError(ConflictError):
    """A session for this application already exists.

    Two simultaneous sessions must never be able to mutate the same
    application's fields at once.
    """

    code = "BROWSER_SESSION_EXISTS"


class UnsupportedOriginError(ConflictError):
    code = "UNSUPPORTED_ORIGIN"


class UnsupportedFormError(ConflictError):
    """The adapter positively refused this page rather than guessing."""

    code = "UNSUPPORTED_FORM"


class MappingStaleError(ConflictError):
    """The approved mapping token no longer matches the session's current
    proposal — the page was re-inspected, or the session moved on."""

    code = "MAPPING_STALE"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"


class ExternalServiceError(AppError):
    status_code = 502
    code = "EXTERNAL_SERVICE_ERROR"


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"


def error_envelope(
    *,
    code: str,
    message: str,
    correlation_id: str,
    details: Any = None,
) -> dict[str, Any]:
    """Build the one response shape used for every error."""
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "correlation_id": correlation_id,
    }
    if details is not None:
        payload["details"] = details
    return {"error": payload}
