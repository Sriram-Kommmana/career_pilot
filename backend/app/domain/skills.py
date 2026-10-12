"""Skill canonicalisation and requirement parsing.

Matching a candidate's skills against a job's requirements fails in boring ways
unless "JS", "Javascript" and "ECMAScript" are recognised as one thing. This module
holds that mapping, plus the extraction of skill mentions and experience thresholds
from requirement text.

Everything here is a lookup table and a regular expression. No model is involved, so
the result is identical on every run and can be explained to a user line by line.
The table is open-world: an unrecognised token is kept as its own canonical skill
rather than discarded, so a niche technology still matches itself.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from app.domain.normalize import fold_dashes, normalize_text

# ---------------------------------------------------------------------------
# Aliases. Key is any spelling we might see; value is the canonical form.
# Deliberately modest in size: a wrong alias produces a confidently wrong match,
# which is worse than no match at all.
# ---------------------------------------------------------------------------
_ALIASES: Final[dict[str, str]] = {
    # languages
    "js": "javascript",
    "ecmascript": "javascript",
    "node": "node.js",
    "nodejs": "node.js",
    "node js": "node.js",
    "ts": "typescript",
    "py": "python",
    "python3": "python",
    "golang": "go",
    "c sharp": "c#",
    "csharp": "c#",
    "dotnet": ".net",
    "asp.net": ".net",
    "cpp": "c++",
    "c plus plus": "c++",
    "objective c": "objective-c",
    # frontend
    "reactjs": "react",
    "react.js": "react",
    "react js": "react",
    "vuejs": "vue",
    "vue.js": "vue",
    "angularjs": "angular",
    "nextjs": "next.js",
    "next js": "next.js",
    "tailwind": "tailwind css",
    "tailwindcss": "tailwind css",
    "html5": "html",
    "css3": "css",
    "scss": "sass",
    # backend / frameworks
    "fast api": "fastapi",
    "django rest framework": "django",
    "drf": "django",
    "spring boot": "spring",
    "rails": "ruby on rails",
    "express": "express.js",
    "expressjs": "express.js",
    # data
    "postgres": "postgresql",
    "psql": "postgresql",
    "pg": "postgresql",
    "mysql server": "mysql",
    "ms sql": "sql server",
    "mssql": "sql server",
    "mongo": "mongodb",
    "elastic": "elasticsearch",
    "elastic search": "elasticsearch",
    "dynamo": "dynamodb",
    "bigquery": "google bigquery",
    "pandas dataframe": "pandas",
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "tf": "tensorflow",
    "pytorch lightning": "pytorch",
    # infra
    "k8s": "kubernetes",
    "kube": "kubernetes",
    "aws cloud": "aws",
    "amazon web services": "aws",
    "gcp": "google cloud",
    "google cloud platform": "google cloud",
    "ms azure": "azure",
    "microsoft azure": "azure",
    "ci cd": "ci/cd",
    "cicd": "ci/cd",
    "continuous integration": "ci/cd",
    "github action": "github actions",
    "iac": "infrastructure as code",
    "tf cloud": "terraform",
    # practices
    "oop": "object-oriented programming",
    "object oriented programming": "object-oriented programming",
    "dsa": "data structures and algorithms",
    "data structures": "data structures and algorithms",
    "algorithms": "data structures and algorithms",
    "unit testing": "testing",
    "test automation": "testing",
    "restful api": "rest api",
    "rest apis": "rest api",
    "restful apis": "rest api",
    "api development": "rest api",
    "ml": "machine learning",
    "dl": "deep learning",
    "nlp": "natural language processing",
    "llm": "large language models",
    "llms": "large language models",
    "genai": "generative ai",
    "ux": "user experience",
    "ui": "user interface",
}

#: Words that appear in requirement text but are not skills. Without this, every
#: requirement "matches" on filler.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    """
    a an and or the of in on for with to from at by as is are be being been have has
    had do does did will would should could can may might must strong solid excellent
    good great deep proven demonstrated hands experience experienced knowledge
    understanding familiarity proficiency proficient expertise skills skill ability
    able work working years year plus minimum least using use used utilize equivalent
    related similar such like e.g eg etc including include includes preferred
    preferably required requirement requirements must-have nice nice-to-have bonus
    plus-point advantage you your we our they their this that these those it its
    role position team teams candidate candidates applicant applicants ideal ideally
    degree field relevant other others any all more most very highly well about
    across within also both either neither not no yes if then than when while who
    whom which what where how why
    """.split()  # noqa: SIM905 - a prose block is far more readable than 140 literals
)

_TOKEN_SPLIT_RE: Final = re.compile(r"[^a-z0-9+#./\- ]+")
_CONNECTOR_RE: Final = re.compile(r"\s*(?:,|/|\bor\b|\band\b|;|\||&)\s*")

# "3+ years", "at least 2 years", "2-4 years", "minimum of 5 years"
_YEARS_RE: Final = re.compile(
    r"""
    (?:
        (?P<lo>\d{1,2}) \s* (?:\+|plus)? \s*
        (?:\s*(?:-|to)\s*(?P<hi>\d{1,2}))? \s*
        (?:\+)? \s* (?:year|yr)s?
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# "Bachelor's degree", "B.Tech", "Master's", "PhD"
_DEGREE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("DOCTORATE", re.compile(r"\b(?:ph\.?\s?d|doctoral|doctorate)\b", re.IGNORECASE)),
    (
        "MASTERS",
        re.compile(
            r"\b(?:master'?s?|m\.?\s?s\.?c?|m\.?\s?tech|m\.?\s?e\.?|mba|post[- ]?graduate)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "BACHELORS",
        re.compile(
            r"\b(?:bachelor'?s?|b\.?\s?s\.?c?|b\.?\s?tech|b\.?\s?e\.?|b\.?\s?a\.?|undergraduate)\b",
            re.IGNORECASE,
        ),
    ),
    ("DIPLOMA", re.compile(r"\b(?:diploma|associate'?s?\s+degree)\b", re.IGNORECASE)),
)

#: Requirement wording that marks something as optional rather than mandatory.
_OPTIONAL_MARKERS: Final[tuple[str, ...]] = (
    "nice to have",
    "nice-to-have",
    "preferred",
    "preferably",
    "a plus",
    "plus point",
    "bonus",
    "desirable",
    "would be great",
    "ideally",
    "optional",
    "not required",
)


def canonical_skill(raw: str) -> str:
    """Canonical form of a single skill name.

    Unknown skills are returned normalised rather than dropped, so a technology
    missing from the alias table still matches an identical mention elsewhere.
    """
    text = normalize_text(raw).strip(" .,;:")
    if not text:
        return ""
    return _ALIASES.get(text, text)


def canonical_skill_set(raws: Iterable[object] | str | None) -> frozenset[str]:
    """Canonicalise an iterable of skill names, dropping blanks and stopwords."""
    if raws is None:
        return frozenset()
    items: Iterable[object] = [raws] if isinstance(raws, str) else raws
    result: set[str] = set()
    for raw in items:
        skill = canonical_skill(str(raw))
        if skill and skill not in _STOPWORDS:
            result.add(skill)
    return frozenset(result)


def extract_skill_mentions(text: str | None, *, vocabulary: frozenset[str]) -> frozenset[str]:
    """Find which of ``vocabulary`` are mentioned in ``text``.

    Driven by the vocabulary rather than by guessing what looks like a technology.
    That keeps false positives out: a requirement only "mentions React" if React is
    something we are actually looking for.

    Multi-word skills are matched as phrases; single tokens are matched on word
    boundaries so "go" does not match "going" and "r" does not match every word.

    A trailing plural is tolerated, because postings write "REST APIs" and
    "microservices" while a profile lists "REST API". Without this, every plural form
    silently failed to match, which is the worst kind of bug here: it produces a
    confidently wrong "you do not have this" in the explanation.
    """
    if not text:
        return frozenset()
    haystack = normalize_text(text)
    if not haystack:
        return frozenset()

    found: set[str] = set()
    for skill in vocabulary:
        if not skill:
            continue
        # re.escape handles the punctuation in "c++", "c#", ".net", "node.js".
        pattern = re.escape(skill)
        if " " in skill or any(ch in skill for ch in "+#./-"):
            # Punctuated or multi-word: require a non-word char or string edge around it.
            regex = rf"(?<![a-z0-9]){pattern}(?:e?s)?(?![a-z0-9])"
        else:
            regex = rf"\b{pattern}(?:e?s)?\b"
        if re.search(regex, haystack):
            found.add(skill)
    return frozenset(found)


def tokenize_requirement(text: str) -> list[str]:
    """Split a requirement line into candidate skill phrases.

    ``"Strong experience with Python, Django and PostgreSQL"`` becomes
    ``["python", "django", "postgresql"]`` — filler words removed, connectors used as
    separators.
    """
    cleaned = _TOKEN_SPLIT_RE.sub(" ", normalize_text(text))
    phrases: list[str] = []
    for chunk in _CONNECTOR_RE.split(cleaned):
        words = [word for word in chunk.split() if word and word not in _STOPWORDS]
        if not words:
            continue
        phrase = canonical_skill(" ".join(words))
        if phrase:
            phrases.append(phrase)
    return phrases


@dataclass(frozen=True, slots=True)
class YearsRequirement:
    """An experience threshold stated in a requirement."""

    minimum_years: int
    maximum_years: int | None = None
    raw: str = ""


def extract_years_requirement(text: str | None) -> YearsRequirement | None:
    """Pull an experience threshold out of requirement text, if one is stated.

    Returns ``None`` when the text says nothing about years — which is different from
    "zero years required", and is treated as *uncertain* by the matching engine
    rather than as satisfied.
    """
    if not text:
        return None
    # Dash variants are folded first, so a range punctuated with an en dash parses.
    match = _YEARS_RE.search(fold_dashes(text))
    if match is None:
        return None
    try:
        low = int(match.group("lo"))
    except (TypeError, ValueError):  # pragma: no cover - regex guarantees digits
        return None
    high_raw = match.group("hi")
    high = int(high_raw) if high_raw else None
    if high is not None and high < low:
        low, high = high, low
    return YearsRequirement(minimum_years=low, maximum_years=high, raw=match.group(0).strip())


def extract_degree_requirement(text: str | None) -> str | None:
    """The highest degree level a requirement names, or ``None`` if it names none.

    Checked highest-first so "Bachelor's or Master's" reports the floor correctly:
    the caller wants the *minimum* acceptable level, which for an "or" list is the
    lowest named. We therefore return the lowest match, scanning from the bottom.
    """
    if not text:
        return None
    matched: list[str] = [level for level, pattern in _DEGREE_PATTERNS if pattern.search(text)]
    if not matched:
        return None
    # _DEGREE_PATTERNS is ordered highest to lowest, so the last match is the floor.
    return matched[-1]


def is_optional_requirement(text: str | None) -> bool:
    """Whether the wording marks this requirement as preferred rather than required."""
    if not text:
        return False
    haystack = normalize_text(text)
    return any(marker in haystack for marker in _OPTIONAL_MARKERS)


# ---------------------------------------------------------------------------
# A recognised-technology vocabulary, and what one skill implies about another.
# ---------------------------------------------------------------------------

#: Technologies CareerPilot recognises by name.
#:
#: This exists so the engine can tell the difference between "the posting asks for
#: something you do not have" and "the posting used a word we do not understand". Only
#: a name in here can ever be reported as a missing skill; anything else in a
#: requirement line is treated as prose, and the line becomes UNCERTAIN rather than
#: producing a fabricated gap.
#:
#: Built from the alias table's canonical values plus the names below, so adding an
#: alias extends the vocabulary automatically.
_EXTRA_TECHNOLOGIES: Final[frozenset[str]] = frozenset(
    """
    python java javascript typescript go rust ruby php swift kotlin scala r matlab c c++ c#
    .net perl bash powershell sql html css sass less react vue angular svelte next.js nuxt
    remix jquery bootstrap redux django flask fastapi spring express.js laravel rails asp.net
    dotnet node.js deno bun graphql rest grpc websockets soap postgresql mysql sqlite mongodb
    redis cassandra dynamodb elasticsearch oracle sql-server mariadb neo4j clickhouse
    snowflake databricks aws azure google-cloud heroku vercel netlify digitalocean cloudflare
    docker kubernetes terraform ansible puppet chef jenkins git github gitlab bitbucket linux
    unix windows nginx apache kafka rabbitmq celery airflow spark hadoop hive flink dbt
    pandas numpy scipy scikit-learn tensorflow pytorch keras opencv nltk spacy transformers
    langchain pytest jest vitest cypress selenium playwright junit mocha figma sketch tableau
    powerbi looker excel
    """.split()  # noqa: SIM905 - a prose block beats 140 quoted literals
)

KNOWN_TECHNOLOGIES: Final[frozenset[str]] = frozenset(
    {canonical_skill(name) for name in _EXTRA_TECHNOLOGIES}
    | {canonical_skill(value) for value in _ALIASES.values()}
    | {canonical_skill(key) for key in _ALIASES}
) - frozenset({""})

#: What holding one skill reasonably implies about another.
#:
#: Narrow and one-directional on purpose. Someone who has used PostgreSQL has written
#: SQL; someone who has used Django has written Python. The reverse does not hold, and
#: nothing here is a guess about the candidate -- it is a statement about the
#: technologies themselves. Keeping it small matters: a wrong implication produces a
#: confidently wrong "you meet this requirement".
IMPLIED_SKILLS: Final[dict[str, frozenset[str]]] = {
    "postgresql": frozenset({"sql"}),
    "mysql": frozenset({"sql"}),
    "sql server": frozenset({"sql"}),
    "sqlite": frozenset({"sql"}),
    "mariadb": frozenset({"sql"}),
    "oracle": frozenset({"sql"}),
    "django": frozenset({"python"}),
    "flask": frozenset({"python"}),
    "fastapi": frozenset({"python"}),
    "pandas": frozenset({"python"}),
    "numpy": frozenset({"python"}),
    "scikit-learn": frozenset({"python"}),
    "pytorch": frozenset({"python"}),
    "tensorflow": frozenset({"python"}),
    "react": frozenset({"javascript"}),
    "vue": frozenset({"javascript"}),
    "angular": frozenset({"javascript"}),
    "svelte": frozenset({"javascript"}),
    "next.js": frozenset({"react", "javascript"}),
    "node.js": frozenset({"javascript"}),
    "express.js": frozenset({"javascript", "node.js"}),
    "typescript": frozenset({"javascript"}),
    "spring": frozenset({"java"}),
    "ruby on rails": frozenset({"ruby"}),
    "laravel": frozenset({"php"}),
    "kubernetes": frozenset({"docker"}),
    "tailwind css": frozenset({"css"}),
    "sass": frozenset({"css"}),
    "github actions": frozenset({"ci/cd", "github"}),
}


def expand_with_implied(skills: frozenset[str]) -> frozenset[str]:
    """Add the skills that the given ones imply.

    Applied once, not transitively: the rules are shallow by design, and chaining them
    would start inventing qualifications a candidate never claimed.
    """
    expanded = set(skills)
    for skill in skills:
        expanded |= IMPLIED_SKILLS.get(skill, frozenset())
    return frozenset(expanded)


def drop_subsumed(candidates: frozenset[str], *, covered: frozenset[str]) -> frozenset[str]:
    """Remove names that are merely part of a longer name already accounted for.

    "REST APIs" matches both ``rest api`` and ``rest`` in the technology vocabulary.
    If the candidate holds ``rest api``, reporting ``rest`` as a missing skill is
    simply wrong, so the shorter name is dropped when a longer covered one contains it.
    """
    return frozenset(
        name for name in candidates if not any(name != held and name in held for held in covered)
    )
