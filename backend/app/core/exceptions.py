"""Excepții custom ale aplicației și handler-ele FastAPI asociate.

Fiecare excepție de domeniu moștenește `GlanceError` și poartă un
`status_code` HTTP potrivit, astfel încât routerele să poată ridica erori
semantice (`CarteNegasita`, `JobNegasit` etc.) fără să manipuleze direct
`HTTPException`.
"""

import structlog
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)


class GlanceError(Exception):
    """Clasă de bază pentru toate excepțiile de domeniu din Glance."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ResursaNegasita(GlanceError):
    """Ridicată când o resursă cerută (carte, job, utilizator) nu există."""

    status_code = status.HTTP_404_NOT_FOUND


class AcccesInterzis(GlanceError):
    """Ridicată când utilizatorul curent nu are drept de acces la resursă."""

    status_code = status.HTTP_403_FORBIDDEN


class DateInvalide(GlanceError):
    """Ridicată când datele primite de la client sunt invalide semantic."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class CredentialeInvalide(GlanceError):
    """Ridicată la autentificare eșuată (parolă greșită, token expirat)."""

    status_code = status.HTTP_401_UNAUTHORIZED


class ServiciuExternIndisponibil(GlanceError):
    """Ridicată când un serviciu extern (Ollama, sursă de date) eșuează."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class FisierPreaMare(GlanceError):
    """Ridicată când fișierul încărcat depășește dimensiunea maximă permisă."""

    status_code = status.HTTP_413_CONTENT_TOO_LARGE


class TipFisierNesuportat(GlanceError):
    """Ridicată când tipul fișierului încărcat nu e printre cele acceptate."""

    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


def register_exception_handlers(app: FastAPI) -> None:
    """Înregistrează handler-ele globale de excepții pe aplicația FastAPI.

    Args:
        app: Instanța FastAPI pe care se înregistrează handler-ele.
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
