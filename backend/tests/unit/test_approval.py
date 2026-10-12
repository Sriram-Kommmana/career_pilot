"""Approval binding and invalidation.

Safety requirement 3 says a meaningful change to the form after approval invalidates
it. The mechanism is an equality check on a digest of the reviewed state, so these
tests are mostly about what does and does not change that digest.

The distinction matters in both directions. A hash that changed too readily would send
users back to re-approve after cosmetic noise, training them to click through the
review. A hash that changed too rarely would let a real edit slip past an old approval.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.approval import (
    AnswerSnapshot,
    ApprovalRecord,
    DocumentSnapshot,
    FieldResolution,
    FieldSnapshot,
    FormSnapshot,
    VerificationStatus,
    approval_matches,
    changed_fields,
    compute_form_state_hash,
    digest_value,
    snapshot_from_pairs,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
URL = "https://example.invalid/apply"


def _field(
    field_id: str,
    value: str | None,
    *,
    required: bool = True,
    resolution: FieldResolution = FieldResolution.RESOLVED,
    verification: VerificationStatus = VerificationStatus.PASSED,
    sensitive: bool = False,
) -> FieldSnapshot:
    return FieldSnapshot(
        field_id=field_id,
        label=field_id.replace("_", " ").title(),
        resolution=resolution,
        required=required,
        value_for_hash=value,
        verification=verification,
        is_sensitive=sensitive,
    )


def _snapshot(*fields: FieldSnapshot, **kwargs: object) -> FormSnapshot:
    return FormSnapshot(application_url=URL, fields=fields, **kwargs)  # type: ignore[arg-type]


class TestHashStability:
    def test_identical_snapshots_hash_the_same(self) -> None:
        a = _snapshot(_field("full_name", "Demo"), _field("email", "d@example.com"))
        b = _snapshot(_field("full_name", "Demo"), _field("email", "d@example.com"))
        assert compute_form_state_hash(a) == compute_form_state_hash(b)

    def test_field_order_does_not_change_the_hash(self) -> None:
        """An adapter's discovery order must not invalidate a valid approval."""
        a = _snapshot(_field("full_name", "Demo"), _field("email", "d@example.com"))
        b = _snapshot(_field("email", "d@example.com"), _field("full_name", "Demo"))
        assert compute_form_state_hash(a) == compute_form_state_hash(b)

    def test_surrounding_whitespace_does_not_change_the_hash(self) -> None:
        """Browsers normalise this themselves; re-reading a form must be stable."""
        a = _snapshot(_field("full_name", "Demo Candidate"))
        b = _snapshot(_field("full_name", "  Demo   Candidate  "))
        assert compute_form_state_hash(a) == compute_form_state_hash(b)

    def test_case_does_change_the_hash(self) -> None:
        """ "Yes" and "yes" can be different option values on a real form."""
        a = _snapshot(_field("authorised", "Yes"))
        b = _snapshot(_field("authorised", "yes"))
        assert compute_form_state_hash(a) != compute_form_state_hash(b)


