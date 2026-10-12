"""Reading a resume's plain text into a staged proposal of candidate facts.

Pure, stdlib-only, no network or filesystem, no model — belongs in ``tests/unit``.
The most important property tested here is traceability: every item's
``source_excerpt`` must be text that is actually present in the resume, never
something invented. See ``app.domain.resume_extraction`` for why.
"""

from __future__ import annotations

import pytest

from app.domain.resume_extraction import ExtractedItem, extract

pytestmark = pytest.mark.unit

_SAMPLE_RESUME = """\
Jane Doe
Backend Engineer

Experienced backend engineer with a passion for distributed systems and reliable APIs.

Experience
Senior Backend Engineer - Acme Corp
Jan 2021 - Present
Built a Python/FastAPI service handling 10k requests per second.
Led migration from MySQL to PostgreSQL.

Backend Engineer - Globex Inc
Jun 2018 - Dec 2020
Developed REST APIs using Django and deployed on AWS with Docker.

Education
Bachelor of Technology in Computer Science
Indian Institute of Technology
2014 - 2018

Skills
Python, Django, FastAPI, PostgreSQL, Docker, Kubernetes, AWS, React

Projects
Redis Clone
https://github.com/janedoe/redis-clone
Implemented a simplified Redis server in Rust supporting GET/SET and pub/sub.

Certifications
AWS Certified Solutions Architect - Associate

Achievements
Won first place in the 2019 National Hackathon for a real-time chat application
serving 5000 concurrent users.
"""


def _items_by_field(items: tuple[ExtractedItem, ...], field: str) -> list[ExtractedItem]:
    return [item for item in items if item.field == field]


class TestTraceability:
    """The property every other guarantee in this module rests on."""

    def test_every_item_source_excerpt_is_verbatim_from_the_resume(self) -> None:
        proposal = extract(_SAMPLE_RESUME)
        assert proposal.items, "the sample resume should yield at least one item"
        for item in proposal.items:
            # Whitespace inside an excerpt is sometimes collapsed/joined across
            # lines, so compare against the resume with the same normalisation
            # rather than requiring an exact substring across newlines.
            collapsed_resume = " ".join(_SAMPLE_RESUME.split())
            collapsed_excerpt = " ".join(item.source_excerpt.split())
            assert collapsed_excerpt in collapsed_resume, (
                f"{item.field} excerpt not found verbatim in the resume: {item.source_excerpt!r}"
            )

    def test_an_empty_resume_yields_nothing(self) -> None:
        assert extract("") == extract("")
        proposal = extract("")
        assert proposal.items == ()
        assert proposal.unparsed_sections == ()

    def test_a_blank_resume_yields_nothing(self) -> None:
        proposal = extract("   \n\n   ")
        assert proposal.items == ()


class TestDeterminism:
    def test_the_same_text_produces_the_same_proposal_every_time(self) -> None:
        first = extract(_SAMPLE_RESUME)
        second = extract(_SAMPLE_RESUME)
        assert first == second


class TestSkillExtraction:
    def test_known_technologies_are_found_across_the_whole_document_not_just_a_skills_section(
        self,
    ) -> None:
        proposal = extract(_SAMPLE_RESUME)
        skill_names = {item.data["name"] for item in _items_by_field(proposal.items, "skill")}
        assert "python" in skill_names
        assert "fastapi" in skill_names
        assert "postgresql" in skill_names
        assert "docker" in skill_names
        assert "kubernetes" in skill_names

    def test_each_skill_is_reported_once_even_if_mentioned_many_times(self) -> None:
        text = "Skills\nPython, Python, Python\n\nExperience\nUsed Python heavily.\n"
        proposal = extract(text)
        skill_items = _items_by_field(proposal.items, "skill")
        python_items = [i for i in skill_items if i.data["name"] == "python"]
        assert len(python_items) == 1

    def test_skill_items_are_high_confidence_and_need_no_review(self) -> None:
        proposal = extract("Skills\nPython, Docker\n")
        for item in _items_by_field(proposal.items, "skill"):
            assert item.confidence == "high"
            assert item.needs_review is False


