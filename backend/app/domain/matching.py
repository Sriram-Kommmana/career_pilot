"""The deterministic matching engine.

Two stages, in this order:

1. **Hard filters** — the user's absolute constraints. A job that fails one is
   excluded outright and never scored, because a score would imply it is a candidate.
2. **Component scoring** — six weighted components, each in ``[0, 1]``, combined into
   a single number for ranking.

Three properties are load-bearing:

*No model is involved.* Identical inputs give an identical score, so the explanation
shown to the user is the actual computation, not a plausible story about it.

*A score is a ranking heuristic.* It orders a list. It is not a calibrated
probability of an interview or an offer, and :class:`MatchAssessment` carries
``score_kind`` to keep that visible all the way to the UI.

*"We couldn't assess this" is not "zero".* A component with nothing to go on returns
``None``, is excluded from the weighted sum, and the remaining weights are
renormalised. Scoring a job 0.0 for education because the posting never mentioned a
degree would be a lie about the job, not an assessment of the candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from app.domain.normalize import (
    EmploymentType,
    SalaryRange,
    SourceStatus,
    WorkArrangement,
    normalize_company_name,
    normalize_location,
    normalize_text,
)
from app.domain.provenance import EvidenceRef
from app.domain.skills import (
    KNOWN_TECHNOLOGIES,
    canonical_skill,
    drop_subsumed,
    expand_with_implied,
    extract_degree_requirement,
    extract_skill_mentions,
    extract_years_requirement,
    is_optional_requirement,
)

# ---------------------------------------------------------------------------
# Candidate and job inputs
# ---------------------------------------------------------------------------


class EducationLevel(StrEnum):
    UNKNOWN = "UNKNOWN"
    HIGH_SCHOOL = "HIGH_SCHOOL"
    DIPLOMA = "DIPLOMA"
    BACHELORS = "BACHELORS"
    MASTERS = "MASTERS"
    DOCTORATE = "DOCTORATE"


_EDUCATION_RANK: Final[dict[EducationLevel, int]] = {
    EducationLevel.UNKNOWN: -1,
    EducationLevel.HIGH_SCHOOL: 0,
    EducationLevel.DIPLOMA: 1,
    EducationLevel.BACHELORS: 2,
    EducationLevel.MASTERS: 3,
    EducationLevel.DOCTORATE: 4,
}

#: Seniority ladder read off a job title. Used to spot a mismatch worth warning about
#: — a first-year student applying to a Staff Engineer role, for instance.
_SENIORITY_MARKERS: Final[tuple[tuple[int, tuple[str, ...]], ...]] = (
    (0, ("intern", "internship", "trainee", "apprentice")),
    (1, ("junior", "entry level", "entry-level", "graduate", "associate", "fresher", "i")),
    (2, ("mid", "mid-level", "ii", "engineer ii")),
    (3, ("senior", "sr", "iii", "lead")),
    (4, ("staff", "principal", "architect", "distinguished", "head of", "director", "vp")),
)


@dataclass(frozen=True, slots=True)
class CandidateSkill:
    """A skill the user claims, and whether a project or job backs it up.

    ``has_evidence`` is what separates "listed on a profile" from "demonstrated". The
    evidence component of the score is built entirely from this flag, and the
    explanation cites ``evidence`` so the user can check the claim.
    """

    name: str
    has_evidence: bool = False
    evidence: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    years: float | None = None

    @property
    def canonical(self) -> str:
        return canonical_skill(self.name)


@dataclass(frozen=True, slots=True)
class ProfileEvidenceItem:
    """A confirmed, retrievable chunk of career evidence that is not shaped like a
    :class:`Project`/experience record — most often a resume excerpt that didn't
    decompose into a structured entry (e.g. a summary or achievements line).

    ``skills`` is computed once, outside the hot scoring path (see
    ``app.services.profile.build_candidate_profile``), by matching ``content``
    against the known technology vocabulary — the same mechanism a job requirement
    is matched against, so "mentioned in your evidence" and "mentioned in a
    requirement" mean the same thing.
    """

    content: str
    skills: frozenset[str] = field(default_factory=frozenset)
    locator: str | None = None
    evidence_ref: EvidenceRef | None = None


@dataclass(frozen=True, slots=True)
class CandidateProfile:
    """The user's side of the comparison.

    Unknowns are ``None``, never a default. ``total_experience_months=None`` means we
    have not been told; it must not be read as zero, because "no experience" and "we
    don't know" lead to different recommendations.
    """

    skills: tuple[CandidateSkill, ...] = field(default_factory=tuple)
    highest_education: EducationLevel = EducationLevel.UNKNOWN
    graduation_year: int | None = None
    total_experience_months: int | None = None

    target_roles: tuple[str, ...] = field(default_factory=tuple)
    preferred_locations: tuple[str, ...] = field(default_factory=tuple)
    open_to_remote: bool | None = None
    employment_preferences: tuple[EmploymentType, ...] = field(default_factory=tuple)

    excluded_companies: tuple[str, ...] = field(default_factory=tuple)
    excluded_roles: tuple[str, ...] = field(default_factory=tuple)
    excluded_locations: tuple[str, ...] = field(default_factory=tuple)

    minimum_salary: float | None = None
    salary_currency: str | None = None

    #: Confirmed free-text evidence (e.g. resume excerpts) that doesn't fit a
    #: Project/ExperienceRecord shape. Empty by default, so a profile with none
    #: behaves exactly as it did before this field existed.
    profile_evidence: tuple[ProfileEvidenceItem, ...] = field(default_factory=tuple)

    @property
    def skill_index(self) -> dict[str, CandidateSkill]:
        """Canonical skill name to the richest record for it.

        A skill listed twice, once with evidence, keeps the evidenced record.
        """
        index: dict[str, CandidateSkill] = {}
        for skill in self.skills:
            key = skill.canonical
            if not key:
                continue
            existing = index.get(key)
            if existing is None or (skill.has_evidence and not existing.has_evidence):
                index[key] = skill
        return index

    @property
    def canonical_skills(self) -> frozenset[str]:
        return frozenset(self.skill_index)

    @property
    def profile_evidence_skill_refs(self) -> dict[str, tuple[EvidenceRef, ...]]:
        """Canonical skill name to the free-text evidence refs that mention it."""
        result: dict[str, list[EvidenceRef]] = {}
        for item in self.profile_evidence:
            if item.evidence_ref is None:
                continue
            for skill in item.skills:
                result.setdefault(skill, []).append(item.evidence_ref)
        return {skill: tuple(refs) for skill, refs in result.items()}


@dataclass(frozen=True, slots=True)
class JobPosting:
    """The job's side of the comparison, as normalised by a connector."""

    title: str
    company_name: str
    location_raw: str | None = None
    work_arrangement: WorkArrangement = WorkArrangement.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    requirement_texts: tuple[str, ...] = field(default_factory=tuple)
    description: str | None = None
    salary: SalaryRange | None = None
    source_status: SourceStatus = SourceStatus.UNKNOWN