class TestHashSensitivity:
    def test_changing_a_value_changes_the_hash(self) -> None:
        a = _snapshot(_field("full_name", "Demo"))
        b = _snapshot(_field("full_name", "Someone Else"))
        assert compute_form_state_hash(a) != compute_form_state_hash(b)

    def test_adding_a_field_changes_the_hash(self) -> None:
        a = _snapshot(_field("full_name", "Demo"))
        b = _snapshot(_field("full_name", "Demo"), _field("phone", "123456789"))
        assert compute_form_state_hash(a) != compute_form_state_hash(b)

    def test_removing_a_field_changes_the_hash(self) -> None:
        a = _snapshot(_field("full_name", "Demo"), _field("phone", "123456789"))
        b = _snapshot(_field("full_name", "Demo"))
        assert compute_form_state_hash(a) != compute_form_state_hash(b)

    def test_changing_resolution_changes_the_hash(self) -> None:
        a = _snapshot(_field("authorised", "Yes"))
        b = _snapshot(_field("authorised", "Yes", resolution=FieldResolution.NEEDS_HUMAN))
        assert compute_form_state_hash(a) != compute_form_state_hash(b)

    def test_changing_verification_changes_the_hash(self) -> None:
        """An approval covers a *verified* form, so losing verification voids it."""
        a = _snapshot(_field("full_name", "Demo"))
        b = _snapshot(_field("full_name", "Demo", verification=VerificationStatus.NOT_ATTEMPTED))
        assert compute_form_state_hash(a) != compute_form_state_hash(b)

    def test_changing_the_application_url_changes_the_hash(self) -> None:
        a = FormSnapshot(application_url=URL, fields=(_field("full_name", "Demo"),))
        b = FormSnapshot(
            application_url="https://elsewhere.invalid/apply",
            fields=(_field("full_name", "Demo"),),
        )
        assert compute_form_state_hash(a) != compute_form_state_hash(b)

    def test_replacing_a_document_with_the_same_filename_changes_the_hash(self) -> None:
        """Keyed on content, not name: swapping the file must invalidate approval."""
        a = _snapshot(
            _field("full_name", "Demo"),
            documents=(
                DocumentSnapshot(slot="resume", filename="cv.pdf", content_sha256="a" * 64),
            ),
        )
        b = _snapshot(
            _field("full_name", "Demo"),
            documents=(
                DocumentSnapshot(slot="resume", filename="cv.pdf", content_sha256="b" * 64),
            ),
        )
        assert compute_form_state_hash(a) != compute_form_state_hash(b)

    def test_changing_an_answer_changes_the_hash(self) -> None:
        a = _snapshot(
            _field("full_name", "Demo"),
            answers=(
                AnswerSnapshot(
                    question_key="why_us",
                    value_sha256=digest_value("Because of the team."),
                    provenance="USER_DIRECT_INPUT",
                ),
            ),
        )
        b = _snapshot(
            _field("full_name", "Demo"),
            answers=(
                AnswerSnapshot(
                    question_key="why_us",
                    value_sha256=digest_value("Because of the product."),
                    provenance="USER_DIRECT_INPUT",
                ),
            ),
        )
        assert compute_form_state_hash(a) != compute_form_state_hash(b)

    def test_sensitive_values_are_covered_by_their_digest(self) -> None:
        """A sensitive value need not exist here, but changing it must still show."""
        a = _snapshot(_field("phone", digest_value("9876543210"), sensitive=True))
        b = _snapshot(_field("phone", digest_value("9876543211"), sensitive=True))
        assert compute_form_state_hash(a) != compute_form_state_hash(b)


class TestApprovalMatching:
    def _approval(self, form_hash: str, *, revoked: bool = False) -> ApprovalRecord:
        return ApprovalRecord(
            application_id="app-1",
            approved_by_user_id="user-1",
            approved_at=NOW,
            form_state_hash=form_hash,
            revoked_at=NOW if revoked else None,
        )

    def test_matching_hash_is_accepted(self) -> None:
        snapshot = _snapshot(_field("full_name", "Demo"))
        digest = compute_form_state_hash(snapshot)
        assert approval_matches(self._approval(digest), current_form_state_hash=digest)

    def test_different_hash_is_rejected(self) -> None:
        assert not approval_matches(self._approval("a" * 64), current_form_state_hash="b" * 64)

    def test_no_approval_is_rejected(self) -> None:
        assert not approval_matches(None, current_form_state_hash="a" * 64)

    def test_revoked_approval_is_rejected_even_on_a_matching_hash(self) -> None:
        assert not approval_matches(
            self._approval("a" * 64, revoked=True), current_form_state_hash="a" * 64
        )

    def test_end_to_end_invalidation(self) -> None:
        """What actually happens when a user edits a field after approving."""
        approved_snapshot = _snapshot(_field("full_name", "Demo"), _field("phone", "9876543210"))
        approval = self._approval(compute_form_state_hash(approved_snapshot))

        # The user corrects their phone number.
        current_snapshot = _snapshot(_field("full_name", "Demo"), _field("phone", "9876500000"))
        current_hash = compute_form_state_hash(current_snapshot)

        assert not approval_matches(approval, current_form_state_hash=current_hash)


