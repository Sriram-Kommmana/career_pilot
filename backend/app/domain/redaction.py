"""Masking sensitive values before they are stored, logged, or shown in artefacts.

Field records exist so a user can audit what was typed into a form. That audit trail
must not itself become a place where a phone number, a date of birth, or a government
id sits in plaintext across the logs and screenshots of every application.

The rule applied here: keep enough of a value for a human to recognise it, discard
the rest. ``"9876543210"`` becomes ``"****3210"`` — the user can confirm it is the
right number without the number being readable.

Two things are never partially shown and never stored at all: passwords and session
state. They are replaced wholesale.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final

MASK: Final = "[redacted]"


class Sensitivity(StrEnum):
    """How a value must be handled in records and logs."""

    #: Safe to store and display as-is, e.g. a job title or a city.
    PUBLIC = "PUBLIC"
    #: Store masked. Recognisable to the owner, useless to anyone else.
    MASKED = "MASKED"
    #: Never stored in any form. Passwords, cookies, tokens, session state.
    NEVER_STORED = "NEVER_STORED"


#: Field-name fragments that imply a sensitivity level. Matched against a lowercased
#: field id or label, so "Mobile Phone Number" and "phone_1" both resolve.
_NEVER_STORED_HINTS: Final[tuple[str, ...]] = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "cookie",
    "session",
    "otp",
    "one time code",
    "verification code",
    "security answer",
    "pin",
    "cvv",
    "card number",
)

_MASKED_HINTS: Final[tuple[str, ...]] = (
    "phone",
    "mobile",
    "telephone",
    "ssn",
    "social security",
    "aadhaar",
    "aadhar",
    "pan number",
    "national id",
    "passport",
    "driver",
    "licence number",
    "license number",
    "date of birth",
    "dob",
    "birth date",
    "address",
    "postcode",
    "postal code",
    "zip",
    "salary",
    "compensation",
    "bank",
    "account number",
    "ifsc",
    "tax",
    "emergency contact",
)

_EMAIL_RE: Final = re.compile(r"^(?P<local>[^@\s]+)@(?P<domain>[^@\s]+)$")

#: Field-name separators, folded to spaces so "date_of_birth" matches "date of birth".
_SEPARATORS_RE: Final = re.compile(r"[_\-.]+")


def classify_field(name: str, *, label: str | None = None) -> Sensitivity:
    """Decide how a form field's value must be handled.

    Errs toward masking: an unrecognised field whose name merely contains "id" is not
    masked, but anything matching a known-sensitive fragment is.

    Separators are folded to spaces before matching. Field *names* are written
    ``date_of_birth`` or ``card-number`` while the hints below read as prose, so
    without this a sensitive field would be classified as public and its value stored
    in the clear. That exact bug was caught by ``tests/unit/test_provenance_and_redaction.py``.
    """
    raw = " ".join(filter(None, (name, label))).lower()
    haystack = _SEPARATORS_RE.sub(" ", raw)
    if any(hint in haystack for hint in _NEVER_STORED_HINTS):
        return Sensitivity.NEVER_STORED
    if any(hint in haystack for hint in _MASKED_HINTS):
        return Sensitivity.MASKED
    return Sensitivity.PUBLIC


def mask_value(value: str | None, *, keep_last: int = 4) -> str | None:
    """Mask a value, leaving the final ``keep_last`` characters visible.

    Short values are masked entirely: showing the last four characters of a
    five-character value reveals almost all of it.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return ""
    if len(stripped) <= keep_last + 2:
        return "*" * len(stripped)
    return "*" * (len(stripped) - keep_last) + stripped[-keep_last:]


def mask_email(value: str | None) -> str | None:
    """Mask the local part of an email, keeping the domain readable.

    The domain is the part a user checks ("did it use my work address?"), and it is
    not itself identifying.
    """
    if value is None:
        return None
    match = _EMAIL_RE.match(value.strip())
    if match is None:
        return mask_value(value)
    local = match.group("local")
    domain = match.group("domain")
    visible = local[0] if local else ""
    return f"{visible}{'*' * max(len(local) - 1, 1)}@{domain}"


def redact_for_storage(
    *,
    field_name: str,
    value: str | None,
    label: str | None = None,
) -> tuple[str | None, Sensitivity]:
    """Return the value as it may be persisted, with its sensitivity.

    Callers store the returned value verbatim. This is the only function that decides
    what a field record contains, so there is one place to audit.
    """
    sensitivity = classify_field(field_name, label=label)
    if sensitivity is Sensitivity.NEVER_STORED:
        return MASK, sensitivity
    if sensitivity is Sensitivity.MASKED:
        if value is not None and "@" in value:
            return mask_email(value), sensitivity
        return mask_value(value), sensitivity
    return value, sensitivity


def redact_text(text: str | None) -> str | None:
    """Scrub obvious identifiers out of free text before logging it.

    A coarse net for log lines and artefact captions, not a DLP system. Email
    addresses and long digit runs are the two things that reliably leak through prose
    such as a page title or an error message.
    """
    if not text:
        return text
    scrubbed = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "[email]",
        text,
    )
    # 7+ consecutive digits: phone numbers, national ids, account numbers.
    scrubbed = re.sub(r"\b\d{7,}\b", "[number]", scrubbed)
    return scrubbed