@dataclass(frozen=True, slots=True)
class MatchConfig:
    """Tunables. Defaults are deliberately permissive on the hard filters.

    A hard filter removes a job from the user's view entirely, so each one here is
    individually switchable and the aggressive ones default to off.
    """

    weights: dict[str, float] = field(
        default_factory=lambda: {
            "skill_overlap": 0.30,
            "evidence": 0.20,
            "role_relevance": 0.20,
            "education": 0.10,
            "location": 0.10,
            "preferences": 0.10,
        }
    )
    enforce_role_filter: bool = False
    enforce_location_filter: bool = False
    enforce_employment_type_filter: bool = True
    enforce_salary_filter: bool = False
    #: Treat a degree requirement above the candidate's level as a hard block rather
    #: than a flagged concern. Off by default: employers routinely list a degree they
    #: do not actually insist on.
    enforce_education_filter: bool = False
    role_relevance_threshold: float = 0.34

    def weight(self, component: str) -> float:
        return self.weights.get(component, 0.0)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class FilterCode(StrEnum):
    JOB_CLOSED = "JOB_CLOSED"
    EXCLUDED_COMPANY = "EXCLUDED_COMPANY"
    EXCLUDED_ROLE = "EXCLUDED_ROLE"
    EXCLUDED_LOCATION = "EXCLUDED_LOCATION"
    ROLE_MISMATCH = "ROLE_MISMATCH"
    LOCATION_MISMATCH = "LOCATION_MISMATCH"
    EMPLOYMENT_TYPE_MISMATCH = "EMPLOYMENT_TYPE_MISMATCH"
    SALARY_BELOW_MINIMUM = "SALARY_BELOW_MINIMUM"
    EDUCATION_BELOW_REQUIREMENT = "EDUCATION_BELOW_REQUIREMENT"


@dataclass(frozen=True, slots=True)
class FilterFailure:
    code: FilterCode
    message: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class HardFilterResult:
    passed: bool
    failures: tuple[FilterFailure, ...] = field(default_factory=tuple)


class RequirementStatus(StrEnum):
    MET = "MET"
    MISSING = "MISSING"
    #: The requirement was understood but we lack the information to judge it, e.g.
    #: "3+ years of Python" with no experience history on file.
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class RequirementAssessment:
    """One requirement line and what we concluded about it."""

    text: str
    status: RequirementStatus
    is_optional: bool
    matched_skills: tuple[str, ...] = field(default_factory=tuple)
    missing_skills: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    note: str | None = None

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence)


