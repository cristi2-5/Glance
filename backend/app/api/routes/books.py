"""Rută pentru analiza coperții unei cărți: upload → job asincron."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, UploadFile, status

from app.api.deps import DbSession, SessionFactory, UtilizatorCurent
from app.core.config import get_settings
from app.core.exceptions import FisierPreaMare, TipFisierNesuportat
from app.models.job import Job, JobStatus
from app.schemas.job import JobCreated
from app.workers.cover_pipeline import proceseaza_coperta

router = APIRouter(prefix="/books", tags=["books"])

TIPURI_IMAGINE_ACCEPTATE = {"image/jpeg", "image/png", "image/heic", "image/heif"}


async def _citeste_si_valideaza(file: UploadFile) -> bytes:
    """Validează tipul MIME și dimensiunea unei imagini încărcate.

    Args:
        file: Fișierul primit în cererea multipart.

    Returns:
        Conținutul brut al fișierului, dacă trece validarea.

    Raises:
        TipFisierNesuportat: Dacă tipul MIME nu e JPEG, PNG sau HEIC/HEIF.
        FisierPreaMare: Dacă fișierul depășește `max_upload_size_bytes`.
    """
    if file.content_type not in TIPURI_IMAGINE_ACCEPTATE:
        raise TipFisierNesuportat(
            f"Tip de fișier nesuportat: {file.content_type!r}. "
            "Acceptate: image/jpeg, image/png, image/heic, image/heif."
        )

    max_bytes = get_settings().max_upload_size_bytes
    continut = await file.read()
    if len(continut) > max_bytes:
        raise FisierPreaMare(
            f"Fișierul depășește dimensiunea maximă permisă de {max_bytes // (1024 * 1024)} MB."
        )
    return continut


@router.post("/analyze-cover", status_code=status.HTTP_202_ACCEPTED, response_model=JobCreated)
async def analizeaza_coperta(
    background_tasks: BackgroundTasks,
    db: DbSession,
    session_factory: SessionFactory,
    utilizator_curent: UtilizatorCurent,
    file: Annotated[UploadFile, File()],
) -> JobCreated:
    """Primește o fotografie a coperții unei cărți și pornește analiza asincronă.

    Creează un job în stare `pending`, apoi îl pasează unui task de fundal
    care parcurge starea `running` → `done`. Clientul primește imediat id-ul
    job-ului și face polling pe `GET /jobs/{job_id}`.

    Args:
        background_tasks: Coada de task-uri de fundal FastAPI.
        db: Sesiunea de bază de date curentă.
        session_factory: Fabrica de sesiuni pasată task-ului de fundal.
        utilizator_curent: Utilizatorul autentificat, proprietarul job-ului.
        file: Imaginea coperții (JPEG, PNG sau HEIC/HEIF, max 8 MB).

    Returns:
        Id-ul job-ului nou creat.

    Raises:
        TipFisierNesuportat: Dacă tipul fișierului nu e acceptat.
        FisierPreaMare: Dacă fișierul depășește dimensiunea maximă.
    """
    continut = await _citeste_si_valideaza(file)

    job = Job(user_id=utilizator_curent.id, status=JobStatus.PENDING.value)
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(proceseaza_coperta, job.id, continut, session_factory)
    return JobCreated(job_id=job.id)
