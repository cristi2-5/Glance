"""Application custom exceptions and their associated FastAPI handlers.

Every domain exception inherits from `GlanceError` and carries a matching
HTTP `status_code`, so routers can raise semantic errors (`BookNotFound`,
`JobNotFound`, etc.) without manipulating `HTTPException` directly.
"""

import structlog
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)


class GlanceError(Exception):
    """Base class for all domain exceptions in Glance."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ResourceNotFound(GlanceError):
    """Raised when a requested resource (book, job, user) does not exist."""

    status_code = status.HTTP_404_NOT_FOUND


class AccessForbidden(GlanceError):
    """Raised when the current user has no access rights to the resource."""

    status_code = status.HTTP_403_FORBIDDEN


class InvalidData(GlanceError):
    """Raised when the data received from the client is semantically invalid."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class InvalidCredentials(GlanceError):
    """Raised on failed authentication (wrong password, expired token)."""

    status_code = status.HTTP_401_UNAUTHORIZED


class ExternalServiceUnavailable(GlanceError):
    """Raised when an external service (Ollama, data source) fails."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class FileTooLarge(GlanceError):
    """Raised when the uploaded file exceeds the maximum allowed size."""

    status_code = status.HTTP_413_CONTENT_TOO_LARGE


class UnsupportedFileType(GlanceError):
    """Raised when the uploaded file type is not among the accepted ones."""

    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


class ImageProcessingFailed(InvalidData):
    """Raised when an uploaded image cannot be decoded or processed (corrupt, truncated)."""


class OllamaUnavailable(ExternalServiceUnavailable):
    """Raised when Ollama cannot be reached or fails after all retry attempts."""


class AIProviderUnavailable(ExternalServiceUnavailable):
    """Raised when Groq errors, rate-limits, or is unreachable after all retry attempts."""


class CoverNotRecognized(GlanceError):
    """Raised when neither OCR nor vision fallback produced a usable title."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


def register_exception_handlers(app: FastAPI) -> None:
    """Registers the global exception handlers on the FastAPI application.

    Args:
        app: The FastAPI instance on which the handlers are registered.
    """

    @app.exception_handler(GlanceError)
    async def glance_error_handler(request: Request, exc: GlanceError) -> JSONResponse:
        logger.warning(
            "glance_error",
            error_type=type(exc).__name__,
            message=exc.message,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message},
        )