@dataclass(frozen=True, slots=True)
class ComponentScore:
    """One weighted component of the total.

    ``raw`` is ``None`` when the component could not be assessed at all. Such a
    component contributes nothing and its weight is redistributed; see
    :func:`combine_components`.
    """

    name: str
    raw: float | None
    weight: float
    rationale: str
    details: dict[str, object] = field(default_factory=dict)

    @property
    def assessed(self) -> bool:
        return self.raw is not None

    @property
    def weighted(self) -> float:
        return 0.0 if self.raw is None else self.raw * self.weight


@dataclass(frozen=True, slots=True)
class MatchExplanation:
    """The human-readable account of a score.

    Mirrors what the product promises to show: why it might fit, what is actually
    evidenced, what is missing or uncertain, eligibility concerns, and one next step.
    """

    why_it_may_fit: tuple[str, ...]
    evidenced_qualifications: tuple[str, ...]
    missing_requirements: tuple[str, ...]
    uncertain_requirements: tuple[str, ...]
    eligibility_concerns: tuple[str, ...]
    suggested_next_step: str


class ScoreKind(StrEnum):
    """What the number is. There is exactly one value, and it is not a probability."""

    RANKING_HEURISTIC = "RANKING_HEURISTIC"


@dataclass(frozen=True, slots=True)
class MatchAssessment:
    """The complete, storable result of assessing one job for one candidate."""

    score: float | None
    score_kind: ScoreKind
    hard_filter: HardFilterResult
    components: tuple[ComponentScore, ...]
    requirements: tuple[RequirementAssessment, ...]
    explanation: MatchExplanation
    evidence_refs: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    assessed_weight: float = 0.0
    engine_version: str = "1.0.0"

    @property
    def excluded(self) -> bool:
        return not self.hard_filter.passed

    @property
    def matched_requirements(self) -> tuple[RequirementAssessment, ...]:
        return tuple(r for r in self.requirements if r.status is RequirementStatus.MET)

    @property
    def missing_requirements(self) -> tuple[RequirementAssessment, ...]:
        return tuple(r for r in self.requirements if r.status is RequirementStatus.MISSING)

    @property
    def uncertain_requirements(self) -> tuple[RequirementAssessment, ...]:
        return tuple(r for r in self.requirements if r.status is RequirementStatus.UNCERTAIN)

    def component(self, name: str) -> ComponentScore | None:
        return next((c for c in self.components if c.name == name), None)


# ---------------------------------------------------------------------------
# Stage 1: hard filters
# ---------------------------------------------------------------------------


def apply_hard_filters(
    *,
    profile: CandidateProfile,
    job: JobPosting,
    config: MatchConfig | None = None,
) -> HardFilterResult:
    """Apply the user's absolute constraints.

    Exclusions the user typed are always enforced — they asked for that. The
    inferential filters (role, location, salary, education) are off by default,
    because a false exclusion is invisible to the user and therefore worse than a
    badly ranked job they can see and ignore.
    """
    cfg = config or MatchConfig()
    failures: list[FilterFailure] = []

    if job.source_status is SourceStatus.CLOSED:
        failures.append(
            FilterFailure(
                code=FilterCode.JOB_CLOSED,
                message="The source reports this posting as closed.",
            )
        )

    company_key = normalize_company_name(job.company_name)
    for excluded in profile.excluded_companies:
        if company_key and company_key == normalize_company_name(excluded):
            failures.append(
                FilterFailure(
                    code=FilterCode.EXCLUDED_COMPANY,
                    message=f"You excluded {job.company_name}.",
                    detail=excluded,
                )
            )
            break

    title_text = normalize_text(job.title)
    for excluded in profile.excluded_roles:
        needle = normalize_text(excluded)
        if needle and needle in title_text:
            failures.append(
                FilterFailure(
                    code=FilterCode.EXCLUDED_ROLE,
                    message=f'You excluded roles matching "{excluded}".',
                    detail=excluded,
                )
            )
            break

    location_key = normalize_location(job.location_raw)
    for excluded in profile.excluded_locations:
        needle = normalize_location(excluded)
        if needle and location_key and needle in location_key:
            failures.append(
                FilterFailure(
                    code=FilterCode.EXCLUDED_LOCATION,
                    message=f'You excluded locations matching "{excluded}".',
                    detail=excluded,
                )
            )
            break

    if (
        cfg.enforce_employment_type_filter
        and profile.employment_preferences
        and job.employment_type is not EmploymentType.UNKNOWN
        and job.employment_type not in profile.employment_preferences
    ):
        wanted = ", ".join(t.value for t in profile.employment_preferences)
        failures.append(
            FilterFailure(
                code=FilterCode.EMPLOYMENT_TYPE_MISMATCH,
                message=f"This is a {job.employment_type.value} role; you want {wanted}.",
                detail=job.employment_type.value,
            )
        )

    if cfg.enforce_role_filter and profile.target_roles:
        relevance = _role_relevance(profile=profile, job=job)
        if relevance is not None and relevance < cfg.role_relevance_threshold:
            failures.append(
                FilterFailure(
                    code=FilterCode.ROLE_MISMATCH,
                    message="The title does not resemble any of your target roles.",
                    detail=job.title,
                )
            )

    if cfg.enforce_location_filter:
        location_ok = _location_acceptable(profile=profile, job=job)
        if location_ok is False:
            failures.append(
                FilterFailure(
                    code=FilterCode.LOCATION_MISMATCH,
                    message="The location is outside your preferences and the role is not remote.",
                    detail=job.location_raw,
                )
            )

    if (
        cfg.enforce_salary_filter
        and profile.minimum_salary is not None
        and job.salary is not None
        and not job.salary.is_empty
    ):
        # Compare against the top of the advertised band: a job offering 80-120k does
        # meet a 100k minimum, and excluding it on the band's floor would be wrong.
        ceiling = job.salary.maximum if job.salary.maximum is not None else job.salary.minimum
        currencies_comparable = (
            profile.salary_currency is None
            or job.salary.currency is None
            or profile.salary_currency.upper() == job.salary.currency.upper()
        )
        if currencies_comparable and ceiling is not None and ceiling < profile.minimum_salary:
            failures.append(
                FilterFailure(
                    code=FilterCode.SALARY_BELOW_MINIMUM,
                    message="The advertised salary is below your minimum.",
                    detail=f"{ceiling} < {profile.minimum_salary}",
                )
            )

    if cfg.enforce_education_filter:
        required = _required_education(job)
        if (
            required is not None
            and profile.highest_education is not EducationLevel.UNKNOWN
            and _EDUCATION_RANK[profile.highest_education] < _EDUCATION_RANK[required]
        ):
            failures.append(
                FilterFailure(
                    code=FilterCode.EDUCATION_BELOW_REQUIREMENT,
                    message=f"The posting asks for a {required.value.lower()} qualification.",
                    detail=required.value,
                )
            )

    return HardFilterResult(passed=not failures, failures=tuple(failures))


