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


#: What happened when the deterministic submit control was clicked. Never
#: ``CONFIRMED_SUCCESS`` merely because the click itself did not raise — that is
#: not evidence, it is the absence of an immediate error. Only a positively
#: recognised confirmation or rejection signal earns a confirmed verdict; anything
#: else, including any exception during or after the click, is ``UNCERTAIN``.
SubmitOutcome = Literal["CONFIRMED_SUCCESS", "CONFIRMED_FAILURE", "UNCERTAIN"]


@dataclass(frozen=True, slots=True)
class SubmitAttempt:
    outcome: SubmitOutcome
    #: A short, non-sensitive description of what was observed (which marker
    #: matched, or that nothing recognisable appeared within the timeout, or
    #: that the page disconnected). Never raw page text and never a field value.
    evidence: str


class FormAdapter(Protocol):
    """What every employer-form adapter must implement.

    Deliberately small: detect-or-refuse, inspect, fill one field, read one
    field back, find the one deterministic submit control, and submit. Nothing
    here decides *what* to fill — that is mapping's job — and nothing here ever
    clicks anything based on generic text alone.
    """

    name: str

    async def detect(self, page: object) -> bool: ...

    async def inspect(self, page: object) -> FormInspectionResult: ...

    async def fill_field(self, page: object, field: ExtractedField, value: str) -> None: ...

    async def read_field(self, page: object, field: ExtractedField) -> FieldReadBack: ...

    async def find_submit_control(self, page: object) -> object | None:
        """The one control that submits this form, or ``None``.

        ``None`` whenever there is any ambiguity at all: zero or more than one
        candidate, not visible, not enabled, or an accessible name that does not
        clearly say "submit". Never a best guess — the caller must refuse and
        ask the human to finish by hand rather than click something uncertain."""
        ...

    async def submit(
        self, page: object, control: object, *, confirmation_timeout_ms: int
    ) -> SubmitAttempt:
        """Click the already-located, already-validated submit control and
        classify what happened. Called only once, only after every other check
        has passed, and only by the deterministic code path the submission gate
        has already approved — never by a model."""
        ...
