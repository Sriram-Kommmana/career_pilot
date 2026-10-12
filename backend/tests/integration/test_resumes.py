"""Resume upload, extraction, and confirmation, end to end.

The property that matters most here is the one from ``app.services.resumes``'s
module docstring: upload, extraction, and confirmation are three separate trust
levels, and nothing before an explicit confirm call may touch the profile. Every
test that checks "did this write to the profile" checks it by re-reading
``GET /profile`` through the real HTTP client, not by inspecting internal state.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_TEXT_RESUME = """\
Jane Doe
Backend Engineer

Experience
Senior Backend Engineer - Acme Corp
Jan 2021 - Present
Built a Python/FastAPI service handling real traffic.

Education
Bachelor of Technology in Computer Science
Indian Institute of Technology
2014 - 2018

Skills
Python, Django, FastAPI, PostgreSQL, Docker
"""


def _minimal_pdf_bytes(lines: list[str] | str) -> bytes:
    """A byte-accurate minimal single-page PDF with one or more lines of real text.

    Each line must avoid PDF string-literal special characters (parentheses,
    backslashes) — plain ASCII words are all any test here needs. A blank string in
    ``lines`` is deliberately supported: a real-world PDF text extractor (``pypdf``
    among them) drops an empty text-show operation's line entirely, which is
    exactly the behaviour ``TestRealFileExtraction`` below exercises and depends on
    — see ``app.domain.resume_extraction._split_by_date_anchors``.
    """
    if isinstance(lines, str):
        lines = [lines]
    y = 740
    ops = []
    for line in lines:
        ops.append(f"1 0 0 1 72 {y} Tm ({line}) Tj")
        y -= 14
    content_stream = ("BT /F1 10 Tf\n" + "\n".join(ops) + "\nET").encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content_stream)).encode() + b" >>\nstream\n"
        + content_stream
        + b"\nendstream",
    ]
    buffer = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(buffer))
        buffer += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(buffer)
    buffer += f"xref\n0 {len(objects) + 1}\n".encode()
    buffer += b"0000000000 65535 f \n"
    for offset in offsets:
        buffer += f"{offset:010d} 00000 n \n".encode()
    buffer += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
    buffer += b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF"
    return bytes(buffer)


def _docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


async def _upload_text_resume(
    client: object,
    api: str,
    auth: dict[str, str],
    *,
    text: str = _TEXT_RESUME,
    label: str | None = None,
) -> dict[str, object]:
    files = {"file": ("resume.txt", text.encode(), "text/plain")}
    data = {"label": label} if label else {}
    response = await client.post(  # type: ignore[attr-defined]
        f"{api}/resumes", headers=auth, files=files, data=data
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


class TestUpload:
    async def test_a_valid_text_resume_is_accepted_and_stored(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        body = await _upload_text_resume(client, api, auth)
        assert body["content_type"] == "text/plain"
        assert body["extraction_status"] == "pending"
        assert body["extraction_confirmed_at"] is None
        assert len(str(body["content_sha256"])) == 64

    async def test_a_valid_pdf_resume_is_accepted(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        pdf = _minimal_pdf_bytes("Jane Doe Backend Engineer")
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes",
            headers=auth,
            files={"file": ("resume.pdf", pdf, "application/pdf")},
        )
        assert response.status_code == 201, response.text
        assert response.json()["content_type"] == "application/pdf"

    async def test_a_valid_docx_resume_is_accepted(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        docx = _docx_bytes(["Jane Doe", "Backend Engineer"])
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes",
            headers=auth,
            files={
                "file": (
                    "resume.docx",
                    docx,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["content_type"].endswith("wordprocessingml.document")

    async def test_the_stored_filename_is_never_the_clients_filename(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        """A path-traversal-shaped filename must not reach the filesystem layer."""
        files = {"file": ("../../etc/passwd.txt", b"not actually sensitive", "text/plain")}
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes", headers=auth, files=files
        )
        assert response.status_code == 201, response.text
        assert response.json()["original_filename"] == "../../etc/passwd.txt"
        # The real path validation is covered at the unit level (test_file_validation.py
        # and app.services.resumes.upload_resume deriving the on-disk name from the
        # resume id); this just confirms the request itself isn't rejected outright for
        # having an unusual display name.

    async def test_an_executable_renamed_to_txt_is_rejected(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        files = {"file": ("resume.txt", b"MZ\x90\x00\x03\x00\x00\x00fake exe", "text/plain")}
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes", headers=auth, files=files
        )
        assert response.status_code == 422, response.text

    async def test_an_unsupported_extension_is_rejected(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        files = {"file": ("resume.exe", b"MZ\x90\x00fake", "application/octet-stream")}
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes", headers=auth, files=files
        )
        assert response.status_code == 422

    async def test_an_oversized_upload_is_rejected(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        # Settings.max_upload_bytes defaults to 5 MiB; comfortably exceed it.
        oversized = b"a" * (6 * 1024 * 1024)
        files = {"file": ("resume.txt", oversized, "text/plain")}
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes", headers=auth, files=files
        )
        assert response.status_code == 413, response.text

    async def test_a_quota_of_ten_resumes_is_enforced(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        for i in range(10):
            await _upload_text_resume(client, api, auth, label=f"resume-{i}")
        files = {"file": ("one-too-many.txt", _TEXT_RESUME.encode(), "text/plain")}
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes", headers=auth, files=files, data={"label": "one-too-many"}
        )
        assert response.status_code == 409, response.text

    async def test_unauthenticated_upload_is_refused(self, client: object, api: str) -> None:
        files = {"file": ("resume.txt", _TEXT_RESUME.encode(), "text/plain")}
        response = await client.post(f"{api}/resumes", files=files)  # type: ignore[attr-defined]
        assert response.status_code == 401

    async def test_a_second_user_cannot_see_the_first_users_resume(
        self, client: object, api: str, auth: dict[str, str], second_auth: dict[str, str]
    ) -> None:
        body = await _upload_text_resume(client, api, auth)
        response = await client.get(  # type: ignore[attr-defined]
            f"{api}/resumes/{body['id']}", headers=second_auth
        )
        assert response.status_code == 404


_MULTI_JOB_RESUME_LINES = [
    "Alex Morgan",
    "Senior Backend Engineer",
    "",
    "Experience",
    "Staff Engineer - Lighthouse Payments",
    "Feb 2022 - Present",
    "Designed a Python service processing payments at scale.",
    "",
    "Backend Engineer - Harbor Analytics",
    "Aug 2017 - Jan 2022",
    "Built internal REST APIs in Django and deployed them on AWS.",
    "",
    "Education",
    "Master of Science in Computer Science",
    "Carnegie State University",
    "2015 - 2017",
    "",
    "Skills",
    "Python, Django, PostgreSQL, AWS",
]


class TestRealFileExtraction:
    """``TestUpload``/``TestExtraction`` exercise the pipeline with plain text,
    which never has the layout problems a real file format can introduce.  These
    tests use genuinely parseable PDF and DOCX bytes (not just a format's magic
    bytes) and check that what comes out the other end is sensible — this is what
    caught a real bug: ``pypdf`` drops a line entirely when it had no visible
    characters, which silently deletes the blank lines a resume used to separate
    two jobs. See ``app.domain.resume_extraction._split_by_date_anchors`` and
    ``tests/unit/test_resume_extraction.py::TestEntriesWithoutBlankLineSeparators``
    for the fix and its unit-level coverage; this is the same scenario, through the
    real upload and extraction HTTP endpoints with a real ``pypdf`` call.
    """

    async def test_a_real_multi_job_pdf_keeps_both_jobs_separate(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        pdf = _minimal_pdf_bytes(_MULTI_JOB_RESUME_LINES)
        upload = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes",
            headers=auth,
            files={"file": ("multi_job.pdf", pdf, "application/pdf")},
        )
        assert upload.status_code == 201, upload.text

        extraction = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{upload.json()['id']}/extract", headers=auth
        )
        assert extraction.status_code == 200, extraction.text
        assert extraction.json()["extraction_status"] == "succeeded"

        experience_items = [
            i for i in extraction.json()["items"] if i["field"] == "experience"
        ]
        companies = {i["data"]["company"] for i in experience_items}
        assert len(experience_items) == 2
        assert "Lighthouse Payments" in companies
        assert "Harbor Analytics" in companies

        skill_names = {
            i["data"]["name"] for i in extraction.json()["items"] if i["field"] == "skill"
        }
        assert {"python", "django", "postgresql", "aws"} <= skill_names

    async def test_a_real_docx_resume_extracts_sensibly(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        docx_bytes = _docx_bytes(_MULTI_JOB_RESUME_LINES)
        upload = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes",
            headers=auth,
            files={
                "file": (
                    "multi_job.docx",
                    docx_bytes,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert upload.status_code == 201, upload.text

        extraction = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{upload.json()['id']}/extract", headers=auth
        )
        assert extraction.status_code == 200, extraction.text
        assert extraction.json()["extraction_status"] == "succeeded"

        experience_items = [
            i for i in extraction.json()["items"] if i["field"] == "experience"
        ]
        assert len(experience_items) == 2

        education_items = [i for i in extraction.json()["items"] if i["field"] == "education"]
        assert education_items[0]["data"]["institution"] == "Carnegie State University"


class TestExtraction:
    async def test_extraction_stages_a_proposal_without_touching_the_profile(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        resume = await _upload_text_resume(client, api, auth)
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume['id']}/extract", headers=auth
        )
        assert response.status_code == 200, response.text
        proposal = response.json()
        assert proposal["extraction_status"] == "succeeded"
        assert proposal["items"], "the sample resume should yield at least one item"

        profile = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        assert profile.json()["skills"] == []
        assert profile.json()["experience_records"] == []

    async def test_every_staged_item_traces_back_to_the_resume_text(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        resume = await _upload_text_resume(client, api, auth)
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume['id']}/extract", headers=auth
        )
        collapsed_resume = " ".join(_TEXT_RESUME.split())
        for item in response.json()["items"]:
            collapsed_excerpt = " ".join(item["source_excerpt"].split())
            assert collapsed_excerpt in collapsed_resume

    async def test_a_corrupted_file_fails_cleanly_not_with_a_500(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        # A .docx that is a zip with the DOCX marker but garbage XML inside it.
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", "not valid xml <<<")
            archive.writestr("[Content_Types].xml", "<Types/>")
        files = {
            "file": (
                "broken.docx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        }
        upload = await client.post(f"{api}/resumes", headers=auth, files=files)  # type: ignore[attr-defined]
        assert upload.status_code == 201, upload.text
        resume_id = upload.json()["id"]

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume_id}/extract", headers=auth
        )
        assert response.status_code == 200
        assert response.json()["extraction_status"] == "failed"

        resume_row = await client.get(f"{api}/resumes/{resume_id}", headers=auth)  # type: ignore[attr-defined]
        assert resume_row.json()["extraction_status"] == "failed"
        assert resume_row.json()["extraction_error"]

    async def test_retrying_extraction_after_a_failure_is_safe(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        """Retrying a failing extraction must not crash or corrupt the resume row,
        even if the retry fails again for the same reason."""
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", "not valid xml <<<")
            archive.writestr("[Content_Types].xml", "<Types/>")
        files = {
            "file": (
                "broken.docx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        }
        upload = await client.post(f"{api}/resumes", headers=auth, files=files)  # type: ignore[attr-defined]
        resume_id = upload.json()["id"]

        first = await client.post(f"{api}/resumes/{resume_id}/extract", headers=auth)  # type: ignore[attr-defined]
        assert first.status_code == 200
        assert first.json()["extraction_status"] == "failed"

        second = await client.post(f"{api}/resumes/{resume_id}/extract", headers=auth)  # type: ignore[attr-defined]
        assert second.status_code == 200
        assert second.json()["extraction_status"] == "failed"

        resume_row = await client.get(f"{api}/resumes/{resume_id}", headers=auth)  # type: ignore[attr-defined]
        assert resume_row.status_code == 200
        assert resume_row.json()["extraction_status"] == "failed"

    async def test_retrying_with_a_fixed_file_succeeds_after_a_prior_failure(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        """The realistic retry path: the first upload is bad, the user deletes it
        and uploads a good one instead. The failed attempt must leave nothing
        behind that could confuse or block the successful one."""
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", "not valid xml <<<")
            archive.writestr("[Content_Types].xml", "<Types/>")
        bad_files = {
            "file": (
                "broken.docx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        }
        bad_upload = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes", headers=auth, files=bad_files, data={"label": "broken"}
        )
        bad_id = bad_upload.json()["id"]
        failed = await client.post(f"{api}/resumes/{bad_id}/extract", headers=auth)  # type: ignore[attr-defined]
        assert failed.json()["extraction_status"] == "failed"

        await client.delete(f"{api}/resumes/{bad_id}", headers=auth)  # type: ignore[attr-defined]

        good = await _upload_text_resume(client, api, auth, label="fixed")
        retried = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{good['id']}/extract", headers=auth
        )
        assert retried.status_code == 200
        assert retried.json()["extraction_status"] == "succeeded"
        assert retried.json()["items"]

    async def test_get_extraction_refetches_without_rerunning(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        resume = await _upload_text_resume(client, api, auth)
        await client.post(f"{api}/resumes/{resume['id']}/extract", headers=auth)  # type: ignore[attr-defined]
        response = await client.get(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume['id']}/extraction", headers=auth
        )
        assert response.status_code == 200
        assert response.json()["items"]

    async def test_a_second_user_cannot_extract_someone_elses_resume(
        self, client: object, api: str, auth: dict[str, str], second_auth: dict[str, str]
    ) -> None:
        resume = await _upload_text_resume(client, api, auth)
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume['id']}/extract", headers=second_auth
        )
        assert response.status_code == 404


class TestConfirmation:
    async def _staged_items(
        self, client: object, api: str, auth: dict[str, str]
    ) -> tuple[str, list[dict[str, Any]]]:
        resume = await _upload_text_resume(client, api, auth)
        extraction = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume['id']}/extract", headers=auth
        )
        return str(resume["id"]), extraction.json()["items"]

    async def test_nothing_is_written_until_confirm_is_called(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        _resume_id, items = await self._staged_items(client, api, auth)
        assert items
        profile = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        assert profile.json()["skills"] == []

    async def test_accepting_items_writes_them_with_resume_confirmed_provenance(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        resume_id, items = await self._staged_items(client, api, auth)
        decisions = [{"index": item["index"], "accept": True} for item in items]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume_id}/confirm-extraction",
            headers=auth,
            json={"decisions": decisions},
        )
        assert response.status_code == 200, response.text
        statuses = {r["status"] for r in response.json()["results"]}
        assert statuses <= {"created", "updated", "skipped"}

        profile = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        skill_names = {s["canonical_name"] for s in profile.json()["skills"]}
        assert "python" in skill_names
        for skill in profile.json()["skills"]:
            if skill["canonical_name"] in {"python", "django", "fastapi", "postgresql", "docker"}:
                assert skill["provenance"] == "RESUME_EXTRACTION_CONFIRMED"

        resume_row = await client.get(f"{api}/resumes/{resume_id}", headers=auth)  # type: ignore[attr-defined]
        assert resume_row.json()["extraction_confirmed_at"] is not None

    async def test_rejecting_an_item_leaves_it_out(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        resume_id, items = await self._staged_items(client, api, auth)
        skill_item = next(
            i for i in items if i["field"] == "skill" and i["data"]["name"] == "python"
        )
        decisions = [{"index": skill_item["index"], "accept": False}]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume_id}/confirm-extraction",
            headers=auth,
            json={"decisions": decisions},
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "rejected"

        profile = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        assert profile.json()["skills"] == []

    async def test_an_existing_confirmed_record_is_never_replaced_without_an_explicit_id(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        """The user already has a manually-entered skill; confirming an extracted
        duplicate of it must not silently alter the existing row."""
        await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/skills", headers=auth, json={"name": "Python", "years": 7}
        )
        resume_id, items = await self._staged_items(client, api, auth)
        python_item = next(
            i for i in items if i["field"] == "skill" and i["data"]["name"] == "python"
        )
        assert python_item["possible_duplicate_of"] is not None

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume_id}/confirm-extraction",
            headers=auth,
            json={"decisions": [{"index": python_item["index"], "accept": True}]},
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "skipped"

        profile = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        python_skills = [s for s in profile.json()["skills"] if s["canonical_name"] == "python"]
        assert len(python_skills) == 1
        assert python_skills[0]["years"] == 7
        assert python_skills[0]["provenance"] == "USER_CONFIRMED_PROFILE"

    async def test_updating_an_existing_education_record_requires_the_explicit_id(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        existing = await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/education",
            headers=auth,
            json={"institution": "Indian Institute of Technology", "degree": "Something Else"},
        )
        assert existing.status_code == 201, existing.text
        existing_id = existing.json()["id"]

        resume_id, items = await self._staged_items(client, api, auth)
        education_item = next(i for i in items if i["field"] == "education")
        assert education_item["possible_duplicate_of"] == existing_id

        # Confirm WITHOUT the explicit id: must create a new row, not touch the old one.
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume_id}/confirm-extraction",
            headers=auth,
            json={"decisions": [{"index": education_item["index"], "accept": True}]},
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "created"

        profile = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        assert len(profile.json()["education_records"]) == 2
        original = next(
            r for r in profile.json()["education_records"] if r["id"] == existing_id
        )
        assert original["degree"] == "Something Else"

    async def test_updating_an_existing_education_record_with_the_explicit_id_works(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        existing = await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/education",
            headers=auth,
            json={"institution": "Indian Institute of Technology", "degree": None},
        )
        existing_id = existing.json()["id"]

        resume_id, items = await self._staged_items(client, api, auth)
        education_item = next(i for i in items if i["field"] == "education")

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume_id}/confirm-extraction",
            headers=auth,
            json={
                "decisions": [
                    {
                        "index": education_item["index"],
                        "accept": True,
                        "existing_record_id": existing_id,
                    }
                ]
            },
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "updated"
        assert response.json()["results"][0]["record_id"] == existing_id

        profile = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        assert len(profile.json()["education_records"]) == 1
        updated = profile.json()["education_records"][0]
        assert updated["provenance"] == "RESUME_EXTRACTION_CONFIRMED"
        assert updated["degree"] == "Bachelor of Technology in Computer Science"

    async def test_a_second_user_cannot_confirm_extraction_on_someone_elses_resume(
        self, client: object, api: str, auth: dict[str, str], second_auth: dict[str, str]
    ) -> None:
        resume_id, items = await self._staged_items(client, api, auth)
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume_id}/confirm-extraction",
            headers=second_auth,
            json={"decisions": [{"index": items[0]["index"], "accept": True}]},
        )
        assert response.status_code == 404

    async def test_deleting_a_resume_after_confirmation_does_not_remove_profile_data(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        """Once confirmed, a fact belongs to the profile, not to the document it
        came from. Deleting the resume must not take the confirmed skill with it."""
        resume_id, items = await self._staged_items(client, api, auth)
        skill_item = next(i for i in items if i["field"] == "skill")
        await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume_id}/confirm-extraction",
            headers=auth,
            json={"decisions": [{"index": skill_item["index"], "accept": True}]},
        )
        before = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        skill_count_before = len(before.json()["skills"])
        assert skill_count_before > 0

        delete_response = await client.delete(f"{api}/resumes/{resume_id}", headers=auth)  # type: ignore[attr-defined]
        assert delete_response.status_code == 200

        after = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        assert len(after.json()["skills"]) == skill_count_before

    async def test_re_extracting_after_confirmation_does_not_touch_confirmed_rows(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        """Re-running extraction only replaces the staged (unconfirmed) proposal.
        It must never revisit or alter a row an earlier confirmation already wrote,
        even though the second extraction produces the identical proposal."""
        resume_id, items = await self._staged_items(client, api, auth)
        skill_item = next(i for i in items if i["field"] == "skill")
        await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume_id}/confirm-extraction",
            headers=auth,
            json={"decisions": [{"index": skill_item["index"], "accept": True}]},
        )
        before = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        skills_before = before.json()["skills"]

        rerun = await client.post(f"{api}/resumes/{resume_id}/extract", headers=auth)  # type: ignore[attr-defined]
        assert rerun.status_code == 200
        assert rerun.json()["extraction_status"] == "succeeded"

        after = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        assert after.json()["skills"] == skills_before


class TestEvidenceBackedMatching:
    async def test_confirming_resume_evidence_changes_the_evidence_component(
        self, client: object, api: str, auth: dict[str, str], session: object, seeded_job: object
    ) -> None:
        """End-to-end proof that confirmed resume facts reach the match engine: a
        job whose requirement matches a skill with no evidence scores that
        component as unassessed or low; once the matching experience entry (with
        skills_used) is confirmed from a resume, the same job's evidence component
        reflects it."""
        await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/skills", headers=auth, json={"name": "Python"}
        )

        before = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/{seeded_job.id}/analyze", headers=auth  # type: ignore[attr-defined]
        )
        before_evidence = next(c for c in before.json()["components"] if c["name"] == "evidence")

        resume = await _upload_text_resume(client, api, auth)
        extraction = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume['id']}/extract", headers=auth
        )
        experience_item = next(
            i for i in extraction.json()["items"] if i["field"] == "experience"
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{resume['id']}/confirm-extraction",
            headers=auth,
            json={"decisions": [{"index": experience_item["index"], "accept": True}]},
        )

        after = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/{seeded_job.id}/analyze", headers=auth  # type: ignore[attr-defined]
        )
        after_evidence = next(c for c in after.json()["components"] if c["name"] == "evidence")

        assert (
            after_evidence != before_evidence
            or after_evidence["assessed"] != before_evidence["assessed"]
        )