# ---------------------------------------------------------------------------
# Stage 2: requirement assessment
# ---------------------------------------------------------------------------


def assess_requirements(
    *,
    profile: CandidateProfile,
    job: JobPosting,
) -> tuple[RequirementAssessment, ...]:
    """Judge each requirement line against the candidate.

    A line resolves to:

    * ``MET`` — every recognised technology it names is held, and any stated
      experience threshold is satisfied by the recorded history.
    * ``MISSING`` — there is positive evidence of a gap: a recognised technology the
      candidate does not have, or a years threshold the recorded history does not meet.
    * ``UNCERTAIN`` — it was parsed but cannot be judged. Either no recognised
      technology appears in it (so the line is prose rather than a checkable
      requirement), or it states a years threshold and there is no work history on file.

    A gap is only ever claimed from the recognised-technology vocabulary in
    ``domain.skills``. An earlier version treated any short phrase absent from the
    user's skill list as a missing skill, which reported "designing" and "consuming
    rest apis" as gaps. A confidently wrong gap is worse than no answer, because the
    explanation is the part the user is asked to trust.

    ``UNCERTAIN`` is reported to the user, never quietly counted as a pass.
    """
    # The user's own skills, plus what those skills imply: someone who has used
    # PostgreSQL has written SQL. The implications are shallow and one-directional;
    # see domain.skills.IMPLIED_SKILLS.
    vocabulary = expand_with_implied(profile.canonical_skills)
    index = profile.skill_index
    resume_evidence_refs = profile.profile_evidence_skill_refs
    assessments: list[RequirementAssessment] = []

    for text in job.requirement_texts:
        if not text or not text.strip():
            continue
        optional = is_optional_requirement(text)

        mentioned = extract_skill_mentions(text, vocabulary=vocabulary)
        recognised = extract_skill_mentions(text, vocabulary=KNOWN_TECHNOLOGIES)
        # A shorter name that is part of one the candidate holds is not a gap: "REST
        # APIs" matches both `rest api` and `rest`, and someone with `rest api` has
        # not failed a `rest` requirement.
        missing = drop_subsumed(recognised - vocabulary, covered=mentioned | vocabulary)

        years = extract_years_requirement(text)
        note: str | None = None
        status: RequirementStatus

        if years is not None and profile.total_experience_months is None:
            status = RequirementStatus.UNCERTAIN
            note = (
                f"Asks for {years.raw}. Your profile has no work history recorded, so "
                "this cannot be checked."
            )
        elif years is not None and profile.total_experience_months is not None:
            held_years = profile.total_experience_months / 12.0
            if held_years + 1e-9 < years.minimum_years:
                status = RequirementStatus.MISSING
                note = f"Asks for {years.raw}; your recorded history totals {held_years:.1f} years."
            elif missing:
                status = RequirementStatus.MISSING
                note = "Not on your profile: " + ", ".join(sorted(missing)) + "."
            elif mentioned:
                status = RequirementStatus.MET
            else:
                status = RequirementStatus.MET
                note = f"Experience threshold {years.raw} is satisfied."
        elif mentioned and not missing:
            status = RequirementStatus.MET
        elif mentioned and missing:
            status = RequirementStatus.MISSING
            note = "Partly met. Not on your profile: " + ", ".join(sorted(missing)) + "."
        elif missing:
            status = RequirementStatus.MISSING
            note = "Not on your profile: " + ", ".join(sorted(missing)) + "."
        else:
            status = RequirementStatus.UNCERTAIN
            note = "No recognised technology in this line; read it yourself."

        evidence: list[EvidenceRef] = []
        for skill_name in sorted(mentioned):
            held = index.get(skill_name)
            if held is not None and held.has_evidence:
                evidence.extend(held.evidence)
            evidence.extend(resume_evidence_refs.get(skill_name, ()))

        assessments.append(
            RequirementAssessment(
                text=text.strip(),
                status=status,
                is_optional=optional,
                matched_skills=tuple(sorted(mentioned)),
                missing_skills=tuple(sorted(missing)),
                evidence=tuple(evidence),
                note=note,
            )
        )

    return tuple(assessments)