class TestExperienceExtraction:
    def test_two_experience_entries_are_both_found(self) -> None:
        proposal = extract(_SAMPLE_RESUME)
        experience_items = _items_by_field(proposal.items, "experience")
        assert len(experience_items) == 2

    def test_title_and_company_are_split_from_the_header_line(self) -> None:
        proposal = extract(_SAMPLE_RESUME)
        experience_items = _items_by_field(proposal.items, "experience")
        first = next(i for i in experience_items if "Acme" in str(i.data["company"]))
        assert first.data["title"] == "Senior Backend Engineer"
        assert first.data["company"] == "Acme Corp"

    def test_a_full_month_and_year_date_is_parsed(self) -> None:
        proposal = extract(_SAMPLE_RESUME)
        experience_items = _items_by_field(proposal.items, "experience")
        acme = next(i for i in experience_items if "Acme" in str(i.data["company"]))
        assert acme.data["start_date"] == "2021-01-01"
        assert acme.data["is_current"] is True
        assert acme.data["end_date"] is None

    def test_a_closed_date_range_sets_both_dates(self) -> None:
        proposal = extract(_SAMPLE_RESUME)
        experience_items = _items_by_field(proposal.items, "experience")
        globex = next(i for i in experience_items if "Globex" in str(i.data["company"]))
        assert globex.data["start_date"] == "2018-06-01"
        assert globex.data["end_date"] == "2020-12-01"
        assert globex.data["is_current"] is False

    def test_experience_items_always_need_review(self) -> None:
        """Title/company splitting is a guess; it is never presented as settled."""
        proposal = extract(_SAMPLE_RESUME)
        for item in _items_by_field(proposal.items, "experience"):
            assert item.needs_review is True

    def test_a_year_only_range_does_not_invent_a_month(self) -> None:
        text = "Experience\nFounder - Solo Startup\n2019 - 2021\nBuilt things alone.\n"
        proposal = extract(text)
        items = _items_by_field(proposal.items, "experience")
        assert len(items) == 1
        assert items[0].data["start_date"] is None
        assert items[0].data["end_date"] is None
        # The raw text is preserved so a human can still read it.
        assert "2019" in str(items[0].data["raw_dates"])
        assert items[0].confidence == "low"

    def test_an_experience_entry_with_no_date_range_is_not_fabricated(self) -> None:
        text = "Experience\nSomething that is not formatted like a job at all.\n"
        proposal = extract(text)
        assert _items_by_field(proposal.items, "experience") == []
        assert "experience" in proposal.unparsed_sections

    def test_skills_mentioned_in_a_bullet_are_attached_to_the_experience_entry(self) -> None:
        proposal = extract(_SAMPLE_RESUME)
        experience_items = _items_by_field(proposal.items, "experience")
        globex = next(i for i in experience_items if "Globex" in str(i.data["company"]))
        globex_skills = list(globex.data["skills_used"])  # type: ignore[call-overload]
        assert "django" in globex_skills
        assert "aws" in globex_skills
        assert "docker" in globex_skills


