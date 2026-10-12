"""Profile CRUD and ownership.

The ownership property matters more here than the CRUD mechanics: a child record's
id is never scoped by anything in the URL but the caller's own token, so there is no
way to address another user's education, skill, project, experience, or certification
row at all. These tests confirm that a second user's token gets a 404, not a 403 and
not the data.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestReadAndUpdate:
    async def test_a_fresh_profile_is_mostly_empty(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        response = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        assert response.status_code == 200
        body = response.json()
        assert body["full_name"] is None
        assert body["target_roles"] == []
        assert body["skills"] == []

    async def test_patch_updates_only_the_given_fields(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        first = await client.patch(  # type: ignore[attr-defined]
            f"{api}/profile", headers=auth, json={"full_name": "Demo Candidate"}
        )
        assert first.status_code == 200
        assert first.json()["full_name"] == "Demo Candidate"

        second = await client.patch(  # type: ignore[attr-defined]
            f"{api}/profile", headers=auth, json={"location": "Bengaluru, India"}
        )
        assert second.status_code == 200
        # The field set in the first call must survive a PATCH that omits it.
        assert second.json()["full_name"] == "Demo Candidate"
        assert second.json()["location"] == "Bengaluru, India"

    async def test_work_authorisation_is_only_ever_what_the_user_typed(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        response = await client.patch(  # type: ignore[attr-defined]
            f"{api}/profile",
            headers=auth,
            json={"work_authorisation": {"authorised": "Yes, citizen"}},
        )
        assert response.status_code == 200
        assert response.json()["work_authorisation"] == {"authorised": "Yes, citizen"}

    async def test_unauthenticated_access_is_refused(self, client: object, api: str) -> None:
        response = await client.get(f"{api}/profile")  # type: ignore[attr-defined]
        assert response.status_code == 401


class TestSkills:
    async def test_adding_a_skill_does_not_let_the_client_set_evidence(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        """``has_evidence`` is derived from projects/experience, never user input."""
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/skills", headers=auth, json={"name": "Python"}
        )
        assert response.status_code == 201, response.text
        assert response.json()["has_evidence"] is False

    async def test_a_project_citing_the_skill_marks_it_evidenced(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/skills", headers=auth, json={"name": "Python"}
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/projects",
            headers=auth,
            json={"name": "Jobboard Aggregator", "skills_used": ["Python"]},
        )
        response = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        skills = {s["name"]: s for s in response.json()["skills"]}
        assert skills["Python"]["has_evidence"] is True

    async def test_removing_the_evidencing_project_removes_the_evidence_flag(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/skills", headers=auth, json={"name": "Python"}
        )
        project = await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/projects",
            headers=auth,
            json={"name": "Jobboard Aggregator", "skills_used": ["Python"]},
        )
        project_id = project.json()["id"]

        await client.delete(  # type: ignore[attr-defined]
            f"{api}/profile/projects/{project_id}", headers=auth
        )

        response = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        skills = {s["name"]: s for s in response.json()["skills"]}
        assert skills["Python"]["has_evidence"] is False

    async def test_a_duplicate_canonical_skill_is_rejected(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/skills", headers=auth, json={"name": "Python"}
        )
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/skills", headers=auth, json={"name": "python"}
        )
        assert response.status_code >= 400


class TestChildRecordOwnership:
    """A second user's token must never reach the first user's child records."""

    async def test_cannot_update_someone_elses_skill(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
    ) -> None:
        created = await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/skills", headers=auth, json={"name": "Python"}
        )
        skill_id = created.json()["id"]

        response = await client.patch(  # type: ignore[attr-defined]
            f"{api}/profile/skills/{skill_id}",
            headers=second_auth,
            json={"name": "Hijacked"},
        )
        assert response.status_code == 404

    async def test_cannot_delete_someone_elses_skill(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
    ) -> None:
        created = await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/skills", headers=auth, json={"name": "Python"}
        )
        skill_id = created.json()["id"]

        response = await client.delete(  # type: ignore[attr-defined]
            f"{api}/profile/skills/{skill_id}", headers=second_auth
        )
        assert response.status_code == 404

        # And it is still there for the real owner.
        mine = await client.get(f"{api}/profile", headers=auth)  # type: ignore[attr-defined]
        assert any(s["id"] == skill_id for s in mine.json()["skills"])

    async def test_cannot_update_someone_elses_project(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
    ) -> None:
        created = await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/projects", headers=auth, json={"name": "Private Project"}
        )
        project_id = created.json()["id"]

        response = await client.patch(  # type: ignore[attr-defined]
            f"{api}/profile/projects/{project_id}",
            headers=second_auth,
            json={"name": "Stolen"},
        )
        assert response.status_code == 404

    async def test_cannot_delete_someone_elses_experience_record(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
    ) -> None:
        created = await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/experience",
            headers=auth,
            json={"company": "Acme", "title": "Engineer"},
        )
        record_id = created.json()["id"]

        response = await client.delete(  # type: ignore[attr-defined]
            f"{api}/profile/experience/{record_id}", headers=second_auth
        )
        assert response.status_code == 404

    async def test_two_users_can_each_use_the_same_skill_name(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
    ) -> None:
        """The uniqueness constraint is per profile, not global."""
        first = await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/skills", headers=auth, json={"name": "Python"}
        )
        second = await client.post(  # type: ignore[attr-defined]
            f"{api}/profile/skills", headers=second_auth, json={"name": "Python"}
        )
        assert first.status_code == 201
        assert second.status_code == 201
