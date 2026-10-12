"""The FastAPI application factory.

Every error leaves through one of the handlers registered here, so a client sees a
single envelope shape and every failure is traceable by correlation id. Nothing is
swallowed: an unexpected exception is logged with its traceback and returned as a 500
whose body carries the same correlation id that appears in the log.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import AppError, error_envelope
from app.core.logging import (
    configure_logging,
    get_correlation_id,
    get_logger,
    set_correlation_id,
)
from app.db.session import check_database_connection, dispose_engine
from app.schemas.common import HealthResponse

logger = get_logger(__name__)

#: Request header a client may send to correlate its own logs with ours.
CORRELATION_HEADER = "X-Correlation-ID"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    settings.ensure_storage()
    logger.info(
        "application_starting",
        version=__version__,
        environment=settings.env,
        llm_provider=settings.llm_provider,
    )
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("application_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    configure_logging(level=cfg.log_level, fmt=cfg.log_format)

    app = FastAPI(
        title="CareerPilot API",
        version=__version__,
        description=(
            "Agentic job search and application platform.\n\n"
            "**Safety contract.** No endpoint lets a language model approve or submit "
            "an application. `POST /applications/{id}/submit` requires an explicit "
            "human confirmation for that specific application plus a server-side gate "
            "with nine conditions; a refusal returns 409 with every blocker listed. "
            "Match scores are ranking heuristics, never probabilities of an outcome. "
            "Seeded demo jobs are flagged `is_demo_data` and are not live postings."
        ),
        lifespan=lifespan,
        openapi_url=f"{cfg.api_prefix}/openapi.json",
        docs_url=f"{cfg.api_prefix}/docs",
        redoc_url=None,
    )
    app.state.settings = cfg

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", CORRELATION_HEADER],
        expose_headers=[CORRELATION_HEADER],
    )

    @app.middleware("http")
    async def correlation_and_timing(
        request: Request, call_next: Callable[[Request], Awaitable[object]]
    ) -> object:
        """Attach a correlation id to the request context and log the outcome.

        An id supplied by the client is honoured so a frontend trace and a backend
        trace can be lined up; otherwise one is generated.
        """
        correlation_id = set_correlation_id(request.headers.get(CORRELATION_HEADER))
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers[CORRELATION_HEADER] = correlation_id  # type: ignore[attr-defined]
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,  # type: ignore[attr-defined]
            duration_ms=duration_ms,
        )
        return response

    _register_error_handlers(app)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        """Liveness and database reachability.

        Unauthenticated on purpose, and it reports no configuration detail beyond the
        environment name.
        """
        reachable = await check_database_connection()
        return HealthResponse(
            status="ok" if reachable else "degraded",
            version=__version__,
            environment=cfg.env,
            database="ok" if reachable else "unreachable",
        )

    app.include_router(api_router, prefix=cfg.api_prefix)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        """Deliberate, typed failures.

        Logged at warning rather than error: these are expected outcomes such as a
        refused submission or a duplicate application, not faults.
        """
        logger.warning(
            "app_error",
            code=exc.code,
            status_code=exc.status_code,
            message=exc.message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(
                code=exc.code,
                message=exc.message,
                correlation_id=get_correlation_id(),
                details=exc.details,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Pydantic rejection, reshaped into our envelope.

        The error list is passed through so a client can highlight the offending
        field, but ``input`` is dropped: it can contain a password or another value
        that must not be echoed back or logged.
        """
        details = [
            {
                "location": list(error.get("loc", ())),
                "message": error.get("msg"),
                "type": error.get("type"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=error_envelope(
                code="VALIDATION_ERROR",
                message="The request body or query string is invalid.",
                correlation_id=get_correlation_id(),
                details={"errors": details},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {
            401: "NOT_AUTHENTICATED",
            403: "PERMISSION_DENIED",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            429: "RATE_LIMITED",
        }
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(
                code=codes.get(exc.status_code, "HTTP_ERROR"),
                message=str(exc.detail),
                correlation_id=get_correlation_id(),
            ),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        """Anything unanticipated.

        The traceback goes to the log; the response carries only the correlation id,
        so an internal detail is never disclosed to a client while remaining
        recoverable by whoever reads the logs.
        """
        logger.exception("unhandled_exception", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_envelope(
                code="INTERNAL_ERROR",
                message="Something went wrong. Quote the correlation id if you report this.",
                correlation_id=get_correlation_id(),
            ),
        )


app = create_app()
