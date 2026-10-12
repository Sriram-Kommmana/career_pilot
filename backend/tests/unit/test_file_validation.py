"""Sniffing an uploaded resume's real type from its bytes.

Pure, stdlib-only, no network or filesystem — belongs entirely in ``tests/unit``.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.domain.file_validation import (
    FileValidationResult,
    SniffedFileType,
    sniff_file_type,
    validate_upload,
)

pytestmark = pytest.mark.unit

_PDF_BYTES = b"%PDF-1.4\n%fake pdf content for a test\n%%EOF"
_TEXT_BYTES = b"Jane Doe\nBackend Engineer\n\nExperience\nAcme Corp - built things.\n"


def _docx_bytes(*, with_marker: bool = True) -> bytes:
    """A minimal in-memory zip, optionally shaped like a real DOCX."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        if with_marker:
            archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()


def _plain_zip_bytes() -> bytes:
    """A zip that is not a DOCX — no word/document.xml inside."""
    return _docx_bytes(with_marker=False)


class TestSniffFileType:
    def test_a_real_pdf_signature_is_recognised(self) -> None:
        assert sniff_file_type(_PDF_BYTES) is SniffedFileType.PDF

    def test_a_real_docx_is_recognised(self) -> None:
        assert sniff_file_type(_docx_bytes()) is SniffedFileType.DOCX

    def test_a_zip_without_the_docx_marker_is_unknown(self) -> None:
        assert sniff_file_type(_plain_zip_bytes()) is SniffedFileType.UNKNOWN

    def test_a_corrupted_zip_is_unknown_not_a_crash(self) -> None:
        assert (
            sniff_file_type(b"PK\x03\x04" + b"not actually a valid zip") is SniffedFileType.UNKNOWN
        )

    def test_plain_utf8_text_is_recognised(self) -> None:
        assert sniff_file_type(_TEXT_BYTES) is SniffedFileType.TEXT

    def test_empty_content_is_unknown(self) -> None:
        assert sniff_file_type(b"") is SniffedFileType.UNKNOWN

    def test_a_null_byte_disqualifies_text(self) -> None:
        assert sniff_file_type(b"resume\x00with a null byte") is SniffedFileType.UNKNOWN

    def test_invalid_utf8_disqualifies_text(self) -> None:
        assert sniff_file_type(b"\xff\xfe\x00bad encoding") is SniffedFileType.UNKNOWN

    def test_an_executable_signature_is_unknown(self) -> None:
        # The MZ header of a Windows executable.
        assert sniff_file_type(b"MZ\x90\x00\x03\x00\x00\x00") is SniffedFileType.UNKNOWN


class TestValidateUpload:
    def test_a_matching_pdf_is_accepted(self) -> None:
        result = validate_upload(filename="resume.pdf", content=_PDF_BYTES)
        assert result == FileValidationResult(ok=True, sniffed_type=SniffedFileType.PDF)

    def test_a_matching_docx_is_accepted(self) -> None:
        result = validate_upload(filename="resume.DOCX", content=_docx_bytes())
        assert result.ok is True
        assert result.sniffed_type is SniffedFileType.DOCX

    def test_a_matching_text_file_is_accepted(self) -> None:
        result = validate_upload(filename="resume.txt", content=_TEXT_BYTES)
        assert result.ok is True

    def test_an_unsupported_extension_is_rejected_before_sniffing(self) -> None:
        result = validate_upload(filename="resume.exe", content=_PDF_BYTES)
        assert result.ok is False
        assert "Unsupported file type" in (result.reason or "")

    def test_a_filename_with_no_extension_is_rejected(self) -> None:
        result = validate_upload(filename="resume", content=_TEXT_BYTES)
        assert result.ok is False

    def test_an_executable_renamed_to_txt_is_rejected(self) -> None:
        result = validate_upload(filename="resume.txt", content=b"MZ\x90\x00fake exe")
        assert result.ok is False
        assert "does not match" in (result.reason or "")

    def test_a_docx_renamed_to_pdf_is_rejected(self) -> None:
        result = validate_upload(filename="resume.pdf", content=_docx_bytes())
        assert result.ok is False
        assert result.sniffed_type is SniffedFileType.DOCX

    def test_a_pdf_renamed_to_docx_is_rejected(self) -> None:
        result = validate_upload(filename="resume.docx", content=_PDF_BYTES)
        assert result.ok is False

    def test_the_rejection_reason_never_contains_file_content(self) -> None:
        secret_content = b"Jane Doe, SSN 123-45-6789, DOB 1990-01-01"
        result = validate_upload(filename="resume.exe", content=secret_content)
        assert result.reason is not None
        assert "123-45-6789" not in result.reason
        assert "Jane Doe" not in result.reason

    def test_extension_matching_is_case_insensitive(self) -> None:
        result = validate_upload(filename="Resume.PDF", content=_PDF_BYTES)
        assert result.ok is True
