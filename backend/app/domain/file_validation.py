"""Sniffing an uploaded file's real type from its bytes.

A client-supplied filename extension and ``Content-Type`` header are claims, not
facts — nothing stops a browser or a script from attaching ``.pdf`` to an executable.
This module looks at the actual bytes and decides what the file really is, with no
dependency on a file-type-sniffing library: ``python-magic`` needs the native
``libmagic`` library, which is awkward to install reliably on Windows, and the three
formats CareerPilot accepts (PDF, DOCX, plain text) are each recognisable from a
short, well-documented signature that is easy to verify and test.

Nothing here reads a file, renders it, or executes anything in it — only the first
few bytes (and, for DOCX, the names inside its zip container) are inspected.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from typing import Final

#: PDF files begin with this literal signature (the version number after the dash
#: varies, so only the fixed prefix is checked).
_PDF_SIGNATURE: Final = b"%PDF-"

#: Every zip-based format (DOCX, XLSX, ODT, a plain .zip) starts with this signature.
_ZIP_SIGNATURE: Final = b"PK\x03\x04"

#: The one file inside a DOCX zip that proves it is a Word document rather than some
#: other zip-based format sharing the same outer signature.
_DOCX_MARKER_ENTRY: Final = "word/document.xml"

#: Allowed upload extensions, matched case-insensitively against the claimed filename.
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset({".pdf", ".docx", ".txt"})

#: A null byte never appears in a genuine UTF-8 text resume; its presence is the
#: cheapest signal that a ".txt" upload is not actually plain text.
_NULL_BYTE: Final = b"\x00"

#: How many leading bytes are enough to recognise every signature this module checks.
_SNIFF_WINDOW: Final = 8


class SniffedFileType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    TEXT = "txt"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FileValidationResult:
    """The outcome of validating one uploaded file's bytes against its claimed name.

    ``ok`` is ``True`` only when the extension is allowed *and* the sniffed type
    matches it. ``reason`` is set whenever ``ok`` is ``False``, and is written to be
    shown directly to the user — it never includes file content.
    """

    ok: bool
    sniffed_type: SniffedFileType
    reason: str | None = None


def sniff_file_type(content: bytes) -> SniffedFileType:
    """Identify a file from its bytes alone, ignoring any claimed name or MIME type."""
    if content.startswith(_PDF_SIGNATURE):
        return SniffedFileType.PDF
    if content.startswith(_ZIP_SIGNATURE):
        if _is_docx(content):
            return SniffedFileType.DOCX
        return SniffedFileType.UNKNOWN
    if _looks_like_text(content):
        return SniffedFileType.TEXT
    return SniffedFileType.UNKNOWN


def _is_docx(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            return _DOCX_MARKER_ENTRY in archive.namelist()
    except zipfile.BadZipFile:
        return False


def _looks_like_text(content: bytes) -> bool:
    """Plain text: no null bytes, and it decodes cleanly as UTF-8.

    An empty file is not text — there is nothing to extract from it, so treating it
    as valid would just defer an equally clear rejection to the parsing step.
    """
    if not content or _NULL_BYTE in content:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


_EXTENSION_TO_TYPE: Final[dict[str, SniffedFileType]] = {
    ".pdf": SniffedFileType.PDF,
    ".docx": SniffedFileType.DOCX,
    ".txt": SniffedFileType.TEXT,
}


def validate_upload(*, filename: str, content: bytes) -> FileValidationResult:
    """Check a claimed filename's extension against what the bytes actually are.

    Both checks must pass: an unsupported extension is rejected before the bytes are
    even sniffed, and a supported extension whose content doesn't match (e.g. a
    ``.pdf`` that is actually a DOCX, or an executable renamed to ``.txt``) is
    rejected just as firmly. There is no "best effort, accept anyway" path.
    """
    extension = _extension_of(filename)
    if extension not in ALLOWED_EXTENSIONS:
        return FileValidationResult(
            ok=False,
            sniffed_type=SniffedFileType.UNKNOWN,
            reason=(
                f"Unsupported file type {extension or '(none)'!r}. "
                "Upload a PDF, DOCX, or plain text resume."
            ),
        )

    sniffed = sniff_file_type(content)
    expected = _EXTENSION_TO_TYPE[extension]
    if sniffed is not expected:
        return FileValidationResult(
            ok=False,
            sniffed_type=sniffed,
            reason=(
                f"This file's content does not match its {extension} extension. "
                "Re-save it and try again, or upload a different file."
            ),
        )
    return FileValidationResult(ok=True, sniffed_type=sniffed)


def _extension_of(filename: str) -> str:
    name = filename.strip().lower()
    dot = name.rfind(".")
    if dot == -1:
        return ""
    return name[dot:]
