"""The adapter protocol and the field shapes every adapter speaks in.

An adapter's only job is to turn a live page into ``ExtractedField`` values —
plain data, no Playwright objects leaking out. ``app.browser.mapping`` (pure,
no Playwright import) turns those into proposed values; ``app.browser.session``
turns an approved proposal back into real page actions through the same
adapter. The adapter is the only thing that ever touches the page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

#: What kind of control a field is, independent of its HTML tag. Drives both the
#: mapping rules and how session.py fills/reads it back.
FieldKind = Literal[
    "text", "email", "tel", "textarea", "file", "combobox", "checkbox", "radio", "unknown"
]


@dataclass(frozen=True, slots=True)
class ExtractedField:
    """One field as read off the live page. No proposed value here — that is
    ``app.browser.mapping``'s job, kept deliberately separate so inspection can
    never accidentally guess."""

    field_id: str
    label: str | None
    field_kind: FieldKind
    required: bool
    #: Playwright locator string, e.g. "#first_name" or a label-based locator the
    #: adapter resolved. Opaque to everything outside the adapter that produced it.
    locator: str
    options: tuple[str, ...] = field(default_factory=tuple)
    #: True for a field with no label, no recognised id, and no understood role —
    #: never force-mapped, always reported.
    unrecognised: bool = False


@dataclass(frozen=True, slots=True)
class FormInspectionResult:
    origin: str
    form_identity: str
    fields: tuple[ExtractedField, ...]
    #: True only when the adapter positively recognised the form type. A False
    #: here means "refuse", never "guess and continue".
    supported: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class FieldReadBack:
    """What the page actually shows for one field, read after a fill attempt."""

    field_id: str
    value: str | None
    #: For a combobox/select: whether the option actually ended up selected.
    matches_intended: bool


class FormAdapter(Protocol):
    """What every employer-form adapter must implement.

    Deliberately small: detect-or-refuse, inspect, fill one field, read one
    field back. Nothing here decides *what* to fill — that is mapping's job —
    and nothing here ever finds or clicks a submit control.
    """

    name: str

    async def detect(self, page: object) -> bool: ...

    async def inspect(self, page: object) -> FormInspectionResult: ...

    async def fill_field(self, page: object, field: ExtractedField, value: str) -> None: ...

    async def read_field(self, page: object, field: ExtractedField) -> FieldReadBack: ...