class TestChangedFields:
    def test_reports_a_changed_value(self) -> None:
        before = _snapshot(_field("full_name", "Demo"))
        after = _snapshot(_field("full_name", "Someone Else"))
        assert changed_fields(approved=before, current=after) == ["full_name: value changed"]

    def test_reports_an_added_and_a_removed_field(self) -> None:
        before = _snapshot(_field("a", "1"))
        after = _snapshot(_field("b", "2"))
        differences = changed_fields(approved=before, current=after)
        assert "a: removed" in differences
        assert "b: added" in differences

    def test_reports_a_resolution_change(self) -> None:
        before = _snapshot(_field("authorised", "Yes"))
        after = _snapshot(_field("authorised", "Yes", resolution=FieldResolution.NEEDS_HUMAN))
        assert changed_fields(approved=before, current=after) == [
            "authorised: RESOLVED -> NEEDS_HUMAN"
        ]

    def test_reports_a_replaced_document(self) -> None:
        before = _snapshot(
            _field("a", "1"),
            documents=(
                DocumentSnapshot(slot="resume", filename="cv.pdf", content_sha256="a" * 64),
            ),
        )
        after = _snapshot(
            _field("a", "1"),
            documents=(
                DocumentSnapshot(slot="resume", filename="cv.pdf", content_sha256="b" * 64),
            ),
        )
        differences = changed_fields(approved=before, current=after)
        assert any("document[resume]" in d for d in differences)

    def test_identical_snapshots_report_no_changes(self) -> None:
        snapshot = _snapshot(_field("full_name", "Demo"))
        assert changed_fields(approved=snapshot, current=snapshot) == []


class TestBlockingFields:
    def test_a_field_needing_a_human_blocks(self) -> None:
        field = _field("authorised", None, resolution=FieldResolution.NEEDS_HUMAN)
        assert field.is_blocking

    def test_an_unknown_field_blocks(self) -> None:
        field = _field("mystery", None, resolution=FieldResolution.UNKNOWN_FIELD)
        assert field.is_blocking

    def test_a_required_skipped_field_blocks(self) -> None:
        field = _field("full_name", None, resolution=FieldResolution.SKIPPED)
        assert field.is_blocking

    def test_an_optional_skipped_field_does_not_block(self) -> None:
        field = _field("cover_letter", None, required=False, resolution=FieldResolution.SKIPPED)
        assert not field.is_blocking

    def test_a_required_unverified_field_blocks(self) -> None:
        field = _field("full_name", "Demo", verification=VerificationStatus.NOT_ATTEMPTED)
        assert field.is_blocking

    def test_a_resolved_verified_required_field_does_not_block(self) -> None:
        assert not _field("full_name", "Demo").is_blocking

    def test_blocking_fields_are_collected(self) -> None:
        snapshot = _snapshot(
            _field("full_name", "Demo"),
            _field("authorised", None, resolution=FieldResolution.NEEDS_HUMAN),
        )
        assert [f.field_id for f in snapshot.blocking_fields] == ["authorised"]


def test_snapshot_from_pairs_marks_missing_values_as_needing_a_human() -> None:
    snapshot = snapshot_from_pairs(
        application_url=URL,
        pairs=[
            ("full_name", "Full name", "Demo"),
            ("authorised", "Work authorisation", None),
        ],
        required_field_ids=["full_name", "authorised"],
    )
    by_id = {f.field_id: f for f in snapshot.fields}
    assert by_id["full_name"].resolution is FieldResolution.RESOLVED
    assert by_id["authorised"].resolution is FieldResolution.NEEDS_HUMAN
    assert by_id["authorised"].is_blocking
