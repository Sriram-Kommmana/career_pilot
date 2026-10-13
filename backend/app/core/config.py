"""The single configuration layer.

Everything configurable is declared here, read from the environment with the
``CAREERPILOT_`` prefix, and validated at import time. No module reads ``os.environ``
directly, and no credential or key is hardcoded.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
LLMProvider = Literal["none", "anthropic", "openai"]

# Repository root, i.e. the directory containing backend/ and frontend/.
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_DIR.parent


class MatchWeights(BaseSettings):
    """Weights for the deterministic matching engine.

    Separated from :class:`Settings` so the matching domain can be handed a plain
    weights object without depending on the whole configuration surface.
    """

    model_config = SettingsConfigDict(env_prefix="CAREERPILOT_WEIGHT_", extra="ignore")

    skill_overlap: Annotated[float, Field(ge=0.0, le=1.0)] = 0.30
    evidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.20
    role_relevance: Annotated[float, Field(ge=0.0, le=1.0)] = 0.20
    education: Annotated[float, Field(ge=0.0, le=1.0)] = 0.10
    location: Annotated[float, Field(ge=0.0, le=1.0)] = 0.10
    preferences: Annotated[float, Field(ge=0.0, le=1.0)] = 0.10

    @model_validator(mode="after")
    def _weights_must_sum_to_one(self) -> MatchWeights:
        total = (
            self.skill_overlap
            + self.evidence
            + self.role_relevance
            + self.education
            + self.location
            + self.preferences
        )
        # Tolerance covers float representation, not sloppy configuration.
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Match weights must sum to 1.0, got {total:.6f}. "
                "Adjust the CAREERPILOT_WEIGHT_* variables."
            )
        return self

    def as_mapping(self) -> dict[str, float]:
        return {
            "skill_overlap": self.skill_overlap,
            "evidence": self.evidence,
            "role_relevance": self.role_relevance,
            "education": self.education,
            "location": self.location,
            "preferences": self.preferences,
        }


class Settings(BaseSettings):
    """Application settings.

    Read once at startup via :func:`get_settings`.
    """

    model_config = SettingsConfigDict(
        env_prefix="CAREERPILOT_",
        env_file=(_REPO_ROOT / ".env", _BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- core -------------------------------------------------------------
    env: Environment = "development"
    debug: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["console", "json"] = "console"
    api_prefix: str = "/api/v1"

    # --- security ---------------------------------------------------------
    # Development gets an ephemeral key so the project runs with no setup; a
    # restart invalidates existing tokens, which is the correct trade-off locally.
    # Production refuses to start without a real key (see the validator below).
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_ttl_minutes: Annotated[int, Field(ge=1, le=60 * 24)] = 60

    # --- database ---------------------------------------------------------
    # "embedded" means CareerPilot manages a local PostgreSQL 16 server itself, via
    # the pgserver wheel: no Docker, no system service, no administrator rights. Point
    # this at a real postgresql+asyncpg:// URL for a server you run yourself, and
    # nothing in app.core.devdb is used. See docs/TROUBLESHOOTING.md.
    database_url: str = "embedded"
    #: Database name used when `database_url` is "embedded".
    database_name: str = "careerpilot"
    #: Separate database for the integration suite, so tests never touch dev data.
    test_database_name: str = "careerpilot_test"
    #: Explicit URL for the test suite. Defaults to the embedded server.
    test_database_url: str | None = None
    #: Data directory for the embedded server. Inside var/, which is git-ignored.
    pgdata_dir: Path = _REPO_ROOT / "var" / "pgdata"
    db_echo: bool = False
    db_pool_size: Annotated[int, Field(ge=1, le=50)] = 5
    db_max_overflow: Annotated[int, Field(ge=0, le=50)] = 10

    # --- redis (Phase 4) --------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # --- storage ----------------------------------------------------------
    storage_dir: Path = _REPO_ROOT / "var" / "storage"
    max_upload_bytes: Annotated[int, Field(ge=1024, le=50 * 1024 * 1024)] = 5 * 1024 * 1024

    # --- frontend ---------------------------------------------------------
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- matching ---------------------------------------------------------
    # A score is a ranking heuristic. This threshold controls notification
    # eligibility only; it is never presented as a probability of an outcome.
    notify_score_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.60

    # --- LLM (Phase 3) ----------------------------------------------------
    # "none" keeps CareerPilot fully functional on deterministic logic. Any other
    # value means paid API usage and must be explicitly chosen by the operator.
    llm_provider: LLMProvider = "none"
    llm_model: str | None = None
    llm_api_key: str | None = None

    # --- browser agent (Phase 5) ------------------------------------------
    # Headless defaults to false: a human has to be able to resolve a CAPTCHA,
    # a login, or an MFA prompt in the real browser.
    browser_headless: bool = False
    browser_timeout_ms: Annotated[int, Field(ge=1000, le=300_000)] = 30_000
    #: A session with no activity for this long is treated as expired on next
    #: access and cleaned up. Sessions are in-memory only, so a process restart
    #: already invalidates every one of them regardless of this value.
    browser_session_ttl_seconds: Annotated[int, Field(ge=60, le=7200)] = 600
    # An approval is only valid against a sufficiently fresh verification.
    max_verification_age_seconds: Annotated[int, Field(ge=30, le=86_400)] = 900
    # --- browser submission (Phase 6) -------------------------------------
    #: How long, after clicking the employer's submit control, to watch the live
    #: page for a recognised confirmation or rejection signal before giving up and
    #: recording the outcome as uncertain. Short on purpose: an unrecognised page
    #: past this window is reported honestly as uncertain, never assumed to be a
    #: success.
    browser_submission_confirmation_timeout_ms: Annotated[int, Field(ge=500, le=60_000)] = 8_000

    # --- connectors (Phase 2) ---------------------------------------------
    connector_user_agent: str = "CareerPilot/0.1"
    connector_rate_limit_per_minute: Annotated[int, Field(ge=1, le=600)] = 20
    connector_max_retries: Annotated[int, Field(ge=0, le=10)] = 3
    #: Per-request timeout for a connector's HTTP calls. Short on purpose: a source
    #: that cannot answer within this window should fail fast and let discovery
    #: move on to the next source, not hold up the whole run.
    connector_timeout_seconds: Annotated[float, Field(ge=1.0, le=60.0)] = 10.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, which is what a .env file gives us."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _production_requires_explicit_secret(self) -> Settings:
        if self.env == "production":
            placeholder = self.secret_key.startswith("replace-me")
            if placeholder or len(self.secret_key) < 32:
                raise ValueError(
                    "CAREERPILOT_SECRET_KEY must be set to a strong random value in "
                    "production. Generate one with: "
                    'python -c "import secrets; print(secrets.token_urlsafe(48))"'
                )
            if self.debug:
                raise ValueError("CAREERPILOT_DEBUG must be false in production.")
        return self

    @model_validator(mode="after")
    def _production_needs_a_real_database(self) -> Settings:
        from app.core.devdb import is_embedded

        if self.env == "production" and is_embedded(self.database_url):
            raise ValueError(
                "The embedded PostgreSQL server is a development convenience. Set "
                "CAREERPILOT_DATABASE_URL to a real postgresql+asyncpg:// URL in "
                "production."
            )
        return self

    @model_validator(mode="after")
    def _llm_provider_needs_a_key(self) -> Settings:
        if self.llm_provider != "none" and not self.llm_api_key:
            raise ValueError(
                f"CAREERPILOT_LLM_PROVIDER={self.llm_provider} requires "
                "CAREERPILOT_LLM_API_KEY. Set the provider back to 'none' to use the "
                "deterministic no-LLM path, which needs no paid service."
            )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def weights(self) -> MatchWeights:
        return MatchWeights()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resume_dir(self) -> Path:
        return self.storage_dir / "resumes"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def artifact_dir(self) -> Path:
        """Screenshots and browser traces. Redacted before anything is written."""
        return self.storage_dir / "artifacts"

    def ensure_storage(self) -> None:
        for directory in (self.storage_dir, self.resume_dir, self.artifact_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, built once.

    Tests that need different settings should clear the cache:
    ``get_settings.cache_clear()``.
    """
    return Settings()


def resolve_database_url(settings: Settings | None = None, *, for_tests: bool = False) -> str:
    """The URL to actually connect to.

    Turns the ``embedded`` sentinel into a live URL, starting the local server if it is
    not already running. Any explicit URL is returned untouched.
    """
    from app.core.devdb import is_embedded, start_embedded

    cfg = settings or get_settings()

    if for_tests:
        if cfg.test_database_url and not is_embedded(cfg.test_database_url):
            return cfg.test_database_url
        if not is_embedded(cfg.database_url):
            # A real server is configured but no test URL was given: derive a sibling
            # database rather than running tests against development data.
            base, _, _ = cfg.database_url.rpartition("/")
            return f"{base}/{cfg.test_database_name}"
        return start_embedded(pgdata=cfg.pgdata_dir, database=cfg.test_database_name)

    if is_embedded(cfg.database_url):
        return start_embedded(pgdata=cfg.pgdata_dir, database=cfg.database_name)
    return cfg.database_url
