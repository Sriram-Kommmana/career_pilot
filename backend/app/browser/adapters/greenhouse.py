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

import re
from typing import Final

from app.browser.adapters.base import (
    ExtractedField,
    FieldKind,
    FieldReadBack,
    FormInspectionResult,
)

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
