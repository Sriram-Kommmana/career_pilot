"""The Greenhouse application-form adapter.

Field structure verified against a real, public Greenhouse application page
(see the Phase 5 plan's "Live-verified, not assumed" note) — not assumed from
documentation. Confirmed there:

- Greenhouse's own standard fields (`first_name`, `last_name`, `email`, `phone`,
  `resume`, `cover_letter`) have stable, semantic ids across postings.
- Custom, job-specific questions do **not** — their ids are per-posting numbers
  (``question_38624168002``), so they are matched by label text only, never by id.
- A custom combobox widget (country, the phone country-code picker) and native
  checkboxes/radios are present. This version deliberately does not attempt to
  fill them — reliably driving a third-party JS widget without false confidence
  is exactly the kind of guess ``docs/SAFETY.md`` forbids. They are reported as
  unrecognised, for the user to complete by hand in the visible browser.
- A required legal question with no possible authoritative source was present.
  ``app.browser.mapping``'s never-autofill rules exist because of exactly this.

Only one real adapter ships in this phase. Lever/Ashby are not built.
"""

from __future__ import annotations

import asyncio
import re
from typing import Final

from app.browser.adapters.base import (
    ExtractedField,
    FieldKind,
    FieldReadBack,
    FormInspectionResult,
    SubmitAttempt,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Greenhouse's own standard fields. Safe to recognise by id because these are
#: stable across postings — confirmed against a real page, not assumed.
_STABLE_TEXT_IDS: Final[frozenset[str]] = frozenset(
    {"first_name", "last_name", "email", "phone"}
)
_STABLE_FILE_IDS: Final[frozenset[str]] = frozenset({"resume", "cover_letter"})

#: The minimum stable fields that must be present for this adapter to claim the
#: page at all. Missing either means this is not a Greenhouse application form
#: in the shape this adapter understands — refuse, do not guess.
_REQUIRED_FOR_DETECTION: Final[frozenset[str]] = frozenset({"first_name", "email"})

_TRAILING_REQUIRED_MARK_RE: Final = re.compile(r"\*\s*$")

#: The accessible name of the one real control that submits a Greenhouse
#: application, confirmed against a live, public posting during Phase 5
#: planning (see docs/PLAN.md's "Live-verified, not assumed" note): literally
#: "Submit application". Matched case-insensitively and loosely (substring),
#: but a control whose name does not contain "submit" at all is never treated
#: as the submit control, no matter where it sits in the DOM.
_SUBMIT_CONTROL_NAME_HINT: Final = "submit"

#: Best-effort, explicitly unverified-against-a-live-submission signals. Phase 5's
#: manual walkthrough deliberately never submitted a real application (see
#: docs/BROWSER.md), so none of this has been confirmed against Greenhouse's real
#: post-submit behaviour. Kept short and conservative on purpose: a real page that
#: matches none of these is reported as UNCERTAIN, never guessed into a result.
#:
#: Public (no leading underscore): ``app.services.browser_submit``'s passive
#: reconciliation check reuses these exact lists rather than keeping its own
#: copy, so there is one definition of what counts as a recognised signal.
SUCCESS_TEXT_MARKERS: Final[tuple[str, ...]] = (
    "thank you for applying",
    "thank you for your interest",
    "application has been submitted",
    "received your application",
    "successfully submitted",
)
FAILURE_TEXT_MARKERS: Final[tuple[str, ...]] = (
    "this field is required",
    "can't be blank",
    "cannot be blank",
    "please fill out this field",
    "there was a problem with your submission",
)
#: How often to re-check the page while waiting for a recognisable signal.
_CONFIRMATION_POLL_INTERVAL_SECONDS: Final = 0.25


def _first_new_marker(text: str, markers: tuple[str, ...], baseline: str) -> str | None:
    """The first marker present in ``text`` that was *not already present*
    in ``baseline``.

    This is the whole point of capturing a baseline before ever clicking:
    a marker that was already sitting somewhere on the page — unrelated
    marketing copy saying "thank you for your interest in joining us", a
    static "* this field is required" disclaimer near an untouched optional
    field, leftover text from a previous attempt — proves nothing about
    *this* click. Only a marker that appears for the first time after the
    click is even a candidate; see ``find_submit_control``'s caller for the
    further corroboration (navigation or the form disappearing) required
    before it is trusted as evidence rather than just a candidate.
    """
    return next((m for m in markers if m in text and m not in baseline), None)


def _clean_label(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = _TRAILING_REQUIRED_MARK_RE.sub("", raw).strip()
    return cleaned or None


class GreenhouseAdapter:
    """Reads and fills a Greenhouse ``job-boards.greenhouse.io`` application form.

    Every method takes a real ``playwright.async_api.Page`` but is typed as
    ``object`` in the shared protocol (``app.browser.adapters.base``) so that
    module never has to import Playwright — only this file does.
    """

    name = "greenhouse"

    async def detect(self, page: object) -> bool:
        ids = await self._collect_ids(page)
        return _REQUIRED_FOR_DETECTION.issubset(ids)

    async def inspect(self, page: object) -> FormInspectionResult:
        from playwright.async_api import Page

        assert isinstance(page, Page)

        if not await self.detect(page):
            return FormInspectionResult(
                origin=_origin_of(page.url),
                form_identity="unknown",
                fields=(),
                supported=False,
                reason=(
                    "This page does not look like a Greenhouse application form "
                    "this adapter recognises (missing a standard field such as "
                    "first name or email)."
                ),
            )

        form = page.locator("form").first
        elements = form.locator("input, textarea")
        count = await elements.count()
        fields: list[ExtractedField] = []

        for index in range(count):
            element = elements.nth(index)
            element_type = (await element.get_attribute("type")) or "text"
            if element_type == "hidden":
                continue

            element_id = await element.get_attribute("id")
            tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
            role = await element.get_attribute("role")
            required_attr = await element.get_attribute("required")
            aria_required = await element.get_attribute("aria-required")
            required = required_attr is not None or aria_required == "true"

            label_text: str | None = None
            if element_id:
                label_locator = page.locator(f'label[for="{element_id}"]')
                if await label_locator.count() > 0:
                    label_text = _clean_label(await label_locator.first.inner_text())
            if label_text is None:
                label_text = _clean_label(await element.get_attribute("aria-label"))

            field_kind, unrecognised = self._classify(
                element_id=element_id,
                element_type=element_type,
                tag_name=tag_name,
                role=role,
            )

            locator = f'[id="{element_id}"]' if element_id else f"nth={index}"
            fields.append(
                ExtractedField(
                    field_id=element_id or f"unlabelled_{index}",
                    label=label_text,
                    field_kind=field_kind,
                    required=required,
                    locator=locator,
                    unrecognised=unrecognised,
                )
            )

        return FormInspectionResult(
            origin=_origin_of(page.url),
            form_identity="greenhouse_application",
            fields=tuple(fields),
            supported=True,
        )

    async def fill_field(self, page: object, field: ExtractedField, value: str) -> None:
        from playwright.async_api import Page

        assert isinstance(page, Page)
        if not field.locator.startswith("nth="):
            locator = page.locator(field.locator).first
        else:
            locator = page.locator("form").first.locator("input, textarea").nth(
                int(field.locator.removeprefix("nth="))
            )

        if field.field_kind == "file":
            await locator.set_input_files(value)
        else:
            await locator.fill(value)

    async def read_field(self, page: object, field: ExtractedField) -> FieldReadBack:
        from playwright.async_api import Page

        assert isinstance(page, Page)
        if not field.locator.startswith("nth="):
            locator = page.locator(field.locator).first
        else:
            locator = page.locator("form").first.locator("input, textarea").nth(
                int(field.locator.removeprefix("nth="))
            )

        if field.field_kind == "file":
            file_count = await locator.evaluate("el => el.files ? el.files.length : 0")
            return FieldReadBack(
                field_id=field.field_id,
                value=f"{file_count} file(s)",
                matches_intended=file_count > 0,
            )

        value = await locator.input_value()
        return FieldReadBack(field_id=field.field_id, value=value, matches_intended=True)

    async def find_submit_control(self, page: object) -> object | None:
        """The one control that submits the application, or ``None``.

        Deliberately strict: must be a ``type="submit"`` control **inside the
        same form ``inspect()`` scanned** (never anywhere else on the page —
        a decoy "Submit" button outside the form, or a newsletter sign-up
        elsewhere, is never a candidate), there must be exactly one, it must
        be visible and enabled right now, and its accessible name must contain
        "submit". Any ambiguity at all returns ``None`` rather than guessing.
        """
        from playwright.async_api import Page

        assert isinstance(page, Page)
        if await page.locator("form").count() == 0:
            return None
        form = page.locator("form").first
        candidates = form.locator('button[type="submit"], input[type="submit"]')
        count = await candidates.count()
        if count != 1:
            return None

        candidate = candidates.first
        tag_name = await candidate.evaluate("el => el.tagName.toLowerCase()")
        name = (
            await candidate.inner_text()
            if tag_name == "button"
            else (await candidate.get_attribute("value")) or ""
        )
        if _SUBMIT_CONTROL_NAME_HINT not in (name or "").strip().lower():
            return None
        if not await candidate.is_visible() or not await candidate.is_enabled():
            return None
        return candidate

    async def submit(
        self, page: object, control: object, *, confirmation_timeout_ms: int
    ) -> SubmitAttempt:
        """Click the already-located, already-validated submit control once,
        then watch the live page for a recognised signal.

        A baseline of the page's own text and URL is captured *before* the
        click — a marker already sitting on the page proves nothing about
        this attempt, so only a marker that is genuinely new counts as a
        candidate at all (see ``_first_new_marker``). A new marker alone is
        still not enough: ``CONFIRMED_SUCCESS`` additionally requires the
        page to have navigated or the form itself to have disappeared
        (what a real confirmation actually looks like structurally), and
        ``CONFIRMED_FAILURE`` additionally requires the *opposite* — the
        same URL and the form still sitting right there, i.e. the form's own
        validation visibly blocked the attempt without anything being sent.
        Neither corroboration met, with or without a text match, falls
        through to ``UNCERTAIN``.

        Any exception during the click itself, or anything that happens
        afterwards (including a navigation race in Playwright's own click
        implementation), is reported as ``UNCERTAIN`` rather than
        ``CONFIRMED_FAILURE`` — we cannot safely rule out that the click's
        real-world effect fired even though the call raised.
        """
        from playwright.async_api import Page

        assert isinstance(page, Page)

        baseline_text, baseline_url = await self._snapshot(page)

        try:
            await control.click()  # type: ignore[attr-defined]
        except Exception as exc:
            logger.debug("browser_submit_click_raised", error_type=type(exc).__name__)
            return SubmitAttempt(
                outcome="UNCERTAIN",
                evidence=(
                    "The submit click did not complete cleanly, so whether the "
                    "employer received this application could not be confirmed."
                ),
            )

        try:
            return await self._observe_after_click(
                page,
                confirmation_timeout_ms,
                baseline_text=baseline_text,
                baseline_url=baseline_url,
            )
        except Exception as exc:
            logger.debug("browser_submit_observation_failed", error_type=type(exc).__name__)
            return SubmitAttempt(
                outcome="UNCERTAIN",
                evidence=(
                    "The page could not be read after submitting, so the outcome "
                    "could not be confirmed."
                ),
            )

    async def _snapshot(self, page: object) -> tuple[str, str]:
        """The page's current lower-cased body text and URL. Used both as the
        pre-click baseline and, read-only, by reconciliation — never changes
        anything on the page."""
        from playwright.async_api import Page

        assert isinstance(page, Page)
        try:
            text = (await page.inner_text("body")).lower()
        except Exception:
            text = ""
        return text, page.url

    async def _form_is_present(self, page: object) -> bool:
        """Whether the application form is still visibly on the page.

        A real Greenhouse confirmation typically replaces or hides the form
        it just accepted; a form still sitting there, unchanged, is itself
        evidence *against* a successful submission, and its continued
        presence (alongside an unchanged URL) is exactly what makes a
        rejection marker trustworthy rather than coincidental. If this
        cannot be determined (the page is in a bad state), the conservative
        reading is "still present" — the direction that blocks a false
        ``CONFIRMED_SUCCESS`` rather than permits one.
        """
        from playwright.async_api import Page

        assert isinstance(page, Page)
        try:
            if await page.locator("form").count() == 0:
                return False
            return await page.locator("form").first.is_visible()
        except Exception:
            return True

    async def _observe_after_click(
        self,
        page: object,
        confirmation_timeout_ms: int,
        *,
        baseline_text: str,
        baseline_url: str,
    ) -> SubmitAttempt:
        from playwright.async_api import Page

        assert isinstance(page, Page)
        deadline = asyncio.get_running_loop().time() + confirmation_timeout_ms / 1000
        while True:
            if page.is_closed():
                return SubmitAttempt(
                    outcome="UNCERTAIN",
                    evidence="The browser closed before the outcome could be confirmed.",
                )
            try:
                text = (await page.inner_text("body")).lower()
            except Exception:
                text = ""

            success = _first_new_marker(text, SUCCESS_TEXT_MARKERS, baseline_text)
            if success is not None:
                navigated = page.url != baseline_url
                form_gone = not await self._form_is_present(page)
                if navigated or form_gone:
                    return SubmitAttempt(
                        outcome="CONFIRMED_SUCCESS",
                        evidence=f"The page showed a new confirmation signal: {success!r}.",
                    )
                # A matching phrase appeared, but nothing about the page's own
                # structure corroborates it (same URL, form still right
                # there) — not trusted on text alone. Keep watching.

            failure = _first_new_marker(text, FAILURE_TEXT_MARKERS, baseline_text)
            if (
                failure is not None
                and page.url == baseline_url
                and await self._form_is_present(page)
            ):
                return SubmitAttempt(
                    outcome="CONFIRMED_FAILURE",
                    evidence=f"The page showed a new rejection signal: {failure!r}.",
                )

            if asyncio.get_running_loop().time() >= deadline:
                return SubmitAttempt(
                    outcome="UNCERTAIN",
                    evidence=(
                        "No recognised confirmation or rejection appeared on the "
                        "page within the timeout. This does not mean the "
                        "application was not received — verify on the "
                        "employer's site."
                    ),
                )
            await asyncio.sleep(_CONFIRMATION_POLL_INTERVAL_SECONDS)

    async def _collect_ids(self, page: object) -> frozenset[str]:
        from playwright.async_api import Page

        assert isinstance(page, Page)
        form_count = await page.locator("form").count()
        if form_count == 0:
            return frozenset()
        ids = await page.locator("form").first.locator("input, textarea").evaluate_all(
            "elements => elements.map(e => e.id).filter(Boolean)"
        )
        return frozenset(ids)

    def _classify(
        self,
        *,
        element_id: str | None,
        element_type: str,
        tag_name: str,
        role: str | None,
    ) -> tuple[FieldKind, bool]:
        if element_id in _STABLE_FILE_IDS or element_type == "file":
            return "file", False
        if role == "combobox":
            # A third-party JS widget (country, phone country-code). Never
            # auto-filled in this version — see the module docstring.
            return "combobox", True
        if element_type in ("checkbox", "radio"):
            return element_type, True  # type: ignore[return-value]
        if element_id in _STABLE_TEXT_IDS:
            kind: FieldKind = "tel" if element_id == "phone" else (
                "email" if element_id == "email" else "text"
            )
            return kind, False
        if tag_name == "textarea":
            return "textarea", False
        if element_type in ("text", "email", "tel"):
            return element_type, False  # type: ignore[return-value]
        return "unknown", True


def _origin_of(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