# ---------------------------------------------------------------------------
# Stage 2: component scores
# ---------------------------------------------------------------------------


def _required_education(job: JobPosting) -> EducationLevel | None:
    """The minimum degree level the posting names, if any."""
    sources = [*job.requirement_texts]
    if job.description:
        sources.append(job.description)
    for text in sources:
        level = extract_degree_requirement(text)
        if level is not None:
            return EducationLevel(level)
    return None


def _seniority_of(title: str) -> int | None:
    """Position on the seniority ladder, or ``None`` if the title does not say."""
    text = normalize_text(title)
    best: int | None = None
    for level, markers in _SENIORITY_MARKERS:
        for marker in markers:
            # Single-letter roman numerals would match far too much; require spacing.
            needle = f" {marker} "
            if needle in f" {text} ":
                best = level if best is None else max(best, level)
    return best


def _role_relevance(*, profile: CandidateProfile, job: JobPosting) -> float | None:
    """Token overlap between the job title and the user's target roles.

    ``None`` when the user has not named any target role, which is common early on
    and must not be scored as a mismatch.
    """
    if not profile.target_roles:
        return None
    title_tokens = {
        token for token in normalize_text(job.title).replace("/", " ").split() if len(token) > 1
    }
    if not title_tokens:
        return 0.0

    best = 0.0
    for target in profile.target_roles:
        target_tokens = {
            token for token in normalize_text(target).replace("/", " ").split() if len(token) > 1
        }
        if not target_tokens:
            continue
        overlap = len(title_tokens & target_tokens) / len(target_tokens)
        best = max(best, min(1.0, overlap))
    return best


def _location_acceptable(*, profile: CandidateProfile, job: JobPosting) -> bool | None:
    """Whether the job's location fits the user's preferences.

    ``None`` means undecidable: no stated preference, or an unknown job location.
    """
    if job.work_arrangement is WorkArrangement.REMOTE:
        # Only an explicit "no" rules a remote role out. `None` means unstated, and an
        # unstated preference must not exclude anything.
        return profile.open_to_remote is not False

    if not profile.preferred_locations:
        return None

    job_key = normalize_location(job.location_raw)
    if not job_key:
        return None

    for preferred in profile.preferred_locations:
        pref_key = normalize_location(preferred)
        if not pref_key:
            continue
        if pref_key in job_key or job_key in pref_key:
            return True
    return False


def _score_skill_overlap(
    requirements: tuple[RequirementAssessment, ...],
) -> ComponentScore:
    """Share of the posting's *required* lines that the candidate meets.

    Optional ("nice to have") lines are excluded from the denominator but counted as
    a small bonus when met, capped so they cannot carry a weak match.
    """
    required = [r for r in requirements if not r.is_optional]
    judgeable = [r for r in required if r.status is not RequirementStatus.UNCERTAIN]
    optional_met = [r for r in requirements if r.is_optional and r.status is RequirementStatus.MET]

    if not judgeable:
        return ComponentScore(
            name="skill_overlap",
            raw=None,
            weight=0.0,
            rationale=(
                "The posting did not list requirements we could parse, so skill "
                "overlap was not assessed."
            ),
            details={
                "required_count": len(required),
                "uncertain_count": len(required) - len(judgeable),
            },
        )

    met = [r for r in judgeable if r.status is RequirementStatus.MET]
    base = len(met) / len(judgeable)
    bonus = min(0.10, 0.05 * len(optional_met))
    raw = min(1.0, base + bonus)

    return ComponentScore(
        name="skill_overlap",
        raw=raw,
        weight=0.0,
        rationale=(
            f"{len(met)} of {len(judgeable)} required items matched"
            + (f", plus {len(optional_met)} preferred item(s)." if optional_met else ".")
        ),
        details={
            "met": len(met),
            "judgeable_required": len(judgeable),
            "optional_met": len(optional_met),
            "uncertain": len(required) - len(judgeable),
        },
    )


