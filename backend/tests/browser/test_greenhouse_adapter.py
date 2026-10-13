"""``app.browser.adapters.greenhouse`` against local, sanitized fixtures.

Real Playwright, real Chromium, zero network — ``page.set_content()`` loads the
fixture directly, so ``page.url`` never leaves ``about:blank`` and no request
ever reaches a real site. The one test every other test in this module exists
to set up for: filling must never submit, under any circumstance.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.browser.adapters.base import ExtractedField
from app.browser.adapters.greenhouse import GreenhouseAdapter
from tests.browser.conftest import load_fixture

pytestmark = pytest.mark.browser


class TestDetection:
    async def test_a_standard_greenhouse_shaped_form_is_detected(self, page: object) -> None:
        await load_fixture(page, "greenhouse_standard.html")
        assert await GreenhouseAdapter().detect(page) is True

    async def test_an_unrelated_form_is_not_detected(self, page: object) -> None:
        await load_fixture(page, "unsupported_form.html")
        assert await GreenhouseAdapter().detect(page) is False


class TestInspection:
    async def test_an_unsupported_form_is_refused_not_guessed(self, page: object) -> None:
        await load_fixture(page, "unsupported_form.html")
        result = await GreenhouseAdapter().inspect(page)
        assert result.supported is False
        assert result.fields == ()
        assert result.reason is not None

    async def test_hidden_fields_are_never_extracted(self, page: object) -> None:
        await load_fixture(page, "greenhouse_standard.html")
        result = await GreenhouseAdapter().inspect(page)
        assert "job_id" not in {f.field_id for f in result.fields}

    async def test_stable_ids_are_recognised_with_the_right_kind_and_required_flag(
        self, page: object
    ) -> None:
        await load_fixture(page, "greenhouse_standard.html")
        result = await GreenhouseAdapter().inspect(page)
        by_id = {f.field_id: f for f in result.fields}

        assert by_id["first_name"].field_kind == "text"
        assert by_id["first_name"].required is True
        assert by_id["first_name"].label == "First Name"  # trailing * stripped

        assert by_id["email"].field_kind == "email"
        assert by_id["email"].required is True
        assert by_id["phone"].field_kind == "tel"
        assert by_id["phone"].required is False

    async def test_file_inputs_are_recognised_and_supported(self, page: object) -> None:
        await load_fixture(page, "greenhouse_standard.html")
        result = await GreenhouseAdapter().inspect(page)
        by_id = {f.field_id: f for f in result.fields}
        assert by_id["resume"].field_kind == "file"
        assert by_id["resume"].unrecognised is False

    async def test_a_combobox_widget_is_detected_but_never_auto_fillable(
        self, page: object
    ) -> None:
        await load_fixture(page, "greenhouse_standard.html")
        result = await GreenhouseAdapter().inspect(page)
        by_id = {f.field_id: f for f in result.fields}
        assert by_id["country"].field_kind == "combobox"
        assert by_id["country"].unrecognised is True

    async def test_checkboxes_and_radios_are_detected_but_never_auto_fillable(
        self, page: object
    ) -> None:
        await load_fixture(page, "greenhouse_standard.html")
        result = await GreenhouseAdapter().inspect(page)
        radios = [f for f in result.fields if f.field_kind == "radio"]
        assert radios
        assert all(f.unrecognised for f in radios)

    async def test_a_dynamically_numbered_custom_question_is_matched_by_label_not_id(
        self, page: object
    ) -> None:
        """The core live-verified finding: Greenhouse custom-question ids are
        per-posting and must never be treated as stable identifiers."""
        await load_fixture(page, "greenhouse_standard.html")
        result = await GreenhouseAdapter().inspect(page)
        linkedin = next(f for f in result.fields if f.field_id == "question_1001")
        assert linkedin.label == "LinkedIn Profile"
        assert linkedin.field_kind == "text"

    async def test_a_required_legal_question_is_extracted_as_a_plain_field(
        self, page: object
    ) -> None:
        """The adapter extracts it like any other required text field — it has
        no concept of "legal". Excluding it from auto-fill is layered
        separately in app.browser.mapping, which this test deliberately does
        not exercise, to prove the layering is real."""
        await load_fixture(page, "greenhouse_standard.html")
        result = await GreenhouseAdapter().inspect(page)
        legal = next(f for f in result.fields if f.field_id == "question_1003")
        assert legal.required is True
        assert legal.unrecognised is False
        assert "employment agreements" in (legal.label or "").lower()

    async def test_duplicate_labels_are_both_extracted_as_distinct_fields(
        self, page: object
    ) -> None:
        await load_fixture(page, "greenhouse_standard.html")
        result = await GreenhouseAdapter().inspect(page)
        websites = [f for f in result.fields if f.label == "Website"]
        assert {f.field_id for f in websites} == {"question_1004", "question_1005"}


class TestFillAndReadBack:
    async def test_filling_a_text_field_and_reading_it_back_matches(self, page: object) -> None:
        await load_fixture(page, "greenhouse_standard.html")
        adapter = GreenhouseAdapter()
        result = await adapter.inspect(page)
        first_name = next(f for f in result.fields if f.field_id == "first_name")

        await adapter.fill_field(page, first_name, "Ada")
        read_back = await adapter.read_field(page, first_name)
        assert read_back.value == "Ada"
        assert read_back.matches_intended is True

    async def test_filling_a_resume_file_input_and_reading_it_back(self, page: object) -> None:
        await load_fixture(page, "greenhouse_standard.html")
        adapter = GreenhouseAdapter()
        result = await adapter.inspect(page)
        resume_field = next(f for f in result.fields if f.field_id == "resume")

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
            handle.write(b"fake resume content")
            temp_path = handle.name
        try:
            await adapter.fill_field(page, resume_field, temp_path)
            read_back = await adapter.read_field(page, resume_field)
            assert read_back.matches_intended is True
        finally:
            Path(temp_path).unlink(missing_ok=True)  # noqa: ASYNC240 - tiny test cleanup


class TestNeverSubmitsDuringFill:
    async def test_filling_every_supported_field_never_calls_form_submit(
        self, page: object
    ) -> None:
        """The standing safety test for this module. Instruments the page's own
        HTMLFormElement.prototype.submit before filling anything, fills every
        field this adapter considers supported, and asserts the real submit
        path was never invoked."""
        await load_fixture(page, "greenhouse_standard.html")
        await page.evaluate(  # type: ignore[attr-defined]
            """() => {
                window.__submitCalls = 0;
                const proto = HTMLFormElement.prototype;
                const original = proto.submit;
                proto.submit = function (...args) {
                    window.__submitCalls += 1;
                    return original.apply(this, args);
                };
            }"""
        )
        requests: list[str] = []
        page.on(  # type: ignore[attr-defined]
            "request", lambda request: requests.append(request.url)
        )

        adapter = GreenhouseAdapter()
        result = await adapter.inspect(page)
        fillable: list[ExtractedField] = [
            f for f in result.fields if not f.unrecognised and f.field_kind != "file"
        ]
        assert fillable, (
            "the fixture must have at least one fillable field for this test to mean anything"
        )

        for extracted_field in fillable:
            await adapter.fill_field(page, extracted_field, "test value")

        submit_calls = await page.evaluate("window.__submitCalls")  # type: ignore[attr-defined]
        assert submit_calls == 0
        assert not any("application" in url and "form" not in url for url in requests)
        assert page.url in ("about:blank", "")  # type: ignore[attr-defined]

    async def test_filling_never_navigates_away_from_the_current_page(
        self, page: object
    ) -> None:
        await load_fixture(page, "greenhouse_standard.html")
        url_before = page.url  # type: ignore[attr-defined]
        adapter = GreenhouseAdapter()
        result = await adapter.inspect(page)
        email_field = next(f for f in result.fields if f.field_id == "email")
        await adapter.fill_field(page, email_field, "test@example.com")
        assert page.url == url_before  # type: ignore[attr-defined]


class TestFindSubmitControl:
    async def test_identifies_the_one_real_submit_control(self, page: object) -> None:
        await load_fixture(page, "greenhouse_standard.html")
        control = await GreenhouseAdapter().find_submit_control(page)
        assert control is not None
        assert (await control.inner_text()).strip() == "Submit application"  # type: ignore[attr-defined]

    async def test_returns_none_when_no_form_is_present(self, page: object) -> None:
        await page.set_content("<html><body><p>No form here.</p></body></html>")  # type: ignore[attr-defined]
        assert await GreenhouseAdapter().find_submit_control(page) is None

    async def test_returns_none_when_the_only_submit_control_is_not_named_submit(
        self, page: object
    ) -> None:
        """``unsupported_form.html``'s one submit button says "Send", not
        "submit" — never treated as the submit control on generic text alone,
        even though it is the only candidate."""
        await load_fixture(page, "unsupported_form.html")
        assert await GreenhouseAdapter().find_submit_control(page) is None

    async def test_returns_none_when_there_are_two_candidates(self, page: object) -> None:
        await load_fixture(page, "greenhouse_ambiguous_submit.html")
        assert await GreenhouseAdapter().find_submit_control(page) is None


class TestSubmitOutcomeClassification:
    """Phase 6's outcome classifier, against fixtures built to exercise each
    branch deterministically. No automated test here (or anywhere else in this
    project) ever submits to a real employer site — every fixture's `onsubmit`
    returns false, and page.set_content() pages have no server to POST to in
    the first place."""

    async def test_a_recognised_confirmation_is_classified_as_confirmed_success(
        self, page: object
    ) -> None:
        await load_fixture(page, "greenhouse_submission_success.html")
        adapter = GreenhouseAdapter()
        control = await adapter.find_submit_control(page)
        assert control is not None
        result = await adapter.submit(page, control, confirmation_timeout_ms=2000)
        assert result.outcome == "CONFIRMED_SUCCESS"

    async def test_a_recognised_rejection_with_no_navigation_is_confirmed_failure(
        self, page: object
    ) -> None:
        await load_fixture(page, "greenhouse_submission_rejected.html")
        adapter = GreenhouseAdapter()
        control = await adapter.find_submit_control(page)
        assert control is not None
        result = await adapter.submit(page, control, confirmation_timeout_ms=2000)
        assert result.outcome == "CONFIRMED_FAILURE"

    async def test_no_recognisable_signal_within_the_timeout_is_uncertain(
        self, page: object
    ) -> None:
        await load_fixture(page, "greenhouse_submission_ambiguous.html")
        adapter = GreenhouseAdapter()
        control = await adapter.find_submit_control(page)
        assert control is not None
        result = await adapter.submit(page, control, confirmation_timeout_ms=1000)
        assert result.outcome == "UNCERTAIN"

    async def test_a_closed_page_during_observation_is_uncertain_not_an_exception(
        self, page: object
    ) -> None:
        """Simulates a browser disconnect between the click and confirmation:
        the click itself must already have happened (it's a real DOM event),
        so this closes the page immediately after and proves the adapter
        still returns a classified result rather than raising."""
        await load_fixture(page, "greenhouse_submission_ambiguous.html")
        adapter = GreenhouseAdapter()
        control = await adapter.find_submit_control(page)
        assert control is not None
        await control.click()  # type: ignore[attr-defined]
        await page.close()  # type: ignore[attr-defined]
        result = await adapter._observe_after_click(
            page, 1000, baseline_text="", baseline_url="about:blank"
        )
        assert result.outcome == "UNCERTAIN"


class TestOutcomeClassifierRejectsStaleOrUnrelatedContent:
    """Adversarial regression tests: the classifier must never treat content
    that was already on the page, or that appears without any corroborating
    structural change, as evidence about *this* click. Each test both proves
    the safe outcome (UNCERTAIN) and that the fixture's own instrumented
    click count is exactly 1 — the click genuinely happened; only its
    interpretation is conservative."""

    async def test_a_success_phrase_present_before_the_click_is_not_confirmed_success(
        self, page: object
    ) -> None:
        await load_fixture(page, "greenhouse_submission_preexisting_success_marker.html")
        adapter = GreenhouseAdapter()
        control = await adapter.find_submit_control(page)
        assert control is not None
        result = await adapter.submit(page, control, confirmation_timeout_ms=1000)
        assert result.outcome == "UNCERTAIN"
        assert await page.evaluate("window.__submitAttempts || 0") == 1  # type: ignore[attr-defined]

    async def test_a_generic_failure_phrase_present_before_the_click_is_not_confirmed_failure(
        self, page: object
    ) -> None:
        await load_fixture(page, "greenhouse_submission_preexisting_failure_marker.html")
        adapter = GreenhouseAdapter()
        control = await adapter.find_submit_control(page)
        assert control is not None
        result = await adapter.submit(page, control, confirmation_timeout_ms=1000)
        assert result.outcome == "UNCERTAIN"
        assert await page.evaluate("window.__submitAttempts || 0") == 1  # type: ignore[attr-defined]

    async def test_navigation_with_no_recognised_marker_is_uncertain_not_success(
        self, page: object
    ) -> None:
        """Navigation alone is not evidence of anything -- it is only ever
        corroboration *alongside* a genuinely new marker, never a signal by
        itself."""
        await load_fixture(page, "greenhouse_submission_navigates_no_marker.html")
        adapter = GreenhouseAdapter()
        control = await adapter.find_submit_control(page)
        assert control is not None
        result = await adapter.submit(page, control, confirmation_timeout_ms=1000)
        assert result.outcome == "UNCERTAIN"
        assert await page.evaluate("window.__submitAttempts || 0") == 1  # type: ignore[attr-defined]

    async def test_a_new_marker_is_recognised_despite_unrelated_stale_content(
        self, page: object
    ) -> None:
        """The flip side of the previous two tests: a stale, unrelated
        failure-style phrase sitting on the page must not suppress a
        genuinely new, corroborated success signal."""
        await load_fixture(page, "greenhouse_submission_new_marker_despite_preexisting.html")
        adapter = GreenhouseAdapter()
        control = await adapter.find_submit_control(page)
        assert control is not None
        result = await adapter.submit(page, control, confirmation_timeout_ms=2000)
        assert result.outcome == "CONFIRMED_SUCCESS"
        assert await page.evaluate("window.__submitAttempts || 0") == 1  # type: ignore[attr-defined]

    async def test_a_new_marker_without_structural_corroboration_is_uncertain(
        self, page: object
    ) -> None:
        """A brand-new matching phrase appears, but the form is still fully
        present and the URL never changed -- text alone is not enough."""
        await load_fixture(page, "greenhouse_submission_marker_without_corroboration.html")
        adapter = GreenhouseAdapter()
        control = await adapter.find_submit_control(page)
        assert control is not None
        result = await adapter.submit(page, control, confirmation_timeout_ms=1000)
        assert result.outcome == "UNCERTAIN"
        assert await page.evaluate("window.__submitAttempts || 0") == 1  # type: ignore[attr-defined]