class TestEntriesWithoutBlankLineSeparators:
    """Some PDF text extractors (``pypdf`` among them) drop a line entirely when it
    had no visible characters, which silently deletes the blank lines a resume used
    to separate two jobs — this is not a hypothetical, it is what a real multi-job
    PDF resume's extracted text looks like. Two jobs packed back to back with *no*
    blank line between them, each still carrying its own date range, must still be
    recognised as two separate entries rather than merged into one, which would
    silently drop the second job and its skills entirely."""

    _NO_BLANK_LINE_EXPERIENCE = (
        "Experience\n"
        "Staff Engineer - Lighthouse Payments\n"
        "Feb 2022 - Present\n"
        "Designed a Python service processing payments at scale.\n"
        "Backend Engineer - Harbor Analytics\n"
        "Aug 2017 - Jan 2022\n"
        "Built internal REST APIs in Django and deployed them on AWS.\n"
    )

    def test_two_jobs_with_no_blank_line_between_them_are_both_found(self) -> None:
        proposal = extract(self._NO_BLANK_LINE_EXPERIENCE)
        experience_items = _items_by_field(proposal.items, "experience")
        assert len(experience_items) == 2

    def test_each_job_keeps_its_own_title_company_and_dates(self) -> None:
        proposal = extract(self._NO_BLANK_LINE_EXPERIENCE)
        experience_items = _items_by_field(proposal.items, "experience")
        first = next(i for i in experience_items if "Lighthouse" in str(i.data["company"]))
        second = next(i for i in experience_items if "Harbor" in str(i.data["company"]))

        assert first.data["title"] == "Staff Engineer"
        assert first.data["start_date"] == "2022-02-01"
        assert first.data["is_current"] is True

        assert second.data["title"] == "Backend Engineer"
        assert second.data["start_date"] == "2017-08-01"
        assert second.data["end_date"] == "2022-01-01"

    def test_each_jobs_description_does_not_leak_into_the_other(self) -> None:
        proposal = extract(self._NO_BLANK_LINE_EXPERIENCE)
        experience_items = _items_by_field(proposal.items, "experience")
        first = next(i for i in experience_items if "Lighthouse" in str(i.data["company"]))
        second = next(i for i in experience_items if "Harbor" in str(i.data["company"]))

        assert "payments" in str(first.data["description"])
        assert "Django" not in str(first.data["description"])
        assert "Django" in str(second.data["description"])
        assert "payments" not in str(second.data["description"])

    def test_a_single_entry_with_one_date_range_is_not_affected(self) -> None:
        """The anchor-splitting fix must not fire when there is nothing to split."""
        text = "Education\nBachelor of Science\nState University\n2015 - 2019\n"
        proposal = extract(text)
        education_items = _items_by_field(proposal.items, "education")
        assert len(education_items) == 1


class TestEducationExtraction:
    def test_an_education_entry_is_found(self) -> None:
        proposal = extract(_SAMPLE_RESUME)
        education_items = _items_by_field(proposal.items, "education")
        assert len(education_items) == 1

    def test_the_degree_level_is_recognised(self) -> None:
        proposal = extract(_SAMPLE_RESUME)
        education_items = _items_by_field(proposal.items, "education")
        assert education_items[0].data["level"] == "BACHELORS"

    def test_degree_on_its_own_line_above_the_institution_is_split_correctly(self) -> None:
        """The sample resume's shape: degree name, then institution, each on their
        own line — as opposed to both on one line separated by a dash."""
        proposal = extract(_SAMPLE_RESUME)
        education_items = _items_by_field(proposal.items, "education")
        assert education_items[0].data["degree"] == "Bachelor of Technology in Computer Science"
        assert education_items[0].data["institution"] == "Indian Institute of Technology"

    def test_degree_and_institution_combined_on_one_line_are_split_correctly(self) -> None:
        text = "Education\nState University - Bachelor of Science\n2015 - 2019\n"
        proposal = extract(text)
        education_items = _items_by_field(proposal.items, "education")
        assert len(education_items) == 1
        assert education_items[0].data["institution"] == "State University"
        assert education_items[0].data["degree"] == "Bachelor of Science"

    def test_an_unrecognised_degree_wording_needs_review_rather_than_a_default(self) -> None:
        text = (
            "Education\nSome Diploma-ish Thing That Matches Nothing\nState College\n2015 - 2017\n"
        )
        proposal = extract(text)
        items = _items_by_field(proposal.items, "education")
        assert len(items) == 1
        assert items[0].needs_review is True