def _score_evidence(
    requirements: tuple[RequirementAssessment, ...],
    *,
    profile: CandidateProfile,
) -> ComponentScore:
    """How much of what matched is backed by a project or a job, not just claimed.

    This is the component that keeps the engine honest. Retrieval similarity and a
    skills checklist both say "Python"; only evidence says the user has written any.
    """
    met = [r for r in requirements if r.status is RequirementStatus.MET]
    if not met:
        return ComponentScore(
            name="evidence",
            raw=None,
            weight=0.0,
            rationale="Nothing matched, so there was nothing to evidence.",
        )

    index = profile.skill_index
    matched_skills = {skill for r in met for skill in r.matched_skills}
    if not matched_skills:
        return ComponentScore(
            name="evidence",
            raw=None,
            weight=0.0,
            rationale=("Requirements were met on non-skill grounds, so evidence was not assessed."),
        )

    resume_evidenced_skills = frozenset(profile.profile_evidence_skill_refs)
    skill_record_evidenced = {
        s for s in matched_skills if index.get(s) is not None and index[s].has_evidence
    }
    resume_only = {
        s
        for s in matched_skills
        if s in resume_evidenced_skills and s not in skill_record_evidenced
    }
    evidenced = skill_record_evidenced | resume_only
    raw = len(evidenced) / len(matched_skills)
    return ComponentScore(
        name="evidence",
        raw=raw,
        weight=0.0,
        rationale=(
            f"{len(evidenced)} of {len(matched_skills)} matched skills are backed by a "
            "project, role, or resume excerpt on your profile."
        ),
        details={
            "evidenced_skills": sorted(evidenced),
            "claimed_only_skills": sorted(matched_skills - evidenced),
            "resume_evidenced_skills": sorted(resume_only),
        },
    )


def _score_role_relevance(*, profile: CandidateProfile, job: JobPosting) -> ComponentScore:
    relevance = _role_relevance(profile=profile, job=job)
    if relevance is None:
        return ComponentScore(
            name="role_relevance",
            raw=None,
            weight=0.0,
            rationale="You have not set any target roles, so title relevance was not assessed.",
        )
    return ComponentScore(
        name="role_relevance",
        raw=relevance,
        weight=0.0,
        rationale=(f'"{job.title}" overlaps {relevance:.0%} with your closest target role.'),
        details={"target_roles": list(profile.target_roles)},
    )


def _score_education(*, profile: CandidateProfile, job: JobPosting) -> ComponentScore:
    required = _required_education(job)
    if required is None:
        return ComponentScore(
            name="education",
            raw=None,
            weight=0.0,
            rationale="The posting does not state an education requirement.",
        )
    if profile.highest_education is EducationLevel.UNKNOWN:
        return ComponentScore(
            name="education",
            raw=None,
            weight=0.0,
            rationale=(
                f"The posting asks for a {required.value.lower()} qualification, but your "
                "profile does not record your education yet."
            ),
            details={"required": required.value},
        )

    held_rank = _EDUCATION_RANK[profile.highest_education]
    required_rank = _EDUCATION_RANK[required]
    if held_rank >= required_rank:
        raw = 1.0
        rationale = f"Your {profile.highest_education.value.lower()} meets the stated requirement."
    else:
        # One level short scores 0.5 rather than 0: employers frequently accept it.
        gap = required_rank - held_rank
        raw = max(0.0, 1.0 - 0.5 * gap)
        rationale = (
            f"The posting asks for a {required.value.lower()} qualification; your profile "
            f"records {profile.highest_education.value.lower()}."
        )
    return ComponentScore(
        name="education",
        raw=raw,
        weight=0.0,
        rationale=rationale,
        details={"required": required.value, "held": profile.highest_education.value},
    )


def _score_location(*, profile: CandidateProfile, job: JobPosting) -> ComponentScore:
    acceptable = _location_acceptable(profile=profile, job=job)
    if acceptable is None:
        return ComponentScore(
            name="location",
            raw=None,
            weight=0.0,
            rationale=(
                "Location preference was not assessed: either you have not set one or "
                "the posting does not say where the role is."
            ),
        )
    if acceptable:
        if job.work_arrangement is WorkArrangement.REMOTE:
            rationale = "The role is remote."
        else:
            rationale = f"{job.location_raw} is in your preferred locations."
        return ComponentScore(name="location", raw=1.0, weight=0.0, rationale=rationale)

    # Hybrid in a non-preferred city is still partly workable; fully onsite is not.
    raw = 0.4 if job.work_arrangement is WorkArrangement.HYBRID else 0.0
    return ComponentScore(
        name="location",
        raw=raw,
        weight=0.0,
        rationale=(f"{job.location_raw or 'The location'} is outside your preferred locations."),
        details={"preferred": list(profile.preferred_locations)},
    )


def _score_preferences(*, profile: CandidateProfile, job: JobPosting) -> ComponentScore:
    """Employment type, work arrangement, and salary against stated preferences."""
    checks: list[tuple[str, float]] = []
    notes: list[str] = []

    if profile.employment_preferences and job.employment_type is not EmploymentType.UNKNOWN:
        hit = job.employment_type in profile.employment_preferences
        checks.append(("employment_type", 1.0 if hit else 0.0))
        notes.append(
            f"Employment type {job.employment_type.value} "
            f"{'matches' if hit else 'does not match'} your preference."
        )

    if profile.open_to_remote is not None and job.work_arrangement is not WorkArrangement.UNKNOWN:
        if profile.open_to_remote:
            hit = job.work_arrangement in (WorkArrangement.REMOTE, WorkArrangement.HYBRID)
        else:
            hit = job.work_arrangement in (WorkArrangement.ONSITE, WorkArrangement.HYBRID)
        checks.append(("work_arrangement", 1.0 if hit else 0.3))
        notes.append(f"Work arrangement is {job.work_arrangement.value}.")

    if profile.minimum_salary is not None and job.salary is not None and not job.salary.is_empty:
        ceiling = job.salary.maximum if job.salary.maximum is not None else job.salary.minimum
        comparable = (
            profile.salary_currency is None
            or job.salary.currency is None
            or profile.salary_currency.upper() == job.salary.currency.upper()
        )
        if comparable and ceiling is not None:
            hit = ceiling >= profile.minimum_salary
            checks.append(("salary", 1.0 if hit else 0.0))
            notes.append(f"Advertised pay {'meets' if hit else 'is below'} your stated minimum.")
        else:
            notes.append("Advertised pay is in a different currency and was not compared.")

    if not checks:
        return ComponentScore(
            name="preferences",
            raw=None,
            weight=0.0,
            rationale=("No stated preference could be compared against this posting's data."),
        )

    raw = sum(value for _, value in checks) / len(checks)
    return ComponentScore(
        name="preferences",
        raw=raw,
        weight=0.0,
        rationale=" ".join(notes),
        details={"checks": dict(checks)},
    )


def combine_components(
    components: tuple[ComponentScore, ...],
    *,
    weights: dict[str, float],
) -> tuple[float | None, float, tuple[ComponentScore, ...]]:
    """Weight and combine the components.

    Returns ``(score, assessed_weight, components_with_weights)``.

    Components that could not be assessed are excluded and the remaining weights are
    renormalised, so a job whose posting is thin is not penalised for the gaps in its
    own description. If nothing at all could be assessed the score is ``None``, which
    the API renders as "not enough information" rather than as a zero.
    """
    stamped = tuple(
        ComponentScore(
            name=c.name,
            raw=c.raw,
            weight=weights.get(c.name, 0.0),
            rationale=c.rationale,
            details=c.details,
        )
        for c in components
    )
    assessed = [c for c in stamped if c.assessed and c.weight > 0]
    assessed_weight = sum(c.weight for c in assessed)
    if assessed_weight <= 0:
        return None, 0.0, stamped
    total = sum(c.weighted for c in assessed) / assessed_weight
    # Clamp against float drift so a stored score is always a valid [0,1] value.
    return min(1.0, max(0.0, total)), assessed_weight, stamped


# ---------------------------------------------------------------------------
# Explanation
# ---------------------------------------------------------------------------