class TestProjectExtraction:
    def test_a_project_entry_is_found_with_its_url(self) -> None:
        proposal = extract(_SAMPLE_RESUME)
        project_items = _items_by_field(proposal.items, "project")
        assert len(project_items) == 1
        project = project_items[0]
        assert project.data["name"] == "Redis Clone"
        assert project.data["repository_url"] == "https://github.com/janedoe/redis-clone"
        assert project.data["url"] is None

    def test_project_skills_used_is_populated_from_the_description(self) -> None:
        proposal = extract(_SAMPLE_RESUME)
        project = _items_by_field(proposal.items, "project")[0]
        # "Rust" is recognised vocabulary; the project description mentions it.
        assert "rust" in list(project.data["skills_used"])  # type: ignore[call-overload]


class TestCertificationExtraction:
    def test_a_certification_line_becomes_its_own_item(self) -> None:
        proposal = extract(_SAMPLE_RESUME)
        cert_items = _items_by_field(proposal.items, "certification")
        assert len(cert_items) == 1
        assert cert_items[0].data["name"] == "AWS Certified Solutions Architect - Associate"


class TestAchievementExtraction:
    def test_a_summary_or_achievement_paragraph_becomes_an_achievement_item(self) -> None:
        proposal = extract(_SAMPLE_RESUME)
        achievement_items = _items_by_field(proposal.items, "achievement")
        assert any("Hackathon" in str(item.data["text"]) for item in achievement_items)

    def test_a_very_short_fragment_is_not_kept_as_an_achievement(self) -> None:
        text = "Achievements\nOk\n"
        proposal = extract(text)
        assert _items_by_field(proposal.items, "achievement") == []


class TestNeverPopulatesSensitiveCategories:
    """A resume can state anything. The extractor's output vocabulary must have no
    slot that could carry a SensitiveCategory value, even when asked to."""

    _RESUME_WITH_SENSITIVE_CONTENT = """\
Jane Doe
Date of Birth: 1990-01-01
SSN: 123-45-6789
Passport Number: X1234567
Visa Status: Requires H1B sponsorship
Marital Status: Married
Veteran Status: Not a veteran
Disability Status: None
Desired Salary: $150,000
Previous Salary: $120,000

Experience
Backend Engineer - Acme Corp
Jan 2020 - Dec 2021
Wrote some code.
"""

    def test_every_item_field_is_from_the_allowed_vocabulary(self) -> None:
        proposal = extract(self._RESUME_WITH_SENSITIVE_CONTENT)
        allowed_fields = {
            "skill",
            "education",
            "project",
            "experience",
            "certification",
            "achievement",
        }
        for item in proposal.items:
            assert item.field in allowed_fields

    def test_no_structured_field_carries_the_sensitive_content(self) -> None:
        """ "achievement" is verbatim free text (the same risk class as the resume's
        own stored full text) and may legitimately quote anything the resume says.
        Every *other* field is structured, and none of them has a slot for this."""
        proposal = extract(self._RESUME_WITH_SENSITIVE_CONTENT)
        for item in proposal.items:
            if item.field == "achievement":
                continue
            serialised = str(item.data).upper()
            assert "123-45-6789" not in serialised
            assert "H1B" not in serialised
            assert "X1234567" not in serialised

    def test_the_sensitive_fields_simply_have_nowhere_to_land(self) -> None:
        """Every item's data dict only ever has the keys this module defines for its
        field type — there is no generic passthrough that could carry anything else."""
        proposal = extract(self._RESUME_WITH_SENSITIVE_CONTENT)
        allowed_keys_by_field = {
            "skill": {"name"},
            "education": {"institution", "degree", "level", "start_date", "end_date", "raw_dates"},
            "project": {"name", "description", "skills_used", "url", "repository_url"},
            "experience": {
                "title",
                "company",
                "start_date",
                "end_date",
                "is_current",
                "description",
                "skills_used",
                "raw_dates",
            },
            "certification": {"name", "issuer", "issued_on"},
            "achievement": {"text"},
        }
        for item in proposal.items:
            assert set(item.data.keys()) <= allowed_keys_by_field[item.field]