def build_explanation(
    *,
    profile: CandidateProfile,
    job: JobPosting,
    hard_filter: HardFilterResult,
    components: tuple[ComponentScore, ...],
    requirements: tuple[RequirementAssessment, ...],
    score: float | None,
) -> MatchExplanation:
    """Turn the computation into the five things the user is promised."""
    if not hard_filter.passed:
        return MatchExplanation(
            why_it_may_fit=(),
            evidenced_qualifications=(),
            missing_requirements=(),
            uncertain_requirements=(),
            eligibility_concerns=tuple(f.message for f in hard_filter.failures),
            suggested_next_step=(
                "This job was filtered out by your own constraints. Relax the relevant "
                "filter in Settings if you want to see roles like it."
            ),
        )

    why: list[str] = []
    for component in components:
        if component.assessed and component.raw is not None and component.raw >= 0.6:
            why.append(component.rationale)
    if not why:
        why.append(
            "Nothing stood out strongly. Read the posting yourself before spending time on it."
        )

    evidenced: list[str] = []
    index = profile.skill_index
    resume_evidence_refs = profile.profile_evidence_skill_refs
    seen: set[str] = set()
    for requirement in requirements:
        if requirement.status is not RequirementStatus.MET:
            continue
        for skill in requirement.matched_skills:
            if skill in seen:
                continue
            held = index.get(skill)
            refs_list = list(held.evidence) if held is not None and held.has_evidence else []
            refs_list.extend(resume_evidence_refs.get(skill, ()))
            if not refs_list:
                continue
            seen.add(skill)
            name = held.name if held is not None else skill
            refs = ", ".join(ref.describe() for ref in refs_list)
            evidenced.append(f"{name} — evidenced by {refs}")

    missing = [
        r.note or f"Not met: {r.text}"
        for r in requirements
        if r.status is RequirementStatus.MISSING and not r.is_optional
    ]
    uncertain = [
        r.note or f"Could not assess: {r.text}"
        for r in requirements
        if r.status is RequirementStatus.UNCERTAIN
    ]

    concerns: list[str] = []
    education = next((c for c in components if c.name == "education"), None)
    if education is not None and education.raw is not None and education.raw < 1.0:
        concerns.append(education.rationale)
    if education is not None and not education.assessed and "asks for" in education.rationale:
        concerns.append(education.rationale)

    seniority = _seniority_of(job.title)
    # Senior and above (level 3+) with under five years recorded is worth saying out
    # loud; the user can still decide to apply.
    if seniority is not None and seniority >= 3 and (profile.total_experience_months or 0) < 60:
        concerns.append(
            f'"{job.title}" reads as a senior-level title; your recorded experience may '
            "be below what they expect."
        )
    if job.employment_type is EmploymentType.UNKNOWN:
        concerns.append(
            "The posting does not state whether this is an internship or a full-time role."
        )

    # The next step is deliberately concrete, and never "apply now".
    if score is None:
        next_step = (
            "There was too little information to rank this job. Open the posting and "
            "judge it yourself."
        )
    elif missing:
        next_step = (
            "Close the gap or decide to apply anyway: "
            f"{missing[0]} Prepare the application to see the exact questions you'd face."
        )
    elif uncertain:
        next_step = (
            "Fill in the parts of your profile this posting asks about, then re-run the "
            "analysis for a sharper picture."
        )
    elif not evidenced:
        next_step = (
            "Your matching skills are listed but not yet evidenced. Add a project or "
            "role that demonstrates them, then prepare the application."
        )
    else:
        next_step = (
            "Prepare the application and review the drafted answers before anything is sent."
        )

    return MatchExplanation(
        why_it_may_fit=tuple(why),
        evidenced_qualifications=tuple(evidenced),
        missing_requirements=tuple(missing),
        uncertain_requirements=tuple(uncertain),
        eligibility_concerns=tuple(dict.fromkeys(concerns)),
        suggested_next_step=next_step,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

ENGINE_VERSION: Final = "1.0.0"


def assess_match(
    *,
    profile: CandidateProfile,
    job: JobPosting,
    config: MatchConfig | None = None,
) -> MatchAssessment:
    """Assess one job for one candidate.

    Pure: same inputs, same output, no I/O, no clock, no model. The returned
    assessment is complete enough to persist and to re-render the explanation later
    without recomputation.
    """
    cfg = config or MatchConfig()

    hard_filter = apply_hard_filters(profile=profile, job=job, config=cfg)
    if not hard_filter.passed:
        # A filtered-out job is not scored. A number next to it would suggest it is a
        # candidate, and it is not.
        return MatchAssessment(
            score=None,
            score_kind=ScoreKind.RANKING_HEURISTIC,
            hard_filter=hard_filter,
            components=(),
            requirements=(),
            explanation=build_explanation(
                profile=profile,
                job=job,
                hard_filter=hard_filter,
                components=(),
                requirements=(),
                score=None,
            ),
            engine_version=ENGINE_VERSION,
        )

    requirements = assess_requirements(profile=profile, job=job)
    raw_components = (
        _score_skill_overlap(requirements),
        _score_evidence(requirements, profile=profile),
        _score_role_relevance(profile=profile, job=job),
        _score_education(profile=profile, job=job),
        _score_location(profile=profile, job=job),
        _score_preferences(profile=profile, job=job),
    )
    score, assessed_weight, components = combine_components(raw_components, weights=cfg.weights)

    evidence_refs = tuple(dict.fromkeys(ref for r in requirements for ref in r.evidence))

    return MatchAssessment(
        score=score,
        score_kind=ScoreKind.RANKING_HEURISTIC,
        hard_filter=hard_filter,
        components=components,
        requirements=requirements,
        explanation=build_explanation(
            profile=profile,
            job=job,
            hard_filter=hard_filter,
            components=components,
            requirements=requirements,
            score=score,
        ),
        evidence_refs=evidence_refs,
        assessed_weight=assessed_weight,
        engine_version=ENGINE_VERSION,
    )
